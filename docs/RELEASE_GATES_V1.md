# V1 release gates (TDD V1 section 10)

Auto level kisi tool ke liye tab hi chalu hota hai jab ye saare gate pass hon. Threshold ASSUMPTION hain.

## Offline gates (CI me har commit par: `python -m app.evals.run`)

| Gate | Kaise napa jata hai | Target |
| --- | --- | --- |
| Galat paisa wala auto action | `gate_scenarios` (224 scenario, har tool 50+): jahan expected auto nahi par gate ne auto diya | 0 |
| Gate scenarios | har scenario ka (route, reason) exact match | 100% |
| Jhootha wada leak | `v1_safety` reply cases (43): bura reply jo validator se nikal gaya | 0 |
| Confirm parser | 73 haan/nahi/unclear cases | 100% |
| Plan contract | 23 cases: tool/amount/address/extra field | 100% |
| Multi-turn | `tests/v1/test_multiturn_eval.py`: 150 conversations, 3 bhasha, 4 tool, auto/approval/no/unclear/gate | 100% |
| Chaos | `tests/v1/test_chaos_v1.py` + `test_writes.py`: duplicate webhook, race, timeout, partial, 5xx | 100% |
| Injection | `app/evals/injection.py` | 100% |

## Real data gates (VERIFY: abhi nape nahi gaye)

| Gate | Kab | Target |
| --- | --- | --- |
| `--llm` eval (tuning split), plan accuracy aur gusse wale cases me handoff recall | real LLM key ke saath | recall 100%, accuracy 90% |
| Final split | prompt tuning band hone par sirf ek baar | wahi |
| Shadow me us tool ke kam se kam 100 asli proposals ka insaani verdict | brand ke saath pilot | precision 99% ya zyada |
| Pilot shadow me galat paisa wala action | pilot | 0 |

## Rollout kram

1. address_update aur order_cancel: shadow, phir approval, phir auto.
2. exchange_request.
3. refund_create (sabse aakhir me).

Auto sirf platform admin chalu karta hai (`PUT /admin/tenants/{id}/autonomy`); tenant mode auto hona zaroori.
