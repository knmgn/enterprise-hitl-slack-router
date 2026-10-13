"""Central configuration. All values come from environment variables / .env."""
import os

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# --- Slack ---
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
# Optional but recommended: verifies that /slack/interactive requests really come from Slack.
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# --- HubSpot ---
HUBSPOT_SERVICE_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "")
HUBSPOT_API_BASE = os.getenv("HUBSPOT_API_BASE", "https://api.hubapi.com")
HUBSPOT_APP_BASE = os.getenv("HUBSPOT_APP_BASE", "https://app-na2.hubspot.com")
HUBSPOT_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "")

# Ticket pipeline stage IDs (account specific).
STAGE_NEW = os.getenv("HUBSPOT_STAGE_NEW", "")
STAGE_ESCALATED = os.getenv("HUBSPOT_STAGE_ESCALATED", "")  # awaiting human decision
STAGE_WAITING_CUSTOMER = os.getenv("HUBSPOT_STAGE_WAITING_CUSTOMER", "")  # rejected -> follow up with customer
STAGE_CLOSED = os.getenv("HUBSPOT_STAGE_CLOSED", "")  # approved / auto-resolved

# --- AI triage ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# --- Policy guardrails (enforced in code, never delegated to the LLM) ---
REFUND_APPROVAL_THRESHOLD = _float("REFUND_APPROVAL_THRESHOLD", 100.0)  # USD, above -> human approval
CONFIDENCE_THRESHOLD = _float("CONFIDENCE_THRESHOLD", 0.8)  # below -> human approval

# --- Runtime ---
POLL_INTERVAL_SECONDS = _float("POLL_INTERVAL_SECONDS", 5.0)
DB_PATH = os.getenv("DB_PATH", "hitl_state.db")
MAX_ATTEMPTS = int(_float("MAX_ATTEMPTS", 3))


def missing_settings() -> list[str]:
    """Names of settings required for the full workflow that are not configured."""
    required = {
        "SLACK_WEBHOOK_URL": SLACK_WEBHOOK_URL,
        "HUBSPOT_SERVICE_KEY": HUBSPOT_SERVICE_KEY,
        "HUBSPOT_STAGE_NEW": STAGE_NEW,
        "HUBSPOT_STAGE_ESCALATED": STAGE_ESCALATED,
        "HUBSPOT_STAGE_WAITING_CUSTOMER": STAGE_WAITING_CUSTOMER,
        "HUBSPOT_STAGE_CLOSED": STAGE_CLOSED,
    }
    return [k for k, v in required.items() if not v or "ここに" in v]
