import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab_suite.round2_b_runtime import event_path, read_json, write_json

SUPPORTED = {"18", "19"}




def runtime_paths(case_id):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    return ["lab_suite/round2_b_runtime.py", "lab_suite/cases/advanced_round2_b.py"]


def install_runtime(case_id):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    from lab_suite.round2_b_runtime import install

    install(os.environ.get("LAB_B_EVIDENCE", "/evidence/round2-b"))


def configure_compose(case_id, definition, source, evidence):
    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    app = definition["services"]["app"]
    app["environment"]["LAB_B_EVIDENCE"] = "/evidence/round2-b"
    app.setdefault("volumes", []).append(
        {
            "type": "bind",
            "source": evidence.as_posix(),
            "target": "/evidence",
        }
    )
    app["stop_grace_period"] = "10s"
    return definition


def _wait(path, predicate=lambda item: bool(item), timeout=20, stop=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read_json(path)
        if value and predicate(value):
            if value.get("error"):
                raise RuntimeError(value["error"])
            return value
        if stop is not None and stop.wait(0.025):
            raise RuntimeError("Lifecycle controller stopped")
        if stop is None:
            time.sleep(0.025)
    raise TimeoutError(f"Operation evidence did not arrive: {Path(path).name}")


def _optional_event(root, token, point, timeout=1.0):
    try:
        return _wait(event_path(root, token, point), timeout=timeout)
    except TimeoutError:
        return None


def _release(root, token, point):
    path = event_path(root, token, point).with_suffix(".release")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def control(case_id, compose_args, evidence_dir, stop_event, run_command):
    if case_id == "19":
        return {"passed": True, "operations": []}
    if case_id != "18":
        raise ValueError(case_id)
    observations = []
    request_path = evidence_dir / "case18-worker-request.json"
    response_path = evidence_dir / "case18-worker-response.json"
    run_id = None
    stage = 0
    try:
        for stage in (1, 2):
            request = _wait(
                request_path,
                lambda item, current_stage=stage, current_run=run_id: (
                    item.get("stage") == current_stage
                    and (current_run is None or item.get("run_id") == current_run)
                ),
                timeout=160,
                stop=stop_event,
            )
            run_id = request["run_id"]
            worker = request["worker"]
            script = (
                "import os,signal,sys; from pathlib import Path; "
                "pid,parent,ticks=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]; "
                "fields=Path('/proc/%d/stat'%pid).read_text().split(')',1)[1].split(); "
                "assert pid>1 and parent>0 and int(fields[1])==parent and fields[19]==ticks; "
                "assert b'multiprocessing.spawn' in Path('/proc/%d/cmdline'%pid).read_bytes(); "
                "os.kill(pid,signal.SIGKILL)"
            )
            run_command(
                [
                    *compose_args,
                    "exec",
                    "-T",
                    "app",
                    "python",
                    "-c",
                    script,
                    str(worker["pid"]),
                    str(worker["parent_pid"]),
                    str(worker["worker_start_ticks"]),
                ],
                log=evidence_dir / "controller.log",
                timeout=20,
            )
            observation = {
                "stage": stage,
                "run_id": run_id,
                "operation": "owned_quote_worker_terminated",
                "worker": worker,
            }
            observations.append(observation)
            write_json(response_path, observation)
        return {"passed": True, "operations": observations}
    except Exception as exc:
        write_json(response_path, {"run_id": run_id, "stage": stage, "error": str(exc)})
        return {"passed": False, "operations": observations, "error": str(exc)}


def _fixture(client, base, name, stock):
    payload = {"name": name, "unit_price": 12000, "initial_stock": stock, "category": "etc"}
    response = client.post(base + "/products", json=payload)
    response.raise_for_status()
    product = response.json()
    if (
        response.status_code != 201
        or product.get("current_stock") != stock
        or any(product.get(key) != value for key, value in payload.items())
    ):
        raise RuntimeError("Product fixture was not created faithfully")
    return product


def _read_controls(client, base, products):
    results = []
    for product in products:
        response = client.get(f"{base}/products/{product['id']}")
        listing = client.get(base + "/products", params={"q": product["name"], "limit": 1})
        results.append(
            {
                "product_id": product["id"],
                "detail_status": response.status_code,
                "list_status": listing.status_code,
                "detail_id": response.json().get("id"),
                "list_ids": [row.get("id") for row in listing.json().get("items", [])],
            }
        )
    return results


def _controls_passed(rows):
    return all(
        row["detail_status"] == row["list_status"] == 200
        and row["detail_id"] == row["product_id"]
        and row["list_ids"] == [row["product_id"]]
        for row in rows
    )


def judge(case_id, checks, groups, lifecycle):
    expected_success = (
        all(
            record["status"] == 201
            for name, records in groups.items()
            if name != "soldout"
            for record in records
        )
        and groups["soldout"][0]["status"] == 409
    )
    healthy = all(checks.values()) and expected_success
    common = (
        all(checks.values())
        and all(record["status"] == 201 for name in ("before", "after") for record in groups[name])
        and groups["soldout"][0]["status"] == 409
    )
    partial_failures = all(
        sorted(record["status"] for record in groups[f"operation-{stage}"]) == [201, 500]
        for stage in (1, 2)
    )
    if case_id == "18":
        mechanism = all(item.get("worker_terminated") for item in lifecycle)
    else:
        mechanism = all(
            item.get("callback_timeout") and item.get("drain_completed") for item in lifecycle
        )
    return {"healthy": bool(healthy), "symptom": bool(common and partial_failures and mechanism)}


def probe(case_id, urls, evidence_dir):
    import httpx

    from lab_suite.cases.advanced_data import _database_snapshot, _reconcile, _send, _views

    if case_id not in SUPPORTED:
        raise ValueError(case_id)
    evidence_dir = Path(evidence_dir)
    root = evidence_dir / "round2-b"
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    base = urls["direct"].rstrip("/")
    groups = {}
    lifecycle = []
    all_gates = []
    products = []
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
    with httpx.Client(timeout=12, limits=limits, trust_env=False) as client:
        for label, stock in (("ordinary", 50), ("first", 20), ("second", 20), ("empty", 0)):
            products.append(_fixture(client, base, f"{run_id[:10]}-{label}", stock))
        ordinary, first, second, empty = products
        write_json(root / "plan.json", {"run_id": run_id, "gates": []})
        groups["before"] = [
            _send(client, base, ordinary, quantity, f"{run_id}-before-{index}")
            for index, quantity in enumerate((1, 20))
        ]
        initial_reads = _read_controls(client, base, products)
        if not all(record["status"] == 201 for record in groups["before"]):
            raise RuntimeError("Initial order controls did not complete")
        with ThreadPoolExecutor(max_workers=3) as callers:
            try:
                for stage in (1, 2):
                    first_token, second_token = (
                        f"{run_id}-{stage}-first",
                        f"{run_id}-{stage}-second",
                    )
                    if case_id == "18":
                        gates = [
                            (first_token, "compute_0"),
                            (second_token, "compute_0"),
                            (second_token, "failed_0"),
                            (first_token, "submit_1"),
                        ]
                        all_gates.extend(gates)
                        write_json(
                            root / "plan.json",
                            {
                                "run_id": run_id,
                                "gates": [f"{token}:{point}" for token, point in gates],
                            },
                        )
                        futures = [
                            callers.submit(_send, client, base, first, 1, first_token),
                            callers.submit(_send, client, base, second, 3, second_token),
                        ]
                        worker = _wait(event_path(root, first_token, "compute_0"), timeout=15)
                        second_worker = _optional_event(
                            root, second_token, "compute_0", timeout=1.5
                        )
                        write_json(
                            evidence_dir / "case18-worker-request.json",
                            {
                                "run_id": run_id,
                                "stage": stage,
                                "worker": worker,
                            },
                        )
                        operation = _wait(
                            evidence_dir / "case18-worker-response.json",
                            lambda item, current_stage=stage: (
                                item.get("run_id") == run_id and item.get("stage") == current_stage
                            ),
                            timeout=25,
                        )
                        retry = _optional_event(root, first_token, "submit_1", timeout=2)
                        second_failure = _optional_event(root, second_token, "failed_0", timeout=1)
                        _release(root, first_token, "compute_0")
                        _release(root, second_token, "compute_0")
                        _release(root, second_token, "failed_0")
                        late_recovery = _optional_event(
                            root, second_token, "recovered_0", timeout=1
                        )
                        _release(root, first_token, "submit_1")
                        records = [future.result(timeout=15) for future in futures]
                        lifecycle.append(
                            {
                                "stage": stage,
                                "worker_terminated": True,
                                "operation": operation,
                                "second_worker_observed": second_worker,
                                "second_failure_observed": second_failure,
                                "successor_borrow": retry,
                                "late_recovery": late_recovery,
                            }
                        )
                    else:
                        rotation_token = f"{run_id}-{stage}-rotation"
                        gates = [(first_token, "compute_0")]
                        all_gates.extend(gates)
                        write_json(
                            root / "plan.json",
                            {
                                "run_id": run_id,
                                "gates": [f"{token}:{point}" for token, point in gates],
                            },
                        )
                        first_future = callers.submit(_send, client, base, first, 1, first_token)
                        worker = _wait(event_path(root, first_token, "compute_0"), timeout=15)
                        write_json(root / "rotation-request.json", {"token": rotation_token})
                        drain = _optional_event(root, rotation_token, "drain_entered", timeout=1.5)
                        second_future = callers.submit(_send, client, base, second, 3, second_token)
                        _release(root, first_token, "compute_0")
                        records = [
                            first_future.result(timeout=15),
                            second_future.result(timeout=15),
                        ]
                        operation = _wait(
                            root / "rotation-response.json",
                            lambda item, expected=rotation_token: item.get("token") == expected,
                            timeout=15,
                        )
                        lifecycle.append(
                            {
                                "stage": stage,
                                "operation": operation,
                                "worker": worker,
                                "drain_entered": drain,
                                "drain_completed": read_json(
                                    event_path(root, rotation_token, "drain_completed")
                                ),
                                "callback_timeout": read_json(
                                    event_path(root, first_token, "callback_timeout_0")
                                ),
                            }
                        )
                    groups[f"operation-{stage}"] = records
            finally:
                for token, point in all_gates:
                    _release(root, token, point)
        groups["after"] = [
            _send(client, base, ordinary, quantity, f"{run_id}-after-{index}")
            for index, quantity in enumerate((3, 2))
        ]
        groups["soldout"] = [_send(client, base, empty, 1, f"{run_id}-soldout")]
        invalid = client.post(
            base + "/orders",
            json={
                "product_id": ordinary["id"],
                "quantity": 0,
                "postal_code": "16841",
            },
            headers={"X-Request-ID": f"{run_id}-invalid"},
        )
        final_reads = _read_controls(client, base, products)
        database = _database_snapshot([product["id"] for product in products])
        views = _views(client, [base], products)
    records = [record for rows in groups.values() for record in rows]
    checks = _reconcile(products, records, database, views)
    checks.update(
        {
            "catalogue_available": _controls_passed(initial_reads)
            and _controls_passed(final_reads),
            "validation_preserved": invalid.status_code == 422
            and invalid.json().get("code") == "VALIDATION_ERROR",
            "two_lifecycle_operations_observed": len(lifecycle) == 2,
        }
    )
    observations = {
        "scope": (
            "Real HTTP orders, isolated pure quotation workers, independent read-only DB truth"
        ),
        "fixtures": products,
        "groups": groups,
        "lifecycle": lifecycle,
        "initial_reads": initial_reads,
        "final_reads": final_reads,
        "database": database,
        "api_views": views,
    }
    write_json(evidence_dir / f"case{case_id}-observations.json", observations)
    return {
        **judge(case_id, checks, groups, lifecycle),
        "checks": checks,
        "observations": observations,
    }
