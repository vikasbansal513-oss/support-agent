import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import tenancy
from app.core.config import get_settings

logger = logging.getLogger("app.core.db")

GLOBAL_TABLE_ALLOWLIST = (
    "users",
    "platform_admins",
    "email_tokens",
    "refresh_tokens",
    "rate_limits",
    "user_mfa",
    "tenants",
    "llm_model_prices",
    "heartbeats",
    "platform_flags",
    "platform_llm_settings",
    "billing_settings",
    "billing_llm_rates",
    "billing_periods",
    "invoice_counters",
    "billing_speech_rates",
    "billing_cost_categories",
    "gcp_billing_import_rows",
    "gcp_reconciliation_runs",
    "gcp_unallocated_costs",
    "monitoring_thresholds",
)

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def make_engine(
    url: str,
    disable_statement_cache: bool = True,
    null_pool: bool = False,
    pool_size: int = 5,
    pool_recycle: int = 300,
) -> AsyncEngine:
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+asyncpg")
    connect_args: dict[str, int] = {}
    if disable_statement_cache:
        parsed = parsed.update_query_dict({"prepared_statement_cache_size": "0"})
        connect_args["statement_cache_size"] = 0
    if null_pool:
        return create_async_engine(parsed, poolclass=NullPool, connect_args=connect_args)
    return create_async_engine(
        parsed,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        # Neon's compute auto-suspends on idle; pre_ping catches a dead connection at
        # checkout, but a pooled connection that survives long enough to be reused across
        # a Neon suspend/resume cycle is recycled proactively here too, same intent, two
        # layers (checkout-time ping + age-based recycle).
        pool_recycle=pool_recycle,
        connect_args=connect_args,
    )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        settings = get_settings()
        _engine = make_engine(
            settings.database_url,
            disable_statement_cache=settings.db_disable_statement_cache,
            pool_size=settings.db_pool_size,
            pool_recycle=settings.db_pool_recycle_seconds,
        )
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _factory


def _is_disconnect_error(exc: Exception) -> bool:
    """True only for a connection-level failure (dropped/reset/closed mid-operation) -
    never for an ordinary query error (bad SQL, constraint violation, etc), which must
    propagate normally and never be retried."""
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    message = str(exc).lower()
    return any(
        s in message
        for s in (
            "connection was closed",
            "connection reset",
            "connection refused",
            "connection closed",
            "server closed the connection",
            "could not connect",
            "errno 104",
        )
    )


async def with_connection_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.2,
) -> T:
    """Retry `fn` up to `attempts` times when it fails with a transient DB connection
    error (e.g. Neon compute suspend/resume dropping a pooled connection mid-operation).

    Only safe to use around a callable whose ENTIRE body is one atomic, idempotent
    database operation (a single guarded UPDATE/SELECT ... RETURNING that either claims
    something or finds nothing left to do) - a clean retry on a fresh connection then
    either succeeds or is a no-op. Never wrap a sequence of dependent writes with this:
    if a connection drops mid-sequence, the transaction has already rolled back and the
    outcome of a blind retry of just part of it would be uncertain.

    A non-connection error (bad SQL, constraint violation, business logic exception) is
    never retried - it is re-raised immediately on the first attempt.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except (OperationalError, DBAPIError) as exc:
            if not _is_disconnect_error(exc) or attempt == attempts:
                raise
            logger.warning(
                "transient db connection error, retrying",
                extra={"attempt": attempt, "max_attempts": attempts},
            )
            await asyncio.sleep(base_delay_seconds * (2 ** (attempt - 1)))
    raise RuntimeError("with_connection_retry: unreachable (attempts must be >= 1)")


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession] | None = None,
    tenant_id: UUID | None = None,
    super_admin: bool = False,
    user_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    use_factory = factory if factory is not None else get_session_factory()
    tokens = (
        tenancy.current_tenant.set(tenant_id),
        tenancy.current_super_admin.set(super_admin),
        tenancy.current_user.set(user_id),
    )
    try:
        async with use_factory() as session, session.begin():
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', :t, true),"
                    " set_config('app.is_super_admin', :s, true),"
                    " set_config('app.user_id', :u, true)"
                ),
                {
                    "t": str(tenant_id) if tenant_id else "",
                    "s": "on" if super_admin else "off",
                    "u": str(user_id) if user_id else "",
                },
            )
            yield session
    finally:
        tenancy.current_tenant.reset(tokens[0])
        tenancy.current_super_admin.reset(tokens[1])
        tenancy.current_user.reset(tokens[2])


_ROLE_SQL = text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
_OWNED_SQL = text(
    "SELECT string_agg(tablename, ',') FROM pg_tables "
    "WHERE schemaname = 'public' AND tableowner = current_user"
)
_NO_RLS_SQL = text(
    "SELECT string_agg(c.relname, ',') FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
    "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
)
_UNLISTED_SQL = text(
    "SELECT string_agg(c.relname, ',') FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
    "AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
    "AND a.attname = 'tenant_id' AND NOT a.attisdropped) "
    "AND NOT (c.relname = ANY(:allow))"
)


_CORE_NO_RLS_SQL = text(
    "SELECT string_agg(c.relname, ',') FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname IN ('tenants', 'memberships') "
    "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
)


async def check_rls_safety(session: AsyncSession) -> list[str]:
    problems: list[str] = []
    if (await session.execute(_ROLE_SQL)).scalar_one():
        problems.append("runtime role is superuser or BYPASSRLS")
    owned = (await session.execute(_OWNED_SQL)).scalar_one()
    if owned:
        problems.append(f"runtime role owns tables: {owned}")
    no_rls = (await session.execute(_NO_RLS_SQL)).scalar_one()
    if no_rls:
        problems.append(f"tenant tables without RLS ENABLE+FORCE: {no_rls}")
    unlisted = (
        await session.execute(_UNLISTED_SQL, {"allow": list(GLOBAL_TABLE_ALLOWLIST)})
    ).scalar_one()
    if unlisted:
        problems.append(f"tables without tenant_id not on global allowlist: {unlisted}")
    core = (await session.execute(_CORE_NO_RLS_SQL)).scalar_one()
    if core:
        problems.append(f"tenants/memberships RLS missing: {core}")
    return problems
