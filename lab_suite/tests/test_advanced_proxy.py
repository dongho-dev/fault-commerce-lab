import http.client
import json
import socket
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from lab_suite import advanced_proxy

pytestmark = pytest.mark.live_http


@contextmanager
def serve(handler, address):
    server = ThreadingHTTPServer(address, handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


def backend_handler(state):
    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests.append((self.command, self.path, body))
            if state.drop_response:
                self.close_connection = True
                return
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        do_GET = respond
        do_POST = respond

    return Backend


def backend_state(name):
    return SimpleNamespace(
        body=json.dumps({"backend": name, "name": "상품"}, ensure_ascii=False).encode(),
        requests=[],
        status=200,
        drop_response=False,
    )


@pytest.fixture
def environment(monkeypatch):
    host = f"catalog-{uuid.uuid4().hex}.test"
    state = SimpleNamespace(ips=["127.0.0.1"], dns_error=False, port=None)
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname

    def addresses(hostname):
        if state.dns_error:
            raise socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")
        return state.ips

    def getaddrinfo(hostname, port, *args, **kwargs):
        if hostname != host:
            return original_getaddrinfo(hostname, port, *args, **kwargs)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, int(port)))
            for ip in addresses(hostname)
        ]

    def gethostbyname(hostname):
        return addresses(hostname)[0] if hostname == host else original_gethostbyname(hostname)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    with serve(advanced_proxy.Handler, ("127.0.0.1", 0)) as proxy:
        state.proxy_port = proxy.server_port

        def target(port):
            state.port = port
            monkeypatch.setattr(advanced_proxy, "TARGET", urlsplit(f"http://{host}:{port}"))

        def request(path="/products/2", method="GET", body=None):
            connection = http.client.HTTPConnection("127.0.0.1", state.proxy_port, timeout=3)
            try:
                connection.request(method, path, body=body)
                response = connection.getresponse()
                data = response.read()
                assert int(response.getheader("Content-Length")) == len(data)
                return response.status, data
            finally:
                connection.close()

        state.target = target
        state.request = request
        yield state


@pytest.mark.parametrize("path", ["/products?limit=20", "/products/2"])
def test_catalogue_survives_two_address_changes(environment, path):
    a, b, c = [backend_state(name) for name in ("a", "b", "c")]
    with serve(backend_handler(a), ("127.0.0.1", 0)) as first:
        environment.target(first.server_port)
        assert environment.request(path) == (200, a.body)
    with serve(backend_handler(c), ("127.0.0.3", environment.port)):
        with serve(backend_handler(b), ("127.0.0.2", environment.port)):
            environment.ips = ["127.0.0.2", "127.0.0.3"]
            assert environment.request(path) == (200, b.body)
        environment.ips = ["127.0.0.3"]
        assert environment.request(path) == (200, c.body)


def test_connection_uses_next_resolved_address_if_first_is_unavailable(environment):
    state = backend_state("available")
    with serve(backend_handler(state), ("127.0.0.2", 0)) as backend:
        environment.target(backend.server_port)
        environment.ips = ["127.0.0.1", "127.0.0.2"]
        assert environment.request() == (200, state.body)
    assert len(state.requests) == 1


def test_next_request_recovers_after_temporary_dns_failure(environment):
    state = backend_state("available")
    with serve(backend_handler(state), ("127.0.0.1", 0)) as backend:
        environment.target(backend.server_port)
        environment.dns_error = True
        assert environment.request() == (502, b"upstream unavailable")
        environment.dns_error = False
        assert environment.request() == (200, state.body)


@pytest.mark.parametrize("method,status", [("GET", 200), ("POST", 201)])
def test_response_and_request_bodies_are_preserved(environment, method, status):
    state = backend_state("available")
    state.status = status
    payload = b'{"quantity":1}' if method == "POST" else None
    with serve(backend_handler(state), ("127.0.0.1", 0)) as backend:
        environment.target(backend.server_port)
        assert environment.request("/products", method, payload) == (status, state.body)
    assert state.requests == [(method, "/products", payload or b"")]


def test_post_is_not_replayed_when_upstream_drops_its_response(environment):
    first, second = backend_state("first"), backend_state("second")
    first.drop_response = True
    with serve(backend_handler(first), ("127.0.0.1", 0)) as backend:
        environment.target(backend.server_port)
        with serve(backend_handler(second), ("127.0.0.2", environment.port)):
            environment.ips = ["127.0.0.1", "127.0.0.2"]
            assert environment.request("/orders", "POST", b'{"quantity":1}') == (
                502,
                b"upstream unavailable",
            )
    assert first.requests == [("POST", "/orders", b'{"quantity":1}')]
    assert second.requests == []
