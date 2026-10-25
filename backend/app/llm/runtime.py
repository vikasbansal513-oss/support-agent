import ipaddress
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import Crypto, CryptoError
from app.core.db import session_scope

logger = logging.getLogger("app.llm.runtime")

PROVIDERS = ("anthropic", "openai", "groq", "openrouter", "openai_compatible")
Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class EffectiveLlm:
    provider: str
    model: str
    api_key: str
    base_url: str
    source: str


def from_env() -> EffectiveLlm:
    settings = get_settings()
    return EffectiveLlm(
        settings.llm_provider,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_base_url.strip(),
        "deploy",
    )


async def load_effective(factory: Factory) -> EffectiveLlm:
    env = from_env()
    try:
        async with session_scope(factory, super_admin=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT provider, model, base_url, api_key_enc"
                        " FROM platform_llm_settings WHERE id = 1"
                    )
                )
            ).first()
    except SQLAlchemyError as exc:
        logger.error("llm settings read failed", extra={"error_class": type(exc).__name__})
        return env
    if row is None:
        return env
    api_key = env.api_key
    if row.api_key_enc:
        try:
            api_key = Crypto(get_settings().encryption_key).decrypt(row.api_key_enc)
        except CryptoError:
            logger.error("llm settings key decrypt failed")
            return env
    return EffectiveLlm(row.provider, row.model, api_key, row.base_url, "dashboard")


def encrypt_key(api_key: str) -> str:
    return Crypto(get_settings().encryption_key).encrypt(api_key)


def validate_choice(provider: str, model: str, base_url: str) -> str | None:
    if provider not in PROVIDERS:
        return "unknown provider"
    if not model.strip():
        return "model is required"
    if provider != "anthropic" and model.strip().lower().startswith("claude"):
        return "model does not belong to this provider"
    if provider == "openai_compatible" and not base_url.strip():
        return "base_url is required for openai_compatible"
    if base_url.strip():
        return _check_base_url(base_url.strip())
    return None


def _check_base_url(base_url: str) -> str | None:
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return "base_url must be an https URL"
    if host == "localhost" or host.endswith((".internal", ".local", ".localhost")):
        return "base_url host is not allowed"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not address.is_global:
        return "base_url host is not allowed"
    return None
