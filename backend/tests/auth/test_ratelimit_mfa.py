import time
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import mfa, ratelimit
from app.core.db import session_scope
from app.core.errors import AppError
from tests.auth.helpers import Clock, new_email

Factory = async_sessionmaker[AsyncSession]


async def test_rate_limit_blocks_after_limit_and_buckets_are_separate(
    auth_env: None, runtime_factory: Factory
) -> None:
    subject = new_email()
    limit = ratelimit.LIMITS["forgot_email"][0]
    for _ in range(limit):
        await ratelimit.hit(runtime_factory, "forgot_email", subject)
    with pytest.raises(AppError) as info:
        await ratelimit.hit(runtime_factory, "forgot_email", subject)
    assert info.value.code == "E-AUTH-006"
    await ratelimit.hit(runtime_factory, "forgot_email", new_email())
    await ratelimit.hit(runtime_factory, "resend_email", subject)


async def test_rate_limit_window_resets(
    auth_env: None, runtime_factory: Factory, clock: Clock
) -> None:
    subject = new_email()
    clock.now = datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC)
    for _ in range(ratelimit.LIMITS["forgot_email"][0]):
        await ratelimit.hit(runtime_factory, "forgot_email", subject)
    with pytest.raises(AppError):
        await ratelimit.hit(runtime_factory, "forgot_email", subject)
    clock.now += timedelta(hours=1)
    await ratelimit.hit(runtime_factory, "forgot_email", subject)


async def test_rate_limit_stores_only_hashed_bucket(
    auth_env: None, runtime_factory: Factory
) -> None:
    subject = new_email()
    await ratelimit.hit(runtime_factory, "login_email", subject)
    async with session_scope(runtime_factory, super_admin=True) as session:
        buckets = (await session.execute(text("SELECT bucket FROM rate_limits"))).scalars().all()
    assert ratelimit.bucket_name("login_email", subject) in buckets
    assert all(subject not in b for b in buckets)


async def _new_user(factory: Factory) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'Mfa User')"),
            {"i": user_id, "e": new_email()},
        )
    return user_id


async def _enable(factory: Factory, user_id: uuid.UUID) -> tuple[str, list[str]]:
    async with session_scope(factory, super_admin=True) as session:
        secret, uri = await mfa.begin_setup(session, user_id, "u@shop.test")
        assert uri.startswith("otpauth://totp/") and "Support%20Agent" in uri
    async with session_scope(factory, super_admin=True) as session:
        codes = await mfa.confirm_setup(session, user_id, pyotp.TOTP(secret).now())
    return secret, codes


async def test_setup_confirm_and_secret_encrypted_at_rest(
    auth_env: None, runtime_factory: Factory
) -> None:
    user_id = await _new_user(runtime_factory)
    secret, codes = await _enable(runtime_factory, user_id)
    assert len(codes) == 10 and len(set(codes)) == 10
    async with session_scope(runtime_factory, super_admin=True) as session:
        row = (
            await session.execute(
                text("SELECT secret_enc, recovery_hashes FROM user_mfa WHERE user_id = :u"),
                {"u": user_id},
            )
        ).one()
        state = await mfa.get_state(session, user_id)
    assert secret not in row[0] and mfa.get_crypto().decrypt(row[0]) == secret
    assert all(code not in row[1] for code in codes) and len(row[1]) == 10
    assert state.confirmed and state.recovery_remaining == 10


async def test_confirm_requires_correct_code_and_blocks_second_setup(
    auth_env: None, runtime_factory: Factory
) -> None:
    user_id = await _new_user(runtime_factory)
    async with session_scope(runtime_factory, super_admin=True) as session:
        secret, _ = await mfa.begin_setup(session, user_id, "u@shop.test")
    for bad in ("000000", "abcdef", "12345"):
        with pytest.raises(AppError) as info:
            async with session_scope(runtime_factory, super_admin=True) as session:
                await mfa.confirm_setup(session, user_id, bad)
        assert info.value.code == "E-AUTH-005"
    async with session_scope(runtime_factory, super_admin=True) as session:
        await mfa.confirm_setup(session, user_id, pyotp.TOTP(secret).now())
    with pytest.raises(AppError) as info:
        async with session_scope(runtime_factory, super_admin=True) as session:
            await mfa.begin_setup(session, user_id, "u@shop.test")
    assert info.value.code == "E-CONF-001"


async def test_totp_replay_rejected_and_old_step_rejected(
    auth_env: None, runtime_factory: Factory
) -> None:
    user_id = await _new_user(runtime_factory)
    secret, _ = await _enable(runtime_factory, user_id)
    totp = pyotp.TOTP(secret)
    code = totp.at(int(time.time()) + 30)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert await mfa.verify_login_code(session, user_id, code)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, code)
    old = totp.at(int(time.time()) - 30)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, old)
    far = totp.at(int(time.time()) + 300)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, far)


async def test_recovery_code_is_single_use(auth_env: None, runtime_factory: Factory) -> None:
    user_id = await _new_user(runtime_factory)
    _, codes = await _enable(runtime_factory, user_id)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert await mfa.verify_login_code(session, user_id, codes[0].upper())
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, codes[0])
        assert not await mfa.verify_login_code(session, user_id, "zzzzz-zzzzz")
        assert (await mfa.get_state(session, user_id)).recovery_remaining == 9


async def test_regenerate_recovery_codes_needs_valid_totp(
    auth_env: None, runtime_factory: Factory
) -> None:
    user_id = await _new_user(runtime_factory)
    secret, old_codes = await _enable(runtime_factory, user_id)
    with pytest.raises(AppError):
        async with session_scope(runtime_factory, super_admin=True) as session:
            await mfa.regenerate_recovery_codes(session, user_id, "000000")
    code = pyotp.TOTP(secret).at(int(time.time()) + 30)
    async with session_scope(runtime_factory, super_admin=True) as session:
        new_codes = await mfa.regenerate_recovery_codes(session, user_id, code)
    assert set(new_codes).isdisjoint(old_codes)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, old_codes[1])
        assert await mfa.verify_login_code(session, user_id, new_codes[1])


async def test_verify_without_mfa_is_false(auth_env: None, runtime_factory: Factory) -> None:
    user_id = await _new_user(runtime_factory)
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert not await mfa.verify_login_code(session, user_id, "123456")
