"""Break one request into internal stages and time each stage (TRAFFIC-001 profiler).

Run inside the app container. Route functions are called directly with a real session while
repository/service methods and SQL execution are wrapped with timers. The same request is also
sent over HTTP so that the framework and queueing share (HTTP total - in-app total) is visible.

Environment: N (repetitions per scenario), LABEL (report label).
"""

import json
import os
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.api import routes
from app.database import SessionFactory, engine
from app.models.order import Order
from app.models.product import Product
from app.repositories.inventory import InventoryRepository
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.schemas.order import OrderCreate
from app.schemas.product import ProductResponse
from app.services.product import ProductSnapshot
from app.services.shipping import ShippingQuoteService

N = int(os.environ.get("N", "15"))
LABEL = os.environ.get("LABEL", "idle")

F = TypeVar("F", bound=Callable[..., Any])
Call = Callable[[Session], BaseModel]

stack: list[str] = []
spans: dict[str, float] = defaultdict(float)  # stage -> ms, children included
sql_ms: dict[str, float] = defaultdict(float)  # stage -> SQL execution ms inside it
sql_count: dict[str, int] = defaultdict(int)
sql_rows: dict[str, int] = defaultdict(int)


def span(name: str) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        @wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            stack.append(name)
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                spans[name] += (time.perf_counter() - started) * 1_000
                stack.pop()

        return inner  # type: ignore[return-value]

    return decorate


@event.listens_for(engine, "before_cursor_execute")
def _before(conn: Any, cursor: Any, stmt: Any, params: Any, ctx: Any, many: bool) -> None:
    ctx._profile_started = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after(conn: Any, cursor: Any, stmt: Any, params: Any, ctx: Any, many: bool) -> None:
    owner = stack[-1] if stack else "(other)"
    sql_ms[owner] += (time.perf_counter() - ctx._profile_started) * 1_000
    sql_count[owner] += 1
    if cursor.rowcount and cursor.rowcount > 0:
        sql_rows[owner] += cursor.rowcount


def instrument() -> None:
    patches: list[tuple[Any, str, str]] = [
        (ProductRepository, "search", "repo.search: 목록·개수 SQL"),
        (ProductRepository, "get", "repo.product.get"),
        (InventoryRepository, "get", "repo.inventory.get"),
        (InventoryRepository, "decrement_if_available", "repo.inventory.decrement"),
        (OrderRepository, "create", "repo.order.create"),
        (OrderRepository, "units_sold_by_product", "repo.order.units_sold_by_product"),
        (ShippingQuoteService, "quote", "shipping.quote"),
    ]
    for owner, attribute, name in patches:
        setattr(owner, attribute, span(name)(getattr(owner, attribute)))
    for owner, attribute, name in [
        (ProductSnapshot, "of", "snapshot 변환"),
        (ProductResponse, "model_validate", "응답 모델 변환"),
    ]:
        setattr(owner, attribute, classmethod(span(name)(getattr(owner, attribute).__func__)))


def run(
    call: Call,
) -> tuple[float, dict[str, float], dict[str, float], dict[str, int], dict[str, int]]:
    for table in (spans, sql_ms, sql_count, sql_rows):
        table.clear()
    started = time.perf_counter()
    with SessionFactory() as session:
        stack.append("세션·트랜잭션(기타)")
        result = call(session)
        stack.pop()
    serialize_started = time.perf_counter()
    result.model_dump_json()
    stages = dict(spans)
    stages["JSON 직렬화"] = (time.perf_counter() - serialize_started) * 1_000
    total = (serialize_started - started) * 1_000 + stages["JSON 직렬화"]
    return total, stages, dict(sql_ms), dict(sql_count), dict(sql_rows)


def main() -> None:
    instrument()
    with SessionFactory() as session:
        detail_id = session.scalar(select(Product.id).where(Product.category == "home").limit(1))
        order_id = session.scalar(
            select(Product.id)
            .where(Product.name.like("부하테스트 전용 상품%"), Product.name.not_like("%품절"))
            .order_by(Product.id.desc())
            .limit(1)
        )
        scale = {
            "products": session.scalar(select(func.count()).select_from(Product)),
            "orders": session.scalar(select(func.count()).select_from(Order)),
        }
    if detail_id is None or order_id is None:
        raise SystemExit("seed the catalog and run the load test once before profiling")
    order_body = {"product_id": order_id, "quantity": 1, "postal_code": "06236"}

    # name -> (in-app call, HTTP method, path, params or JSON body)
    scenarios: dict[str, tuple[Call, str, str, dict[str, Any] | None]] = {
        "목록(category=home, 추천순, 20개)": (
            lambda s: routes.list_products(s, None, "home", "recommended", 20, 0),
            "GET",
            "/products",
            {"category": "home", "sort": "recommended", "limit": 20},
        ),
        "개수 조회(limit=1)": (
            lambda s: routes.list_products(s, None, "food", "recommended", 1, 0),
            "GET",
            "/products",
            {"category": "food", "limit": 1},
        ),
        "검색(q=커피, 추천순, 20개)": (
            lambda s: routes.list_products(s, "커피", None, "recommended", 20, 0),
            "GET",
            "/products",
            {"q": "커피", "limit": 20},
        ),
        "상품 상세": (
            lambda s: routes.get_product(detail_id, s),
            "GET",
            f"/products/{detail_id}",
            None,
        ),
        "주문": (
            lambda s: routes.create_order(OrderCreate.model_validate(order_body), s),
            "POST",
            "/orders",
            order_body,
        ),
    }

    http = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)
    report: dict[str, Any] = {"label": LABEL, "scale": scale, "scenarios": {}}
    for name, (call, method, path, payload) in scenarios.items():
        totals: list[float] = []
        http_totals: list[float] = []
        stage_ms: dict[str, list[float]] = defaultdict(list)
        stage_sql: dict[str, list[float]] = defaultdict(list)
        counts: dict[str, int] = {}
        rows: dict[str, int] = {}
        for _ in range(N):
            total, stages, sql, count, row = run(call)
            totals.append(total)
            for stage, value in stages.items():
                stage_ms[stage].append(value)
            for stage, value in sql.items():
                stage_sql[stage].append(value)
            counts.update(count)
            rows.update(row)
            started = time.perf_counter()
            if method == "GET":
                http.get(path, params=payload).raise_for_status()
            else:
                http.post(path, json=payload).raise_for_status()
            http_totals.append((time.perf_counter() - started) * 1_000)
        report["scenarios"][name] = {
            "app_total_ms": round(statistics.median(totals), 2),
            "http_total_ms": round(statistics.median(http_totals), 2),
            "stages": {
                stage: {
                    "ms": round(statistics.median(values), 2),
                    "sql_ms": round(statistics.median(stage_sql[stage]), 2)
                    if stage in stage_sql
                    else 0.0,
                    "sql_count": counts.get(stage, 0),
                    "rows": rows.get(stage, 0),
                }
                for stage, values in stage_ms.items()
            },
        }
    print("RESULT " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
