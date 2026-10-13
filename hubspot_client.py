"""Minimal async HubSpot CRM client (tickets, contacts, notes)."""
import asyncio
import datetime as dt
import logging
from typing import Optional

import httpx

import config

log = logging.getLogger("hitl.hubspot")

TICKET_PROPS = ["subject", "content", "hs_pipeline_stage", "hs_ticket_priority", "createdate"]
# HubSpot-defined association type: note -> ticket
NOTE_TO_TICKET = 228


class HubSpotError(RuntimeError):
    pass


class HubSpot:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.HUBSPOT_API_BASE,
            headers={"Authorization": f"Bearer {config.HUBSPOT_SERVICE_KEY}"},
            timeout=20,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> dict:
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = await self._client.request(method, url, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise HubSpotError(f"{r.status_code} {r.text[:200]}")
                if r.status_code >= 400:
                    # client errors are not retryable
                    raise HubSpotError(f"{r.status_code} {r.text[:300]}") from None
                return r.json() if r.content else {}
            except (httpx.TransportError, HubSpotError) as e:
                last = e
                if isinstance(e, HubSpotError) and not str(e)[:3] in ("429", "500", "502", "503", "504"):
                    raise
                await asyncio.sleep(0.5 * (2**attempt))
        raise HubSpotError(f"HubSpot request failed after retries: {last}")

    async def get_ticket(self, ticket_id: str) -> dict:
        data = await self._request(
            "GET",
            f"/crm/v3/objects/tickets/{ticket_id}",
            params={"properties": ",".join(TICKET_PROPS), "associations": "contacts"},
        )
        return data

    async def get_contact(self, contact_id: str) -> dict:
        data = await self._request(
            "GET",
            f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": "firstname,lastname,email"},
        )
        return data.get("properties", {})

    async def search_new_tickets(self, created_since_ms: int, limit: int = 20) -> list[dict]:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "hs_pipeline_stage", "operator": "EQ", "value": config.STAGE_NEW},
                        {"propertyName": "createdate", "operator": "GTE", "value": str(created_since_ms)},
                    ]
                }
            ],
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
            "properties": TICKET_PROPS,
            "limit": limit,
        }
        data = await self._request("POST", "/crm/v3/objects/tickets/search", json=body)
        return data.get("results", [])

    async def update_stage(self, ticket_id: str, stage_id: str) -> None:
        await self._request(
            "PATCH",
            f"/crm/v3/objects/tickets/{ticket_id}",
            json={"properties": {"hs_pipeline_stage": stage_id}},
        )

    async def add_note(self, ticket_id: str, body: str) -> bool:
        """Best-effort audit note on the ticket timeline. Returns False if not permitted."""
        payload = {
            "properties": {
                "hs_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "hs_note_body": body,
            },
            "associations": [
                {
                    "to": {"id": ticket_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_TICKET}],
                }
            ],
        }
        try:
            await self._request("POST", "/crm/v3/objects/notes", json=payload)
            return True
        except HubSpotError as e:
            log.warning("Could not add note to ticket %s: %s", ticket_id, e)
            return False

    def ticket_url(self, ticket_id: str) -> str:
        if not config.HUBSPOT_PORTAL_ID:
            return ""
        return f"{config.HUBSPOT_APP_BASE}/contacts/{config.HUBSPOT_PORTAL_ID}/record/0-5/{ticket_id}"
