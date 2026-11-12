# Change report - handoff only for unclear intent or autonomy off (2026-11-12)

Problem: a return/refund email on an unshipped order (Approval level) went to a human handoff.

## New behaviour (`app/v1/gate.py::resolve`, used by the graph and by regate)
- Reply to the customer, no handoff ("decline"): NOT_DELIVERED, WINDOW_EXPIRED, ORDER_CLOSED,
  ALREADY_REFUNDED, RETURN_EXISTS, EXCHANGE_EXISTS.
- Send to Approvals, after every hard check has passed: HIGH_VALUE_ORDER, ORDER_DISPUTED,
  RECENT_REFUND, DELIVERY_DATE_UNKNOWN.
- Still handoff (unchanged): TOOL_OFF, TOOL_KILLED, CLAIM_CLUSTERING (fraud), WRITE_LIMIT,
  ALREADY_SHIPPED, ADDRESS_ALREADY_CHANGED, FINANCIAL_STATUS, FINAL_SALE, DELIVERY_IN_FUTURE,
  plus every non-gate handoff (unclear intent, anger, LLM failure, media, etc.).
- `evaluate()` is untouched (gate evals, voice_safety and old tests still use the raw rules).
- At confirm time (`regate`), a decline becomes a handoff, because the customer already said yes
  and the order changed underneath.

## Files
- `backend/app/v1/gate.py`, `regate.py`, `graph.py`, `templates.py` (4 `decline_*` templates in
  en/hinglish/hi + `DECLINE_TEMPLATE`).
- Tests: `tests/v1/test_gate.py` (resolve tests), `test_flow_v1.py`, `test_multiturn_eval.py`
  (two tests rewritten for the new behaviour).

## Not verified (nothing was executed)
Only ruff check/format ran on these files. pytest, mypy and `python -m app.evals.run` were not run.

## Return auto limit (same day)
- New `limits.return_auto_limit_paise` (default Rs 2000, like cancel/exchange) in `app/v1/policy.py`;
  old saved policies get the default automatically. Owner cannot raise it while return is `auto`
  (added to `limits_raised_under_auto`, same rule as the other limits).
- `app/v1/gate.py::_within_auto`: a return on `auto` level stays auto only if the ORDER total is
  within the limit, otherwise it goes to Approvals (`LIMIT_EXCEEDED`). Return amount stays 0, so it
  is not counted in the brand daily auto value and no other flow changes.
- Frontend: new "Return auto limit (₹)" box on Autonomy page (`Autonomy.tsx`, `types.ts`, `i18n.ts`).
- Behaviour change to know: a brand that already had Return on `auto` now needs approval for
  orders above Rs 2000 until it sets this limit.
- Not run: pytest, mypy, `tsc`/vitest (frontend node_modules not installed here).

## Email format for decline replies
On email the four `decline_*` replies now use the same layout as the other email replies
(greeting, text, order summary table) via `EMAIL_T` built from `T` in `app/v1/templates.py`;
`graph.py` passes `greeting`/`details` for them. WhatsApp and voice use the plain text.

## Chats page (Conversations.tsx, styles.css)
Page no longer scrolls: title+filters in one row, left list / open chat messages / customer panel
scroll on their own, reply box pinned under the messages, latest message shown on open.

## Deploy order
1. Backend first (new `limits.return_auto_limit_paise` must be known to the API before the
   dashboard saves it; the model forbids unknown fields). 2. Then the frontend (Vercel).
Do not roll the backend back after a policy was saved with the new field. No DB migration.

## Not changed / not checked
- `docs/TDD_V1.md` section 7 (handoff table) still lists the old handoff reasons for the
  cases that now reply or go to Approvals.
- Nothing was executed: no pytest, mypy, evals, tsc or vitest.
