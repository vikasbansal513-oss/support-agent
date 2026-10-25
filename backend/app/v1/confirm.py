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


def parse_confirmation(text: str) -> Answer:
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    if not tokens or len(tokens) > MAX_TOKENS or "?" in text:
        return "unclear"
    yes = any(t in YES for t in tokens)
    no = any(t in NO for t in tokens)
    rest = [t for t in tokens if t not in YES and t not in NO and t not in FILLER]
    if yes == no or rest:
        return "unclear"
    return "yes" if yes else "no"
