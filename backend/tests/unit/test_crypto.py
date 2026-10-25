import pytest

from app.core.crypto import Crypto, CryptoError


def test_roundtrip_and_ciphertext_differs() -> None:
    crypto = Crypto(Crypto.generate_key())
    token = crypto.encrypt('{"k":"v"}')
    assert token != '{"k":"v"}'
    assert crypto.decrypt(token) == '{"k":"v"}'


def test_rotation_without_downtime() -> None:
    old, new = Crypto.generate_key(), Crypto.generate_key()
    token = Crypto(old).encrypt("secret")
    both = Crypto(f"{new},{old}")
    assert both.decrypt(token) == "secret"
    rotated = both.rotate(token)
    assert Crypto(new).decrypt(rotated) == "secret"
    with pytest.raises(CryptoError):
        Crypto(old).decrypt(rotated)


def test_decrypt_failure_raises_never_returns_value() -> None:
    crypto = Crypto(Crypto.generate_key())
    for bad in ("garbage", "", crypto.encrypt("x")[:-4] + "AAAA"):
        with pytest.raises(CryptoError):
            crypto.decrypt(bad)
    with pytest.raises(CryptoError):
        Crypto(Crypto.generate_key()).decrypt(crypto.encrypt("x"))


def test_bad_keys_rejected_without_echo() -> None:
    for raw in ("", " , ", "not-a-key"):
        with pytest.raises(CryptoError) as info:
            Crypto(raw)
        assert "not-a-key" not in str(info.value)
