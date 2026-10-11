"""CallRail API v3 client (read-only)."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

from . import config

BASE = "https://api.callrail.com/v3/a"
EXTRA_FIELDS = (
    "source_name,tracker_id,first_call,keywords,campaign,gclid,medium,"
    "landing_page_url,lead_status,value,tags,company_id"
)


def _headers() -> dict[str, str]:
    if not config.CALLRAIL_API_KEY or not config.CALLRAIL_ACCOUNT_ID:
        raise RuntimeError("CALLRAIL_API_KEY and CALLRAIL_ACCOUNT_ID must be set")
    return {"Authorization": f'Token token="{config.CALLRAIL_API_KEY}"'}


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}/{config.CALLRAIL_ACCOUNT_ID}/{path}"
    with httpx.Client(timeout=30) as http:
        resp = http.get(url, headers=_headers(), params=params)
        if resp.status_code == 400 and "fields" in params:
            # A field name this account doesn't support; fall back to defaults.
            params = {k: v for k, v in params.items() if k != "fields"}
            resp = http.get(url, headers=_headers(), params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"CallRail {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def _paged(path: str, key: str, params: dict[str, Any], max_pages: int = 20) -> list[dict]:
    out: list[dict] = []
    page = 1
    while page <= max_pages:
        data = _get(path, {**params, "page": page, "per_page": 250})
        out.extend(data.get(key, []))
        if page >= int(data.get("total_pages") or 1):
            break
        page += 1
    return out


def _range(start_date: str | None, end_date: str | None, days: int) -> tuple[str, str]:
    if start_date and end_date:
        return start_date, end_date
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def trackers() -> list[dict]:
    params: dict[str, Any] = {}
    if config.CALLRAIL_COMPANY_ID:
        params["company_id"] = config.CALLRAIL_COMPANY_ID
    rows = _paged("trackers.json", "trackers", params)
    return [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "type": t.get("type"),
            "status": t.get("status"),
            "source": t.get("source"),
            "tracking_numbers": t.get("tracking_numbers"),
            "destination_number": t.get("destination_number"),
        }
        for t in rows
    ]


def calls(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
    tracker_id: str | None = None,
) -> list[dict]:
    start, end = _range(start_date, end_date, days)
    params: dict[str, Any] = {"start_date": start, "end_date": end, "fields": EXTRA_FIELDS}
    if config.CALLRAIL_COMPANY_ID:
        params["company_id"] = config.CALLRAIL_COMPANY_ID
    rows = _paged("calls.json", "calls", params)
    if tracker_id:
        rows = [r for r in rows if str(r.get("tracker_id")) == str(tracker_id)]
    return rows


def summary(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
    tracker_id: str | None = None,
) -> dict[str, Any]:
    rows = calls(start_date, end_date, days, tracker_id)
    by_day: dict[str, Counter] = defaultdict(Counter)
    by_source: Counter = Counter()
    for r in rows:
        day = (r.get("start_time") or "")[:10]
        by_day[day]["calls"] += 1
        by_day[day]["answered"] += 1 if r.get("answered") else 0
        by_day[day]["first_time"] += 1 if r.get("first_call") else 0
        by_source[r.get("source_name") or r.get("source") or "unknown"] += 1
    total = len(rows)
    answered = sum(1 for r in rows if r.get("answered"))
    first = sum(1 for r in rows if r.get("first_call"))
    long_calls = sum(1 for r in rows if (r.get("duration") or 0) >= 60)
    return {
        "range": _range(start_date, end_date, days),
        "total_calls": total,
        "answered": answered,
        "answer_rate": round(answered / total, 3) if total else None,
        "first_time_callers": first,
        "calls_60s_plus": long_calls,
        "missed_or_voicemail": total - answered,
        "by_source": dict(by_source.most_common()),
        "by_day": {d: dict(c) for d, c in sorted(by_day.items())},
    }
