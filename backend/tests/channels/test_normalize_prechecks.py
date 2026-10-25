import base64
from datetime import UTC, datetime
from typing import Any

from app.channels import normalize, prechecks
from app.contracts.contracts import NormalizedInbound


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(**overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": "m-1",
        "threadId": "t-1",
        "internalDate": "1767000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Asha Rao <Asha@Example.com>"},
                {"name": "Subject", "value": "Order #1001 kahan hai"},
                {"name": "Message-ID", "value": "<abc@mail>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64("Mera order kahan hai?\n\nOn Mon, X wrote:\n> old")},
                },
                {"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}},
            ],
        },
    }
    message.update(overrides)
    return message


def inbound(**kw: Any) -> NormalizedInbound:
    data: dict[str, Any] = {
        "channel": "email",
        "external_id": "m1",
        "from_email": "customer@example.com",
        "text": "hi",
        "received_at": datetime.now(UTC),
    }
    data.update(kw)
    return NormalizedInbound(**data)


def test_gmail_normalize_full_body_and_headers() -> None:
    result = normalize.from_gmail(gmail_message())
    assert result is not None
    assert result.from_email == "asha@example.com" and result.email_thread_id == "t-1"
    assert result.text == "Mera order kahan hai?"
    assert result.email_subject == "Order #1001 kahan hai"
    assert result.email_headers["message-id"] == "<abc@mail>"
    assert result.received_at.year >= 2025


def test_gmail_html_fallback_and_invalid() -> None:
    msg = gmail_message()
    msg["payload"]["parts"] = [
        {"mimeType": "text/html", "body": {"data": b64("<p>Hello <b>there</b></p>")}}
    ]
    result = normalize.from_gmail(msg)
    assert result is not None and result.text == "Hello there"
    assert normalize.from_gmail({"id": "x", "payload": {"headers": []}}) is None
    assert normalize.from_gmail(gmail_message(id="")) is None


def test_strip_quoted() -> None:
    assert normalize.strip_quoted("a\n> q\nb") == "a\nb"
    assert normalize.strip_quoted("hello\n-----Original Message-----\nold") == "hello"


def test_gupshup_normalize() -> None:
    body = {
        "type": "message",
        "app": "A",
        "payload": {"id": "1", "source": "9876543210", "type": "text", "payload": {"text": "hi"}},
    }
    result = normalize.from_gupshup(body)
    assert (
        result is not None and result.from_phone == "+919876543210" and result.channel == "whatsapp"
    )
    assert normalize.from_gupshup({"type": "message-event"}) is None


def test_email_skip_reasons() -> None:
    cases: dict[str, dict[str, Any]] = {
        "AUTO_SUBMITTED": {"email_headers": {"Auto-Submitted": "auto-replied"}},
        "AUTO_REPLY_HEADER": {"email_headers": {"X-Autoreply": "yes"}},
        "BULK_PRECEDENCE": {"email_headers": {"Precedence": "bulk"}},
        "LIST_MAIL": {"email_headers": {"List-Unsubscribe": "<mailto:x>"}},
        "NO_REPLY_SENDER": {"from_email": "no-reply@shop.example.com"},
        "BOUNCE_SUBJECT": {"email_subject": "Delivery Status Notification (Failure)"},
        "OUT_OF_OFFICE": {"email_subject": "Automatic reply: away"},
        "OWN_ADDRESS": {"from_email": "Support@Brand.com"},
    }
    for reason, kw in cases.items():
        assert prechecks.email_skip_reason(inbound(**kw), "support@brand.com") == reason, reason
    assert (
        prechecks.email_skip_reason(inbound(email_headers={"Auto-Submitted": "no"}), None) is None
    )
    assert prechecks.email_skip_reason(inbound(), "support@brand.com") is None
    assert (
        prechecks.email_skip_reason(inbound(from_email="mailer-daemon@x.com"), None)
        == "NO_REPLY_SENDER"
    )
