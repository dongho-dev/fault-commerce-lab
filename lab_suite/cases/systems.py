import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path


def replacements(case_id: str) -> list[tuple[str, str, str]]:
    if case_id == "04":
        return [
            (
                "app/observability/middleware.py",
                'normalized_path = getattr(route, "path", "__unmatched__")',
                "normalized_path = request.url.path + "
                '("?" + request.url.query if request.url.query else "")',
            )
        ]
    if case_id == "05":
        return [
            (
                "lab_suite/network_proxy.py",
                "                body = upstream_response.read()",
                "                body = upstream_response.read(8192)",
            )
        ]
    if case_id == "06":
        return [
            (
                "app/services/product.py",
                "from app.services.errors import ProductNotFoundError",
                "from app.services.errors import ProductNotFoundError\n"
                "from lab_suite.cache_adapter import cached_snapshot",
            ),
            (
                "app/services/product.py",
                "    def get(self, product_id: int) -> ProductSnapshot:",
                "    @cached_snapshot\n    def get(self, product_id: int) -> ProductSnapshot:",
            ),
            (
                "app/services/order.py",
                "from app.services.shipping import ShippingQuoteService, calculate_total_amount",
                "from app.services.shipping import ShippingQuoteService, calculate_total_amount\n"
                "from lab_suite.cache_adapter import invalidate_after_order",
            ),
            (
                "app/services/order.py",
                "    def create(self, *, product_id: int, quantity: int, "
                "postal_code: str) -> OrderSnapshot:",
                "    @invalidate_after_order\n"
                "    def create(self, *, product_id: int, quantity: int, "
                "postal_code: str) -> OrderSnapshot:",
            ),
        ]
    raise ValueError(f"unsupported systems case: {case_id}")


def install_runtime(case_id: str) -> None:
    if case_id not in {"04", "05", "06"}:
        raise ValueError(f"unsupported systems case: {case_id}")


def probe(case_id: str, urls: dict[str, str], evidence_dir: Path) -> dict:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if case_id == "04":
        return asyncio.run(_probe_metrics(urls, evidence_dir))
    if case_id == "05":
        return _probe_transport(urls)
    if case_id == "06":
        return _probe_cache(urls)
    raise ValueError(f"unsupported systems case: {case_id}")


def _runtime(evidence_dir: Path) -> dict:
    try:
        return json.loads((evidence_dir / "runtime.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _metric_summary(payload: str) -> dict:
    output = {"request_series": 0, "query_request_series": 0}
    for line in payload.splitlines():
        if line.startswith("commerce_http_requests_total{"):
            output["request_series"] += 1
            output["query_request_series"] += "?q=" in line
        for metric in ("process_resident_memory_bytes", "process_start_time_seconds"):
            if line.startswith(metric + " "):
                output[metric] = float(line.split()[1])
    return output


async def _probe_metrics(urls: dict[str, str], evidence_dir: Path) -> dict:
    import httpx

    request_count = int(os.environ.get("LAB04_REQUESTS", "12000"))
    concurrency = int(os.environ.get("LAB04_CONCURRENCY", "8"))
    stage_timeout = float(os.environ.get("LAB04_STAGE_TIMEOUT_SECONDS", "1800"))
    request_timeout = float(os.environ.get("LAB04_REQUEST_TIMEOUT_SECONDS", "5"))
    pressure_path = os.environ.get("LAB04_PRESSURE_PATH", "/health/live")
    padding_bytes = int(os.environ.get("LAB04_PADDING_BYTES", "48000"))
    if not 0 <= padding_bytes <= 48000:
        raise ValueError("LAB04_PADDING_BYTES must be between 0 and 48000")
    padding = "x" * padding_bytes
    customer_interval = float(os.environ.get("LAB04_CUSTOMER_INTERVAL_SECONDS", "0.1"))
    recovery_timeout = float(os.environ.get("LAB04_RECOVERY_TIMEOUT_SECONDS", "60"))
    if request_count < 128 or not 1 <= concurrency <= 20:
        raise ValueError("case 04 requires >=128 requests and concurrency 1..20")
    if min(stage_timeout, request_timeout, recovery_timeout) <= 0 or customer_interval < 0.1:
        raise ValueError("timeouts must be positive and customer interval must be >=0.1 seconds")
    if pressure_path not in {"/health/live", "/products"}:
        raise ValueError("LAB04_PRESSURE_PATH must be /health/live or /products")
    base = urls["direct"].rstrip("/")
    initial_runtime = _runtime(evidence_dir)
    runtime_deadline = time.monotonic() + 15
    while not initial_runtime and time.monotonic() < runtime_deadline:
        await asyncio.sleep(0.25)
        initial_runtime = _runtime(evidence_dir)
    if not initial_runtime:
        raise RuntimeError("case 04 runtime.json was not readable within 15 seconds")
    initial_restarts = int(initial_runtime.get("restart_count", 0))
    progress_path = evidence_dir / "metrics-pressure.jsonl"
    active_stage = "initial"
    probe_started = time.monotonic()
    customer = {
        "attempted": 0,
        "completed": 0,
        "succeeded": 0,
        "errors": 0,
        "availability_errors": 0,
        "bad_payloads": 0,
        "first_errors": [],
        "first_availability_errors": [],
        "by_path": {},
        "by_stage": {},
        "interval_seconds": customer_interval,
    }
    last_healthy_cycle_started = 0.0
    stop_monitor = asyncio.Event()
    with progress_path.open("w", encoding="utf-8") as progress:
        limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
        monitor_limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
        async with (
            httpx.AsyncClient(
                base_url=base, timeout=request_timeout, limits=limits, trust_env=False
            ) as client,
            httpx.AsyncClient(
                base_url=base, timeout=request_timeout, limits=monitor_limits, trust_env=False
            ) as monitor_client,
        ):
            fixture_response = await client.post(
                "/products",
                json={
                    "name": "case04-customer-" + uuid.uuid4().hex[:10],
                    "unit_price": 10000,
                    "initial_stock": 5,
                },
            )
            fixture_response.raise_for_status()
            product_id = fixture_response.json()["id"]
            customer_paths = ("/products?limit=1", f"/products/{product_id}")

            def counters():
                return {
                    "attempted": 0,
                    "completed": 0,
                    "succeeded": 0,
                    "errors": 0,
                    "availability_errors": 0,
                    "bad_payloads": 0,
                }

            async def customer_read(path: str) -> bool:
                stage = active_stage
                path_counts = customer["by_path"].setdefault(path, counters())
                stage_counts = customer["by_stage"].setdefault(stage, counters())
                groups = (customer, path_counts, stage_counts)
                for group in groups:
                    group["attempted"] += 1
                started_at = time.time()
                status_code = None
                availability_error = False
                try:
                    response = await monitor_client.get(path)
                    status_code = response.status_code
                    if status_code != 200:
                        availability_error = status_code >= 500
                        raise RuntimeError(f"HTTP {status_code}")
                    body = response.json()
                    if path == customer_paths[0]:
                        valid = (
                            isinstance(body, dict)
                            and body.get("total") == 1
                            and len(body.get("items", [])) == 1
                            and body["items"][0].get("id") == product_id
                            and body["items"][0].get("current_stock") == 5
                        )
                    else:
                        valid = (
                            isinstance(body, dict)
                            and body.get("id") == product_id
                            and body.get("current_stock") == 5
                        )
                    if not valid:
                        for group in groups:
                            group["bad_payloads"] += 1
                        raise RuntimeError("unexpected product response body")
                    for group in groups:
                        group["succeeded"] += 1
                    return True
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    availability_error |= isinstance(exc, httpx.TransportError)
                    for group in groups:
                        group["errors"] += 1
                        group["availability_errors"] += availability_error
                    item = {
                        "path": path,
                        "stage": stage,
                        "started_at_unix_seconds": round(started_at, 6),
                        "observed_at_unix_seconds": round(time.time(), 6),
                        "elapsed_from_probe_seconds": round(time.monotonic() - probe_started, 3),
                        "status_code": status_code,
                        "type": type(exc).__name__,
                        "message": str(exc)[:180],
                        "availability_error": availability_error,
                        "runtime": _runtime(evidence_dir),
                    }
                    if len(customer["first_errors"]) < 5:
                        customer["first_errors"].append(item)
                    if availability_error and len(customer["first_availability_errors"]) < 5:
                        customer["first_availability_errors"].append(item)
                    return False
                finally:
                    for group in groups:
                        group["completed"] += 1

            async def monitor_customers():
                nonlocal last_healthy_cycle_started
                while not stop_monitor.is_set():
                    cycle_started = time.monotonic()
                    outcomes = await asyncio.gather(
                        *(customer_read(path) for path in customer_paths)
                    )
                    if all(outcomes):
                        last_healthy_cycle_started = cycle_started
                    try:
                        await asyncio.wait_for(stop_monitor.wait(), timeout=customer_interval)
                    except TimeoutError:
                        pass

            async def metrics():
                response = await client.get("/metrics")
                response.raise_for_status()
                return _metric_summary(response.text)

            initial_metrics = await metrics()

            async def load(label: str, count: int, unique: bool, offset: int = 0):
                nonlocal active_stage
                active_stage = label
                started = time.monotonic()
                next_index = 0
                result = {
                    "planned": count,
                    "attempted": 0,
                    "completed": 0,
                    "succeeded": 0,
                    "errors": 0,
                    "bad_payloads": 0,
                    "first_errors": [],
                    "runtime_samples": [],
                    "restart_observed": False,
                    "oom_killed_observed": False,
                    "peak_observed_memory_bytes": 0,
                    "deadline_reached": False,
                }

                def sample_runtime():
                    state = _runtime(evidence_dir)
                    if state:
                        result["peak_observed_memory_bytes"] = max(
                            result["peak_observed_memory_bytes"],
                            int(state.get("memory_usage") or 0),
                        )
                        result["restart_observed"] |= (
                            int(state.get("restart_count", 0)) > initial_restarts
                        )
                        result["oom_killed_observed"] |= bool(state.get("oom_killed", False))
                    return state

                def checkpoint():
                    state = sample_runtime()
                    item = {
                        "stage": label,
                        "pressure_path": pressure_path,
                        "attempted": result["attempted"],
                        "completed": result["completed"],
                        "succeeded": result["succeeded"],
                        "errors": result["errors"],
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "runtime": state,
                        "customer_attempted": customer["attempted"],
                        "customer_errors": customer["errors"],
                    }
                    progress.write(json.dumps(item, separators=(",", ":")) + "\n")
                    progress.flush()
                    result["runtime_samples"].append(item)
                    if len(result["runtime_samples"]) > 12:
                        result["runtime_samples"] = (
                            result["runtime_samples"][:3] + result["runtime_samples"][-9:]
                        )

                async def worker():
                    nonlocal next_index
                    while next_index < count:
                        if result["restart_observed"] or result["oom_killed_observed"]:
                            return
                        if time.monotonic() - started >= stage_timeout:
                            result["deadline_reached"] = True
                            return
                        index = next_index
                        next_index += 1
                        query_index = offset + index + 1 if unique else 0
                        query = f"metric-{query_index:09d}-" + "x" * 83
                        params = {"q": query}
                        if label != "unique-query-sample" and padding:
                            params["diagnostic"] = padding
                        if pressure_path == "/products":
                            params["limit"] = 1
                        result["attempted"] += 1
                        try:
                            response = await client.get(pressure_path, params=params)
                            if response.status_code != 200:
                                raise RuntimeError(f"HTTP {response.status_code}")
                            body = response.json()
                            valid = (
                                body == {"status": "live"}
                                if pressure_path == "/health/live"
                                else isinstance(body, dict)
                                and body.get("total") == 0
                                and body.get("items") == []
                            )
                            if not valid:
                                result["bad_payloads"] += 1
                                raise RuntimeError("unexpected pressure response body")
                            result["succeeded"] += 1
                        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                            result["errors"] += 1
                            if len(result["first_errors"]) < 5:
                                result["first_errors"].append(
                                    {
                                        "request": query_index,
                                        "type": type(exc).__name__,
                                        "message": str(exc)[:180],
                                        "observed_at_unix_seconds": round(time.time(), 6),
                                    }
                                )
                            sample_runtime()
                            await asyncio.sleep(0.1)
                        finally:
                            result["completed"] += 1
                        if result["completed"] % 1000 == 0:
                            checkpoint()

                await asyncio.gather(*(worker() for _ in range(concurrency)))
                checkpoint()
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                result["all_started_requests_completed"] = (
                    result["attempted"] == result["completed"]
                )
                return result

            monitor_task = asyncio.create_task(monitor_customers())
            try:
                control = await load("same-query-control", request_count, False)
                control_metrics = await metrics() if control["errors"] == 0 else {}
                sample_count = 64
                sample = await load("unique-query-sample", sample_count, True)
                sample_metrics = await metrics() if sample["errors"] == 0 else {}
                leak_observed = (
                    sample_metrics.get("query_request_series", 0)
                    - control_metrics.get("query_request_series", 0)
                    >= sample_count
                )
                stress = await load(
                    "unique-query-stress", request_count - sample_count, True, sample_count
                )
                active_stage = "recovery"
                pressure_finished = time.monotonic()
                health_required_after = pressure_finished
                restart_detected = False
                recovered = False
                final_runtime = _runtime(evidence_dir)
                while time.monotonic() - pressure_finished < recovery_timeout:
                    if monitor_task.done():
                        await monitor_task
                    final_runtime = _runtime(evidence_dir)
                    restarted = (
                        stress["restart_observed"]
                        or stress["oom_killed_observed"]
                        or int(final_runtime.get("restart_count", 0)) > initial_restarts
                        or bool(final_runtime.get("oom_killed", False))
                    )
                    if restarted and not restart_detected:
                        restart_detected = True
                        health_required_after = time.monotonic()
                    recovered = last_healthy_cycle_started > health_required_after
                    if recovered and time.monotonic() - pressure_finished >= 3.5:
                        break
                    await asyncio.sleep(0.1)
                recovery_seconds = round(time.monotonic() - pressure_finished, 3)
            finally:
                stop_monitor.set()
                await monitor_task
            final_runtime = _runtime(evidence_dir)
            restarted = (
                restart_detected
                or stress["restart_observed"]
                or stress["oom_killed_observed"]
                or int(final_runtime.get("restart_count", 0)) > initial_restarts
                or bool(final_runtime.get("oom_killed", False))
            )
            control_customer = customer["by_stage"].get("same-query-control", counters())
            control_passed = (
                control["succeeded"] == request_count
                and control["errors"] == 0
                and control["bad_payloads"] == 0
                and not control["restart_observed"]
                and control_customer["attempted"] > 0
                and control_customer["errors"] == 0
            )
            unique_succeeded = sample["succeeded"] + stress["succeeded"]
            unique_errors = sample["errors"] + stress["errors"]
            unique_passed = (
                unique_succeeded == request_count
                and unique_errors == 0
                and sample["bad_payloads"] + stress["bad_payloads"] == 0
            )
            customer_failure_observed = any(
                counts["availability_errors"] > 0
                for stage, counts in customer["by_stage"].items()
                if stage in {"unique-query-sample", "unique-query-stress", "recovery"}
            )
            checks = {
                "same_query_control_completed": control_passed,
                "unique_queries_completed": unique_passed,
                "bounded_metric_cardinality": not leak_observed,
                "no_container_restart": not restarted,
                "runtime_monitor_present": bool(initial_runtime) and bool(final_runtime),
                "customer_queries_all_succeeded": customer["errors"] == 0,
                "customer_queries_recovered": recovered,
                "all_started_pressure_requests_completed": all(
                    stage["all_started_requests_completed"] for stage in (control, sample, stress)
                ),
                "customer_monitor_closed": monitor_task.done(),
            }
            return {
                "healthy": all(checks.values()),
                "symptom": (
                    control_passed
                    and leak_observed
                    and restarted
                    and customer_failure_observed
                    and recovered
                ),
                "checks": checks,
                "observations": {
                    "pressure_path": pressure_path,
                    "validation_method": (
                        "DB 작업을 제외하고 큰 유효 URL로 보유 메모리 누적을 가속한 합성 입력"
                        if pressure_path == "/health/live"
                        else "DB 조회를 포함하는 동일 검색어 및 고유 검색어 합성 입력"
                    ),
                    "padding_bytes": padding_bytes,
                    "sample_padding_bytes": 0,
                    "accelerated_input": True,
                    "operational_capacity_claim": False,
                    "requested_per_stage": request_count,
                    "concurrency": concurrency,
                    "stage_timeout_seconds": stage_timeout,
                    "request_timeout_seconds": request_timeout,
                    "query_characters": 100,
                    "fixture_product_id": product_id,
                    "fixture_products_created": 1,
                    "initial_metrics": initial_metrics,
                    "after_control_metrics": control_metrics,
                    "after_sample_metrics": sample_metrics,
                    "initial_runtime": initial_runtime,
                    "final_runtime": final_runtime,
                    "control": control,
                    "unique_sample": sample,
                    "unique_stress": stress,
                    "unique_total_attempted": sample["attempted"] + stress["attempted"],
                    "unique_total_completed": sample["completed"] + stress["completed"],
                    "unique_total_succeeded": unique_succeeded,
                    "unique_total_errors": unique_errors,
                    "customer_queries": customer,
                    "customer_availability_failure_observed": customer_failure_observed,
                    "customer_recovery_confirmed": recovered,
                    "recovery_wait_seconds": recovery_seconds,
                    "leak_observed": leak_observed,
                    "restart_observed": restarted,
                    "oom_event_confirmation_required": True,
                    "large_metrics_scrape_avoided": True,
                },
            }


def _create_product(client, base: str, name: str, description: str = "") -> dict:
    response = client.post(
        base.rstrip("/") + "/products",
        json={
            "name": name,
            "unit_price": 10000,
            "initial_stock": 5,
            "description": description,
            "image_url": "/static/assets/products/blanket.jpg",
        },
    )
    response.raise_for_status()
    return response.json()


def _capture_response(client, url: str) -> dict:
    import httpx

    body = bytearray()
    status_code = None
    content_length = None
    content_type = ""
    error_type = None
    error_message = None
    try:
        with client.stream("GET", url) as response:
            status_code = response.status_code
            content_length = response.headers.get("content-length")
            content_type = response.headers.get("content-type", "")
            for chunk in response.iter_bytes():
                body.extend(chunk)
    except httpx.HTTPError as exc:
        error_type = type(exc).__name__
        error_message = str(exc)[:240]
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        data = None
    return {
        "status_code": status_code,
        "content_length": int(content_length) if content_length is not None else None,
        "received_bytes": len(body),
        "content_type": content_type,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "json_valid": data is not None,
        "json_total": data.get("total") if isinstance(data, dict) else None,
        "error_type": error_type,
        "error_message": error_message,
    }


def _same_body(left: dict, right: dict) -> bool:
    return (
        left["status_code"] == right["status_code"] == 200
        and left["error_type"] is None
        and right["error_type"] is None
        and left["body_sha256"] == right["body_sha256"]
    )


def _probe_transport(urls: dict[str, str]) -> dict:
    import httpx

    direct = urls["direct"].rstrip("/")
    proxy = urls.get("proxy", urls["app"]).rstrip("/")
    prefix = "case05-" + uuid.uuid4().hex[:10]
    with httpx.Client(timeout=15, trust_env=False) as client:
        products = [
            _create_product(client, proxy, f"{prefix}-{index}", "item detail " + "D" * 1680)
            for index in range(8)
        ]
        small_path = f"/products/{products[0]['id']}"
        large_path = f"/products?q={prefix}&limit=100"
        direct_small = _capture_response(client, direct + small_path)
        proxy_small = _capture_response(client, proxy + small_path)
        direct_large = _capture_response(client, direct + large_path)
        proxy_large = _capture_response(client, proxy + large_path)
        static_results = {}
        for path in (
            "/",
            "/static/styles.css",
            "/static/app.js",
            "/static/assets/products/blanket.jpg",
        ):
            left = _capture_response(client, direct + path)
            right = _capture_response(client, proxy + path)
            static_results[path] = {
                "direct": left,
                "proxy": right,
                "equal": _same_body(left, right),
            }
        direct_after = _capture_response(client, direct + large_path)
    short_preserved = _same_body(direct_small, proxy_small)
    length_boundary = direct_small["received_bytes"] < 8192 < direct_large["received_bytes"]
    intact_direct = (
        _same_body(direct_large, direct_after)
        and direct_large["json_valid"]
        and direct_large["json_total"] == 8
    )
    intact_static = all(item["equal"] for item in static_results.values())
    truncated = (
        proxy_large["received_bytes"] == 8192
        and proxy_large["content_length"] == direct_large["received_bytes"]
        and proxy_large["error_type"] == "RemoteProtocolError"
        and not proxy_large["json_valid"]
    )
    checks = {
        "small_json_preserved": short_preserved,
        "payload_sizes_cross_8192_boundary": length_boundary,
        "direct_large_json_intact": intact_direct,
        "static_assets_preserved": intact_static,
        "large_json_preserved": _same_body(direct_large, proxy_large),
        "post_through_proxy_succeeded": len(products) == 8,
    }
    return {
        "healthy": all(checks.values()),
        "symptom": short_preserved
        and length_boundary
        and intact_direct
        and intact_static
        and truncated,
        "checks": checks,
        "observations": {
            "direct_small": direct_small,
            "proxy_small": proxy_small,
            "direct_large": direct_large,
            "proxy_large": proxy_large,
            "direct_large_after": direct_after,
            "static": static_results,
            "fixture_products_created": len(products),
            "truncation_boundary_bytes": 8192,
        },
    }


def _probe_cache(urls: dict[str, str]) -> dict:
    import httpx

    nodes = {"a": urls["direct"].rstrip("/"), "b": urls["secondary"].rstrip("/")}
    public = urls.get("proxy", urls["app"]).rstrip("/")
    prefix = "case06-" + uuid.uuid4().hex[:10]
    with httpx.Client(timeout=10, trust_env=False) as client:

        def stock(base: str, product_id: int) -> int:
            response = client.get(f"{base}/products/{product_id}")
            response.raise_for_status()
            return response.json()["current_stock"]

        def order(base: str, product_id: int) -> dict:
            response = client.post(
                base + "/orders",
                json={
                    "product_id": product_id,
                    "quantity": 1,
                    "postal_code": "12345",
                },
            )
            response.raise_for_status()
            return response.json()

        sequential = {}
        for node, base in nodes.items():
            product = _create_product(client, base, f"{prefix}-single-{node}")
            trace = [stock(base, product["id"])]
            for _ in range(3):
                order(base, product["id"])
                trace.append(stock(base, product["id"]))
            sequential[node] = trace
        directions = []
        for writer, reader in (("a", "b"), ("b", "a")):
            name = f"{prefix}-{writer}-writes"
            product = _create_product(client, nodes[writer], name)
            product_id = product["id"]
            warm = {node: stock(base, product_id) for node, base in nodes.items()}
            committed = order(nodes[writer], product_id)
            after = {node: stock(base, product_id) for node, base in nodes.items()}
            database_views = {}
            for node, base in nodes.items():
                response = client.get(base + "/products", params={"q": name})
                response.raise_for_status()
                page = response.json()
                database_views[node] = {
                    "total": page["total"],
                    "stocks": [item["current_stock"] for item in page["items"]],
                    "ids": [item["id"] for item in page["items"]],
                }
            public_reads = [stock(public, product_id) for _ in range(8)]
            directions.append(
                {
                    "writer": writer,
                    "reader": reader,
                    "product_id": product_id,
                    "warm": warm,
                    "after": after,
                    "database_views": database_views,
                    "order_id": committed["id"],
                    "order_status": committed["status"],
                    "order_quantity": committed["quantity"],
                    "public_reads": public_reads,
                }
            )
    single_pass = all(trace == [5, 4, 3, 2] for trace in sequential.values())
    warm_pass = all(item["warm"] == {"a": 5, "b": 5} for item in directions)
    orders_pass = all(
        item["order_status"] == "CONFIRMED" and item["order_quantity"] == 1 for item in directions
    )
    database_pass = all(
        view == {"total": 1, "stocks": [4], "ids": [item["product_id"]]}
        for item in directions
        for view in item["database_views"].values()
    )
    direct_consistent = all(item["after"] == {"a": 4, "b": 4} for item in directions)
    public_consistent = all(item["public_reads"] == [4] * 8 for item in directions)
    cross_node_stale = all(
        item["after"][item["writer"]] == 4 and item["after"][item["reader"]] == 5
        for item in directions
    )
    public_oscillates = all(set(item["public_reads"]) == {4, 5} for item in directions)
    checks = {
        "single_node_sequential_freshness": single_pass,
        "both_nodes_warmed_at_five": warm_pass,
        "orders_committed_once": orders_pass,
        "shared_database_stock_is_four": database_pass,
        "both_nodes_read_committed_stock": direct_consistent,
        "public_round_robin_reads_consistent": public_consistent,
    }
    return {
        "healthy": all(checks.values()),
        "symptom": (
            single_pass
            and warm_pass
            and orders_pass
            and database_pass
            and cross_node_stale
            and public_oscillates
        ),
        "checks": checks,
        "observations": {
            "single_node_stock_traces": sequential,
            "directions": directions,
            "cross_node_staleness_observed": cross_node_stale,
            "public_read_oscillation_observed": public_oscillates,
            "cache_ttl_seconds": float(os.environ.get("LAB06_CACHE_TTL_SECONDS", "120")),
            "scope": "product detail snapshots, two one-worker nodes, sequential committed writes",
        },
    }
