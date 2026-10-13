"""Deterministic guardrails deciding between auto-handling and human escalation."""
from dataclasses import dataclass, field

import config

# Categories that must never be handled automatically, regardless of AI confidence.
ALWAYS_ESCALATE = {"legal_threat", "fraud_chargeback"}


@dataclass
class Decision:
    route: str  # "auto" | "escalate"
    reasons: list[str] = field(default_factory=list)


def decide(triage: dict) -> Decision:
    reasons: list[str] = []

    # Fail safe: if the AI could not analyse the ticket, a human must look at it.
    if not triage.get("ok"):
        return Decision("escalate", [f"AI triage unavailable ({triage.get('error', 'unknown error')})"])

    amount = triage.get("refund_amount_usd")
    if amount is not None and amount > config.REFUND_APPROVAL_THRESHOLD:
        reasons.append(f"Refund ${amount:,.0f} exceeds ${config.REFUND_APPROVAL_THRESHOLD:,.0f} approval threshold")

    if triage["confidence"] < config.CONFIDENCE_THRESHOLD:
        reasons.append(
            f"AI confidence {triage['confidence']:.0%} is below the {config.CONFIDENCE_THRESHOLD:.0%} threshold"
        )

    if triage["risk_level"] == "high":
        reasons.append("AI assessed risk level as HIGH")

    if triage["category"] in ALWAYS_ESCALATE:
        reasons.append(f"Category '{triage['category']}' always requires human review")

    # A refund request with no identifiable amount cannot be auto-approved.
    if triage["category"] == "refund_request" and amount is None:
        reasons.append("Refund amount could not be determined")

    return Decision("escalate" if reasons else "auto", reasons)
