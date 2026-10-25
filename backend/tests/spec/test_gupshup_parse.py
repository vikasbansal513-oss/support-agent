from app.channels import gupshup_parse as G
from app.contracts.contracts import NormalizedInbound


def env(ptype, inner, **kw):
    return {
        "app": "DemoApp",
        "timestamp": 1580227766370,
        "version": 2,
        "type": "message",
        "payload": {
            "id": "ABEG1",
            "source": "918898912345",
            "type": ptype,
            "payload": inner,
            "sender": {"phone": "918898912345", "name": "Drew"},
            **kw,
        },
    }


def test_text():
    r = G.parse(env("text", {"text": "mera order #1001 kahan hai"}))
    n = NormalizedInbound.model_validate({k: v for k, v in r.items() if not k.startswith("_")})
    assert n.from_phone == "+918898912345" and n.text.endswith("kahan hai") and n.media_type is None


def test_image_is_media():
    r = G.parse(env("image", {"url": "https://x/y.jpg", "caption": "see"}))
    assert r["media_type"] == "image" and r["text"] == "see"


def test_status_events_ignored():
    assert G.parse({"app": "A", "type": "message-event", "payload": {}}) is None
    assert G.parse({"type": "user-event"}) is None


def test_bad_inputs():
    assert G.parse(None) is None and G.parse({"type": "message", "payload": "x"}) is None
    assert G.parse(env("text", {"text": "x"}, id=None, source="12")) is None


def test_unknown_type_goes_to_human_as_media():
    assert G.parse(env("order", {}))["media_type"] == "other"


def test_ten_digit_defaults_india():
    assert G.phone_e164("9876543210") == "+919876543210" and G.phone_e164("abc") is None


def test_token_hash():
    t, h = G.new_webhook_token()
    assert (
        len(t) >= 43
        and G.tokens_equal(h, G.hash_token(t))
        and not G.tokens_equal(h, G.hash_token(t + "x"))
    )
