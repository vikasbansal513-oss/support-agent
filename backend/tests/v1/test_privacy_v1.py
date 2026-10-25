from sqlalchemy import text

from app.core.db import session_scope
from app.jobs import ops
from tests.agent.helpers import AgentEnv
from tests.ops.test_privacy import PHONE, ctx, seed


async def test_delete_request_erases_v1_state_and_usage(env: AgentEnv) -> None:
    await env.publish_policy()
    await seed(env)
    conv = (await env.q("SELECT id, customer_id FROM conversations"))[0]
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("INSERT INTO conversation_state (conversation_id) VALUES (:c)"), {"c": conv.id}
        )
        await session.execute(
            text(
                "INSERT INTO action_usage (customer_id, day, tool, count, sum_paise)"
                " VALUES (:c, current_date, 'order_cancel', 1, 100)"
            ),
            {"c": conv.customer_id},
        )
    await env.client.post(
        "/privacy/requests", json={"subject_ref": PHONE, "kind": "delete"}, headers=env.wa.headers
    )
    await ops.retention(ctx(env, "retention"))
    for table in ("conversation_state", "action_usage", "customers"):
        rows = await env.q(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
        assert rows[0].n == 0, table
