import pytest
from pydantic import ValidationError

from app.contracts.contracts import PolicyV1
from app.v1.contracts import (
    HANDOFF_ORDER_V1,
    V1_NEW_REASONS,
    AddressIn,
    Plan,
    PlanArgs,
    ReplyDraft,
)
from app.v1.policy import Limits, PolicyV2, parse_policy, raised_levels, upgrade


def test_plan_ignores_llm_supplied_amount_and_unknown_fields() -> None:
    plan = Plan.model_validate(
        {
            "intent": "refund",
            "action": "propose",
            "tool": "refund_create",
            "args": {"order_ref": "#1001", "amount_paise": 99999999, "amount": "99999"},
            "confidence": 0.9,
            "amount_paise": 123,
        }
    )
    dumped = plan.model_dump()
    assert "amount_paise" not in dumped and "amount_paise" not in dumped["args"]


def test_plan_propose_needs_tool_and_order_ref() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate({"intent": "cancel", "action": "propose", "confidence": 0.9})
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {"intent": "cancel", "action": "propose", "tool": "order_cancel", "confidence": 0.9}
        )


def test_plan_secondary_answers_defaults_empty_and_drops_duplicates_of_primary() -> None:
    # Section 5 Phase 1: no secondary_answers at all is the common case and must
    # keep working exactly as before (default []).
    plan = Plan.model_validate({"intent": "order_status", "action": "answer", "confidence": 0.9})
    assert plan.secondary_answers == []

    # The LLM is untrusted even producing our own schema: a secondary entry that
    # repeats the primary intent, or that repeats itself, must not survive -
    # there is nothing to "add" by answering the same thing twice.
    plan = Plan.model_validate(
        {
            "intent": "tracking",
            "action": "answer",
            "confidence": 0.9,
            "secondary_answers": ["tracking", "policy_question", "policy_question"],
        }
    )
    assert plan.secondary_answers == ["policy_question"]


def test_plan_secondary_answers_rejects_write_intents() -> None:
    # Only the three read-only kinds are valid here - a write intent sneaking
    # into secondary_answers would mean v1/graph.py silently skipping a real
    # action, so this must fail validation, not be dropped quietly.
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "intent": "order_status",
                "action": "answer",
                "confidence": 0.9,
                "secondary_answers": ["cancel"],
            }
        )


def test_plan_tool_only_with_propose() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "intent": "cancel",
                "action": "answer",
                "tool": "order_cancel",
                "args": {"order_ref": "#1"},
                "confidence": 0.9,
            }
        )


def test_plan_rejects_non_registry_tool() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "intent": "other",
                "action": "propose",
                "tool": "delete_database",
                "args": {"order_ref": "#1"},
                "confidence": 0.9,
            }
        )


def test_exchange_and_address_need_their_args() -> None:
    base = {"intent": "exchange", "action": "propose", "confidence": 0.8}
    with pytest.raises(ValidationError):
        Plan.model_validate({**base, "tool": "exchange_request", "args": {"order_ref": "#1"}})
    ok = Plan.model_validate(
        {
            **base,
            "tool": "exchange_request",
            "args": {"order_ref": "#1", "line_item": "Kurta", "new_variant": "L"},
        }
    )
    assert ok.args.new_variant == "L"
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "intent": "address_change",
                "action": "propose",
                "tool": "address_update",
                "args": {"order_ref": "#1", "new_address": {"line1": "x"}},
                "confidence": 0.8,
            }
        )


def test_confidence_percent_and_sentiment_clamped() -> None:
    plan = Plan.model_validate(
        {"intent": "other", "action": "answer", "confidence": 85, "sentiment": -7}
    )
    assert plan.confidence == 0.85 and plan.sentiment == -1.0


def test_address_pin_validation() -> None:
    AddressIn(line1="12 MG Road", city="Pune", state="MH", pin="411001")
    for pin in ("011001", "41100", "4110011", "abcdef"):
        with pytest.raises(ValidationError):
            AddressIn(line1="12 MG Road", city="Pune", state="MH", pin=pin)


def test_plan_args_drop_garbage_address() -> None:
    assert PlanArgs.model_validate({"new_address": {"line1": "x"}}).new_address is None


def test_reply_draft_unknown_promise_rejected() -> None:
    with pytest.raises(ValidationError):
        ReplyDraft.model_validate({"text": "ok", "promises": ["free_gift"]})
    draft = ReplyDraft.model_validate({"text": " ok ", "promises": ["refund_initiated"]})
    assert draft.text == "ok" and draft.language == "hinglish"


def test_new_reason_codes_are_ordered_and_distinct() -> None:
    assert len(set(V1_NEW_REASONS)) == len(V1_NEW_REASONS) == 11
    assert HANDOFF_ORDER_V1.index("ANGRY") < HANDOFF_ORDER_V1.index("CONFUSED")
    assert HANDOFF_ORDER_V1.index("THREAT") < HANDOFF_ORDER_V1.index("STEP_LIMIT")


def test_policy_v2_defaults_have_no_auto_and_no_write_tools() -> None:
    policy = PolicyV2()
    assert policy.schema_version == 2
    for tool in ("address_update", "order_cancel", "exchange_request", "refund_create"):
        assert policy.write_level(tool) == "off"
    assert policy.refund_auto_limit_paise is None
    assert policy.limits.refund_auto_limit_paise == 50_000
    assert policy.confirm_ttl_minutes == 30 and policy.max_tool_steps == 4


def test_policy_v2_level_requires_enabled_tool() -> None:
    with pytest.raises(ValidationError):
        PolicyV2.model_validate({"autonomy": {"order_cancel": "approval"}})
    ok = PolicyV2.model_validate(
        {
            "autonomy": {"order_cancel": "approval"},
            "enabled_tools": ["handoff_to_human", "order_cancel"],
        }
    )
    assert ok.write_level("order_cancel") == "approval"


def test_policy_v2_strict_and_limit_order() -> None:
    with pytest.raises(ValidationError):
        PolicyV2.model_validate({"surprise": 1})
    with pytest.raises(ValidationError):
        Limits(refund_auto_limit_paise=60_000, refund_customer_daily_paise=50_000)
    with pytest.raises(ValidationError):
        Limits(brand_daily_auto_value_paise=1000)
    with pytest.raises(ValidationError):
        PolicyV2.model_validate({"max_tool_steps": 5})


def test_autonomy_rejects_auto_order_status_typos() -> None:
    with pytest.raises(ValidationError):
        PolicyV2.model_validate({"autonomy": {"order_status": "off"}})
    with pytest.raises(ValidationError):
        PolicyV2.model_validate({"autonomy": {"refund_create": "yolo"}})


def test_v1_content_upgrades_without_enabling_writes() -> None:
    old = PolicyV1(refund_auto_limit_paise=30_000, refund_customer_daily_limit_paise=60_000)
    new = upgrade(old)
    assert new.schema_version == 2
    assert new.limits.refund_auto_limit_paise == 30_000
    assert new.limits.refund_customer_daily_paise == 60_000
    assert new.write_level("refund_create") == "off"
    assert new.return_window_days == old.return_window_days


def test_parse_policy_dispatches_on_schema_version() -> None:
    assert parse_policy(PolicyV1().model_dump(mode="json")).schema_version == 2
    assert parse_policy(PolicyV2().model_dump(mode="json")).schema_version == 2


def test_raised_levels_only_flags_new_auto() -> None:
    published = PolicyV2.model_validate(
        {
            "autonomy": {"order_cancel": "auto"},
            "enabled_tools": ["handoff_to_human", "order_cancel", "refund_create"],
        }
    )
    draft = PolicyV2.model_validate(
        {
            "autonomy": {"order_cancel": "auto", "refund_create": "auto"},
            "enabled_tools": ["handoff_to_human", "order_cancel", "refund_create"],
        }
    )
    assert raised_levels(draft, published) == ["refund_create"]
    assert raised_levels(draft, None) == ["order_cancel", "refund_create"]
    assert raised_levels(published, published) == []
