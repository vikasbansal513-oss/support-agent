import re
from typing import Literal

Answer = Literal["yes", "no", "unclear"]
YES = frozenset(
    {
        "haan",
        "han",
        "ha",
        "haa",
        "haanji",
        "hanji",
        "yes",
        "y",
        "yep",
        "yeah",
        "yup",
        "ok",
        "okay",
        "kardo",
        "karo",
        "kr",
        "confirm",
        "confirmed",
        "sure",
        "ji",
        "theek",
        "thik",
        "sahi",
        "done",
        "हाँ",
        "हां",
        "हा",
        "जी",
        "ठीक",
        "कर",
        "दो",
        "करो",
        "कीजिए",
        "कीजिये",
        "👍",
    }
)
NO = frozenset(
    {
        "nahi",
        "nahin",
        "nhi",
        "nahii",
        "no",
        "na",
        "mat",
        "ruko",
        "ruk",
        "stop",
        "rehne",
        "rahne",
        "cancel",
        "nope",
        "नहीं",
        "नही",
        "ना",
        "मत",
        "रुको",
        "रहने",
        "👎",
    }
)
FILLER = frozenset(
    {
        "hai",
        "h",
        "do",
        "dena",
        "dijiye",
        "kijiye",
        "please",
        "pls",
        "plz",
        "to",
        "bhai",
        "sir",
        "madam",
        "the",
        "it",
        "that",
        "is",
        "so",
        "kar",
        "है",
        "दीजिए",
        "प्लीज",
    }
)
_TOKEN = re.compile(r"[\wऀ-ॿ]+|[👍👎]", re.UNICODE)
MAX_TOKENS = 6


def parse_confirmation(text: str, tool: str | None = None) -> Answer:
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    if not tokens or len(tokens) > MAX_TOKENS or "?" in text:
        return "unclear"
    yes = any(t in YES for t in tokens)
    no_tokens = [t for t in tokens if t in NO]
    # "cancel" is both how a customer declines ("cancel that, forget it" - NO) and
    # how they name the very action being confirmed when the pending write is
    # order_cancel ("yes, please cancel"). Only strip it as a NO signal when an
    # explicit yes is also present and "cancel" is the *only* NO-token in the
    # message - a lone "cancel" (no "yes") still reads as a refusal, same as for
    # every other tool, and "cancel" alongside another real NO word (e.g. "haan
    # cancel mat karo") stays genuinely ambiguous.
    if tool == "order_cancel" and yes and no_tokens == ["cancel"]:
        no_tokens = []
    no = bool(no_tokens)
    rest = [t for t in tokens if t not in YES and t not in NO and t not in FILLER]
    if yes == no or rest:
        return "unclear"
    return "yes" if yes else "no"
