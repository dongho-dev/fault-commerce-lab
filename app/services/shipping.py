from dataclasses import dataclass


@dataclass(frozen=True)
class ShippingQuoteService:
    base_fee: int = 3_000
    remote_area_fee: int = 2_500
    extra_packaging_fee: int = 700
    free_shipping_threshold: int = 200_000

    def quote(self, *, postal_code: str, quantity: int, merchandise_amount: int) -> int:
        fee = 0 if merchandise_amount >= self.free_shipping_threshold else self.base_fee
        digits = "".join(character for character in postal_code if character.isdigit())
        digits = str(int(digits)) if digits else digits
        if digits and int(digits[:2]) >= 60:
            fee += self.remote_area_fee
        if quantity > 2:
            fee += (quantity - 2) * self.extra_packaging_fee
        return max(fee, 0)


def calculate_total_amount(*, unit_price: int, quantity: int, shipping_fee: int) -> int:
    return unit_price * quantity + shipping_fee
