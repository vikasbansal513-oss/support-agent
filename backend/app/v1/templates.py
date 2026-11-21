import unicodedata
from collections.abc import Sequence
from string import Formatter

from app.agent import templates as v0
from app.contracts.contracts import OrderFacts
from app.core import timeutil

T: dict[str, dict[str, str]] = {
    "confirm_order_cancel": {
        "hinglish": (
            "{note}Kya aap cancel order *{order}* confirm karna chahte hain?\n\n"
            "- Items: {items}\n"
            "- Total: *Rs {amount}*\n\n"
            "Cancel karne ke liye *haan* likhein, rehne dene ke liye *nahi*."
        ),
        "en": (
            "{note}Would you like us to cancel order *{order}*?\n\n"
            "- Items: {items}\n"
            "- Total: *Rs {amount}*\n\n"
            "Reply *yes* to cancel, or *no* to keep it."
        ),
        "hi": (
            "{note}क्या आप ऑर्डर *{order}* कैंसिल करना चाहते हैं?\n\n"
            "- आइटम: {items}\n"
            "- कुल: *Rs {amount}*\n\n"
            "कैंसिल के लिए *हाँ* लिखें, रहने देने के लिए *नहीं*।"
        ),
    },
    "confirm_address_update": {
        "hinglish": (
            "{note}Kya is order ka address badal dein?\n\n"
            "- Order: *{order}*\n"
            "- Naya address: {address}\n\n"
            "Confirm ke liye *haan* likhein, nahi to *nahi*."
        ),
        "en": (
            "{note}Would you like us to update this order's address?\n\n"
            "- Order: *{order}*\n"
            "- New address: {address}\n\n"
            "Reply *yes* to confirm, or *no*."
        ),
        "hi": (
            "{note}क्या इस ऑर्डर का पता बदल दें?\n\n"
            "- ऑर्डर: *{order}*\n"
            "- नया पता: {address}\n\n"
            "कन्फर्म के लिए *हाँ* लिखें, नहीं तो *नहीं*।"
        ),
    },
    "confirm_exchange_request": {
        "hinglish": (
            "{note}Kya is order me item exchange kar dein?\n\n"
            "- Order: *{order}*\n"
            "- Item: {item}\n"
            "- Naya variant: *{variant}*\n\n"
            "Confirm ke liye *haan* likhein, nahi to *nahi*."
        ),
        "en": (
            "{note}Would you like us to exchange an item in this order?\n\n"
            "- Order: *{order}*\n"
            "- Item: {item}\n"
            "- New variant: *{variant}*\n\n"
            "Reply *yes* to confirm, or *no*."
        ),
        "hi": (
            "{note}क्या इस ऑर्डर में आइटम बदल दें?\n\n"
            "- ऑर्डर: *{order}*\n"
            "- आइटम: {item}\n"
            "- नया वेरिएंट: *{variant}*\n\n"
            "कन्फर्म के लिए *हाँ* लिखें, नहीं तो *नहीं*।"
        ),
    },
    "confirm_refund_create": {
        "hinglish": (
            "{note}Kya is order ka refund shuru kar dein?\n\n"
            "- Order: *{order}*\n"
            "- Refund amount: *Rs {amount}*\n\n"
            "Confirm ke liye *haan* likhein, nahi to *nahi*."
        ),
        "en": (
            "{note}Would you like us to start a refund for this order?\n\n"
            "- Order: *{order}*\n"
            "- Refund amount: *Rs {amount}*\n\n"
            "Reply *yes* to confirm, or *no*."
        ),
        "hi": (
            "{note}क्या इस ऑर्डर का रिफंड शुरू कर दें?\n\n"
            "- ऑर्डर: *{order}*\n"
            "- रिफंड राशि: *Rs {amount}*\n\n"
            "कन्फर्म के लिए *हाँ* लिखें, नहीं तो *नहीं*।"
        ),
    },
    "confirm_return_request": {
        "hinglish": (
            "{note}Kya is order ke liye return request bhej dein?\n\n"
            "- Order: *{order}*\n\n"
            "Confirm ke liye *haan* likhein, nahi to *nahi*."
        ),
        "en": (
            "{note}Would you like us to submit a return request for this order?\n\n"
            "- Order: *{order}*\n\n"
            "Reply *yes* to confirm, or *no*."
        ),
        "hi": (
            "{note}क्या इस ऑर्डर के लिए रिटर्न रिक्वेस्ट भेज दें?\n\n"
            "- ऑर्डर: *{order}*\n\n"
            "कन्फर्म के लिए *हाँ* लिखें, नहीं तो *नहीं*।"
        ),
    },
    "approval_note": {
        "hinglish": "Confirm ke baad hamari team isse dekhkar approve karegi.\n\n",
        "en": "After you confirm, our team will review and approve it.\n\n",
        "hi": "कन्फर्म के बाद हमारी टीम इसे देखकर अप्रूव करेगी।\n\n",
    },
    "no_note": {"hinglish": "", "en": "", "hi": ""},
    # Agent_Reply_TopClass_Notes.md Section 3 / KNOWN_GAPS.md #7: shown before the
    # confirm_* templates below, only when the planner's sentiment for this message is
    # negative (plan.sentiment < 0) - i.e. the customer described a delay/problem, not a
    # plain request. These confirm_* replies are static templates, not LLM-composed, so
    # the reply.v1.md empathy rule does not reach them; this is the template-path version
    # of the same fix.
    "empathy_note": {
        "hinglish": "Isme hui dikkat ke liye maaf kijiye.\n\n",
        "en": "I'm sorry for the trouble.\n\n",
        "hi": "इसमें हुई असुविधा के लिए खेद है।\n\n",
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
    "ask_order_ref_write": {
        # BUGFIX (2026-11-21): this used to be the same English sentence copy-pasted into
        # all three language keys (a dict-comprehension shortcut that was never actually
        # translated) - every other template in this file has distinct hinglish/en/hi text,
        # this one was the one exception. On a voice call this is picked via pick_language()
        # reading the call's own established language (call.language, see Decision
        # Framework / P20 notes), so a Hindi-speaking caller was being read this line in
        # English by the TTS - confirmed via a real production call (2026-11-21, tenant
        # pranjal-project) where the customer heard an English ask-for-order-number line
        # mid-Hindi-call and never supplied an order number, so the order lookup never ran.
        "hinglish": "Khushi hogi aapki madad karke. Kripya apna order number bataiye (jaise *#1001*).",
        "en": "Happy to help with this. Please share your order number (for example *#1001*).",
        "hi": "आपकी मदद करके खुशी होगी। कृपया अपना ऑर्डर नंबर बताएं (जैसे *#1001*)।",
    },
    "ask_line_item": {
        "hinglish": "Order *{order}* me kaunsa item badalna hai aur kis size ya variant me?",
        "en": "Which item in order *{order}* would you like to exchange, and for which size or variant?",
        "hi": "ऑर्डर *{order}* में कौन सा आइटम किस साइज़ या वेरिएंट में बदलना है?",
    },
    "ask_evidence_photo": {
        "hinglish": "Isse aage le jaane ke liye product ki clear photo ya video bhejna zaroori hai. Kripya bhejein, hamari team usko dekh kar aapko confirm karegi.",
        "en": "To proceed with this, please send a clear photo or video of the product. Our team will review it and confirm with you.",
        "hi": "आगे बढ़ने के लिए प्रोडक्ट की साफ़ फ़ोटो या वीडियो भेजना ज़रूरी है। कृपया भेजें, हमारी टीम देखकर आपको कन्फर्म करेगी।",
    },
    # Omnichannel duplicate-request guard (v1/graph.py's propose(), via state["active_claims"]):
    # the same customer already has this same tool+order on ANOTHER conversation. Three honest
    # variants, chosen from that earlier action's real status: still being handled by the team,
    # waiting for the customer's own yes/no, or already completed. The plain versions below are
    # the voice/other-channel text; WhatsApp and email get order-details versions further down.
    "duplicate_request_ack": {
        "hinglish": "Shukriya. Order *{order}* ke liye aapki *{request}* request hume pehle hi mil chuki hai"
        " aur hamari team uspar kaam kar rahi hai - dobara bhejne ki zaroorat nahi."
        " Poora hote hi hum yahin update karenge.",
        "en": "Thank you. We have already received your *{request}* request for order *{order}* and"
        " our team is working on it - there is no need to send it again. We will update you"
        " here once it is done.",
        "hi": "धन्यवाद। ऑर्डर *{order}* के लिए आपका *{request}* अनुरोध हमें पहले ही मिल चुका है और"
        " हमारी टीम उस पर काम कर रही है - दोबारा भेजने की ज़रूरत नहीं है। पूरा होने पर हम यहीं"
        " अपडेट करेंगे।",
    },
    "duplicate_request_awaiting": {
        "hinglish": "Order *{order}* ke liye aapki *{request}* request ki confirmation hum pehle hi"
        " maang chuke hain. Kripya usi message ka jawab *haan* ya *nahi* me dein.",
        "en": "We have already asked you to confirm your *{request}* request for order *{order}*."
        " Please reply *yes* or *no* to that earlier message.",
        "hi": "ऑर्डर *{order}* के लिए आपके *{request}* अनुरोध की पुष्टि हम पहले ही माँग चुके हैं।"
        " कृपया उसी संदेश का जवाब *हाँ* या *नहीं* में दें।",
    },
    "duplicate_request_done": {
        "hinglish": "Order *{order}* ke liye aapki *{request}* request pehle hi poori ho chuki hai."
        " Naya kuch karne ki zaroorat nahi.",
        "en": "Your *{request}* request for order *{order}* has already been completed."
        " No further action is needed.",
        "hi": "ऑर्डर *{order}* के लिए आपका *{request}* अनुरोध पहले ही पूरा हो चुका है।"
        " आगे कुछ करने की ज़रूरत नहीं है।",
    },
    "action_failed_ack": {
        "hinglish": "Aapki request abhi poori nahi ho payi. Hamari team ise dekhkar aapse sampark karegi.",
        "en": "We could not complete your request yet. Our team will review it and get back to you.",
        "hi": "आपका अनुरोध अभी पूरा नहीं हो पाया। हमारी टीम इसे देखकर आपसे संपर्क करेगी।",
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
    "decline_not_shipped": {
        "hinglish": (
            "Order *{order}* abhi delivered nahi hua hai, isliye abhi return, exchange ya refund ki "
            "request nahi ho sakti. Agar aap order cancel karna chahte hain to bata dijiye."
        ),
        "en": (
            "Order *{order}* is not marked as delivered yet, so a return, exchange or refund cannot "
            "be requested right now. If you would like to cancel the order instead, just let me know."
        ),
        "hi": (
            "ऑर्डर *{order}* अभी डिलीवर नहीं हुआ है, इसलिए अभी रिटर्न, एक्सचेंज या रिफंड की रिक्वेस्ट "
            "नहीं हो सकती। अगर आप ऑर्डर कैंसल करना चाहते हैं तो बता दीजिए।"
        ),
    },
    "decline_window_expired": {
        "hinglish": (
            "Maaf kijiye, order *{order}* ki return window (delivery ke {days} din baad tak) khatam ho "
            "chuki hai, isliye ye request nahi ho sakti."
        ),
        "en": (
            "I'm sorry, the return window for order *{order}* ({days} days after delivery) has ended, "
            "so this request cannot be processed."
        ),
        "hi": (
            "क्षमा कीजिए, ऑर्डर *{order}* की रिटर्न विंडो (डिलीवरी के {days} दिन बाद तक) खत्म हो चुकी है, "
            "इसलिए यह रिक्वेस्ट नहीं हो सकती।"
        ),
    },
    "decline_order_closed": {
        "hinglish": "Order *{order}* pehle hi cancel ya refund ho chuka hai, isliye ispar ab kuch aur nahi ho sakta.",
        "en": "Order *{order}* has already been cancelled or refunded, so nothing more can be done on it.",
        "hi": "ऑर्डर *{order}* पहले ही कैंसल या रिफंड हो चुका है, इसलिए इस पर अब कुछ और नहीं हो सकता।",
    },
    "decline_already_requested": {
        "hinglish": "Order *{order}* ke liye is tarah ki request pehle se darj hai, isliye dusri request nahi ban sakti.",
        "en": "A request of this kind is already registered for order *{order}*, so I cannot create another one.",
        "hi": "ऑर्डर *{order}* के लिए इस तरह की रिक्वेस्ट पहले से दर्ज है, इसलिए दूसरी रिक्वेस्ट नहीं बन सकती।",
    },
    "awaiting_team": {
        "hinglish": "Aapki confirmation mil gayi. Hamari team ise dekh rahi hai, update milte hi hum batayenge.",
        "en": "Thanks for confirming. Our team is reviewing it and we will update you as soon as it is done.",
        "hi": "आपका कन्फर्मेशन मिल गया। हमारी टीम इसे देख रही है, अपडेट मिलते ही हम बताएँगे।",
    },
    "done_order_cancel": {
        "hinglish": "✅ Order *{order}* cancel ho gaya hai.",
        "en": "✅ Order *{order}* has been cancelled.",
        "hi": "✅ ऑर्डर *{order}* कैंसिल हो गया है।",
    },
    "done_address_update": {
        "hinglish": "✅ Order *{order}* ka address update ho gaya hai.",
        "en": "✅ The address for order *{order}* has been updated.",
        "hi": "✅ ऑर्डर *{order}* का पता अपडेट हो गया है।",
    },
    "done_exchange_request": {
        "hinglish": "✅ Order *{order}* ke liye exchange request register ho gayi hai.",
        "en": "✅ The exchange request for order *{order}* has been registered.",
        "hi": "✅ ऑर्डर *{order}* के लिए एक्सचेंज रिक्वेस्ट दर्ज हो गई है।",
    },
    "done_refund_create": {
        "hinglish": "✅ Order *{order}* ka Rs {amount} refund shuru ho gaya hai.",
        "en": "✅ A refund of *Rs {amount}* has been started for order *{order}*.",
        "hi": "✅ ऑर्डर *{order}* का Rs {amount} रिफंड शुरू हो गया है।",
    },
    "done_return_request": {
        "hinglish": "✅ Order *{order}* ke liye return request register ho gayi hai.",
        "en": "✅ The return request for order *{order}* has been registered.",
        "hi": "✅ ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट दर्ज हो गई है।",
    },
    "handoff_reference": {
        "hinglish": "Aapka reference number *{ref}* hai. Dobara contact karte waqt yeh number bataiyega.",
        "en": "Your reference number is *{ref}*. Please share it if you contact us again about this.",
        "hi": "आपका रेफ़रेंस नंबर *{ref}* है। दोबारा संपर्क करते समय कृपया यह नंबर बताइए।",
    },
}


DECLINE_TEMPLATE: dict[str, str] = {
    "NOT_DELIVERED": "decline_not_shipped",
    "WINDOW_EXPIRED": "decline_window_expired",
    "ORDER_CLOSED": "decline_order_closed",
    "ALREADY_REFUNDED": "decline_order_closed",
    "RETURN_EXISTS": "decline_already_requested",
    "EXCHANGE_EXISTS": "decline_already_requested",
}


# WhatsApp-only restyle of the 10 confirm_*/done_* write-flow templates: emoji anchors,
# short lines, a closing signature - the look WhatsApp customers expect, matching
# WhatsApp's own *bold* syntax. Selected in render() only when channel == "whatsapp"
# and only for the ids below; every other id, and every other channel (voice, email,
# anything else), keeps using T untouched above - voice TTS in particular must not
# gain emoji or a sign-off line it would end up reading aloud oddly. Deliberately not
# used as the email body either: email gets its own HTML rendering (see
# core/html_email.py) built from the plain T text, not from this WhatsApp copy.
WA_T: dict[str, dict[str, str]] = {
    "confirm_order_cancel": {
        "hinglish": (
            "{note}Aapne order *{order}* cancel karne ko kaha hai.\n\n"
            "{details}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Order cancel karein\n"
            "❌ *NAHI* – Order rakhein\n\n"
            "Confirm hote hi update kar denge.\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}We've received your request to cancel order *{order}*.\n\n"
            "{details}\n\n"
            "Please reply:\n"
            "✅ *YES* – Cancel order\n"
            "❌ *NO* – Keep order\n\n"
            "We'll update you right after confirmation.\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}आपने ऑर्डर *{order}* कैंसिल करने के लिए कहा है।\n\n"
            "{details}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – ऑर्डर कैंसिल करें\n"
            "❌ *नहीं* – ऑर्डर रखें\n\n"
            "कन्फर्म होते ही अपडेट कर देंगे।\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_address_update": {
        "hinglish": (
            "{note}Order *{order}* ka delivery address update karna hai?\n\n"
            "{details}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Address update karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Update the delivery address for order *{order}*?\n\n"
            "{details}\n\n"
            "Please reply:\n"
            "✅ *YES* – Update address\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* का डिलीवरी पता अपडेट करें?\n\n"
            "{details}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – पता अपडेट करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_exchange_request": {
        "hinglish": (
            "{note}Order *{order}* me item exchange karna hai?\n\n"
            "{details}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Exchange shuru karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Exchange an item in order *{order}*?\n\n"
            "{details}\n\n"
            "Please reply:\n"
            "✅ *YES* – Start the exchange\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* में आइटम बदलें?\n\n"
            "{details}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – एक्सचेंज शुरू करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_refund_create": {
        "hinglish": (
            "{note}Order *{order}* ka refund shuru karein?\n\n"
            "{details}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Refund shuru karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Start a refund for order *{order}*?\n\n"
            "{details}\n\n"
            "Please reply:\n"
            "✅ *YES* – Start the refund\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* का रिफंड शुरू करें?\n\n"
            "{details}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – रिफंड शुरू करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_return_request": {
        "hinglish": (
            "{note}Order *{order}* ke liye return request bhejein?\n\n"
            "{details}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Return request bhejein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Submit a return request for order *{order}*?\n\n"
            "{details}\n\n"
            "Please reply:\n"
            "✅ *YES* – Submit the return\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट भेजें?\n\n"
            "{details}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – रिटर्न रिक्वेस्ट भेजें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "done_order_cancel": {
        "hinglish": "✅ Order *{order}* cancel ho gaya hai.\n\n{details}\n\n— {brand} Support",
        "en": "✅ Order *{order}* has been cancelled.\n\n{details}\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* कैंसिल हो गया है।\n\n{details}\n\n— {brand} Support",
    },
    "done_address_update": {
        "hinglish": "✅ Order *{order}* ka address update ho gaya hai.\n\n{details}\n\n— {brand} Support",
        "en": "✅ The address for order *{order}* has been updated.\n\n{details}\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* का पता अपडेट हो गया है।\n\n{details}\n\n— {brand} Support",
    },
    "done_exchange_request": {
        "hinglish": "✅ Order *{order}* ke liye exchange request register ho gayi hai.\n\n{details}\n\n— {brand} Support",
        "en": "✅ The exchange request for order *{order}* has been registered.\n\n{details}\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* के लिए एक्सचेंज रिक्वेस्ट दर्ज हो गई है।\n\n{details}\n\n— {brand} Support",
    },
    "done_refund_create": {
        "hinglish": "✅ Order *{order}* ka Rs {amount} refund shuru ho gaya hai.\n\n{details}\n\n— {brand} Support",
        "en": "✅ A refund of *Rs {amount}* has been started for order *{order}*.\n\n{details}\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* का Rs {amount} रिफंड शुरू हो गया है।\n\n{details}\n\n— {brand} Support",
    },
    "done_return_request": {
        "hinglish": "✅ Order *{order}* ke liye return request register ho gayi hai.\n\n{details}\n\n— {brand} Support",
        "en": "✅ The return request for order *{order}* has been registered.\n\n{details}\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट दर्ज हो गई है।\n\n{details}\n\n— {brand} Support",
    },
    "awaiting_team": {
        "hinglish": "Aapki confirmation mil gayi. Hamari team ise dekh rahi hai, update milte hi hum batayenge.\n\n{details}\n\n— {brand} Support",
        "en": "Thanks for confirming. Our team is reviewing it and we will update you as soon as it is done.\n\n{details}\n\n— {brand} Support",
        "hi": "आपका कन्फर्मेशन मिल गया। हमारी टीम इसे देख रही है, अपडेट मिलते ही हम बताएँगे।\n\n{details}\n\n— {brand} Support",
    },
    "action_failed_ack": {
        "hinglish": "Aapki request abhi poori nahi ho payi. Hamari team ise dekhkar aapse sampark karegi.\n\n{details}\n\n— {brand} Support",
        "en": "We could not complete your request yet. Our team will review it and get back to you.\n\n{details}\n\n— {brand} Support",
        "hi": "आपका अनुरोध अभी पूरा नहीं हो पाया। हमारी टीम इसे देखकर आपसे संपर्क करेगी।\n\n{details}\n\n— {brand} Support",
    },
    "duplicate_request_ack": {
        "hinglish": (
            "Shukriya. Order *{order}* ke liye aapki request hume pehle hi mil chuki hai aur hamari team uspar kaam kar rahi hai - dobara bhejne ki zaroorat nahi.\n\n"
            "{details}\n\n"
            "Poora hote hi hum yahin update karenge.\n\n— {brand} Support"
        ),
        "en": (
            "Thank you. We have already received your request for order *{order}* and our team is working on it - there is no need to send it again.\n\n"
            "{details}\n\n"
            "We will update you here once it is done.\n\n— {brand} Support"
        ),
        "hi": (
            "धन्यवाद। ऑर्डर *{order}* के लिए आपका अनुरोध हमें पहले ही मिल चुका है और हमारी टीम उस पर काम कर रही है - दोबारा भेजने की ज़रूरत नहीं है।\n\n"
            "{details}\n\n"
            "पूरा होने पर हम यहीं अपडेट करेंगे।\n\n— {brand} Support"
        ),
    },
    "duplicate_request_awaiting": {
        "hinglish": (
            "Order *{order}* ke liye aapki request ki confirmation hum pehle hi maang chuke hain. Kripya usi message ka jawab *haan* ya *nahi* me dein.\n\n"
            "{details}\n\n— {brand} Support"
        ),
        "en": (
            "We have already asked you to confirm your request for order *{order}*. Please reply *yes* or *no* to that earlier message.\n\n"
            "{details}\n\n— {brand} Support"
        ),
        "hi": (
            "ऑर्डर *{order}* के लिए आपके अनुरोध की पुष्टि हम पहले ही माँग चुके हैं। कृपया उसी संदेश का जवाब *हाँ* या *नहीं* में दें।\n\n"
            "{details}\n\n— {brand} Support"
        ),
    },
    "duplicate_request_done": {
        "hinglish": (
            "Order *{order}* ke liye aapki request pehle hi poori ho chuki hai. Naya kuch karne ki zaroorat nahi.\n\n"
            "{details}\n\n— {brand} Support"
        ),
        "en": (
            "Your request for order *{order}* has already been completed. No further action is needed.\n\n"
            "{details}\n\n— {brand} Support"
        ),
        "hi": (
            "ऑर्डर *{order}* के लिए आपका अनुरोध पहले ही पूरा हो चुका है। आगे कुछ करने की ज़रूरत नहीं है।\n\n"
            "{details}\n\n— {brand} Support"
        ),
    },
}


# Email-only restyle of the 5 confirm_* write-flow templates: a name-or-"Hi there," greeting
# and a structured order-details table (see core/html_email.py's TABLE_START/TABLE_END
# markup, built by email_facts() below) instead of a "- " bullet list. Selected in render()
# only when channel == "email" and only for these 5 ids; done_* stays on the plain T dict
# above (already clean after HTML conversion - a one-line "action is done" statement gains
# nothing from a table) and every other channel (whatsapp, voice) is untouched.
EMAIL_T: dict[str, dict[str, str]] = {
    "confirm_order_cancel": {
        "hinglish": (
            "{greeting}\n\n{note}Aapne order *{order}* cancel karne ki request ki hai.\n\n"
            "Aapke order ki details:\n\n{items_table}\n\n"
            "Confirm karne ke liye reply karein:\n\n"
            "- *HAAN* – cancellation aage badhayein\n"
            "- *NAHI* – order rakhein\n"
        ),
        "en": (
            "{greeting}\n\n{note}Thank you for reaching out regarding your request to cancel "
            "order *{order}*.\n\nHere are your order details:\n\n{items_table}\n\n"
            "Please confirm by replying:\n\n"
            "- *YES* — to proceed with the cancellation\n"
            "- *NO* — to keep your order as is\n"
        ),
        "hi": (
            "{greeting}\n\n{note}आपने ऑर्डर *{order}* "
            "कैंसिल करने का "
            "अनुरोध किया है।\n\n"
            "आपके ऑर्डर का "
            "विवरण:\n\n{items_table}\n\n"
            "कृपया पुष्टि करने "
            "के लिए रिप्लाई "
            "करें:\n\n"
            "- *हाँ* – कैंसिलेशन "
            "आगे बढ़ाएं\n"
            "- *नहीं* – ऑर्डर रखें\n"
        ),
    },
    "confirm_address_update": {
        "hinglish": (
            "{greeting}\n\n{note}Aapne order *{order}* ka delivery address update karne ko kaha hai.\n\n"
            "{items_table}\n\n"
            "Confirm karne ke liye reply karein:\n\n"
            "- *HAAN* – address update karein\n"
            "- *NAHI* – rehne dein\n"
        ),
        "en": (
            "{greeting}\n\n{note}You asked us to update the delivery address for order *{order}*.\n\n"
            "{items_table}\n\n"
            "Please confirm by replying:\n\n"
            "- *YES* — to update the address\n"
            "- *NO* — to leave it as is\n"
        ),
        "hi": (
            "{greeting}\n\n{note}आपने ऑर्डर *{order}* "
            "का डिलीवरी पता "
            "अपडेट करने के लिए "
            "कहा है।\n\n{items_table}\n\n"
            "कृपया पुष्टि करने "
            "के लिए रिप्लाई "
            "करें:\n\n"
            "- *हाँ* – पता अपडेट "
            "करें\n"
            "- *नहीं* – रहने दें\n"
        ),
    },
    "confirm_exchange_request": {
        "hinglish": (
            "{greeting}\n\n{note}Aapne order *{order}* me item exchange karne ko kaha hai.\n\n"
            "{items_table}\n\n"
            "Confirm karne ke liye reply karein:\n\n"
            "- *HAAN* – exchange shuru karein\n"
            "- *NAHI* – rehne dein\n"
        ),
        "en": (
            "{greeting}\n\n{note}You asked to exchange an item in order *{order}*.\n\n"
            "{items_table}\n\n"
            "Please confirm by replying:\n\n"
            "- *YES* — to start the exchange\n"
            "- *NO* — to leave it as is\n"
        ),
        "hi": (
            "{greeting}\n\n{note}आपने ऑर्डर *{order}* "
            "में आइटम बदलने के "
            "लिए कहा है।\n\n{items_table}\n\n"
            "कृपया पुष्टि करने "
            "के लिए रिप्लाई "
            "करें:\n\n"
            "- *हाँ* – एक्सचेंज "
            "शुरू करें\n"
            "- *नहीं* – रहने दें\n"
        ),
    },
    "confirm_refund_create": {
        "hinglish": (
            "{greeting}\n\n{note}Aapne order *{order}* ka refund shuru karne ko kaha hai.\n\n"
            "{items_table}\n\n"
            "Confirm karne ke liye reply karein:\n\n"
            "- *HAAN* – refund shuru karein\n"
            "- *NAHI* – rehne dein\n"
        ),
        "en": (
            "{greeting}\n\n{note}You asked us to start a refund for order *{order}*.\n\n"
            "{items_table}\n\n"
            "Please confirm by replying:\n\n"
            "- *YES* — to start the refund\n"
            "- *NO* — to leave it as is\n"
        ),
        "hi": (
            "{greeting}\n\n{note}आपने ऑर्डर *{order}* "
            "का रिफंड शुरू करने "
            "के लिए कहा है।\n\n{items_table}\n\n"
            "कृपया पुष्टि करने "
            "के लिए रिप्लाई "
            "करें:\n\n"
            "- *हाँ* – रिफंड शुरू "
            "करें\n"
            "- *नहीं* – रहने दें\n"
        ),
    },
    "confirm_return_request": {
        "hinglish": (
            "{greeting}\n\n{note}Aapne order *{order}* ke liye return request bhejne ko kaha hai.\n\n"
            "{items_table}\n\n"
            "Confirm karne ke liye reply karein:\n\n"
            "- *HAAN* – return request bhejein\n"
            "- *NAHI* – rehne dein\n"
        ),
        "en": (
            "{greeting}\n\n{note}You asked us to submit a return request for order *{order}*.\n\n"
            "{items_table}\n\n"
            "Please confirm by replying:\n\n"
            "- *YES* — to submit the return\n"
            "- *NO* — to leave it as is\n"
        ),
        "hi": (
            "{greeting}\n\n{note}आपने ऑर्डर *{order}* "
            "के लिए रिटर्न रिक्"
            "वेस्ट भेजने के लिए "
            "कहा है।\n\n{items_table}\n\n"
            "कृपया पुष्टि करने "
            "के लिए रिप्लाई "
            "करें:\n\n"
            "- *हाँ* – रिटर्न रिक्"
            "वेस्ट भेजें\n"
            "- *नहीं* – रहने दें\n"
        ),
    },
    "done_order_cancel": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ki cancellation poori ho gayi hai.\n\n"
            "Aapki request ka saransh:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nWe are writing to confirm that order *{order}* has been cancelled "
            "as requested.\n\nHere is a summary of your request:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ऑर्डर *{order}* की कैंसिलेशन पूरी हो गई है।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "done_address_update": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ka delivery address update ho gaya hai.\n\n"
            "Aapki request ka saransh:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nWe are writing to confirm that the delivery address for order "
            "*{order}* has been updated.\n\nHere is a summary of your request:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ऑर्डर *{order}* का डिलीवरी पता अपडेट हो गया है।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "done_exchange_request": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ke liye exchange request register ho gayi hai.\n\n"
            "Aapki request ka saransh:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nWe are writing to confirm that your exchange request for order "
            "*{order}* has been registered.\n\nHere is a summary of your request:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ऑर्डर *{order}* के लिए एक्सचेंज रिक्वेस्ट दर्ज हो गई है।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "done_refund_create": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ka Rs {amount} ka refund shuru ho gaya hai.\n\n"
            "Aapki request ka saransh:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nWe are writing to confirm that a refund of *Rs {amount}* has been "
            "started for order *{order}*.\n\nHere is a summary of your request:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ऑर्डर *{order}* का Rs {amount} का रिफंड शुरू हो गया है।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "done_return_request": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ke liye return request register ho gayi hai.\n\n"
            "Aapki request ka saransh:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nWe are writing to confirm that your return request for order "
            "*{order}* has been registered.\n\nHere is a summary of your request:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट दर्ज हो गई है।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "awaiting_team": {
        "hinglish": (
            "{greeting}\n\nAapki confirmation ke liye shukriya. Hamari team aapki request dekh rahi hai "
            "aur poora hote hi hum aapko email se batayenge.\n\n"
            "Aapki request ka saransh:\n\n{details}\n"
        ),
        "en": (
            "{greeting}\n\nThank you for confirming. Our team is now reviewing your request and "
            "we will email you as soon as it is completed.\n\n"
            "Here is a summary of your request:\n\n{details}\n"
        ),
        "hi": (
            "{greeting}\n\nपुष्टि के लिए धन्यवाद। हमारी टीम आपके अनुरोध की समीक्षा कर रही है और "
            "पूरा होते ही हम आपको ईमेल से सूचित करेंगे।\n\n"
            "आपके अनुरोध का सारांश:\n\n{details}\n"
        ),
    },
    "action_failed_ack": {
        "hinglish": (
            "{greeting}\n\nAapki request abhi poori nahi ho payi. Hamari team ise dekhkar aapse "
            "sampark karegi.\n\nAapki request ka saransh:\n\n{details}\n"
        ),
        "en": (
            "{greeting}\n\nWe were unable to complete your request at this time. Our team will "
            "review it and get back to you shortly.\n\n"
            "Here is a summary of your request:\n\n{details}\n"
        ),
        "hi": (
            "{greeting}\n\nआपका अनुरोध अभी पूरा नहीं हो पाया। हमारी टीम इसे देखकर आपसे जल्द संपर्क करेगी।"
            "\n\nआपके अनुरोध का सारांश:\n\n{details}\n"
        ),
    },
    "duplicate_request_ack": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ki *{request}* request hume pehle hi mil chuki hai "
            "aur hamari team uspar kaam kar rahi hai. Dobara bhejne ki zaroorat nahi hai.\n\n"
            "Aapke order ki details:\n\n{details}\n\n"
            "Poora hote hi hum aapko email se update karenge.\n"
        ),
        "en": (
            "{greeting}\n\nThank you for your email. We have already received your *{request}* "
            "request for order *{order}*, and our team is working on it. There is no need to send "
            "it again.\n\nHere are your order details:\n\n{details}\n\n"
            "We will update you by email as soon as it is completed.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ईमेल के लिए धन्यवाद। ऑर्डर *{order}* के लिए आपका *{request}* अनुरोध "
            "हमें पहले ही मिल चुका है और हमारी टीम उस पर काम कर रही है। दोबारा भेजने की ज़रूरत नहीं है।"
            "\n\nआपके ऑर्डर का विवरण:\n\n{details}\n\n"
            "पूरा होते ही हम आपको ईमेल से अपडेट करेंगे।\n"
        ),
    },
    "duplicate_request_awaiting": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ki *{request}* request ki confirmation hum pehle hi "
            "maang chuke hain. Kripya usi email ka jawab *HAAN* ya *NAHI* me dein.\n\n"
            "Aapke order ki details:\n\n{details}\n"
        ),
        "en": (
            "{greeting}\n\nThank you for your email. We have already asked you to confirm your "
            "*{request}* request for order *{order}*. Please reply *YES* or *NO* to that earlier "
            "email and we will proceed.\n\nHere are your order details:\n\n{details}\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ईमेल के लिए धन्यवाद। ऑर्डर *{order}* के *{request}* अनुरोध की पुष्टि "
            "हम पहले ही माँग चुके हैं। कृपया उसी ईमेल का जवाब *हाँ* या *नहीं* में दें।\n\n"
            "आपके ऑर्डर का विवरण:\n\n{details}\n"
        ),
    },
    "duplicate_request_done": {
        "hinglish": (
            "{greeting}\n\nAapke order *{order}* ki *{request}* request pehle hi poori ho chuki hai. "
            "Aage kuch karne ki zaroorat nahi hai.\n\n"
            "Aapke order ki details:\n\n{details}\n\n"
            "Kisi bhi sawaal ke liye kripya is email ka reply karein.\n"
        ),
        "en": (
            "{greeting}\n\nThank you for your email. Your *{request}* request for order *{order}* "
            "has already been completed, so no further action is needed.\n\n"
            "Here are your order details:\n\n{details}\n\n"
            "If you have any questions, please reply to this email.\n"
        ),
        "hi": (
            "{greeting}\n\nआपके ईमेल के लिए धन्यवाद। ऑर्डर *{order}* का आपका *{request}* अनुरोध "
            "पहले ही पूरा हो चुका है, इसलिए आगे कुछ करने की ज़रूरत नहीं है।\n\n"
            "आपके ऑर्डर का विवरण:\n\n{details}\n\n"
            "किसी भी प्रश्न के लिए कृपया इस ईमेल का उत्तर दें।\n"
        ),
    },
    "handoff_ack_order": {
        "hinglish": (
            "{greeting}\n\n{note}Humse sampark karne ke liye shukriya. {request_note}\n\n"
            "Aapke order ki details:\n\n{order_block}\n\n"
            "Maine aapki request hamari support team ko de di hai. Woh ise dekhkar aapko is email par "
            "jawab denge.\n"
        ),
        "en": (
            "{greeting}\n\n{note}Thank you for contacting us. {request_note}\n\n"
            "Here are your order details:\n\n{order_block}\n\n"
            "We have forwarded your request to our support team. They will review it and reply to "
            "you by email.\n"
        ),
        "hi": (
            "{greeting}\n\n{note}हमसे संपर्क करने के लिए धन्यवाद। {request_note}\n\n"
            "आपके ऑर्डर का विवरण:\n\n{order_block}\n\n"
            "हमने आपका अनुरोध हमारी सपोर्ट टीम को भेज दिया है। वे इसकी समीक्षा करके आपको ईमेल से "
            "जवाब देंगे।\n"
        ),
    },
    "off_hours_ack_order": {
        "hinglish": (
            "{greeting}\n\n{note}Humse sampark karne ke liye shukriya. {request_note}\n\n"
            "Aapke order ki details:\n\n{order_block}\n\n"
            "Hamari team abhi offline hai. *{opens}* par khulne ke baad hum is par follow up karenge.\n"
        ),
        "en": (
            "{greeting}\n\n{note}Thank you for contacting us. {request_note}\n\n"
            "Here are your order details:\n\n{order_block}\n\n"
            "Our team is currently offline. We will follow up on this when we open at *{opens}*.\n"
        ),
        "hi": (
            "{greeting}\n\n{note}हमसे संपर्क करने के लिए धन्यवाद। {request_note}\n\n"
            "आपके ऑर्डर का विवरण:\n\n{order_block}\n\n"
            "हमारी टीम अभी ऑफ़लाइन है। *{opens}* पर खुलने के बाद हम इस पर फ़ॉलो अप करेंगे।\n"
        ),
    },
}


_ASK_ORDER_REF_EMAIL = (
    "{greeting}\n\n"
    "Thank you for contacting us. To help with your request, please reply with your order "
    "number (for example *#1001*).\n\n"
    "If you placed the order using a different email address, please reply from that "
    "registered email address instead."
)
EMAIL_T["ask_order_ref_write"] = {lang: _ASK_ORDER_REF_EMAIL for lang in ("en", "hinglish", "hi")}

_DECLINE_SUMMARY = {
    "en": "Here is a summary of your order:",
    "hinglish": "Aapke order ka saransh:",
    "hi": "आपके ऑर्डर का सारांश:",
}
for _decline_id in set(DECLINE_TEMPLATE.values()):
    EMAIL_T[_decline_id] = {
        _lang: f"{{greeting}}\n\n{T[_decline_id][_lang]}\n\n{_DECLINE_SUMMARY[_lang]}\n\n{{details}}\n"
        for _lang in ("en", "hinglish", "hi")
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


def render(template_id: str, language: str, channel: str | None = None, **facts: object) -> str:
    lang = language if language in ("en", "hi", "hinglish") else "hinglish"
    if channel == "whatsapp" and template_id in WA_T:
        source = WA_T
    elif channel == "email" and template_id in EMAIL_T:
        source = EMAIL_T
    else:
        source = T
    template = source[template_id][lang]
    missing = fields(template) - set(facts)
    if missing:
        raise KeyError(f"template {template_id}/{lang} missing facts: {sorted(missing)}")
    return template.format(**facts)


def rs(paise: int) -> str:
    return v0.paise_to_rs(paise)


_GREETING = {
    "en": ("Dear {name},", "Dear Customer,"),
    "hinglish": ("Dear {name},", "Dear Customer,"),
    "hi": ("प्रिय {name},", "प्रिय ग्राहक,"),
}


def first_name(raw: str | None) -> str | None:
    """The sender's first name for a salutation, or None. The name is customer-supplied text
    (an email From header), so only letters (any script), marks, apostrophes and hyphens
    survive, an address or a number never becomes a name, and it is length-capped."""
    parts = (raw or "").split()
    token = parts[0] if parts else ""
    if "@" in token:
        return None
    kept = "".join(
        ch for ch in token if ch.isalpha() or unicodedata.category(ch)[0] == "M" or ch in "'-"
    )[:30]
    if not any(ch.isalpha() for ch in kept):
        return None
    return kept[:1].upper() + kept[1:]


def greeting(lang: str, name: str | None = None) -> str:
    """Formal email salutation with the sender's own first name; "Dear Customer," when there
    is none. Callers pass the name of the person writing (never the Shopify order's name -
    an order can be placed by someone else)."""
    name_tpl, no_name = _GREETING.get(lang, _GREETING["en"])
    clean = first_name(name)
    return name_tpl.format(name=clean) if clean else no_name


_DETAIL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "head_key": "Detail",
        "head_value": "Information",
        "order": "Order ID",
        "date": "Order date",
        "items": "Items",
        "total": "Order total",
        "payment": "Payment status",
        "delivery": "Delivery status",
        "tracking": "Tracking",
        "request": "Request",
        "status": "Request status",
        "refund_amount": "Refund amount",
        "new_address": "New delivery address",
        "exchange_item": "Item to exchange",
        "new_variant": "New variant",
    },
    "hinglish": {
        "head_key": "Detail",
        "head_value": "Jaankari",
        "order": "Order ID",
        "date": "Order date",
        "items": "Items",
        "total": "Order total",
        "payment": "Payment status",
        "delivery": "Delivery status",
        "tracking": "Tracking",
        "request": "Request",
        "status": "Request status",
        "refund_amount": "Refund amount",
        "new_address": "Naya delivery address",
        "exchange_item": "Exchange ka item",
        "new_variant": "Naya variant",
    },
    "hi": {
        "head_key": "विवरण",
        "head_value": "जानकारी",
        "order": "ऑर्डर आईडी",
        "date": "ऑर्डर की तारीख",
        "items": "आइटम",
        "total": "ऑर्डर कुल",
        "payment": "भुगतान स्थिति",
        "delivery": "डिलीवरी स्थिति",
        "tracking": "ट्रैकिंग",
        "request": "अनुरोध",
        "status": "अनुरोध की स्थिति",
        "refund_amount": "रिफंड राशि",
        "new_address": "नया डिलीवरी पता",
        "exchange_item": "बदलने वाला आइटम",
        "new_variant": "नया वेरिएंट",
    },
}

# The kind of request, named once for every reply (details table row + duplicate-guard text).
REQUEST_NOUN: dict[str, dict[str, str]] = {
    "en": {
        "order_cancel": "Order cancellation",
        "refund_create": "Refund",
        "return_request": "Return request",
        "exchange_request": "Exchange request",
        "address_update": "Address change",
    },
    "hinglish": {
        "order_cancel": "Order cancel",
        "refund_create": "Refund",
        "return_request": "Return request",
        "exchange_request": "Exchange request",
        "address_update": "Address change",
    },
    "hi": {
        "order_cancel": "ऑर्डर कैंसिलेशन",
        "refund_create": "रिफंड",
        "return_request": "रिटर्न रिक्वेस्ट",
        "exchange_request": "एक्सचेंज रिक्वेस्ट",
        "address_update": "पता बदलना",
    },
}

# Real state of the request, shown as one more details row where it is known.
REQUEST_STATUS: dict[str, dict[str, str]] = {
    "en": {
        "completed": "Completed",
        "processing": "Being processed by our team",
        "awaiting": "Awaiting your confirmation",
        "review": "Under review by our team",
    },
    "hinglish": {
        "completed": "Poori ho chuki hai",
        "processing": "Hamari team process kar rahi hai",
        "awaiting": "Aapki confirmation ka intezaar hai",
        "review": "Hamari team review kar rahi hai",
    },
    "hi": {
        "completed": "पूरी हो चुकी है",
        "processing": "हमारी टीम प्रोसेस कर रही है",
        "awaiting": "आपकी पुष्टि की प्रतीक्षा है",
        "review": "हमारी टीम समीक्षा कर रही है",
    },
}

DETAIL_CHANNELS = ("email", "whatsapp")


def _cell(value: str) -> str:
    return " ".join(str(value).replace("|", "/").split())


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def order_details(
    channel: str,
    lang: str,
    order: OrderFacts,
    extra: Sequence[tuple[str, str]] = (),
    include_status: bool = True,
    delivery_text: str | None = None,
) -> str:
    """Full order details block for customer replies, built only from verified order facts
    (never from LLM text). Email gets the TABLE_START/TABLE_END markup core/html_email.py
    turns into a real HTML table; every other channel gets "- Label: *value*" lines.
    include_status=False drops payment/delivery for messages that can outlive the order
    snapshot they were built from (post-confirmation outcome messages). delivery_text
    replaces the plain delivery-status word with a richer line (shipped, expected date)."""
    labels = _DETAIL_LABELS.get(lang, _DETAIL_LABELS["en"])
    items = ", ".join(f"{ln.title} x{ln.quantity}" for ln in order.lines)[:200] or "-"
    rows: list[tuple[str, str]] = [
        (labels["order"], order.order_name),
        (labels["date"], timeutil.to_ist(order.created_at).strftime("%d %b %Y")),
        (labels["items"], items),
        (labels["total"], f"Rs {rs(order.total_paise)}"),
    ]
    if include_status:
        rows.append(
            (labels["payment"], _cap(v0.status_word(v0.PAY, order.financial_status, lang))),
        )
        rows.append(
            (
                labels["delivery"],
                delivery_text or _cap(v0.status_word(v0.FULFILL, order.fulfillment_status, lang)),
            ),
        )
        if order.tracking_numbers and not delivery_text:
            carrier = f"{order.courier} " if order.courier else ""
            rows.append((labels["tracking"], f"{carrier}{order.tracking_numbers[0]}"))
    rows.extend(extra)
    if channel == "email":
        lines = ["TABLE_START", f"{_cell(labels['head_key'])}|{_cell(labels['head_value'])}"]
        lines.extend(f"{_cell(k)}|{_cell(v)}" for k, v in rows)
        lines.append("TABLE_END")
        return "\n".join(lines)
    return "\n".join(f"- {k}: *{_cell(v)}*" for k, v in rows)


def request_rows(
    lang: str, tool: str | None, facts: dict[str, object], status: str | None = None
) -> list[tuple[str, str]]:
    """Request-specific rows: what was asked, its real status (when known), and the tool's own
    facts (refund amount, new address, exchange item/variant)."""
    labels = _DETAIL_LABELS.get(lang, _DETAIL_LABELS["en"])
    rows: list[tuple[str, str]] = []
    if tool:
        noun = REQUEST_NOUN.get(lang, REQUEST_NOUN["en"]).get(tool)
        if noun:
            rows.append((labels["request"], noun))
    if status:
        rows.append((labels["status"], REQUEST_STATUS.get(lang, REQUEST_STATUS["en"])[status]))
    if tool == "refund_create" and facts.get("amount"):
        rows.append((labels["refund_amount"], f"Rs {facts['amount']}"))
    elif tool == "address_update" and facts.get("address"):
        rows.append((labels["new_address"], str(facts["address"])))
    elif tool == "exchange_request":
        if facts.get("item"):
            rows.append((labels["exchange_item"], str(facts["item"])))
        if facts.get("variant"):
            rows.append((labels["new_variant"], str(facts["variant"])))
    return rows


def detail_facts(
    channel: str | None,
    lang: str,
    tool: str | None,
    order: OrderFacts | None,
    facts: dict[str, object],
    status: str | None = None,
    include_status: bool = True,
    greet_name: str | None = None,
) -> dict[str, object]:
    """{details}/{greeting}/{items_table} facts for the email and WhatsApp reply templates
    above - a no-op (returns {}) for any other channel (voice reads the plain T text), so
    voice rendering is never affected. order is None only when the order snapshot is not
    available (an old action from before snapshots existed); the block then falls back to
    the order id and the request rows."""
    if channel not in DETAIL_CHANNELS:
        return {}
    extra = request_rows(lang, tool, facts, status)
    if order is not None:
        details = order_details(str(channel), lang, order, extra, include_status)
    else:
        labels = _DETAIL_LABELS.get(lang, _DETAIL_LABELS["en"])
        fallback = [(labels["order"], str(facts.get("order") or "-")), *extra]
        if channel == "email":
            lines = ["TABLE_START", f"{_cell(labels['head_key'])}|{_cell(labels['head_value'])}"]
            lines.extend(f"{_cell(k)}|{_cell(v)}" for k, v in fallback)
            lines.append("TABLE_END")
            details = "\n".join(lines)
        else:
            details = "\n".join(f"- {k}: *{_cell(v)}*" for k, v in fallback)
    result: dict[str, object] = {"details": details}
    if channel == "email":
        result["greeting"] = greeting(lang, greet_name)
        result["items_table"] = details
    return result
