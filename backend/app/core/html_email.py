"""Turn an already-rendered plain/WhatsApp-style reply (``*bold*``, ``- `` bullet
lines, ``TABLE_START``/``TABLE_END`` pipe-delimited table blocks, blank-line
paragraphs) into a professional HTML email body.

This runs once, at send time, only for the email channel (see
``actions/engine.py::_send``). It works purely off the final rendered string, not
the structured facts that built it, so it needs no new database column and no
change to the ~10 places that call ``engine.submit_reply`` with a plain body - the
TABLE_START/TABLE_END markup (built by ``v1/templates.py::email_facts()`` and
embedded into the plain text via the ``{items_table}`` placeholder) is just more
plain text until it reaches here. Any body without a TABLE_START block - every
other template, every other channel - renders exactly as before this was added.
"""

import html
import re

_BOLD = re.compile(r"\*([^*\n]+)\*")
_EMAIL_CSS = (
    "font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;"
    "color:#1a1a1a;max-width:560px;margin:0;padding:0;"
)
_TABLE_START = "TABLE_START"
_TABLE_END = "TABLE_END"


def _bold(escaped_line: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", escaped_line)


def _table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header, *body_rows = rows
    cells = "".join(
        f'<th style="text-align:left;padding:8px 12px 8px 0;'
        f'border-bottom:2px solid #333333;white-space:nowrap;">{_bold(html.escape(c))}</th>'
        for c in header
    )
    parts = [
        '<table style="width:100%;border-collapse:collapse;margin:0 0 16px 0;">',
        f"<tr>{cells}</tr>",
    ]
    for row in body_rows:
        cells = "".join(
            f'<td style="padding:8px 12px 8px 0;border-bottom:1px solid #eeeeee;">'
            f"{_bold(html.escape(c))}</td>"
            for c in row
        )
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "".join(parts)


def plain_to_email_html(body: str, brand: str | None = None) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    parts: list[str] = []
    list_open = False
    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]
        line = raw_line.strip()
        if line == _TABLE_START:
            if list_open:
                parts.append("</ul>")
                list_open = False
            rows: list[list[str]] = []
            i += 1
            while i < n and lines[i].strip() != _TABLE_END:
                row_line = lines[i].strip()
                if row_line:
                    rows.append([c.strip() for c in row_line.split("|")])
                i += 1
            parts.append(_table_html(rows))
            i += 1  # past TABLE_END
            continue
        if not line:
            if list_open:
                parts.append("</ul>")
                list_open = False
            i += 1
            continue
        if line.startswith("- "):
            if not list_open:
                parts.append('<ul style="margin:0 0 12px 0;padding-left:20px;">')
                list_open = True
            parts.append(f"<li>{_bold(html.escape(line[2:]))}</li>")
            i += 1
            continue
        if list_open:
            parts.append("</ul>")
            list_open = False
        parts.append(f'<p style="margin:0 0 12px 0;">{_bold(html.escape(line))}</p>')
        i += 1
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
