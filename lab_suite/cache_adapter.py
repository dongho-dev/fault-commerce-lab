import os
from collections import OrderedDict
from functools import wraps
from threading import RLock
from time import monotonic


class SnapshotCache:
    def __init__(self) -> None:
        self.ttl = float(os.environ.get("LAB06_CACHE_TTL_SECONDS", "120"))
        self.max_entries = int(os.environ.get("LAB06_CACHE_MAX_ENTRIES", "256"))
        if self.ttl <= 0 or self.max_entries < 1:
            raise ValueError("cache TTL and capacity must be positive")
        self.entries: OrderedDict[int, tuple[float, object]] = OrderedDict()
        self.entries_lock = RLock()
        self.product_locks = tuple(RLock() for _ in range(64))

    def product_lock(self, product_id: int):
        return self.product_locks[product_id % len(self.product_locks)]

    def get(self, product_id: int):
        with self.entries_lock:
            entry = self.entries.get(product_id)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= monotonic():
                del self.entries[product_id]
                return None
            self.entries.move_to_end(product_id)
            return value

    def put(self, product_id: int, value: object) -> None:
        with self.entries_lock:
            self.entries[product_id] = (monotonic() + self.ttl, value)
            self.entries.move_to_end(product_id)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)

    def invalidate(self, product_id: int) -> None:
        with self.entries_lock:
            self.entries.pop(product_id, None)


CACHE = SnapshotCache()


def cached_snapshot(function):
    @wraps(function)
    def wrapped(service, product_id: int):
        with CACHE.product_lock(product_id):
            value = CACHE.get(product_id)
            if value is None:
                value = function(service, product_id)
                CACHE.put(product_id, value)
            return value

    return wrapped


def invalidate_after_order(function):
    @wraps(function)
    def wrapped(service, *, product_id: int, quantity: int, postal_code: str):
        with CACHE.product_lock(product_id):
            result = function(
                service, product_id=product_id, quantity=quantity, postal_code=postal_code
            )
            CACHE.invalidate(product_id)
            return result

    return wrapped
