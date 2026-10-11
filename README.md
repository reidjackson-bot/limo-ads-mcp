# limo-ads-mcp

Remote MCP server that gives Claude read access, guarded write access and reporting on the limo service's Google Ads account, plus read access to CallRail. Built to run on Railway and connect to claude.ai as a custom connector.

## Tools

**Google Ads reads:** `gaql_search`, `campaign_performance`, `daily_trend`, `search_terms`, `keyword_performance`, `google_call_log`, `conversion_setup`, `change_history`, `ad_health`

**CallRail reads:** `callrail_trackers`, `callrail_calls`, `callrail_summary`

**Reports:** `period_report` (the weekly report: this period vs. the previous one, campaigns, CallRail ad calls, converting terms, spend with no conversions, budget-limited campaigns, policy issues, changes) and `anomaly_check` (yesterday vs. trailing 14 days; empty `flags` means nothing to act on)

**Writes:** `add_negative_keywords`, `add_keywords`, `set_campaign_budget`, `set_status` (campaign, ad group, ad or keyword; pause or enable only)

**Approval queue and log:** `list_pending_changes`, `approve_change`, `reject_change`, `change_log`, `server_settings`

## How writes are guarded

Every write runs the same path. The server reads the live state, refuses anything outside the hard limits, sends the change to Google with `validate_only` so bad requests fail before touching the account, then either queues it or applies it. Every proposal and every applied change is written to the change log with before and after values and the reason Claude gave.

- `WRITE_MODE=propose` (default) queues every change. Start here.
- `WRITE_MODE=auto` applies changes inside the limits and queues the rest. Under auto, negative keywords and ad- or keyword-level pauses apply immediately. Budget moves above `MAX_AUTO_BUDGET_CHANGE_PCT`, campaign pauses (unless `AUTO_CAMPAIGN_STATUS=true`) and new keywords always queue.
- `MAX_DAILY_BUDGET` is a ceiling no change can cross, approved or not.
- Shared budgets, bidding strategy, conversion settings and removals are deliberately not exposed.
- The server only ever touches `GOOGLE_ADS_CUSTOMER_ID`.

Approving a queued change re-reads the account and re-validates first, so a stale proposal fails cleanly instead of overwriting something that moved.

## Deploy on Railway

1. Push this folder to a new GitHub repo (for example `reidjackson-bot/limo-ads-mcp`) and create a Railway service from it. The Dockerfile and `railway.json` handle the build and the `/health` check.
2. Add a volume mounted at `/data` so the change log survives redeploys.
3. Set the variables from `.env.example`. The Google Ads credentials can be the same ones the call-conversion worker uses. `MCP_PATH_SECRET` should be a long random string (`openssl rand -hex 24`).
4. In claude.ai, add a custom connector pointing at `https://<railway-host>/<MCP_PATH_SECRET>/mcp`. Anyone with that URL can use the tools, so treat it like a password.
5. Ask Claude to run `server_settings`, then `callrail_trackers` to find the ads-only tracker. Put its ID in `CALLRAIL_ADS_TRACKER_ID` and redeploy.

CallRail API keys are under Settings, then Integrations, then API Keys. The account ID is in the CallRail URL.

## First checks after connecting

Run `conversion_setup` before trusting any report. The server flags when more than one call-type conversion action counts toward the Conversions column. With CallRail on the ads, Google's own call reporting and the worker's revenue uploads, the same phone call could be counted two or three times. Pick one primary action (the worker's revenue-valued call conversions are the strongest candidate) and set the rest to secondary.

Run `change_history` to see what Instinct has been changing. Turn off Instinct's automated changes before switching this server to `WRITE_MODE=auto`, so two systems aren't adjusting the same budgets and negatives.

## Scheduled work (set up in Claude once connected)

- Weekly, Monday morning: `period_report` for the last 7 days, written up with a short summary for the account owner up top and detail below.
- Daily: `anomaly_check`; stays silent when `flags` is empty.

## Local run

```
pip install -r requirements.txt
MCP_PATH_SECRET=dev DB_PATH=./limo_ads.db python -m limo_ads_mcp.server
```

## Not in this version

Responsive search ad text edits, ad schedules, location targeting, and pulling Limo Anywhere booking revenue directly (today that comes in through the worker's uploaded call conversions).
