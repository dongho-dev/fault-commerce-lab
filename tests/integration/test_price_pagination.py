import pytest
from fastapi.testclient import TestClient

from tests.integration.test_catalog import create_catalog_product

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "sort, expected_names",
    [
        ("price_asc", ["Cheap A", "Cheap B", "Middle A", "Middle B", "Expensive A", "Expensive B"]),
        (
            "price_desc",
            ["Expensive A", "Expensive B", "Middle A", "Middle B", "Cheap A", "Cheap B"],
        ),
    ],
)
@pytest.mark.parametrize("page_size", [2, 3, 20])
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"q": "Sort Target"},
        {"category": "digital"},
        {"q": "Sort Target", "category": "digital"},
    ],
    ids=["all", "query", "category", "query-and-category"],
)
def test_price_order_across_pages_with_id_ties(
    client: TestClient,
    sort: str,
    expected_names: list[str],
    page_size: int,
    filters: dict[str, str],
) -> None:
    products = {}
    for name, price in [
        ("Expensive A", 3_000),
        ("Cheap A", 1_000),
        ("Middle A", 2_000),
        ("Cheap B", 1_000),
        ("Expensive B", 3_000),
        ("Middle B", 2_000),
    ]:
        products[name] = create_catalog_product(
            client,
            name=f"Sort Target {name}",
            unit_price=price,
            category="digital",
            initial_stock=0 if name == "Cheap B" else 5,
        )

    for price in (1, 90_000):
        if "q" in filters:
            create_catalog_product(
                client, name=f"Unrelated {price}", unit_price=price, category="digital"
            )
        if "category" in filters:
            create_catalog_product(
                client, name=f"Sort Target Home {price}", unit_price=price, category="home"
            )

    observed_ids = []
    for offset in range(0, 6, page_size):
        response = client.get(
            "/products", params={**filters, "sort": sort, "limit": page_size, "offset": offset}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 6
        assert page["limit"] == page_size
        assert page["offset"] == offset
        assert len(page["items"]) == min(page_size, 6 - offset)
        observed_ids.extend(item["id"] for item in page["items"])

    assert observed_ids == [products[name]["id"] for name in expected_names]

    terminal = client.get(
        "/products", params={**filters, "sort": sort, "limit": page_size, "offset": 6}
    )
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["items"] == []
    assert terminal.json()["total"] == 6
