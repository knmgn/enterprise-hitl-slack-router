"""Orchestration: HubSpot ticket -> AI triage -> policy gate -> auto-handle or Slack approval -> HubSpot update."""
import logging
import time
from typing import Optional

import httpx

import ai_triage
import config
import policy
import slack_ui
from hubspot_client import HubSpot
from store import Store

log = logging.getLogger("hitl.workflow")


class Workflow:
    def __init__(self, store: Store, hubspot: HubSpot) -> None:
        self.store = store
        self.hs = hubspot

    # ---------------------------------------------------------------- Slack I/O
    async def _post_slack(self, blocks: list[dict], text: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(config.SLACK_WEBHOOK_URL, json={"text": text, "blocks": blocks})
        if r.status_code != 200:
            raise RuntimeError(f"Slack webhook returned {r.status_code}: {r.text[:200]}")

    async def _notify_failure(self, ticket_id: str, stage: str, error: str) -> None:
        try:
            await self._post_slack(slack_ui.failure(ticket_id, stage, error), f"Workflow failure on ticket {ticket_id}")
        except Exception:  # noqa: BLE001
            log.exception("Could not send failure notification to Slack")

    # ----------------------------------------------------------- ticket handling
    async def process_ticket(self, ticket_id: str) -> str:
        """Process one ticket exactly once. Returns a short status string."""
        ticket_id = str(ticket_id)
        if not self.store.claim(ticket_id, config.MAX_ATTEMPTS):
            log.info("Ticket %s already handled - skipping (idempotent)", ticket_id)
            return "duplicate"

        stage = "fetch ticket"
        try:
            t = await self.hs.get_ticket(ticket_id)
            props = t.get("properties", {})
            if props.get("hs_pipeline_stage") != config.STAGE_NEW:
                self.store.set_status(ticket_id, "skipped")
                self.store.audit(ticket_id, "skipped", {"reason": "ticket is not in the New stage"})
                return "skipped"

            customer = "Unknown customer"
            contacts = (t.get("associations", {}).get("contacts", {}) or {}).get("results", [])
            if contacts:
                c = await self.hs.get_contact(contacts[0]["id"])
                customer = (
                    f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip() or c.get("email") or customer
                )

            subject = props.get("subject") or "(no subject)"
            content = props.get("content") or ""
            self.store.audit(ticket_id, "received", {"subject": subject, "customer": customer})

            stage = "AI triage"
            result = await ai_triage.triage(subject, content, customer)
            decision = policy.decide(result)
            self.store.audit(ticket_id, "triaged", {"triage": result, "route": decision.route, "reasons": decision.reasons})

            snap = {
                "ticket": {
                    "id": ticket_id,
                    "subject": subject,
                    "content": content,
                    "customer": customer,
                    "url": self.hs.ticket_url(ticket_id),
                },
                "triage": result,
                "decision": {"route": decision.route, "reasons": decision.reasons},
            }

            if decision.route == "auto":
                stage = "HubSpot update (auto-resolve)"
                await self.hs.update_stage(ticket_id, config.STAGE_CLOSED)
                await self.hs.add_note(
                    ticket_id,
                    f"[HITL Router] Auto-resolved by AI ({result['engine']}). "
                    f"Category={result['category']}, confidence={result['confidence']:.0%}. {result['reasoning']}",
                )
                self.store.set_status(ticket_id, "auto_resolved", snap)
                self.store.audit(ticket_id, "auto_resolved", {})
                try:
                    await self._post_slack(slack_ui.auto_resolved(snap), f"Auto-resolved ticket {ticket_id}")
                except Exception:  # noqa: BLE001 - FYI card only, the action itself already succeeded
                    log.exception("FYI card failed")
                return "auto_resolved"

            stage = "Slack notification"
            await self._post_slack(slack_ui.approval_request(snap, decision.reasons), f"Approval required: ticket {ticket_id}")
            # Persist before touching HubSpot so a retry can never post a second card.
            self.store.set_status(ticket_id, "pending_approval", snap)
            self.store.audit(ticket_id, "escalated_to_slack", {"reasons": decision.reasons})

            stage = "HubSpot update (escalate)"
            await self.hs.update_stage(ticket_id, config.STAGE_ESCALATED)
            await self.hs.add_note(
                ticket_id,
                "[HITL Router] Escalated for human approval in Slack. " + "; ".join(decision.reasons),
            )
            return "pending_approval"

        except Exception as e:  # noqa: BLE001
            log.exception("Ticket %s failed at stage '%s'", ticket_id, stage)
            row = self.store.get(ticket_id)
            if not row or row["status"] == "processing":
                self.store.set_status(ticket_id, "error")
            self.store.audit(ticket_id, "error", {"stage": stage, "error": str(e)[:300]})
            await self._notify_failure(ticket_id, stage, str(e))
            return "error"

    async def poll_once(self) -> list[str]:
        """Pick up tickets created since the baseline that are still in the New stage."""
        baseline = self.store.get_meta("baseline_ms")
        if baseline is None:
            baseline = str(int(time.time() * 1000))
            self.store.set_meta("baseline_ms", baseline)
            log.info("First start: only tickets created from now on will be processed")
        tickets = await self.hs.search_new_tickets(int(baseline))
        results = []
        for t in tickets:
            results.append(f"{t['id']}:{await self.process_ticket(t['id'])}")
        return results

    # ------------------------------------------------------ human decision (Slack)
    async def _respond(self, response_url: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(response_url, json=payload)

    async def handle_decision(self, action: str, ticket_id: str, user_id: Optional[str], username: str, response_url: str) -> None:
        approved = action == "approve"
        row = self.store.get(ticket_id)
        new_status = "approved" if approved else "rejected"
        # Atomic compare-and-set: a double click or a second reviewer cannot decide twice.
        if not row or not self.store.transition(ticket_id, "pending_approval", new_status):
            current = row["status"] if row else "unknown"
            await self._respond(
                response_url,
                {
                    "response_type": "ephemeral",
                    "replace_original": False,
                    "text": f"ℹ️ This request was already handled (status: {current}). No action taken.",
                },
            )
            return

        snap = row["snapshot"]
        reviewer = f"<@{user_id}>" if user_id else f"@{username}"
        self.store.audit(ticket_id, new_status, {"by": username, "slack_user_id": user_id})
        note = None
        try:
            await self.hs.update_stage(ticket_id, config.STAGE_CLOSED if approved else config.STAGE_WAITING_CUSTOMER)
            await self.hs.add_note(
                ticket_id,
                f"[HITL Router] Refund {'APPROVED' if approved else 'REJECTED'} by {username} via Slack.",
            )
            note = "HubSpot ticket updated: " + ("closed." if approved else "moved to waiting-on-customer.")
        except Exception as e:  # noqa: BLE001
            log.exception("HubSpot sync failed after decision on %s", ticket_id)
            self.store.set_status(ticket_id, "sync_failed")
            self.store.audit(ticket_id, "sync_failed", {"error": str(e)[:300]})
            note = f"⚠️ Decision recorded, but updating HubSpot failed — please update ticket #{ticket_id} manually."

        blocks = slack_ui.decided(snap, approved, reviewer, snap["decision"]["reasons"], note)
        await self._respond(
            response_url,
            {"replace_original": True, "text": f"{'Approved' if approved else 'Rejected'} by {username}", "blocks": blocks},
        )
