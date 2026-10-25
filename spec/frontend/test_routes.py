import pathlib, sys, re
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "contracts"))
from contracts import ENDPOINTS
from errors import ERRORS
from routes import ROUTES, GLOBAL_RULES
def norm(p): return re.sub(r"\{[^}]*\}", "{}", p)
def test_every_ui_call_exists_in_api():
    api = {(e["method"], norm(e["path"])) for e in ENDPOINTS}
    missing = [(m, p) for r in ROUTES for m, p in r[3] if (m, norm(p)) not in api]
    assert not missing, missing
def test_every_user_facing_endpoint_used_by_some_screen():
    used = {(m, norm(p)) for r in ROUTES for m, p in r[3]}
    internal_prefix = ("/health", "/ready", "/webhooks/")
    internal_exact = {("POST","/auth/refresh"),("POST","/auth/logout"),("POST","/admin/auth/refresh"),("POST","/admin/auth/logout"),
                      ("GET","/auth/me"),("GET","/admin/tenants/{id}"),("PATCH","/admin/tenants/{id}")}
    unused = [(e["method"], e["path"]) for e in ENDPOINTS if (e["method"], norm(e["path"])) not in used and not e["path"].startswith(internal_prefix) and (e["method"], e["path"]) not in internal_exact]
    assert not unused, unused
def test_error_codes_in_ui_exist():
    for r in ROUTES:
        for code in re.findall(r"E-[A-Z]+-\d{3}", r[4]): assert code in ERRORS, code
    for blob in re.findall(r"E-([A-Z]+)-(\d{3})/(\d{3})(?:/(\d{3}))?", " ".join(r[4] for r in ROUTES)):
        for n in blob[1:]:
            if n: assert f"E-{blob[0]}-{n}" in ERRORS, blob
def test_unique_routes(): assert len({r[0] for r in ROUTES}) == len(ROUTES)
