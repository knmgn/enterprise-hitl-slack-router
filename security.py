"""Slack request signature verification (https://api.slack.com/authentication/verifying-requests-from-slack)."""
import hashlib
import hmac
import time


def verify_slack_signature(secret: str, body: bytes, timestamp: str, signature: str, now: float | None = None) -> bool:
    """Return True if the request is authentic and fresh (within 5 minutes)."""
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > 60 * 5:
        return False  # replay protection
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
