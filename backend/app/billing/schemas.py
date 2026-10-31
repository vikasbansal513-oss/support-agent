import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.billing.calc import STATE_CODES, gstin_problem

Rate = Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=18, decimal_places=6)]
SecondRate = Annotated[Decimal, Field(ge=0, le=1000, max_digits=18, decimal_places=10)]
Paise = Annotated[int, Field(ge=0, le=100_000_000_000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _check_state(value: str) -> str:
    if value and value not in STATE_CODES:
        raise ValueError("unknown state code")
    return value


def _check_gstin(gstin: str, state_code: str) -> None:
    problem = gstin_problem(gstin, state_code)
    if problem:
        raise ValueError(problem)


class BillingSettingsIn(Strict):
    seller_name: str = Field(default="", max_length=200)
    seller_address: str = Field(default="", max_length=500)
    seller_state_code: str = Field(default="", pattern=r"^([0-9]{2})?$")
    seller_gstin: str = Field(default="", max_length=15)
    seller_email: str = Field(default="", max_length=254)
    seller_phone: str = Field(default="", max_length=30)
    payment_details: str = Field(default="", max_length=500)
    sac_code: str = Field(default="", pattern=r"^[0-9]{0,8}$")
    invoice_prefix: str = Field(default="INV", pattern=r"^[A-Z0-9]{1,3}$")
    gst_enabled: bool = True
    gst_rate_percent: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] = (
        Decimal("18.00")
    )
    cloudrun_vcpu: Annotated[Decimal, Field(ge=0, le=64, max_digits=8, decimal_places=3)] = Decimal(
        0
    )
    cloudrun_gib: Annotated[Decimal, Field(ge=0, le=512, max_digits=8, decimal_places=3)] = Decimal(
        0
    )
    cloudrun_usd_per_vcpu_second: SecondRate = Decimal(0)
    cloudrun_usd_per_gib_second: SecondRate = Decimal(0)

    @field_validator("seller_gstin")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("seller_state_code")
    @classmethod
    def _state(cls, value: str) -> str:
        return _check_state(value)

    @model_validator(mode="after")
    def _gstin_matches_state(self) -> "BillingSettingsIn":
        _check_gstin(self.seller_gstin, self.seller_state_code)
        return self


class BillingSettingsOut(BillingSettingsIn):
    updated_at: datetime | None = None


class LlmRateIn(Strict):
    provider: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    model: str = Field(min_length=1, max_length=200)
    input_usd_per_million: Rate
    output_usd_per_million: Rate


class LlmRateOut(LlmRateIn):
    updated_at: datetime


class ModelRef(BaseModel):
    provider: str
    model: str


class LlmRatesOut(BaseModel):
    rates: list[LlmRateOut]
    unpriced: list[ModelRef]


SpeechProvider = Literal["sarvam_stt", "google_stt", "sarvam_tts", "google_tts"]


class SpeechRateIn(Strict):
    provider: SpeechProvider
    unit: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]{1,40}$")
    rate_inr: Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=18, decimal_places=6)]
    active: bool = True


class SpeechRateOut(SpeechRateIn):
    updated_at: datetime


class SpeechRatesOut(BaseModel):
    rates: list[SpeechRateOut]


class ProfileIn(Strict):
    legal_name: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=500)
    state_code: str = Field(default="", pattern=r"^([0-9]{2})?$")
    gstin: str = Field(default="", max_length=15)
    email: str = Field(default="", max_length=254)
    default_platform_fee_paise: Paise = 0

    @field_validator("gstin")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("state_code")
    @classmethod
    def _state(cls, value: str) -> str:
        return _check_state(value)

    @model_validator(mode="after")
    def _gstin_matches_state(self) -> "ProfileIn":
        _check_gstin(self.gstin, self.state_code)
        return self


class ProfileRowOut(ProfileIn):
    tenant_id: uuid.UUID
    tenant_name: str
    slug: str


class PeriodIn(Strict):
    usd_inr: Annotated[Decimal, Field(gt=0, le=1000, max_digits=12, decimal_places=4)] | None = None
    cloudrun_actual_paise: Paise | None = None
    telephony_actual_paise: Paise | None = None


class GenerateIn(Strict):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    tenant_id: uuid.UUID | None = None


class FeeIn(Strict):
    platform_fee_paise: Paise


class FinalizeIn(Strict):
    expected_total_paise: Annotated[int, Field(ge=0, le=1_000_000_000_000)]


class FlagOut(BaseModel):
    code: str
    detail: str | None = None


class InvoiceSummaryOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: Literal["draft", "final"]
    invoice_no: str | None
    total_paise: int


class PeriodRowOut(BaseModel):
    tenant_id: uuid.UUID
    name: str
    slug: str
    calls: int
    input_tokens: int
    output_tokens: int
    llm_paise: int | None
    compute_seconds: int
    jobs: int
    cloudrun_paise: int | None
    voice_calls: int
    voice_call_seconds: int
    telephony_paise: int | None
    default_platform_fee_paise: int
    unpriced_models: list[str]
    invoice: InvoiceSummaryOut | None


class PeriodOut(BaseModel):
    period: str
    period_ended: bool
    usd_inr: Decimal | None
    cloudrun_actual_paise: int | None
    cloudrun_unallocated_paise: int
    telephony_actual_paise: int | None
    telephony_unallocated_paise: int
    rows: list[PeriodRowOut]


class SellerOut(BaseModel):
    name: str
    address: str
    state_code: str
    gstin: str
    email: str
    phone: str
    payment_details: str
    sac_code: str


class BuyerOut(BaseModel):
    name: str
    address: str
    state_code: str
    gstin: str
    email: str


class InvoiceLineOut(BaseModel):
    position: int
    kind: Literal["llm", "cloudrun", "telephony", "platform_fee"]
    description: str
    quantity: Decimal
    unit: str
    amount_paise: int
    detail: dict[str, Any]


class InvoiceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    period: str
    period_start: date
    status: Literal["draft", "final"]
    doc_type: Literal["tax_invoice", "proforma"]
    invoice_no: str | None
    invoice_date: date | None
    usd_inr: Decimal
    cloudrun_basis: Literal["estimate", "actual"]
    llm_paise: int
    cloudrun_paise: int
    telephony_paise: int
    platform_fee_paise: int
    subtotal_paise: int
    gst_rate_percent: Decimal
    gst_kind: Literal["none", "intra", "inter"]
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    seller: SellerOut
    buyer: BuyerOut
    lines: list[InvoiceLineOut]
    blockers: list[FlagOut]
    warnings: list[FlagOut]
    generated_at: datetime
    finalized_at: datetime | None


class GenerateOut(BaseModel):
    generated: int
    skipped_final: int
    invoices: list[InvoiceSummaryOut]
