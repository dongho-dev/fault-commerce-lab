import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from app.repositories.order import OrderRepository
from tests.integration.conftest import create_product

pytestmark = pytest.mark.integration


def test_create_and_get_product(client: TestClient) -> None:
    created = create_product(client, name="  Limited Keyboard  ", unit_price=129_000, stock=10)
    assert created["name"] == "Limited Keyboard"
    assert created["initial_stock"] == created["current_stock"] == 10
    response = client.get(f"/products/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_missing_product_has_contract_error(client: TestClient) -> None:
    response = client.get("/products/999999", headers={"X-Request-ID": "lookup-123"})
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "lookup-123"
    assert response.json() == {
        "code": "PRODUCT_NOT_FOUND",
        "message": "상품을 찾을 수 없습니다.",
        "request_id": "lookup-123",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"name": " ", "unit_price": 1, "initial_stock": 0},
        {"name": "Product", "unit_price": 0, "initial_stock": 0},
        {"name": "Product", "unit_price": 1, "initial_stock": -1},
    ],
)
def test_invalid_product_input_returns_422(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post("/products", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_normal_order_decrements_stock_and_snapshots_price(client: TestClient) -> None:
    product = create_product(client, unit_price=12_000, stock=3)
    response = client.post(
        "/orders",
        json={"product_id": product["id"], "quantity": 2, "postal_code": "16841"},
    )
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "CONFIRMED"
    assert order["unit_price"] == 12_000
    assert order["total_amount"] == 27_000
    loaded = client.get(f"/products/{product['id']}").json()
    assert loaded["current_stock"] == 1

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE products SET unit_price = 20000 WHERE id = :id"),
            {"id": product["id"]},
        )
        stored_price = connection.execute(
            text("SELECT unit_price FROM orders WHERE id = :id"), {"id": order["id"]}
        ).scalar_one()
    assert stored_price == 12_000


def test_insufficient_stock_is_atomic(client: TestClient) -> None:
    product = create_product(client, stock=2)
    response = client.post(
        "/orders",
        json={"product_id": product["id"], "quantity": 3, "postal_code": "16841"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "INSUFFICIENT_STOCK"
    with engine.connect() as connection:
        stock = connection.execute(
            text("SELECT current_stock FROM inventories WHERE product_id = :id"),
            {"id": product["id"]},
        ).scalar_one()
        orders = connection.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
    assert stock == 2
    assert orders == 0


def test_missing_product_order_returns_404(client: TestClient) -> None:
    response = client.post(
        "/orders", json={"product_id": 999_999, "quantity": 1, "postal_code": "16841"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.parametrize(
    "payload",
    [
        {"product_id": 1, "quantity": 0, "postal_code": "16841"},
        {"product_id": 1, "quantity": 1, "postal_code": " "},
        {"product_id": 1, "quantity": 1, "postal_code": "#invalid"},
    ],
)
def test_invalid_order_input_returns_422(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post("/orders", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_unexpected_exception_rolls_back_inventory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = create_product(client, stock=2)

    def fail_create(self: OrderRepository, **_values: object) -> None:
        raise RuntimeError("injected test exception")

    monkeypatch.setattr(OrderRepository, "create", fail_create)
    response = client.post(
        "/orders",
        json={"product_id": product["id"], "quantity": 1, "postal_code": "16841"},
    )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
    with engine.connect() as connection:
        stock = connection.execute(
            text("SELECT current_stock FROM inventories WHERE product_id = :id"),
            {"id": product["id"]},
        ).scalar_one()
        orders = connection.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
    assert stock == 2
    assert orders == 0


def test_health_docs_and_metrics(client: TestClient) -> None:
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "Fault Commerce" in homepage.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    client.get("/products/12345")
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "commerce_http_requests_total" in metrics.text
    assert 'path="/products/{product_id}"' in metrics.text
    assert 'path="/products/12345"' not in metrics.text
