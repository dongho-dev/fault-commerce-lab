import pytest
from pydantic import ValidationError

from app.schemas.order import OrderCreate
from app.schemas.product import ProductCreate


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_product_name_rejects_blank_values(name: str) -> None:
    with pytest.raises(ValidationError):
        ProductCreate(name=name, unit_price=1, initial_stock=0)


def test_product_name_is_trimmed() -> None:
    value = ProductCreate(name="  Keyboard  ", unit_price=1, initial_stock=0)
    assert value.name == "Keyboard"


@pytest.mark.parametrize(
    ("field", "value"),
    [("unit_price", 0), ("initial_stock", -1)],
)
def test_product_numeric_constraints(field: str, value: int) -> None:
    payload = {"name": "Keyboard", "unit_price": 1, "initial_stock": 0}
    payload[field] = value
    with pytest.raises(ValidationError):
        ProductCreate(**payload)


@pytest.mark.parametrize("quantity", [0, -1])
def test_order_quantity_must_be_positive(quantity: int) -> None:
    with pytest.raises(ValidationError):
        OrderCreate(product_id=1, quantity=quantity, postal_code="16841")


@pytest.mark.parametrize("postal_code", ["", "   ", "#invalid"])
def test_postal_code_validation(postal_code: str) -> None:
    with pytest.raises(ValidationError):
        OrderCreate(product_id=1, quantity=1, postal_code=postal_code)
