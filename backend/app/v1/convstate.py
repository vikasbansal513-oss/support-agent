import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.v1.contracts import ConvUpdate


@dataclass
class ConvRow:
    awaiting: str = "none"
    pending_action_id: uuid.UUID | None = None
    turn_count: int = 0
    unclear_count: int = 0
    write_count: int = 0
    context: dict[str, Any] = field(default_factory=dict)


async def load(session: AsyncSession, conversation_id: uuid.UUID) -> ConvRow:
    row = (
        await session.execute(
            text(
                "SELECT awaiting, pending_action_id, turn_count, unclear_count, write_count, context"
                " FROM conversation_state WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        )
    ).first()
    if row is None:
        return ConvRow()
    return ConvRow(
        awaiting=row.awaiting,
        pending_action_id=row.pending_action_id,
        turn_count=row.turn_count,
        unclear_count=row.unclear_count,
        write_count=row.write_count,
        context=dict(row.context or {}),
    )


async def save(session: AsyncSession, conversation_id: uuid.UUID, row: ConvRow) -> None:
    await session.execute(
        text(
            "INSERT INTO conversation_state (conversation_id, pending_action_id, awaiting,"
            " turn_count, unclear_count, write_count, context) VALUES (:c, :p, :a, :t, :u, :w,"
            " CAST(:x AS jsonb)) ON CONFLICT (tenant_id, conversation_id) DO UPDATE SET"
            " pending_action_id = :p, awaiting = :a, turn_count = :t, unclear_count = :u,"
            " write_count = :w, context = CAST(:x AS jsonb), updated_at = now()"
        ),
        {
            "c": conversation_id,
            "p": row.pending_action_id,
            "a": row.awaiting,
            "t": row.turn_count,
            "u": row.unclear_count,
            "w": row.write_count,
            "x": json.dumps(row.context),
        },
    )


def apply_update(row: ConvRow, update: ConvUpdate) -> ConvRow:
    row.awaiting = update.awaiting
    row.unclear_count = update.unclear_count
    row.turn_count = update.turn_count
    if update.clear_pending:
        row.pending_action_id = None
        row.context.pop("confirm_token", None)
        row.context.pop("pending", None)
    if update.order_ref:
        row.context["order_ref"] = update.order_ref
    return row
