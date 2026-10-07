from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

PRODUCT_NAME = "LAB09-Continuity"


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _time_left(deadline: float, maximum: float) -> float:
    remaining = min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("The deployment continuity probe exceeded its total timeout")
    return remaining


def _status(evidence: Path, result: dict, phase: str, **details) -> None:
    status = {
        "phase": phase,
        "run_id": result["run_id"],
        "updated_at": _stamp(),
        **details,
    }
    result["observations"]["status_events"].append(status)
    _write_json(evidence / "continuity.json", result)
    _write_json(evidence / "status.json", status)
    print(json.dumps(status, ensure_ascii=False), flush=True)


def _await_signal(evidence: Path, phase: str, deadline: float, phase_timeout: float) -> dict:
    phase_deadline = min(deadline, time.monotonic() + phase_timeout)
    last_signal = None
    last_read_error = None
    while time.monotonic() < phase_deadline:
        try:
            signal = json.loads((evidence / "signal.json").read_text(encoding="utf-8-sig"))
            last_signal = signal
            if isinstance(signal, dict) and signal.get("phase") == phase:
                return {"phase": phase, "received_at": _stamp(), "signal": signal}
        except (FileNotFoundError, json.JSONDecodeError, PermissionError) as exc:
            last_read_error = str(exc)
        time.sleep(min(0.2, max(0.001, phase_deadline - time.monotonic())))
    raise TimeoutError(
        f"Timed out waiting for signal phase {phase!r}; "
        f"last signal={last_signal!r}; last read error={last_read_error!r}"
    )


def _health(client) -> dict:
    response = client.get("/health/ready")
    body = response.json()
    return {
        "url": str(response.request.url),
        "status": response.status_code,
        "body": body,
        "ready": response.status_code == 200 and body.get("status") == "ready",
    }


def _fixture(client) -> dict:
    response = client.post(
        "/products",
        json={
            "name": PRODUCT_NAME,
            "category": "digital",
            "unit_price": 12000,
            "list_price": 16000,
            "initial_stock": 8,
            "description": "Synthetic product for deployment continuity verification",
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"Fixture creation failed: {response.status_code} {response.text}")
    product = response.json()
    reread = client.get(f"/products/{product['id']}")
    reread.raise_for_status()
    if reread.json() != product:
        raise RuntimeError("Created product did not survive its HTTP readback")
    return product


def _require_checks(result: dict, keys: list[str]) -> None:
    failed = [key for key in keys if result["checks"].get(key) is not True]
    if failed:
        raise AssertionError(f"Deployment continuity checks failed: {', '.join(failed)}")


def _attach_events(page, label: str, state: dict, result: dict) -> None:
    def response_received(response):
        result["observations"]["responses"].append(
            {
                "page": label,
                "phase": state["phase"],
                "url": response.url,
                "status": response.status,
                "resource_type": response.request.resource_type,
                "received_at": _stamp(),
            }
        )

    def request_failed(request):
        result["observations"]["request_failures"].append(
            {
                "page": label,
                "phase": state["phase"],
                "url": request.url,
                "failure": request.failure,
            }
        )

    page.on("response", response_received)
    page.on("requestfailed", request_failed)
    page.on(
        "pageerror",
        lambda error: result["observations"]["page_errors"].append(
            {"page": label, "phase": state["phase"], "message": str(error)}
        ),
    )


def _screenshot(page, evidence: Path, result: dict, label: str) -> None:
    filename = f"case09-continuity-{label}.png"
    page.screenshot(path=str(evidence / filename), full_page=True)
    result["screenshots"][label] = filename


def _home(page, base: str, deadline: float) -> dict:
    page.goto(f"{base}/", wait_until="networkidle", timeout=_time_left(deadline, 20) * 1000)
    page.wait_for_function(
        "name => [...document.querySelectorAll('#app .card-name')]"
        ".some(node => node.textContent === name)",
        arg=PRODUCT_NAME,
        timeout=_time_left(deadline, 15) * 1000,
    )
    return {
        "url": page.url,
        "time_origin": page.evaluate("performance.timeOrigin"),
        "visible_products": page.locator("#app .card-name").all_text_contents(),
        "fixture_visible": page.locator("#app .card-name")
        .filter(has_text=PRODUCT_NAME)
        .first.is_visible(),
    }


def _search(page, query: str, product: dict, deadline: float) -> dict:
    def matches(response):
        parts = urlparse(response.url)
        params = parse_qs(parts.query)
        return (
            parts.path == "/products"
            and params.get("q") == [query]
            and params.get("category") == ["digital"]
            and params.get("limit") == ["20"]
        )

    previous_url = page.url
    with page.expect_response(matches, timeout=_time_left(deadline, 20) * 1000) as pending:
        page.locator("[data-search-input]").fill(query)
        page.locator("[data-search-category]").select_option("digital")
        page.locator("[data-search-form] button[type='submit']").click()
    response = pending.value
    payload = response.json()
    page.wait_for_function(
        "name => JSON.stringify([...document.querySelectorAll('[data-results] .card-name')]"
        ".map(node => node.textContent)) === JSON.stringify([name])",
        arg=PRODUCT_NAME,
        timeout=_time_left(deadline, 15) * 1000,
    )
    visible = page.locator("[data-results] .card-name").all_text_contents()
    title = page.locator(".result-title").inner_text()
    return {
        "previous_url": previous_url,
        "url": page.url,
        "query": query,
        "time_origin": page.evaluate("performance.timeOrigin"),
        "request_url": response.url,
        "request_status": response.status,
        "response_product_ids": [item["id"] for item in payload.get("items", [])],
        "visible_products": visible,
        "search_input": page.locator("[data-search-input]").input_value(),
        "title": title,
        "passed": (
            response.status == 200
            and [item["id"] for item in payload.get("items", [])] == [product["id"]]
            and visible == [PRODUCT_NAME]
            and page.locator("[data-search-input]").input_value() == query
            and query in title
            and previous_url != page.url
        ),
    }


def _new_context(browser, deadline: float):
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}, locale="ko-KR", service_workers="block"
    )
    context.set_default_timeout(_time_left(deadline, 15) * 1000)
    return context


def _run(base: str, evidence: Path, phase_timeout: float, total_timeout: float) -> int:
    result = {
        "passed": False,
        "run_id": str(uuid4()),
        "started_at": _stamp(),
        "checks": {},
        "observations": {
            "base_url": base,
            "status_events": [],
            "signals": [],
            "responses": [],
            "request_failures": [],
            "page_errors": [],
            "fixture_method": "POST /products and GET readback in the isolated lab database",
            "deployment_method": (
                "The external orchestrator replaces the app container image at the same app alias; "
                "this probe never intercepts, fulfills, or substitutes browser responses"
            ),
        },
        "browser_environment": {
            "platform": sys.platform,
            "docker_container": Path("/.dockerenv").is_file(),
            "browser": "Playwright Chromium headless",
            "host_desktop_input": False,
            "pointer_lock": False,
            "network_interception": False,
        },
        "screenshots": {},
    }
    evidence.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + total_timeout
    browser = None
    page = None
    state = {"phase": "healthy"}
    try:
        if sys.platform != "linux" or not Path("/.dockerenv").is_file():
            raise RuntimeError("Deployment continuity requires an isolated Linux Docker container")

        import httpx
        from playwright.sync_api import sync_playwright

        with httpx.Client(
            base_url=base,
            timeout=15,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            healthy_api = _health(client)
            result["observations"]["healthy_api"] = healthy_api
            result["checks"]["healthy_api_ready"] = healthy_api["ready"]
            _require_checks(result, ["healthy_api_ready"])
            product = _fixture(client)
            result["observations"]["fixture"] = product
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                result["browser_environment"]["chromium_version"] = browser.version
                try:
                    existing_context = _new_context(browser, deadline)
                    page = existing_context.new_page()
                    _attach_events(page, "existing", state, result)
                    healthy_home = _home(page, base, deadline)
                    _screenshot(page, evidence, result, "healthy-home")
                    healthy_search = _search(page, "LAB09", product, deadline)
                    _screenshot(page, evidence, result, "healthy-search")
                    result["observations"]["healthy_home"] = healthy_home
                    result["observations"]["healthy_search"] = healthy_search
                    result["checks"].update(
                        {
                            "healthy_home_fixture_visible": healthy_home["fixture_visible"],
                            "healthy_search_works": healthy_search["passed"],
                            "healthy_search_keeps_document": (
                                healthy_search["time_origin"] == healthy_home["time_origin"]
                            ),
                            "healthy_script_loaded": any(
                                response["page"] == "existing"
                                and urlparse(response["url"]).path == "/static/app.js"
                                and response["status"] == 200
                                for response in result["observations"]["responses"]
                            ),
                        }
                    )
                    _require_checks(result, list(result["checks"]))
                    _status(evidence, result, "healthy_ready", passed=True)
                    result["observations"]["signals"].append(
                        _await_signal(evidence, "fault", deadline, phase_timeout)
                    )
                    state["phase"] = "fault"
                    fault_api = _health(client)
                    missing_script = client.get("/static/app.js")
                    fault_existing = _search(page, PRODUCT_NAME, product, deadline)
                    _screenshot(page, evidence, result, "fault-existing-tab")
                    result["observations"]["fault_api"] = fault_api
                    result["observations"]["fault_existing_search"] = fault_existing
                    new_context = _new_context(browser, deadline)
                    new_page = new_context.new_page()
                    _attach_events(new_page, "fault-new", state, result)
                    new_page.goto(
                        f"{base}/",
                        wait_until="networkidle",
                        timeout=_time_left(deadline, 20) * 1000,
                    )
                    new_cards = new_page.locator("#app .card-name").all_text_contents()
                    new_main = new_page.locator("#app").inner_html()
                    new_shell = {
                        "logo": new_page.locator(".site-header .logo").is_visible(),
                        "search": new_page.locator("[data-search-input]").is_visible(),
                        "footer": new_page.locator(".site-footer").is_visible(),
                    }
                    _screenshot(new_page, evidence, result, "fault-new-context")
                    new_script = [
                        response
                        for response in result["observations"]["responses"]
                        if response["page"] == "fault-new"
                        and urlparse(response["url"]).path == "/static/app.js"
                    ]
                    result["observations"]["fault_new_context"] = {
                        "url": new_page.url,
                        "visible_products": new_cards,
                        "main_html_length": len(new_main),
                        "shell": new_shell,
                        "script_responses": new_script,
                        "independent_script_http_status": missing_script.status_code,
                        "fresh_context": True,
                    }
                    result["checks"].update(
                        {
                            "fault_api_still_ready": fault_api["ready"],
                            "fault_script_http_404": missing_script.status_code == 404,
                            "existing_tab_search_after_deployment_works": fault_existing["passed"],
                            "existing_tab_keeps_original_document": (
                                fault_existing["time_origin"] == healthy_home["time_origin"]
                            ),
                            "existing_tab_did_not_reload_script": not any(
                                response["page"] == "existing"
                                and response["phase"] == "fault"
                                and urlparse(response["url"]).path == "/static/app.js"
                                for response in result["observations"]["responses"]
                            ),
                            "new_context_script_404": len(new_script) == 1
                            and new_script[0]["status"] == 404,
                            "new_context_shell_visible": all(new_shell.values()),
                            "new_context_main_empty": not new_cards and not new_main.strip(),
                        }
                    )
                    _require_checks(result, list(result["checks"]))
                    _status(evidence, result, "fault_checked", passed=True)
                    result["observations"]["signals"].append(
                        _await_signal(evidence, "restored", deadline, phase_timeout)
                    )
                    state["phase"] = "restored"
                    restored_api = _health(client)
                    restored_context = _new_context(browser, deadline)
                    restored_page = restored_context.new_page()
                    _attach_events(restored_page, "restored-new", state, result)
                    restored_home = _home(restored_page, base, deadline)
                    _screenshot(restored_page, evidence, result, "restored-home")
                    restored_search = _search(restored_page, PRODUCT_NAME, product, deadline)
                    _screenshot(restored_page, evidence, result, "restored-search")
                    result["observations"]["restored_api"] = restored_api
                    result["observations"]["restored_home"] = restored_home
                    result["observations"]["restored_search"] = restored_search
                    result["checks"].update(
                        {
                            "restored_api_ready": restored_api["ready"],
                            "restored_new_context_home_visible": restored_home["fixture_visible"],
                            "restored_new_context_search_works": restored_search["passed"],
                            "restored_new_context_script_200": any(
                                response["page"] == "restored-new"
                                and urlparse(response["url"]).path == "/static/app.js"
                                and response["status"] == 200
                                for response in result["observations"]["responses"]
                            ),
                            "no_browser_page_errors": not result["observations"]["page_errors"],
                        }
                    )
                    _require_checks(result, list(result["checks"]))
                    result["passed"] = True
                finally:
                    browser.close()
                    browser = None
        result["finished_at"] = _stamp()
        _status(evidence, result, "finished", passed=True)
        return 0
    except Exception as exc:
        result["passed"] = False
        result["finished_at"] = _stamp()
        result["error"] = {
            "phase": state["phase"],
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        if browser is not None and page is not None:
            try:
                _screenshot(page, evidence, result, "error")
            except Exception as screenshot_error:
                result["error"]["screenshot_error"] = str(screenshot_error)
        _status(evidence, result, "finished", passed=False, error=result["error"])
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://app:8000")
    parser.add_argument("--evidence", type=Path, default=Path("/evidence"))
    parser.add_argument("--phase-timeout", type=float, default=120)
    parser.add_argument("--total-timeout", type=float, default=360)
    args = parser.parse_args()
    if args.phase_timeout <= 0 or args.total_timeout <= 0:
        parser.error("Timeouts must be positive")
    return _run(args.base_url.rstrip("/"), args.evidence, args.phase_timeout, args.total_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
