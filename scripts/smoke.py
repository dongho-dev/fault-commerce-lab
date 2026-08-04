import argparse
from typing import Any

import httpx

from oracle.check import collect_report
from oracle.reset import reset_database


def require_status(response: httpx.Response, expected: int, label: str) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"{label}: expected HTTP {expected}, received {response.status_code}")
    return response.json()


def run_smoke(base_url: str, database_url: str | None = None) -> None:
    reset_database([], database_url=database_url)
    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        created = require_status(
            client.post(
                "/products",
                json={"name": "Smoke Keyboard", "unit_price": 129_000, "initial_stock": 3},
            ),
            201,
            "product creation",
        )
        product_id = int(created["id"])
        loaded = require_status(client.get(f"/products/{product_id}"), 200, "product lookup")
        if loaded["current_stock"] != 3:
            raise RuntimeError("product lookup returned unexpected stock")

        order = require_status(
            client.post(
                "/orders",
                json={"product_id": product_id, "quantity": 1, "postal_code": "16841"},
            ),
            201,
            "normal order",
        )
        if order["total_amount"] != order["unit_price"] + order["shipping_fee"]:
            raise RuntimeError("order amount equation failed")
        after_order = require_status(
            client.get(f"/products/{product_id}"), 200, "post-order product lookup"
        )
        if after_order["current_stock"] != 2:
            raise RuntimeError("stock did not decrease after order")

        insufficient = require_status(
            client.post(
                "/orders",
                json={"product_id": product_id, "quantity": 3, "postal_code": "16841"},
            ),
            409,
            "insufficient stock",
        )
        if insufficient.get("code") != "INSUFFICIENT_STOCK":
            raise RuntimeError("insufficient stock error code mismatch")
        missing = require_status(
            client.post(
                "/orders",
                json={"product_id": product_id + 999_999, "quantity": 1, "postal_code": "16841"},
            ),
            404,
            "missing product",
        )
        if missing.get("code") != "PRODUCT_NOT_FOUND":
            raise RuntimeError("missing product error code mismatch")

    oracle = collect_report(database_url)
    if not oracle["passed"]:
        raise RuntimeError("oracle invariant check failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the L1 HTTP smoke check.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    run_smoke(args.base_url, args.database_url)
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
