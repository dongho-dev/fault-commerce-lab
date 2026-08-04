import re
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.observability.context import request_id_context
from app.observability.logging import event_logger, log_event, log_stage
from app.observability.metrics import HTTP_DURATION, HTTP_REQUESTS

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def select_request_id(candidate: str | None) -> str:
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = select_request_id(request.headers.get("X-Request-ID"))
        token = request_id_context.set(request_id)
        started = time.perf_counter()
        log_stage(
            stage="request_received",
            event="received",
            method=request.method,
            path=request.url.path,
        )
        try:
            try:
                response = await call_next(request)
            except Exception:
                event_logger().exception(
                    "unhandled_server_error",
                    extra={
                        "event_data": {
                            "event": "unhandled_server_error",
                            "request_id": request_id,
                            "method": request.method,
                            "path": request.url.path,
                        }
                    },
                )
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "서버 내부 오류가 발생했습니다.",
                        "request_id": request_id,
                    },
                )

            duration_seconds = time.perf_counter() - started
            duration_ms = duration_seconds * 1_000
            route = request.scope.get("route")
            normalized_path = getattr(route, "path", "__unmatched__")
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id

            HTTP_REQUESTS.labels(request.method, normalized_path, str(status_code)).inc()
            HTTP_DURATION.labels(request.method, normalized_path).observe(duration_seconds)
            log_stage(
                stage="response_sent",
                event="completed",
                duration_ms=duration_ms,
                status_code=status_code,
            )
            log_event(
                request_id=request_id,
                method=request.method,
                path=normalized_path,
                status_code=status_code,
                duration_ms=round(duration_ms, 3),
                event="http_request_completed",
            )
            return response
        finally:
            request_id_context.reset(token)
