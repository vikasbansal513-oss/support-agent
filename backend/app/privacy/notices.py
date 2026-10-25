from html import escape

from app.contracts.contracts import PublicNoticeOut
from app.core.config import get_settings

NOTICE_VERSION = "2026-01"

_PRIVACY = (
    ("Hum kaun hain", "Ye service aapke brand ki taraf se customer support jawab deti hai."),
    (
        "Kaun sa data",
        "Aapka phone number ya email, aapke messages, aur order ki jaankari jo brand ke store se aati hai.",
    ),
    (
        "Kyun",
        "Aapke sawaal ka jawab dene ke liye. Messages ko samajhne ke liye ek AI service use hoti hai;"
        " usse pehle phone, email aur card jaise number hata diye jaate hain.",
    ),
    ("Kitne din", "Conversations {days} din ke baad hata di jaati hain."),
    (
        "Aapke haq",
        "Aap apna data dekhne (export), sudharne ya hatane ki request brand ko bhej sakte hain."
        " Request par 30 din ke andar karyavahi hoti hai. STOP likh kar aap messages band kar sakte hain.",
    ),
    ("Shikayat", "Shikayat ke liye: {grievance}."),
)
_TERMS = (
    ("Seva", "AI assistant sirf order status, tracking aur policy ke sawaalon me madad karta hai."),
    (
        "Insaan",
        "Refund, cancel jaise faisle insaan lete hain. Assistant koi wada nahi karta jo store ne confirm na kiya ho.",
    ),
    ("Galti", "Koi jawab galat lage to 'insaan se baat' likhein, team contact karegi."),
)


def _render(title: str, sections: tuple[tuple[str, str], ...]) -> str:
    settings = get_settings()
    grievance = settings.grievance_email or "brand ke support se sampark karein"
    body = "".join(
        f"<h2>{escape(head)}</h2><p>{escape(text.format(days=settings.retention_days, grievance=grievance))}</p>"
        for head, text in sections
    )
    return f"<h1>{escape(title)}</h1>{body}"


def privacy_notice() -> PublicNoticeOut:
    settings = get_settings()
    draft = not settings.notice_published
    return PublicNoticeOut(
        version=NOTICE_VERSION,
        draft=draft,
        grievance_email=settings.grievance_email or None,
        html=_render("Privacy notice", _PRIVACY),
    )


def terms() -> PublicNoticeOut:
    settings = get_settings()
    draft = not settings.notice_published
    return PublicNoticeOut(
        version=NOTICE_VERSION,
        draft=draft,
        grievance_email=settings.grievance_email or None,
        html=_render("Terms", _TERMS),
    )
