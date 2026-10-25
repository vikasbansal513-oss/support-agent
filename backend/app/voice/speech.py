import re
from typing import Literal

Lang = Literal["hi", "en", "hinglish"]
_MONEY = re.compile(r"Rs\.?\s?(\d[\d,]*)(?:\.(\d{1,2}))?")
_REF = re.compile(r"#([A-Za-z0-9-]+)")
_MARKUP = re.compile(r"[*_`~<>\[\]{}|\\]")
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U00002b00-\U00002bff]"
)
_SWAPS: dict[str, dict[str, str]] = {
    "hinglish": {
        "likhein": "bolein",
        "likhiye": "boliye",
        "bhejein": "bataiye",
        "Kripya sirf": "Kripya sirf",
    },
    "en": {
        "Reply with just": "Please say just",
        "Reply": "Please say",
        "reply": "say",
        "send": "tell me",
    },
    "hi": {"लिखें": "बोलें", "भेजें": "बताइए"},
}
RUPEE_WORD = {"hinglish": "rupaye", "hi": "रुपये", "en": "rupees"}
PAISE_WORD = {"hinglish": "paise", "hi": "पैसे", "en": "paise"}
MAX_SPOKEN_CHARS = 400


def _money(match: re.Match[str], lang: str) -> str:
    rupees = match.group(1).replace(",", "")
    paise = match.group(2)
    out = f"{rupees} {RUPEE_WORD[lang]}"
    if paise and int(paise) != 0:
        out += f" {int(paise.ljust(2, '0'))} {PAISE_WORD[lang]}"
    return out


def _ref(match: re.Match[str]) -> str:
    return " ".join(ch for ch in match.group(1) if ch.isalnum())


def speakable(text: str, lang: str) -> str:
    """Turn a chat reply into something a text-to-speech engine reads clearly."""
    language = lang if lang in ("hi", "en", "hinglish") else "hinglish"
    out = _EMOJI.sub("", text)
    out = _MONEY.sub(lambda m: _money(m, language), out)
    out = _REF.sub(_ref, out)
    for old, new in _SWAPS[language].items():
        out = out.replace(old, new)
    out = _MARKUP.sub(" ", out)
    out = re.sub(r"\s*\n+\s*", ". ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if len(out) > MAX_SPOKEN_CHARS:
        cut = out[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "))
        out = cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0]
    return out


VOICE_T: dict[str, dict[str, str]] = {
    "greeting": {
        "hinglish": "Namaste! Main {brand} ka AI sahayak hoon, aur is baat-cheet ka text quality ke liye save hota hai. Aap order ka status, tracking, cancel ya exchange ke baare mein pooch sakte hain. Bataiye, main kya madad karun?",
        "en": "Hello! I am the AI assistant for {brand}, and a text transcript of this call is saved for quality. You can ask about order status, tracking, cancellation or exchange. How can I help?",
        "hi": "नमस्ते! मैं {brand} का एआई सहायक हूँ, और इस बातचीत का टेक्स्ट गुणवत्ता के लिए सहेजा जाता है। आप ऑर्डर स्टेटस, ट्रैकिंग, कैंसिल या एक्सचेंज के बारे में पूछ सकते हैं। बताइए, मैं क्या मदद करूँ?",
    },
    "unclear_audio": {
        "hinglish": "Maaf kijiye, awaaz saaf nahi aayi. Kya aap dobara bol sakte hain?",
        "en": "Sorry, I could not hear that clearly. Could you say it again?",
        "hi": "माफ़ कीजिए, आवाज़ साफ़ नहीं आई। क्या आप दोबारा बोल सकते हैं?",
    },
    "unclear_confirm": {
        "hinglish": "Confirm ke liye mujhe saaf sunai nahi diya. Kripya sirf haan ya nahi bolein.",
        "en": "I did not catch that clearly. Please say just yes or no.",
        "hi": "कन्फर्म के लिए मुझे साफ़ सुनाई नहीं दिया। कृपया सिर्फ हाँ या नहीं बोलें।",
    },
    "silence": {
        "hinglish": "Kya aap line par hain? Kripya bolein.",
        "en": "Are you still there? Please go ahead.",
        "hi": "क्या आप लाइन पर हैं? कृपया बोलें।",
    },
    "goodbye": {
        "hinglish": "Dhanyavaad! Aapka din shubh ho.",
        "en": "Thank you! Have a good day.",
        "hi": "धन्यवाद! आपका दिन शुभ हो।",
    },
    "transfer": {
        "hinglish": "Main aapko hamari team se jod raha hoon. Kripya line par bane rahein.",
        "en": "I am connecting you to our team. Please stay on the line.",
        "hi": "मैं आपको हमारी टीम से जोड़ रहा हूँ। कृपया लाइन पर बने रहें।",
    },
    "callback": {
        "hinglish": "Maine aapki baat hamari team ko de di hai. Woh aapko isi number par call karke madad karenge. Dhanyavaad.",
        "en": "I have passed your request to our team. They will call you back on this number. Thank you.",
        "hi": "मैंने आपकी बात हमारी टीम को दे दी है। वे इसी नंबर पर आपको कॉल करके मदद करेंगे। धन्यवाद।",
    },
    "callback_off_hours": {
        "hinglish": "Hamari team abhi available nahi hai. Maine aapki baat note kar li hai, woh agle working time par isi number par call karenge. Dhanyavaad.",
        "en": "Our team is not available right now. I have noted your request and they will call you back during working hours. Thank you.",
        "hi": "हमारी टीम अभी उपलब्ध नहीं है। मैंने आपकी बात नोट कर ली है, वे अगले कामकाजी समय पर कॉल करेंगे। धन्यवाद।",
    },
    "approval_pending": {
        "hinglish": "Aapki confirmation mil gayi. Hamari team ise dekhegi aur aapko isi number par call karke batayegi. Dhanyavaad.",
        "en": "Thanks for confirming. Our team will review it and call you back on this number. Thank you.",
        "hi": "आपका कन्फर्मेशन मिल गया। हमारी टीम इसे देखेगी और इसी नंबर पर कॉल करके बताएगी। धन्यवाद।",
    },
    "summary": {
        "hinglish": "{brand}: aapki call ka saaransh. {body}",
        "en": "{brand}: summary of your call. {body}",
        "hi": "{brand}: आपकी कॉल का सारांश। {body}",
    },
    "summary_order": {
        "hinglish": "Order {order} ke baare me baat hui.",
        "en": "We spoke about order {order}.",
        "hi": "ऑर्डर {order} के बारे में बात हुई।",
    },
    "summary_pending": {
        "hinglish": "Aapki request hamari team ke paas hai, woh aapko call karke batayengi.",
        "en": "Your request is with our team, who will call you back.",
        "hi": "आपकी रिक्वेस्ट हमारी टीम के पास है, वे आपको कॉल करके बताएँगे।",
    },
    "summary_callback": {
        "hinglish": "Hamari team aapko isi number par call karegi.",
        "en": "Our team will call you back on this number.",
        "hi": "हमारी टीम आपको इसी नंबर पर कॉल करेगी।",
    },
    "error": {
        "hinglish": "Maaf kijiye, abhi kuch takneeki dikkat aa gayi. Main aapko hamari team se jod raha hoon.",
        "en": "Sorry, we hit a technical problem. I am connecting you to our team.",
        "hi": "माफ़ कीजिए, अभी कुछ तकनीकी दिक्कत आ गई। मैं आपको हमारी टीम से जोड़ रहा हूँ।",
    },
}


def voice_line(template: str, lang: str, **facts: str) -> str:
    language = lang if lang in ("hi", "en", "hinglish") else "hinglish"
    return VOICE_T[template][language].format(**facts)


def tts_language(lang: str) -> Literal["hi-IN", "en-IN"]:
    return "en-IN" if lang == "en" else "hi-IN"


def digits_to_text(digits: str) -> str:
    cleaned = re.sub(r"[^0-9]", "", digits)
    return f"mera order #{cleaned} hai" if cleaned else ""
