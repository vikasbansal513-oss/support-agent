from app.contracts.contracts import PolicyV1
from app.policy.regression import build_scenarios, run_regression
from app.policy.service import content_hash


def test_default_policy_passes() -> None:
    result = run_regression(PolicyV1())
    assert result.passed and result.total >= 10


def test_refund_and_final_sale_scenarios_included() -> None:
    policy = PolicyV1(
        final_sale_tags=["Clearance"],
        refund_auto_limit_paise=50000,
        refund_customer_daily_limit_paise=100000,
        office_hours={"start": "10:00", "end": "18:00", "days": [0, 1, 2]},
    )
    names = [name for name, _ in build_scenarios(policy)]
    assert "final_sale_tag_clearance" in names and "refund_daily_cap_rejected" in names
    assert "office_hours_off_day_closed" in names
    assert run_regression(policy).passed


def test_failing_scenario_reported(monkeypatch: object) -> None:
    import app.policy.regression as module

    original = module.build_scenarios

    def broken(policy: PolicyV1) -> list[tuple[str, object]]:
        return [*original(policy), ("boom", lambda: 1 / 0 > 0), ("no", lambda: False)]

    monkeypatch.setattr(module, "build_scenarios", broken)  # type: ignore[attr-defined]
    result = module.run_regression(PolicyV1())
    assert result.failed == ["boom", "no"] and not result.passed


def test_content_hash_stable_and_sensitive() -> None:
    assert content_hash(PolicyV1()) == content_hash(PolicyV1())
    assert content_hash(PolicyV1()) != content_hash(PolicyV1(return_window_days=8))
