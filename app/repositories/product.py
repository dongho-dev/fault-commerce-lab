from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        name: str,
        unit_price: int,
        category: str = "etc",
        brand: str = "",
        description: str = "",
        list_price: int | None = None,
        image_url: str | None = None,
    ) -> Product:
        product = Product(
            name=name,
            unit_price=unit_price,
            category=category,
            brand=brand,
            description=description,
            list_price=list_price,
            image_url=image_url,
        )
        self.session.add(product)
        self.session.flush()
        return product

    def get(self, product_id: int) -> Product | None:
        return self.session.scalar(select(Product).where(Product.id == product_id))

    def search(
        self,
        *,
        query: str | None,
        category: str | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[tuple[Product, Inventory]], int]:
        conditions: list[ColumnElement[bool]] = []
        if category is not None:
            conditions.append(Product.category == category)
        if query:
            pattern = "%" + escape_like(query) + "%"
            conditions.append(
                or_(
                    Product.name.ilike(pattern, escape="\\"),
                    Product.brand.ilike(pattern, escape="\\"),
                )
            )

        total = self.session.scalar(
            select(func.count()).select_from(Product).where(*conditions)
        )
        statement = (
            select(Product, Inventory)
            .join(Inventory, Inventory.product_id == Product.id)
            .where(*conditions)
            .order_by(*order_clauses(sort), Product.id)
            .limit(limit)
            .offset(offset)
        )
        return self.session.execute(statement).tuples().all(), int(total or 0)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def order_clauses(sort: str) -> list[ColumnElement[Any]]:
    in_stock_first = case((Inventory.current_stock > 0, 0), else_=1)
    discount = func.coalesce(Product.list_price, Product.unit_price) - Product.unit_price
    match sort:
        case "price_asc":
            return [Product.unit_price.asc()]
        case "price_desc":
            return [Product.unit_price.desc()]
        case "discount":
            return [in_stock_first, (discount * 1000 / Product.unit_price).desc()]
        case "newest":
            return [Product.created_at.desc(), Product.id.desc()]
        case _:
            return [in_stock_first]
