from collections import Counter
from collections.abc import Collection

from sqlalchemy import func, select
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

    def units_sold_by_product(self, product_ids: Collection[int]) -> Counter[int]:
        """Sum ordered quantities in the database, only for the given products."""
        sold: Counter[int] = Counter()
        if not product_ids:
            return sold
        rows = self.session.execute(
            select(Order.product_id, func.sum(Order.quantity))
            .where(Order.product_id.in_(product_ids))
            .group_by(Order.product_id)
        )
        for product_id, quantity in rows:
            sold[product_id] = int(quantity)
        return sold
