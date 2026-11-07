import re
import uuid
from typing import Literal

from app.llm.client import LlmAuthError, LlmClient, LlmInvalid, LlmUnavailable, load_prompt
from app.llm.guard import wrap_untrusted
from app.v1.contracts import ConfirmAnswerV1

Answer = Literal["yes", "no", "unclear"]
CONFIRM_LLM_PROMPT = "confirm.v1.md"
CONFIRM_LLM_VERSION = "confirm.v1"
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


async def classify_confirmation_llm(
    llm: LlmClient,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None,
    tool: str | None,
    text: str,
) -> Answer | None:
    """LLM fallback for a reply the keyword matcher above (parse_confirmation) could not
    read - e.g. "Yes, go ahead and cancel it.", where "go"/"ahead"/"and"/"it" are not in any
    of the YES/NO/FILLER lists so the matcher bails to "unclear" despite the clear "yes".

    Only called by app.v1.graph.confirm_step when parse_confirmation() already returned
    "unclear" - a real customer message can be phrased any way, so this reads the whole
    sentence's meaning instead of extending the keyword lists forever. It never runs for a
    message the keyword matcher already read as a plain "yes" or "no", so it can never
    change an answer that already worked.

    Returns None - never raises - if the LLM call fails or returns something invalid, so the
    caller simply keeps parse_confirmation()'s own "unclear" result (same behaviour as
    before this fallback existed; a broken LLM call can only ever fall back to re-asking the
    customer, never to a wrong yes/no that triggers a write action).
    """
    system = load_prompt(CONFIRM_LLM_PROMPT).replace("{{tool}}", tool or "this request")
    user = wrap_untrusted("customer_message", text, 500)
    try:
        result = await llm.complete_json(
            tenant_id=tenant_id,
            run_id=run_id,
            purpose="confirm",
            system=system,
            user=user,
            schema=ConfirmAnswerV1,
            prompt_version=CONFIRM_LLM_VERSION,
        )
    except (LlmAuthError, LlmUnavailable, LlmInvalid):
        return None
    return result.answer
