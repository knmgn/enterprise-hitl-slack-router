"""Enterprise HITL Router: HubSpot -> AI triage -> (auto | Slack human approval) -> HubSpot."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

import config
from hubspot_client import HubSpot
from security import verify_slack_signature
from store import Store
from workflow import Workflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hitl.app")

state: dict = {}


async def _poller(wf: Workflow) -> None:
    while True:
        try:
            results = await wf.poll_once()
            if results:
                log.info("Poll processed: %s", results)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("Poll cycle failed")
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_settings()
    if missing:
        log.warning("Missing settings (workflow disabled until set): %s", ", ".join(missing))
    if not config.SLACK_SIGNING_SECRET:
        log.warning("SLACK_SIGNING_SECRET not set - Slack request signatures are NOT verified")
    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set - using the rule-based triage fallback")

    store = Store(config.DB_PATH)
    hubspot = HubSpot()
    wf = Workflow(store, hubspot)
    state.update(store=store, hubspot=hubspot, wf=wf, ready=not missing)

    task = None
    if not missing:
        task = asyncio.create_task(_poller(wf))
    yield
    if task:
        task.cancel()
    await hubspot.aclose()


app = FastAPI(title="Enterprise HITL Router", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "service": "Enterprise HITL Router",
        "workflow_ready": state.get("ready", False),
        "missing_settings": config.missing_settings(),
        "ai_engine": f"gemini:{config.GEMINI_MODEL}" if config.GEMINI_API_KEY else "rule-based",
        "slack_signature_verification": bool(config.SLACK_SIGNING_SECRET),
    }


def _require_ready() -> Workflow:
    if not state.get("ready"):
        raise HTTPException(503, f"Not configured: {', '.join(config.missing_settings())}")
    return state["wf"]


@app.post("/webhook/hubspot")
async def hubspot_webhook(request: Request, background: BackgroundTasks):
    """HubSpot webhook receiver (ticket.creation events).

    An empty body triggers an immediate poll - handy for demos and as a polling fallback.
    """
    wf = _require_ready()
    raw = await request.body()
    events = []
    if raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON")
        events = data if isinstance(data, list) else [data]

    ticket_ids = [
        str(e["objectId"]) for e in events if "ticket" in str(e.get("subscriptionType", "ticket.creation")) and "objectId" in e
    ]
    if not events:
        return {"status": "poll", "processed": await wf.poll_once()}
    for tid in ticket_ids:
        background.add_task(wf.process_ticket, tid)
    return {"status": "accepted", "tickets": ticket_ids}


@app.post("/slack/interactive")
async def slack_interactive(request: Request, background: BackgroundTasks):
    """Slack button callback. Must answer within 3s, so the work runs in the background."""
    body = await request.body()
    if config.SLACK_SIGNING_SECRET and not verify_slack_signature(
        config.SLACK_SIGNING_SECRET,
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(401, "Invalid Slack signature")

    wf = _require_ready()
    form = parse_qs(body.decode())
    if "payload" not in form:
        raise HTTPException(400, "Missing payload")
    data = json.loads(form["payload"][0])
    if data.get("type") != "block_actions" or not data.get("actions"):
        return {}

    action, _, ticket_id = data["actions"][0]["value"].partition(":")
    if action not in ("approve", "reject") or not ticket_id:
        raise HTTPException(400, "Unknown action")

    user = data.get("user", {})
    background.add_task(
        wf.handle_decision,
        action,
        ticket_id,
        user.get("id"),
        user.get("username") or user.get("name") or "user",
        data["response_url"],
    )
    return {}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
