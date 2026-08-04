from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "commerce_http_requests_total",
    "HTTP requests handled by the application.",
    ("method", "path", "status_code"),
)
HTTP_DURATION = Histogram(
    "commerce_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "path"),
)
ORDER_ATTEMPTS = Counter("commerce_order_attempts_total", "Order creation attempts.")
ORDERS_CONFIRMED = Counter(
    "commerce_orders_confirmed_total", "Orders committed with CONFIRMED status."
)
INSUFFICIENT_STOCK_REJECTIONS = Counter(
    "commerce_insufficient_stock_rejections_total",
    "Order attempts rejected because inventory was insufficient.",
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
