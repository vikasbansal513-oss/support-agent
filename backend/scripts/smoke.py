import asyncio
import os
import sys

import httpx

Check = tuple[str, str, int | tuple[int, ...]]

CHECKS: list[Check] = [
    ("GET", "/health", 200),
    ("GET", "/ready", 200),
    ("GET", "/public/privacy-notice", 200),
    ("GET", "/public/terms", 200),
    ("GET", "/conversations", 401),
    ("GET", "/admin/tenants", 401),
    ("POST", "/webhooks/whatsapp/" + "x" * 43, 404),
]


async def run(client: httpx.AsyncClient) -> list[str]:
    failures: list[str] = []
    for method, path, expected in CHECKS:
        allowed = (expected,) if isinstance(expected, int) else expected
        response = await client.request(method, path, json={} if method == "POST" else None)
        if response.status_code not in allowed:
            failures.append(f"{method} {path}: got {response.status_code}, wanted {allowed}")
        if "x-request-id" not in response.headers:
            failures.append(f"{method} {path}: missing X-Request-ID")
        if response.status_code >= 400 and "Traceback" in response.text:
            failures.append(f"{method} {path}: error body leaks a traceback")
    return failures


async def main() -> int:
    base = os.environ.get("SMOKE_BASE_URL", "").rstrip("/")
    if not base:
        print("SMOKE_BASE_URL is required", file=sys.stderr)
        return 2
    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
        failures = await run(client)
    for line in failures:
        print("FAIL", line)
    print("smoke ok" if not failures else f"smoke failed: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
