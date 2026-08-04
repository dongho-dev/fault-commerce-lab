import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine
from app.repositories.inventory import InventoryRepository
from tests.integration.conftest import create_product

pytestmark = pytest.mark.integration


def test_product_and_inventory_creation_roll_back_together(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_inventory_create(self: InventoryRepository, **_values: object) -> None:
        raise RuntimeError("injected test exception")

    monkeypatch.setattr(InventoryRepository, "create", fail_inventory_create)
    response = client.post(
        "/products",
        json={"name": "Atomic Product", "unit_price": 10_000, "initial_stock": 4},
    )
    assert response.status_code == 500
    with engine.connect() as connection:
        product_count = connection.execute(text("SELECT COUNT(*) FROM products")).scalar_one()
        inventory_count = connection.execute(text("SELECT COUNT(*) FROM inventories")).scalar_one()
    assert product_count == 0
    assert inventory_count == 0


def test_database_constraint_blocks_negative_inventory(client: TestClient) -> None:
    product = create_product(client, stock=1)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE inventories SET current_stock = -1 WHERE product_id = :id"),
            {"id": product["id"]},
        )


def test_order_foreign_key_restricts_product_deletion(client: TestClient) -> None:
    product = create_product(client, stock=1)
    response = client.post(
        "/orders",
        json={"product_id": product["id"], "quantity": 1, "postal_code": "16841"},
    )
    assert response.status_code == 201
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("DELETE FROM products WHERE id = :id"),
            {"id": product["id"]},
        )
