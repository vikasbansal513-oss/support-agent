import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.contracts.contracts import ErrorBody
from app.core.db import get_session_factory, make_engine
from app.main import app
from tests.conftest import TestDb, alembic_config, drop_db, migrate_up, provision_db


def _client(url: str) -> Iterator[TestClient]:
    engine = make_engine(url, null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_ready_ok_for_runtime_role(test_db: TestDb) -> None:
    for client in _client(test_db.app_url):
        response = client.get("/ready")
        assert response.status_code == 200 and response.json() == {"status": "ready"}
        assert "x-request-id" in response.headers


@pytest.mark.parametrize("which", ["admin_url", "owner_url"])
def test_ready_503_for_unsafe_roles(test_db: TestDb, which: str) -> None:
    for client in _client(getattr(test_db, which)):
        response = client.get("/ready")
        assert response.status_code == 503
        body = ErrorBody.model_validate(response.json())
        assert body.code == "E-SYS-002"
        assert "superuser" not in response.text and "owns tables" not in response.text


def test_ready_503_when_db_unreachable(test_db: TestDb) -> None:
    for client in _client(test_db.app_url.replace(test_db.name, "no_such_db")):
        response = client.get("/ready")
        assert response.status_code == 503
        assert ErrorBody.model_validate(response.json()).code == "E-SYS-002"


def test_health_needs_no_database() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_migration_up_down_up_and_runtime_grants() -> None:
    from alembic import command

    db = provision_db(migrate=False)
    try:
        migrate_up(db)
        cfg = alembic_config(db)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        async def tables_and_privs() -> tuple[list[str], bool]:
            engine = make_engine(db.owner_url, null_pool=True)
            async with engine.connect() as conn:
                names = (
                    (
                        await conn.execute(
                            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                        )
                    )
                    .scalars()
                    .all()
                )
                can_update = (
                    await conn.execute(
                        text(
                            "SELECT bool_or(has_table_privilege("
                            "'support_app_runtime', 'audit_log', p)) "
                            "FROM unnest(ARRAY['UPDATE', 'DELETE', 'TRUNCATE']) AS p"
                        )
                    )
                ).scalar_one()
            await engine.dispose()
            return list(names), bool(can_update)

        names, can_mutate = asyncio.run(tables_and_privs())
        assert sorted(names) == sorted(
            [
                "audit_log",
                "tenants",
                "users",
                "platform_admins",
                "memberships",
                "email_tokens",
                "refresh_tokens",
                "rate_limits",
                "user_mfa",
                "integrations",
                "wa_numbers",
                "tenant_settings",
                "external_calls",
                "eval_runs",
                "policies",
                "customers",
                "conversations",
                "inbound_events",
                "messages",
                "usage_counters",
                "jobs",
                "notifications",
                "heartbeats",
                "opt_outs",
                "agent_runs",
                "agent_actions",
                "handoffs",
                "llm_model_prices",
                "llm_calls",
                "answer_reports",
                "shadow_verdicts",
                "consents",
                "privacy_requests",
                "platform_flags",
                "conversation_state",
                "action_usage",
                "voice_numbers",
                "voice_calls",
                "platform_llm_settings",
                "billing_settings",
                "billing_llm_rates",
                "billing_periods",
                "invoice_counters",
                "tenant_billing_profiles",
                "invoices",
                "invoice_lines",
                "brand_quotas",
                "alerts",
            ]
        )
        assert can_mutate is False
    finally:
        drop_db(db)


def test_alembic_version_table_kept_out_of_public(test_db: TestDb) -> None:
    async def versions() -> list[str]:
        engine = make_engine(test_db.owner_url, null_pool=True)
        async with engine.connect() as conn:
            rows = (
                (await conn.execute(text("SELECT version_num FROM alembic_meta.alembic_version")))
                .scalars()
                .all()
            )
        await engine.dispose()
        return list(rows)

    assert asyncio.run(versions()) == ["0020"]
