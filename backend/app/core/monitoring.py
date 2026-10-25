from typing import Any

import sentry_sdk

from app.core.config import get_settings
from app.core.masking import redact_text

_DROP_KEYS = {"data", "cookies", "headers", "query_string", "env"}


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        for key in _DROP_KEYS:
            request.pop(key, None)
    event.pop("user", None)
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    for value in (event.get("exception") or {}).get("values", []):
        if isinstance(value.get("value"), str):
            value["value"] = redact_text(value["value"])
        for frame in (value.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    return event


def init_monitoring(component: str) -> bool:
    settings = get_settings()
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        release=settings.app_release,
        environment=settings.app_env,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0.0,
        before_send=scrub_event,  # type: ignore[arg-type]
    )
    sentry_sdk.set_tag("service", settings.service_name)
    sentry_sdk.set_tag("component", component)
    return True


def tag_tenant(tenant_id: str) -> None:
    sentry_sdk.set_tag("tenant_id", tenant_id)
