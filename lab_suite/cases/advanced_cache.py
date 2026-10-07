import json
import sys
import time
import uuid
from urllib.parse import urlencode, urlsplit


def _case(case_id):
    if case_id != "15":
        raise ValueError(f"Unsupported conditional-response case: {case_id}")




def install_runtime(case_id):
    _case(case_id)


def configure_compose(case_id, definition, source, evidence):
    _case(case_id)
    services = definition["services"]
    services["app"].pop("ports", None)
    services["proxy"] = {
        "image": services["app"]["image"],
        "command": ["python", "-m", "lab_suite.cache_gateway"],
        "cpus": 0.5,
        "mem_limit": "128m",
        "environment": {"UPSTREAM": "http://app:8000"},
        "ports": ["127.0.0.1:18115:8080"],
        "depends_on": {"app": {"condition": "service_healthy"}},
    }
    services["probe"]["environment"]["LAB_BASE_URL"] = "http://proxy:8080"
    return definition


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _create(client, name, brand, category, price):
    payload = {
        "name": name, "brand": brand, "category": category,
        "unit_price": price, "initial_stock": 10,
        "description": name + " 상품 안내입니다.",
    }
    response = client.post("/products", json=payload)
    response.raise_for_status()
    product = response.json()
    if any(product.get(key) != value for key, value in payload.items()):
        raise RuntimeError("API fixture payload was not preserved")
    detail = client.get(f"/products/{product['id']}")
    detail.raise_for_status()
    if detail.json() != product or product.get("current_stock") != 10:
        raise RuntimeError("API fixture failed independent detail readback")
    return product


def _path(query, category, sort="price_asc"):
    return "/products?" + urlencode({
        "q": query, "category": category, "sort": sort, "limit": 20, "offset": 0,
    })


def _listing_expected(products, brand, category, sort="price_asc"):
    selected = [product for product in products if product["brand"] == brand
                and product["category"] == category]
    selected.sort(key=lambda product: (
        product["unit_price"] if sort == "price_asc" else -product["unit_price"], product["id"]
    ))
    return {"items": selected, "total": len(selected), "limit": 20, "offset": 0}


def _snapshot(client, path, expected):
    response = client.get(path)
    if response.status_code != 200 or response.json() != expected:
        raise RuntimeError(f"Unconditional catalogue control failed: {path}")
    return {"path": path, "etag": response.headers.get("etag"), "body": response.json()}


def _conditional(client, previous, expected, label):
    headers = {"If-None-Match": previous["etag"]} if previous["etag"] else {}
    response = client.get(previous["path"], headers=headers)
    returned = None
    if response.status_code == 304:
        effective = previous["body"]
        framing_ok = response.content == b""
    elif response.status_code == 200:
        try:
            returned = response.json()
        except ValueError:
            returned = None
        effective = returned
        framing_ok = returned is not None
    else:
        effective = None
        framing_ok = False
    changed = previous["body"] != expected
    prior_tag = previous["etag"]
    returned_tag = response.headers.get("etag")
    validator_ok = not (
        changed and prior_tag and returned_tag
        and prior_tag.strip().removeprefix("W/") == returned_tag.strip().removeprefix("W/")
    )
    return {
        "label": label, "path": previous["path"],
        "sent_validator": previous["etag"], "status": response.status_code,
        "returned_validator": returned_tag,
        "representation_changed": changed,
        "advertised_validator_consistent": validator_ok,
        "expected_ids": [item["id"] for item in expected["items"]],
        "effective_ids": [item["id"] for item in effective.get("items", [])]
        if isinstance(effective, dict) else None,
        "correct": framing_ok and effective == expected and validator_ok,
        "returned_body": returned,
    }


class NativeNetworkLedger:
    def __init__(self, context, page):
        self.records = {}
        self.session = context.new_cdp_session(page)
        self.session.on("Network.requestWillBeSent", self.request)
        self.session.on("Network.requestWillBeSentExtraInfo", self.request_extra)
        self.session.on("Network.responseReceived", self.response)
        self.session.on("Network.responseReceivedExtraInfo", self.response_extra)
        self.session.send("Network.enable")

    def _record(self, request_id):
        return self.records.setdefault(request_id, {})

    def request(self, event):
        request = event["request"]
        record = self._record(event["requestId"])
        record.update(url=request["url"], method=request["method"],
                      request_headers=request.get("headers", {}))

    def request_extra(self, event):
        self._record(event["requestId"])["wire_request_headers"] = event.get("headers", {})

    def response(self, event):
        response = event["response"]
        self._record(event["requestId"]).update(
            logical_status=response["status"],
            from_disk_cache=response.get("fromDiskCache", False),
            from_service_worker=response.get("fromServiceWorker", False),
        )

    def response_extra(self, event):
        self._record(event["requestId"]).update(
            wire_status=event["statusCode"], wire_response_headers=event.get("headers", {})
        )

    def mark(self):
        return set(self.records)

    def since(self, mark, target=None):
        result = []
        for request_id, record in self.records.items():
            if request_id in mark or urlsplit(record.get("url", "")).path != "/products":
                continue
            if target is not None:
                url = urlsplit(record["url"])
                if url.path + "?" + url.query != target:
                    continue
            result.append({"request_id": request_id, **record})
        return result

    def all(self):
        return self.since(set())


def _view(page, base, brand, category, evidence, label, prior_product=None):
    target = base + "/#/search?" + urlencode({
        "q": brand, "category": category, "sort": "price_asc",
    })
    if page.url == target:
        if prior_product is None:
            raise RuntimeError("A normal away/back navigation requires an existing product")
        page.goto(base + f"/#/product/{prior_product['id']}",
                  wait_until="networkidle", timeout=15000)
        page.locator(".pdp-info h1").wait_for(timeout=10000)
    page.goto(target, wait_until="networkidle", timeout=15000)
    page.locator("[data-results] .card-name").first.wait_for(timeout=10000)
    result = {
        "url": page.url,
        "names": page.locator("[data-results] .card-name").all_text_contents(),
        "ids": page.locator("[data-results] [data-quick-add]").evaluate_all(
            "nodes => nodes.map(node => Number(node.dataset.quickAdd))"
        ),
        "total": page.locator("[data-total]").inner_text(),
    }
    page.screenshot(path=str(evidence / f"{label}.png"), full_page=True)
    return result


def _native_revalidation(records):
    return any(record.get("wire_status") == 304 and any(
        key.lower() == "if-none-match" for key in record.get("wire_request_headers", {})
    ) for record in records)


def probe(case_id, urls, evidence_dir):
    _case(case_id)
    if sys.platform != "linux":
        raise RuntimeError("Native browser cache verification requires isolated Linux")
    import httpx
    from playwright.sync_api import sync_playwright

    evidence_dir.mkdir(parents=True, exist_ok=True)
    base = urls["app"].rstrip("/")
    brand = "여행상점-" + uuid.uuid4().hex[:8]
    paths = {"primary": _path(brand, "digital"), "variant": _path(brand, "home"),
             "nonmatching": _path(brand + "-none", "digital")}
    products = []
    protocol = []
    views = []
    mutation_checks = []
    native_matching = []
    page_errors = []
    with httpx.Client(base_url=urls["direct"], timeout=10, trust_env=False) as origin, \
            httpx.Client(base_url=base, timeout=10, trust_env=False) as wire, \
            sync_playwright() as playwright:
        first = _create(origin, "여행 파우치 소형", brand, "digital", 12000)
        other = _create(origin, "여행 정리함 소형", brand, "home", 15000)
        products.extend([first, other])
        expected = {
            "primary": _listing_expected(products, brand, "digital"),
            "variant": _listing_expected(products, brand, "home"),
            "nonmatching": _listing_expected(products, brand + "-none", "digital"),
        }
        saved = {key: _snapshot(wire, path, expected[key]) for key, path in paths.items()}
        for key in paths:
            protocol.append(_conditional(wire, saved[key], expected[key], f"unchanged-{key}"))
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        try:
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)[:200]))
            ledger = NativeNetworkLedger(context, page)
            views.append({"label": "initial-primary",
                          **_view(page, base, brand, "digital", evidence_dir, "case15-before")})
            views.append({"label": "initial-variant",
                          **_view(page, base, brand, "home", evidence_dir,
                                  "case15-variant-before")})
            mark = ledger.mark()
            unchanged_view = _view(page, base, brand, "digital", evidence_dir,
                                   "case15-unchanged", first)
            unchanged_native = ledger.since(mark, paths["primary"])
            views.append({"label": "unchanged-primary", **unchanged_view})
            if views[0]["ids"] != [first["id"]] or views[1]["ids"] != [other["id"]] \
                    or unchanged_view["ids"] != [first["id"]]:
                raise RuntimeError("Initial native browser controls failed")

            unrelated = _create(origin, "여행 정리함 중형", brand, "home", 18000)
            products.append(unrelated)
            expected["variant"] = _listing_expected(products, brand, "home")
            protocol.append(_conditional(wire, saved["primary"], expected["primary"],
                                         "nonmatching-mutation-primary"))
            protocol.append(_conditional(wire, saved["variant"], expected["variant"],
                                         "matching-mutation-variant"))
            protocol.append(_conditional(wire, saved["nonmatching"], expected["nonmatching"],
                                         "nonmatching-mutation-empty-query"))
            unrelated_view = _view(page, base, brand, "digital", evidence_dir,
                                   "case15-unrelated-change", first)
            views.append({"label": "nonmatching-mutation-primary", **unrelated_view})
            variant_view = _view(page, base, brand, "home", evidence_dir,
                                 "case15-variant-changed")
            views.append({"label": "matching-mutation-variant", **variant_view})

            for number, name, price in ((1, "여행 파우치 중형", 16000),
                                        (2, "여행 파우치 대형", 19000)):
                created = _create(origin, name, brand, "digital", price)
                products.append(created)
                expected["primary"] = _listing_expected(products, brand, "digital")
                direct_list = _snapshot(origin, paths["primary"], expected["primary"])
                fresh_wire = _snapshot(wire, paths["primary"], expected["primary"])
                direct_detail = wire.get(f"/products/{created['id']}")
                direct_detail.raise_for_status()
                mutation_checks.append(direct_detail.json() == created
                                       and direct_list["body"] == fresh_wire["body"])
                protocol.append(_conditional(wire, saved["primary"], expected["primary"],
                                             f"matching-mutation-primary-{number}"))
                mark = ledger.mark()
                observed = _view(page, base, brand, "digital", evidence_dir,
                                 f"case15-after-{number}", first)
                records = ledger.since(mark, paths["primary"])
                native_matching.append({
                    "number": number,
                    "expected_ids": [row["id"] for row in expected["primary"]["items"]],
                    "observed_ids": observed["ids"], "records": records,
                    "native_304": _native_revalidation(records),
                })
                views.append({"label": f"matching-mutation-primary-{number}", **observed})
                fresh = browser.new_context(viewport={"width": 1280, "height": 1000})
                try:
                    fresh_page = fresh.new_page()
                    fresh_view = _view(fresh_page, base, brand, "digital", evidence_dir,
                                       f"case15-fresh-{number}")
                    mutation_checks.append(
                        fresh_view["ids"] == [row["id"] for row in expected["primary"]["items"]]
                    )
                    views.append({"label": f"fresh-primary-{number}", **fresh_view})
                finally:
                    fresh.close()
                page.goto(base + f"/#/product/{created['id']}", wait_until="networkidle",
                          timeout=15000)
                page.locator(".pdp-info h1").wait_for(timeout=10000)
                mutation_checks.append(page.locator(".pdp-info h1").inner_text() == created["name"])
                page.screenshot(path=str(evidence_dir / f"case15-direct-product-{number}.png"),
                                full_page=True)

            current = _snapshot(wire, paths["primary"], expected["primary"])
            order = origin.post("/orders", json={
                "product_id": first["id"], "quantity": 1, "postal_code": "16841",
            })
            order.raise_for_status()
            if order.status_code != 201 or order.json().get("product_id") != first["id"]:
                raise RuntimeError("Stock freshness regression order failed")
            first["current_stock"] -= 1
            expected["primary"] = _listing_expected(products, brand, "digital")
            _snapshot(origin, paths["primary"], expected["primary"])
            protocol.append(_conditional(wire, current, expected["primary"], "stock-freshness"))
            _snapshot(wire, paths["variant"], expected["variant"])
            protocol.append(_conditional(wire, saved["nonmatching"], expected["nonmatching"],
                                         "nonmatching-query-after-all-mutations"))
            native_records = ledger.all()
        finally:
            context.close()
            browser.close()
    unchanged_controls = all(row["correct"] for row in protocol
                             if not row["representation_changed"])
    browser_controls = unrelated_view["ids"] == [first["id"]] and all(mutation_checks)
    missing_matching = all(
        item["observed_ids"] == [first["id"]]
        and item["observed_ids"] != item["expected_ids"]
        and item["native_304"] for item in native_matching
    )
    fresh_matching = all(
        item["observed_ids"] == item["expected_ids"] for item in native_matching
    )
    changed_primary = [row for row in protocol
                       if row["label"].startswith("matching-mutation-primary")]
    bad_revalidation = all(row["status"] == 304 and not row["correct"] for row in changed_primary)
    healthy = (unchanged_controls and browser_controls and fresh_matching
               and all(row["correct"] for row in protocol)
               and variant_view["ids"] == [other["id"], unrelated["id"]] and not page_errors)
    symptom = (unchanged_controls and browser_controls and missing_matching
               and bad_revalidation and not page_errors)
    observations = {
        "brand": brand, "paths": paths, "views": views, "protocol": protocol,
        "native_matching": native_matching, "unchanged_native": unchanged_native,
        "native_network": native_records, "page_errors": page_errors,
        "fixture_ids": [product["id"] for product in products],
        "finished_at_unix_seconds": time.time(),
    }
    _dump(evidence_dir / "case15-conditional-evidence.json", observations)
    return {
        "healthy": bool(healthy), "symptom": bool(symptom),
        "checks": {"unchanged_representations_correct": unchanged_controls,
                   "fresh_browser_and_direct_data_correct": browser_controls,
                   "existing_browser_current": fresh_matching,
                   "changed_representation_protocol_correct": all(
                       row["correct"] for row in protocol if row["representation_changed"]
                   ), "native_stale_revalidation_observed": missing_matching},
        "observations": observations,
    }
