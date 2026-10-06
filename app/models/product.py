from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, server_default="etc")
    brand: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    list_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    inventory: Mapped[Inventory] = relationship(
        back_populates="product", uselist=False, passive_deletes=True
    )
    orders: Mapped[list[Order]] = relationship(back_populates="product", passive_deletes=True)


from app.models.inventory import Inventory  # noqa: E402
from app.models.order import Order  # noqa: E402
