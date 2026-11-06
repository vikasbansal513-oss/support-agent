"""Turn an already-rendered plain/WhatsApp-style reply (``*bold*``, ``- `` bullet
lines, blank-line paragraphs) into a simple, professional HTML email body.

This runs once, at send time, only for the email channel (see
``actions/engine.py::_send``). It does not need the structured facts (order ref,
items, amount) that built the plain text in the first place - it works purely off
the final string, so it needs no new database column and no change to any of the
~10 places that call ``engine.submit_reply`` with a plain body. The trade-off: no
literal HTML ``<table>`` for order details, just a clean bullet list - good enough
for "professional-looking", not a pixel match for a hand-designed template.
"""

import html
import re

_BOLD = re.compile(r"\*([^*\n]+)\*")
_EMAIL_CSS = (
    "font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;"
    "color:#1a1a1a;max-width:560px;margin:0;padding:0;"
)


def _bold(escaped_line: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", escaped_line)


def plain_to_email_html(body: str, brand: str | None = None) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    parts: list[str] = []
    list_open = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if list_open:
                parts.append("</ul>")
                list_open = False
            continue
        if line.startswith("- "):
            if not list_open:
                parts.append('<ul style="margin:0 0 12px 0;padding-left:20px;">')
                list_open = True
            parts.append(f"<li>{_bold(html.escape(line[2:]))}</li>")
            continue
        if list_open:
            parts.append("</ul>")
            list_open = False
        parts.append(f'<p style="margin:0 0 12px 0;">{_bold(html.escape(line))}</p>')
    if list_open:
        parts.append("</ul>")
    signature = f"Thanks,<br>{html.escape(brand)} Support" if brand else "Thanks,<br>Support Team"
    body_html = "\n".join(parts)
    return (
        "<!doctype html>"
        f'<html><body style="{_EMAIL_CSS}">'
        f"{body_html}"
        f'<p style="margin:20px 0 0 0;color:#555555;">{signature}</p>'
        "</body></html>"
    )
