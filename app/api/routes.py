from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.observability.metrics import metrics_payload
from app.schemas.order import OrderCreate, OrderResponse
from app.schemas.product import ProductCreate, ProductResponse
from app.services.order import OrderService
from app.services.product import ProductService

router = APIRouter()
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, session: SessionDependency) -> ProductResponse:
    result = ProductService(session).create(
        name=payload.name,
        unit_price=payload.unit_price,
        initial_stock=payload.initial_stock,
    )
    return ProductResponse.model_validate(result)


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, session: SessionDependency) -> ProductResponse:
    result = ProductService(session).get(product_id)
    return ProductResponse.model_validate(result)


@router.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, session: SessionDependency) -> OrderResponse:
    result = OrderService(session).create(
        product_id=payload.product_id,
        quantity=payload.quantity,
        postal_code=payload.postal_code,
    )
    return OrderResponse.model_validate(result)


@router.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
def health_ready(session: SessionDependency) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="database is not ready") from exc
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    body, content_type = metrics_payload()
    return Response(content=body, media_type=content_type)
