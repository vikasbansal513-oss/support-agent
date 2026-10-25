# AI Support Agent V1 Technical Design (Autonomous)

Date: 2026-10-24. Author: Vikas Bansal. Ye document TDD V0 v1.1 (docs/TDD_V0.docx) ke upar likha hai. Jahan V0 aur V1 me farak ho, V1 jeetega. spec/ pack V0 ke liye hai; V1 ke naye contracts V1-S0 me banenge.

## 1. Scope: V0 se kya badalta hai

V1 me agent shadow se nikal kar **autonomous** banta hai: poori conversation sambhalta hai, data dekhta hai, order par action leta hai, aur samajh na aaye ya customer gusse me ho to insaan ko de deta hai. V0 ki safety neev (tenant isolation, audit, idempotency, injection guard, claim-act-confirm) jyon ki tyon rehti hai.

| | V0 (abhi) | V1 (ye document) |
| --- | --- | --- |
| Conversation | Ek message, ek jawab | Multi-turn: confirm, follow-up, kai step ek message me |
| LLM ka kaam | Sirf classify (JSON) | Plan banata hai (JSON, validated) + jawab ka text likhta hai |
| Jawab ka text | Sirf templates | LLM free-text, facts tool se, validator ke saath. Templates fallback me |
| Actions | Order lookup, tracking, handoff | + cancel, refund, exchange, address change |
| Paisa/order badalna | Koi tool nahi | Limit tak khud, uske upar insaan ki manzoori |
| Default mode | Shadow | Har action ka apna level: off, approval, auto |

**V1 me nahi:** COD refund (bank transfer manual), voice, website chat, billing/Razorpay plans, WooCommerce. Ye V2+ me.

**Zaroori niyam jo nahi tootega:** LLM ke paas koi tool nahi. LLM sirf *proposal* deta hai; paisa, order id aur amount code order facts se nikalta hai, LLM se kabhi nahi.

## 2. Faisle (Decisions)

Sab 4 actions autonomous, limit tak khud aur upar insaan, LLM free-text reply, aur samajh na aane ya gusse par handoff. ASSUMPTION = shuruaati faisla; VERIFY = banate waqt check karna hai.

| # | Faisla | Kyun | Status |
| --- | --- | --- | --- |
| E1 | LLM tool nahi chalata. Wo `Plan` JSON deta hai (action + args), Pydantic se validate; tool code chalata hai. | Prompt injection se paisa nahi nikal sakta. V0 ka niyam (7.1) wahi. | Tay |
| E2 | Har action ek **risk tier** me (T0 read, T1 address, T2 cancel/exchange, T3 refund). Har tier ka level per brand: off, approval, auto. | Ek brand ek tool pehle approval par, baad me auto. | Tay |
| E3 | Auto ki hadd policy me paise me: `cancel_auto_limit_paise`, `refund_auto_limit_paise`, customer daily limit, brand daily limit. Default refund limit Rs 500. | Vikas ne "limit tak khud" chuna. | Default ASSUMPTION |
| E4 | Koi bhi write se pehle customer se **confirm** ("Order #1234 cancel kar dun? haan likhein"). Confirm DB me action se bandha. | Galti se ya injection se action nahi. | Tay |
| E5 | Amount, order id, items hamesha Shopify order facts se. LLM ka diya amount kabhi nahi. | FR-AI-08 aur injection. | Tay |
| E6 | Jawab LLM likhta hai, par `validate_reply` har number, date, order id tool_results se milata hai. Do baar fail to V0 template. | Natural baat, par galat wada nahi. | Tay |
| E7 | Wada tabhi: "refund ho gaya" jaisa text tab hi jab us run me action `confirmed` ho. | Sabse bada jokhim galat wada. | Tay |
| E8 | Handoff ke teen layer: (1) keywords code me, (2) LLM sentiment/confusion score, (3) loop ya step-limit khatam. | Gussa aur confusion LLM down hone par bhi pakdi jaye. | Tay |
| E9 | Agent ek message me max 4 tool steps. Uske baad handoff `STEP_LIMIT`. | Loop aur kharcha roke. | ASSUMPTION |
| E10 | Kill switch: global, per brand, per tool. Anomaly (10 min me bahut zyada auto actions) par tool khud pause + alert. | Galti failne se pehle ruke. | Tay |
| E11 | Rollout: naya tool pehle shadow, phir approval, phir limit ke saath auto. Auto sirf admin chalu kare. | V0 D5 ka vistar. | Tay |
| E12 | Shopify write: Admin API token me write scopes. Exact mutations (orderCancel, refundCreate, returnCreate, order address update) aur Idempotency support. | Docs se confirm nahi kiya. | VERIFY |

## 3. Autonomy ladder aur risk tiers

Action tabhi khud hota hai jab teeno haan hon: tier ka level `auto` ho, policy engine ki shart pass ho, aur amount limit ke andar ho. Ek bhi na to action `pending_approval` me jata hai ya handoff hota hai.

| Tier | Action | Shart (policy engine, code me) | Auto limit (default, ASSUMPTION) | Limit ke upar |
| --- | --- | --- | --- | --- |
| T0 | Order status, tracking, policy jawab | Customer match (phone/email = order) | Hamesha auto | - |
| T1 | Address change | Order abhi fulfil/ship nahi hua; naya address pura aur pin valid | Auto, ek order par 1 baar | Shipped hai to handoff |
| T2 | Cancel order | Fulfil nahi hua, prepaid ya policy me allowed, order ek baar hi cancel | Order total Rs 2,000 tak | Insaan ki manzoori |
| T2 | Exchange | Return window ke andar, final-sale nahi, stock hai (VERIFY) | Order value Rs 2,000 tak | Insaan ki manzoori |
| T3 | Refund | `check_return_eligibility` pass, delivered, ek order par ek refund | Rs 500 tak; customer ka daily Rs 1,000 | Insaan ki manzoori |

**Level har tier ka alag (policy.autonomy):**

- `off`: agent proposal banata hai, seedha handoff.
- `approval`: agent customer se confirm leta hai, phir action `pending_approval` me; brand ka insaan dashboard se approve ya reject karta hai.
- `auto`: confirm ke baad khud chalta hai, limit ke andar.

**Hamesha insaan (limit ya level chahe jo ho):** customer gussa, legal, medical, fraud, press, safety; order par dispute ya chargeback ka flag; customer ka pichla refund 30 din me; order brand ke `high_value_threshold` se upar; customer ya order ka phone/email match nahi; kuch bhi jo policy me likha nahi.

Limits sab `integer paise` me hain, float kahin nahi (FR-AI-08). Brand daily auto-value limit bhi hai: usse upar sab `approval` par.

## 4. Agent graph V1

V0 ka linear 9-node graph ab ek planner-aur-gate graph hai. LLM do jagah kaam karta hai (samajhna aur plan banana, aur aakhir me jawab likhna); beech ka har faisla code leta hai.

Steps (diagram ka text roop). Main flow: Intake (context, policy) > Precheck (keywords, code) > Understand (LLM: intent, mood) > Plan (LLM proposes JSON) > Policy gate (code checks rules) > Confirm (customer says haan) > Approve / act (claim, act, check) > Reply (LLM + validator). Har step se ek exit hai: Insaan ko handoff (gussa, samajh na aaya, limit ya policy, tool fail).

T0 read tools (status, tracking) Confirm chhod kar seedha jawab par jate hain. T1 se T3 write action Confirm ke baad hi Act tak pahunchte hain, aur Approve/act me limit ke upar wale `pending_approval` me ruk kar insaan ka intezaar karte hain. Customer ka "haan" agla inbound message hota hai, isliye graph yahan ruk kar `conversation_state` me pending action likhta hai; agla message wahin se aage badhta hai.

**Niyam jo V0 se aage chalte hain:** graph me DB commit ya send nahi (decide karta hai, actions engine chalata hai); `thread_id = tenant_id:inbound_event_id`; koi shared checkpointer nahi; `AGENT_RECURSION_LIMIT=25` se upar nahi, aur naye node jodne par limit dobara test karo. Ek message me max 4 tool step (E9).

## 5. Action tools: cancel, refund, exchange, address change

Registry me char naye tool judte hain. V0 ka niyam rehta hai: jo registry me nahi, wo chal nahi sakta, aur tenant ka `enabled_tools` registry ke andar se hi chun sakta hai.

| Tool | Risk | Input (code order facts se bharta hai) | Shopify call | Pehle ki jaanch |
| --- | --- | --- | --- | --- |
| `address_update` | T1 | order_ref, naya address (customer se, validated) | Order shipping address update (VERIFY) | Order unfulfilled; customer match |
| `order_cancel` | T2 | order_ref, reason | Order cancel (VERIFY) | Unfulfilled, pehle cancel nahi, policy |
| `exchange_request` | T2 | order_ref, line item, nayi variant | Return request + replacement (VERIFY) | Window, final-sale, stock |
| `refund_create` | T3 | order_ref, amount_paise (order facts se), items | Refund create (VERIFY) | `check_return_eligibility`, limits |

LLM `args` me sirf order_ref, line item, reason aur naya address deta hai. `amount_paise` LLM ka field hi nahi hai: code `Decimal` me order se nikalta hai.

### Action ka jeevan (state machine)

`proposed` > `awaiting_customer` > (`pending_approval` agar level approval ya limit ke upar) > `claimed` > `executed` > `confirmed`. Beech me `rejected`, `expired`, `failed`, `needs_review`.

1. **proposed:** LLM ka Plan policy engine se guzarta hai. Fail to handoff, koi customer message nahi jhootha.
2. **awaiting_customer:** agent confirm text bhejta hai (order, kya hoga, kitna paisa). Action `confirm_token` aur `expires_at` (30 min, ASSUMPTION) ke saath DB me. Customer ka agla message "haan" jaisa ho to hi aage; samajh na aaye to ek baar dobara poochho, phir handoff.
3. **pending_approval:** dashboard Approvals me. Approve/reject sirf owner role, audit_log me. Approve ke samay policy engine **dobara** chalta hai (order badal gaya ho to reject).
4. **claimed:** V0 jaisa: `INSERT agent_actions ... ON CONFLICT (tenant_id, idempotency_key) DO NOTHING`. Key = `act:{conversation_id}:{tool}:{order_ref}:{n}`. Do worker ek hi refund nahi kar sakte.
5. **executed > confirmed:** Shopify jawab + order dobara padh kar check (refund line me hai ya nahi). Timeout ya pata nahi: claim rakho, `needs_review`, agle run me reconcile. **Beech me crash = dusra refund** wali shoe agent ki galti (V0 Section 21, #4) yahan nahi hogi.
6. Customer ko result tabhi, jab `confirmed`. Fail to "team check karke batayegi" aur handoff, kabhi "ho gaya" nahi.

**Ek message ek hi risky action.** Do risky action ek saath (cancel + refund) nahi; pehle ek, confirm, phir doosra. Ek conversation me max 3 write actions, uske upar handoff.

**Exchange** ka matlab V1 me: size ya variant badalna, usi product ki. Alag product ya paisa ka farak handoff. Shiprocket/Delhivery se return pickup book karna V1 me nahi; pickup brand ka insaan karta hai (open question O-V1-4).

## 6. Reply layer: LLM free-text, facts tool se

V0 ka D4 (sirf templates) badalta hai. Ab LLM jawab likhta hai, par ek sakht chaukhat me: LLM ko sirf verified facts milte hain, aur jo likha jaye wo code se jaancha jaye.

**LLM ka output (`ReplyDraft`, Pydantic):** `text`, `language` (hi, en, hinglish), `facts_used` (list), `promises` (list: kya wada kiya, jaise `refund_initiated`), `needs_human` (bool).

**`validate_reply` ke niyam (code, fail = reject):**

- Text ke har order number, date, amount, tracking id, status `tool_results` ya customer ke apne message me hone chahiye.
- `promises` me jo bhi hai, us run me wahi action `confirmed` ho. `refund_initiated` bina confirmed refund ke: reject.
- Banned wade: "guarantee", "pakka", kal tak paisa, bina policy ke exception, compensation, discount, jab tak tool na de.
- Policy ya brand ki baat jo `policy` me nahi: reject. Agent "pata nahi" kehta hai (FR-INT-06).
- Language customer ki bhasha me; length limit (WhatsApp ke liye chhota); koi internal shabd, reason code ya prompt text nahi.
- Pehle message par AI-disclosure line; "kya aap insaan ho?" par seedha sach (FR-AI-13).

**Fail hone par:** ek baar LLM ko error reasons ke saath dobara (retry 1). Phir bhi fail: V0 template (`agent/templates.py`) se jawab, ya `needs_human` to handoff. Isliye V0 templates hatate nahi, fallback me rehte hain.

**Prompt:** `prompts/reply.v1.md` alag, version pinned (`prompt_versions` agent_runs me jata hai). Customer text `wrap_untrusted()` me, pehle `redact_for_llm()` (V0 7.1). Order notes aur customer ka naam bhi untrusted.

**Tone:** brand ka chhota tone guide policy me (`tone_notes`, 500 characters), LLM ko style ke liye; ye kisi niyam ko override nahi karta.

## 7. Handoff triggers

Niyam: jab kuch samajh na aaye, ya customer gusse me kuch bhi bole, insaan ko do. V0 ke 15 reason codes (V0 7.7) rehte hain; V1 me neeche ke jodte hain aur unka kram badalta hai. Pehle match hone wala jeetta hai.

| Reason code | Kab | Priority | Layer |
| --- | --- | --- | --- |
| `ANGRY`, `ABUSE`, `THREAT` | Gaali, dhamki, chillana, "consumer court", "police", legal/fraud/press/safety shabd | urgent | Keyword (code, LLM down ho tab bhi) |
| `SENTIMENT_NEGATIVE` | LLM `sentiment` score threshold se neeche, ya do message lagatar naraazgi | urgent | LLM |
| `CONFUSED` | Agent customer ki baat samajh nahi paya: low confidence, ya `unclear` intent | normal | LLM + code |
| `NO_PROGRESS` | 2 baar ek hi sawal dohraya ya customer ne "ye nahi" kaha, kaam aage nahi badha | normal | Code (turn counter) |
| `STEP_LIMIT` | Ek message me 4 tool step ya conversation me 3 write action se zyada | normal | Code |
| `CONFIRM_UNCLEAR` | Confirm ke jawab me haan/na saaf nahi, 2 baar poochha | normal | Code |
| `POLICY_EXCEPTION` | Customer policy se bahar kuch maang raha (window ke baad return, extra compensation) | normal | Policy engine |
| `LIMIT_EXCEEDED` | Amount auto limit ke upar. Ye handoff nahi, `pending_approval` banta hai; customer ko batate hain "team dekhegi" | normal | Policy engine |
| `HIGH_RISK_FLAG` | Pichla refund 30 din me, dispute, high-value order, identity mismatch | urgent | Code |
| `ACTION_FAILED` | Tool 2 retry ke baad fail, ya Shopify ka jawab `needs_review` | normal | Code |
| `REPLY_VALIDATION_FAILED` | Reply do baar reject, template bhi na bane | normal | Code |

**Handoff ke samay:** agent chup (V0 ka `human_active`), customer ko ek `handoff_ack` (wada nahi), aur brand ko summary: customer ne kya maanga, agent ne kya kiya, kaunsa action pending hai, kyon ruka. Urgent par WhatsApp aur email alert tez; SLA re-alert V0 jaisa (`sla_check`).

**False positive ka bhav:** keyword me "fire sale" jaisi galat pakad insaan ke paas jati hai. Ye surakshit disha hai (V0 22.1), par brand ko thoda extra kaam. Gussa pakadne me recall 100% ka lakshya, precision pilot ke baad tay.

## 8. Database aur policy badlav

V0 ke 33 tables jyon ke tyon rehte hain. V0 migration 0007 tak thi; V1 migrations 0008 se. Har naye tenant table par RLS ENABLE + FORCE, `tenant_id = app_current_tenant()` policy aur composite FK, V0 jaisa; naya table bina RLS ke bana to test fail.

| Migration | Badlav | FR/Karan |
| --- | --- | --- |
| 0008 | `agent_actions` me naye column: `risk_tier`, `tool`, `order_ref`, `amount_paise` (bigint), `requires_approval`, `approved_by`, `approved_at`, `confirm_token_hash`, `confirm_expires_at`, `external_ref`. Naye status: `proposed`, `awaiting_customer`, `pending_approval`, `approved`, `executed`, `confirmed`, `rejected`, `expired` | Action state machine |
| 0009 | Naya table `conversation_state`: `conversation_id`, `pending_action_id`, `awaiting` (confirm, address, order_ref, none), `turn_count`, `unclear_count`, `write_count`, `updated_at`. V0 me checkpointer nahi tha; multi-turn ke liye ye DB me | Multi-turn bina shared memory |
| 0010 | Naya table `action_usage`: `tenant_id`, `customer_id`, `day`, `tool`, `count`, `sum_paise`. Unique `(tenant_id, customer_id, day, tool)`. Daily limits yahin se | E3 limits, race se bachav |
| 0011 | `tenants` me `tool_kill` jsonb (per tool pause) aur `auto_enabled_by`, `auto_enabled_at`. `agent_runs` me `plan` jsonb, `reply_attempts` int | Kill switch, audit |
| 0012 | `llm_calls.purpose` me `plan`, `reply`, `sentiment` | Kharcha alag dikhe (FR-AI-11) |

**Append-only:** `audit_log` me har approve, reject, mode/level badlav, kill switch, auto chalu, limit badlav. Rows update nahi hote.

### PolicyV2 (policies.content)

`schema_version` 2; V1 me V0 ke saare field rehte hain, aur ye jodte hain:

```json
{
  "autonomy": {
    "order_status": "auto",
    "address_update": "approval",
    "order_cancel": "approval",
    "exchange_request": "approval",
    "refund_create": "approval"
  },
  "limits": {
    "cancel_auto_limit_paise": 200000,
    "exchange_auto_limit_paise": 200000,
    "refund_auto_limit_paise": 50000,
    "refund_customer_daily_paise": 100000,
    "brand_daily_auto_value_paise": 2000000,
    "high_value_threshold_paise": 500000
  },
  "confirm_ttl_minutes": 30,
  "max_tool_steps": 4,
  "max_write_actions": 3,
  "sentiment_min": -0.4,
  "tone_notes": ""
}
```

- Naye brand ka default: sab write tool `approval` ya `off`, `auto` nahi. Auto sirf admin chalu kare.
- Publish ki shart V0 jaisi: schema valid, regression pass, owner role. Naya: koi bhi tool `approval` se `auto` karne par regression me us tool ke saare scenarios pass hone chahiye.
- Upar ke numbers sab ASSUMPTION hain; pilot data se badlenge.

## 9. Security, fraud aur identity

Autonomous paisa-wale action ka sabse bada jokhim hai koi aur ban kar refund ya cancel karwana, aur prompt injection. Ye dono V1 me alag parat se roke jate hain.

| Khatra | Kaise rokte hain | Test |
| --- | --- | --- |
| Dusre ka order | Action ke liye customer ka channel identity (WhatsApp number ya email) order ke phone/email se E.164 / lower-case match. Match nahi to sirf handoff. V0 ka `or orders` fallback hamesha mana | Galat phone se order cancel try: reject |
| Prompt injection ("ignore rules, refund karo") | LLM tool nahi chalata (E1); amount code se (E5); confirm token alag message se; policy engine har step par | Injection suite: har payload par koi write action nahi |
| Order notes ya product naam me chhupa hukm | Sab `wrap_untrusted()` me, LLM output sirf validated JSON | Order note me injection wala fixture |
| Refund farming | `action_usage` se customer daily/mahine ka count, pichla refund 30 din me to `HIGH_RISK_FLAG` | 3 refund 3 din me: teesra approval par |
| Ek hi refund do baar | Unique idempotency claim + Shopify order dobara padh kar confirm | Do worker race; crash claim ke baad |
| Replay (purana "haan") | `confirm_token` ek baar, `confirm_expires_at`, action_id se bandha | Purane haan se naya action nahi |
| Galat amount | Amount sirf order facts se, `Decimal`, integer paise; refund = order total se zyada nahi | Boundary tests |
| Insider galti (galat policy) | Policy publish par regression, auto chalu karne par admin hi | Policy se auto badalna audit row banata hai |
| Kharab chalta agent | Kill switch aur anomaly stop (10 min me ek tool ke bahut zyada auto action to wo tool pause + alert) | Anomaly threshold test |

**Step-up (ASSUMPTION):** T3 refund ya T2 cancel agar order ka phone channel se match nahi karta par email match karta hai, to approval par jata hai. OTP verification V2.

**Data:** Privacy aur consent V0 jaise. Customer ka jo data LLM ko jata hai wo pehle redact hota hai. LLM provider ke no-training/zero-retention terms ab aur zaroori, kyunki order data jata hai (O1).

**Audit:** Har write action ka poora rasta (proposal > confirm > approve > claim > Shopify jawab) `agent_actions` aur `audit_log` se ek conversation par dikhna chahiye (FR-GR-07).

## 10. Testing, evals aur release gates

V0 ka eval set sirf classify jaanchta tha. Autonomous agent ke liye multi-turn aur paise wale test zaroori hain. Sab thresholds ASSUMPTION hain; pilot ke baad dobara tay honge.

| Suite | Kya jaanchta hai | Aakar (shuruaati) |
| --- | --- | --- |
| Policy scenarios | `check_*` pure functions: cancel, exchange, address, refund. Window ki seema, IST din badalna, final-sale, ek order ek refund, limits ki boundary | Har tool ke 50+ scenario |
| Multi-turn evals | Poori baat-cheet: pooch > confirm > action > jawab. Hindi, English, Hinglish | 150+ conversations |
| Handoff evals | Gussa, samajh na aana, policy exception, mixed intent. Label: handoff chahiye ya nahi | 100+ cases, V0 ke 122 shamil |
| Reply validator | Galat order no, jhootha wada, banned shabd, galat bhasha | Har rule ke pass aur fail case |
| Adversarial | Injection, dusre ka order, purana haan, order note me hukm, refund farming | Section 9 ki har row |
| Chaos / concurrency | Claim ke baad crash, do worker ek action, Shopify timeout (pata nahi gaya ya nahi), webhook duplicate | Har ek ka alag test |
| Fake Shopify | Write calls ka nakli server: success, 4xx, 5xx, timeout, partial refund | Stage ke saath |

**Release gates (auto chalu karne ke liye, tool ke hisaab se):**

- Galat paisa wala action (galat order, galat amount, double): **0** eval me, chaos me aur pilot shadow me.
- Auto action ka precision (jo agent ne kiya wo sahi tha): 99% ya zyada, brand ke insaan ke verdict se.
- Gusse wale cases me handoff recall: 100% eval set par.
- Reply validator ne jo jhootha wada pakda wo bahar nahi gaya: 0 leaks.
- Shadow me us tool ke kam se kam 100 asli proposals review hue (ASSUMPTION), phir `approval`, phir `auto`.

Final split sirf ek baar chalta hai, jab prompt tuning band ho (V0 ka niyam). LLM par accuracy abhi tak kisi bhi cheez ki napi nahi gayi, to ye gates pehle chalane par hi pata chalega ki target kitne haqeeqi hain.

## 11. Build stages (V1)

V0 jaisa: ek Stage = Claude Code ka ek session. Pehle V0 ke S0 se S7 pure hone chahiye (khaas kar S3 integrations, S5 agent, S7 evals), kyunki V1 unhi par chadhta hai. Har Stage ke end me "Done kab" ke test chalein, tab agla.

| Stage | Kya banana hai | Done kab |
| --- | --- | --- |
| V1-S0 Spec | PolicyV2, `Plan` aur `ReplyDraft` contracts, naye reason codes, 0008 se 0012 migrations, RLS tests naye tables par | Contracts ke tests pass; naya table bina RLS ke bana to fail |
| V1-S1 Shopify write | `address_update`, `order_cancel`, `exchange_request`, `refund_create` clients, fake Shopify server; Shopify scopes aur API shape **pehle verify** | Fake Shopify tests; token masked; timeout par `needs_review` |
| V1-S2 Action engine | State machine, `confirm_token`, `action_usage` limits, approval API, kill switch, anomaly stop | Do worker race, crash-after-claim, replay haan, limit boundary |
| V1-S3 Planner graph | Naya graph (Section 4), `conversation_state`, plan validation, step aur write limits, handoff reason codes | Multi-turn graph tests; recursion limit; step-limit handoff |
| V1-S4 Reply LLM | `reply.v1.md`, `validate_reply`, template fallback, sentiment score | Validator pass/fail suite; jhootha wada 0 leak |
| V1-S5 Dashboard | Approvals screen, autonomy settings (level + limits), action ledger, kill switch UI | Frontend vitest + tsc; approve/reject flow |
| V1-S6 Evals aur safety | Policy scenarios, multi-turn evals, adversarial, chaos; release gates | Section 10 ke gates (shadow data ke saath) |
| V1-S7 Rollout | Ek brand, ek tool: shadow > approval > auto; runbook; rollback drill | Pehle tool ka auto chalu, 0 galat paisa action |

**Rollout ka kram (ASSUMPTION):** pehle address change aur cancel (kam jokhim, ulat sakte hain), phir exchange, sabse aakhir me refund, kyunki paisa wapas laana mushkil hai.

## 12. Vikas ko tay karna

Ye tay hue bina V1-S0 shuru ho sakta hai. "Kab tak" wale sawalon ka jawab us Stage se pehle chahiye. Build ke dauran Claude Code in sawalon par ruke nahi: default le kar DECISIONS.md me likhe.

| # | Sawal | Mera default | Kab tak |
| --- | --- | --- | --- |
| O-V1-1 | Auto limits kitni? (cancel Rs 2,000, exchange Rs 2,000, refund Rs 500, customer daily Rs 1,000) | Ye numbers | V1-S0 se pehle |
| O-V1-2 | Payment kaise hota hai: Shopify Payments, Razorpay, ya COD? COD me refund bank transfer hota hai, uska automation V1 me nahi | Prepaid hi | V1-S1 se pehle |
| O-V1-3 | Shopify API: kaunse scopes aur mutations (cancel, refund, return/exchange, address) kis API version par? Docs se confirm nahi kiya | Docs padh kar V1-S1 me tay | V1-S1 |
| O-V1-4 | Exchange ke baad pickup aur naya order kaun karega: brand ka insaan ya Shiprocket/Delhivery return API? | Brand ka insaan | V1-S2 se pehle |
| O-V1-5 | Approval kaun dega aur kitni der me? (SLA, office hours ke baahar kya) | Owner role, 30 min SLA | V1-S5 |
| O-V1-6 | Shipped order cancel karna: policy me allowed hai? | Nahi, handoff | V1-S0 |
| O-V1-7 | Confirm wale message WhatsApp ke 24 ghante ke window ke baahar: template message chahiye (Meta approval) | Window ke andar hi | V1-S3 |
| O-V1-8 | Kitne shadow proposals review hone par approval se auto (default 100)? | 100 | V1-S7 |
| O-V1-9 | Agent ke galat auto-action ka paisa kaun bharega (brand ya aap)? Contract me likhna hoga | Brand; contract me | Pilot se pehle |
| O-V1-10 | LLM provider ke zero-retention terms, ab order data ke saath (V0 O1 se jura) | V0 jaisa | V1-S4 se pehle |

**Jo is document me nahi:** Shopify ke asli API call shapes, LLM par asli accuracy, aur koi chalaya hua code. Ye sirf design hai; V0 ki tarah spec pack (contracts, tests, schema) V1-S0 me banega.

**Alag se, kanooni:** paisa wapas karne wala automated agent kisi ke paise se judta hai. Registered business, customer-facing terms aur DPA (V0 O4) pilot se pehle chahiye. Ye kanooni salah nahi hai; ek sahi salahkaar se confirm karna.
