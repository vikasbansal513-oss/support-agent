"""Deterministic prechecks (run BEFORE the LLM, FR safety rules). Pure functions, no I/O."""

from __future__ import annotations

import re
import unicodedata

ORDER_REF_FIND = re.compile(r"(?<![A-Za-z0-9])#?(\d{4,10})(?![A-Za-z0-9])")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[​-‏⁠﻿]", "", t)
    t = re.sub(r"(.)\1{3,}", r"\1\1", t)
    return " ".join(t.split())


def _rx(words: list[str]) -> re.Pattern[str]:
    parts = [
        r"(?<![a-z0-9ऀ-ॿ])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![a-z0-9ऀ-ॿ])" for w in words
    ]
    return re.compile("|".join(parts))


OPT_OUT_EXACT = {
    "stop",
    "unsubscribe",
    "stop messages",
    "band karo",
    "band kar do",
    "mat bhejo",
    "msg mat bhejo",
    "message mat bhejo",
    "रुकिए",
    "बंद करो",
    "बंद करें",
    "stop it",
}
OPT_IN_EXACT = {"start", "subscribe", "start messages"}

HUMAN = _rx(
    [
        "human",
        "real person",
        "agent se baat",
        "insaan",
        "insan",
        "manager",
        "customer care",
        "kisi se baat",
        "kisi aadmi",
        "executive se",
        "representative",
        "talk to someone",
        "speak to someone",
        "call me",
        "call karo",
        "call kijiye",
        "इंसान",
        "मैनेजर",
        "किसी से बात",
        "एजेंट से बात",
        "कस्टमर केयर",
    ]
)
AI_QUESTION = _rx(
    [
        "are you a bot",
        "are you ai",
        "are you human",
        "kya aap bot",
        "kya tum bot",
        "bot ho",
        "bot hai",
        "ai ho",
        "robot ho",
        "machine ho",
        "insaan ho",
        "real person ho",
        "kya yeh bot",
        "is this a bot",
        "क्या आप बॉट",
        "बॉट हो",
        "रोबोट",
    ]
)
ANGRY = _rx(
    [
        "worst",
        "pathetic",
        "useless",
        "bakwas",
        "bekar service",
        "bekaar",
        "ghatiya",
        "disgusting",
        "fraud company",
        "idiots",
        "stupid",
        "nonsense",
        "kitni baar bolu",
        "kitni baar bata",
        "sick of",
        "fed up",
        "irritated",
        "harassment",
        "bahut bura",
        "bakwaas",
        "faltu",
        "chor",
        "loot",
        "thug",
        "dhokha",
        "बकवास",
        "घटिया",
        "बेकार",
        "धोखा",
        "लूट",
    ]
)
LEGAL = _rx(
    [
        "legal action",
        "lawyer",
        "advocate",
        "court",
        "consumer court",
        "consumer forum",
        "police",
        "fir",
        "case karunga",
        "case karungi",
        "notice bhejunga",
        "legal notice",
        "sue",
        "kanooni",
        "vakil",
        "ncdrc",
        "consumer complaint",
        "वकील",
        "कोर्ट",
        "पुलिस",
        "कानूनी",
        "उपभोक्ता",
    ]
)
MEDICAL = _rx(
    [
        "allergy",
        "allergic",
        "rash",
        "reaction",
        "hospital",
        "doctor",
        "infection",
        "swelling",
        "burn",
        "injury",
        "injured",
        "poison",
        "vomit",
        "itching",
        "side effect",
        "skin problem",
        "एलर्जी",
        "अस्पताल",
        "डॉक्टर",
        "जल गया",
    ]
)
FRAUD = _rx(
    [
        "scam",
        "scammer",
        "fake product",
        "duplicate product",
        "counterfeit",
        "cheated",
        "cheating",
        "fraud",
        "fraudster",
        "unauthorized",
        "unauthorised",
        "paisa kat gaya",
        "double charge",
        "charged twice",
        "paise kat gaye",
        "chargeback",
        "धोखाधड़ी",
        "नकली",
        "ठगी",
    ]
)
PRESS = _rx(
    [
        "media",
        "journalist",
        "reporter",
        "news channel",
        "viral",
        "twitter",
        "x.com",
        "instagram par",
        "post karunga",
        "post kar dunga",
        "review bomb",
        "expose",
        "youtube",
        "going public",
        "पत्रकार",
        "मीडिया",
    ]
)
SAFETY = _rx(
    [
        "fire",
        "caught fire",
        "exploded",
        "explosion",
        "electric shock",
        "shock laga",
        "sharp",
        "choking",
        "dangerous",
        "unsafe",
        "short circuit",
        "aag lag",
        "blast",
        "आग",
        "धमाका",
        "करंट",
    ]
)
GREETING_ONLY = re.compile(
    r"^(hi|hii+|hello|hey|hlo|namaste|namaskar|good (morning|evening|afternoon)|नमस्ते|हेलो|हाय)[\s!.,]*$"
)


def is_opt_out(text: str) -> bool:
    return norm(text).strip(" .!") in OPT_OUT_EXACT


def is_opt_in(text: str) -> bool:
    return norm(text).strip(" .!") in OPT_IN_EXACT


def detect_flags(text: str, extra_keywords: list[str] | None = None) -> set[str]:
    """Returns subset of: human, ai_question, angry, legal, medical, fraud, press, safety, extra."""
    t = norm(text)
    out: set[str] = set()
    for name, rx in (
        ("human", HUMAN),
        ("ai_question", AI_QUESTION),
        ("angry", ANGRY),
        ("legal", LEGAL),
        ("medical", MEDICAL),
        ("fraud", FRAUD),
        ("press", PRESS),
        ("safety", SAFETY),
    ):
        if rx.search(t):
            out.add(name)
    extra = [norm(k) for k in (extra_keywords or []) if norm(k)]
    if extra and _rx(extra).search(t):
        out.add("extra")
    return out


FLAG_REASON = {
    "legal": "LEGAL",
    "medical": "MEDICAL",
    "fraud": "FRAUD",
    "press": "PRESS",
    "safety": "SAFETY",
    "angry": "ANGRY",
    "extra": "SAFETY",
    "human": "HUMAN_REQUESTED",
}
REASON_PRIORITY = ["SAFETY", "MEDICAL", "LEGAL", "FRAUD", "PRESS", "ANGRY", "HUMAN_REQUESTED"]


def top_reason(flags: set[str]) -> str | None:
    reasons = {FLAG_REASON[f] for f in flags if f in FLAG_REASON}
    for r in REASON_PRIORITY:
        if r in reasons:
            return r
    return None


def is_greeting_only(text: str) -> bool:
    return bool(GREETING_ONLY.match(norm(text)))


def extract_order_ref(text: str) -> str | None:
    m = ORDER_REF_FIND.search(text or "")
    return f"#{m.group(1)}" if m else None


def detect_language(text: str) -> str:
    """Rough script/word based guess; the LLM value is advisory, this is the fallback."""
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi"
    t = norm(text)
    hinglish = _rx(
        [
            "mera",
            "meri",
            "kab",
            "kahan",
            "kya",
            "hai",
            "nahi",
            "nahin",
            "aaya",
            "aya",
            "bhej",
            "karo",
            "kijiye",
            "abhi",
            "order ka",
            "kitna",
            "wala",
            "wali",
            "mujhe",
            "chahiye",
            "kaise",
            "bhai",
            "yaar",
            "ji",
        ]
    )
    return "hinglish" if hinglish.search(t) else "en"
