import httpx
import pytest
from sqlalchemy import text

from app.database import engine
from oracle.check import collect_report
from scripts.concurrency_support import RequestSpec, run_batch, summarize_results
from tests.integration.live_server import running_server

pytestmark = [pytest.mark.integration, pytest.mark.live_http]


def create_live_product(base_url: str, *, name: str, stock: int) -> dict[str, object]:
    response = httpx.post(
        f"{base_url}/products",
        json={"name": name, "unit_price": 10_000, "initial_stock": stock},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_specs(product_id: int, *, quantity: int, count: int, group: str) -> list[RequestSpec]:
    return [
        RequestSpec(
            group=group,
            payload={"product_id": product_id, "quantity": quantity, "postal_code": "16841"},
        )
        for _ in range(count)
    ]


def test_data_survives_application_restart() -> None:
    with running_server() as first_url:
        product = create_live_product(first_url, name="Persistent Product", stock=5)
    with running_server() as second_url:
        response = httpx.get(f"{second_url}/products/{product['id']}", timeout=10)
    assert response.status_code == 200
    assert response.json()["current_stock"] == 5


def test_exact_concurrent_success_and_rejection_counts() -> None:
    with running_server() as base_url:
        product = create_live_product(base_url, name="Concurrent Product", stock=10)
        results = run_batch(
            base_url=base_url,
            specs=make_specs(int(product["id"]), quantity=1, count=40, group="single"),
        )
    summary = summarize_results(results)
    assert summary["status_counts"] == {"201": 10, "409": 30}
    assert summary["connection_errors"] == 0
    report = collect_report()
    assert report["passed"]
    assert report["products"][0]["confirmed_quantity"] == 10
    assert report["products"][0]["current_stock"] == 0


def test_two_products_remain_independently_consistent() -> None:
    with running_server() as base_url:
        product_a = create_live_product(base_url, name="Product A", stock=13)
        product_b = create_live_product(base_url, name="Product B", stock=18)
        specs = make_specs(int(product_a["id"]), quantity=1, count=30, group="A")
        specs.extend(make_specs(int(product_b["id"]), quantity=2, count=20, group="B"))
        results = run_batch(base_url=base_url, specs=specs)
    a_summary = summarize_results([item for item in results if item.group == "A"])
    b_summary = summarize_results([item for item in results if item.group == "B"])
    assert a_summary["status_counts"] == {"201": 13, "409": 17}
    assert b_summary["status_counts"] == {"201": 9, "409": 11}
    assert collect_report()["passed"]


def test_oracle_detects_database_mismatch() -> None:
    with running_server() as base_url:
        product = create_live_product(base_url, name="Oracle Product", stock=2)
    assert collect_report()["passed"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE inventories SET initial_stock = initial_stock + 1 WHERE product_id = :id"),
            {"id": product["id"]},
        )
    report = collect_report()
    assert not report["passed"]
    assert not report["products"][0]["stock_equation"]
