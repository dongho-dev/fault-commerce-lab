import copy
import importlib.util
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUPPORTED = {"16", "17"}


def runtime_paths(case):
    if case == "16":
        return ["lab_suite/round2_a_gateway.py", "lab_suite/round2_a_handoff.py"]
    if case == "17":
        return ["lab_suite/round2_a_projection.py"]
    raise ValueError(case)




def install_runtime(case):
    if case == "17":
        from lab_suite.round2_a_projection import install

        install()
    elif case != "16":
        raise ValueError(case)


def configure_compose(case, definition, source, evidence):
    services = definition["services"]
    app = services["app"]
    mount = {"type": "bind", "source": evidence.as_posix(), "target": "/evidence"}
    if case == "16":
        app.pop("ports", None)
        services["db"]["command"] = [
            "postgres",
            "-c",
            "wal_level=replica",
            "-c",
            "max_wal_senders=4",
            "-c",
            "wal_keep_size=32MB",
        ]
        services["standby"] = {
            "image": "postgres:16",
            "user": "postgres",
            "profiles": ["handoff"],
            "environment": {"PGPASSWORD": "lab", "PGDATA": "/var/lib/postgresql/data/replica"},
            "entrypoint": ["bash", "-ceu"],
            "command": [
                'mkdir -p "$$PGDATA"; chmod 700 "$$PGDATA"; '
                'pg_basebackup -h db -U lab -D "$$PGDATA" -R -X stream --checkpoint=fast; '
                'exec postgres -D "$$PGDATA" -c hot_standby=on'
            ],
            "tmpfs": ["/var/lib/postgresql/data:size=320m,mode=1777"],
            "cpus": 1.0,
            "mem_limit": "320m",
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U lab -d lab"],
                "interval": "1s",
                "timeout": "3s",
                "retries": 35,
            },
        }
        secondary = copy.deepcopy(app)
        secondary.pop("build")
        secondary["profiles"] = ["handoff"]
        secondary["environment"]["DATABASE_URL"] = "postgresql+psycopg://lab:lab@standby:5432/lab"
        secondary["environment"]["LAB_MIGRATE"] = "0"
        secondary["depends_on"] = {"standby": {"condition": "service_healthy"}}
        services["app-b"] = secondary
        services["gateway"] = {
            "image": app["image"],
            "command": ["python", "-m", "lab_suite.round2_a_gateway"],
            "environment": {"LAB_EVIDENCE": "/evidence", "PYTHONUNBUFFERED": "1"},
            "ports": ["127.0.0.1:18116:8080"],
            "volumes": [mount],
            "cpus": 0.5,
            "mem_limit": "128m",
            "depends_on": {"app": {"condition": "service_healthy"}},
        }
        services["probe"]["environment"]["LAB_BASE_URL"] = "http://gateway:8080"
    elif case == "17":
        services["projection"] = {
            "image": app["image"],
            "command": ["python", "-m", "lab_suite.round2_a_projection"],
            "environment": {
                "DATABASE_URL": app["environment"]["DATABASE_URL"],
                "LAB_EVIDENCE": "/evidence",
                "PYTHONUNBUFFERED": "1",
            },
            "volumes": [mount],
            "cpus": 0.5,
            "mem_limit": "192m",
            "stop_grace_period": "1s",
            "depends_on": {"app": {"condition": "service_healthy"}},
        }
    else:
        raise ValueError(case)
    return definition


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _wait(path, field, value, timeout=80, stop=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            row = {}
        if row.get("error"):
            raise RuntimeError(row["error"])
        if row.get(field) == value:
            return row
        if stop is not None:
            if stop.wait(0.03):
                raise RuntimeError("Lifecycle cancelled")
        else:
            time.sleep(0.03)
    raise TimeoutError(f"Missing lifecycle acknowledgement: {field}")


def control(case, compose, evidence, stop, command):
    if case == "16":
        config = json.loads(Path(compose[-1]).read_text(encoding="utf-8"))
        source = Path(config["services"]["app"]["build"]["context"]).resolve()
        path = source / "lab_suite/round2_a_handoff.py"
        if not path.resolve().is_relative_to(source):
            raise ValueError("Operational source escaped exercise checkout")
        spec = importlib.util.spec_from_file_location("exercise_handoff_" + uuid.uuid4().hex, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.control(compose, evidence, stop, command)
    if case == "17":
        try:
            request = _wait(
                evidence / "projection-restart-request.json", "stage", "restart", stop=stop
            )
            old = json.loads((evidence / "projection-generation.json").read_text())
            command(
                [*compose, "restart", "-t", "1", "projection"],
                log=evidence / "projection-restart.log",
                timeout=30,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                current = json.loads((evidence / "projection-generation.json").read_text())
                if current["generation"] != old["generation"]:
                    result = {
                        "passed": True,
                        "token": request["token"],
                        "stage": "restarted",
                        "old_generation": old,
                        "new_generation": current,
                    }
                    _write(evidence / "projection-restart-response.json", result)
                    return result
                if stop.wait(0.03):
                    raise RuntimeError("Lifecycle cancelled")
            raise TimeoutError("Projection process did not change generation")
        except Exception as exc:
            result = {"passed": False, "stage": "error", "error": str(exc)}
            _write(evidence / "projection-restart-response.json", result)
            return result
    raise ValueError(case)


def _connect(host="db"):
    import psycopg

    return psycopg.connect(f"host={host} port=5432 dbname=lab user=lab password=lab")


def _create(client, name, stock=12, price=12000):
    payload = {"name": name, "unit_price": price, "initial_stock": stock, "category": "etc"}
    response = client.post("/products", json=payload)
    response.raise_for_status()
    value = response.json()
    if response.status_code != 201 or any(value.get(k) != v for k, v in payload.items()):
        raise RuntimeError("Ordinary API fixture creation failed")
    return value


def _order(client, product, quantity=1):
    response = client.post(
        "/orders",
        json={
            "product_id": product["id"],
            "quantity": quantity,
            "postal_code": "06236",
        },
    )
    if response.status_code == 201:
        value = response.json()
        if value["product_id"] != product["id"] or value["quantity"] != quantity:
            raise RuntimeError("Order receipt does not match its request")
        return {"status": 201, "receipt": value}
    return {"status": response.status_code, "body": response.text}


def _orders(host="db"):
    from psycopg.rows import dict_row

    with _connect(host) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id,product_id,quantity,unit_price,postal_code,"
                "shipping_fee,total_amount,status FROM orders ORDER BY id"
            )
            orders = cursor.fetchall()
            cursor.execute("SELECT product_id,initial_stock,current_stock FROM inventories")
            stocks = cursor.fetchall()
            cursor.execute(
                "SELECT pg_is_in_recovery() AS recovering, inet_server_addr()::text AS host"
            )
            identity = cursor.fetchone()
    return {"orders": orders, "stocks": stocks, "identity": identity}


def _reconciled(snapshot):
    return all(
        row["current_stock"]
        == row["initial_stock"]
        - sum(
            item["quantity"]
            for item in snapshot["orders"]
            if item["product_id"] == row["product_id"] and item["status"] == "CONFIRMED"
        )
        and row["current_stock"] >= 0
        for row in snapshot["stocks"]
    )


def _contains(snapshot, receipt):
    return any(all(receipt.get(k) == v for k, v in row.items()) for row in snapshot["orders"])


def _probe16(urls, evidence):
    import httpx

    token = uuid.uuid4().hex
    with httpx.Client(base_url=urls["app"], timeout=20, trust_env=False) as customer:
        product = _create(customer, "수납 파우치 " + token[:8])
        earlier = _order(customer, product, 2)
        assert earlier["status"] == 201
        _write(evidence / "handoff-request.json", {"run_id": token, "stage": "prepare"})
        _wait(evidence / "handoff-response.json", "stage", "prepared", timeout=100)
        before = {"old": _orders("db"), "replacement": _orders("standby")}
        assert before["replacement"]["identity"]["recovering"]
        assert _contains(before["replacement"], earlier["receipt"])
        lock = _connect()
        in_flight = None
        try:
            lock.execute("SET LOCAL idle_in_transaction_session_timeout = '45000ms'")
            lock.execute(
                "SELECT product_id FROM inventories WHERE product_id=%s FOR UPDATE",
                (product["id"],),
            )

            def pending_order():
                with httpx.Client(base_url=urls["app"], timeout=45, trust_env=False) as other:
                    return _order(other, product)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(pending_order)
                deadline = time.monotonic() + 10
                with _connect() as observer:
                    observer.autocommit = True
                    while time.monotonic() < deadline:
                        blocked = observer.execute(
                            "SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))",
                            (lock.info.backend_pid,),
                        ).fetchall()
                        if blocked:
                            break
                        time.sleep(0.02)
                    else:
                        raise RuntimeError("Accepted order did not reach the held inventory row")
                _write(evidence / "handoff-request.json", {"run_id": token, "stage": "handoff"})
                deadline = time.monotonic() + 25
                progress = {}
                while time.monotonic() < deadline:
                    for name in ("router-observation.json", "handoff-response.json"):
                        path = evidence / name
                        value = json.loads(path.read_text()) if path.exists() else {}
                        if value.get("error"):
                            raise RuntimeError(value["error"])
                        if value.get("writes", 0) > 0 or value.get("stage") == "boundary":
                            progress = {"observed_file": name, **value}
                            break
                    if progress:
                        break
                    time.sleep(0.02)
                else:
                    raise TimeoutError("The handoff did not expose an observed admission boundary")
                lock.rollback()
                in_flight = future.result(timeout=12)
                assert in_flight["status"] == 201
        finally:
            lock.rollback()
            lock.close()
        boundary = _wait(evidence / "handoff-response.json", "stage", "boundary")
        during = _order(customer, product)
        _write(evidence / "handoff-request.json", {"run_id": token, "stage": "boundary-observed"})
        active = _wait(evidence / "handoff-response.json", "stage", "active")
        later = _order(customer, product, 3)
        assert later["status"] == 201
        detail = customer.get(f"/products/{product['id']}")
        detail.raise_for_status()
        old, replacement = _orders("db"), _orders("standby")
        receipts = [
            item["receipt"] for item in (earlier, in_flight, during, later) if item["status"] == 201
        ]
        missing = [r for r in receipts if not _contains(replacement, r)]
        final_stock = next(
            r["current_stock"] for r in replacement["stocks"] if r["product_id"] == product["id"]
        )
        controls = (
            _reconciled(old)
            and _reconciled(replacement)
            and not replacement["identity"]["recovering"]
            and old["identity"]["host"] != replacement["identity"]["host"]
            and _contains(replacement, earlier["receipt"])
            and _contains(replacement, later["receipt"])
            and detail.json()["current_stock"] == final_stock
            and during["status"] in {201, 503}
        )
        healthy = controls and not missing
        symptom = (
            controls
            and during["status"] == 201
            and _contains(old, during["receipt"])
            and during["receipt"] in missing
            and all(r in [in_flight["receipt"], during["receipt"]] for r in missing)
        )
    observations = {
        "before": before,
        "boundary": boundary,
        "active": active,
        "attempts": [earlier, in_flight, during, later],
        "in_flight_boundary": progress,
        "old": old,
        "final": replacement,
        "missing_receipts": missing,
        "customer_product": detail.json(),
    }
    _write(evidence / "handoff-observations.json", observations)
    return {"healthy": healthy, "symptom": symptom, "observations": observations}


def _projection_state():
    with _connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        return {
            "cursor": connection.execute("SELECT last_id FROM lab_r2a_progress").fetchone()[0],
            "applied": [
                r[0]
                for r in connection.execute(
                    "SELECT order_id FROM lab_r2a_applied ORDER BY order_id"
                ).fetchall()
            ],
            "projection": connection.execute(
                "SELECT product_id,current_stock FROM lab_r2a_projection ORDER BY product_id"
            ).fetchall(),
        }


def _probe17(urls, evidence):
    import httpx

    token = uuid.uuid4().hex
    with httpx.Client(base_url=urls["app"], timeout=10, trust_env=False) as customer:
        products = [
            _create(customer, name + token[:8]) for name in ("정리함 ", "보관함 ", "수납함 ")
        ]
        for product in products:
            response = customer.get(f"/products/{product['id']}")
            assert response.status_code == 200 and response.json()["current_stock"] == 12
        deadline = time.monotonic() + 10
        while not (evidence / "projection-generation.json").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Projection worker did not start")
            time.sleep(0.02)
        lock = _connect()
        try:
            lock.execute("SET LOCAL idle_in_transaction_session_timeout = '45000ms'")
            lock.execute(
                "SELECT product_id FROM lab_r2a_projection WHERE product_id=ANY(%s) FOR UPDATE",
                ([products[0]["id"], products[1]["id"]],),
            )
            receipts = [
                _order(customer, product, quantity)
                for product, quantity in zip(products, (2, 3, 1), strict=True)
            ]
            assert all(row["status"] == 201 for row in receipts)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                state = _projection_state()
                if receipts[2]["receipt"]["id"] in state["applied"]:
                    break
                time.sleep(0.03)
            else:
                raise RuntimeError("Independent product did not finish during partial progress")
            assert all(row["receipt"]["id"] not in state["applied"] for row in receipts[:2])
            with _connect() as observer:
                blocked = observer.execute(
                    "SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))",
                    (lock.info.backend_pid,),
                ).fetchall()
                assert len(blocked) >= 2
            _write(
                evidence / "projection-restart-request.json", {"token": token, "stage": "restart"}
            )
            restarted = _wait(evidence / "projection-restart-response.json", "stage", "restarted")
        finally:
            lock.rollback()
            lock.close()
        with _connect() as observer:
            outstanding = observer.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE application_name='round2-projection-apply' "
                "AND wait_event_type='Lock'"
            ).fetchall()
        outcomes = []
        for _ in range(2):
            row = []
            for product in products:
                response = customer.get(f"/products/{product['id']}")
                row.append(
                    {
                        "status": response.status_code,
                        "body": response.json() if response.status_code == 200 else response.text,
                    }
                )
            outcomes.append(row)
        listing = customer.get("/products", params={"q": token[:8], "limit": 10})
        extra = _order(customer, products[2], 2)
        assert extra["status"] == 201
        final_good = customer.get(f"/products/{products[2]['id']}")
        snapshot, progress = _orders(), _projection_state()
        controls = (
            _reconciled(snapshot)
            and all(_contains(snapshot, x["receipt"]) for x in [*receipts, extra])
            and all(
                row[2]["status"] == 200 and row[2]["body"]["current_stock"] == 11
                for row in outcomes
            )
            and final_good.status_code == 200
            and final_good.json()["current_stock"] == 9
        )
        correct_reads = all(
            all(
                item["status"] == 200 and item["body"]["current_stock"] == expected
                for item, expected in zip(row, (10, 9, 11), strict=True)
            )
            for row in outcomes
        )
        expected_stock = {products[0]["id"]: 10, products[1]["id"]: 9, products[2]["id"]: 11}
        correct_listing = (
            listing.status_code == 200
            and listing.json()["total"] == 3
            and {p["id"]: p["current_stock"] for p in listing.json()["items"]} == expected_stock
        )
        unavailable = all(all(item["status"] == 500 for item in row[:2]) for row in outcomes)
        healthy = controls and correct_reads and correct_listing
        symptom = (
            controls
            and unavailable
            and listing.status_code == 500
            and all(r["receipt"]["id"] not in progress["applied"] for r in receipts[:2])
            and progress["cursor"] >= extra["receipt"]["id"]
        )
    observations = {
        "before_restart": state,
        "restart": restarted,
        "outcomes": outcomes,
        "orders": snapshot,
        "progress": progress,
        "receipts": [*receipts, extra],
        "listing": {"status": listing.status_code, "body": listing.text},
        "lock_waiters_after_release": outstanding,
    }
    _write(evidence / "projection-observations.json", observations)
    return {"healthy": healthy, "symptom": symptom, "observations": observations}


def probe(case, urls, evidence_dir):
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    if case == "16":
        return _probe16(urls, evidence)
    if case == "17":
        return _probe17(urls, evidence)
    raise ValueError(case)
