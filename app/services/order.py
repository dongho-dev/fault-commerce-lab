import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.product import Product
from app.observability.logging import log_stage
from app.observability.metrics import (
    INSUFFICIENT_STOCK_REJECTIONS,
    ORDER_ATTEMPTS,
    ORDERS_CONFIRMED,
)
from app.repositories.inventory import InventoryRepository
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.services.errors import InsufficientStockError, ProductNotFoundError
from app.services.shipping import ShippingQuoteService, calculate_total_amount


@dataclass(frozen=True)
class OrderQuote:
    product_id: int
    quantity: int
    unit_price: int
    postal_code: str
    merchandise_amount: int
    shipping_fee: int
    total_amount: int


@dataclass(frozen=True)
class OrderSnapshot:
    id: int
    product_id: int
    quantity: int
    unit_price: int
    postal_code: str
    shipping_fee: int
    total_amount: int
    status: str
    created_at: datetime


class OrderService:
    def __init__(self, session: Session, shipping: ShippingQuoteService | None = None) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.inventories = InventoryRepository(session)
        self.orders = OrderRepository(session)
        self.shipping = shipping or ShippingQuoteService()

    def quote(self, *, product_id: int, quantity: int, postal_code: str) -> OrderQuote:
        product = self.products.get(product_id)
        if product is None:
            raise ProductNotFoundError
        return self._calculate_quote(product=product, quantity=quantity, postal_code=postal_code)

    def _calculate_quote(self, *, product: Product, quantity: int, postal_code: str) -> OrderQuote:
        merchandise_amount = product.unit_price * quantity
        shipping_fee = self.shipping.quote(
            postal_code=postal_code,
            quantity=quantity,
            merchandise_amount=merchandise_amount,
        )
        return OrderQuote(
            product_id=product.id,
            quantity=quantity,
            unit_price=product.unit_price,
            postal_code=postal_code,
            merchandise_amount=merchandise_amount,
            shipping_fee=shipping_fee,
            total_amount=calculate_total_amount(
                unit_price=product.unit_price, quantity=quantity, shipping_fee=shipping_fee
            ),
        )

    def create(self, *, product_id: int, quantity: int, postal_code: str) -> OrderSnapshot:
        ORDER_ATTEMPTS.inc()
        transaction_started = time.perf_counter()
        with self.session.begin():
            stage_started = time.perf_counter()
            product = self.products.get(product_id)
            if product is None:
                raise ProductNotFoundError
            log_stage(
                stage="product_loaded",
                event="completed",
                duration_ms=(time.perf_counter() - stage_started) * 1_000,
                product_id=product_id,
            )

            inventory = self.inventories.get(product_id)
            if inventory is None:
                raise RuntimeError("product inventory is missing")
            log_stage(
                stage="inventory_observed",
                event="completed",
                product_id=product_id,
                requested_quantity=quantity,
                observed_current_stock=inventory.current_stock,
            )

            stage_started = time.perf_counter()
            quote = self._calculate_quote(
                product=product, quantity=quantity, postal_code=postal_code
            )
            log_stage(
                stage="shipping_quote_completed",
                event="completed",
                duration_ms=(time.perf_counter() - stage_started) * 1_000,
                product_id=product_id,
                requested_quantity=quantity,
            )

            stage_started = time.perf_counter()
            stock_change = self.inventories.decrement_if_available(
                product_id=product_id,
                quantity=quantity,
            )
            if stock_change is None:
                INSUFFICIENT_STOCK_REJECTIONS.inc()
                raise InsufficientStockError
            log_stage(
                stage="stock_updated",
                event="completed",
                duration_ms=(time.perf_counter() - stage_started) * 1_000,
                product_id=product_id,
                requested_quantity=quantity,
                observed_current_stock=stock_change.previous_stock,
                resulting_current_stock=stock_change.current_stock,
            )

            stage_started = time.perf_counter()
            order = self.orders.create(
                product_id=product_id,
                quantity=quantity,
                unit_price=quote.unit_price,
                postal_code=postal_code,
                shipping_fee=quote.shipping_fee,
                total_amount=quote.total_amount,
            )
            log_stage(
                stage="order_persisted",
                event="completed",
                duration_ms=(time.perf_counter() - stage_started) * 1_000,
                product_id=product_id,
                order_id=order.id,
                requested_quantity=quantity,
            )

        log_stage(
            stage="transaction_completed",
            event="committed",
            duration_ms=(time.perf_counter() - transaction_started) * 1_000,
            product_id=product_id,
            order_id=order.id,
            requested_quantity=quantity,
        )
        ORDERS_CONFIRMED.inc()
        return OrderSnapshot(
            id=order.id,
            product_id=order.product_id,
            quantity=order.quantity,
            unit_price=order.unit_price,
            postal_code=order.postal_code,
            shipping_fee=order.shipping_fee,
            total_amount=order.total_amount,
            status=order.status,
            created_at=order.created_at,
        )
