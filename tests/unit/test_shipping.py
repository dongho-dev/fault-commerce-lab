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
