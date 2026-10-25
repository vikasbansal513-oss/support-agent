from app.core.masking import mask_email, mask_mobile, redact_for_llm, redact_text, secret_hint


def test_phone_variants_redacted() -> None:
    for raw in ("+919811100001", "9811100001", "+91 98111 00001", "98111-00001"):
        assert "[PHONE]" in redact_text(f"call {raw} now")
        assert "9811100001" not in redact_text(raw).replace(" ", "")


def test_email_and_upi_redacted() -> None:
    out = redact_text("mail a.b@shop.in or pay ravi@okaxis")
    assert "[EMAIL]" in out and "[UPI]" in out
    assert "shop.in" not in out and "okaxis" not in out


def test_card_redacted() -> None:
    assert redact_text("card 4111 1111 1111 1111 ok") == "card [CARD] ok"
    assert redact_text("4111-1111-1111-1111") == "[CARD]"


def test_long_digits_redacted_order_numbers_kept() -> None:
    assert redact_for_llm("order #1001 and SHOE-4521 ref 1234567890123") == (
        "order #1001 and SHOE-4521 ref [CARD]"
    )
    assert "[NUMBER]" in redact_for_llm("account 123456789")
    assert redact_for_llm("order #1001") == "order #1001"


def test_redact_for_llm_truncates() -> None:
    assert len(redact_for_llm("a" * 5000, max_chars=100)) == 100


def test_mask_helpers() -> None:
    assert mask_mobile("+919811100001") == "*********0001"
    assert mask_mobile("123") == "***"
    assert mask_email("ravi@shop.in") == "r***@shop.in"
    assert mask_email("nomail") == "******"
    assert secret_hint("shpat_abcdef123456") == "****3456"
    assert secret_hint("short") == "****"
