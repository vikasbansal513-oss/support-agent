from scripts import api_sweep, smoke
from tests.agent.helpers import AgentEnv


async def test_smoke_passes_against_app(env: AgentEnv) -> None:
    assert await smoke.run(env.client) == []


async def test_api_sweep_no_5xx_and_auth_enforced(env: AgentEnv) -> None:
    assert await api_sweep.run(env.client) == []


def test_concrete_fills_path_params() -> None:
    assert "{" not in api_sweep.concrete("/integrations/{shiprocket|delhivery}")
    assert api_sweep.concrete("/conversations/{id}/reply").startswith("/conversations/")
