import pytest

from app.services.shipping import ShippingQuoteService, calculate_total_amount


def test_shipping_quote_is_deterministic() -> None:
    service = ShippingQuoteService()
    first = service.quote(postal_code="16841", quantity=1, merchandise_amount=50_000)
    second = service.quote(postal_code="16841", quantity=1, merchandise_amount=50_000)
    assert first == second == 3_000


def test_shipping_quote_adds_remote_and_packaging_fees() -> None:
    service = ShippingQuoteService()
    assert service.quote(postal_code="63000", quantity=4, merchandise_amount=50_000) == 6_900


def test_free_shipping_threshold_does_not_remove_surcharges() -> None:
    service = ShippingQuoteService()
    assert service.quote(postal_code="63000", quantity=3, merchandise_amount=200_000) == 3_200


def test_total_amount_uses_integer_won() -> None:
    assert calculate_total_amount(unit_price=129_000, quantity=2, shipping_fee=3_000) == 261_000


@pytest.mark.parametrize("postal_code", ["06236", "00623", "00062", "00000", "05999"])
def test_postal_prefix_preserves_leading_zeroes(postal_code: str) -> None:
    service = ShippingQuoteService()
    assert service.quote(postal_code=postal_code, quantity=1, merchandise_amount=50_000) == 3_000


@pytest.mark.parametrize(
    ("postal_code", "quantity", "merchandise_amount", "expected_fee"),
    [
        ("59999", 2, 199_999, 3_000),
        ("60000", 2, 199_999, 5_500),
        ("99999", 2, 199_999, 5_500),
        ("06236", 2, 200_000, 0),
        ("06236", 3, 200_000, 700),
        ("60000", 2, 200_000, 2_500),
        ("60000", 3, 200_000, 3_200),
    ],
)
def test_shipping_policy_boundaries(
    postal_code: str, quantity: int, merchandise_amount: int, expected_fee: int
) -> None:
    assert (
        ShippingQuoteService().quote(
            postal_code=postal_code, quantity=quantity, merchandise_amount=merchandise_amount
        )
        == expected_fee
    )
