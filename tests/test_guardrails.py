import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
import hmac
import time

import ai_triage
import config
import policy
from security import verify_slack_signature


def tri(**kw):
    base = {
        "ok": True,
        "engine": "test",
        "intent_summary": "x",
        "category": "refund_request",
        "refund_amount_usd": 30.0,
        "risk_level": "low",
        "confidence": 0.95,
        "recommended_action": "auto_resolve",
        "reasoning": "r",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- policy gate
def test_small_confident_refund_is_auto():
    assert policy.decide(tri()).route == "auto"


def test_large_refund_escalates_even_if_ai_is_confident():
    d = policy.decide(tri(refund_amount_usd=500.0, confidence=0.99))
    assert d.route == "escalate"
    assert any("threshold" in r for r in d.reasons)


def test_threshold_is_exclusive():
    assert policy.decide(tri(refund_amount_usd=config.REFUND_APPROVAL_THRESHOLD)).route == "auto"


def test_low_confidence_escalates():
    assert policy.decide(tri(confidence=0.4)).route == "escalate"


def test_legal_threat_always_escalates():
    assert policy.decide(tri(category="legal_threat", refund_amount_usd=None)).route == "escalate"


def test_refund_without_amount_escalates():
    assert policy.decide(tri(refund_amount_usd=None)).route == "escalate"


def test_ai_failure_fails_safe():
    d = policy.decide({"ok": False, "error": "boom"})
    assert d.route == "escalate"


def test_llm_cannot_override_guardrail_by_recommending_auto():
    # Model says auto_resolve for a $5000 refund: code still escalates.
    assert policy.decide(tri(refund_amount_usd=5000.0, recommended_action="auto_resolve")).route == "escalate"


# -------------------------------------------------------- output normalisation
def test_normalize_clamps_and_defaults_unsafe():
    out = ai_triage._normalize(
        {"category": "weird", "risk_level": "??", "confidence": 7, "refund_amount_usd": "abc"}, "t"
    )
    assert out["category"] == "other"
    assert out["risk_level"] == "high"  # unknown risk is treated as high
    assert out["confidence"] == 1.0
    assert out["refund_amount_usd"] is None
    assert out["recommended_action"] == "escalate"


def test_rule_based_fallback_scenarios():
    small = ai_triage._rules("Damaged mug", "Please refund $30 thanks")
    assert small["refund_amount_usd"] == 30.0
    legal = ai_triage._rules("Bad service", "I will contact my lawyer")
    assert legal["category"] == "legal_threat"


# ------------------------------------------------------------ slack signature
def _sign(secret, ts, body):
    return "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted():
    body, ts = b"payload=abc", str(int(time.time()))
    assert verify_slack_signature("s3cret", body, ts, _sign("s3cret", ts, body))


def test_tampered_body_rejected():
    ts = str(int(time.time()))
    sig = _sign("s3cret", ts, b"payload=abc")
    assert not verify_slack_signature("s3cret", b"payload=evil", ts, sig)


def test_old_timestamp_rejected():
    ts = str(int(time.time()) - 3600)
    body = b"payload=abc"
    assert not verify_slack_signature("s3cret", body, ts, _sign("s3cret", ts, body))


def test_missing_secret_rejected():
    assert not verify_slack_signature("", b"x", "1", "v0=abc")
