from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, name: str, unit_price: int) -> Product:
        product = Product(name=name, unit_price=unit_price)
        self.session.add(product)
        self.session.flush()
        return product

    def get(self, product_id: int) -> Product | None:
        return self.session.scalar(select(Product).where(Product.id == product_id))
