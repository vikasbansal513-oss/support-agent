from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.v1.gate import evaluate
from app.v1.policy import PolicyV2
from tests.v1.test_gate import NOW, TOOLS, ctx, order


def _policy(**extra: object) -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "enabled_tools": TOOLS,
            "autonomy": {t: "approval" for t in TOOLS if t != "handoff_to_human"},
            **extra,
        }
    )


def _delivered(days_ago: int, tags: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    return order(
        fulfillment_status="fulfilled", delivered_at=NOW - timedelta(days=days_ago), tags=tags
    )


def test_default_window_unchanged_when_no_tag_windows() -> None:
    p = _policy()
    assert p.return_window_for(("shoes",)) == 7
    assert evaluate(p, ctx("return_request", _delivered(7))).route == "approval"
    assert evaluate(p, ctx("return_request", _delivered(8))).reason == "WINDOW_EXPIRED"


def test_tagged_order_uses_its_own_window() -> None:
    p = _policy(return_window_by_tag={"Shoes": 15, "sale": 3})
    assert p.return_window_by_tag == {"shoes": 15, "sale": 3}
    assert evaluate(p, ctx("return_request", _delivered(12, ("shoes",)))).route == "approval"
    assert (
        evaluate(p, ctx("return_request", _delivered(12, ("shoes", "sale")))).reason
        == "WINDOW_EXPIRED"
    )
    assert evaluate(p, ctx("return_request", _delivered(5, ("other",)))).route == "approval"
    assert evaluate(p, ctx("return_request", _delivered(8, ("other",)))).reason == "WINDOW_EXPIRED"


def test_strictest_matching_window_wins() -> None:
    p = _policy(return_window_by_tag={"shoes": 15, "sale": 3})
    assert p.return_window_for(["SHOES", "sale"]) == 3


def test_validation() -> None:
    for bad in ({"shoes": 0}, {"shoes": 61}, {"": 5}):
        with pytest.raises(ValidationError):
            _policy(return_window_by_tag=bad)
