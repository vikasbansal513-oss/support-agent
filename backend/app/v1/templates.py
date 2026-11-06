from string import Formatter

from app.agent import templates as v0
from app.contracts.contracts import OrderFacts

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
            "📦 {items}\n"
            "💰 Total: *Rs {amount}*\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Order cancel karein\n"
            "❌ *NAHI* – Order rakhein\n\n"
            "Confirm hote hi update kar denge.\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}We've received your request to cancel order *{order}*.\n\n"
            "📦 {items}\n"
            "💰 Total: *Rs {amount}*\n\n"
            "Please reply:\n"
            "✅ *YES* – Cancel order\n"
            "❌ *NO* – Keep order\n\n"
            "We'll update you right after confirmation.\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}आपने ऑर्डर *{order}* कैंसिल करने के लिए कहा है।\n\n"
            "📦 {items}\n"
            "💰 कुल: *Rs {amount}*\n\n"
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
            "🏠 Naya address: {address}\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Address update karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Update the delivery address for order *{order}*?\n\n"
            "🏠 New address: {address}\n\n"
            "Please reply:\n"
            "✅ *YES* – Update address\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* का डिलीवरी पता अपडेट करें?\n\n"
            "🏠 नया पता: {address}\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – पता अपडेट करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_exchange_request": {
        "hinglish": (
            "{note}Order *{order}* me item exchange karna hai?\n\n"
            "🔄 {item} → *{variant}*\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Exchange shuru karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Exchange an item in order *{order}*?\n\n"
            "🔄 {item} → *{variant}*\n\n"
            "Please reply:\n"
            "✅ *YES* – Start the exchange\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* में आइटम बदलें?\n\n"
            "🔄 {item} → *{variant}*\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – एक्सचेंज शुरू करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_refund_create": {
        "hinglish": (
            "{note}Order *{order}* ka refund shuru karein?\n\n"
            "💰 Refund amount: *Rs {amount}*\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Refund shuru karein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Start a refund for order *{order}*?\n\n"
            "💰 Refund amount: *Rs {amount}*\n\n"
            "Please reply:\n"
            "✅ *YES* – Start the refund\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* का रिफंड शुरू करें?\n\n"
            "💰 रिफंड राशि: *Rs {amount}*\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – रिफंड शुरू करें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "confirm_return_request": {
        "hinglish": (
            "{note}Order *{order}* ke liye return request bhejein?\n\n"
            "Reply karein:\n"
            "✅ *HAAN* – Return request bhejein\n"
            "❌ *NAHI* – Rehne dein\n\n"
            "— {brand} Support"
        ),
        "en": (
            "{note}Submit a return request for order *{order}*?\n\n"
            "Please reply:\n"
            "✅ *YES* – Submit the return\n"
            "❌ *NO* – Leave it as is\n\n"
            "— {brand} Support"
        ),
        "hi": (
            "{note}ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट भेजें?\n\n"
            "कृपया रिप्लाई करें:\n"
            "✅ *हाँ* – रिटर्न रिक्वेस्ट भेजें\n"
            "❌ *नहीं* – रहने दें\n\n"
            "— {brand} Support"
        ),
    },
    "done_order_cancel": {
        "hinglish": "✅ Order *{order}* cancel ho gaya hai.\n\n— {brand} Support",
        "en": "✅ Order *{order}* has been cancelled.\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* कैंसिल हो गया है।\n\n— {brand} Support",
    },
    "done_address_update": {
        "hinglish": "✅ Order *{order}* ka address update ho gaya hai.\n\n— {brand} Support",
        "en": "✅ The address for order *{order}* has been updated.\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* का पता अपडेट हो गया है।\n\n— {brand} Support",
    },
    "done_exchange_request": {
        "hinglish": "✅ Order *{order}* ke liye exchange request register ho gayi hai.\n\n— {brand} Support",
        "en": "✅ The exchange request for order *{order}* has been registered.\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* के लिए एक्सचेंज रिक्वेस्ट दर्ज हो गई है।\n\n— {brand} Support",
    },
    "done_refund_create": {
        "hinglish": "✅ Order *{order}* ka Rs {amount} refund shuru ho gaya hai.\n\n— {brand} Support",
        "en": "✅ A refund of *Rs {amount}* has been started for order *{order}*.\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* का Rs {amount} रिफंड शुरू हो गया है।\n\n— {brand} Support",
    },
    "done_return_request": {
        "hinglish": "✅ Order *{order}* ke liye return request register ho gayi hai.\n\n— {brand} Support",
        "en": "✅ The return request for order *{order}* has been registered.\n\n— {brand} Support",
        "hi": "✅ ऑर्डर *{order}* के लिए रिटर्न रिक्वेस्ट दर्ज हो गई है।\n\n— {brand} Support",
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
    "en": ("Hi {name},", "Hi there,"),
    "hinglish": ("Hi {name},", "Hi there,"),
    "hi": ("नमस्ते {name},", "नमस्ते,"),
}

# Column headers for the order-details TABLE_START/TABLE_END block each confirm_* email
# template embeds via {items_table} (see core/html_email.py for how the markers become a
# real HTML <table>). One row per OrderFacts.lines entry for order_cancel; the other tools
# don't have per-line facts, so they get a single summary row instead.
_HEADERS: dict[str, dict[str, tuple[str, ...]]] = {
    "order_cancel": {
        "en": ("Order ID", "Item", "Qty", "Total"),
        "hinglish": ("Order ID", "Item", "Qty", "Total"),
        "hi": ("ऑर्डर आईडी", "आइटम", "मात्रा", "कुल"),
    },
    "refund_create": {
        "en": ("Order ID", "Refund Amount"),
        "hinglish": ("Order ID", "Refund Amount"),
        "hi": ("ऑर्डर आईडी", "रिफंड राशि"),
    },
    "address_update": {
        "en": ("Order ID", "New Address"),
        "hinglish": ("Order ID", "New Address"),
        "hi": ("ऑर्डर आईडी", "नया पता"),
    },
    "exchange_request": {
        "en": ("Order ID", "Item", "New Variant"),
        "hinglish": ("Order ID", "Item", "New Variant"),
        "hi": ("ऑर्डर आईडी", "आइटम", "नया वेरिएंट"),
    },
    "return_request": {
        "en": ("Order ID",),
        "hinglish": ("Order ID",),
        "hi": ("ऑर्डर आईडी",),
    },
}


def _table_row(*cols: str) -> str:
    return "|".join(cols)


def email_facts(
    channel: str | None, lang: str, tool: str, order: "OrderFacts | None", facts: dict[str, object]
) -> dict[str, object]:
    """{greeting}/{items_table} facts for the EMAIL_T confirm_* templates above - a no-op
    (returns {}) for any other channel, so WhatsApp/voice rendering is never affected. order
    is None only when a template is rendered outside the normal order-verified write flow,
    which does not happen for confirm_* in practice; guarded anyway so render() still gets a
    usable (if order-less) table instead of a KeyError."""
    if channel != "email":
        return {}
    name_tpl, no_name = _GREETING.get(lang, _GREETING["en"])
    name = (order.customer_name if order else None) or ""
    greeting = name_tpl.format(name=name) if name.strip() else no_name
    headers = _HEADERS.get(tool, {}).get(lang) or _HEADERS.get(tool, {}).get("en") or ("Order ID",)
    order_name = str(facts.get("order") or (order.order_name if order else "") or "-")
    rows = [_table_row("TABLE_START"), _table_row(*headers)]
    if tool == "order_cancel" and order and order.lines:
        for line in order.lines:
            rows.append(
                _table_row(
                    order_name,
                    line.title,
                    str(line.quantity),
                    f"Rs {rs(line.unit_price_paise * line.quantity)}",
                )
            )
    elif tool == "refund_create":
        amount = facts.get("amount")
        rows.append(_table_row(order_name, f"Rs {amount}" if amount else "-"))
    elif tool == "address_update":
        rows.append(_table_row(order_name, str(facts.get("address") or "-")))
    elif tool == "exchange_request":
        rows.append(
            _table_row(order_name, str(facts.get("item") or "-"), str(facts.get("variant") or "-"))
        )
    else:
        rows.append(_table_row(order_name))
    rows.append("TABLE_END")
    return {"greeting": greeting, "items_table": "\n".join(rows)}
