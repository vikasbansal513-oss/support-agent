"""Customer-facing replies are templates, never LLM text (TDD D4). Placeholders are filled only from tool facts."""
from __future__ import annotations

from string import Formatter

T: dict[str, dict[str, str]] = {
    "order_status": {
        "en": "Your order {order_name} ({items}) is {fulfillment}. Payment status: {payment}. Total: Rs {total}.",
        "hinglish": "Aapka order {order_name} ({items}) abhi {fulfillment} hai. Payment status: {payment}. Total: Rs {total}.",
        "hi": "आपका ऑर्डर {order_name} ({items}) अभी {fulfillment} है। भुगतान स्थिति: {payment}। कुल: Rs {total}।",
    },
    "tracking_status": {
        "en": "Order {order_name} is with {courier}, AWB {awb}. Latest status: {status}.",
        "hinglish": "Order {order_name} {courier} ke paas hai, AWB {awb}. Latest status: {status}.",
        "hi": "ऑर्डर {order_name} {courier} के पास है, AWB {awb}। ताज़ा स्थिति: {status}।",
    },
    "ask_order_ref": {
        "en": "Happy to help. Please share your order number (for example #1001).",
        "hinglish": "Zaroor madad karenge. Apna order number bataiye (jaise #1001).",
        "hi": "ज़रूर मदद करेंगे। कृपया अपना ऑर्डर नंबर बताइए (जैसे #1001)।",
    },
    "order_not_found_reask": {
        "en": "We could not find that order for this number. Please check the order number and send it again.",
        "hinglish": "Is number par yeh order nahi mila. Order number check karke dobara bhejiye.",
        "hi": "इस नंबर पर यह ऑर्डर नहीं मिला। कृपया ऑर्डर नंबर जाँचकर दोबारा भेजिए।",
    },
    "tool_unavailable_ack": {
        "en": "We are checking this and our team will get back to you shortly.",
        "hinglish": "Hum isko check kar rahe hain, hamari team jaldi aapse sampark karegi.",
        "hi": "हम इसे जाँच रहे हैं, हमारी टीम जल्द ही आपसे संपर्क करेगी।",
    },
    "handoff_ack": {
        "en": "I have passed this to our team. They will reply to you here. Expected within {sla} minutes during working hours.",
        "hinglish": "Maine yeh hamari team ko de diya hai. Woh yahin aapko reply karenge. Working hours me lagbhag {sla} minute me.",
        "hi": "मैंने यह हमारी टीम को दे दिया है। वे यहीं आपको जवाब देंगे। कार्य समय में लगभग {sla} मिनट में।",
    },
    "off_hours_ack": {
        "en": "Our team is offline right now. We will reply when we open at {opens}.",
        "hinglish": "Hamari team abhi offline hai. {opens} par khulne ke baad reply karenge.",
        "hi": "हमारी टीम अभी ऑफ़लाइन है। {opens} पर खुलने के बाद जवाब देंगे।",
    },
    "media_ack": {
        "en": "Thanks for sharing this. Our team will look at it and reply here.",
        "hinglish": "Share karne ke liye shukriya. Hamari team ise dekhkar yahin reply karegi.",
        "hi": "साझा करने के लिए धन्यवाद। हमारी टीम इसे देखकर यहीं जवाब देगी।",
    },
    "human_requested_ack": {
        "en": "Understood. I am connecting you to our team. They will reply here.",
        "hinglish": "Samajh gaya. Main aapko hamari team se jod raha hoon. Woh yahin reply karenge.",
        "hi": "समझ गया। मैं आपको हमारी टीम से जोड़ रहा हूँ। वे यहीं जवाब देंगे।",
    },
    "ai_disclosure_line": {"en": "{disclosure}", "hinglish": "{disclosure}", "hi": "{disclosure}"},
    "ai_identity_answer": {
        "en": "Yes, I am an AI assistant for {brand}. I can check order status and tracking, and I can connect you to our team anytime.",
        "hinglish": "Haan, main {brand} ka AI assistant hoon. Order status aur tracking check kar sakta hoon, aur kabhi bhi team se jod sakta hoon.",
        "hi": "हाँ, मैं {brand} का AI सहायक हूँ। मैं ऑर्डर स्थिति और ट्रैकिंग देख सकता हूँ, और कभी भी टीम से जोड़ सकता हूँ।",
    },
    "opt_out_confirm": {
        "en": "You will not receive further messages from us. Reply START to restart.",
        "hinglish": "Ab aapko hamari taraf se message nahi aayenge. Dobara shuru karne ke liye START bhejiye.",
        "hi": "अब आपको हमारी ओर से संदेश नहीं आएँगे। दोबारा शुरू करने के लिए START भेजिए।",
    },
    "handoff_reference": {
        "en": "Your reference number is {ref}. Please share it if you contact us again about this.",
        "hinglish": "Aapka reference number {ref} hai. Dobara contact karte waqt yeh number bataiyega.",
        "hi": "आपका रेफ़रेंस नंबर {ref} है। दोबारा संपर्क करते समय कृपया यह नंबर बताइए।",
    },
}

FULFILL = {
    "unfulfilled": {"en": "being prepared", "hinglish": "tayyar ho raha", "hi": "तैयार हो रहा"},
    "fulfilled": {"en": "shipped", "hinglish": "ship ho chuka", "hi": "भेजा जा चुका"},
    "partial": {"en": "partly shipped", "hinglish": "aadha ship ho chuka", "hi": "आंशिक रूप से भेजा गया"},
}
PAY = {"paid": {"en": "paid", "hinglish": "paid", "hi": "भुगतान हो चुका"},
       "pending": {"en": "pending", "hinglish": "pending", "hi": "लंबित"},
       "refunded": {"en": "refunded", "hinglish": "refund ho chuka", "hi": "रिफंड हो चुका"}}


def fields(template: str) -> set[str]:
    return {f for _, f, _, _ in Formatter().parse(template) if f}


def render(template_id: str, language: str, **facts: object) -> str:
    lang = language if language in ("en", "hi", "hinglish") else "hinglish"
    tpl = T[template_id][lang]
    missing = fields(tpl) - set(facts)
    if missing:
        raise KeyError(f"template {template_id}/{lang} missing facts: {sorted(missing)}")
    return tpl.format(**facts)


def paise_to_rs(p: int) -> str:
    return f"{p // 100:,}" if p % 100 == 0 else f"{p / 100:,.2f}"


def status_word(table: dict[str, dict[str, str]], key: str, language: str) -> str:
    return table.get(key, {}).get(language, key)
