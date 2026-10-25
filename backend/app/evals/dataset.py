import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.agent import keywords

DATA = Path(__file__).parent / "data" / "support_v0.jsonl"
OPT_OUT_TAG = "opt_out"
OPT_IN_TAG = "opt_in"


@dataclass(frozen=True)
class EvalRow:
    id: str
    text: str
    language: str
    intent: str
    flags: frozenset[str]
    order_ref: str | None
    tags: tuple[str, ...]
    expect_opt_out: bool
    expect_opt_in: bool
    split: str
    known_false_positive: bool


def split_of(text: str) -> str:
    return "final" if int(hashlib.sha256(text.encode()).hexdigest(), 16) % 10 >= 7 else "tuning"


def load_rows(split: str | None = None) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        row = EvalRow(
            id=raw["id"],
            text=raw["text"],
            language=raw["language"],
            intent=raw["intent"],
            flags=frozenset(raw["flags"]),
            order_ref=raw["order_ref"],
            tags=tuple(raw["tags"]),
            expect_opt_out=bool(raw["expect_opt_out"]),
            expect_opt_in=bool(raw["expect_opt_in"]),
            split=raw["split"],
            known_false_positive=bool(raw["known_false_positive"]),
        )
        if split is None or row.split == split:
            rows.append(row)
    return rows


def code_errors(row: EvalRow) -> list[str]:
    errors: list[str] = []
    got = keywords.detect_flags(row.text)
    if set(row.flags) != got:
        errors.append(f"flags expected {sorted(row.flags)} got {sorted(got)}")
    if keywords.is_opt_out(row.text) != row.expect_opt_out:
        errors.append("opt_out mismatch")
    if keywords.is_opt_in(row.text) != row.expect_opt_in:
        errors.append("opt_in mismatch")
    ref = keywords.extract_order_ref(row.text)
    if ref != row.order_ref:
        errors.append(f"order_ref expected {row.order_ref} got {ref}")
    if split_of(row.text) != row.split:
        errors.append("split drift")
    return errors


def code_subset_passed() -> bool:
    return all(not code_errors(row) for row in load_rows())
