from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def is_globally_paused(session: AsyncSession) -> bool:
    value = (
        await session.execute(
            text("SELECT enabled FROM platform_flags WHERE name = 'global_pause'")
        )
    ).scalar_one_or_none()
    return bool(value)
