# LLM provider terms (FR-AI-11, FR-PRV-11)

Provider: Anthropic, one platform key in Secret Manager. Model pinned by `LLM_MODEL`.

What is sent: the customer's message after `redact_for_llm()` (phone, email, card and UPI patterns removed) wrapped in `wrap_untrusted()`, plus order facts needed to classify. No secrets, no brand tokens, no raw contact details.

Confirm in writing before any real customer traffic (VERIFY, owner: Vikas):
1. No training on API inputs or outputs under the account's commercial terms.
2. Retention: zero-data-retention arrangement if available for the account; otherwise the maximum retention period must be recorded here and be no longer than the 30-day conversation retention.
3. Data location and sub-processors acceptable for Indian customer data; add the provider to the privacy notice.
4. Incident notification terms.

Status: NOT CONFIRMED. Until confirmed, tenants stay in shadow mode with test data only.
