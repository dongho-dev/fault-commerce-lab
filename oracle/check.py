import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from oracle.common import resolve_database_url


@dataclass(frozen=True)
class ProductInvariant:
    product_id: int
    initial_stock: int
    confirmed_quantity: int
    current_stock: int
    non_negative: bool
    stock_equation: bool


def stock_equation_holds(*, initial: int, confirmed_quantity: int, current: int) -> bool:
    return initial - confirmed_quantity == current


def order_amount_holds(
    *, unit_price: int, quantity: int, shipping_fee: int, total_amount: int
) -> bool:
    return total_amount == unit_price * quantity + shipping_fee


def collect_report(database_url: str | None = None) -> dict[str, Any]:
    products: list[ProductInvariant] = []
    invalid_order_ids: list[int] = []
    checked_orders = 0
    with psycopg.connect(resolve_database_url(database_url), row_factory=dict_row) as connection:
        product_rows = connection.execute(
            """
            SELECT
                p.id AS product_id,
                i.initial_stock,
                i.current_stock,
                COALESCE(SUM(o.quantity) FILTER (WHERE o.status = 'CONFIRMED'), 0)::BIGINT
                    AS confirmed_quantity
            FROM products AS p
            JOIN inventories AS i ON i.product_id = p.id
            LEFT JOIN orders AS o ON o.product_id = p.id
            GROUP BY p.id, i.initial_stock, i.current_stock
            ORDER BY p.id
            """
        ).fetchall()
        for row in product_rows:
            initial = int(row["initial_stock"])
            confirmed = int(row["confirmed_quantity"])
            current = int(row["current_stock"])
            products.append(
                ProductInvariant(
                    product_id=int(row["product_id"]),
                    initial_stock=initial,
                    confirmed_quantity=confirmed,
                    current_stock=current,
                    non_negative=current >= 0,
                    stock_equation=stock_equation_holds(
                        initial=initial,
                        confirmed_quantity=confirmed,
                        current=current,
                    ),
                )
            )

        order_rows = connection.execute(
            """
            SELECT id, unit_price, quantity, shipping_fee, total_amount
            FROM orders
            ORDER BY id
            """
        ).fetchall()
        checked_orders = len(order_rows)
        for row in order_rows:
            if not order_amount_holds(
                unit_price=int(row["unit_price"]),
                quantity=int(row["quantity"]),
                shipping_fee=int(row["shipping_fee"]),
                total_amount=int(row["total_amount"]),
            ):
                invalid_order_ids.append(int(row["id"]))

    passed = all(item.non_negative and item.stock_equation for item in products)
    passed = passed and not invalid_order_ids
    return {
        "passed": passed,
        "products": [asdict(item) for item in products],
        "orders": {
            "checked": checked_orders,
            "invalid_order_ids": invalid_order_ids,
            "amounts_valid": not invalid_order_ids,
        },
    }


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for product in report["products"]:
        non_negative_status = "PASS" if product["non_negative"] else "FAIL"
        lines.append(
            f"[{non_negative_status}] product={product['product_id']} non_negative "
            f"current_stock={product['current_stock']}"
        )
        equation_status = "PASS" if product["stock_equation"] else "FAIL"
        lines.append(
            f"[{equation_status}] product={product['product_id']} stock_equation "
            f"initial={product['initial_stock']} "
            f"confirmed_quantity={product['confirmed_quantity']} "
            f"actual={product['current_stock']}"
        )
    orders = report["orders"]
    order_status = "PASS" if orders["amounts_valid"] else "FAIL"
    lines.append(f"[{order_status}] order_amounts checked={orders['checked']}")
    lines.append(f"FINAL: {'PASS' if report['passed'] else 'FAIL'}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check L1 database invariants.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = collect_report(args.database_url)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render_text(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
