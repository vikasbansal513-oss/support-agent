# CLAUDE.md (project niyam)

Ye repo AI Customer Support Agent ka hai. Design: docs/TDD_V0.docx (V0) aur docs/TDD_V1.md (V1 autonomous). Spec pack: spec/. Source of truth: spec/ > TDD V1 > TDD V0.

## Kaam ka tareeka
- Kaam parts me (BUILD_PROMPT.md ka P0 se P16), ek ke baad ek. Ek time par ek part.
- Mujhse (Vikas) kuch mat poochho. Khud faisla lo, DECISIONS.md me ek line kaaran ke saath.
- Part khatam: tests pass, ruff, mypy clean, PROGRESS.md update, parts/partNN_naam.zip (sirf is part ki files, har file poori).
- Chat me summary 3 se 5 line. Poori files chat me paste mat karo.
- Purane parts ka behavior mat bigaado.

## Stack
Python 3.13 (ek hi version; constraints.txt me pinned).
FastAPI, SQLAlchemy 2 async + asyncpg, Pydantic v2, LangGraph (pinned), Postgres RLS (Neon), Cloud Run + Run Job + Scheduler, GitHub Actions + Workload Identity, Secret Manager. Frontend React + Vite + TypeScript on Vercel.

## Code niyam
- Type hints, ruff, mypy, har module ke tests. Asli Postgres par DB tests.
- Koi placeholder ya TODO nahi. Koi demo code nahi. Comments sirf zaroori jagah.
- Paisa integer paise ya Decimal, float kabhi nahi.
- Koi secret, token ya customer data logs, fixtures ya git me nahi.
- Har tenant table par RLS ENABLE + FORCE, app code me bhi tenant_id filter (do parat).
- Naya tenant table bina RLS ke bana to test fail.

## Agent niyam
- LLM koi tool nahi chalata. Sirf validated JSON (Pydantic). Tools code chalata hai.
- Customer text aur order notes prompt me hamesha wrap_untrusted() me, pehle redact_for_llm().
- Graph me DB commit ya send nahi. Send actions engine karta hai (claim, act, confirm).
- Amount, order id, items order facts se, LLM se kabhi nahi.
- AGENT_RECURSION_LIMIT=25; graph badalne par dobara test.
- Jo verify nahi hua (Shopify write API, Gupshup asli callback, LLM accuracy): interface + fake client + DECISIONS.md me VERIFY. Asli API shape invent mat karo.
