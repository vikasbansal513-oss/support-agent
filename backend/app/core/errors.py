import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.contracts.errors import ERRORS
from app.core.logging import request_id_var

logger = logging.getLogger("app.errors")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_HTTP_TO_CODE = {401: "E-AUTH-004", 403: "E-AUTH-007", 404: "E-NF-001"}


class AppError(Exception):
    def __init__(
        self, code: str, detail: str | None = None, status_code: int | None = None
    ) -> None:
        http, severity, message = ERRORS[code]
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status_code = status_code or http
        self.severity = severity
        self.message = message


def new_error_id() -> str:
    return f"ERR-{uuid.uuid4().hex[:12].upper()}"


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message["headers"] if h[0].lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def _response(
    request: Request, code: str, status_code: int, detail: str | None, exc: BaseException | None
) -> JSONResponse:
    _, severity, message = ERRORS[code]
    error_id = new_error_id()
    request_id = request.scope.get("state", {}).get("request_id", "")
    logger.error(
        "request failed",
        extra={
            "error_id": error_id,
            "error_code": code,
            "detail": detail,
            "path_template": getattr(request.scope.get("route"), "path", None),
        },
        exc_info=exc if severity == "critical" else None,
    )
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "error_id": error_id,
        "severity": severity,
    }
    return JSONResponse(body, status_code=status_code, headers={"X-Request-ID": request_id})


def install_error_handlers(app: FastAPI) -> None:
    app.add_middleware(RequestIDMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return _response(request, exc.code, exc.status_code, exc.detail, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ",".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        return _response(request, "E-VAL-001", ERRORS["E-VAL-001"][0], fields, None)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            return _response(request, "E-SYS-001", 500, None, exc)
        code = _HTTP_TO_CODE.get(exc.status_code, "E-VAL-001")
        return _response(request, code, exc.status_code, None, None)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return _response(request, "E-SYS-001", 500, type(exc).__name__, exc)
