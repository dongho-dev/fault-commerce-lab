from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.repositories.inventory import InventoryRepository
from app.repositories.product import ProductRepository
from app.services.errors import ProductNotFoundError
from lab_suite.cache_adapter import cached_snapshot


@dataclass(frozen=True)
class ProductSnapshot:
    id: int
    name: str
    unit_price: int
    initial_stock: int
    current_stock: int
    created_at: datetime
    category: str
    brand: str
    description: str
    list_price: int | None
    image_url: str | None

    @classmethod
    def of(cls, product: Product, inventory: Inventory) -> "ProductSnapshot":
        return cls(
            id=product.id,
            name=product.name,
            unit_price=product.unit_price,
            initial_stock=inventory.initial_stock,
            current_stock=inventory.current_stock,
            created_at=product.created_at,
            category=product.category,
            brand=product.brand,
            description=product.description,
            list_price=product.list_price,
            image_url=product.image_url,
        )


@dataclass(frozen=True)
class ProductPage:
    items: list[ProductSnapshot]
    total: int
    limit: int
    offset: int


class ProductService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.inventories = InventoryRepository(session)

    def create(
        self,
        *,
        name: str,
        unit_price: int,
        initial_stock: int,
        category: str = "etc",
        brand: str = "",
        description: str = "",
        list_price: int | None = None,
        image_url: str | None = None,
    ) -> ProductSnapshot:
        with self.session.begin():
            product = self.products.create(
                name=name,
                unit_price=unit_price,
                category=category,
                brand=brand,
                description=description,
                list_price=list_price,
                image_url=image_url,
            )
            inventory = self.inventories.create(
                product_id=product.id,
                initial_stock=initial_stock,
            )
        return ProductSnapshot.of(product, inventory)

    @cached_snapshot
    def get(self, product_id: int) -> ProductSnapshot:
        with self.session.begin():
            product = self.products.get(product_id)
            if product is None:
                raise ProductNotFoundError
            inventory = self.inventories.get(product_id)
            if inventory is None:
                raise RuntimeError("product inventory is missing")
        return ProductSnapshot.of(product, inventory)

    def search(
        self,
        *,
        query: str | None,
        category: str | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> ProductPage:
        with self.session.begin():
            rows, total = self.products.search(
                query=query,
                category=category,
                sort=sort,
                limit=limit,
                offset=offset,
            )
            items = [ProductSnapshot.of(product, inventory) for product, inventory in rows]
        return ProductPage(items=items, total=total, limit=limit, offset=offset)
