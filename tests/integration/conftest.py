from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE orders, inventories, products RESTART IDENTITY CASCADE")
        )
    yield
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE orders, inventories, products RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def create_product(
    client: TestClient, *, name: str = "Test Product", unit_price: int = 10_000, stock: int = 3
) -> dict[str, object]:
    response = client.post(
        "/products",
        json={"name": name, "unit_price": unit_price, "initial_stock": stock},
    )
    assert response.status_code == 201, response.text
    return response.json()
