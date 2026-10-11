"""MCP server: Google Ads (read + guarded writes) and CallRail for the limo account."""

import json
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from . import ads, callrail, config, reports, store, writes

mcp = FastMCP(
    "limo-ads",
    instructions=(
        "Google Ads and CallRail for a single limo/car-service account. Phone calls are the main "
        "conversion. Read tools are safe to call freely. Write tools never change the account "
        "directly: each change is validated with Google first, logged with the reason you give, "
        "and either applied (if inside the auto limits) or queued for the owner's approval. "
        "Always state the evidence behind a proposed change in `reason`. Use list_pending_changes "
        "to see the approval queue; only call approve_change when the user has explicitly approved "
        "that change id in conversation."
    ),
    host="0.0.0.0",
    port=config.PORT,
    streamable_http_path=f"/{config.MCP_PATH_SECRET}/mcp",
    stateless_http=True,
    json_response=True,
)


def _out(data: Any) -> str:
    return json.dumps(data, indent=1, default=str)


# =========================================================================== read: Google Ads


@mcp.tool()
def gaql_search(query: str, limit: int = 500) -> str:
    """Run any read-only Google Ads Query Language (GAQL) query against the account.
    Use for anything the pre-built reports don't cover. Money fields are in micros (divide by 1,000,000)."""
    return _out(ads.search(query, limit=limit))


@mcp.tool()
def campaign_performance(days: int = 7, start_date: str | None = None, end_date: str | None = None) -> str:
    """Per-campaign spend, clicks, calls from ads, conversions, value, budget, and search impression share
    (including share lost to budget and to rank). Defaults to the last N full days; dates are YYYY-MM-DD."""
    return _out(reports.campaign_performance(start_date, end_date, days))


@mcp.tool()
def daily_trend(days: int = 30, start_date: str | None = None, end_date: str | None = None) -> str:
    """Account-level daily spend, clicks, calls from ads, conversions and value."""
    return _out(reports.daily_trend(start_date, end_date, days))


@mcp.tool()
def search_terms(days: int = 14, limit: int = 100, start_date: str | None = None, end_date: str | None = None) -> str:
    """Search terms that triggered ads, highest spend first, with the campaign and ad group IDs
    needed to add negatives or keywords."""
    return _out(reports.search_terms(start_date, end_date, days, limit))


@mcp.tool()
def keyword_performance(days: int = 30, start_date: str | None = None, end_date: str | None = None) -> str:
    """Keyword-level performance and quality score. Each keyword's `id` is the value set_status expects."""
    return _out(reports.keywords(start_date, end_date, days))


@mcp.tool()
def google_call_log(days: int = 7) -> str:
    """Individual calls from ads as Google records them (Google forwarding numbers): time, duration,
    answered/missed, caller area code, campaign."""
    return _out(reports.google_call_log(days))


@mcp.tool()
def conversion_setup(days: int = 30) -> str:
    """Enabled conversion actions, which ones feed the Conversions column, and recent volume per action.
    Flags when more than one call-type action is counted (double counting)."""
    return _out(reports.conversion_setup(days=days))


@mcp.tool()
def change_history(days: int = 14) -> str:
    """Changes made to the account by anyone (people, Instinct, scripts, this server) in the last N days, max 29."""
    return _out(reports.change_history(days))


@mcp.tool()
def ad_health() -> str:
    """Every live ad with status, policy approval, review status and ad strength.
    Each ad's `id` is the value set_status expects."""
    return _out(reports.ad_health())


# =========================================================================== read: CallRail


@mcp.tool()
def callrail_trackers() -> str:
    """CallRail tracking numbers and their sources. Use this to find the ads-only tracker ID."""
    return _out(callrail.trackers())


@mcp.tool()
def callrail_calls(
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    tracker_id: str | None = None,
    ads_only: bool = False,
) -> str:
    """Individual CallRail calls: caller number, time, duration, answered, first-time caller, source,
    keyword/campaign/gclid when CallRail captured them, lead status and value.
    ads_only=True filters to the ads-only tracking number."""
    if ads_only:
        tracker_id = tracker_id or config.CALLRAIL_ADS_TRACKER_ID
    return _out(callrail.calls(start_date, end_date, days, tracker_id))


@mcp.tool()
def callrail_summary(days: int = 7, start_date: str | None = None, end_date: str | None = None, ads_only: bool = False) -> str:
    """CallRail totals: calls, answer rate, first-time callers, 60s+ calls, by source and by day."""
    tracker = config.CALLRAIL_ADS_TRACKER_ID if ads_only else None
    return _out(callrail.summary(start_date, end_date, days, tracker))


# =========================================================================== reports


@mcp.tool()
def period_report(days: int = 7) -> str:
    """Full report for the last N full days versus the N days before: totals and % change, campaigns,
    daily trend, CallRail ad calls, converting search terms, spend with no conversions, budget-limited
    campaigns, ad policy issues, pending and applied changes. Use for the weekly report."""
    return _out(reports.period_summary(days))


@mcp.tool()
def anomaly_check() -> str:
    """Yesterday versus the trailing 14 days: spend spikes or drops, missing calls, unanswered ad calls,
    disapproved ads, pending approvals. An empty `flags` list means nothing needs attention."""
    return _out(reports.anomaly_check())


# =========================================================================== writes (guarded)


def _submit(kind: str, params: dict, reason: str) -> str:
    try:
        return _out(writes.submit(kind, params, reason))
    except writes.GuardrailError as exc:
        return _out({"status": "refused", "reason": str(exc)})


@mcp.tool()
def add_negative_keywords(campaign_id: str, keywords: list[dict], reason: str) -> str:
    """Add campaign-level negative keywords. keywords: [{"text": "...", "match_type": "EXACT|PHRASE|BROAD"}].
    reason: the evidence (e.g. search terms, spend, zero conversions). Validated, logged, and applied or queued."""
    return _submit("add_negative_keywords", {"campaign_id": campaign_id, "keywords": keywords}, reason)


@mcp.tool()
def add_keywords(ad_group_id: str, keywords: list[dict], reason: str) -> str:
    """Add keywords to an ad group. keywords: [{"text": "...", "match_type": "EXACT|PHRASE|BROAD"}].
    Always queued for approval."""
    return _submit("add_keywords", {"ad_group_id": ad_group_id, "keywords": keywords}, reason)


@mcp.tool()
def set_campaign_budget(campaign_id: str, daily_budget: float, reason: str) -> str:
    """Set a campaign's daily budget in account currency. Refused above MAX_DAILY_BUDGET or on shared budgets;
    queued when the move exceeds the auto limit."""
    return _submit("set_campaign_budget", {"campaign_id": campaign_id, "daily_budget": daily_budget}, reason)


@mcp.tool()
def set_status(level: str, id: str, status: str, reason: str) -> str:
    """Pause or enable a campaign, ad_group, ad or keyword. level: campaign|ad_group|ad|keyword.
    id: numeric for campaign/ad_group; '<ad_group_id>~<ad_id>' for ad; '<ad_group_id>~<criterion_id>' for keyword.
    status: ENABLED|PAUSED."""
    return _submit("set_status", {"level": level, "id": id, "status": status}, reason)


@mcp.tool()
def list_pending_changes() -> str:
    """Changes waiting for approval, with summary, before/after and the reason given."""
    return _out(store.list_changes("pending"))


@mcp.tool()
def change_log(limit: int = 30, status: str | None = None) -> str:
    """This server's change log, newest first. status filter: pending|applied|rejected|failed."""
    return _out(store.list_changes(status, limit))


@mcp.tool()
def approve_change(change_id: int) -> str:
    """Apply a queued change. Only call when the user has explicitly approved this change id.
    Re-reads live state and re-validates before applying."""
    try:
        return _out(writes.approve(change_id))
    except writes.GuardrailError as exc:
        return _out({"status": "refused", "reason": str(exc)})


@mcp.tool()
def reject_change(change_id: int, note: str = "") -> str:
    """Reject a queued change so it is never applied."""
    try:
        return _out(writes.reject(change_id, note))
    except writes.GuardrailError as exc:
        return _out({"status": "refused", "reason": str(exc)})


@mcp.tool()
def server_settings() -> str:
    """Current guardrail settings and which integrations are configured."""
    return _out(
        {
            "customer_id": config.GOOGLE_ADS_CUSTOMER_ID,
            "write_mode": config.WRITE_MODE,
            "max_auto_budget_change_pct": config.MAX_AUTO_BUDGET_CHANGE_PCT,
            "max_daily_budget": config.MAX_DAILY_BUDGET,
            "auto_campaign_status": config.AUTO_CAMPAIGN_STATUS,
            "callrail_configured": bool(config.CALLRAIL_API_KEY and config.CALLRAIL_ACCOUNT_ID),
            "callrail_ads_tracker_id": config.CALLRAIL_ADS_TRACKER_ID,
        }
    )


def main() -> None:
    app = mcp.streamable_http_app()

    async def health(scope, receive, send):  # tiny ASGI wrapper so Railway health checks don't need the secret
        if scope["type"] == "http" and scope["path"] == "/health":
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await app(scope, receive, send)

    uvicorn.run(health, host="0.0.0.0", port=config.PORT, lifespan="on")


if __name__ == "__main__":
    main()
