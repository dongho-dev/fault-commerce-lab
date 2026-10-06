from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProductSort = Literal["recommended", "price_asc", "price_desc", "discount", "newest"]


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit_price: int = Field(ge=1)
    initial_stock: int = Field(ge=0)
    category: str = Field(default="etc", pattern=r"^[a-z][a-z0-9-]{0,39}$")
    brand: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=2000)
    list_price: int | None = Field(default=None, ge=1)
    image_url: str | None = Field(default=None, max_length=300, pattern=r"^(/|https://)\S+$")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("brand", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def list_price_not_below_unit_price(self) -> Self:
        if self.list_price is not None and self.list_price < self.unit_price:
            raise ValueError("list_price must not be below unit_price")
        return self


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    limit: int
    offset: int
