from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        product_id: int,
        quantity: int,
        unit_price: int,
        postal_code: str,
        shipping_fee: int,
        total_amount: int,
    ) -> Order:
        order = Order(
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            postal_code=postal_code,
            shipping_fee=shipping_fee,
            total_amount=total_amount,
            status="CONFIRMED",
        )
        self.session.add(order)
        self.session.flush()
        return order

    def units_sold_by_product(self) -> Counter[int]:
        sold: Counter[int] = Counter()
        for order in self.session.scalars(select(Order)):
            sold[order.product_id] += order.quantity
        return sold
