from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.errors import AppError

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")

MIN_LENGTH = 10
MAX_LENGTH = 128


def validate_new_password(password: str) -> None:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise AppError("E-AUTH-011")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    target = stored_hash or _DUMMY_HASH
    try:
        ok = _hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)
