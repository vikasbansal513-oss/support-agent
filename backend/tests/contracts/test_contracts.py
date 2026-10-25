import json, pytest
from pydantic import ValidationError
from app.contracts.contracts import *

def test_classification_clean():
    c = Classification.model_validate({"intent":"tracking","order_ref":"#1001","confidence":85,"language":"Hindi?","extra":1})
    assert c.confidence == 0.85 and c.language == "hinglish" and c.order_ref == "#1001"

def test_classification_bad_order_ref_dropped():
    assert Classification(intent="other", confidence=.5, order_ref="ignore previous; DROP").order_ref is None
    assert Classification(intent="other", confidence=.5, order_ref="abc").order_ref is None

def test_classification_bad_intent():
    with pytest.raises(ValidationError): Classification(intent="refund", confidence=.9)

def test_classification_conf_range():
    with pytest.raises(ValidationError): Classification(intent="other", confidence=150)

def test_mixed_filtered():
    assert Classification(intent="other", confidence=.5, mixed_intents=["tracking","hack"]).mixed_intents == ["tracking"]

def test_policy_defaults_and_roundtrip():
    p = PolicyV1()
    assert PolicyV1.model_validate(json.loads(p.model_dump_json())) == p
    assert p.refund_auto_limit_paise is None

def test_policy_refund_pair():
    with pytest.raises(ValidationError): PolicyV1(refund_auto_limit_paise=50000)
    with pytest.raises(ValidationError): PolicyV1(refund_auto_limit_paise=50000, refund_customer_daily_limit_paise=1000)
    PolicyV1(refund_auto_limit_paise=50000, refund_customer_daily_limit_paise=100000)

def test_policy_office_hours():
    with pytest.raises(ValidationError): OfficeHours(start="21:00", end="09:00")
    with pytest.raises(ValidationError): OfficeHours(start="9am")
    with pytest.raises(ValidationError): OfficeHours(days=[7])

def test_policy_tools_handoff_required():
    with pytest.raises(ValidationError): PolicyV1(enabled_tools=["order_lookup"])

def test_policy_extra_forbidden():
    with pytest.raises(ValidationError): PolicyV1(foo=1)

def test_decision_shapes():
    h = HandoffSummary(customer_asked="x", reason_code="ANGRY", why_stuck="y")
    DecisionOut(action="handoff", reason_code="ANGRY", handoff=h)
    with pytest.raises(ValidationError): DecisionOut(action="handoff")
    with pytest.raises(ValidationError): DecisionOut(action="reply")
    with pytest.raises(ValidationError): DecisionOut(action="skip", handoff=h)

def test_toolresult():
    with pytest.raises(ValidationError): ToolResult(tool="order_lookup", ok=True, error_code="not_found")
    ToolResult(tool="order_lookup", ok=False, error_code="not_found")

def test_inbound():
    now = "2026-10-24T10:00:00+05:30"
    NormalizedInbound(channel="whatsapp", external_id="a", from_phone="+919876543210", received_at=now)
    with pytest.raises(ValidationError): NormalizedInbound(channel="whatsapp", external_id="a", from_phone="9876543210", received_at=now)
    with pytest.raises(ValidationError): NormalizedInbound(channel="whatsapp", external_id="a", from_phone="+919876543210", received_at="2026-10-24T10:00:00")

def test_api_inputs():
    with pytest.raises(ValidationError): SignupIn(brand_name="A", name="n", email="bad", password="x"*10)
    with pytest.raises(ValidationError): ModeIn(mode="auto")
    with pytest.raises(ValidationError): ShopifyIn(shop_domain="evil.com", access_token="x"*12)
    ShopifyIn(shop_domain="shoe-brand.myshopify.com", access_token="shpat_"+"a"*20)
    with pytest.raises(ValidationError): VerdictIn(verdict="maybe")

def test_endpoints_refs_resolve():
    names = set(globals())
    import re
    for e in ENDPOINTS:
        for t in filter(None, [e["body"]] + re.findall(r"[A-Z][A-Za-z]+", e["returns"])):
            if t in ("Page","StatusOut","MfaChallengeOut","LoginOut","ADMIN","JWT","SECRET","X","CSRF","Token","PolicyOut","CSRF"): pass
    bodies = {e["body"] for e in ENDPOINTS if e["body"]}
    assert all(b in names for b in bodies), [b for b in bodies if b not in names]
    keys = [(e["method"], e["path"]) for e in ENDPOINTS]
    assert len(keys) == len(set(keys))

def test_reason_codes_in_sync():
    from typing import get_args
    assert set(REASON_CODES) == set(get_args(ReasonCode))
    assert URGENT_REASONS <= set(REASON_CODES)
