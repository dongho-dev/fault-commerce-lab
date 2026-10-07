import asyncio
import copy
import json
import socket
import statistics
import sys
import time
import uuid
from urllib.parse import urlencode

SUPPORTED = {"13", "14", "15"}




def install_runtime(case_id):
    if case_id not in SUPPORTED:
        raise ValueError(f"Unsupported advanced systems case: {case_id}")
    if case_id != "15":
        return
    from contextvars import ContextVar

    from sqlalchemy import event

    from app.database import engine
    from app.main import app
    from app.observability.logging import log_event

    current = ContextVar("catalogue_query_observation", default=None)

    def count_query(*args):
        record = current.get()
        if record is not None:
            record["queries"] += 1

    class QueryObservation:
        def __init__(self, app):
            self.application = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or scope["path"] != "/products":
                return await self.application(scope, receive, send)
            record = {"queries": 0}
            token = current.set(record)
            try:
                return await self.application(scope, receive, send)
            finally:
                current.reset(token)
                log_event(event="catalogue_query_observation", **record)

    event.listen(engine, "before_cursor_execute", count_query)
    app.add_middleware(QueryObservation)


def configure_compose(case_id, definition, source, evidence):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    services = definition["services"]
    app = services["app"]
    app["environment"].update(DATABASE_POOL_SIZE="30", DATABASE_MAX_OVERFLOW="30")
    if case_id == "13":
        app["ports"] = ["127.0.0.1:19113:8000"]
        app["networks"] = {"default": {"aliases": ["catalog-service"]}}
        for name in ("app-b", "app-c"):
            replacement = copy.deepcopy(app)
            replacement.pop("build", None)
            replacement.pop("ports", None)
            replacement["profiles"] = ["replacement"]
            replacement["environment"]["LAB_MIGRATE"] = "0"
            services[name] = replacement
        services["proxy"] = {
            "image": app["image"],
            "command": ["python", "-m", "lab_suite.advanced_proxy"],
            "environment": {"UPSTREAM": "http://catalog-service:8000"},
            "cpus": 0.5, "mem_limit": "128m",
            "ports": ["127.0.0.1:18113:8080"],
            "depends_on": {"app": {"condition": "service_healthy"}},
        }
        services["probe"]["environment"]["LAB_BASE_URL"] = "http://proxy:8080"
    elif case_id == "14":
        app["command"] = ["python", "-m", "lab_suite.advanced_server"]
        app["environment"].update(DATABASE_POOL_SIZE="8", DATABASE_MAX_OVERFLOW="8",
                                  LAB_EVIDENCE="/evidence")
        app.setdefault("volumes", []).append(
            {"type": "bind", "source": evidence.as_posix(), "target": "/evidence"}
        )
        app["stop_grace_period"] = "15s"
    else:
        services["db"].pop("cpus", None)
        services["db"].pop("mem_limit", None)
    return definition


def _write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _wait_file(path, stage, run_id=None, stop=None, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = _read(path)
        if value and value.get("stage") == stage and (
            run_id is None or value.get("run_id") == run_id
        ):
            if value.get("error"):
                raise RuntimeError(value["error"])
            return value
        if stop is not None:
            if stop.wait(0.1):
                raise RuntimeError("Exercise controller stopped")
        else:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {path.name} stage {stage}")


def _decoded(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def control(case_id, compose_args, evidence_dir, stop_event, run_command):
    if case_id == "15":
        return {"passed": True, "operations": []}
    request = evidence_dir / f"case{case_id}-request.json"
    response = evidence_dir / f"case{case_id}-response.json"
    observations = []
    generations = {}
    run_id = None
    try:
        for stage in (1, 2):
            signal = _wait_file(request, stage, run_id, stop_event, timeout=180)
            run_id = signal["run_id"]
            if case_id == "14":
                run_command([*compose_args, "restart", "--timeout", "12", "app"],
                            log=evidence_dir / "controller.log", timeout=35)
                run_command([*compose_args, "up", "-d", "--no-deps", "--wait",
                             "--wait-timeout", "75", "app"],
                            log=evidence_dir / "controller.log", timeout=90)
                observation = {"stage": stage, "operation": "worker_generation_replaced"}
            else:
                old_name = "app" if stage == 1 else "app-b"
                active_names = ("app-b", "app-c") if stage == 1 else ("app-c",)

                def inspect(service):
                    cid = _decoded(run_command([*compose_args, "ps", "-q", service],
                                              capture=True, timeout=15)).strip()
                    if not cid:
                        raise RuntimeError(f"Missing project-owned service {service}")
                    data = json.loads(_decoded(run_command(
                        ["docker", "inspect", cid], capture=True, timeout=15
                    )))[0]
                    project = compose_args[compose_args.index("-p") + 1]
                    labels = data["Config"].get("Labels", {})
                    if (labels.get("com.docker.compose.project") != project
                            or labels.get("com.docker.compose.service") != service):
                        raise RuntimeError("Container ownership mismatch")
                    return cid, data

                if stage == 1:
                    inspect(old_name)
                    run_command([*compose_args, "up", "-d", "--no-deps", "--wait",
                                 "--wait-timeout", "35", "app-b", "app-c"],
                                log=evidence_dir / "controller.log", timeout=45)
                attached = {name: inspect(name) for name in (old_name, *active_names)}
                networks = set.intersection(*(
                    set(data["NetworkSettings"]["Networks"])
                    for _, data in attached.values()
                ))
                if len(networks) != 1:
                    raise RuntimeError("Expected one shared exercise network")
                network = networks.pop()
                for name, (cid, data) in attached.items():
                    state = data["State"]
                    if not state["Running"] or state.get("Health", {}).get("Status") != "healthy":
                        raise RuntimeError(f"Application is not healthy before drain: {name}")
                    endpoint = {"id": cid, "ip": data["NetworkSettings"]["Networks"]
                                [network]["IPAddress"], "direct": f"http://{name}:8000"}
                    if not endpoint["ip"]:
                        raise RuntimeError(f"Missing endpoint address: {name}")
                    if name in generations and generations[name] != endpoint:
                        raise RuntimeError(f"Application endpoint changed unexpectedly: {name}")
                    generations[name] = endpoint
                if (len(generations) != 3
                        or len({item["id"] for item in generations.values()}) != 3
                        or len({item["ip"] for item in generations.values()}) != 3):
                    raise RuntimeError("All three application endpoints must be distinct")
                if stop_event.is_set():
                    raise RuntimeError("Exercise controller stopped")
                run_command(["docker", "network", "disconnect", network,
                             generations[old_name]["id"]],
                            log=evidence_dir / "controller.log", timeout=20)
                run_command([*compose_args, "stop", "--timeout", "10", old_name],
                            log=evidence_dir / "controller.log", timeout=25)
                expected_ips = sorted(generations[name]["ip"] for name in active_names)
                resolutions = {}
                for name in active_names:
                    aliases = _decoded(run_command(
                        [*compose_args, "exec", "-T", name, "python", "-c",
                         "import json,socket; print(json.dumps(sorted({item[4][0] "
                         "for item in socket.getaddrinfo('catalog-service',8000,"
                         "socket.AF_INET,socket.SOCK_STREAM)})))"],
                        capture=True, timeout=15
                    ))
                    resolved = json.loads(aliases)
                    if resolved != expected_ips:
                        raise RuntimeError(f"Service alias not converged on {name}: {resolved}")
                    resolutions[name] = resolved
                observation = {
                    "stage": stage, "retired": old_name, "network": network,
                    "generations": copy.deepcopy(generations), "resolved": expected_ips,
                    "resolutions": resolutions,
                    "active_backends": [{"service": name, **generations[name]}
                                        for name in active_names],
                }
            observations.append(observation)
            _write(response, {"run_id": run_id, "stage": stage, **observation})
        return {"passed": True, "operations": observations}
    except Exception as exc:
        _write(response, {"run_id": run_id, "stage": len(observations) + 1,
                          "error": str(exc)})
        return {"passed": False, "operations": observations, "error": str(exc)}


def _create(client, **fields):
    payload = {"unit_price": 12000, "initial_stock": 10, "category": "digital", **fields}
    response = client.post("/products", json=payload)
    response.raise_for_status()
    product = response.json()
    if any(product.get(key) != value for key, value in payload.items()):
        raise RuntimeError("Fixture creation did not preserve its payload")
    if product.get("current_stock") != payload["initial_stock"]:
        raise RuntimeError("Fixture inventory was not initialized correctly")
    return product


def _browser_product(base, product, evidence, label):
    if sys.platform != "linux":
        raise RuntimeError("Browser observation requires isolated Linux")
    from playwright.sync_api import sync_playwright

    result = {"visible": False, "errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda error: result["errors"].append(str(error)[:200]))
            started = time.monotonic()
            response = page.goto(f"{base}/#/product/{product['id']}", wait_until="domcontentloaded",
                                 timeout=10000)
            result["status"] = response.status if response else None
            try:
                page.locator(".pdp-info h1").wait_for(timeout=6000)
                result["visible"] = page.locator(".pdp-info h1").inner_text() == product["name"]
            except Exception as exc:
                result["errors"].append(str(exc)[:180])
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            result["text"] = page.locator("body").inner_text()[:600]
            page.screenshot(path=str(evidence / f"{label}.png"), full_page=True)
        except Exception as exc:
            result["errors"].append(str(exc)[:200])
        finally:
            browser.close()
    return result


def _catalogue_reads(client, products, count=20):
    observed = []
    expected = {product["id"]: product for product in products}
    for index in range(count):
        product = products[index % len(products)]
        path = (f"/products/{product['id']}" if index % 2
                else "/products?" + urlencode({"q": products[0]["brand"], "limit": 20}))
        item = {"path": path, "status": None, "valid": False}
        started = time.monotonic()
        try:
            response = client.get(path)
            item["status"] = response.status_code
            body = response.json()
            if "/products/" in path:
                item["valid"] = response.status_code == 200 and body == product
            else:
                actual = {row["id"]: row for row in body.get("items", [])}
                item["valid"] = response.status_code == 200 and all(
                    actual.get(key) == value for key, value in expected.items()
                )
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        item["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        observed.append(item)
        time.sleep(0.05)
    return observed


def _probe13(urls, evidence):
    import httpx

    run_id = uuid.uuid4().hex
    base = urls["app"].rstrip("/")
    with httpx.Client(base_url=base, timeout=7, trust_env=False) as client:
        products = [_create(client, name=f"생활 소품 {run_id[:6]}-{index}",
                            brand=f"case13-{run_id[:8]}") for index in range(4)]
        initial = _catalogue_reads(client, products)
        before_browser = _browser_product(base, products[0], evidence, "case13-before")
        if not all(row["valid"] for row in initial) or not before_browser["visible"]:
            raise RuntimeError("Initial customer control failed")
        rounds = []
        for stage in (1, 2):
            _write(evidence / "case13-request.json", {"run_id": run_id, "stage": stage})
            lifecycle = _wait_file(evidence / "case13-response.json", stage, run_id, timeout=120)
            active = lifecycle["active_backends"]
            expected_names = ["app-b", "app-c"] if stage == 1 else ["app-c"]
            if [backend["service"] for backend in active] != expected_names:
                raise RuntimeError("Unexpected active application set")
            resolved = sorted({item[4][0] for item in socket.getaddrinfo(
                "catalog-service", 8000, socket.AF_INET, socket.SOCK_STREAM
            )})
            if resolved != sorted(backend["ip"] for backend in active):
                raise RuntimeError("Probe observed an unexpected customer service address set")
            controls = []
            for backend in active:
                with httpx.Client(base_url=backend["direct"], timeout=7,
                                  trust_env=False) as direct:
                    ready = direct.get("/health/ready")
                    if ready.status_code != 200 or ready.json() != {"status": "ready"}:
                        raise RuntimeError(f"Active application is not ready: {backend['service']}")
                    control_rows = _catalogue_reads(direct, products, count=4)
                if not all(row["valid"] for row in control_rows):
                    raise RuntimeError("Replacement direct application control failed")
                controls.append({"backend": backend, "requests": control_rows})
            rows = _catalogue_reads(client, products)
            browser = _browser_product(base, products[0], evidence, f"case13-after-{stage}")
            rounds.append({"lifecycle": lifecycle, "direct": controls, "resolved": resolved,
                           "customer": rows, "browser": browser})
        healthy = all(all(row["valid"] for row in stage["customer"])
                      and stage["browser"]["visible"] for stage in rounds)
        symptom = all(all(not row["valid"] and row["status"] == 502
                          for row in stage["customer"])
                      and not stage["browser"]["visible"] for stage in rounds)
        return {"healthy": healthy, "symptom": symptom,
                "observations": {"initial": initial, "before_browser": before_browser,
                                 "replacements": rounds}}


async def _parallel_catalogue(base, products):
    import httpx

    expected = {product["id"]: product for product in products}
    samples = []
    deadline = time.monotonic() + 30
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=0)
    async with httpx.AsyncClient(base_url=base, timeout=5, limits=limits,
                                trust_env=False) as client:
        async def shopper(worker):
            for number in range(20):
                if time.monotonic() >= deadline:
                    return
                product = products[(worker + number) % len(products)]
                path = (f"/products/{product['id']}" if number % 2
                        else "/products?q=CS14%20catalogue%20item&limit=20")
                item = {"path": path, "valid": False, "status": None}
                started = time.monotonic()
                try:
                    response = await client.get(path)
                    item["status"] = response.status_code
                    item["worker"] = response.headers.get("x-lab-worker")
                    body = response.json()
                    if "/products/" in path:
                        item["valid"] = response.status_code == 200 and body == product
                    else:
                        actual = {row["id"]: row for row in body.get("items", [])}
                        item["valid"] = response.status_code == 200 and actual == expected
                except Exception as exc:
                    item["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                item["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
                samples.append(item)
                await asyncio.sleep(0.05)
        await asyncio.gather(*(shopper(index) for index in range(8)))
    return samples


def _probe14(urls, evidence):
    products = _read(evidence / "case14-fixtures.json")
    if not isinstance(products, list) or len(products) != 12:
        raise RuntimeError("Prefork API fixture ledger is unavailable")
    run_id = uuid.uuid4().hex
    rounds = []
    for generation in range(3):
        if generation:
            _write(evidence / "case14-request.json", {"run_id": run_id, "stage": generation})
            _wait_file(evidence / "case14-response.json", generation, run_id, timeout=120)
        rows = asyncio.run(_parallel_catalogue(urls["app"], products))
        rounds.append({"generation": generation, "requests": rows,
                       "valid": sum(row["valid"] for row in rows)})
    browser = _browser_product(urls["app"], products[0], evidence, "case14-product")
    successful = sum(stage["valid"] for stage in rounds)
    total = sum(len(stage["requests"]) for stage in rounds)
    workers_by_generation = [
        sorted({row["worker"] for row in stage["requests"] if row.get("worker")})
        for stage in rounds
    ]
    both_workers_observed = all(len(workers) == 2 for workers in workers_by_generation)
    return {"healthy": successful == total == 480 and browser["visible"]
                       and both_workers_observed,
            "symptom": 0 < successful < total and both_workers_observed,
            "checks": {"both_workers_observed": both_workers_observed},
            "observations": {"generations": rounds, "browser": browser,
                             "workers_by_generation": workers_by_generation,
                             "successful": successful, "total": total}}


def _catalogue_control(client, products, brand):
    expected = sorted(products, key=lambda product: (product["unit_price"], product["id"]))
    checks = []
    for sort, ordered in (("price_asc", expected),
                          ("price_desc", sorted(products,
                                                key=lambda product: (-product["unit_price"],
                                                                     product["id"])))):
        items = []
        for offset in (0, 100):
            response = client.get("/products", params={
                "q": brand, "sort": sort, "limit": 100, "offset": offset,
            })
            response.raise_for_status()
            body = response.json()
            checks.append(body["total"] == len(products))
            items.extend(body["items"])
        checks.append(items == ordered)
    return all(checks)


def _browser_listing(base, brand, evidence):
    if sys.platform != "linux":
        raise RuntimeError("Browser observation requires isolated Linux")
    from playwright.sync_api import sync_playwright

    observation = {"visible": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(base + "/", wait_until="networkidle", timeout=15000)
            started = time.monotonic()
            page.goto(base + "/#/search?" + urlencode({"q": brand, "category": "digital"}),
                      wait_until="domcontentloaded", timeout=10000)
            page.locator("[data-results] .card-name").first.wait_for(timeout=15000)
            observation.update(visible=True, elapsed_ms=round(
                (time.monotonic() - started) * 1000, 3
            ), names=page.locator("[data-results] .card-name").all_text_contents())
            page.screenshot(path=str(evidence / "case15-catalogue.png"), full_page=True)
        except Exception as exc:
            observation["error"] = str(exc)[:250]
        finally:
            browser.close()
    return observation


async def _performance15(base, products, brand, evidence):
    from dataclasses import asdict

    import httpx

    from scripts.load_test import Slo, prepare_targets, run_stage, summarize

    samples = []
    browser = None
    limits = httpx.Limits(max_connections=30, max_keepalive_connections=20)
    async with httpx.AsyncClient(base_url=base, timeout=8, limits=limits,
                                trust_env=False) as client:
        targets = await prepare_targets(client, 20)
        targets.catalog_ids = [product["id"] for product in products]
        await run_stage(client, targets, 5, 5, 100, 0.1, 815)
        for repeat in range(3):
            for users in (10, 20):
                job = run_stage(client, targets, users, 20, 100, 0.1,
                                15000 + repeat * 100 + users)
                if repeat == 0 and users == 10:
                    (values, elapsed), browser = await asyncio.gather(
                        job, asyncio.to_thread(_browser_listing, base, brand, evidence)
                    )
                else:
                    values, elapsed = await job
                stage = asdict(summarize(users, elapsed, values, Slo()))
                stage["repeat"] = repeat
                samples.append(stage)
                _write(evidence / "case15-stages.json", samples)
    medians = []
    for users in (10, 20):
        stages = [stage for stage in samples if stage["users"] == users]
        medians.append({
            "users": users,
            "p95_ms": statistics.median(stage["p95_ms"] for stage in stages),
            "p99_ms": statistics.median(stage["p99_ms"] for stage in stages),
            "error_rate": statistics.median(stage["error_rate"] for stage in stages),
            "minimum_samples": min(stage["requests"] for stage in stages),
            "browse_p95_ms": statistics.median(
                stage["by_action"].get("browse", {}).get("p95_ms", float("inf"))
                for stage in stages
            ),
            "search_p95_ms": statistics.median(
                stage["by_action"].get("search", {}).get("p95_ms", float("inf"))
                for stage in stages
            ),
        })
    return samples, medians, browser


def _probe15(urls, evidence):
    import httpx

    base = urls["app"].rstrip("/")
    brand = "catalogue-" + uuid.uuid4().hex[:8]
    names = {"digital": "무선 키보드", "home": "생활 매트", "kitchen": "스테인리스 커피 세트",
             "food": "감귤 세트", "beauty": "보습 크림", "sports": "운동 매트"}
    products = []
    with httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
        for category, name in names.items():
            for index in range(24):
                price = 10000 + len(products) * 100
                products.append(_create(
                    client, name=f"{name} {index + 1:02d}", category=category,
                    unit_price=price, list_price=price + 3000, brand=brand,
                    description=f"{name} 상품 안내입니다. 일상에서 사용하는 상품입니다.",
                ))
        if not _catalogue_control(client, products, brand):
            raise RuntimeError("Catalogue correctness control failed before load")
    stages, medians, browser = asyncio.run(_performance15(base, products, brand, evidence))
    with httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
        correct = _catalogue_control(client, products, brand)
        product = products[0]
        response = client.post("/orders", json={
            "product_id": product["id"], "quantity": 1, "postal_code": "16841",
        })
        response.raise_for_status()
        product["current_stock"] -= 1
        detail = client.get(f"/products/{product['id']}")
        correct = correct and detail.status_code == 200 and detail.json() == product
        correct = correct and _catalogue_control(client, products, brand)
    sufficient = all(stage["minimum_samples"] >= 200 for stage in medians)
    slo = all(stage["p95_ms"] <= 300 and stage["p99_ms"] <= 1000
              and stage["error_rate"] < 0.01 for stage in medians)
    materially_slow = any(
        max(stage["browse_p95_ms"], stage["search_p95_ms"]) > 600
        and stage["p95_ms"] > 300 for stage in medians
    )
    healthy = correct and sufficient and slo and browser and browser["visible"]
    symptom = correct and sufficient and materially_slow
    return {"healthy": bool(healthy), "symptom": bool(symptom),
            "checks": {"catalogue_correct": correct, "sufficient_samples": sufficient,
                       "capacity_slo": slo, "customer_delay_observed": materially_slow},
            "observations": {"stages": stages, "medians": medians, "browser": browser,
                             "fixture_count": len(products), "brand": brand,
                             "app_resources": {"cpus": 1, "memory": "512m",
                                               "pool": "30+30", "workers": 1}}}


def probe(case_id, urls, evidence_dir):
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if case_id == "13":
        return _probe13(urls, evidence_dir)
    if case_id == "14":
        return _probe14(urls, evidence_dir)
    if case_id == "15":
        return _probe15(urls, evidence_dir)
    raise ValueError(f"Unsupported advanced systems case: {case_id}")
