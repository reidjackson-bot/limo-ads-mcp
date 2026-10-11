"""Environment-driven settings. Every value comes from Railway variables."""

import os


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# --- MCP endpoint -----------------------------------------------------------
# The connector URL is https://<host>/<MCP_PATH_SECRET>/mcp . Long random string.
MCP_PATH_SECRET = _env("MCP_PATH_SECRET", required=True)
PORT = int(_env("PORT", "8000"))

# --- Google Ads -------------------------------------------------------------
GOOGLE_ADS_DEVELOPER_TOKEN = _env("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_CLIENT_ID = _env("GOOGLE_ADS_CLIENT_ID")
GOOGLE_ADS_CLIENT_SECRET = _env("GOOGLE_ADS_CLIENT_SECRET")
GOOGLE_ADS_REFRESH_TOKEN = _env("GOOGLE_ADS_REFRESH_TOKEN")
# Manager account ID if access runs through an MCC, digits only.
GOOGLE_ADS_LOGIN_CUSTOMER_ID = _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
# The one account this server is allowed to touch, digits only.
GOOGLE_ADS_CUSTOMER_ID = (_env("GOOGLE_ADS_CUSTOMER_ID") or "").replace("-", "")

# --- Write guardrails -------------------------------------------------------
# "propose": every change is queued for approval.
# "auto": changes inside the limits below apply immediately; the rest queue.
WRITE_MODE = (_env("WRITE_MODE", "propose") or "propose").lower()
# Largest budget move (as % of current) allowed without approval.
MAX_AUTO_BUDGET_CHANGE_PCT = float(_env("MAX_AUTO_BUDGET_CHANGE_PCT", "20"))
# Hard ceiling on any campaign's daily budget, in account currency. Never exceeded, even with approval.
MAX_DAILY_BUDGET = float(_env("MAX_DAILY_BUDGET", "150"))
# Pausing or enabling a whole campaign always needs approval unless this is "true".
AUTO_CAMPAIGN_STATUS = (_env("AUTO_CAMPAIGN_STATUS", "false") or "false").lower() == "true"

# --- Storage ----------------------------------------------------------------
# Point this at a Railway volume mount so the change log survives deploys.
DB_PATH = _env("DB_PATH", "/data/limo_ads.db")

# --- CallRail ---------------------------------------------------------------
CALLRAIL_API_KEY = _env("CALLRAIL_API_KEY")
CALLRAIL_ACCOUNT_ID = _env("CALLRAIL_ACCOUNT_ID")
CALLRAIL_COMPANY_ID = _env("CALLRAIL_COMPANY_ID")  # optional filter
# The tracker whose number appears only on ads. Reports use it as "calls from ads".
CALLRAIL_ADS_TRACKER_ID = _env("CALLRAIL_ADS_TRACKER_ID")
