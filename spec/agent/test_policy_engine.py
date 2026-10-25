from datetime import datetime, timezone, timedelta
import pytest
from policy_engine import *

IST_ = IST
def d(y, m, dd, h=12, mi=0, tz=IST_): return datetime(y, m, dd, h, mi, tzinfo=tz)
def O(deliv, tags=(), fin="paid", ful="fulfilled"): return OrderForPolicy(deliv, tuple(tags), fin, ful)

# (name, order, now, window, final_tags, expected_decision, expected_reason)
R = [
 ("day0",            O(d(2026,10,1)), d(2026,10,1,20), 7, [], "eligible", "WITHIN_WINDOW"),
 ("day6",            O(d(2026,10,1)), d(2026,10,7),    7, [], "eligible", "WITHIN_WINDOW"),
 ("day7_boundary",   O(d(2026,10,1)), d(2026,10,8,23,59), 7, [], "eligible", "WITHIN_WINDOW"),
 ("day8_expired",    O(d(2026,10,1)), d(2026,10,9,0,1), 7, [], "not_eligible", "WINDOW_EXPIRED"),
 ("late_night_deliv_early_morning_day7", O(d(2026,10,1,23,50)), d(2026,10,8,0,10), 7, [], "eligible", "WITHIN_WINDOW"),
 ("utc_late_is_next_ist_day", O(datetime(2026,10,1,19,0,tzinfo=timezone.utc)), datetime(2026,10,9,5,0,tzinfo=timezone.utc), 7, [], "eligible", "WITHIN_WINDOW"),
 ("utc_late_expired_by_one", O(datetime(2026,10,1,5,0,tzinfo=timezone.utc)), datetime(2026,10,8,19,0,tzinfo=timezone.utc), 7, [], "not_eligible", "WINDOW_EXPIRED"),
 ("window1_day1",    O(d(2026,10,1)), d(2026,10,2),    1, [], "eligible", "WITHIN_WINDOW"),
 ("window1_day2",    O(d(2026,10,1)), d(2026,10,3),    1, [], "not_eligible", "WINDOW_EXPIRED"),
 ("window60_day60",  O(d(2026,8,1)),  d(2026,9,30),    60, [], "eligible", "WITHIN_WINDOW"),
 ("window60_day61",  O(d(2026,8,1)),  d(2026,10,1),    60, [], "not_eligible", "WINDOW_EXPIRED"),
 ("window30_day30",  O(d(2026,9,1)),  d(2026,10,1),    30, [], "eligible", "WITHIN_WINDOW"),
 ("window30_day31",  O(d(2026,9,1)),  d(2026,10,2),    30, [], "not_eligible", "WINDOW_EXPIRED"),
 ("final_sale",      O(d(2026,10,1), ["final sale"]), d(2026,10,2), 7, ["final sale"], "not_eligible", "FINAL_SALE"),
 ("final_sale_case", O(d(2026,10,1), ["FINAL SALE"]), d(2026,10,2), 7, ["final sale"], "not_eligible", "FINAL_SALE"),
 ("final_sale_space",O(d(2026,10,1), ["final   sale"]), d(2026,10,2), 7, ["Final Sale"], "not_eligible", "FINAL_SALE"),
 ("final_sale_other_tag", O(d(2026,10,1), ["sale"]), d(2026,10,2), 7, ["final sale"], "eligible", "WITHIN_WINDOW"),
 ("final_sale_multi",O(d(2026,10,1), ["new","clearance"]), d(2026,10,2), 7, ["final sale","clearance"], "not_eligible", "FINAL_SALE"),
 ("final_sale_empty_list", O(d(2026,10,1), ["final sale"]), d(2026,10,2), 7, [], "eligible", "WITHIN_WINDOW"),
 ("final_beats_expired", O(d(2026,9,1), ["final sale"]), d(2026,10,2), 7, ["final sale"], "not_eligible", "FINAL_SALE"),
 ("refunded",        O(d(2026,10,1), fin="refunded"), d(2026,10,2), 7, [], "not_eligible", "ALREADY_REFUNDED"),
 ("voided",          O(d(2026,10,1), fin="voided"),   d(2026,10,2), 7, [], "not_eligible", "ALREADY_REFUNDED"),
 ("refunded_beats_final", O(d(2026,10,1), ["final sale"], fin="refunded"), d(2026,10,2), 7, ["final sale"], "not_eligible", "ALREADY_REFUNDED"),
 ("unfulfilled",     O(None, ful="unfulfilled"), d(2026,10,2), 7, [], "not_eligible", "NOT_DELIVERED"),
 ("unfulfilled_with_date", O(d(2026,10,1), ful="unfulfilled"), d(2026,10,2), 7, [], "not_eligible", "NOT_DELIVERED"),
 ("restocked",       O(None, ful="restocked"), d(2026,10,2), 7, [], "not_eligible", "NOT_DELIVERED"),
 ("fulfilled_no_date", O(None), d(2026,10,2), 7, [], "needs_human", "DELIVERY_DATE_UNKNOWN"),
 ("partial_no_date", O(None, ful="partial"), d(2026,10,2), 7, [], "needs_human", "DELIVERY_DATE_UNKNOWN"),
 ("partial_in_window", O(d(2026,10,1), ful="partial"), d(2026,10,3), 7, [], "eligible", "WITHIN_WINDOW"),
 ("future_delivery", O(d(2026,10,5)), d(2026,10,2), 7, [], "needs_human", "DELIVERY_IN_FUTURE"),
 ("pending_payment_fulfilled", O(d(2026,10,1), fin="pending"), d(2026,10,2), 7, [], "eligible", "WITHIN_WINDOW"),
 ("partially_refunded_ok", O(d(2026,10,1), fin="partially_refunded"), d(2026,10,2), 7, [], "eligible", "WITHIN_WINDOW"),
 ("year_boundary",   O(d(2026,12,28)), d(2027,1,4), 7, [], "eligible", "WITHIN_WINDOW"),
 ("year_boundary_exp", O(d(2026,12,28)), d(2027,1,5), 7, [], "not_eligible", "WINDOW_EXPIRED"),
 ("leap_day",        O(d(2028,2,27)), d(2028,3,5), 7, [], "eligible", "WITHIN_WINDOW"),
 ("leap_day_exp",    O(d(2028,2,27)), d(2028,3,6), 7, [], "not_eligible", "WINDOW_EXPIRED"),
]

@pytest.mark.parametrize("name,order,now,win,tags,dec,reason", R, ids=[r[0] for r in R])
def test_return(name, order, now, win, tags, dec, reason):
    e = check_return_eligibility(order, now, win, tags)
    assert (e.decision, e.reason) == (dec, reason)

def test_naive_rejected():
    with pytest.raises(ValueError): check_return_eligibility(O(datetime(2026,10,1)), d(2026,10,2), 7, [])

# office hours: (name, now, start, end, days, expected)
H = [
 ("open_exact",   d(2026,10,26,9,0),   "09:00","21:00",[0,1,2,3,4,5,6], True),
 ("before_open",  d(2026,10,26,8,59),  "09:00","21:00",[0,1,2,3,4,5,6], False),
 ("close_exact",  d(2026,10,26,21,0),  "09:00","21:00",[0,1,2,3,4,5,6], False),
 ("last_minute",  d(2026,10,26,20,59), "09:00","21:00",[0,1,2,3,4,5,6], True),
 ("sunday_off",   d(2026,10,25,12,0),  "09:00","21:00",[0,1,2,3,4,5],   False),
 ("monday_on",    d(2026,10,26,12,0),  "09:00","21:00",[0,1,2,3,4,5],   True),
 ("utc_input_converted", datetime(2026,10,26,3,30,tzinfo=timezone.utc), "09:00","21:00",[0], True),
 ("utc_input_before_open", datetime(2026,10,26,3,29,tzinfo=timezone.utc), "09:00","21:00",[0], False),
 ("utc_sunday_is_monday_ist", datetime(2026,10,25,19,0,tzinfo=timezone.utc), "00:00","23:59",[0], True),
 ("seconds_ignored", d(2026,10,26,20,59).replace(second=59), "09:00","21:00",[0], True),
]
@pytest.mark.parametrize("name,now,s,e,days,exp", H, ids=[h[0] for h in H])
def test_hours(name, now, s, e, days, exp): assert within_office_hours(now, s, e, days) is exp

def test_refund_limits():
    assert refund_within_limits(40000, 0, 50000, 100000)
    assert not refund_within_limits(50001, 0, 50000, 100000)
    assert not refund_within_limits(40000, 70000, 50000, 100000)
    assert not refund_within_limits(40000, 0, None, None)
    assert not refund_within_limits(40000, 0, 50000, None)
    assert not refund_within_limits(0, 0, 50000, 100000)
