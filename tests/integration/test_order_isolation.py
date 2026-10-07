from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

from app.database import engine
from app.services.shipping import ShippingQuoteService
from tests.integration.conftest import create_product

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("stock", "request_count", "expected_counts"),
    [
        (2, 2, {201: 2}),
        (1, 2, {201: 1, 409: 1}),
        (10, 20, {201: 10, 409: 10}),
    ],
)
def test_overlapping_orders_use_available_stock(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    stock: int,
    request_count: int,
    expected_counts: dict[int, int],
) -> None:
    product = create_product(client, stock=stock)
    ready_to_update = Barrier(request_count)
    original_quote = ShippingQuoteService.quote

    def quote_after_reads(
        self: ShippingQuoteService,
        *,
        postal_code: str,
        quantity: int,
        merchandise_amount: int,
    ) -> int:
        ready_to_update.wait(timeout=20)
        return original_quote(
            self,
            postal_code=postal_code,
            quantity=quantity,
            merchandise_amount=merchandise_amount,
        )

    monkeypatch.setattr(ShippingQuoteService, "quote", quote_after_reads)

    def order_once(index: int) -> Response:
        return client.post(
            "/orders",
            json={"product_id": product["id"], "quantity": 1, "postal_code": "16841"},
            headers={"X-Request-ID": f"cs11-{stock}-{request_count}-{index}"},
        )

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        responses = list(executor.map(order_once, range(request_count)))

    assert Counter(response.status_code for response in responses) == expected_counts
    success_ids = {response.json()["id"] for response in responses if response.status_code == 201}
    for index, response in enumerate(responses):
        request_id = f"cs11-{stock}-{request_count}-{index}"
        assert response.headers["X-Request-ID"] == request_id
        if response.status_code == 409:
            assert response.json()["code"] == "INSUFFICIENT_STOCK"
            assert response.json()["request_id"] == request_id

    with engine.connect() as connection:
        current_stock = connection.execute(
            text("SELECT current_stock FROM inventories WHERE product_id = :id"),
            {"id": product["id"]},
        ).scalar_one()
        orders = (
            connection.execute(
                text("SELECT id, quantity, status FROM orders WHERE product_id = :id"),
                {"id": product["id"]},
            )
            .mappings()
            .all()
        )

    assert current_stock == max(stock - request_count, 0)
    assert len(orders) == min(stock, request_count)
    assert {order["id"] for order in orders} == success_ids
    assert all(order["quantity"] == 1 and order["status"] == "CONFIRMED" for order in orders)
