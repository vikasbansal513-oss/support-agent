import asyncio
import fcntl
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import Crypto
from app.core.db import make_engine
from app.integrations.http import ExternalHttp
from tests.auth.helpers import Clock, MemoryMailer


async def _instant_sleep(_: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_external_retries() -> Iterator[None]:
    """ExternalHttp retries real asyncio.sleep() by default (production needs that).
    Tests that trigger retry/timeout paths otherwise burn real wall-clock seconds
    per attempt. Only the sleep default is replaced; max_attempts/timeout/status
    handling stay exactly as written, so retry *behavior* is still tested."""
    defaults = dict(ExternalHttp.__init__.__kwdefaults__)
    ExternalHttp.__init__.__kwdefaults__ = {**defaults, "sleep": _instant_sleep}
    yield
    ExternalHttp.__init__.__kwdefaults__ = defaults

ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)
OWNER_ROLE = "sa_test_owner"
APP_ROLE = "sa_test_app"
ROLE_PASSWORD = "sa_test_pw"  # noqa: S105


@dataclass(frozen=True)
class TestDb:
    __test__ = False

    name: str
    admin_url: str
    owner_url: str
    app_url: str


def _url_for(user: str, password: str, name: str) -> str:
    base = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    return f"postgresql://{user}:{password}@{base}/{name}"


async def _admin_exec(statements: list[str], database: str | None = None) -> None:
    url = ADMIN_URL if database is None else _url_for("postgres", "postgres", database)
    conn = await asyncpg.connect(url)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


async def _ensure_roles() -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        for role, options in ((OWNER_ROLE, "CREATEROLE"), (APP_ROLE, "")):
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            if not exists:
                await conn.execute(
                    f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS {options} "
                    f"PASSWORD '{ROLE_PASSWORD}'"
                )
    finally:
        await conn.close()


def provision_db(migrate: bool = True) -> TestDb:
    name = f"sa_test_{uuid.uuid4().hex[:10]}"
    lock_path = os.path.join(tempfile.gettempdir(), "sa_test_provision.lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        asyncio.run(_ensure_roles())
        asyncio.run(_admin_exec([f"CREATE DATABASE {name} OWNER {OWNER_ROLE}"]))
        db = TestDb(
            name=name,
            admin_url=_url_for("postgres", "postgres", name),
            owner_url=_url_for(OWNER_ROLE, ROLE_PASSWORD, name),
            app_url=_url_for(APP_ROLE, ROLE_PASSWORD, name),
        )
        if migrate:
            migrate_up(db)
    return db


def alembic_config(db: TestDb) -> Config:
    os.environ["MIGRATION_DATABASE_URL"] = db.owner_url
    get_settings.cache_clear()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "migrations")
    )
    return cfg


def migrate_up(db: TestDb, revision: str = "head") -> None:
    command.upgrade(alembic_config(db), revision)
    asyncio.run(_admin_exec([f"GRANT support_app_runtime TO {APP_ROLE}"], db.name))


def drop_db(db: TestDb) -> None:
    asyncio.run(
        _admin_exec(
            [
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db.name}'",
                f"DROP DATABASE IF EXISTS {db.name}",
            ]
        )
    )


@pytest.fixture(scope="session", autouse=True)
def fast_password_hasher() -> Iterator[None]:
    from argon2 import PasswordHasher

    from app.auth import passwords

    original = passwords._hasher
    passwords._hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    yield
    passwords._hasher = original


@pytest.fixture(scope="session")
def test_db() -> Iterator[TestDb]:
    db = provision_db()
    yield db
    drop_db(db)


@pytest.fixture
async def runtime_factory(test_db: TestDb) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(test_db.app_url, null_pool=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def owner_factory(test_db: TestDb) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(test_db.owner_url, null_pool=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def probe_table(
    owner_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    from app.core.tenancy import tenant_rls_statements

    name = f"probe_{uuid.uuid4().hex[:8]}"
    async with owner_factory() as session, session.begin():
        await session.execute(
            text(
                f"CREATE TABLE {name} (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                " tenant_id uuid NOT NULL DEFAULT app_current_tenant(), note text)"
            )
        )
        for statement in tenant_rls_statements(name, super_admin_read=True):
            await session.execute(text(statement))
    yield name
    async with owner_factory() as session, session.begin():
        await session.execute(text(f"DROP TABLE {name}"))


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    values = {
        "JWT_SECRET": "j" * 40,
        "ADMIN_JWT_SECRET": "a" * 40,
        "ENCRYPTION_KEY": Crypto.generate_key(),
        "FRONTEND_URL": "https://app.example.test",
        "CORS_ORIGINS": "",
        "COOKIE_SECURE": "true",
        "COOKIE_SAMESITE": "none",
        "TRUSTED_PROXY_HOPS": "1",
        "ACCESS_TOKEN_MINUTES": "15",
        "REFRESH_TOKEN_DAYS": "14",
        "LOCKOUT_MAX_ATTEMPTS": "5",
        "LOCKOUT_MINUTES": "15",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mailer() -> MemoryMailer:
    return MemoryMailer()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock(datetime.now(UTC))
    monkeypatch.setattr("app.core.timeutil.utc_now", fake)
    return fake


@pytest.fixture
async def client(
    auth_env: None,
    runtime_factory: async_sessionmaker[AsyncSession],
    mailer: MemoryMailer,
) -> AsyncIterator[httpx.AsyncClient]:
    from app.core.db import get_session_factory
    from app.core.mail import get_mailer
    from app.main import create_app
    from tests.auth.helpers import new_ip

    application = create_app()
    application.dependency_overrides[get_session_factory] = lambda: runtime_factory
    application.dependency_overrides[get_mailer] = lambda: mailer
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        headers={"X-Forwarded-For": new_ip()},
    ) as http:
        yield http
