import threading
import time
from dataclasses import dataclass, field


class CarrierZoneClient:
    """Resolves the carrier delivery zone for a postal code.

    Remote-area surcharges follow the carrier's own zone table, so every quote asks the
    carrier zone service instead of keeping a local copy of the rule. The carrier sandbox
    is emulated in-process with the same round-trip time and client settings as the
    production integration.
    """

    REMOTE_ZONE_PREFIX = 60

    def __init__(self, *, round_trip_ms: float = 100.0, max_connections: int = 2) -> None:
        self._round_trip_seconds = round_trip_ms / 1_000
        self._connections = threading.BoundedSemaphore(max_connections)

    def is_remote_area(self, postal_code: str) -> bool:
        with self._connections:
            time.sleep(self._round_trip_seconds)
            digits = "".join(character for character in postal_code if character.isdigit())
            return bool(digits) and int(digits[:2]) >= self.REMOTE_ZONE_PREFIX


_carrier_zones = CarrierZoneClient()


def default_carrier_zones() -> CarrierZoneClient:
    return _carrier_zones


@dataclass(frozen=True)
class ShippingQuoteService:
    base_fee: int = 3_000
    remote_area_fee: int = 2_500
    extra_packaging_fee: int = 700
    free_shipping_threshold: int = 200_000
    zones: CarrierZoneClient = field(default_factory=default_carrier_zones)

    def quote(self, *, postal_code: str, quantity: int, merchandise_amount: int) -> int:
        fee = 0 if merchandise_amount >= self.free_shipping_threshold else self.base_fee
        if self.zones.is_remote_area(postal_code):
            fee += self.remote_area_fee
        if quantity > 2:
            fee += (quantity - 2) * self.extra_packaging_fee
        return max(fee, 0)


def calculate_total_amount(*, unit_price: int, quantity: int, shipping_fee: int) -> int:
    return unit_price * quantity + shipping_fee
