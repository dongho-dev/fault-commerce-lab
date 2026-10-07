import contextlib
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

SUPPORTED = {"20", "21"}
RUNTIME = "lab_suite/round2_c_gateway.py"




def runtime_paths(case_id):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    return [RUNTIME]


def install_runtime(case_id):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)


def configure_compose(case_id, definition, source, evidence):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    services = definition["services"]
    app = services["app"]
    app.pop("ports", None)
    services["proxy"] = {
        "image": app["image"],
        "command": ["python", "-m", "lab_suite.round2_c_gateway"],
        "environment": {"UPSTREAM": "http://app:8000", "LAB_CASE": case_id,
                        "LAB_EVIDENCE": "/evidence", "PYTHONUNBUFFERED": "1"},
        "volumes": [{"type": "bind", "source": evidence.as_posix(), "target": "/evidence"}],
        "ports": [f"127.0.0.1:{18100 + int(case_id)}:8080"],
        "cpus": 1.0, "mem_limit": "320m",
        "depends_on": {"app": {"condition": "service_healthy"}},
    }
    services["probe"]["environment"]["LAB_BASE_URL"] = "http://proxy:8080"
    return definition


def _events(directory):
    records = []
    for path in directory.glob("round2-c-*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return sorted(records, key=lambda item: item["at"])


def _wait_event(directory, event, *, timeout=12, since=0, **fields):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for record in _events(directory):
            if record.get("at", 0) >= since and record.get("event") == event and all(
                    record.get(key) == value for key, value in fields.items()):
                return record
        time.sleep(0.02)
    raise TimeoutError(f"Missing observed event {event}: {fields}")


def _rotate(directory):
    token = uuid.uuid4().hex
    target = directory / "round2-c-rotate.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"token": token}), encoding="utf-8")
    temporary.replace(target)
    return _wait_event(directory, "rotation_acknowledged", token=token)


def _database():
    import psycopg

    return psycopg.connect(os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1))


@contextlib.contextmanager
def _hold_product(product_id=None):
    connection = _database()
    try:
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '25000ms'")
        connection.execute("SET LOCAL lock_timeout = '3000ms'")
        if product_id is None:
            connection.execute("LOCK TABLE products IN ACCESS EXCLUSIVE MODE")
        else:
            connection.execute(
                "SELECT product_id FROM inventories WHERE product_id = %s FOR UPDATE",
                (product_id,))
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _wait_blocker(connection):
    blocker = connection.info.backend_pid
    deadline = time.monotonic() + 8
    with _database() as observer:
        observer.autocommit = True
        while time.monotonic() < deadline:
            rows = observer.execute(
                "SELECT pid, wait_event_type, wait_event FROM pg_stat_activity "
                "WHERE %s = ANY(pg_blocking_pids(pid))", (blocker,)).fetchall()
            if rows:
                return [{"pid": row[0], "wait_event_type": row[1], "wait_event": row[2]}
                        for row in rows]
            time.sleep(0.02)
    raise TimeoutError("The real origin query did not reach the held database lock")


def _open_get(base, path):
    target = urlsplit(base)
    connection = socket.create_connection((target.hostname, target.port or 80), timeout=5)
    connection.sendall(
        f"GET {path} HTTP/1.1\r\nHost: {target.netloc}\r\nConnection: close\r\n\r\n"
        .encode("ascii"))
    return connection


def _create(client, name, price):
    payload = {"name": name, "unit_price": price, "initial_stock": 7, "category": "etc",
               "description": name + " 상품 안내"}
    response = client.post("/products", json=payload)
    response.raise_for_status()
    item = response.json()
    if response.status_code != 201 or any(item.get(key) != value
                                         for key, value in payload.items()):
        raise RuntimeError("Fixture creation was not preserved")
    return item


def _snapshot(ids):
    with _database() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        products = connection.execute(
            "SELECT id, name, unit_price FROM products WHERE id = ANY(%s) ORDER BY id",
            (ids,)).fetchall()
        inventories = connection.execute(
            "SELECT product_id, initial_stock, current_stock FROM inventories "
            "WHERE product_id = ANY(%s) ORDER BY product_id", (ids,)).fetchall()
        orders = connection.execute(
            "SELECT id, product_id, quantity, unit_price, postal_code, shipping_fee, "
            "total_amount, status FROM orders WHERE product_id = ANY(%s) ORDER BY id",
            (ids,)).fetchall()
    keys = ("id", "product_id", "quantity", "unit_price", "postal_code", "shipping_fee",
            "total_amount", "status")
    return {"products": products, "inventories": inventories,
            "orders": [dict(zip(keys, row, strict=True)) for row in orders]}


def _browser_view(page, product, evidence, label):
    page.wait_for_function("() => !!document.querySelector('.pdp-info h1')", timeout=10000)
    item = {"requested_id": product["id"], "requested_name": product["name"],
            "displayed_name": page.locator(".pdp-info h1").inner_text(), "url": page.url}
    page.screenshot(path=str(evidence / f"{label}.png"), full_page=True)
    return item


def _navigate(page, product):
    page.evaluate("(id) => { location.hash = '#/product/' + id; }", product["id"])


def _probe20(case_id, urls, evidence_dir):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    if sys.platform != "linux":
        raise RuntimeError("Browser observation requires isolated Linux")
    import httpx
    from playwright.sync_api import sync_playwright

    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    base = urls["app"].rstrip("/")
    run_id = uuid.uuid4().hex[:8]
    observations = {"run_id": run_id, "case": case_id, "controls": [], "views": [],
                    "wire": [], "locks": [], "page_errors": []}
    with httpx.Client(base_url=urls["direct"], timeout=10, trust_env=False) as direct, \
            httpx.Client(base_url=base, timeout=10, trust_env=False) as customer, \
            sync_playwright() as playwright:
        products = [_create(direct, f"{name} {run_id}", price) for name, price in (
            ("여행용 파우치", 12000), ("책상 정리함", 17000), ("텀블러", 23000))]
        ids = [item["id"] for item in products]
        before = _snapshot(ids)
        for product in products:
            for label, client in (("origin", direct), ("customer", customer)):
                response = client.get(f"/products/{product['id']}")
                control = {"label": label, "status": response.status_code,
                           "body": response.json(), "expected": product}
                observations["controls"].append(control)
                if response.status_code != 200 or response.json() != product:
                    raise RuntimeError("The initial actual origin/customer control failed")
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 960})
        try:
            page = context.new_page()
            page.on("pageerror", lambda error: observations["page_errors"].append(str(error)))
            def response_seen(response):
                if urlsplit(response.url).path.startswith("/products/"):
                    observations["wire"].append({
                        "url": response.url, "status": response.status,
                        "body": response.json(), "headers": response.all_headers()})
            page.on("response", response_seen)
            page.goto(base + "/", wait_until="networkidle", timeout=15000)
            first, second, third = products
            first_path, second_path = f"/products/{first['id']}", f"/products/{second['id']}"
            started = time.monotonic()
            with _hold_product() as first_lock:
                abandoned = _open_get(base, first_path)
                try:
                    sent = _wait_event(evidence, "origin_sent", path=first_path, since=started)
                    observations["locks"].append(_wait_blocker(first_lock))
                    abandoned.close()
                    _wait_event(evidence, "client_cancelled", path=first_path, since=started)
                    _navigate(page, second)
                    sent_second = _wait_event(evidence, "origin_sent",
                                              path=second_path, since=started)
                    observations["sent"] = [sent, sent_second]
                    first_lock.rollback()
                finally:
                    abandoned.close()
            view = _browser_view(page, second, evidence, f"case{case_id}-customer")
            observations["views"].append(view)
            _navigate(page, third)
            page.wait_for_function(
                "(prior) => { const h = document.querySelector('.pdp-info h1'); "
                "return h && h.textContent !== prior; }", arg=view["displayed_name"],
                timeout=10000)
            observations["views"].append(
                _browser_view(page, third, evidence, f"case{case_id}-following"))
            for product in products:
                response = direct.get(f"/products/{product['id']}")
                observations["controls"].append(
                    {"label": "origin-after", "status": response.status_code,
                     "body": response.json(), "expected": product})
            missing = customer.get("/products/2147483647")
            observations["missing"] = {"status": missing.status_code, "body": missing.json()}
            ready = customer.get("/health/ready")
            observations["ready"] = {"status": ready.status_code, "body": ready.json()}
        finally:
            context.close()
            browser.close()
        after = _snapshot(ids)
        observations.update(database_before=before, database_after=after,
                            events=_events(evidence), fixtures=products)
    controls = all(row["status"] == 200 and row["body"] == row["expected"]
                   for row in observations["controls"])
    stable = before == after
    first_view, following = observations["views"]
    expected = first_view["displayed_name"] == products[1]["name"]
    wrong = first_view["displayed_name"] == products[0]["name"]
    wire = [row for row in observations["wire"]
            if urlsplit(row["url"]).path == f"/products/{products[1]['id']}"]
    complete_wrong = len(wire) == 1 and wire[0]["status"] == 200 \
        and wire[0]["body"] == products[0]
    complete_right = len(wire) == 1 and wire[0]["status"] == 200 \
        and wire[0]["body"] == products[1]
    live = observations["ready"] == {"status": 200, "body": {"status": "ready"}}
    negative = observations["missing"]["status"] == 404
    following_right = following["displayed_name"] == products[2]["name"]
    common = controls and stable and live and not observations["page_errors"]
    healthy = common and expected and complete_right and following_right and negative
    symptom = common and wrong and complete_wrong
    symptom = symptom and following["displayed_name"] == products[1]["name"]
    result = {"healthy": bool(healthy), "symptom": bool(symptom),
              "checks": {"origin_correct": controls, "database_unchanged": stable,
                         "selected_product_correct": expected, "following_product_correct":
                         following_right, "missing_product_status_correct": negative,
                         "complete_wrong_product_response": complete_wrong,
                         "service_ready": live}, "observations": observations}
    (evidence / f"case{case_id}-evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _post_socket(base, payload, request_id):
    target = urlsplit(base)
    body = json.dumps(payload).encode("utf-8")
    connection = socket.create_connection((target.hostname, target.port or 80), timeout=5)
    head = (f"POST /orders HTTP/1.1\r\nHost: {target.netloc}\r\nConnection: close\r\n"
            f"Content-Type: application/json\r\nX-Request-ID: {request_id}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n")
    connection.sendall(head.encode("ascii") + body)
    return connection


def _prepare_checkout(page, base, product, quantity, postal):
    page.goto(base + f"/#/product/{product['id']}", wait_until="networkidle", timeout=15000)
    page.locator("[data-qty-input]").fill(str(quantity))
    page.locator("[data-buy-now]").click()
    page.locator("[data-postal]").fill(postal)
    return {"text": page.locator("[data-checkout-form]").inner_text(),
            "pay": page.locator("[data-pay]").inner_text()}


def _expected_order(product, quantity, postal, shipping):
    return {"product_id": product["id"], "quantity": quantity,
            "unit_price": product["unit_price"], "postal_code": postal,
            "shipping_fee": shipping, "total_amount": product["unit_price"] * quantity + shipping,
            "status": "CONFIRMED"}


def _matches_order(actual, expected):
    return isinstance(actual, dict) and all(actual.get(key) == value
                                           for key, value in expected.items())


def _probe21(urls, evidence_dir):
    if sys.platform != "linux":
        raise RuntimeError("Browser observation requires isolated Linux")
    import httpx
    from playwright.sync_api import sync_playwright

    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    base = urls["app"].rstrip("/")
    run_id = uuid.uuid4().hex[:8]
    observations = {"run_id": run_id, "wire": [], "requests": [], "locks": [],
                    "page_errors": [], "controls": []}
    with httpx.Client(base_url=urls["direct"], timeout=10, trust_env=False) as direct, \
            httpx.Client(base_url=base, timeout=10, trust_env=False) as customer, \
            sync_playwright() as playwright:
        products = [_create(direct, f"{name} {run_id}", price) for name, price in (
            ("여행용 파우치", 12000), ("책상 정리함", 17000), ("텀블러", 23000))]
        first, second, third = products
        expected = [_expected_order(first, 1, "16841", 3000),
                    _expected_order(second, 3, "63309", 6200),
                    _expected_order(third, 2, "16841", 3000)]
        ids = [item["id"] for item in products]
        before = _snapshot(ids)
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 960})
        try:
            page = context.new_page()
            page.on("pageerror", lambda error: observations["page_errors"].append(str(error)))

            def request_seen(request):
                if request.method == "POST" and urlsplit(request.url).path == "/orders":
                    observations["requests"].append(request.post_data_json)

            def response_seen(response):
                if response.request.method == "POST" and urlsplit(response.url).path == "/orders":
                    observations["wire"].append({
                        "status": response.status, "body": response.json(),
                        "headers": response.all_headers(),
                        "request": response.request.post_data_json})

            page.on("request", request_seen)
            page.on("response", response_seen)
            observations["checkout"] = _prepare_checkout(page, base, second, 3, "63309")
            observations["initial_rotation"] = _rotate(evidence)
            old_generation = observations["initial_rotation"]["generation"]
            started = time.monotonic()
            payload_a = {"product_id": first["id"], "quantity": 1, "postal_code": "16841"}
            payload_b = {"product_id": second["id"], "quantity": 3, "postal_code": "63309"}
            observations["abandoned_request"] = payload_a
            with _hold_product(first["id"]) as first_lock:
                abandoned = _post_socket(base, payload_a, "r2c-old-" + run_id)
                try:
                    sent_a = _wait_event(evidence, "broker_dispatched", path="/orders",
                                         generation=old_generation)
                    observations["locks"].append(_wait_blocker(first_lock))
                    abandoned.close()
                    _wait_event(evidence, "client_cancelled", path="/orders", since=started)
                    observations["replacement"] = _rotate(evidence)
                    generation = observations["replacement"]["generation"]
                    with _hold_product(second["id"]) as second_lock:
                        page.locator("[data-pay]").click()
                        sent_b = _wait_event(evidence, "broker_dispatched", path="/orders",
                                             generation=generation)
                        observations["locks"].append(_wait_blocker(second_lock))
                        observations["sent"] = [sent_a, sent_b]
                        first_lock.rollback()
                        observations["late_reply"] = _wait_event(
                            evidence, "broker_reply", generation=old_generation,
                            sequence=sent_a["sequence"])
                        second_lock.rollback()
                    _wait_event(evidence, "broker_reply", generation=generation,
                                sequence=sent_b["sequence"])
                finally:
                    abandoned.close()
            page.locator(".done-hero").wait_for(timeout=10000)
            observations["complete_text"] = page.locator("#app").inner_text()
            observations["complete_record"] = page.evaluate(
                "() => JSON.parse(sessionStorage.getItem('faultmart.lastCheckout'))")
            page.screenshot(path=str(evidence / "case21-complete.png"), full_page=True)
            page.get_by_role("link", name="주문내역 보기", exact=True).click()
            page.wait_for_url("**/#/orders")
            observations["history_text"] = page.locator("#app").inner_text()
            observations["history_record"] = page.evaluate(
                "() => JSON.parse(localStorage.getItem('faultmart.orders.v1'))")
            page.screenshot(path=str(evidence / "case21-history.png"), full_page=True)
            observations["next_rotation"] = _rotate(evidence)
            follow_payload = {"product_id": third["id"], "quantity": 2, "postal_code": "16841"}
            follow = customer.post("/orders", json=follow_payload)
            observations["follow"] = {"status": follow.status_code, "body": follow.json(),
                                      "request": follow_payload}
            shortage = customer.post("/orders", json={**follow_payload, "quantity": 99})
            missing = customer.post("/orders", json={**follow_payload,
                                                    "product_id": 2147483647})
            observations["negative"] = [
                {"status": shortage.status_code, "body": shortage.json()},
                {"status": missing.status_code, "body": missing.json()}]
            for product, quantity in zip(products, (1, 3, 2), strict=True):
                for label, client in (("origin", direct), ("customer", customer)):
                    response = client.get(f"/products/{product['id']}")
                    observations["controls"].append({
                        "label": label, "status": response.status_code, "body": response.json(),
                        "expected": {**product,
                                     "current_stock": product["initial_stock"] - quantity}})
            ready = customer.get("/health/ready")
            observations["ready"] = {"status": ready.status_code, "body": ready.json()}
        finally:
            context.close()
            browser.close()
        after = _snapshot(ids)
        observations.update(database_before=before, database_after=after,
                            fixtures=products, expected_orders=expected, events=_events(evidence))
    orders = after["orders"]
    one_each = len(orders) == 3 and all(
        sum(_matches_order(order, item) for order in orders) == 1 for item in expected)
    stock = all(row[1] == 7 and row[2] == 7 - quantity
                for row, quantity in zip(after["inventories"], (1, 3, 2), strict=True))
    body = observations["wire"][0]["body"] if len(observations["wire"]) == 1 else None
    ledger = observations["requests"] == [payload_b] and len(observations["wire"]) == 1 \
        and observations["wire"][0]["request"] == payload_b \
        and observations["wire"][0]["status"] == 201
    saved = observations["complete_record"]["orders"]
    history = observations["history_record"]
    visible_saved = len(saved) == 1 and history and history[0]["orders"] == saved \
        and _matches_order(saved[0], body or {})
    customer_correct = _matches_order(body, expected[1])
    customer_wrong = _matches_order(body, expected[0])
    returned_id = next((order["id"] for order in orders
                        if body is not None and all(body.get(key) == value
                                                    for key, value in order.items())), None)
    attached = body is not None and body.get("id") == returned_id
    controls = all(row["status"] == 200 and row["body"] == row["expected"]
                   for row in observations["controls"])
    regressions = observations["follow"]["status"] == 201 \
        and _matches_order(observations["follow"]["body"], expected[2]) \
        and [row["status"] for row in observations["negative"]] == [409, 404]
    database = one_each and stock and before["orders"] == [] \
        and before["products"] == after["products"]
    common = database and ledger and attached and visible_saved and controls and regressions \
        and not observations["page_errors"] \
        and observations["ready"] == {"status": 200, "body": {"status": "ready"}}
    result = {"healthy": bool(common and customer_correct),
              "symptom": bool(common and customer_wrong),
              "checks": {"each_actual_order_persisted_once": one_each,
                         "inventory_matches_actual_requests": stock,
                         "customer_request_preserved": ledger,
                         "response_matches_persisted_order": attached,
                         "customer_received_own_order": customer_correct,
                         "completion_and_history_agree": bool(visible_saved),
                         "origin_and_customer_stock_correct": controls,
                         "subsequent_order_and_error_controls": regressions},
              "observations": observations}
    (evidence / "case21-evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def probe(case_id, urls, evidence_dir):
    if case_id == "20":
        return _probe20(case_id, urls, evidence_dir)
    if case_id == "21":
        return _probe21(urls, evidence_dir)
    raise ValueError(case_id)
