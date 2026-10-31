import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.billing import calc, service
from app.billing.schemas import (
    BillingSettingsIn,
    BillingSettingsOut,
    FeeIn,
    FinalizeIn,
    GenerateIn,
    GenerateOut,
    InvoiceOut,
    LlmRateIn,
    LlmRateOut,
    LlmRatesOut,
    PeriodIn,
    PeriodOut,
    ProfileIn,
    ProfileRowOut,
    SpeechRateIn,
    SpeechRateOut,
    SpeechRatesOut,
)
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])
Factory = async_sessionmaker[AsyncSession]


def _period(value: str) -> date:
    try:
        return calc.parse_period(value)
    except ValueError as exc:
        raise AppError("E-VAL-001", detail="bad period") from exc


@router.get("/settings")
async def read_settings(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> BillingSettingsOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_settings(session)


@router.put("/settings")
async def save_settings(
    body: BillingSettingsIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> BillingSettingsOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_settings(session, body, admin.user_id)


@router.get("/llm-rates")
async def read_rates(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmRatesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_rates(session)


@router.put("/llm-rates")
async def save_rate(
    body: LlmRateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmRateOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_rate(session, body, admin.user_id)


@router.get("/speech-rates")
async def read_speech_rates(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechRatesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_speech_rates(session)


@router.put("/speech-rates")
async def save_speech_rate(
    body: SpeechRateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechRateOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_speech_rate(session, body, admin.user_id)


@router.get("/profiles")
async def list_profiles(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ProfileRowOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_profiles(session)


@router.put("/profiles/{tenant_id}")
async def save_profile(
    tenant_id: uuid.UUID,
    body: ProfileIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ProfileRowOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_profile(session, tenant_id, body, admin.user_id)


@router.get("/periods/{period}")
async def read_period(
    period: str,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PeriodOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.period_overview(session, period_start)


@router.put("/periods/{period}")
async def save_period(
    period: str,
    body: PeriodIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PeriodOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_period(session, period_start, body, admin.user_id)


@router.post("/invoices/generate")
async def generate_invoices(
    body: GenerateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GenerateOut:
    period_start = _period(body.period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.generate(session, period_start, body.tenant_id, admin.user_id)


@router.get("/invoices/{invoice_id}")
async def read_invoice(
    invoice_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.get_invoice(session, invoice_id)


@router.put("/invoices/{invoice_id}/fee")
async def set_fee(
    invoice_id: uuid.UUID,
    body: FeeIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.set_fee(session, invoice_id, body.platform_fee_paise, admin.user_id)


@router.post("/invoices/{invoice_id}/finalize")
async def finalize_invoice(
    invoice_id: uuid.UUID,
    body: FinalizeIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.finalize(session, invoice_id, body.expected_total_paise, admin.user_id)
