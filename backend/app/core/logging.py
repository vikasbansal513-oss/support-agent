import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.masking import redact_text

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|credential|otp|"
    r"phone|mobile|email|address|upi|card|private[_-]?key|signature",
    re.IGNORECASE,
)
_CUSTOMER_TEXT_KEY = re.compile(
    r"^(text|body|content|body_text|raw_payload|payload|message_text|summary)$", re.IGNORECASE
)
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


_WEBHOOK_PATH = re.compile(r"(/webhooks/whatsapp/)[^\s\"'?#]+")


def scrub_paths(value: str) -> str:
    return _WEBHOOK_PATH.sub(r"\1[REDACTED]", value)


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def is_customer_text_key(key: str) -> bool:
    return bool(_CUSTOMER_TEXT_KEY.match(key))


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if is_sensitive_key(str(k)) or is_customer_text_key(str(k))
            else _clean(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return scrub_paths(redact_text(value))
    if isinstance(value, int | float | bool) or value is None:
        return value
    return scrub_paths(redact_text(str(value)))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub_paths(redact_text(record.getMessage())),
            "request_id": request_id_var.get(),
            "run_id": run_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = (
                "[REDACTED]"
                if is_sensitive_key(key) or is_customer_text_key(key)
                else _clean(value)
            )
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            payload["exc_type"] = type(exc).__name__
            payload["exc_message"] = scrub_paths(redact_text(str(exc)))[:300]
            payload["exc_frames"] = [
                f"{f.filename}:{f.lineno}:{f.name}"
                for f in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
