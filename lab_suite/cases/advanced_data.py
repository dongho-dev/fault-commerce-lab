from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path




def install_runtime(case_id: str) -> None:
    if case_id not in {"11", "12"}:
        raise ValueError(f"Unsupported advanced data case: {case_id}")


def _send(client, base: str, product: dict, quantity: int, request_id: str, barrier=None):
    payload = {"product_id": product["id"], "quantity": quantity, "postal_code": "16841"}
    if barrier is not None:
        barrier.wait(timeout=20)
    started = time.monotonic()
    result = {
        "node": base,
        "request_id": request_id,
        "payload": payload,
        "started": started,
        "status": None,
        "body": None,
    }
    try:
        response = client.post(base + "/orders", json=payload, headers={"X-Request-ID": request_id})
        result["status"] = response.status_code
        result["response_request_id"] = response.headers.get("x-request-id")
        try:
            result["body"] = response.json()
        except ValueError:
            result["body_text"] = response.text[:500]
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["ended"] = time.monotonic()
    result["elapsed_ms"] = round((result["ended"] - started) * 1000, 3)
    return result


def _batch(client, nodes: list[str], specs: list[tuple[dict, int]], prefix: str):
    barrier = threading.Barrier(len(specs) + 1)
    with ThreadPoolExecutor(max_workers=len(specs)) as executor:
        futures = [
            executor.submit(
                _send,
                client,
                nodes[index % len(nodes)],
                product,
                quantity,
                f"{prefix}-{index}",
                barrier,
            )
            for index, (product, quantity) in enumerate(specs)
        ]
        barrier.wait(timeout=20)
        return [future.result(timeout=40) for future in futures]


def _create(client, base: str, name: str, stock: int):
    payload = {"name": name, "unit_price": 12000, "initial_stock": stock, "category": "etc"}
    response = client.post(base + "/products", json=payload)
    if response.status_code != 201:
        raise RuntimeError(f"Fixture creation failed: {response.status_code} {response.text}")
    product = response.json()
    if any(product.get(key) != value for key, value in payload.items()):
        raise RuntimeError("Fixture creation returned incorrect product fields")
    if product.get("current_stock") != stock or not isinstance(product.get("id"), int):
        raise RuntimeError("Fixture creation returned incorrect inventory or product ID")
    return product


def _database_snapshot(product_ids: list[int]):
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        inventory = connection.execute(
            "SELECT product_id, initial_stock, current_stock FROM inventories "
            "WHERE product_id = ANY(%s) ORDER BY product_id",
            (product_ids,),
        ).fetchall()
        orders = connection.execute(
            "SELECT id, product_id, quantity, unit_price, postal_code, shipping_fee, "
            "total_amount, status FROM orders WHERE product_id = ANY(%s) ORDER BY id",
            (product_ids,),
        ).fetchall()
    return {"inventory": inventory, "orders": orders}


def _views(client, nodes: list[str], products: list[dict]):
    observed = []
    for base in nodes:
        for product in products:
            detail = client.get(f"{base}/products/{product['id']}")
            listing = client.get(base + "/products", params={"q": product["name"], "limit": 100})
            detail.raise_for_status()
            listing.raise_for_status()
            observed.append(
                {
                    "node": base,
                    "product_id": product["id"],
                    "detail": detail.json(),
                    "listing": listing.json(),
                }
            )
    return observed


def _reconcile(products: list[dict], records: list[dict], database: dict, views: list[dict]):
    catalog = {product["id"]: product for product in products}
    persisted = {order["id"]: order for order in database["orders"]}
    inventory = {item["product_id"]: item for item in database["inventory"]}
    success_ids = []
    response_valid = True
    for record in records:
        body = record.get("body")
        request_id = record["request_id"]
        if not isinstance(body, dict) or record.get("response_request_id") != request_id:
            response_valid = False
            continue
        if record["status"] == 201:
            payload = record["payload"]
            product = catalog[payload["product_id"]]
            quantity = payload["quantity"]
            merchandise = quantity * product["unit_price"]
            shipping = (3000 if merchandise < 200000 else 0) + max(quantity - 2, 0) * 700
            fields = {
                **payload,
                "unit_price": product["unit_price"],
                "shipping_fee": shipping,
                "total_amount": merchandise + shipping,
                "status": "CONFIRMED",
            }
            order_id = body.get("id")
            if not isinstance(order_id, int) or order_id <= 0:
                response_valid = False
                continue
            success_ids.append(order_id)
            stored = persisted.get(order_id, {})
            response_valid &= all(body.get(key) == value for key, value in fields.items())
            response_valid &= all(stored.get(key) == value for key, value in fields.items())
            response_valid &= bool(body.get("created_at"))
        elif record["status"] in (409, 500):
            expected_code = (
                "INSUFFICIENT_STOCK" if record["status"] == 409 else "INTERNAL_SERVER_ERROR"
            )
            response_valid &= (
                body.get("code") == expected_code and body.get("request_id") == request_id
            )
        else:
            response_valid = False
    exact_orders = len(success_ids) == len(set(success_ids)) == len(database["orders"]) and set(
        success_ids
    ) == set(persisted)
    quantities = Counter()
    for order in database["orders"]:
        quantities[order["product_id"]] += order["quantity"]
    stock_equation = set(inventory) == set(catalog)
    nonnegative = stock_equation
    initial_unchanged = stock_equation
    for product_id, row in inventory.items():
        stock_equation &= row["initial_stock"] - quantities[product_id] == row["current_stock"]
        nonnegative &= row["current_stock"] >= 0
        initial_unchanged &= row["initial_stock"] == catalog[product_id]["initial_stock"]
    views_valid = True
    for view in views:
        product_id = view["product_id"]
        product = catalog[product_id]
        stock = inventory.get(product_id, {}).get("current_stock")
        fields = {"id": product_id, "unit_price": product["unit_price"], "current_stock": stock}
        page = view["listing"]
        items = page.get("items", [])
        views_valid &= all(view["detail"].get(key) == value for key, value in fields.items())
        views_valid &= page.get("total") == 1 and len(items) == 1
        if len(items) == 1:
            views_valid &= all(items[0].get(key) == value for key, value in fields.items())
    return {
        "response_contract_and_amounts": bool(response_valid),
        "success_ids_equal_persisted_orders": exact_orders,
        "nonnegative_stock": bool(nonnegative),
        "initial_stock_unchanged": bool(initial_unchanged),
        "stock_equation": bool(stock_equation),
        "api_views_match_database": bool(views_valid),
    }


def _legal_allocation(initial: int, records: list[dict]) -> bool:
    if len(records) > 16 or any(record["status"] not in (201, 409) for record in records):
        return False
    predecessors = [
        sum(
            1 << j
            for j, other in enumerate(records)
            if j != i and other["ended"] <= record["started"]
        )
        for i, record in enumerate(records)
    ]

    @cache
    def visit(remaining: int, stock: int) -> bool:
        if remaining == 0:
            return True
        for index, record in enumerate(records):
            bit = 1 << index
            if not remaining & bit or predecessors[index] & remaining:
                continue
            quantity = record["payload"]["quantity"]
            success = stock >= quantity
            if success != (record["status"] == 201):
                continue
            if visit(remaining ^ bit, stock - quantity if success else stock):
                return True
        return False

    return visit((1 << len(records)) - 1, initial)


def _counts(records: list[dict]) -> dict[str, int]:
    return dict(Counter(str(record["status"]) for record in records))


def probe(case_id: str, urls: dict[str, str], evidence_dir: Path) -> dict:
    import httpx

    install_runtime(case_id)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    nodes = [urls["direct"].rstrip("/")]
    if case_id == "12":
        nodes.append(urls["secondary"].rstrip("/"))
        if nodes[0] == nodes[1]:
            raise ValueError("Case 12 requires two distinct application node URLs")
    prefix = f"case{case_id}-{uuid.uuid4().hex[:10]}"
    products = []
    groups = {}
    specs = {}
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(timeout=20, limits=limits, trust_env=False) as client:
        for base in nodes:
            response = client.get(base + "/health/ready")
            response.raise_for_status()

        def fixture(label: str, stock: int):
            product = _create(client, nodes[0], f"{prefix}-{label}", stock)
            products.append(product)
            return product

        for index, base in enumerate(nodes):
            product = fixture(f"serial-{index}", 5)
            label = f"serial-{index}"
            groups[label] = [
                _send(client, base, product, 1, f"{prefix}-{label}-{i}") for i in range(8)
            ]
            specs[label] = {
                "product_id": product["id"],
                "initial": 5,
                "expected": {"201": 5, "409": 3},
            }

        if case_id == "11":
            product = fixture("ample", 120)
            for number in range(10):
                label = f"ample-{number}"
                groups[label] = _batch(client, nodes, [(product, 1)] * 8, f"{prefix}-{label}")
                specs[label] = {"product_id": product["id"], "expected": {"201": 8}}
            for number in range(2):
                label = f"sellout-{number}"
                product = fixture(label, 5)
                groups[label] = _batch(client, nodes, [(product, 1)] * 12, f"{prefix}-{label}")
                specs[label] = {
                    "product_id": product["id"],
                    "initial": 5,
                    "expected": {"201": 5, "409": 7},
                }
        else:
            for number in range(10):
                label = f"allocation-{number}"
                product = fixture(label, 5)
                groups[label] = _batch(client, nodes, [(product, 1)] * 8, f"{prefix}-{label}")
                specs[label] = {
                    "product_id": product["id"],
                    "initial": 5,
                    "expected": {"201": 5, "409": 3},
                }

        product = fixture("quantity-two", 10)
        groups["quantity-two"] = _batch(client, nodes, [(product, 2)] * 8, f"{prefix}-quantity-two")
        specs["quantity-two"] = {
            "product_id": product["id"],
            "initial": 10,
            "expected": {"201": 5, "409": 3},
        }
        product = fixture("mixed", 12)
        groups["mixed"] = _batch(
            client, nodes, [(product, q) for q in (1, 2, 3, 1, 2, 3, 1, 2)], f"{prefix}-mixed"
        )
        specs["mixed"] = {"product_id": product["id"], "initial": 12}
        database = _database_snapshot([product["id"] for product in products])
        views = _views(client, nodes, products)

    records = [record for group in groups.values() for record in group]
    checks = _reconcile(products, records, database, views)
    checks["serial_controls"] = all(
        _counts(records) == specs[name]["expected"]
        for name, records in groups.items()
        if name.startswith("serial-")
    )
    checks["uniform_allocation_counts"] = all(
        _counts(records) == specs[name]["expected"]
        for name, records in groups.items()
        if "expected" in specs[name]
    )
    checks["mixed_allocation_has_legal_order"] = _legal_allocation(12, groups["mixed"])
    if case_id == "12":
        checks["both_nodes_received_concurrent_orders"] = all(
            set(record["node"] for record in records) == set(nodes)
            for name, records in groups.items()
            if name.startswith("allocation-")
        )
    healthy = all(checks.values())
    common = all(
        value
        for name, value in checks.items()
        if name
        in {
            "response_contract_and_amounts",
            "success_ids_equal_persisted_orders",
            "nonnegative_stock",
            "initial_stock_unchanged",
            "api_views_match_database",
            "serial_controls",
        }
    )
    if case_id == "11":
        ample = [
            record
            for name, records in groups.items()
            if name.startswith("ample-")
            for record in records
        ]
        symptom = (
            common
            and checks["stock_equation"]
            and any(record["status"] == 500 for record in ample)
            and any(record["status"] == 201 for record in ample)
            and all(record["status"] in (201, 500) for record in ample)
        )
    else:
        allocation_ids = {
            value["product_id"] for name, value in specs.items() if name.startswith("allocation-")
        }
        quantities = Counter()
        for order in database["orders"]:
            quantities[order["product_id"]] += order["quantity"]
        symptom = (
            common
            and not checks["stock_equation"]
            and checks["both_nodes_received_concurrent_orders"]
            and all(record["status"] in (201, 409) for record in records)
            and any(quantities[product_id] > 5 for product_id in allocation_ids)
        )
    observations = {
        "scope": (
            "Bounded real HTTP callers; fixtures through API; "
            "independent read-only PostgreSQL reconciliation"
        ),
        "nodes": nodes,
        "products": products,
        "groups": {
            name: {"fixture": specs[name], "status_counts": _counts(group), "requests": group}
            for name, group in groups.items()
        },
        "database": database,
        "api_views": views,
    }
    (evidence_dir / f"case{case_id}-data-observations.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "healthy": bool(healthy),
        "symptom": bool(symptom),
        "checks": checks,
        "observations": observations,
    }
