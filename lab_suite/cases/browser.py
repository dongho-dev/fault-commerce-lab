from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

SUPPORTED = {"07", "08", "09", "10"}
CART_KEY = "faultmart.cart.v1"


def _case(case_id: str) -> str:
    value = str(case_id).removeprefix("case").zfill(2)
    if value not in SUPPORTED:
        raise ValueError(f"Unsupported browser case: {case_id}")
    return value


def replacements(case_id: str) -> list[tuple[str, str, str]]:
    case_id = _case(case_id)
    if case_id == "07":
        before = (
            "        ...[...CATEGORIES, ETC].map((c) => "
            "listProducts({ q, category: c.slug, limit: 1 })),\n"
            "      ]);\n"
            "      if (token !== renderToken) return;\n"
            "      remember(result.items);"
        )
        return [
            (
                "app/frontend/app.js",
                before,
                before.replace("      if (token !== renderToken) return;\n", "", 1),
            )
        ]
    if case_id == "08":
        before = '${esc(p.description || "등록된 상품 설명이 없습니다.")}'
        after = '${p.description || "등록된 상품 설명이 없습니다."}'
        return [("app/frontend/app.js", before, after)]
    if case_id == "09":
        return [(".dockerignore", "artifacts/*\n", "artifacts/*\napp/frontend/app.js\n")]
    before = 'data-quick-add="${p.id}" aria-label="${esc(p.name)} 장바구니 담기"'
    after = 'data-quick-add="${p.id}" aria-label="장바구니 담기"'
    return [("app/frontend/app.js", before, after)]


def install_runtime(case_id: str) -> None:
    _case(case_id)


def _create(client, name: str, *, category: str = "etc", description: str = "") -> dict:
    response = client.post(
        "/products",
        json={
            "name": name,
            "unit_price": 12000,
            "initial_stock": 8,
            "category": category,
            "description": description,
            "list_price": 16000,
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"Fixture POST /products failed: {response.status_code} {response.text}")
    product = response.json()
    reread = client.get(f"/products/{product['id']}")
    reread.raise_for_status()
    if reread.json() != product:
        raise RuntimeError("Product fixture readback did not match its HTTP creation response")
    return product


def _dump(evidence_dir: Path, filename: str, value) -> None:
    (evidence_dir / filename).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cart(page) -> list[dict]:
    return page.evaluate("key => JSON.parse(localStorage.getItem(key) || '[]')", CART_KEY)


def _listing_url(base: str, query: str, category: str = "etc") -> str:
    return f"{base}/#/search?{urlencode({'q': query, 'category': category})}"


def _wait_listing(page, names: list[str]) -> None:
    page.wait_for_function(
        "names => JSON.stringify([...document.querySelectorAll('[data-results] .card-name')]"
        ".map(node => node.textContent).sort()) === JSON.stringify([...names].sort())",
        arg=names,
    )


def _listing_state(page) -> dict:
    return {
        "url": page.url,
        "search_input": page.locator("[data-search-input]").input_value(),
        "title": page.locator(".result-title").inner_text(),
        "product_names": page.locator("[data-results] .card-name").all_text_contents(),
    }


def _case07(page, client, base: str, evidence_dir: Path) -> dict:
    first = _create(client, "여행 파우치")
    second = _create(client, "케이블 정리함")
    singles = []
    for product in (first, second):
        page.goto(_listing_url(base, product["name"]), wait_until="networkidle")
        _wait_listing(page, [product["name"]])
        singles.append(_listing_state(page))

    held = []
    trace = []
    completed_first = []
    expected_requests = 9
    started = time.monotonic()

    def route_request(route):
        query = parse_qs(urlparse(route.request.url).query)
        record = {
            "url": route.request.url,
            "query": query,
            "requested_after_ms": round((time.monotonic() - started) * 1000, 1),
        }
        if query.get("q") == [first["name"]]:
            record["action"] = "held_until_second_results_visible"
            held.append((route, record))
        else:
            record["action"] = "continued"
            route.continue_()
        trace.append(record)

    def request_finished(request):
        query = parse_qs(urlparse(request.url).query)
        if query.get("q") == [first["name"]]:
            completed_first.append(
                {
                    "url": request.url,
                    "finished_after_ms": round((time.monotonic() - started) * 1000, 1),
                }
            )

    page.on("requestfinished", request_finished)
    page.route("**/products?*", route_request)
    page.evaluate(
        "url => { location.hash = new URL(url).hash; }", _listing_url(base, first["name"])
    )
    deadline = time.monotonic() + 15
    while len(held) < expected_requests and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    if len(held) != expected_requests:
        raise RuntimeError(f"Expected 9 held listing/count requests; observed {len(held)}")
    first_pending = _listing_state(page)
    page.evaluate(
        "url => { location.hash = new URL(url).hash; }", _listing_url(base, second["name"])
    )
    _wait_listing(page, [second["name"]])
    second_visible = _listing_state(page)
    second_visible_ms = round((time.monotonic() - started) * 1000, 1)
    for route, record in held:
        response = route.fetch()
        record["upstream_status"] = response.status
        record["upstream_item_names"] = [item["name"] for item in response.json()["items"]]
        record["released_after_ms"] = round((time.monotonic() - started) * 1000, 1)
        route.fulfill(response=response)
    deadline = time.monotonic() + 15
    while len(completed_first) < expected_requests and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    if len(completed_first) != expected_requests:
        raise RuntimeError("Not all released first-query responses completed in the browser")
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    final = _listing_state(page)
    page.screenshot(path=str(evidence_dir / "case07.png"), full_page=True)
    page.unroute("**/products?*", route_request)
    page.remove_listener("requestfinished", request_finished)
    checks = {
        "each_query_alone_shows_matching_product": all(
            state["product_names"] == [product["name"]]
            for state, product in zip(singles, (first, second), strict=True)
        ),
        "all_nine_first_query_requests_held": len(held) == expected_requests,
        "all_nine_late_responses_completed": len(completed_first) == expected_requests,
        "second_product_visible_before_first_released": second_visible["product_names"]
        == [second["name"]],
        "all_held_responses_successful": all(
            record["upstream_status"] == 200 for _, record in held
        ),
        "search_input_and_title_keep_second_query": (
            final["search_input"] == second["name"] and second["name"] in final["title"]
        ),
        "category_etc_kept_in_both_routes": all(
            "category=etc" in state["url"] for state in (first_pending, second_visible, final)
        ),
        "last_query_results_kept": final["product_names"] == [second["name"]],
        "older_query_overwrote_results": final["product_names"] == [first["name"]],
    }
    common = all(
        value
        for key, value in checks.items()
        if key not in {"last_query_results_kept", "older_query_overwrote_results"}
    )
    observations = {
        "fixtures": [first, second],
        "single_query_results": singles,
        "first_query_pending": first_pending,
        "second_query_visible": second_visible,
        "second_visible_after_ms": second_visible_ms,
        "after_first_query_released": final,
        "request_schedule": trace,
        "late_response_completion": completed_first,
        "schedule_method": (
            "Actual HTTP responses held by Playwright routes; no mocked product payloads"
        ),
        "navigation_method": "Real hash routes with category=etc fixed for both queries",
    }
    _dump(evidence_dir, "case07-request-order.json", observations)
    return {
        "healthy": common and checks["last_query_results_kept"],
        "symptom": common and checks["older_query_overwrote_results"],
        "checks": checks,
        "observations": observations,
    }


def _case08(page, client, base: str, evidence_dir: Path) -> dict:
    payload = (
        '<button type="button" onclick="this.textContent=\'LAB-PROBE\'">장바구니 확인</button>'
    )
    product = _create(client, "설명 버튼 검증 상품", description=payload)
    page.goto(f"{base}/#/product/{product['id']}", wait_until="networkidle")
    page.locator("#tab-detail .desc-text").wait_for(state="visible")
    description = page.locator("#tab-detail .desc-text")
    raw_text = description.inner_text()
    html = description.inner_html()
    injected = description.get_by_role("button", name="장바구니 확인", exact=True)
    button_present = injected.count() == 1
    cart_before = _cart(page)
    changed = False
    if button_present:
        injected.click()
        changed = description.get_by_role("button", name="LAB-PROBE", exact=True).count() == 1
    cart_after_probe = _cart(page)
    page.locator("[data-add-cart]").click()
    page.wait_for_function(
        "id => JSON.parse(localStorage.getItem('faultmart.cart.v1') || '[]')"
        ".some(item => item.id === id && item.qty === 1)",
        arg=product["id"],
    )
    cart_after_add = _cart(page)
    page.screenshot(path=str(evidence_dir / "case08.png"), full_page=True)
    page.evaluate("() => { location.hash = '#/cart'; }")
    page.locator(f"[data-line='{product['id']}']").wait_for(state="visible")
    cart_visible = page.locator(".cart-info > a").all_text_contents()
    checks = {
        "stored_description_matches_input": product["description"] == payload,
        "description_shown_as_literal_text": raw_text == payload and not button_present,
        "description_created_interactive_button": button_present,
        "description_button_executes_local_text_change": changed,
        "description_probe_does_not_add_to_cart": cart_before == cart_after_probe == [],
        "existing_add_button_adds_only_expected_product": (
            len(cart_after_add) == 1
            and cart_after_add[0]["id"] == product["id"]
            and cart_after_add[0]["qty"] == 1
            and cart_visible == [product["name"]]
        ),
    }
    common = all(
        checks[key]
        for key in (
            "stored_description_matches_input",
            "description_probe_does_not_add_to_cart",
            "existing_add_button_adds_only_expected_product",
        )
    )
    return {
        "healthy": common and checks["description_shown_as_literal_text"] and not changed,
        "symptom": common and button_present and changed,
        "checks": checks,
        "observations": {
            "fixture": product,
            "description_text_before_click": raw_text,
            "description_html_before_click": html,
            "cart_before_probe": cart_before,
            "cart_after_probe": cart_after_probe,
            "cart_after_existing_add_button": cart_after_add,
            "visible_cart_products": cart_visible,
            "probe_effect": (
                "Only changes the local synthetic button text; "
                "no external requests or cookie access"
            ),
        },
    }


def _case09(page, client, base: str, evidence_dir: Path) -> dict:
    products = [
        _create(client, "새 접속 검증 머그", category="home"),
        _create(client, "새 접속 검증 키보드", category="digital"),
    ]
    ready = client.get("/health/ready")
    listing = client.get("/products")
    static_script = client.get("/static/app.js")
    responses = []
    page.on(
        "response",
        lambda response: responses.append({"url": response.url, "status": response.status}),
    )
    page.goto(f"{base}/", wait_until="networkidle")
    cards = page.locator("#app .card-name").all_text_contents()
    shell = {
        "logo_visible": page.locator(".site-header .logo").is_visible(),
        "search_visible": page.locator("[data-search-input]").is_visible(),
        "footer_visible": page.locator(".site-footer").is_visible(),
    }
    main_html = page.locator("#app").inner_html()
    browser_script = [item for item in responses if urlparse(item["url"]).path == "/static/app.js"]
    page.screenshot(path=str(evidence_dir / "case09.png"), full_page=True)
    api_names = (
        [item["name"] for item in listing.json().get("items", [])]
        if listing.status_code == 200
        else []
    )
    checks = {
        "readiness_endpoint_ready": ready.status_code == 200
        and ready.json().get("status") == "ready",
        "catalog_api_returns_created_products": all(
            product["name"] in api_names for product in products
        ),
        "static_shell_visible": all(shell.values()),
        "fresh_browser_requested_app_script": len(browser_script) == 1,
        "script_available": static_script.status_code == 200
        and any(item["status"] == 200 for item in browser_script),
        "script_missing": static_script.status_code == 404
        and any(item["status"] == 404 for item in browser_script),
        "fresh_browser_home_shows_products": all(product["name"] in cards for product in products),
        "fresh_browser_main_is_empty": not cards and not main_html.strip(),
    }
    common = all(
        checks[key]
        for key in (
            "readiness_endpoint_ready",
            "catalog_api_returns_created_products",
            "static_shell_visible",
            "fresh_browser_requested_app_script",
        )
    )
    return {
        "healthy": common
        and checks["script_available"]
        and checks["fresh_browser_home_shows_products"],
        "symptom": common and checks["script_missing"] and checks["fresh_browser_main_is_empty"],
        "checks": checks,
        "observations": {
            "fixtures": products,
            "ready_status": ready.status_code,
            "catalog_status": listing.status_code,
            "static_app_script_status": static_script.status_code,
            "browser_script_responses": browser_script,
            "visible_product_names": cards,
            "static_shell": shell,
            "main_html_length": len(main_html),
            "browser_context": (
                "Fresh nonpersistent Chromium context with no prior cache or storage"
            ),
            "image_build_verification": (
                "The orchestrator must record successful image build "
                "and omitted app.js filesystem evidence"
            ),
            "preexisting_tab_across_deployment": {
                "verified": False,
                "reason": (
                    "A single phase cannot prove an existing tab survives deployment; "
                    "requires the same tab across baseline-to-fault replacement"
                ),
            },
        },
    }


def _case10(page, client, base: str, evidence_dir: Path) -> dict:
    products = [_create(client, name) for name in ("파란 머그", "접이식 우산", "수납 바구니")]
    page.goto(_listing_url(base, ""), wait_until="networkidle")
    _wait_listing(page, [product["name"] for product in products])
    cdp = page.context.new_cdp_session(page)
    cdp.send("Accessibility.enable")
    full_tree = cdp.send("Accessibility.getFullAXTree")
    _dump(evidence_dir, "case10-accessibility-tree.json", full_tree)
    document = cdp.send("DOM.getDocument", {"depth": 0})
    button_node_ids = cdp.send(
        "DOM.querySelectorAll",
        {"nodeId": document["root"]["nodeId"], "selector": "[data-results] [data-quick-add]"},
    )["nodeIds"]
    records = []
    for node_id in button_node_ids:
        dom_node = cdp.send("DOM.describeNode", {"nodeId": node_id})["node"]
        attrs = dom_node.get("attributes", [])
        attributes = dict(zip(attrs[::2], attrs[1::2], strict=True))
        partial = cdp.send(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": dom_node["backendNodeId"], "fetchRelatives": False},
        )["nodes"]
        matching = [
            node for node in partial if node.get("backendDOMNodeId") == dom_node["backendNodeId"]
        ]
        if len(matching) != 1:
            raise RuntimeError(
                "Could not map a product button to exactly one Chromium accessibility node"
            )
        ax = matching[0]
        records.append(
            {
                "product_id": int(attributes["data-quick-add"]),
                "role": ax.get("role", {}).get("value"),
                "accessible_name": ax.get("name", {}).get("value", ""),
                "ignored": ax.get("ignored", False),
                "backend_node_id": dom_node["backendNodeId"],
            }
        )
    cdp.detach()
    page.screenshot(path=str(evidence_dir / "case10.png"), full_page=True)
    selected = products[1]
    tab_trace = []
    reached = False
    for _ in range(100):
        page.keyboard.press("Tab")
        active = page.evaluate("""() => ({
            tag: document.activeElement.tagName,
            quick_add: document.activeElement.getAttribute('data-quick-add'),
            name: document.activeElement.getAttribute('aria-label')
                || document.activeElement.textContent.trim()
        })""")
        tab_trace.append(active)
        if active["quick_add"] == str(selected["id"]):
            reached = True
            break
    if reached:
        page.keyboard.press("Enter")
        page.wait_for_function(
            "id => JSON.parse(localStorage.getItem('faultmart.cart.v1') || '[]')"
            ".some(item => item.id === id && item.qty === 1)",
            arg=selected["id"],
        )
    cart = _cart(page)
    page.evaluate("() => { location.hash = '#/cart'; }")
    page.wait_for_load_state("networkidle")
    if reached:
        page.locator(f"[data-line='{selected['id']}']").wait_for(state="visible")
    visible_cart = page.locator(".cart-info > a").all_text_contents()
    page.screenshot(path=str(evidence_dir / "case10-keyboard-cart.png"), full_page=True)
    by_id = {product["id"]: product for product in products}
    accessible_names = [record["accessible_name"] for record in records]
    checks = {
        "three_expected_product_buttons_in_accessibility_tree": (
            len(records) == 3
            and {record["product_id"] for record in records} == set(by_id)
            and all(record["role"] == "button" and not record["ignored"] for record in records)
        ),
        "button_names_include_corresponding_product": all(
            by_id[record["product_id"]]["name"] in record["accessible_name"] for record in records
        ),
        "button_names_are_distinct": len(set(accessible_names)) == 3,
        "all_button_names_are_generic": accessible_names == ["장바구니 담기"] * 3,
        "keyboard_tab_reaches_selected_button": reached,
        "keyboard_enter_adds_only_selected_product": (
            len(cart) == 1
            and cart[0]["id"] == selected["id"]
            and cart[0]["qty"] == 1
            and visible_cart == [selected["name"]]
        ),
    }
    common = all(
        checks[key]
        for key in (
            "three_expected_product_buttons_in_accessibility_tree",
            "keyboard_tab_reaches_selected_button",
            "keyboard_enter_adds_only_selected_product",
        )
    )
    observations = {
        "fixtures": products,
        "accessibility_buttons": records,
        "keyboard_target": selected,
        "keyboard_tab_trace": tab_trace,
        "cart_after_enter": cart,
        "visible_cart_products": visible_cart,
        "verification_environment": (
            "Chromium CDP accessibility tree and Playwright Tab/Enter inside Linux Docker"
        ),
        "screen_reader_audio": {
            "verified": False,
            "reason": "NVDA or other screen-reader speech was not listened to",
        },
        "desktop_input": "No host desktop input, pointer capture, or Pointer Lock is used",
    }
    _dump(evidence_dir, "case10-accessibility-and-keyboard.json", observations)
    return {
        "healthy": common
        and checks["button_names_include_corresponding_product"]
        and checks["button_names_are_distinct"],
        "symptom": common and checks["all_button_names_are_generic"],
        "checks": checks,
        "observations": observations,
    }


def probe(case_id: str, urls: dict[str, str], evidence_dir: Path) -> dict:
    if sys.platform != "linux" or not Path("/.dockerenv").is_file():
        raise RuntimeError("Browser probes must run in the isolated Linux Docker probe container")

    import httpx
    from playwright.sync_api import sync_playwright

    case_id = _case(case_id)
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    base = urls["app"].rstrip("/")
    with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000}, locale="ko-KR"
                )
                page = context.new_page()
                page.set_default_timeout(15000)
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                functions = {"07": _case07, "08": _case08, "09": _case09, "10": _case10}
                result = functions[case_id](page, client, base, evidence_dir)
                result["checks"]["no_browser_page_errors"] = not page_errors
                result["observations"]["browser_page_errors"] = page_errors
                result["observations"]["browser_version"] = browser.version
                result["observations"]["fixture_creation_method"] = (
                    "POST /products with GET readback; no direct DB writes"
                )
                if page_errors:
                    result["healthy"] = False
                    result["symptom"] = False
                _dump(evidence_dir, f"case{case_id}-browser-result.json", result)
                return result
            finally:
                browser.close()
