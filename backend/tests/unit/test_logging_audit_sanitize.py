import json
import logging

from app.core.audit import sanitize
from app.core.logging import JsonFormatter, is_sensitive_key, request_id_var


def _format(**extra: object) -> dict[str, object]:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello 9811100001", (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_sensitive_keys() -> None:
    for key in (
        "password",
        "access_token",
        "Authorization",
        "phone_e164",
        "email_lower",
        "secret_enc",
    ):
        assert is_sensitive_key(key)
    for key in ("tenant_id", "status", "latency_ms"):
        assert not is_sensitive_key(key)


def test_log_redacts_message_and_extras() -> None:
    token = request_id_var.set("req-12345678")
    try:
        out = _format(
            phone="+919811100001",
            body_text="order kahan hai 9811100001",
            tenant_id="t1",
            note="mail me a@b.in",
        )
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "hello [PHONE]"
    assert out["phone"] == "[REDACTED]"
    assert out["body_text"] == "[REDACTED]"
    assert out["tenant_id"] == "t1"
    assert out["note"] == "mail me [EMAIL]"
    assert out["request_id"] == "req-12345678"


def test_exception_logged_without_raw_values() -> None:
    try:
        raise ValueError("params: 9811100001 a@b.in")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(record))
    assert out["exc_type"] == "ValueError"
    assert "9811100001" not in out["exc_message"] and "a@b.in" not in out["exc_message"]
    assert out["exc_frames"]


def test_audit_sanitize_nested() -> None:
    cleaned = sanitize(
        {"password": "x", "cfg": {"api_key": "k", "shop": "demo"}, "list": ["a@b.in", 5], "n": None}
    )
    assert cleaned == {
        "password": "[REDACTED]",
        "cfg": {"api_key": "[REDACTED]", "shop": "demo"},
        "list": ["[EMAIL]", 5],
        "n": None,
    }
