from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.agent.policy_engine import (
    IST,
    OrderForPolicy,
    check_return_eligibility,
    refund_within_limits,
    within_office_hours,
)
from app.evals.dataset import code_subset_passed
from app.evals.policy_scenarios import SCENARIOS as TABLE_SCENARIOS
from app.v1.policy import AnyPolicy

REFERENCE_NOW = datetime(2026, 3, 10, 12, 0, tzinfo=IST)


@dataclass(frozen=True)
class RegressionResult:
    total: int
    failed: list[str]
    eval_subset_passed: bool = True

    @property
    def passed(self) -> bool:
        return not self.failed and self.eval_subset_passed


Scenario = tuple[str, Callable[[], bool]]


def _delivered(days_ago: int, tags: tuple[str, ...] = ()) -> OrderForPolicy:
    return OrderForPolicy(delivered_at=REFERENCE_NOW - timedelta(days=days_ago), tags=tags)


def _eligibility(policy: AnyPolicy, order: OrderForPolicy) -> tuple[str, str]:
    result = check_return_eligibility(
        order, REFERENCE_NOW, policy.return_window_days, policy.final_sale_tags
    )
    return result.decision, result.reason


def build_scenarios(policy: AnyPolicy) -> list[Scenario]:
    window = policy.return_window_days
    scenarios: list[Scenario] = [
        (
            "return_on_last_day_eligible",
            lambda: _eligibility(policy, _delivered(window)) == ("eligible", "WITHIN_WINDOW"),
        ),
        (
            "return_day_after_window_rejected",
            lambda: (
                _eligibility(policy, _delivered(window + 1)) == ("not_eligible", "WINDOW_EXPIRED")
            ),
        ),
        (
            "return_on_delivery_day_eligible",
            lambda: _eligibility(policy, _delivered(0))[0] == "eligible",
        ),
        (
            "return_unknown_delivery_date_needs_human",
            lambda: _eligibility(policy, OrderForPolicy(delivered_at=None))[0] == "needs_human",
        ),
        (
            "return_undelivered_rejected",
            lambda: (
                _eligibility(
                    policy, OrderForPolicy(delivered_at=None, fulfillment_status="unfulfilled")
                )
                == ("not_eligible", "NOT_DELIVERED")
            ),
        ),
        (
            "return_already_refunded_rejected",
            lambda: (
                _eligibility(
                    policy, OrderForPolicy(delivered_at=REFERENCE_NOW, financial_status="refunded")
                )
                == ("not_eligible", "ALREADY_REFUNDED")
            ),
        ),
    ]
    for tag in policy.final_sale_tags[:20]:
        scenarios.append(
            (
                f"final_sale_tag_{tag}",
                lambda tag=tag: (
                    _eligibility(policy, _delivered(0, (tag.upper(),)))  # type: ignore[misc]
                    == ("not_eligible", "FINAL_SALE")
                ),
            )
        )
    auto, daily = policy.refund_auto_limit_paise, policy.refund_customer_daily_limit_paise
    if auto is None or daily is None:
        scenarios.append(
            ("refund_off_when_limits_unset", lambda: not refund_within_limits(100, 0, auto, daily))
        )
    else:
        scenarios += [
            ("refund_at_limit_allowed", lambda: refund_within_limits(auto, 0, auto, daily)),
            (
                "refund_above_limit_rejected",
                lambda: not refund_within_limits(auto + 1, 0, auto, daily),
            ),
            (
                "refund_daily_cap_rejected",
                lambda: not refund_within_limits(1, daily, auto, daily),
            ),
            ("refund_zero_rejected", lambda: not refund_within_limits(0, 0, auto, daily)),
        ]
    hours = policy.office_hours
    day = hours.days[0]
    monday = datetime(2026, 1, 5, tzinfo=IST)
    base = monday + timedelta(days=day)
    start_h, start_m = (int(x) for x in hours.start.split(":"))
    end_h, end_m = (int(x) for x in hours.end.split(":"))
    start_at = base.replace(hour=start_h, minute=start_m)
    end_at = base.replace(hour=end_h, minute=end_m)
    scenarios += [
        (
            "office_hours_start_inclusive",
            lambda: within_office_hours(start_at, hours.start, hours.end, hours.days),
        ),
        (
            "office_hours_end_exclusive",
            lambda: not within_office_hours(end_at, hours.start, hours.end, hours.days),
        ),
    ]
    off_days = [d for d in range(7) if d not in hours.days]
    if off_days:
        off_at = (monday + timedelta(days=off_days[0])).replace(hour=start_h, minute=start_m)
        scenarios.append(
            (
                "office_hours_off_day_closed",
                lambda: not within_office_hours(off_at, hours.start, hours.end, hours.days),
            )
        )
    scenarios += [
        ("handoff_tool_enabled", lambda: "handoff_to_human" in policy.enabled_tools),
        (
            "ai_disclosure_present",
            lambda: all(
                len(text.strip()) >= 5
                for text in (
                    policy.ai_disclosure.hi,
                    policy.ai_disclosure.en,
                    policy.ai_disclosure.hinglish,
                )
            ),
        ),
        (
            "caps_consistent",
            lambda: policy.customer_daily_msg_cap <= policy.brand_daily_msg_cap,
        ),
    ]
    scenarios += [(f"table_{item.name}", item.check) for item in TABLE_SCENARIOS]
    return scenarios


def run_regression(policy: AnyPolicy) -> RegressionResult:
    scenarios = build_scenarios(policy)
    failed: list[str] = []
    for name, check in scenarios:
        try:
            ok = check()
        except Exception:
            ok = False
        if not ok:
            failed.append(name)
    return RegressionResult(
        total=len(scenarios), failed=failed, eval_subset_passed=code_subset_passed()
    )
