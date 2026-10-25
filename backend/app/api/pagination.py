import base64

from app.core.errors import AppError


def encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("E-VAL-001", detail="bad cursor") from exc
    if value < 0:
        raise AppError("E-VAL-001", detail="bad cursor")
    return value
