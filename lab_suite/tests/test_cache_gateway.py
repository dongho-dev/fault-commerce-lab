import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from lab_suite import cache_gateway

PRIMARY = "/products?q=pouch&category=digital&sort=price_asc&limit=20&offset=0"
OTHER = "/products?q=box&category=home&sort=price_asc&limit=20&offset=0"


def catalogue(*items):
    return json.dumps(
        {"items": list(items), "total": len(items), "limit": 20, "offset": 0}
    ).encode()


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def gateway(monkeypatch):
    first = {"id": 1, "name": "Pouch", "unit_price": 12000, "current_stock": 10}
    other = {"id": 2, "name": "Box", "unit_price": 15000, "current_stock": 10}
    state = SimpleNamespace(
        first=first,
        bodies={PRIMARY: catalogue(first), OTHER: catalogue(other)},
        requests=[],
    )

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            state.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            body = state.bodies[self.path]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with serve(Upstream) as upstream:
        monkeypatch.setattr(cache_gateway, "UPSTREAM", urlsplit(upstream))
        with serve(cache_gateway.Handler) as address:
            with httpx.Client(base_url=address, timeout=5, trust_env=False) as client:
                yield client, state


@pytest.mark.parametrize("mutation", ["new_product", "stock", "price"])
def test_changed_catalogue_returns_new_body_before_it_can_be_reused(gateway, mutation):
    client, state = gateway
    first = client.get(PRIMARY)
    assert first.status_code == 200
    previous_tag = first.headers["etag"]
    if mutation == "new_product":
        second = {"id": 3, "name": "New pouch", "unit_price": 16000, "current_stock": 10}
        state.bodies[PRIMARY] = catalogue(state.first, second)
    else:
        changed = (
            {**state.first, "current_stock": 9}
            if mutation == "stock"
            else {**state.first, "unit_price": 11000}
        )
        state.bodies[PRIMARY] = catalogue(changed)

    refreshed = client.get(PRIMARY, headers={"If-None-Match": previous_tag})

    assert refreshed.status_code == 200
    assert refreshed.content == state.bodies[PRIMARY]
    assert refreshed.headers["etag"] != previous_tag
    assert int(refreshed.headers["content-length"]) == len(refreshed.content)
    assert refreshed.headers["cache-control"] == "no-cache"
    assert "if-none-match" not in state.requests[-1][1]
    unchanged = client.get(PRIMARY, headers={"If-None-Match": refreshed.headers["etag"]})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == refreshed.headers["etag"]


@pytest.mark.parametrize("condition", ["exact", "weak", "list"])
def test_unchanged_catalogue_revalidates_without_resending_body(gateway, condition):
    client, state = gateway
    first = client.get(PRIMARY)
    assert first.status_code == 200
    assert first.content == state.bodies[PRIMARY]
    tag = first.headers["etag"]
    validator = {"exact": tag, "weak": f"W/{tag}", "list": f'"other", W/{tag}'}[condition]

    unchanged = client.get(
        PRIMARY,
        headers={
            "If-None-Match": validator,
            "If-Modified-Since": "Wed, 01 Jan 2025 00:00:00 GMT",
        },
    )

    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == tag
    assert unchanged.headers["cache-control"] == "no-cache"
    assert len(state.requests) == 2
    assert "if-none-match" not in state.requests[-1][1]
    assert "if-modified-since" not in state.requests[-1][1]


def test_unrelated_query_change_does_not_invalidate_unchanged_result(gateway):
    client, state = gateway
    primary = client.get(PRIMARY)
    other = client.get(OTHER)
    state.bodies[OTHER] = catalogue({"id": 2, "name": "Box", "unit_price": 17000})

    unchanged = client.get(PRIMARY, headers={"If-None-Match": primary.headers["etag"]})
    changed = client.get(OTHER, headers={"If-None-Match": other.headers["etag"]})

    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == primary.headers["etag"]
    assert changed.status_code == 200
    assert changed.content == state.bodies[OTHER]
    assert changed.headers["etag"] != other.headers["etag"]


def test_browser_with_pre_fix_validator_receives_current_body(gateway):
    client, state = gateway
    old_tag = '"b7f243ef695c3176ae482516b3ad992daea32c22f2bf09dd2724bcfe351c94c5"'

    response = client.get(PRIMARY, headers={"If-None-Match": old_tag})

    assert response.status_code == 200
    assert response.content == state.bodies[PRIMARY]
    assert response.headers["etag"] != old_tag
