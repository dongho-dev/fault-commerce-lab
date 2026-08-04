from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.inventory import InventoryRepository
from app.repositories.product import ProductRepository
from app.services.errors import ProductNotFoundError


@dataclass(frozen=True)
class ProductSnapshot:
    id: int
    name: str
    unit_price: int
    initial_stock: int
    current_stock: int
    created_at: datetime


class ProductService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.inventories = InventoryRepository(session)

    def create(self, *, name: str, unit_price: int, initial_stock: int) -> ProductSnapshot:
        with self.session.begin():
            product = self.products.create(name=name, unit_price=unit_price)
            inventory = self.inventories.create(
                product_id=product.id,
                initial_stock=initial_stock,
            )
        return ProductSnapshot(
            id=product.id,
            name=product.name,
            unit_price=product.unit_price,
            initial_stock=inventory.initial_stock,
            current_stock=inventory.current_stock,
            created_at=product.created_at,
        )

    def get(self, product_id: int) -> ProductSnapshot:
        with self.session.begin():
            product = self.products.get(product_id)
            if product is None:
                raise ProductNotFoundError
            inventory = self.inventories.get(product_id)
            if inventory is None:
                raise RuntimeError("product inventory is missing")
        return ProductSnapshot(
            id=product.id,
            name=product.name,
            unit_price=product.unit_price,
            initial_stock=inventory.initial_stock,
            current_stock=inventory.current_stock,
            created_at=product.created_at,
        )
