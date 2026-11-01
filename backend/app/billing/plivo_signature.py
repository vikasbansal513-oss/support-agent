"""Verify Plivo's X-Plivo-Signature-V2 webhook signature (HMAC-SHA1 of url+nonce, base64).
VERIFY: this follows Plivo's documented V2 scheme but has not been exercised against a real
Plivo account/webhook in this sandbox - test against a real call before relying on it to
reject unsigned requests in production."""

import base64
import hashlib
import hmac


def verify_plivo_signature(url: str, nonce: str, signature: str, auth_token: str) -> bool:
    if not signature or not nonce or not auth_token:
        return False
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), (url + nonce).encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)
