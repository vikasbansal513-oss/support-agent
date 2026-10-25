from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.shopify_write import set_writer_override
from tests.agent.helpers import AgentEnv, build_env
from tests.auth.helpers import MemoryMailer


@pytest.fixture
async def env(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: async_sessionmaker[AsyncSession],
    owner_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AgentEnv]:
    async for value in build_env(client, mailer, runtime_factory, owner_factory, monkeypatch):
        yield value


@pytest.fixture(autouse=True)
def _reset_writer() -> Iterator[None]:
    yield
    set_writer_override(None)
