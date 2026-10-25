import re
from app.contracts.errors import ERRORS
from app.contracts.contracts import ErrorBody
def test_codes_valid():
    for code, (http, sev, msg) in ERRORS.items():
        assert re.match(r"^E-[A-Z]+-\d{3}$", code) and 400 <= http <= 599 and sev in ("critical", "error", "warning") and msg
        ErrorBody(code=code, message=msg, error_id="ERR-0123456789AB", severity=sev)
def test_no_internal_words_in_messages():
    for _, (_, _, msg) in ERRORS.items():
        assert not re.search(r"traceback|exception|sql|stack|psycopg|asyncpg", msg, re.I)
