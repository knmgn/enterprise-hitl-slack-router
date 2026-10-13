"""Create demo tickets in HubSpot (and the demo contacts they belong to).

    python seed_demo.py small      # $30 refund      -> AI auto-resolves
    python seed_demo.py large      # $500 refund     -> escalated to Slack for approval
    python seed_demo.py ambiguous  # angry/unclear   -> low AI confidence, escalated
    python seed_demo.py all
    python seed_demo.py cleanup    # archive every ticket whose subject starts with [DEMO]
"""
import asyncio
import sys

import httpx

import config

SCENARIOS = {
    "small": {
        "contact": ("Jane", "Smith", "jane.smith@example.com"),
        "subject": "[DEMO] Damaged mug - refund request",
        "content": "Hi, the ceramic mug I ordered (#10482) arrived with a chip in it. "
        "Could I get a refund of $30 for the item? Thanks!",
    },
    "large": {
        "contact": ("John", "Doe", "john.doe@example.com"),
        "subject": "[DEMO] Duplicate charge - refund request",
        "content": "I was charged twice for order #10517. Please refund the duplicate charge of $500 "
        "to my original payment method.",
    },
    "ambiguous": {
        "contact": ("Alex", "Morgan", "alex.morgan@example.com"),
        "subject": "[DEMO] Unacceptable service!!!",
        "content": "This is the third time something went wrong with my orders. I want this resolved NOW, "
        "I'm done being ignored and I'm considering talking to a lawyer. Make it right.",
    },
}


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.HUBSPOT_API_BASE,
        headers={"Authorization": f"Bearer {config.HUBSPOT_SERVICE_KEY}"},
        timeout=20,
    )


async def ensure_contact(c: httpx.AsyncClient, first: str, last: str, email: str) -> str:
    r = await c.post(
        "/crm/v3/objects/contacts/search",
        json={"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}]},
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    if results:
        return results[0]["id"]
    r = await c.post(
        "/crm/v3/objects/contacts",
        json={"properties": {"firstname": first, "lastname": last, "email": email}},
    )
    r.raise_for_status()
    return r.json()["id"]


async def create_ticket(c: httpx.AsyncClient, key: str) -> str:
    s = SCENARIOS[key]
    contact_id = await ensure_contact(c, *s["contact"])
    r = await c.post(
        "/crm/v3/objects/tickets",
        json={
            "properties": {
                "subject": s["subject"],
                "content": s["content"],
                "hs_pipeline": "0",
                "hs_pipeline_stage": config.STAGE_NEW,
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    # HubSpot-defined association: ticket -> contact
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 16}],
                }
            ],
        },
    )
    r.raise_for_status()
    return r.json()["id"]


async def cleanup(c: httpx.AsyncClient) -> int:
    r = await c.post(
        "/crm/v3/objects/tickets/search",
        json={
            "filterGroups": [{"filters": [{"propertyName": "subject", "operator": "CONTAINS_TOKEN", "value": "DEMO*"}]}],
            "properties": ["subject"],
            "limit": 100,
        },
    )
    r.raise_for_status()
    n = 0
    for t in r.json().get("results", []):
        if (t["properties"].get("subject") or "").startswith("[DEMO]"):
            (await c.delete(f"/crm/v3/objects/tickets/{t['id']}")).raise_for_status()
            n += 1
    return n


async def main(arg: str, interval: float = 6.0, initial_wait: float = 5.0) -> None:
    async with client() as c:
        if arg == "cleanup":
            print(f"Archived {await cleanup(c)} demo ticket(s)")
            return
        if arg == "sequence":
            print(f"Starting in {initial_wait:.0f}s... Switch to HubSpot and start recording!")
            await asyncio.sleep(initial_wait)
            for k in ["small", "large", "ambiguous"]:
                tid = await create_ticket(c, k)
                print(f"Created {k} ticket #{tid}")
                await asyncio.sleep(interval)
            print("All 3 demo tickets created!")
            return
        keys = list(SCENARIOS) if arg == "all" else [arg]
        for k in keys:
            print(f"{k}: created ticket {await create_ticket(c, k)}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in (*SCENARIOS, "all", "cleanup", "sequence"):
        sys.exit(__doc__ + "    python seed_demo.py sequence [interval_sec] [wait_sec]\n")
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    asyncio.run(main(sys.argv[1], interval, wait))

