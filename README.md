# AI Customer Support Agent

WhatsApp, email aur phone call (voice) par brand ke customers ko jawab dene wala multi-tenant agent. V0 jawab deta hai (order status, tracking, policy). V1 order par kaam bhi karta hai: address badalna, cancel, exchange, refund; har kaam customer ki "haan" ke baad, policy gate se, aur brand ki marzi ke level par (`off`, `approval`, `auto`).

## Niyam jo kabhi nahi tootte
- LLM koi tool nahi chalata; wo sirf validated JSON (`Plan`, `ReplyDraft`) deta hai. Tools code chalata hai.
- Amount, order id aur items hamesha Shopify order facts se aate hain, LLM se kabhi nahi.
- Har write: customer confirm, policy gate, claim (unique idempotency key), Shopify, order dobara padh kar check. "Ho gaya" tabhi jab action `confirmed` ho.
- Gussa, samajh na aana, limit ya policy ke bahar, tool fail: insaan ko handoff.
- Naye brand par write tools `off` ya `approval`; `auto` sirf platform admin chalu karta hai.
- Customer text hamesha `redact_for_llm()` ke baad `wrap_untrusted()` me. Logs me koi secret ya customer data nahi.

## Stack
Python 3.13, FastAPI, SQLAlchemy 2 async + asyncpg, Pydantic v2, LangGraph, Postgres with RLS (ENABLE + FORCE, aur app code me bhi tenant_id filter). Cloud Run (API), Cloud Run Job + Scheduler (worker), GitHub Actions + Workload Identity, Secret Manager. Frontend: React + Vite + TypeScript on Vercel.

## Layout
- `backend/app`: `api/` routes, `agent/` V0 graph aur runner, `v1/` plan/gate/confirm/reply/graph, `actions/` write engine, `integrations/` Shopify, couriers, WhatsApp, Gmail, `jobs/` worker jobs, `evals/` offline evals aur release gates, `contracts/` spec (badalna mat).
- `backend/migrations`: Alembic, forward-only (0001 se 0012).
- `frontend/src`: dashboard (conversations, handoffs, review, approvals, autonomy, policy, integrations, privacy, admin).
- `docs/`: TDD_V0, TDD_V1, RUNBOOK, RELEASE_GATES_V1, RELEASE_TARGETS, LLM_PROVIDER_TERMS.
- `infra/monitoring`: alert policies. `PROGRESS.md`, `DECISIONS.md`: build ki sthiti aur har faisla (kaaran ke saath).

## Chalana
Backend (Postgres 16 chahiye):
```
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt ".[dev]"
cp ../.env.example .env   # DATABASE_URL, MIGRATION_DATABASE_URL, ENCRYPTION_KEY, JWT secrets
alembic upgrade head
uvicorn app.main:app          # API
python -m app.worker          # worker
pytest -q && ruff check . && mypy .
python -m app.evals.run       # offline evals + release gates
```
Frontend:
```
cd frontend
npm ci && npm test && npx tsc --noEmit && npm run build
```

## Deploy aur ops
`docs/RUNBOOK.md`: setup, deploy, rollback levers, emergency stop, alerts, backup, retention. Rollout ka kram aur gates: `docs/RELEASE_GATES_V1.md`.

## Abhi verify nahi hua (auto chalu karne se pehle zaroori)
- Shopify write API shapes aur scopes (interface + `FakeShopify` hai, asli client nahi; default `DisabledShopifyWriter`).
- Shopify read fields: delivery date, refund transactions, dispute tags.
- Gupshup asli callback aur send API, Gmail send, Brevo mail API.
- Asli LLM par plan accuracy, handoff recall aur reply quality (`python -m app.evals.run --llm` key ke saath).
- LLM provider ki data terms (docs/LLM_PROVIDER_TERMS.md), privacy notice ki legal review.
- Staging par deploy.yml, Scheduler aur alert policies ka pehla run.
Poori list: `DECISIONS.md` me "VERIFY" lines.
