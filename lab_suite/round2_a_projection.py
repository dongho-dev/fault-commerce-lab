import concurrent.futures
import dataclasses
import json
import os
import time
import uuid
from pathlib import Path


def connect(application="round2-projection"):
    import psycopg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(dsn, application_name=application)


def initialize():
    with connect() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lab_r2a_projection "
            "(product_id BIGINT PRIMARY KEY, current_stock INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lab_r2a_applied "
            "(order_id BIGINT PRIMARY KEY, product_id BIGINT NOT NULL, quantity INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lab_r2a_progress "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_id BIGINT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO lab_r2a_progress VALUES (1,0) ON CONFLICT(singleton) DO NOTHING"
        )


def pending(connection):
    return connection.execute(
        "SELECT o.id, o.product_id, o.quantity FROM orders o "
        "WHERE NOT EXISTS (SELECT 1 FROM lab_r2a_applied a WHERE a.order_id=o.id) "
        "ORDER BY o.id LIMIT 16"
    ).fetchall()


def apply_order(order):
    order_id, product_id, quantity = order
    with connect("round2-projection-apply") as connection:
        connection.execute(
            "INSERT INTO lab_r2a_projection(product_id,current_stock) "
            "SELECT product_id,initial_stock FROM inventories WHERE product_id=%s "
            "ON CONFLICT(product_id) DO NOTHING",
            (product_id,),
        )
        inserted = connection.execute(
            "INSERT INTO lab_r2a_applied VALUES (%s,%s,%s) "
            "ON CONFLICT(order_id) DO NOTHING RETURNING order_id",
            (order_id, product_id, quantity),
        ).fetchone()
        if inserted:
            connection.execute(
                "UPDATE lab_r2a_projection SET current_stock=current_stock-%s WHERE product_id=%s",
                (quantity, product_id),
            )
            connection.execute(
                "UPDATE lab_r2a_progress SET last_id=GREATEST(last_id,%s) WHERE singleton=1",
                (order_id,),
            )


def projected(product_id, timeout=2):
    deadline = time.monotonic() + timeout
    with connect("round2-projection-read") as connection:
        connection.autocommit = True
        connection.execute(
            "INSERT INTO lab_r2a_projection(product_id,current_stock) "
            "SELECT product_id,initial_stock FROM inventories WHERE product_id=%s "
            "ON CONFLICT(product_id) DO NOTHING",
            (product_id,),
        )
        while True:
            row = connection.execute(
                "SELECT p.current_stock, NOT EXISTS (SELECT 1 FROM orders o "
                "WHERE o.product_id=p.product_id AND NOT EXISTS "
                "(SELECT 1 FROM lab_r2a_applied a WHERE a.order_id=o.id)) "
                "FROM lab_r2a_projection p WHERE p.product_id=%s",
                (product_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Projection product is missing")
            if row[1]:
                return row[0]
            if time.monotonic() >= deadline:
                raise TimeoutError("Product view did not reach the committed order boundary")
            time.sleep(0.02)


def install():
    from app.services.product import ProductService

    initialize()
    original_get = ProductService.get
    original_search = ProductService.search

    def get(self, product_id):
        product = original_get(self, product_id)
        return dataclasses.replace(product, current_stock=projected(product_id))

    def search(self, **kwargs):
        page = original_search(self, **kwargs)
        return dataclasses.replace(
            page,
            items=[
                dataclasses.replace(product, current_stock=projected(product.id))
                for product in page.items
            ],
        )

    ProductService.get = get
    ProductService.search = search


def main():
    initialize()
    path = Path(os.environ.get("LAB_EVIDENCE", "/evidence")) / "projection-generation.json"
    value = {"generation": uuid.uuid4().hex, "pid": os.getpid()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)
    running = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        while True:
            for order_id, future in list(running.items()):
                if future.done():
                    future.result()
                    del running[order_id]
            with connect("round2-projection-scan") as connection:
                candidates = pending(connection)
            for order in candidates:
                if order[0] not in running and len(running) < 3:
                    running[order[0]] = executor.submit(apply_order, order)
            time.sleep(0.02)


if __name__ == "__main__":
    main()
