"""Response security headers for the API.

The frontend (Vercel, see frontend/vercel.json) already sets X-Content-Type-Options,
X-Frame-Options, Referrer-Policy and Content-Security-Policy on the pages it serves -
those protect the browser rendering the dashboard. This module sets the same class of
headers on API *responses* themselves, which the frontend's vercel.json cannot reach
(it only covers what Vercel serves, not what Cloud Run returns). Two concrete reasons
this matters even for a JSON API:

- X-Content-Type-Options: nosniff - stops a browser from MIME-sniffing a response body
  into something it isn't. Matters specifically for any endpoint that returns a file
  (e.g. billing/pdf.py's invoice PDF download) or user-influenced text, not just JSON.
- X-Frame-Options / frame-ancestors - defense in depth in case any API response is ever
  rendered directly (an error page, a redirect target) rather than consumed as JSON.

Kept as a tiny dependency-free ASGI middleware (not starlette.BaseHTTPMiddleware) so it
adds no new package and no response-buffering overhead.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    # API responses are JSON/PDF, never HTML the browser would execute script from, so a
    # strict default-src with nothing else allowed is safe and does not need the
    # frontend's 'unsafe-inline' style allowance.
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


class SecurityHeadersMiddleware:
    """ASGI middleware: appends fixed security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)
