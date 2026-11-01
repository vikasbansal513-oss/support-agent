"""Plivo call-status (CDR) webhook: ingests the actual billed call into plivo_call_records
so Usage & Costs / invoices can use real per-call Plivo cost instead of only an admin-entered
monthly actual bill. VERIFY: signature verification follows Plivo's documented V2 scheme but
has not been exercised against a real Plivo account in this sandbox."""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing import service
from app.billing.plivo_signature import verify_plivo_signature
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import new_error_id

logger = logging.getLogger("app.webhooks.plivo")
router = APIRouter(tags=["webhooks"])
Factory = async_sessionmaker[AsyncSession]
MAX_BODY_BYTES = 65_536


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


@router.post("/webhooks/plivo/call-status")
async def plivo_call_status(
    request: Request,
) -> JSONResponse:
    settings = get_settings()
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"ok": False}, status_code=413)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        import json

        try:
            fields: dict[str, Any] = json.loads(raw) if raw else {}
        except ValueError:
            fields = {}
    else:
        fields = dict((await request.form()))
    if settings.plivo_auth_token:
        signature = request.headers.get("X-Plivo-Signature-V2", "")
        nonce = request.headers.get("X-Plivo-Signature-V2-Nonce", "")
        if not verify_plivo_signature(
            str(request.url), nonce, signature, settings.plivo_auth_token
        ):
            return JSONResponse({"ok": False, "error": "bad signature"}, status_code=403)
    else:
        logger.warning("PLIVO_AUTH_TOKEN not set - Plivo webhook signature not verified")
    call_uuid = str(fields.get("CallUUID") or fields.get("call_uuid") or "")
    if not call_uuid:
        return JSONResponse({"ok": False, "error": "missing CallUUID"}, status_code=400)
    direction_raw = str(fields.get("Direction") or fields.get("direction") or "inbound").lower()
    direction = "outbound" if direction_raw == "outbound" else "inbound"
    duration = _int(fields.get("Duration") or fields.get("duration_seconds"))
    billed = _int(fields.get("BillDuration") or fields.get("billed_seconds") or duration)
    try:
        factory = get_session_factory()
        async with session_scope(factory, super_admin=True) as session:
            await service.ingest_plivo_call(
                session,
                call_uuid=call_uuid,
                direction=direction,  # type: ignore[arg-type]
                from_number=str(fields.get("From") or fields.get("from_number") or ""),
                to_number=str(fields.get("To") or fields.get("to_number") or ""),
                duration_seconds=duration,
                billed_seconds=billed,
                call_status=str(fields.get("CallStatus") or fields.get("call_status") or ""),
                provider_charge_inr=_decimal_or_none(
                    fields.get("TotalCost") or fields.get("provider_charge_inr")
                ),
                raw={k: str(v) for k, v in fields.items()},
            )
    except Exception as exc:
        logger.error(
            "plivo webhook failed",
            extra={"error_id": new_error_id(), "error_class": type(exc).__name__},
        )
        return JSONResponse({"ok": False}, status_code=200)  # ack anyway, Plivo retries on non-2xx
    return JSONResponse({"ok": True})
