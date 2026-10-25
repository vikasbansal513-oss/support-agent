import asyncio
import os
import re
import sys
import uuid

import httpx

from app.contracts.contracts import ENDPOINTS

UNAUTH_OK = {"user": (401,), "owner": (401,), "admin": (401,)}
SAMPLE = {"id": str(uuid.UUID(int=1)), "kind": "shopify", "token": "x" * 43}


def concrete(path: str) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1).split("|")[0]
        return SAMPLE.get(name, SAMPLE["id"])

    return re.sub(r"\{([^}]+)\}", fill, path)


async def run(client: httpx.AsyncClient) -> list[str]:
    failures: list[str] = []
    for endpoint in ENDPOINTS:
        path = concrete(endpoint["path"])
        if (
            path.startswith("/webhooks")
            or endpoint["auth"] == "public"
            and endpoint["method"] != "GET"
        ):
            continue
        response = await client.request(
            endpoint["method"], path, json={} if endpoint["method"] != "GET" else None
        )
        label = f"{endpoint['method']} {endpoint['path']}"
        if response.status_code >= 500:
            failures.append(f"{label}: server error {response.status_code}")
        elif (
            endpoint["auth"] in UNAUTH_OK
            and response.status_code not in UNAUTH_OK[endpoint["auth"]]
        ):
            failures.append(f"{label}: unauthenticated call got {response.status_code}")
        elif endpoint["auth"] == "public" and response.status_code != 200:
            failures.append(f"{label}: public endpoint got {response.status_code}")
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
    print("sweep ok" if not failures else f"sweep failed: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
