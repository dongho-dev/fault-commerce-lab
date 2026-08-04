import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from oracle.common import ensure_artifact_parent, resolve_database_url

DEFAULT_ARTIFACT = Path("artifacts/reset-state.json")


def parse_stocks(value: str) -> list[int]:
    if not value.strip():
        return []
    stocks = [int(item.strip()) for item in value.split(",")]
    if any(stock < 0 for stock in stocks):
        raise ValueError("stocks must be non-negative integers")
    return stocks


def reset_database(
    stocks: list[int],
    *,
    database_url: str | None = None,
    artifact_path: Path = DEFAULT_ARTIFACT,
) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    with psycopg.connect(resolve_database_url(database_url), row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute(
                "TRUNCATE TABLE orders, inventories, products RESTART IDENTITY CASCADE"
            )
            for index, stock in enumerate(stocks, start=1):
                name = f"Baseline Product {index}"
                unit_price = 10_000 + index * 1_000
                postal_code = f"{10000 + index}"
                product = connection.execute(
                    """
                    INSERT INTO products (name, unit_price)
                    VALUES (%s, %s)
                    RETURNING id, name, unit_price, created_at
                    """,
                    (name, unit_price),
                ).fetchone()
                if product is None:
                    raise RuntimeError("product insert returned no row")
                connection.execute(
                    """
                    INSERT INTO inventories (product_id, initial_stock, current_stock)
                    VALUES (%s, %s, %s)
                    """,
                    (product["id"], stock, stock),
                )
                products.append(
                    {
                        "id": product["id"],
                        "name": product["name"],
                        "unit_price": product["unit_price"],
                        "postal_code": postal_code,
                        "initial_stock": stock,
                        "current_stock": stock,
                        "created_at": product["created_at"],
                    }
                )

    result = {"stocks": stocks, "products": products}
    ensure_artifact_parent(artifact_path)
    artifact_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=serialize_value),
        encoding="utf-8",
    )
    return result


def serialize_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reset deterministic L1 oracle data.")
    parser.add_argument("--stocks", default="10", help="Comma-separated initial stock values.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        stocks = parse_stocks(args.stocks)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    result = reset_database(stocks, database_url=args.database_url, artifact_path=args.artifact)
    print(json.dumps(result, ensure_ascii=False, default=serialize_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
