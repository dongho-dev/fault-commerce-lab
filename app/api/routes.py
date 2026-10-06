from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.observability.metrics import metrics_payload
from app.schemas.order import OrderCreate, OrderQuoteResponse, OrderResponse
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductSort,
)
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
        category=payload.category,
        brand=payload.brand,
        description=payload.description,
        list_price=payload.list_price,
        image_url=payload.image_url,
    )
    return ProductResponse.model_validate(result)


@router.get("/products", response_model=ProductListResponse)
def list_products(
    session: SessionDependency,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9-]{0,39}$")] = None,
    sort: ProductSort = "recommended",
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListResponse:
    page = ProductService(session).search(
        query=q.strip() if q else None,
        category=category,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return ProductListResponse(
        items=[ProductResponse.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, session: SessionDependency) -> ProductResponse:
    result = ProductService(session).get(product_id)
    return ProductResponse.model_validate(result)


@router.post("/orders/quote", response_model=OrderQuoteResponse)
def quote_order(payload: OrderCreate, session: SessionDependency) -> OrderQuoteResponse:
    result = OrderService(session).quote(
        product_id=payload.product_id,
        quantity=payload.quantity,
        postal_code=payload.postal_code,
    )
    return OrderQuoteResponse.model_validate(result)


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
