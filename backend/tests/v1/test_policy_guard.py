import pytest

from app.core.db import session_scope
from app.core.errors import AppError
from app.policy.service import _guard_content
from app.v1.policy import PolicyV2, parse_any
from tests.agent.helpers import AgentEnv


def draft(**autonomy: str) -> PolicyV2:
    body = PolicyV2().model_dump(mode="json")
    body["autonomy"] = {**body["autonomy"], **autonomy}
    body["enabled_tools"] = sorted({*body["enabled_tools"], *autonomy})
    parsed = parse_any(body)
    assert isinstance(parsed, PolicyV2)
    return parsed


async def test_guard_names_raised_tools(env: AgentEnv) -> None:
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await _guard_content(session, draft(order_cancel="approval"))
        with pytest.raises(AppError) as err:
            await _guard_content(session, draft(order_cancel="auto", refund_create="auto"))
    assert err.value.code == "E-POL-002"
    assert err.value.detail == "auto_needs_admin:order_cancel,refund_create"
