import pytest

from oracle.check import order_amount_holds, stock_equation_holds
from oracle.reset import parse_stocks
from scripts.concurrency_support import percentile


def test_oracle_stock_equation() -> None:
    assert stock_equation_holds(initial=10, confirmed_quantity=7, current=3)
    assert not stock_equation_holds(initial=10, confirmed_quantity=8, current=3)


def test_oracle_order_amount_equation() -> None:
    assert order_amount_holds(
        unit_price=10_000, quantity=2, shipping_fee=3_000, total_amount=23_000
    )
    assert not order_amount_holds(
        unit_price=10_000, quantity=2, shipping_fee=3_000, total_amount=22_999
    )


def test_parse_stocks() -> None:
    assert parse_stocks("13,18") == [13, 18]
    assert parse_stocks("") == []
    with pytest.raises(ValueError):
        parse_stocks("10,-1")


def test_percentile_interpolates() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 25.0
    assert percentile(values, 95) == pytest.approx(38.5)
    assert percentile([], 50) == 0.0
    with pytest.raises(ValueError):
        percentile(values, 101)
