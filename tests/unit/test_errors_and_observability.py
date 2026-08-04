import json

from app.main import error_response
from app.observability.context import request_id_context
from app.observability.logging import JsonFormatter
from app.observability.middleware import select_request_id
from app.services.errors import InsufficientStockError, ProductNotFoundError


def test_business_error_contracts() -> None:
    missing = ProductNotFoundError()
    insufficient = InsufficientStockError()
    assert (missing.status_code, missing.code) == (404, "PRODUCT_NOT_FOUND")
    assert (insufficient.status_code, insufficient.code) == (409, "INSUFFICIENT_STOCK")


def test_error_response_uses_request_context() -> None:
    token = request_id_context.set("unit-request-id")
    try:
        response = error_response(code="EXAMPLE", message="example", status_code=409)
    finally:
        request_id_context.reset(token)
    assert response.status_code == 409
    assert json.loads(response.body) == {
        "code": "EXAMPLE",
        "message": "example",
        "request_id": "unit-request-id",
    }


def test_request_id_accepts_safe_values_and_replaces_invalid_values() -> None:
    assert select_request_id("client.request-123") == "client.request-123"
    generated = select_request_id("bad request id")
    assert generated != "bad request id"
    assert len(generated) == 32


def test_json_formatter_emits_valid_json() -> None:
    import logging

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "event", (), None)
    record.event_data = {"request_id": "request-1", "status_code": 200}
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["request_id"] == "request-1"
    assert parsed["status_code"] == 200
