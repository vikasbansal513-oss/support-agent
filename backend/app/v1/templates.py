from string import Formatter

from app.agent import templates as v0

T: dict[str, dict[str, str]] = {
    "confirm_order_cancel": {
        "hinglish": "Order {order} ({items}) cancel karna hai? Kul amount Rs {amount} hai. {note}Cancel karne ke liye 'haan' likhein, rehne dene ke liye 'nahi'.",
        "en": "Do you want to cancel order {order} ({items})? The total is Rs {amount}. {note}Reply 'yes' to cancel, or 'no' to keep it.",
        "hi": "क्या आप ऑर्डर {order} ({items}) कैंसिल करना चाहते हैं? कुल रकम Rs {amount} है। {note}कैंसिल के लिए 'हाँ' लिखें, रहने देने के लिए 'नहीं'।",
    },
    "confirm_address_update": {
        "hinglish": "Order {order} ka address ye karna hai: {address}? {note}Confirm ke liye 'haan' likhein, nahi to 'nahi'.",
        "en": "Change the address of order {order} to: {address}? {note}Reply 'yes' to confirm, or 'no'.",
        "hi": "क्या ऑर्डर {order} का पता यह करना है: {address}? {note}कन्फर्म के लिए 'हाँ' लिखें, नहीं तो 'नहीं'।",
    },
    "confirm_exchange_request": {
        "hinglish": "Order {order} me {item} ko {variant} se exchange karna hai? {note}Confirm ke liye 'haan' likhein, nahi to 'nahi'.",
        "en": "Exchange {item} in order {order} for {variant}? {note}Reply 'yes' to confirm, or 'no'.",
        "hi": "क्या ऑर्डर {order} में {item} को {variant} से बदलना है? {note}कन्फर्म के लिए 'हाँ' लिखें, नहीं तो 'नहीं'।",
    },
    "confirm_refund_create": {
        "hinglish": "Order {order} ka Rs {amount} refund shuru karna hai? {note}Confirm ke liye 'haan' likhein, nahi to 'nahi'.",
        "en": "Start a refund of Rs {amount} for order {order}? {note}Reply 'yes' to confirm, or 'no'.",
        "hi": "क्या ऑर्डर {order} का Rs {amount} रिफंड शुरू करना है? {note}कन्फर्म के लिए 'हाँ' लिखें, नहीं तो 'नहीं'।",
    },
    "confirm_return_request": {
        "hinglish": "Order {order} ke liye return request bhejna hai? {note}Confirm ke liye 'haan' likhein, nahi to 'nahi'.",
        "en": "Submit a return request for order {order}? {note}Reply 'yes' to confirm, or 'no'.",
        "hi": "क्या ऑर्डर {order} के लिए रिटर्न रिक्वेस्ट भेजनी है? {note}कन्फर्म के लिए 'हाँ' लिखें, नहीं तो 'नहीं'।",
    },
    "approval_note": {
        "hinglish": "Confirm ke baad hamari team isse dekhkar approve karegi. ",
        "en": "After you confirm, our team will review and approve it. ",
        "hi": "कन्फर्म के बाद हमारी टीम इसे देखकर अप्रूव करेगी। ",
    },
    "no_note": {"hinglish": "", "en": "", "hi": ""},
    # Agent_Reply_TopClass_Notes.md Section 3 / KNOWN_GAPS.md #7: shown before the
    # confirm_* templates below, only when the planner's sentiment for this message is
    # negative (plan.sentiment < 0) - i.e. the customer described a delay/problem, not a
    # plain request. These confirm_* replies are static templates, not LLM-composed, so
    # the reply.v1.md empathy rule does not reach them; this is the template-path version
    # of the same fix.
    "empathy_note": {
        "hinglish": "Isme hui dikkat ke liye maaf kijiye. ",
        "en": "I'm sorry for the trouble. ",
        "hi": "इसमें हुई असुविधा के लिए खेद है। ",
    },
    "ask_address": {
        "hinglish": "Naya address bhejein: ghar/flat, area, city, state aur 6 digit pin code.",
        "en": "Please send the new address: house/flat, area, city, state and 6-digit pin code.",
        "hi": "कृपया नया पता भेजें: घर/फ्लैट, इलाका, शहर, राज्य और 6 अंकों का पिन कोड।",
    },
    "ask_clarify": {
        "hinglish": "Thoda aur batayein, aap kya karwana chahte hain?",
        "en": "Could you tell me a little more about what you need?",
        "hi": "कृपया थोड़ा और बताइए, आप क्या करवाना चाहते हैं?",
    },
    "ask_line_item": {
        "hinglish": "Order {order} me kaunsa item badalna hai aur kis size ya variant me?",
        "en": "Which item in order {order} would you like to exchange, and for which size or variant?",
        "hi": "ऑर्डर {order} में कौन सा आइटम किस साइज़ या वेरिएंट में बदलना है?",
    },
    "ask_evidence_photo": {
        "hinglish": "Isse aage le jaane ke liye product ki clear photo ya video bhejna zaroori hai. Kripya bhejein, hamari team usko dekh kar aapko confirm karegi.",
        "en": "To proceed with this, please send a clear photo or video of the product. Our team will review it and confirm with you.",
        "hi": "आगे बढ़ने के लिए प्रोडक्ट की साफ़ फ़ोटो या वीडियो भेजना ज़रूरी है। कृपया भेजें, हमारी टीम देखकर आपको कन्फर्म करेगी।",
    },
    "confirm_reask": {
        "hinglish": "Kripya sirf 'haan' ya 'nahi' likhein.",
        "en": "Please reply with just 'yes' or 'no'.",
        "hi": "कृपया सिर्फ 'हाँ' या 'नहीं' लिखें।",
    },
    "confirm_expired": {
        "hinglish": "Pichli request ka samay nikal gaya. Kripya dobara batayein.",
        "en": "The earlier request has expired. Please tell me again what you need.",
        "hi": "पिछली रिक्वेस्ट का समय निकल गया। कृपया दोबारा बताइए।",
    },
    "declined": {
        "hinglish": "Theek hai, koi badlav nahi kiya gaya.",
        "en": "Okay, no changes have been made.",
        "hi": "ठीक है, कोई बदलाव नहीं किया गया।",
    },
    "awaiting_team": {
        "hinglish": "Aapki confirmation mil gayi. Hamari team ise dekh rahi hai, update milte hi hum batayenge.",
        "en": "Thanks for confirming. Our team is reviewing it and we will update you as soon as it is done.",
        "hi": "आपका कन्फर्मेशन मिल गया। हमारी टीम इसे देख रही है, अपडेट मिलते ही हम बताएँगे।",
    },
    "done_order_cancel": {
        "hinglish": "Order {order} cancel ho gaya hai.",
        "en": "Order {order} has been cancelled.",
        "hi": "ऑर्डर {order} कैंसिल हो गया है।",
    },
    "done_address_update": {
        "hinglish": "Order {order} ka address update ho gaya hai.",
        "en": "The address for order {order} has been updated.",
        "hi": "ऑर्डर {order} का पता अपडेट हो गया है।",
    },
    "done_exchange_request": {
        "hinglish": "Order {order} ke liye exchange request register ho gayi hai.",
        "en": "The exchange request for order {order} has been registered.",
        "hi": "ऑर्डर {order} के लिए एक्सचेंज रिक्वेस्ट दर्ज हो गई है।",
    },
    "done_refund_create": {
        "hinglish": "Order {order} ka Rs {amount} refund shuru ho gaya hai.",
        "en": "A refund of Rs {amount} has been started for order {order}.",
        "hi": "ऑर्डर {order} का Rs {amount} रिफंड शुरू हो गया है।",
    },
    "done_return_request": {
        "hinglish": "Order {order} ke liye return request register ho gayi hai.",
        "en": "The return request for order {order} has been registered.",
        "hi": "ऑर्डर {order} के लिए रिटर्न रिक्वेस्ट दर्ज हो गई है।",
    },
    "handoff_reference": {
        "hinglish": "Aapka reference number {ref} hai. Dobara contact karte waqt yeh number bataiyega.",
        "en": "Your reference number is {ref}. Please share it if you contact us again about this.",
        "hi": "आपका रेफ़रेंस नंबर {ref} है। दोबारा संपर्क करते समय कृपया यह नंबर बताइए।",
    },
}


# Decision Framework Section 17.2: these are the write-flow confirm/done templates that
# read an order ref and/or a rupee amount as part of a yes/no ask or a just-completed-action
# statement - what the framework means by "an account number, order ID... it is reading
# out", which must not be interruptible mid-number. Plain informational templates
# (order_status, tracking_status, ask_*, awaiting_team, ...) are deliberately NOT in this
# set - they stay interruptible, exactly as the existing voice test
# test_greeting_discloses_ai_and_transcript_and_cannot_be_interrupted already requires for a
# plain order_status answer (which also contains an order number, but is an open-ended
# answer, not a confirm/done commitment - see Section 17.2's own table).
CRITICAL_VOICE_TEMPLATES = frozenset(
    {
        "confirm_order_cancel",
        "confirm_address_update",
        "confirm_exchange_request",
        "confirm_refund_create",
        "confirm_return_request",
        "done_order_cancel",
        "done_address_update",
        "done_exchange_request",
        "done_refund_create",
        "done_return_request",
    }
)


def fields(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def render(template_id: str, language: str, **facts: object) -> str:
    lang = language if language in ("en", "hi", "hinglish") else "hinglish"
    template = T[template_id][lang]
    missing = fields(template) - set(facts)
    if missing:
        raise KeyError(f"template {template_id}/{lang} missing facts: {sorted(missing)}")
    return template.format(**facts)


def rs(paise: int) -> str:
    return v0.paise_to_rs(paise)
