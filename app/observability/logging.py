import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.observability.context import get_request_id

LOGGER_NAME = "fault_commerce"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
        }
        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(event_data)
        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception_type"] = exception_type.__name__ if exception_type else "Exception"
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    destination = log_file or get_settings().log_file
    destination.parent.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(destination, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def event_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(*, level: int = logging.INFO, **values: Any) -> None:
    event_logger().log(level, values.get("event", "event"), extra={"event_data": values})


def log_stage(*, stage: str, event: str, duration_ms: float = 0.0, **values: Any) -> None:
    if get_settings().observability_level != "detailed":
        return
    log_event(
        request_id=get_request_id(),
        stage=stage,
        event=event,
        duration_ms=round(duration_ms, 3),
        **values,
    )
