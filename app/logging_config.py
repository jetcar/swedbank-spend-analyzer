"""One JSON object per Python log event, including multiline exceptions."""

from datetime import datetime, timezone
import json
import logging


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = "app"):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": self.service,
            "logger": record.name,
            "process_id": record.process,
            "message": record.getMessage(),
        }
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            event["stack"] = record.stack_info
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def logging_config(service: str) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter, "service": service}},
        "handlers": {"json": {
            "class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout",
        }},
        "root": {"handlers": ["json"], "level": "INFO"},
        "loggers": {
            name: {"handlers": [], "propagate": True, "level": "INFO"}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }
