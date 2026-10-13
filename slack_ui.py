"""Slack Block Kit builders for the three card types: approval request, decision, FYI/error."""
from typing import Optional

RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def esc(text: str) -> str:
    """Escape Slack mrkdwn control characters in untrusted text."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _facts(snap: dict) -> str:
    t = snap["ticket"]
    a = snap["triage"]
    amount = a.get("refund_amount_usd")
    amount_txt = f"${amount:,.2f}" if amount is not None else "n/a"
    return (
        f"*Customer:* {esc(t['customer'])}\n"
        f"*Ticket:* {esc(_clip(t['subject'], 120))}\n"
        f"*Request:* “{esc(_clip(t['content'], 400))}”\n"
        f"*Refund amount:* {amount_txt}"
    )


def _analysis(snap: dict) -> str:
    a = snap["triage"]
    if not a.get("ok"):
        return f"*AI Analysis:* ⚠️ unavailable — `{esc(a.get('error', ''))}`"
    return (
        f"*AI Analysis* (`{esc(a['engine'])}`)\n"
        f"• *Intent:* {esc(a['intent_summary'])}\n"
        f"• *Category:* `{a['category']}`   *Risk:* {RISK_EMOJI.get(a['risk_level'], '')} {a['risk_level']}   "
        f"*Confidence:* {a['confidence']:.0%}\n"
        f"• *Reasoning:* {esc(a['reasoning'])}"
    )


def _link_context(snap: dict) -> list[dict]:
    url = snap["ticket"].get("url")
    label = f"HubSpot ticket #{snap['ticket']['id']}"
    text = f"<{url}|{label}>" if url else label
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}]


def approval_request(snap: dict, reasons: list[str]) -> list[dict]:
    ticket_id = snap["ticket"]["id"]
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🚨 Human Approval Required", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _facts(snap)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _analysis(snap)}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Why escalated:*\n" + "\n".join(f"• {esc(r)}" for r in reasons),
            },
        },
        *_link_context(snap),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "hitl_approve",
                    "text": {"type": "plain_text", "text": "✅ Approve Refund"},
                    "style": "primary",
                    "value": f"approve:{ticket_id}",
                },
                {
                    "type": "button",
                    "action_id": "hitl_reject",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "value": f"reject:{ticket_id}",
                },
            ],
        },
    ]


def decided(snap: dict, approved: bool, reviewer: str, reasons: list[str], crm_note: Optional[str] = None) -> list[dict]:
    amount = snap["triage"].get("refund_amount_usd")
    amount_txt = f"${amount:,.0f}" if amount is not None else "the requested"
    if approved:
        headline = f"✅ *Approved by {reviewer}*\n_Demo: processing {amount_txt} refund via Stripe API (simulated)…_"
        title = "✅ Refund Approved"
    else:
        headline = f"❌ *Rejected by {reviewer}*\nRefund cancelled. Ticket moved back for customer follow-up."
        title = "❌ Refund Rejected"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _facts(snap)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _analysis(snap)}},
    ]
    if crm_note:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": crm_note}]})
    blocks.extend(_link_context(snap))
    return blocks


def auto_resolved(snap: dict) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🤖 Auto-resolved by AI", "emoji": True}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Within policy guardrails and high AI confidence — no human approval needed. "
                "Ticket closed in HubSpot.",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": _facts(snap)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _analysis(snap)}},
        *_link_context(snap),
    ]


def failure(ticket_id: str, stage: str, error: str) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "⚠️ Workflow Failure", "emoji": True}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Ticket:* #{esc(ticket_id)}\n*Stage:* {esc(stage)}\n*Error:* `{esc(_clip(error, 300))}`\n"
                "A person should check this ticket manually.",
            },
        },
    ]
