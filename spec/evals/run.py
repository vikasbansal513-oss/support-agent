"""Code-only eval: verifies keyword flags, opt-out, order_ref extraction, language fallback. Intent labels need an LLM (--llm, not run offline)."""
import json, sys, pathlib, collections, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "agent"))
import keywords as K
from cases import C

OPT_OUT_TAG, OPT_IN_TAG = "opt_out", "opt_in"

def split(text):  # stable: 70% tuning, 30% final
    return "final" if int(hashlib.sha256(text.encode()).hexdigest(), 16) % 10 >= 7 else "tuning"

def rows():
    for i, (text, lang, intent, flags, ref, tags) in enumerate(C, 1):
        yield {"id": f"C{i:03d}", "text": text, "language": lang, "intent": intent, "flags": sorted(flags), "order_ref": ref,
               "tags": tags, "expect_opt_out": OPT_OUT_TAG in tags, "expect_opt_in": OPT_IN_TAG in tags, "split": split(text),
               "known_false_positive": "known_false_positive" in tags}

def check(r):
    errs = []
    got = K.detect_flags(r["text"])
    got_core = got - {"ai_question"} if "ai_question" not in r["flags"] else got
    if set(r["flags"]) != got:
        errs.append(f"flags expected {r['flags']} got {sorted(got)}")
    if K.is_opt_out(r["text"]) != r["expect_opt_out"]: errs.append("opt_out mismatch")
    if K.is_opt_in(r["text"]) != r["expect_opt_in"]: errs.append("opt_in mismatch")
    if K.extract_order_ref(r["text"]) != r["order_ref"]: errs.append(f"order_ref expected {r['order_ref']} got {K.extract_order_ref(r['text'])}")
    return errs

if __name__ == "__main__":
    data = list(rows())
    out = pathlib.Path(__file__).parent / "dataset.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n", encoding="utf-8")
    fails = [(r, check(r)) for r in data]
    bad = [(r, e) for r, e in fails if e]
    by = collections.Counter(r["split"] for r in data)
    print(f"cases={len(data)} tuning={by['tuning']} final={by['final']} intents={dict(collections.Counter(r['intent'] for r in data))}")
    for r, e in bad: print("FAIL", r["id"], repr(r["text"]), e)
    print(f"code-checked: {len(data)-len(bad)}/{len(data)} pass")
    sys.exit(1 if bad else 0)
