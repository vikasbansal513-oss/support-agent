import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.contracts.contracts import ErrorBody
from app.core.errors import AppError, install_error_handlers


class Item(BaseModel):
    qty: int


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/app-error")
    async def app_error() -> None:
        raise AppError("E-POL-001", detail="internal reason 9811100001")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal text 9811100001")

    @app.post("/item")
    async def item(body: Item) -> Item:
        return body

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_shape_and_headers() -> None:
    response = _client().get("/app-error")
    assert response.status_code == 409
    body = ErrorBody.model_validate(response.json())
    assert body.code == "E-POL-001"
    assert "internal" not in response.text and "9811100001" not in response.text
    assert re.match(r"^[0-9a-f-]{36}$", response.headers["x-request-id"])


def test_unhandled_exception_hides_raw_text_and_keeps_request_id() -> None:
    response = _client().get("/boom", headers={"X-Request-ID": "trace-abcdef12"})
    assert response.status_code == 500
    assert ErrorBody.model_validate(response.json()).code == "E-SYS-001"
    assert "secret" not in response.text and "RuntimeError" not in response.text
    assert response.headers["x-request-id"] == "trace-abcdef12"


def test_validation_and_not_found_use_catalog() -> None:
    client = _client()
    bad = client.post("/item", json={"qty": "x"})
    assert bad.status_code == 422 and ErrorBody.model_validate(bad.json()).code == "E-VAL-001"
    missing = client.get("/nope")
    assert (
        missing.status_code == 404 and ErrorBody.model_validate(missing.json()).code == "E-NF-001"
    )
    assert "x-request-id" in missing.headers


def test_bad_incoming_request_id_replaced() -> None:
    response = _client().get("/ok", headers={"X-Request-ID": "bad id!"})
    assert response.headers["x-request-id"] != "bad id!"


def test_unknown_code_rejected() -> None:
    with pytest.raises(KeyError):
        AppError("E-NOPE-999")
