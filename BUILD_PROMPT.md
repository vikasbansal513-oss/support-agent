Tum 15 saal ke senior backend + full-stack developer ho. Mujhe AI Customer Support Agent ka poora project end-to-end chahiye, production quality me. Neeche ke niyam sakht hain.

REPO ME YE FILES HAIN
- docs/TDD_V0.docx (V0 design), docs/TDD_V1.md (V1 design: autonomous agent)
- spec/ (V0 ka spec pack: db/schema.sql, contracts/, agent/, channels/, prompts/, evals/, frontend/)
- CLAUDE.md, PROGRESS.md, DECISIONS.md
Pehle CLAUDE.md, PROGRESS.md aur dono TDD padho. TDD_V0.docx padhne ke liye: pandoc docs/TDD_V0.docx -t plain --wrap=none. Jahan TDD aur spec/ me farak ho, spec/ jeetega. Jahan V0 aur V1 me farak ho, V1 jeetega. Spec ki files app/ me copy karo, badlo mat; unke tests app ke tests me shamil karo.

STACK (badalna mat)
FastAPI, SQLAlchemy 2 async + asyncpg, Pydantic v2, LangGraph (version pinned), Postgres (Neon pooled) with RLS, Cloud Run service + Cloud Run Job + Cloud Scheduler, GitHub Actions + Workload Identity, Secret Manager. Frontend: React + Vite + TypeScript, Vercel par deploy (backend Google Cloud Run par). Python version ek hi rakho aur constraints.txt me pin karo (TDD V0 me 3.11 aur spec README me 3.13 likha hai: ek chuno aur DECISIONS.md me likho).

KAAM KA TAREEKA (sabse zaroori)
1. Kaam PARTS me hoga, ek ke baad ek. Ek time par sirf ek part. Do part kabhi ek saath mat banao.
2. Mujhse kuch mat poochho. Har faisla khud lo, aur DECISIONS.md me ek line me kaaran ke saath likho. Ruko mat.
3. Ek part complete hone par (code likha + tests pass + ruff + mypy clean) turant agla part shuru karo.
4. Har part ke end me PROGRESS.md update karo: kaun sa part done, kaun sa baaki, kya verify karna baaki hai. Context reset ho to PROGRESS.md se wahin se aage badho.
5. Token bachao: poori files chat me paste mat karo, disk par likho. Naya part shuru karne se pehle sirf wahi files padho jo us part ko chahiye, poora repo dobara mat padho.
6. Har part ke end me ek zip banao: parts/partNN_naam.zip. Zip me sirf is part me bani ya badli files hon, har file POORI (sirf badli hui lines nahi). Chat me sirf 3 se 5 line ka summary: kya bana, tests ka natija, kya baaki.
7. Purane parts ka behavior mat bigaado. Purani file badalni pade to poori file zip me do aur DECISIONS.md me kaaran likho.

CODE KA STANDARD
- Type hints, ruff aur mypy clean, har module ke tests, asli Postgres par DB tests.
- Koi placeholder, TODO, "apna logic yahan likho" nahi. Jo likho wo chalna chahiye.
- Comments sirf jahan zaroori. Koi example/demo code nahi.
- Koi secret, token ya customer ka data logs, fixtures ya git me nahi. .env.example me sirf naam.
- Paisa sirf integer paise ya Decimal, float kabhi nahi.
- Jo cheez verify nahi hui (Shopify write API shapes, Gupshup asli callback, LLM accuracy), use interface ke peeche rakho, fake client + tests banao, aur DECISIONS.md me VERIFY likho. Andaza lagakar asli API ka shape invent mat karo.
- Asli keys na ho to kaam mat roko: fake clients aur env placeholders se aage badho.

PARTS KA KRAM
P0 Bootstrap: repo, pyproject, constraints, Dockerfile, ci.yml, deploy.yml skeleton, CLAUDE.md
P1 DB neev: core/*, migration 0001, RLS helpers aur tests, errors, logging, crypto, audit
P2 Auth aur admin: migration 0002, auth, MFA, rate limits, admin tenants
P3 Integrations aur policy: migrations 0003-0004, Shopify read client, courier clients, policy API + versions
P4 Channels: migrations 0005-0006, webhooks, inbound_events, jobs queue + worker, Gmail fetch
P5 Agent (V0 TDD ke hisaab se, par graph aur state aise likho ki V1 me extend ho): migration 0007, llm layer, nodes, tools, templates, policy_engine, actions engine, handoff
P6 Dashboard aur shadow: APIs, frontend screens, shadow review
P7 Evals aur safety: eval set, policy scenarios, run.py, safety suite
P8 Ops aur privacy: deploy.yml poora, Scheduler, monitoring, runbook, retention, consent
P9 V1 spec: PolicyV2, Plan aur ReplyDraft contracts, migrations 0008-0012, RLS tests
P10 Shopify write clients: address_update, order_cancel, exchange_request, refund_create + fake Shopify
P11 Action engine V1: state machine, confirm_token, action_usage limits, approvals API, kill switch, anomaly stop
P12 Planner graph: conversation_state, plan validation, step aur write limits, naye reason codes
P13 Reply LLM: reply.v1.md, validate_reply, template fallback, sentiment
P14 Dashboard V1: Approvals screen, autonomy settings, action ledger, kill switch UI
P15 V1 evals: policy scenarios, multi-turn evals, adversarial, chaos, release gates
P16 Rollout: runbook, rollback drill, final README

V1 KE NIYAM JO KABHI NAHI TOOTENGE
- LLM koi tool nahi chalata, sirf validated JSON Plan deta hai. Tools code chalata hai.
- Amount, order id, items hamesha Shopify order facts se, LLM se kabhi nahi.
- Har write action: customer confirm, policy engine, claim-act-confirm (unique idempotency claim), phir Shopify, phir order dobara padh kar check.
- "Ho gaya" jaisa wada tabhi jab action confirmed ho.
- Gussa, samajh na aana, limit ya policy ke bahar, tool fail: insaan ko handoff.
- Naye brand par saare write tools off ya approval par, auto sirf admin chalu kare.

Ab PROGRESS.md dekho. Jo part baaki hai wahin se shuru karo (pehli baar P0). Aakhri part P16 tak bina ruke chalo, har part ek ke baad ek.
