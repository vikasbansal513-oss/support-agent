import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_UPI = re.compile(r"(?<![\w.@-])[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}(?![\w.-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_LONG_DIGITS = re.compile(r"\+?\d{9,}")


def redact_text(text: str) -> str:
    out = _EMAIL.sub("[EMAIL]", text)
    out = _UPI.sub("[UPI]", out)
    out = _CARD.sub("[CARD]", out)
    out = _PHONE.sub("[PHONE]", out)
    return _LONG_DIGITS.sub("[NUMBER]", out)


def redact_for_llm(text: str, max_chars: int = 2000) -> str:
    return redact_text(text)[:max_chars]


def mask_mobile(value: str) -> str:
    digits = value.strip()
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def mask_email(value: str) -> str:
    local, sep, domain = value.partition("@")
    if not sep or not local:
        return "*" * len(value)
    return f"{local[0]}***@{domain}"


def secret_hint(secret: str) -> str:
    if len(secret) < 8:
        return "****"
    return f"****{secret[-4:]}"
