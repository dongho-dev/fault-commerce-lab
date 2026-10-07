from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PRODUCT_BEFORE = """        statement = (
            select(Product, Inventory)
            .join(Inventory, Inventory.product_id == Product.id)
            .where(*conditions)
            .order_by(*order_clauses(sort), Product.id)
            .limit(limit)
            .offset(offset)
        )
        return self.session.execute(statement).tuples().all(), int(total or 0)
"""
_PRODUCT_AFTER = """        if sort == "price_asc":
            statement = (
                select(Product, Inventory)
                .join(Inventory, Inventory.product_id == Product.id)
                .where(*conditions)
                .order_by(Product.id)
                .limit(limit)
                .offset(offset)
            )
            rows = self.session.execute(statement).tuples().all()
            return sorted(rows, key=lambda row: (row[0].unit_price, row[0].id)), int(total or 0)
        statement = (
            select(Product, Inventory)
            .join(Inventory, Inventory.product_id == Product.id)
            .where(*conditions)
            .order_by(*order_clauses(sort), Product.id)
            .limit(limit)
            .offset(offset)
        )
        return self.session.execute(statement).tuples().all(), int(total or 0)
"""
_ORDER_BEFORE = """            stage_started = time.perf_counter()
            total_amount = calculate_total_amount(
                unit_price=product.unit_price,
                quantity=quantity,
                shipping_fee=shipping_fee,
            )
            order = self.orders.create(
                product_id=product_id,
                quantity=quantity,
                unit_price=product.unit_price,
                postal_code=postal_code,
                shipping_fee=shipping_fee,
                total_amount=total_amount,
            )
"""
_ORDER_AFTER = """            unit_price = product.unit_price

        with self.session.begin():
            stage_started = time.perf_counter()
            total_amount = calculate_total_amount(
                unit_price=unit_price,
                quantity=quantity,
                shipping_fee=shipping_fee,
            )
            order = self.orders.create(
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price,
                postal_code=postal_code,
                shipping_fee=shipping_fee,
                total_amount=total_amount,
            )
"""
_SHIPPING_BEFORE = (
    '        digits = "".join(character for character in postal_code if character.isdigit())\n'
)
_SHIPPING_AFTER = _SHIPPING_BEFORE + "        digits = str(int(digits)) if digits else digits\n"


def replacements(case_id: str) -> list[tuple[str, str, str]]:
    if case_id == "01":
        return [("app/repositories/product.py", _PRODUCT_BEFORE, _PRODUCT_AFTER)]
    if case_id == "02":
        return [("app/services/order.py", _ORDER_BEFORE, _ORDER_AFTER)]
    if case_id == "03":
        return [("app/services/shipping.py", _SHIPPING_BEFORE, _SHIPPING_AFTER)]
    raise ValueError(f"Unknown data case: {case_id}")


def install_runtime(case_id: str) -> None:
    if case_id != "02":
        return
    from app.models.product import Product
    from app.repositories.order import OrderRepository

    original = OrderRepository.create
    if getattr(original, "_lab02_controlled_failure", False):
        return

    def create_with_failure(self: Any, **fields: Any) -> Any:
        product = self.session.get(Product, fields["product_id"])
        if product is not None and product.name.startswith("LAB02-Fail"):
            raise RuntimeError("LAB02 controlled order persistence failure")
        return original(self, **fields)

    create_with_failure._lab02_controlled_failure = True
    OrderRepository.create = create_with_failure


def _product(
    client: Any,
    *,
    name: str,
    price: int = 10_000,
    stock: int = 100,
    category: str = "digital",
    **fields: Any,
) -> dict[str, Any]:
    response = client.post(
        "/products",
        json={
            "name": name,
            "unit_price": price,
            "initial_stock": stock,
            "category": category,
            **fields,
        },
    )
    response.raise_for_status()
    return response.json()


def _listing(client: Any, **params: Any) -> dict[str, Any]:
    response = client.get("/products", params=params)
    response.raise_for_status()
    return response.json()


def _checkout_browser(
    base_url: str, product: dict[str, Any], postal: str, evidence_dir: Path, prefix: str
) -> dict[str, Any]:
    if sys.platform != "linux":
        raise RuntimeError("Browser probes must run in the isolated Linux lab container")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 1100},
                extra_http_headers={"X-Request-ID": prefix},
            )
            page = context.new_page()
            page.goto(f"{base_url.rstrip('/')}/#/product/{product['id']}", wait_until="networkidle")
            page.locator("[data-buy-now]").click()
            page.locator("[data-postal]").fill(postal)
            before = {
                "postal": page.locator("[data-postal]").input_value(),
                "summary": page.locator("[data-co-summary]").inner_text(),
                "total": page.locator("[data-co-summary] .total dd").inner_text(),
                "button": page.locator("[data-pay]").inner_text(),
            }
            page.screenshot(path=str(evidence_dir / f"{prefix}-checkout.png"), full_page=True)
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and response.url.endswith("/orders")
                )
            ) as pending:
                page.locator("[data-pay]").click()
            response = pending.value
            page.wait_for_function("location.hash === '#/complete'")
            page.locator(".done-hero").wait_for()
            result = {
                "before": before,
                "http_status": response.status,
                "request_id": response.headers.get("x-request-id"),
                "request": response.request.post_data_json,
                "response": response.json(),
                "completion": page.locator("#app").inner_text(),
                "last_checkout": page.evaluate(
                    "JSON.parse(sessionStorage.getItem('faultmart.lastCheckout') || 'null')"
                ),
                "history": page.evaluate(
                    "JSON.parse(localStorage.getItem('faultmart.orders.v1') || '[]')"
                ),
                "stored_postal": page.evaluate(
                    "JSON.parse(localStorage.getItem('faultmart.postal') || 'null')"
                ),
            }
            page.screenshot(path=str(evidence_dir / f"{prefix}-complete.png"), full_page=True)
            page.locator(".done-actions a[href='#/orders']").click()
            page.locator(".page-title").wait_for()
            result["history_page"] = page.locator("#app").inner_text()
            page.screenshot(path=str(evidence_dir / f"{prefix}-history.png"), full_page=True)
            return result
        finally:
            browser.close()


def _case01(client: Any, base_url: str, evidence_dir: Path) -> dict[str, Any]:
    products = [
        _product(client, name=f"LAB01-Main-{index:02d}", price=price)
        for index, price in enumerate(range(41_000, 0, -1_000), 1)
    ]
    _product(client, name="LAB01-Main-other-category", price=1, category="home")
    expected = [
        (product["unit_price"], product["id"])
        for product in sorted(products, key=lambda item: (item["unit_price"], item["id"]))
    ]
    pages = [
        _listing(
            client, q="LAB01-Main", category="digital", sort="price_asc", limit=20, offset=offset
        )
        for offset in (0, 20, 40)
    ]
    combined = [item for page in pages for item in page["items"]]
    observed = [(item["unit_price"], item["id"]) for item in combined]
    sizes = {}
    for size in (7, 13, 41):
        batch = [
            _listing(
                client,
                q="LAB01-Main",
                category="digital",
                sort="price_asc",
                limit=size,
                offset=offset,
            )
            for offset in range(0, 41, size)
        ]
        sizes[str(size)] = [
            (item["unit_price"], item["id"]) for page in batch for item in page["items"]
        ]
    terminal = _listing(
        client, q="LAB01-Main", category="digital", sort="price_asc", limit=20, offset=41
    )
    ties = [
        _product(client, name=f"LAB01-Tie-{index}", price=price)
        for index, price in enumerate((10_000, 5_000, 10_000, 5_000, 10_000, 5_000))
    ]
    expected_ties = [
        item["id"] for item in sorted(ties, key=lambda item: (item["unit_price"], item["id"]))
    ]
    tie_runs = []
    for _ in range(2):
        tie_runs.append(
            [
                item["id"]
                for offset in (0, 2, 4)
                for item in _listing(
                    client,
                    q="LAB01-Tie",
                    category="digital",
                    sort="price_asc",
                    limit=2,
                    offset=offset,
                )["items"]
            ]
        )
    controls = [
        _product(
            client,
            name="LAB01-Control-full",
            price=10_000,
            stock=5,
            list_price=10_000,
            brand="LAB01Brand",
        ),
        _product(
            client,
            name="LAB01-Control-discount",
            price=20_000,
            stock=5,
            list_price=40_000,
            brand="LAB01Brand",
        ),
        _product(
            client,
            name="LAB01-Control-100%",
            price=1_000,
            stock=0,
            list_price=4_000,
            brand="LAB01Brand",
        ),
    ]
    control_orders = {}
    for sort in ("recommended", "price_desc", "discount", "newest"):
        control_orders[sort] = [
            item["id"]
            for item in _listing(
                client, q="LAB01-Control", category="digital", sort=sort, limit=2, offset=0
            )["items"]
        ]
    a, b, c = [item["id"] for item in controls]
    control_expected = {
        "recommended": [a, b],
        "price_desc": [b, a],
        "discount": [b, a],
        "newest": [c, b],
    }
    brand = _listing(client, q="lab01brand", category="digital", limit=100)
    literal = _listing(client, q="100%", category="digital", limit=100)
    browser_pages = []
    if sys.platform != "linux":
        raise RuntimeError("Browser probes must run in the isolated Linux lab container")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            for number, count in ((1, 20), (2, 20), (3, 1)):
                page.goto(
                    f"{base_url.rstrip('/')}/#/search?q=LAB01-Main&category=digital&sort=price_asc&page={number}",
                    wait_until="networkidle",
                )
                page.wait_for_function(
                    "count => document.querySelectorAll('[data-results] .card').length === count",
                    arg=count,
                )
                browser_pages.append(
                    page.locator("[data-results] .card-link").evaluate_all(
                        "links => links.map(link => "
                        "Number(link.getAttribute('href').split('/').pop()))"
                    )
                )
                page.screenshot(path=str(evidence_dir / f"lab01-page-{number}.png"), full_page=True)
        finally:
            browser.close()
    browser_ids = [product_id for page in browser_pages for product_id in page]
    checks = {
        "global_price_id_order": observed == expected,
        "no_missing_or_duplicate": sorted(item["id"] for item in combined)
        == sorted(item["id"] for item in products),
        "page_metadata": all(
            page["total"] == 41 and page["limit"] == 20 and page["offset"] == offset
            for page, offset in zip(pages, (0, 20, 40), strict=True)
        )
        and [len(page["items"]) for page in pages] == [20, 20, 1],
        "empty_terminal_page": terminal["items"] == [] and terminal["total"] == 41,
        "page_size_independence": all(order == expected for order in sizes.values()),
        "stable_price_ties": all(run == expected_ties for run in tie_runs),
        "other_sort_and_filter_controls": control_orders == control_expected
        and sorted(item["id"] for item in brand["items"]) == sorted([a, b, c])
        and [item["id"] for item in literal["items"]] == [c],
        "browser_matches_api_pages": browser_ids == [item["id"] for item in combined],
        "browser_global_order": browser_ids == [product_id for _, product_id in expected],
    }
    boundary_drop = any(
        pages[index]["items"][-1]["unit_price"] > pages[index + 1]["items"][0]["unit_price"]
        for index in (0, 1)
    )
    symptom = (
        boundary_drop
        and not checks["global_price_id_order"]
        and not checks["browser_global_order"]
        and checks["no_missing_or_duplicate"]
        and checks["other_sort_and_filter_controls"]
        and checks["browser_matches_api_pages"]
    )
    return {
        "healthy": all(checks.values()),
        "symptom": symptom,
        "checks": checks,
        "observations": {
            "expected_price_id_order": expected,
            "observed_price_id_order": observed,
            "page_size_orders": sizes,
            "expected_tie_ids": expected_ties,
            "tie_runs": tie_runs,
            "browser_page_ids": browser_pages,
            "boundary_price_drop": boundary_drop,
            "control_orders": control_orders,
            "control_expected": control_expected,
        },
    }


def _case02(client: Any, base_url: str, evidence_dir: Path) -> dict[str, Any]:
    failed = _product(client, name="LAB02-Fail-atomicity", stock=2)
    control = _product(client, name="LAB02-Control", stock=2)
    before = client.get(f"/products/{failed['id']}").json()
    browser = _checkout_browser(base_url, failed, "16841", evidence_dir, "lab02-failure")
    after_response = client.get(f"/products/{failed['id']}")
    after_response.raise_for_status()
    after = after_response.json()
    control_response = client.post(
        "/orders",
        json={"product_id": control["id"], "quantity": 1, "postal_code": "16841"},
        headers={"X-Request-ID": "lab02-control"},
    )
    control_body = control_response.json()
    control_stock = client.get(f"/products/{control['id']}").json()["current_stock"]
    record = browser["last_checkout"] or {}
    checks = {
        "controlled_save_failure": browser["http_status"] == 500
        and browser["response"].get("code") == "INTERNAL_SERVER_ERROR",
        "request_correlated": browser["request_id"] == "lab02-failure"
        and browser["response"].get("request_id") == "lab02-failure",
        "failed_order_stock_unchanged": before["current_stock"] == 2
        and after["current_stock"] == 2,
        "failed_order_absent_from_browser_history": browser["history"] == []
        and record.get("orders") == []
        and len(record.get("failures", [])) == 1
        and "주문한 내역이 없습니다" in browser["history_page"],
        "control_order_succeeds": control_response.status_code == 201
        and control_stock == 1
        and control_body.get("quantity") == 1
        and control_body.get("total_amount") == 13_000,
        "postal_input_preserved": browser["request"]["postal_code"] == "16841"
        and browser["stored_postal"] == "16841",
    }
    symptom = (
        checks["controlled_save_failure"]
        and checks["request_correlated"]
        and checks["failed_order_absent_from_browser_history"]
        and checks["control_order_succeeds"]
        and before["current_stock"] == 2
        and after["current_stock"] == 1
    )
    return {
        "healthy": all(checks.values()),
        "symptom": symptom,
        "checks": checks,
        "observations": {
            "failure_product_id": failed["id"],
            "control_product_id": control["id"],
            "initial_stock": before["current_stock"],
            "stock_after_failed_order": after["current_stock"],
            "failure_request": browser,
            "control_http_status": control_response.status_code,
            "control_request_id": control_response.headers.get("x-request-id"),
            "control_order": control_body,
            "control_stock": control_stock,
            "database_order_presence": (
                "Runner must verify order presence with read-only DB queries and Oracle"
            ),
        },
    }


def _case03(client: Any, base_url: str, evidence_dir: Path) -> dict[str, Any]:
    products = {
        price: _product(client, name=f"LAB03-Price-{price}", price=price)
        for price in (10_000, 199_999, 200_000)
    }
    scenarios = [
        ("leading_zero", "06236", 10_000, 1, 3_000),
        ("ordinary_control", "16841", 10_000, 1, 3_000),
        ("remote_control", "63309", 10_000, 1, 5_500),
        ("below_remote_boundary", "59999", 10_000, 1, 3_000),
        ("remote_boundary", "60000", 10_000, 1, 5_500),
        ("maximum_prefix", "99999", 10_000, 1, 5_500),
        ("all_zero", "00000", 10_000, 1, 3_000),
        ("zero_before_remote_boundary", "06000", 10_000, 1, 3_000),
        ("zero_before_ordinary_prefix", "05999", 10_000, 1, 3_000),
        ("multiple_leading_zeroes", "00060", 10_000, 1, 3_000),
        ("formatted_postal", "06-236", 10_000, 1, 3_000),
        ("two_items", "16841", 10_000, 2, 3_000),
        ("three_items", "16841", 10_000, 3, 3_700),
        ("three_remote_items", "63309", 10_000, 3, 6_200),
        ("below_free_threshold", "16841", 199_999, 1, 3_000),
        ("free_threshold", "16841", 200_000, 1, 0),
        ("free_remote", "63309", 200_000, 1, 2_500),
        ("free_with_packaging", "16841", 200_000, 3, 700),
    ]
    observations = []
    checks = {}
    for label, postal, price, quantity, fee in scenarios:
        request_id = f"lab03-{label}"
        response = client.post(
            "/orders",
            json={"product_id": products[price]["id"], "quantity": quantity, "postal_code": postal},
            headers={"X-Request-ID": request_id},
        )
        body = response.json()
        passed = (
            response.status_code == 201
            and body.get("postal_code") == postal
            and body.get("unit_price") == price
            and body.get("quantity") == quantity
            and body.get("shipping_fee") == fee
            and body.get("total_amount") == price * quantity + fee
        )
        checks[label] = passed
        observations.append(
            {
                "scenario": label,
                "postal_code": postal,
                "unit_price": price,
                "quantity": quantity,
                "expected_shipping_fee": fee,
                "expected_total_amount": price * quantity + fee,
                "http_status": response.status_code,
                "request_id": response.headers.get("x-request-id"),
                "response": body,
            }
        )
    browser = _checkout_browser(base_url, products[10_000], "06236", evidence_dir, "lab03-browser")
    order = browser["response"]
    checks["browser_estimate_correct"] = browser["before"]["total"] == "13,000원"
    checks["postal_string_preserved"] = (
        browser["before"]["postal"] == "06236"
        and browser["request"].get("postal_code") == "06236"
        and order.get("postal_code") == "06236"
        and browser["stored_postal"] == "06236"
    )
    checks["browser_confirmed_amount_matches_estimate"] = (
        browser["http_status"] == 201
        and order.get("shipping_fee") == 3_000
        and order.get("total_amount") == 13_000
        and "13,000원" in browser["completion"]
        and "13,000원" in browser["history_page"]
    )
    checks["browser_history_matches_response"] = (
        len(browser["history"]) == 1
        and len(browser["history"][0]["orders"]) == 1
        and browser["history"][0]["orders"][0]["id"] == order.get("id")
        and browser["history"][0]["orders"][0]["total_amount"] == order.get("total_amount")
    )
    leading = observations[0]["response"]
    controls = [
        label
        for label, *_ in scenarios
        if label
        not in {
            "leading_zero",
            "zero_before_remote_boundary",
            "multiple_leading_zeroes",
            "formatted_postal",
        }
    ]
    symptom = (
        leading.get("shipping_fee") == 5_500
        and leading.get("total_amount") == 15_500
        and checks["browser_estimate_correct"]
        and checks["postal_string_preserved"]
        and order.get("shipping_fee") == 5_500
        and order.get("total_amount") == 15_500
        and "15,500원" in browser["completion"]
        and "15,500원" in browser["history_page"]
        and checks["browser_history_matches_response"]
        and all(checks[label] for label in controls)
    )
    return {
        "healthy": all(checks.values()),
        "symptom": symptom,
        "checks": checks,
        "observations": {
            "shipping_matrix": observations,
            "browser": browser,
            "independent_expected_rule": (
                "Postal prefixes 60 through 99 add 2500; merchandise below 200000 adds 3000; "
                "each item after two adds 700. Postal strings retain leading zeroes."
            ),
        },
    }


def probe(case_id: str, urls: dict[str, str], evidence_dir: Path) -> dict[str, Any]:
    import httpx

    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    runners = {"01": _case01, "02": _case02, "03": _case03}
    if case_id not in runners:
        raise ValueError(f"Unknown data case: {case_id}")
    with httpx.Client(base_url=urls["app"].rstrip("/"), timeout=30.0, trust_env=False) as client:
        result = runners[case_id](client, urls["app"], evidence_dir)
    (evidence_dir / f"case{case_id}-data-probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
