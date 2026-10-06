import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from tests.integration.conftest import create_product

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("postal_code", "unit_price", "quantity", "expected_shipping"),
    [
        ("06236", 50_000, 1, 3_000),
        ("00623", 50_000, 1, 3_000),
        ("00062", 50_000, 1, 3_000),
        ("16841", 50_000, 1, 3_000),
        ("59999", 199_999, 1, 3_000),
        ("60000", 199_999, 1, 5_500),
        ("06236", 200_000, 1, 0),
        ("06236", 100_000, 2, 0),
        ("06236", 50_000, 3, 3_700),
        ("63309", 50_000, 3, 6_200),
        ("06236", 70_000, 3, 700),
        ("63309", 70_000, 3, 3_200),
    ],
)
def test_quote_matches_created_and_stored_order(
    client: TestClient, postal_code: str, unit_price: int, quantity: int, expected_shipping: int
) -> None:
    product = create_product(client, unit_price=unit_price, stock=10)
    payload = {"product_id": product["id"], "quantity": quantity, "postal_code": postal_code}
    response = client.post("/orders/quote", json=payload)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote == {
        **payload,
        "unit_price": unit_price,
        "merchandise_amount": unit_price * quantity,
        "shipping_fee": expected_shipping,
        "total_amount": unit_price * quantity + expected_shipping,
    }

    order_response = client.post("/orders", json=payload)
    assert order_response.status_code == 201, order_response.text
    order = order_response.json()
    expected_snapshot = {key: value for key, value in quote.items() if key != "merchandise_amount"}
    assert {key: order[key] for key in expected_snapshot} == expected_snapshot
    with engine.connect() as connection:
        stored = (
            connection.execute(
                text(
                    "SELECT product_id, quantity, unit_price, postal_code, "
                    "shipping_fee, total_amount "
                    "FROM orders WHERE id = :id"
                ),
                {"id": order["id"]},
            )
            .mappings()
            .one()
        )
    assert dict(stored) == expected_snapshot
    assert client.get(f"/products/{product['id']}").json()["current_stock"] == 10 - quantity


def test_quote_uses_server_price_without_creating_or_reserving_an_order(client: TestClient) -> None:
    product = create_product(client, unit_price=50_000, stock=1)
    payload = {
        "product_id": product["id"],
        "quantity": 1,
        "postal_code": "06236",
        "unit_price": 1,
        "shipping_fee": 0,
        "total_amount": 1,
    }
    first = client.post("/orders/quote", json=payload)
    second = client.post("/orders/quote", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["unit_price"] == 50_000
    assert first.json()["shipping_fee"] == 3_000
    assert first.json()["total_amount"] == 53_000
    assert client.get(f"/products/{product['id']}").json()["current_stock"] == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM orders")).scalar_one() == 0


def test_missing_quote_product_returns_contract_error(client: TestClient) -> None:
    response = client.post(
        "/orders/quote",
        json={"product_id": 999_999, "quantity": 1, "postal_code": "06236"},
        headers={"X-Request-ID": "quote-missing"},
    )
    assert response.status_code == 404
    assert response.json() == {
        "code": "PRODUCT_NOT_FOUND",
        "message": "상품을 찾을 수 없습니다.",
        "request_id": "quote-missing",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"product_id": 1, "quantity": 0, "postal_code": "06236"},
        {"product_id": 1, "quantity": 1, "postal_code": " "},
        {"product_id": 1, "quantity": 1, "postal_code": "#invalid"},
    ],
)
def test_invalid_quote_input_returns_422(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post("/orders/quote", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
