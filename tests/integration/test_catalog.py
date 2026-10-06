import pytest
from fastapi.testclient import TestClient

from app.catalog import CATALOG, seed_catalog
from app.database import SessionFactory
from tests.integration.conftest import create_product

pytestmark = pytest.mark.integration


def create_catalog_product(client: TestClient, **fields: object) -> dict[str, object]:
    payload: dict[str, object] = {"unit_price": 10_000, "initial_stock": 5, **fields}
    response = client.post("/products", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_fields_default_for_minimal_product(client: TestClient) -> None:
    created = create_product(client, name="Plain Product")
    assert created["category"] == "etc"
    assert created["brand"] == ""
    assert created["description"] == ""
    assert created["list_price"] is None
    assert created["image_url"] is None


def test_catalog_fields_round_trip(client: TestClient) -> None:
    created = create_catalog_product(
        client,
        name="Desk Lamp",
        category="home",
        brand="  Lightway ",
        description="Warm light",
        unit_price=30_000,
        list_price=40_000,
        image_url="/static/assets/products/lamp.svg",
    )
    assert created["brand"] == "Lightway"
    assert client.get(f"/products/{created['id']}").json() == created


@pytest.mark.parametrize(
    "fields",
    [
        {"list_price": 9_999},
        {"category": "Has Space"},
        {"image_url": "javascript:alert(1)"},
    ],
)
def test_invalid_catalog_fields_return_422(client: TestClient, fields: dict[str, object]) -> None:
    response = client.post(
        "/products",
        json={"name": "Bad", "unit_price": 10_000, "initial_stock": 1, **fields},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_list_filters_by_category_and_query(client: TestClient) -> None:
    create_catalog_product(client, name="Wireless Keyboard", category="digital", brand="Keyline")
    create_catalog_product(client, name="Wireless Mouse", category="digital", brand="Keyline")
    create_catalog_product(client, name="Frying Pan", category="kitchen", brand="Cookwell")

    digital = client.get("/products", params={"category": "digital"}).json()
    assert digital["total"] == 2
    assert {item["name"] for item in digital["items"]} == {"Wireless Keyboard", "Wireless Mouse"}

    by_brand = client.get("/products", params={"q": "cookwell"}).json()
    assert [item["name"] for item in by_brand["items"]] == ["Frying Pan"]

    literal_percent = client.get("/products", params={"q": "%"}).json()
    assert literal_percent["total"] == 0


def test_list_sorts_and_paginates(client: TestClient) -> None:
    create_catalog_product(client, name="Mid", unit_price=20_000)
    create_catalog_product(client, name="Cheap", unit_price=5_000)
    create_catalog_product(client, name="Sold Out", unit_price=1_000, initial_stock=0)
    create_catalog_product(client, name="Pricey", unit_price=90_000, list_price=180_000)

    ascending = client.get("/products", params={"sort": "price_asc"}).json()
    assert [item["name"] for item in ascending["items"]] == ["Sold Out", "Cheap", "Mid", "Pricey"]

    recommended = client.get("/products").json()
    assert recommended["items"][-1]["name"] == "Sold Out"

    discount = client.get("/products", params={"sort": "discount"}).json()
    assert discount["items"][0]["name"] == "Pricey"

    page = client.get("/products", params={"sort": "price_desc", "limit": 2, "offset": 1}).json()
    assert page["total"] == 4
    assert [item["name"] for item in page["items"]] == ["Mid", "Cheap"]


def test_list_rejects_invalid_query(client: TestClient) -> None:
    assert client.get("/products", params={"limit": 0}).status_code == 422
    assert client.get("/products", params={"sort": "random"}).status_code == 422


def test_seed_catalog_is_idempotent(client: TestClient) -> None:
    with SessionFactory() as session:
        first = seed_catalog(session)
    with SessionFactory() as session:
        second = seed_catalog(session)
    assert len(first) == len(CATALOG)
    assert second == []

    listing = client.get("/products", params={"limit": 100}).json()
    assert listing["total"] == len(CATALOG)
    assert all(item["image_url"].endswith(".jpg") for item in listing["items"])
