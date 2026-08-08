from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import Inventory


@dataclass(frozen=True)
class StockChange:
    initial_stock: int
    previous_stock: int
    current_stock: int


class InventoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, product_id: int, initial_stock: int) -> Inventory:
        inventory = Inventory(
            product_id=product_id,
            initial_stock=initial_stock,
            current_stock=initial_stock,
        )
        self.session.add(inventory)
        self.session.flush()
        return inventory

    def get(self, product_id: int) -> Inventory | None:
        return self.session.scalar(select(Inventory).where(Inventory.product_id == product_id))

    def decrement_if_available(self, *, product_id: int, quantity: int) -> StockChange | None:
        inventory = self.get(product_id)
        if inventory is None or inventory.current_stock < quantity:
            return None

        previous_stock = inventory.current_stock
        inventory.current_stock = previous_stock - quantity
        self.session.flush()
        return StockChange(
            initial_stock=inventory.initial_stock,
            previous_stock=previous_stock,
            current_stock=inventory.current_stock,
        )
