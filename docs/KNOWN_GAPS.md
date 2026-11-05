# Known gaps and undocumented behaviours

Source: `Agent_Decision_Handoff_Reference.docx` (compiled November 2026), Section 3
and Section 4. These are real, verified behaviours (traced against the actual code
in this repo) that a new developer - or you, six months from now - would have no
way to discover without reading every source file. The complete, current
handoff-reason table itself was folded into `docs/TDD_V1.md` Section 7, replacing
the outdated one there; this file holds the items that existed only in code, with
no documentation anywhere, before November 2026.

## 1. One request per message only

A single customer message can only carry ONE actionable request (cancel, refund,
return, exchange, address-change, tracking). If the customer asks for two different
things at once ("cancel karo aur refund bhi"), the planner cannot split them - it
hands the whole message to a human instead of processing either part.

**Source:** `app/prompts/plan.v1.md`, line 32.

## 2. No lookup by phone/name alone

The only order-lookup function in the codebase requires an explicit order number
(`order_lookup(order_ref, phone, email)`). There is no "find all orders for this
phone number" tool. A customer who refuses to give an order number will always be
asked again - the agent cannot look it up another way.

**Source:** `app/agent/tools.py`.

## 3. Conversation memory resets when a conversation is Resolved

Chat history and the remembered order number (`active_order_ref`) are scoped to the
current still-open conversation row only. The moment a conversation is marked
Resolved (by a human, from the dashboard), the next message from that customer
starts a brand-new, empty conversation - no memory of yesterday's exchange, even
though the customer remembers it.

**Source:** `app/agent/runner.py` - `get_or_create_conversation()`.

## 4. WhatsApp and Email are not merged

Each channel gets its own separate conversation row for the same customer
(conversation lookup is filtered by channel). This does not match the originally
stated requirement that WhatsApp and email context should merge for the same
customer.

**Source:** `app/channels/identity.py` - `get_or_create_conversation()`.

## 5. Reply language is now brand-configurable (was hardcoded English)

Until 2026-11, whatever language the customer wrote in (Hindi, Hinglish, English),
the agent's reply always went out in English by a hardcoded constant
(`pick_language()` ignored the detected language). That was intentional and
consistent, but a real product risk for Indian D2C brands whose end-customers write
in Hindi/Hinglish - see `Agent_Reply_TopClass_Notes.md` Section 3b.

**Fixed:** `pick_language()` now takes the brand's own `PolicyV2.reply_language`
(Dashboard -> Policy page), default `"en"` so existing behaviour is unchanged until
a brand opts in. The reply-writing prompt (`reply.v1.md`) was updated to match: it
no longer hardcodes English, it writes in whatever language is handed to it via
`<language>`.

**Source:** `app/agent/validate.py::pick_language()`, `app/v1/policy.py::PolicyV2`.

## 6. "Same question repeated" detection exists only in the old V0 path

V0 (`agent/graph.py`) escalates to a human if the same order number comes up again
in the conversation (`REPEAT_COMPLAINT`). V1 (the current main planner) only has an
equivalent check for write-action claims (3 claims in 24h - `CLAIM_CLUSTERING`). A
customer who just repeats a plain read-only question ("order kahan hai" asked 5
times) gets no special handling in V1 - the agent answers fresh each time.

**Source:** `app/agent/graph.py` (has it) vs `app/v1/graph.py` (does not).

## 7. Reply empathy line (fixed 2026-11)

Until 2026-11, the reply-writing prompt gave the LLM only facts to report; it had no
rule telling it to open with a short "I'm sorry about the delay" when the customer
describes a problem. Nothing blocked such a line (it isn't a banned phrase) - the
LLM simply wasn't told to include it.

**Fixed:** `reply.v1.md` now has an explicit rule: begin with one short empathetic
line, in the reply's own language, when the customer describes a delay, problem or
frustration (not a plain status question); no apology when they haven't described a
problem. This sits alongside the existing 6-checkpoint reply pipeline unchanged
(facts-only generation, banned-phrase filter, internal-jargon filter, fact-matching
validator, length cap, language lock) - see `Agent_Reply_TopClass_Notes.md`.

**Source:** `backend/app/prompts/reply.v1.md`.

## 8. AI-disclosure line is brand-customizable, but this wasn't documented anywhere

The opening "I'm an AI assistant..." line is not hardcoded - each brand can set its
own wording (including its own name) from Dashboard -> Policy -> AI Disclosure. No
doc told a new brand owner that this field exists or what it's for, until now.

**Source:** `frontend/src/pages/Policy.tsx`, line ~108-111.

## 9. SaaS-scale gap: usage limits don't stop service

Confirmed in `app/api/admin_billing.py`: the `usage_limits` table (built in P23) only
powers an admin alert when a tenant crosses 70/90/100% of their monthly cap. It does
not pause the agent, block new conversations, or notify the brand's own customers.
For a startup still proving the product, alert-only is a reasonable default - but as
a billing-enforcement feature it is incomplete, and should not be assumed to work as
a hard limit until `QUOTA_EXCEEDED` (see `docs/TDD_V1.md` Section 7, last row) is
actually built.
