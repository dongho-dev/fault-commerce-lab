import argparse
import json
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from oracle.common import resolve_database_url


def serialize_value(value: object) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def rows_as_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def collect_snapshot(database_url: str | None = None) -> dict[str, Any]:
    with psycopg.connect(resolve_database_url(database_url), row_factory=dict_row) as connection:
        products = connection.execute(
            "SELECT id, name, unit_price, created_at FROM products ORDER BY id"
        ).fetchall()
        inventories = connection.execute(
            """
            SELECT product_id, initial_stock, current_stock, updated_at
            FROM inventories
            ORDER BY product_id
            """
        ).fetchall()
        aggregates = connection.execute(
            """
            SELECT
                p.id AS product_id,
                p.name,
                i.initial_stock,
                i.current_stock,
                COUNT(o.id) FILTER (WHERE o.status = 'CONFIRMED')::BIGINT
                    AS confirmed_order_count,
                COALESCE(SUM(o.quantity) FILTER (WHERE o.status = 'CONFIRMED'), 0)::BIGINT
                    AS confirmed_quantity
            FROM products AS p
            JOIN inventories AS i ON i.product_id = p.id
            LEFT JOIN orders AS o ON o.product_id = p.id
            GROUP BY p.id, p.name, i.initial_stock, i.current_stock
            ORDER BY p.id
            """
        ).fetchall()
        recent_orders = connection.execute(
            """
            SELECT id, product_id, quantity, unit_price, postal_code, shipping_fee,
                   total_amount, status, created_at
            FROM orders
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
    return {
        "products": rows_as_dicts(products),
        "inventories": rows_as_dicts(inventories),
        "product_order_summary": rows_as_dicts(aggregates),
        "recent_orders": rows_as_dicts(recent_orders),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a direct PostgreSQL L1 snapshot.")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    snapshot = collect_snapshot(args.database_url)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=serialize_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
