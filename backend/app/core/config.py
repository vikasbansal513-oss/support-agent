from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = ""
    migration_database_url: str = ""
    encryption_key: str = ""
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_disable_statement_cache: bool = True

    jwt_secret: str = ""
    admin_jwt_secret: str = ""
    access_token_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    lockout_max_attempts: int = Field(default=5, ge=1, le=50)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)
    frontend_url: str = ""
    cors_origins: str = ""
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "none"
    trusted_proxy_hops: int = Field(default=1, ge=0, le=5)
    mail_from: str = ""
    brevo_api_key: str = ""
    llm_provider: Literal["anthropic", "openai", "groq", "openrouter", "openai_compatible"] = (
        "anthropic"
    )
    llm_model: str = "claude-haiku-5-5"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    llm_p95_target_ms: int = Field(default=4000, ge=100, le=60000)
    classify_min_confidence: float = Field(default=0.7, ge=0, le=1)
    agent_recursion_limit: int = Field(default=25, ge=10, le=25)
    action_stale_minutes: int = Field(default=10, ge=1, le=240)
    shadow_min_reviewed: int = Field(default=20, ge=1, le=10000)
    shadow_min_correct_pct: int = Field(default=90, ge=1, le=100)
    public_base_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    gupshup_ip_allowlist: str = ""
    worker_max_seconds: int = Field(default=55, ge=5, le=3000)
    instant_drain_enabled: bool = False
    job_lease_seconds: int = Field(default=120, ge=30, le=3600)
    job_tenant_limit: int = Field(default=2, ge=1, le=20)
    customer_flood_per_minute: int = Field(default=10, ge=2, le=200)
    app_release: str = "dev"
    service_name: str = "support-agent"
    sentry_dsn: str = ""
    grievance_email: str = ""
    notice_published: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)
    technical_retention_days: int = Field(default=14, ge=1, le=365)
    sla_realert_minutes: int = Field(default=30, ge=5, le=1440)
    voice_min_confidence: float = Field(default=0.55, ge=0, le=1)
    voice_confirm_confidence: float = Field(default=0.8, ge=0, le=1)
    voice_max_turns: int = Field(default=20, ge=3, le=100)
    voice_silence_limit: int = Field(default=2, ge=1, le=5)
    voice_low_confidence_limit: int = Field(default=2, ge=1, le=5)
    voice_slow_ms: int = Field(default=2000, ge=200, le=30000)
    voice_order_memory_days: int = Field(default=30, ge=0, le=365)
    approval_sla_minutes: int = Field(default=60, ge=5, le=1440)
    privacy_warn_days: int = Field(default=5, ge=1, le=29)
    unanswered_alert_minutes: int = Field(default=15, ge=1, le=240)
    anomaly_auto_threshold: int = Field(default=10, ge=2, le=1000)
    anomaly_window_minutes: int = Field(default=10, ge=1, le=240)
    default_tenant_mode: Literal["shadow", "draft", "auto"] = "shadow"

    @model_validator(mode="after")
    def _separate_admin_secret(self) -> "Settings":
        if self.jwt_secret and self.admin_jwt_secret and self.jwt_secret == self.admin_jwt_secret:
            raise ValueError("ADMIN_JWT_SECRET must differ from JWT_SECRET")
        return self

    @model_validator(mode="after")
    def _llm_provider_settings(self) -> "Settings":
        if self.llm_provider != "anthropic" and self.llm_model.startswith("claude"):
            raise ValueError("LLM_MODEL must be set to a model of the chosen LLM_PROVIDER")
        if self.llm_provider == "openai_compatible" and not self.llm_base_url.strip():
            raise ValueError("LLM_BASE_URL is required when LLM_PROVIDER is openai_compatible")
        return self

    def allowed_origins(self) -> list[str]:
        raw = [self.frontend_url, *self.cors_origins.split(",")]
        return sorted({o.strip().rstrip("/") for o in raw if o.strip()})


@lru_cache
def get_settings() -> Settings:
    return Settings()
