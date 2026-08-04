from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderCreate(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1)
    postal_code: str = Field(min_length=1, max_length=20)

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 20:
            raise ValueError("postal_code must contain 1 to 20 characters")
        if not normalized[0].isalnum() or any(
            not (character.isalnum() or character in {"-", " "}) for character in normalized
        ):
            raise ValueError("postal_code contains unsupported characters")
        return normalized


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    unit_price: int
    postal_code: str
    shipping_fee: int
    total_amount: int
    status: str
    created_at: datetime
