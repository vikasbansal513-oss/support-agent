"""Ingest one Web Vitals beacon per page navigation. One INSERT per beacon: a page
navigation is rare compared to an API call, so this does not need buffering/batching
(unlike a per-request counter would) to stay within "avoid excessive database writes".
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.contracts import PagePerfBeaconIn

_INSERT = text(
    "INSERT INTO page_perf_events (tenant_id, route, ttfb_ms, dom_content_loaded_ms, load_ms,"
    " lcp_ms, inp_ms, cls, api_duration_ms, failed_api, user_agent)"
    " VALUES (:tenant, :route, :ttfb, :dcl, :load, :lcp, :inp, :cls, :api_ms, :failed, :ua)"
)


async def record_beacon(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    beacon: PagePerfBeaconIn,
    user_agent: str | None,
) -> None:
    await session.execute(
        _INSERT,
        {
            "tenant": tenant_id,
            "route": beacon.route,
            "ttfb": beacon.ttfb_ms,
            "dcl": beacon.dom_content_loaded_ms,
            "load": beacon.load_ms,
            "lcp": beacon.lcp_ms,
            "inp": beacon.inp_ms,
            "cls": beacon.cls,
            "api_ms": beacon.api_duration_ms,
            "failed": beacon.failed_api,
            "ua": (user_agent or "")[:300] or None,
        },
    )
