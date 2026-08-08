import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.config import get_settings
from app.observability.context import get_request_id
from app.observability.logging import configure_logging, event_logger
from app.observability.middleware import RequestObservabilityMiddleware
from app.services.errors import BusinessError

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


def error_response(*, code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "request_id": get_request_id()},
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_file)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        limiter = to_thread.current_default_thread_limiter()
        original_tokens = limiter.total_tokens
        limiter.total_tokens = settings.database_pool_size + settings.database_max_overflow
        try:
            yield
        finally:
            limiter.total_tokens = original_tokens

    application = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(RequestObservabilityMiddleware)
    application.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @application.get("/", include_in_schema=False, response_class=FileResponse)
    async def storefront() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

    @application.exception_handler(BusinessError)
    async def handle_business_error(_request: Request, exc: BusinessError) -> JSONResponse:
        return error_response(code=exc.code, message=exc.message, status_code=exc.status_code)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            code="VALIDATION_ERROR",
            message="요청 값이 유효하지 않습니다.",
            status_code=422,
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return error_response(code=code, message=str(exc.detail), status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        event_logger().log(
            logging.ERROR,
            "unhandled_server_error",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "event_data": {
                    "event": "unhandled_server_error",
                    "request_id": get_request_id(),
                }
            },
        )
        return error_response(
            code="INTERNAL_SERVER_ERROR",
            message="서버 내부 오류가 발생했습니다.",
            status_code=500,
        )

    application.include_router(router)
    return application


app = create_app()
