import itertools
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.billing import calc
from app.core import timeutil
from app.core.db import session_scope
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import active_user, bearer, error_code, set_tenant_status
from tests.auth.test_admin_flow import _admin_login_with_mfa

SELLER = {
    "seller_name": "Vikas AI Services",
    "seller_address": "Faridabad, Haryana",
    "seller_state_code": "06",
    "seller_gstin": "06ABCDE1234F1Z5",
    "seller_email": "billing@example.com",
    "seller_phone": "9999999999",
    "payment_details": "Bank: Test Bank, A/c 000, IFSC TEST0000001",
    "sac_code": "998314",
    "invoice_prefix": "INV",
    "gst_enabled": True,
    "gst_rate_percent": "18.00",
    "cloudrun_vcpu": "1",
    "cloudrun_gib": "0.5",
    "cloudrun_usd_per_vcpu_second": "0.000024",
    "cloudrun_usd_per_gib_second": "0.0000025",
}


_MONTHS_BACK = itertools.count(1)


def previous_period() -> tuple[date, str]:
    """A different past month for every call, so tests never share usage or final invoices."""
    first = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1)
    year, month = divmod(first.year * 12 + first.month - 1 - next(_MONTHS_BACK), 12)
    period = date(year, month + 1, 1)
    return period, calc.period_label(period)


@pytest.fixture(autouse=True)
async def clean_global_billing(env: AgentEnv) -> None:
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(text("DELETE FROM billing_settings"))
        await session.execute(text("DELETE FROM billing_llm_rates"))


def mid_month(period_start: date) -> datetime:
    return datetime(period_start.year, period_start.month, 10, 12, 0, tzinfo=timeutil.IST)


async def admin(env: AgentEnv) -> dict[str, str]:
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    return bearer(verified)


async def audit_rows(env: AgentEnv, pattern: str) -> list[Any]:
    async with session_scope(env.owner_factory, super_admin=True) as session:
        return list(
            (
                await session.execute(
                    text("SELECT action, new_value::text AS v FROM audit_log WHERE action LIKE :p"),
                    {"p": pattern},
                )
            ).all()
        )


async def second_tenant(env: AgentEnv) -> uuid.UUID:
    email = await active_user(env.client, env.mailer, env.factory)
    return await set_tenant_status(env.factory, email, "active")


async def add_llm(
    env: AgentEnv,
    tenant_id: uuid.UUID,
    provider: str,
    model: str,
    when: datetime,
    input_tokens: int,
    output_tokens: int,
    est_cost_micro: int | None = None,
    count: int = 1,
) -> None:
    async with session_scope(env.owner_factory, tenant_id=tenant_id) as session:
        for _ in range(count):
            await session.execute(
                text(
                    "INSERT INTO llm_calls (purpose, provider, model, input_tokens, output_tokens,"
                    " est_cost_micro, latency_ms, outcome, created_at) VALUES ('classify', :p, :m,"
                    " :i, :o, :c, 100, 'ok', :t)"
                ),
                {
                    "p": provider,
                    "m": model,
                    "i": input_tokens,
                    "o": output_tokens,
                    "c": est_cost_micro,
                    "t": when,
                },
            )


async def add_job(env: AgentEnv, tenant_id: uuid.UUID, when: datetime, milliseconds: int) -> None:
    async with session_scope(env.owner_factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO jobs (kind, status, started_at, finished_at, duration_ms)"
                " VALUES ('process_inbound', 'done', :s, :f, :d)"
            ),
            {"s": when, "f": when + timedelta(milliseconds=milliseconds), "d": milliseconds},
        )


async def add_voice_call(env: AgentEnv, tenant_id: uuid.UUID, when: datetime, seconds: int) -> None:
    async with session_scope(env.owner_factory, tenant_id=tenant_id) as session:
        customer_id = (
            await session.execute(
                text("INSERT INTO customers (tenant_id, phone_e164) VALUES (:t, :p) RETURNING id"),
                {"t": tenant_id, "p": f"+1{uuid.uuid4().int % 10**10:010d}"},
            )
        ).scalar_one()
        conversation_id = (
            await session.execute(
                text(
                    "INSERT INTO conversations (tenant_id, customer_id, channel)"
                    " VALUES (:t, :c, 'voice') RETURNING id"
                ),
                {"t": tenant_id, "c": customer_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO voice_calls (tenant_id, provider_call_id, conversation_id,"
                " customer_id, status, started_at, ended_at) VALUES (:t, :pc, :conv, :cust,"
                " 'completed', :s, :e)"
            ),
            {
                "t": tenant_id,
                "pc": str(uuid.uuid4()),
                "conv": conversation_id,
                "cust": customer_id,
                "s": when,
                "e": when + timedelta(seconds=seconds),
            },
        )


async def put(env: AgentEnv, headers: dict[str, str], path: str, body: Any) -> httpx.Response:
    return await env.client.put(f"/admin/billing{path}", json=body, headers=headers)


async def post(env: AgentEnv, headers: dict[str, str], path: str, body: Any) -> httpx.Response:
    return await env.client.post(f"/admin/billing{path}", json=body, headers=headers)


async def get(env: AgentEnv, headers: dict[str, str], path: str) -> httpx.Response:
    return await env.client.get(f"/admin/billing{path}", headers=headers)


async def prepare(
    env: AgentEnv,
    headers: dict[str, str],
    tenant_id: uuid.UUID,
    state: str = "06",
    fee: int = 500000,
    **seller: Any,
) -> None:
    assert (await put(env, headers, "/settings", {**SELLER, **seller})).status_code == 200
    profile = {
        "legal_name": "Brand Pvt Ltd",
        "address": "Delhi",
        "state_code": state,
        "gstin": "",
        "email": "accounts@brand.example",
        "default_platform_fee_paise": fee,
    }
    assert (await put(env, headers, f"/profiles/{tenant_id}", profile)).status_code == 200
    rate = {
        "provider": "anthropic",
        "model": "claude-x",
        "input_usd_per_million": "3",
        "output_usd_per_million": "15",
    }
    assert (await put(env, headers, "/llm-rates", rate)).status_code == 200


async def usage_for(env: AgentEnv, tenant_id: uuid.UUID, period_start: date) -> None:
    when = mid_month(period_start)
    await add_llm(env, tenant_id, "anthropic", "claude-x", when, 250_000, 50_000, count=4)
    await add_job(env, tenant_id, when, 3_600_000)


async def test_settings_roundtrip_validation_and_owner_blocked(env: AgentEnv) -> None:
    headers = await admin(env)
    default = (await get(env, headers, "/settings")).json()
    assert default["gst_enabled"] is True and default["gst_rate_percent"] == "18.00"
    assert default["invoice_prefix"] == "INV" and default["seller_name"] == ""
    saved = await put(env, headers, "/settings", SELLER)
    assert saved.status_code == 200 and saved.json()["seller_gstin"] == "06ABCDE1234F1Z5"
    again = (await get(env, headers, "/settings")).json()
    assert again["cloudrun_usd_per_vcpu_second"] == "0.0000240000"
    assert again["updated_at"] is not None
    for bad in (
        {"seller_gstin": "06ABCDE1234F1Z5", "seller_state_code": "07"},
        {"seller_gstin": "NOTAGSTIN"},
        {"seller_state_code": "25"},
        {"invoice_prefix": "TOOLONG"},
        {"gst_rate_percent": "101"},
        {"sac_code": "99ab"},
        {"unknown_field": 1},
    ):
        out = await put(env, headers, "/settings", {**SELLER, **bad})
        assert out.status_code == 422 and error_code(out) == "E-VAL-001", bad
    lowered = await put(env, headers, "/settings", {**SELLER, "seller_gstin": "06abcde1234f1z5"})
    assert lowered.json()["seller_gstin"] == "06ABCDE1234F1Z5"
    owner = await env.client.get("/admin/billing/settings", headers=env.wa.headers)
    assert owner.status_code in (401, 403)
    owner_put = await env.client.put("/admin/billing/settings", json=SELLER, headers=env.wa.headers)
    assert owner_put.status_code in (401, 403)
    audit = await audit_rows(env, "admin.billing_settings_changed")
    assert audit and all("Test Bank" not in a.v and "06ABCDE" not in a.v for a in audit)


async def test_rates_list_shows_models_that_still_need_a_price(env: AgentEnv) -> None:
    headers = await admin(env)
    period_start, _ = previous_period()
    await add_llm(env, env.wa.tenant_id, "openai", "gpt-z", mid_month(period_start), 10, 5)
    listed = (await get(env, headers, "/llm-rates")).json()
    assert {"provider": "openai", "model": "gpt-z"} in listed["unpriced"]
    saved = await put(
        env,
        headers,
        "/llm-rates",
        {
            "provider": "openai",
            "model": "gpt-z",
            "input_usd_per_million": "0.15",
            "output_usd_per_million": "0.6",
        },
    )
    assert saved.status_code == 200 and saved.json()["input_usd_per_million"] == "0.150000"
    listed = (await get(env, headers, "/llm-rates")).json()
    assert {"provider": "openai", "model": "gpt-z"} not in listed["unpriced"]
    assert any(r["model"] == "gpt-z" for r in listed["rates"])
    negative = await put(
        env,
        headers,
        "/llm-rates",
        {
            "provider": "openai",
            "model": "gpt-z",
            "input_usd_per_million": "-1",
            "output_usd_per_million": "1",
        },
    )
    assert negative.status_code == 422


async def test_profile_saves_default_fee_and_rejects_wrong_gstin(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    body = {
        "legal_name": "Brand Pvt Ltd",
        "address": "Delhi",
        "state_code": "07",
        "gstin": "07ABCDE1234F1Z5",
        "email": "a@b.example",
        "default_platform_fee_paise": 250000,
    }
    saved = await put(env, headers, f"/profiles/{tenant}", body)
    assert saved.status_code == 200 and saved.json()["default_platform_fee_paise"] == 250000
    wrong = await put(env, headers, f"/profiles/{tenant}", {**body, "gstin": "06ABCDE1234F1Z5"})
    assert wrong.status_code == 422
    unknown = await put(env, headers, f"/profiles/{uuid.uuid4()}", body)
    assert unknown.status_code == 404
    rows = (await get(env, headers, "/profiles")).json()
    mine = next(r for r in rows if r["tenant_id"] == str(tenant))
    assert mine["state_code"] == "07" and mine["tenant_name"]


async def test_generate_needs_usd_inr_rate_and_a_past_or_current_month(env: AgentEnv) -> None:
    headers = await admin(env)
    period_start, label = previous_period()
    blocked = await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": None})
    assert blocked.status_code == 422
    future = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1) + timedelta(days=40)
    future_label = calc.period_label(future.replace(day=1))
    assert (
        await put(env, headers, f"/periods/{future_label}", {"usd_inr": "88"})
    ).status_code == 422
    bad = await get(env, headers, "/periods/2026-13")
    assert bad.status_code == 422
    zero = await put(env, headers, f"/periods/{label}", {"usd_inr": "0"})
    assert zero.status_code == 422
    assert period_start.day == 1


async def test_full_invoice_flow_same_state_cgst_sgst_then_lock(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    period_start, label = previous_period()
    await prepare(env, headers, tenant)
    await usage_for(env, tenant, period_start)
    saved = await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    assert saved.status_code == 200
    row = next(r for r in saved.json()["rows"] if r["tenant_id"] == str(tenant))
    assert (
        row["calls"] == 4 and row["input_tokens"] == 1_000_000 and row["output_tokens"] == 200_000
    )
    assert row["llm_paise"] == 52800 and row["cloudrun_paise"] == 800 and row["jobs"] == 1
    assert row["compute_seconds"] == 3600 and row["invoice"] is None

    generated = await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": None})
    assert generated.status_code == 200
    mine = [s for s in generated.json()["invoices"] if s["tenant_id"] == str(tenant)]
    assert len(mine) == 1
    invoice_id = mine[0]["id"]
    draft = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    assert draft["status"] == "draft" and draft["invoice_no"] is None
    assert draft["llm_paise"] == 52800 and draft["cloudrun_paise"] == 800
    assert draft["platform_fee_paise"] == 500000 and draft["subtotal_paise"] == 553600
    assert draft["gst_kind"] == "intra" and draft["cgst_paise"] == 49824
    assert draft["sgst_paise"] == 49824 and draft["igst_paise"] == 0
    assert draft["total_paise"] == 653248 and draft["doc_type"] == "tax_invoice"
    assert draft["blockers"] == [] and draft["cloudrun_basis"] == "estimate"
    kinds = [line["kind"] for line in draft["lines"]]
    assert kinds == ["llm", "cloudrun", "platform_fee"]
    llm_line = draft["lines"][0]
    assert llm_line["quantity"] == "4.0000" and llm_line["unit"] == "calls"
    assert (
        llm_line["detail"]["input_tokens"] == 1_000_000 and llm_line["detail"]["source"] == "rate"
    )
    assert sum(line["amount_paise"] for line in draft["lines"]) == draft["subtotal_paise"]

    changed = await put(env, headers, f"/invoices/{invoice_id}/fee", {"platform_fee_paise": 600000})
    assert changed.status_code == 200
    assert changed.json()["subtotal_paise"] == 653600 and changed.json()["total_paise"] == 771248
    again = await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": None})
    assert any(s["id"] == invoice_id for s in again.json()["invoices"])
    kept = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    assert kept["platform_fee_paise"] == 600000

    stale = await post(
        env, headers, f"/invoices/{invoice_id}/finalize", {"expected_total_paise": 653248}
    )
    assert stale.status_code == 409 and error_code(stale) == "E-CONF-001"
    still_draft = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    assert still_draft["status"] == "draft" and still_draft["invoice_no"] is None

    final = await post(
        env, headers, f"/invoices/{invoice_id}/finalize", {"expected_total_paise": 771248}
    )
    assert final.status_code == 200, final.text
    body = final.json()
    today = timeutil.to_ist(timeutil.utc_now()).date()
    assert body["status"] == "final"
    assert body["invoice_no"].startswith(f"INV/{calc.fiscal_year(today)}/")
    assert len(body["invoice_no"]) == 16
    assert body["invoice_date"] == today.isoformat() and body["finalized_at"] is not None
    assert body["seller"]["gstin"] == "06ABCDE1234F1Z5" and body["buyer"]["name"] == "Brand Pvt Ltd"
    assert body["blockers"] == [] and body["total_paise"] == 771248

    await put(env, headers, "/settings", {**SELLER, "seller_name": "Changed Name"})
    snapshot = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    assert snapshot["seller"]["name"] == "Vikas AI Services"

    assert (
        await put(env, headers, f"/invoices/{invoice_id}/fee", {"platform_fee_paise": 1})
    ).status_code == 409
    assert (
        await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)})
    ).status_code == 409
    skipped = await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": None})
    assert skipped.json()["skipped_final"] == 1
    assert all(s["id"] != invoice_id for s in skipped.json()["invoices"])
    assert (await put(env, headers, f"/periods/{label}", {"usd_inr": "90"})).status_code == 409
    twice = await post(
        env, headers, f"/invoices/{invoice_id}/finalize", {"expected_total_paise": 771248}
    )
    assert twice.status_code == 409

    async with session_scope(env.owner_factory, super_admin=True) as session:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text("UPDATE invoices SET llm_paise = 0"))
    async with session_scope(env.owner_factory, super_admin=True) as session:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text("DELETE FROM invoice_lines"))
    async with session_scope(env.owner_factory, super_admin=True) as session:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text("DELETE FROM invoices"))

    audit = await audit_rows(env, "admin.invoice_%")
    actions = {a.action for a in audit}
    assert {
        "admin.invoice_generated",
        "admin.invoice_fee_changed",
        "admin.invoice_finalized",
    } <= actions


async def test_other_state_gets_igst_and_numbers_run_in_sequence(env: AgentEnv) -> None:
    headers = await admin(env)
    other = await second_tenant(env)
    period_start, label = previous_period()
    await prepare(env, headers, env.wa.tenant_id, state="06")
    assert (
        await put(
            env,
            headers,
            f"/profiles/{other}",
            {
                "legal_name": "Far Brand",
                "address": "Mumbai",
                "state_code": "27",
                "gstin": "",
                "email": "",
                "default_platform_fee_paise": 100000,
            },
        )
    ).status_code == 200
    await usage_for(env, env.wa.tenant_id, period_start)
    await usage_for(env, other, period_start)
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    generated = (
        await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": None})
    ).json()
    mine_ids = {str(env.wa.tenant_id), str(other)}
    own = [s for s in generated["invoices"] if s["tenant_id"] in mine_ids]
    assert generated["generated"] >= 2 and len(own) == 2
    totals = {}
    for summary in own:
        inv = (await get(env, headers, f"/invoices/{summary['id']}")).json()
        totals[inv["tenant_id"]] = inv
    far = totals[str(other)]
    assert far["gst_kind"] == "inter" and far["cgst_paise"] == 0 and far["sgst_paise"] == 0
    assert far["subtotal_paise"] == 52800 + 800 + 100000
    assert far["igst_paise"] == 27648 and far["total_paise"] == far["subtotal_paise"] + 27648
    near = totals[str(env.wa.tenant_id)]
    first = await post(
        env,
        headers,
        f"/invoices/{near['id']}/finalize",
        {"expected_total_paise": near["total_paise"]},
    )
    second = await post(
        env,
        headers,
        f"/invoices/{far['id']}/finalize",
        {"expected_total_paise": far["total_paise"]},
    )
    assert first.status_code == 200 and second.status_code == 200
    fy = calc.fiscal_year(timeutil.to_ist(timeutil.utc_now()).date())
    one, two = first.json()["invoice_no"], second.json()["invoice_no"]
    assert one.startswith(f"INV/{fy}/") and two.startswith(f"INV/{fy}/")
    assert int(two.rsplit("/", 1)[1]) == int(one.rsplit("/", 1)[1]) + 1
    assert len(one) == 16 and one != two


async def test_actual_google_bill_is_split_by_job_time_and_adds_up(env: AgentEnv) -> None:
    headers = await admin(env)
    other = await second_tenant(env)
    period_start, label = previous_period()
    await prepare(env, headers, env.wa.tenant_id, fee=0)
    when = mid_month(period_start)
    await add_job(env, env.wa.tenant_id, when, 2_000_000)
    await add_job(env, other, when, 1_000_000)
    saved = await put(
        env, headers, f"/periods/{label}", {"usd_inr": "88", "cloudrun_actual_paise": 10001}
    )
    assert saved.status_code == 200 and saved.json()["cloudrun_unallocated_paise"] == 0
    rows = {r["tenant_id"]: r for r in saved.json()["rows"]}
    mine, theirs = rows[str(env.wa.tenant_id)], rows[str(other)]
    assert mine["cloudrun_paise"] == 6667 and theirs["cloudrun_paise"] == 3334
    assert mine["cloudrun_paise"] + theirs["cloudrun_paise"] == 10001
    generated = await post(
        env, headers, "/invoices/generate", {"period": label, "tenant_id": str(env.wa.tenant_id)}
    )
    invoice = (await get(env, headers, f"/invoices/{generated.json()['invoices'][0]['id']}")).json()
    assert invoice["cloudrun_basis"] == "actual" and invoice["cloudrun_paise"] == 6667
    line = next(line for line in invoice["lines"] if line["kind"] == "cloudrun")
    assert line["detail"]["basis"] == "actual" and line["detail"]["share_percent"] == "66.67"
    assert line["detail"]["rate_missing"] is False


async def test_actual_bill_with_no_compute_is_reported_as_unallocated(env: AgentEnv) -> None:
    headers = await admin(env)
    _, label = previous_period()
    out = await put(
        env, headers, f"/periods/{label}", {"usd_inr": "88", "cloudrun_actual_paise": 5000}
    )
    assert out.json()["cloudrun_unallocated_paise"] == 5000
    cleared = await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    assert cleared.json()["cloudrun_actual_paise"] is None
    assert cleared.json()["cloudrun_unallocated_paise"] == 0


async def test_telephony_bill_is_split_by_call_seconds_and_adds_up(env: AgentEnv) -> None:
    headers = await admin(env)
    other = await second_tenant(env)
    period_start, label = previous_period()
    await prepare(env, headers, env.wa.tenant_id, fee=0)
    when = mid_month(period_start)
    await add_voice_call(env, env.wa.tenant_id, when, 120)
    await add_voice_call(env, other, when, 60)
    saved = await put(
        env, headers, f"/periods/{label}", {"usd_inr": "88", "telephony_actual_paise": 9000}
    )
    assert saved.status_code == 200 and saved.json()["telephony_unallocated_paise"] == 0
    rows = {r["tenant_id"]: r for r in saved.json()["rows"]}
    mine, theirs = rows[str(env.wa.tenant_id)], rows[str(other)]
    assert mine["voice_calls"] == 1 and mine["voice_call_seconds"] == 120
    assert theirs["voice_calls"] == 1 and theirs["voice_call_seconds"] == 60
    assert mine["telephony_paise"] == 6000 and theirs["telephony_paise"] == 3000
    assert mine["telephony_paise"] + theirs["telephony_paise"] == 9000
    generated = await post(
        env, headers, "/invoices/generate", {"period": label, "tenant_id": str(env.wa.tenant_id)}
    )
    invoice = (await get(env, headers, f"/invoices/{generated.json()['invoices'][0]['id']}")).json()
    assert invoice["telephony_paise"] == 6000
    assert invoice["subtotal_paise"] == invoice["llm_paise"] + invoice["cloudrun_paise"] + 6000
    line = next(line for line in invoice["lines"] if line["kind"] == "telephony")
    assert line["detail"]["seconds"] == 120 and line["detail"]["calls"] == 1
    assert line["detail"]["share_percent"] == "66.67"
    assert line["unit"] == "minutes" and line["quantity"] == "2.0000"


async def test_telephony_bill_with_no_calls_is_reported_as_unallocated(env: AgentEnv) -> None:
    headers = await admin(env)
    _, label = previous_period()
    out = await put(
        env, headers, f"/periods/{label}", {"usd_inr": "88", "telephony_actual_paise": 4000}
    )
    assert out.json()["telephony_unallocated_paise"] == 4000
    cleared = await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    assert cleared.json()["telephony_actual_paise"] is None
    assert cleared.json()["telephony_unallocated_paise"] == 0


async def test_finalize_is_blocked_until_rates_and_details_are_complete(env: AgentEnv) -> None:
    headers = await admin(env)
    period_start, label = previous_period()
    tenant = env.wa.tenant_id
    await put(
        env,
        headers,
        "/settings",
        {**SELLER, "cloudrun_usd_per_vcpu_second": "0", "cloudrun_usd_per_gib_second": "0"},
    )
    await put(
        env,
        headers,
        f"/profiles/{tenant}",
        {
            "legal_name": "",
            "address": "",
            "state_code": "",
            "gstin": "",
            "email": "",
            "default_platform_fee_paise": 0,
        },
    )
    await add_llm(env, tenant, "anthropic", "claude-unpriced", mid_month(period_start), 1000, 100)
    await add_job(env, tenant, mid_month(period_start), 60_000)
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    generated = await post(
        env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)}
    )
    invoice_id = generated.json()["invoices"][0]["id"]
    draft = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    codes = {b["code"] for b in draft["blockers"]}
    assert {
        "llm_rate_missing",
        "cloudrun_rate_missing",
        "buyer_state_missing",
        "empty_invoice",
    } <= codes
    missing = next(b for b in draft["blockers"] if b["code"] == "llm_rate_missing")
    assert missing["detail"] == "anthropic/claude-unpriced"
    assert {"buyer_address_missing", "platform_fee_zero"} <= {w["code"] for w in draft["warnings"]}
    refused = await post(
        env,
        headers,
        f"/invoices/{invoice_id}/finalize",
        {"expected_total_paise": draft["total_paise"]},
    )
    assert refused.status_code == 422 and error_code(refused) == "E-VAL-001"

    await put(
        env,
        headers,
        "/llm-rates",
        {
            "provider": "anthropic",
            "model": "claude-unpriced",
            "input_usd_per_million": "1",
            "output_usd_per_million": "5",
        },
    )
    await put(env, headers, "/settings", SELLER)
    await put(
        env,
        headers,
        f"/profiles/{tenant}",
        {
            "legal_name": "",
            "address": "Delhi",
            "state_code": "07",
            "gstin": "",
            "email": "",
            "default_platform_fee_paise": 1000,
        },
    )
    await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)})
    ready = (await get(env, headers, f"/invoices/{invoice_id}")).json()
    assert ready["blockers"] == [] and ready["buyer"]["name"]
    ok = await post(
        env,
        headers,
        f"/invoices/{invoice_id}/finalize",
        {"expected_total_paise": ready["total_paise"]},
    )
    assert ok.status_code == 200 and ok.json()["gst_kind"] == "inter"


async def test_running_month_can_be_previewed_but_not_finalized(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    current = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1)
    label = calc.period_label(current)
    await prepare(env, headers, tenant)
    await add_llm(env, tenant, "anthropic", "claude-x", timeutil.utc_now(), 100, 10)
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    overview = (await get(env, headers, f"/periods/{label}")).json()
    assert overview["period_ended"] is False
    generated = await post(
        env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)}
    )
    invoice = (await get(env, headers, f"/invoices/{generated.json()['invoices'][0]['id']}")).json()
    assert "period_running" in {b["code"] for b in invoice["blockers"]}
    refused = await post(
        env,
        headers,
        f"/invoices/{invoice['id']}/finalize",
        {"expected_total_paise": invoice["total_paise"]},
    )
    assert refused.status_code == 422


async def test_logged_cost_is_used_when_no_billing_rate_exists(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    period_start, label = previous_period()
    await prepare(env, headers, tenant, fee=0)
    await add_llm(
        env,
        tenant,
        "groq",
        "logged-model",
        mid_month(period_start),
        10,
        10,
        est_cost_micro=1_500_000,
    )
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    overview = (await get(env, headers, f"/periods/{label}")).json()
    row = next(r for r in overview["rows"] if r["tenant_id"] == str(tenant))
    assert row["llm_paise"] == 13200 and row["unpriced_models"] == []


async def test_period_overview_without_fx_hides_money_but_keeps_usage(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    period_start, label = previous_period()
    await prepare(env, headers, tenant)
    await usage_for(env, tenant, period_start)
    overview = (await get(env, headers, f"/periods/{label}")).json()
    row = next(r for r in overview["rows"] if r["tenant_id"] == str(tenant))
    assert overview["usd_inr"] is None and row["calls"] == 4
    assert row["llm_paise"] is None and row["cloudrun_paise"] is None


async def test_gst_off_gives_a_plain_invoice_and_no_gstin_means_proforma(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    period_start, label = previous_period()
    await prepare(env, headers, tenant, seller_gstin="", gst_enabled=False)
    await usage_for(env, tenant, period_start)
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    generated = await post(
        env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)}
    )
    invoice = (await get(env, headers, f"/invoices/{generated.json()['invoices'][0]['id']}")).json()
    assert invoice["gst_kind"] == "none" and invoice["total_paise"] == invoice["subtotal_paise"]
    assert invoice["doc_type"] == "proforma" and invoice["gst_rate_percent"] == "0.00"
    assert invoice["blockers"] == []
    await put(env, headers, "/settings", {**SELLER, "seller_gstin": ""})
    again = (await get(env, headers, f"/invoices/{invoice['id']}")).json()
    assert "gst_without_gstin" in {w["code"] for w in again["warnings"]}


async def test_brand_users_cannot_read_billing_tables_even_by_rls(env: AgentEnv) -> None:
    headers = await admin(env)
    tenant = env.wa.tenant_id
    period_start, label = previous_period()
    await prepare(env, headers, tenant)
    await usage_for(env, tenant, period_start)
    await put(env, headers, f"/periods/{label}", {"usd_inr": "88"})
    await post(env, headers, "/invoices/generate", {"period": label, "tenant_id": str(tenant)})
    for path in ("/settings", "/llm-rates", "/profiles", f"/periods/{label}"):
        denied = await env.client.get(f"/admin/billing{path}", headers=env.wa.headers)
        assert denied.status_code in (401, 403), path
    async with session_scope(env.factory, tenant_id=tenant) as session:
        for table in (
            "invoices",
            "invoice_lines",
            "tenant_billing_profiles",
            "billing_settings",
            "billing_llm_rates",
            "billing_periods",
            "invoice_counters",
        ):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO billing_periods (period_start, usd_inr) VALUES ('2020-01-01', 1)"
                    )
                )
