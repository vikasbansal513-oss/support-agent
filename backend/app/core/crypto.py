from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoError(Exception):
    pass


class Crypto:
    def __init__(self, raw_keys: str | list[str]) -> None:
        keys = raw_keys.split(",") if isinstance(raw_keys, str) else raw_keys
        cleaned = [k.strip() for k in keys if k.strip()]
        if not cleaned:
            raise CryptoError("no encryption key configured")
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in cleaned])
        except (ValueError, TypeError) as exc:
            raise CryptoError("invalid encryption key") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError, TypeError) as exc:
            raise CryptoError("decrypt failed") from exc

    def rotate(self, token: str) -> str:
        try:
            return self._fernet.rotate(token.encode()).decode()
        except (InvalidToken, ValueError, TypeError) as exc:
            raise CryptoError("rotate failed") from exc
