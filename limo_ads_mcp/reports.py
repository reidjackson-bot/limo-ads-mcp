"""Pre-built reports. Scheduled runs call these so numbers are computed the same way every week."""

from datetime import date, datetime, timedelta
from typing import Any

from . import ads, callrail, config, store


def _m(row: dict, key: str) -> float:
    v = row.get("metrics", {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _window(days: int, offset_days: int = 0) -> tuple[str, str]:
    end = date.today() - timedelta(days=1 + offset_days)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


# --------------------------------------------------------------------------- building blocks


def campaign_performance(start_date=None, end_date=None, days: int = 7) -> list[dict[str, Any]]:
    rows = ads.search(
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign.bidding_strategy_type, campaign_budget.amount_micros, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
        "metrics.conversions_value, metrics.phone_calls, "
        "metrics.search_impression_share, metrics.search_budget_lost_impression_share, "
        "metrics.search_rank_lost_impression_share "
        f"FROM campaign WHERE {ads.date_clause(start_date, end_date, days)} "
        "AND campaign.status != 'REMOVED'"
    )
    out = []
    for r in rows:
        c, m = r["campaign"], r.get("metrics", {})
        cost = ads.micros(m.get("cost_micros"))
        conv = _m(r, "conversions")
        out.append(
            {
                "campaign_id": c.get("id"),
                "campaign": c.get("name"),
                "status": c.get("status"),
                "type": c.get("advertising_channel_type"),
                "bidding": c.get("bidding_strategy_type"),
                "daily_budget": ads.micros(r.get("campaign_budget", {}).get("amount_micros")),
                "impressions": int(_m(r, "impressions")),
                "clicks": int(_m(r, "clicks")),
                "cost": cost,
                "calls_from_ads": int(_m(r, "phone_calls")),
                "conversions": round(conv, 2),
                "conversion_value": round(_m(r, "conversions_value"), 2),
                "cost_per_conversion": round(cost / conv, 2) if conv else None,
                "search_impression_share": m.get("search_impression_share"),
                "lost_is_budget": m.get("search_budget_lost_impression_share"),
                "lost_is_rank": m.get("search_rank_lost_impression_share"),
            }
        )
    return sorted(out, key=lambda x: x["cost"], reverse=True)


def daily_trend(start_date=None, end_date=None, days: int = 30) -> list[dict[str, Any]]:
    rows = ads.search(
        "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions, metrics.conversions_value, metrics.phone_calls "
        f"FROM customer WHERE {ads.date_clause(start_date, end_date, days)}"
    )
    return [
        {
            "date": r["segments"]["date"],
            "impressions": int(_m(r, "impressions")),
            "clicks": int(_m(r, "clicks")),
            "cost": ads.micros(r.get("metrics", {}).get("cost_micros")),
            "calls_from_ads": int(_m(r, "phone_calls")),
            "conversions": round(_m(r, "conversions"), 2),
            "conversion_value": round(_m(r, "conversions_value"), 2),
        }
        for r in sorted(rows, key=lambda r: r["segments"]["date"])
    ]


def search_terms(start_date=None, end_date=None, days: int = 14, limit: int = 100) -> list[dict[str, Any]]:
    rows = ads.search(
        "SELECT search_term_view.search_term, search_term_view.status, campaign.id, campaign.name, "
        "ad_group.id, ad_group.name, metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions FROM search_term_view "
        f"WHERE {ads.date_clause(start_date, end_date, days)} "
        f"ORDER BY metrics.cost_micros DESC LIMIT {int(limit)}"
    )
    return [
        {
            "term": r["search_term_view"]["search_term"],
            "status": r["search_term_view"].get("status"),
            "campaign_id": r["campaign"]["id"],
            "campaign": r["campaign"]["name"],
            "ad_group_id": r["ad_group"]["id"],
            "ad_group": r["ad_group"]["name"],
            "impressions": int(_m(r, "impressions")),
            "clicks": int(_m(r, "clicks")),
            "cost": ads.micros(r.get("metrics", {}).get("cost_micros")),
            "conversions": round(_m(r, "conversions"), 2),
        }
        for r in rows
    ]


def keywords(start_date=None, end_date=None, days: int = 30) -> list[dict[str, Any]]:
    rows = ads.search(
        "SELECT ad_group.id, ad_group.name, campaign.name, ad_group_criterion.criterion_id, "
        "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group_criterion.status, ad_group_criterion.quality_info.quality_score, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
        f"FROM keyword_view WHERE {ads.date_clause(start_date, end_date, days)} "
        "AND ad_group_criterion.status != 'REMOVED'"
    )
    out = []
    for r in rows:
        k = r["ad_group_criterion"]
        out.append(
            {
                "id": f"{r['ad_group']['id']}~{k['criterion_id']}",
                "keyword": k["keyword"]["text"],
                "match_type": k["keyword"]["match_type"],
                "status": k.get("status"),
                "quality_score": k.get("quality_info", {}).get("quality_score"),
                "ad_group": r["ad_group"]["name"],
                "campaign": r["campaign"]["name"],
                "clicks": int(_m(r, "clicks")),
                "cost": ads.micros(r.get("metrics", {}).get("cost_micros")),
                "conversions": round(_m(r, "conversions"), 2),
            }
        )
    return sorted(out, key=lambda x: x["cost"], reverse=True)


def google_call_log(days: int = 7) -> list[dict[str, Any]]:
    start, _ = _window(days)
    rows = ads.search(
        "SELECT call_view.start_call_date_time, call_view.call_duration_seconds, "
        "call_view.call_status, call_view.type, call_view.caller_area_code, "
        "call_view.call_tracking_display_location, campaign.name, ad_group.name "
        f"FROM call_view WHERE call_view.start_call_date_time >= '{start} 00:00:00' "
        "ORDER BY call_view.start_call_date_time DESC"
    )
    return [
        {
            "start": r["call_view"].get("start_call_date_time"),
            "duration_s": int(r["call_view"].get("call_duration_seconds") or 0),
            "status": r["call_view"].get("call_status"),
            "type": r["call_view"].get("type"),
            "area_code": r["call_view"].get("caller_area_code"),
            "shown_on": r["call_view"].get("call_tracking_display_location"),
            "campaign": r.get("campaign", {}).get("name"),
            "ad_group": r.get("ad_group", {}).get("name"),
        }
        for r in rows
    ]


def conversion_setup(start_date=None, end_date=None, days: int = 30) -> dict[str, Any]:
    actions = ads.search(
        "SELECT conversion_action.id, conversion_action.name, conversion_action.type, "
        "conversion_action.category, conversion_action.status, conversion_action.primary_for_goal, "
        "conversion_action.include_in_conversions_metric, conversion_action.counting_type "
        "FROM conversion_action WHERE conversion_action.status = 'ENABLED'"
    )
    volume = ads.search(
        "SELECT segments.conversion_action_name, metrics.all_conversions, metrics.conversions, "
        "metrics.all_conversions_value FROM customer "
        f"WHERE {ads.date_clause(start_date, end_date, days)}"
    )
    vol = {
        r["segments"]["conversion_action_name"]: {
            "all_conversions": round(_m(r, "all_conversions"), 2),
            "counted_in_conversions": round(_m(r, "conversions"), 2),
            "value": round(_m(r, "all_conversions_value"), 2),
        }
        for r in volume
    }
    out = []
    for a in actions:
        ca = a["conversion_action"]
        out.append(
            {
                "id": ca.get("id"),
                "name": ca.get("name"),
                "type": ca.get("type"),
                "category": ca.get("category"),
                "primary": ca.get("primary_for_goal", False),
                "in_conversions_column": ca.get("include_in_conversions_metric", False),
                "counting": ca.get("counting_type"),
                "volume": vol.get(ca.get("name"), {}),
            }
        )
    primaries_with_calls = [
        a["name"] for a in out if a["in_conversions_column"] and "CALL" in str(a["type"]).upper()
    ]
    warning = None
    if len(primaries_with_calls) > 1:
        warning = (
            "More than one call-type conversion action feeds the Conversions column: "
            + ", ".join(primaries_with_calls)
            + ". The same phone call can be counted more than once."
        )
    return {"actions": out, "double_count_warning": warning}


def change_history(days: int = 14) -> list[dict[str, Any]]:
    days = min(days, 29)
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    end = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    rows = ads.search(
        "SELECT change_event.change_date_time, change_event.change_resource_type, "
        "change_event.resource_change_operation, change_event.user_email, change_event.client_type, "
        "change_event.changed_fields, campaign.name FROM change_event "
        f"WHERE change_event.change_date_time >= '{start}' AND change_event.change_date_time <= '{end}' "
        "ORDER BY change_event.change_date_time DESC LIMIT 200"
    )
    return [
        {
            "when": r["change_event"].get("change_date_time"),
            "resource": r["change_event"].get("change_resource_type"),
            "operation": r["change_event"].get("resource_change_operation"),
            "by": r["change_event"].get("user_email"),
            "via": r["change_event"].get("client_type"),
            "fields": r["change_event"].get("changed_fields"),
            "campaign": r.get("campaign", {}).get("name"),
        }
        for r in rows
    ]


def ad_health() -> list[dict[str, Any]]:
    rows = ads.search(
        "SELECT ad_group.id, ad_group.name, campaign.name, ad_group_ad.ad.id, ad_group_ad.ad.type, "
        "ad_group_ad.status, ad_group_ad.policy_summary.approval_status, "
        "ad_group_ad.policy_summary.review_status, ad_group_ad.ad_strength "
        "FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED' "
        "AND campaign.status != 'REMOVED'"
    )
    return [
        {
            "id": f"{r['ad_group']['id']}~{r['ad_group_ad']['ad']['id']}",
            "campaign": r["campaign"]["name"],
            "ad_group": r["ad_group"]["name"],
            "type": r["ad_group_ad"]["ad"].get("type"),
            "status": r["ad_group_ad"].get("status"),
            "approval": r["ad_group_ad"].get("policy_summary", {}).get("approval_status"),
            "review": r["ad_group_ad"].get("policy_summary", {}).get("review_status"),
            "ad_strength": r["ad_group_ad"].get("ad_strength"),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- composite reports


def _totals(trend: list[dict]) -> dict[str, float]:
    t = {k: 0.0 for k in ("cost", "clicks", "impressions", "calls_from_ads", "conversions", "conversion_value")}
    for d in trend:
        for k in t:
            t[k] += d[k]
    t = {k: round(v, 2) for k, v in t.items()}
    t["cost_per_conversion"] = round(t["cost"] / t["conversions"], 2) if t["conversions"] else None
    t["cost_per_ad_call"] = round(t["cost"] / t["calls_from_ads"], 2) if t["calls_from_ads"] else None
    return t


def _delta(cur: dict, prev: dict) -> dict[str, float | None]:
    out = {}
    for k, v in cur.items():
        p = prev.get(k)
        out[k] = round((v - p) / p * 100, 1) if isinstance(v, (int, float)) and p else None
    return out


def _callrail_block(start: str, end: str) -> dict[str, Any] | None:
    if not config.CALLRAIL_API_KEY:
        return None
    try:
        return {
            "ads_tracker": callrail.summary(start, end, tracker_id=config.CALLRAIL_ADS_TRACKER_ID)
            if config.CALLRAIL_ADS_TRACKER_ID
            else None,
            "all_tracked_calls": callrail.summary(start, end),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def period_summary(days: int = 7) -> dict[str, Any]:
    start, end = _window(days)
    pstart, pend = _window(days, offset_days=days)
    trend = daily_trend(start, end)
    prev = daily_trend(pstart, pend)
    cur_t, prev_t = _totals(trend), _totals(prev)
    terms = search_terms(start, end, limit=200)
    wasted = [t for t in terms if t["cost"] > 0 and t["conversions"] == 0 and t["status"] != "EXCLUDED"]
    campaigns = campaign_performance(start, end)
    budget_limited = [
        c["campaign"] for c in campaigns if (c.get("lost_is_budget") or 0) >= 0.10 and c["status"] == "ENABLED"
    ]
    bad_ads = [a for a in ad_health() if a["approval"] in ("DISAPPROVED", "APPROVED_LIMITED")]
    return {
        "period": {"start": start, "end": end},
        "previous_period": {"start": pstart, "end": pend},
        "totals": cur_t,
        "previous_totals": prev_t,
        "change_pct": _delta(cur_t, prev_t),
        "campaigns": campaigns,
        "daily": trend,
        "callrail": _callrail_block(start, end),
        "top_converting_terms": [t for t in terms if t["conversions"] > 0][:15],
        "spend_with_no_conversions": sorted(wasted, key=lambda t: t["cost"], reverse=True)[:20],
        "budget_limited_campaigns": budget_limited,
        "ads_with_policy_issues": bad_ads,
        "pending_changes": store.list_changes("pending"),
        "changes_applied_this_period": [
            c for c in store.list_changes("applied", limit=100) if c["decided_at"][:10] >= start
        ],
    }


def anomaly_check() -> dict[str, Any]:
    """Compare yesterday to the trailing 14 days. Returns an empty flags list when nothing is off."""
    trend = daily_trend(days=15)
    flags: list[str] = []
    if len(trend) < 2:
        return {"flags": ["Not enough daily data to compare"], "yesterday": trend[-1] if trend else None}
    y, base = trend[-1], trend[:-1]
    avg = {k: sum(d[k] for d in base) / len(base) for k in ("cost", "clicks", "calls_from_ads", "conversions")}
    if avg["cost"] and y["cost"] > 1.6 * avg["cost"]:
        flags.append(f"Spend {y['cost']:.2f} is {y['cost'] / avg['cost']:.1f}x the 14-day average ({avg['cost']:.2f})")
    if avg["cost"] and y["cost"] < 0.4 * avg["cost"]:
        flags.append(f"Spend {y['cost']:.2f} is far below the 14-day average ({avg['cost']:.2f}); check delivery, billing or budgets")
    if y["cost"] > 0 and y["clicks"] == 0:
        flags.append("Spend with zero clicks")
    if avg["calls_from_ads"] >= 1 and y["calls_from_ads"] == 0:
        flags.append(f"No calls from ads yesterday (14-day average {avg['calls_from_ads']:.1f}/day)")

    cr = None
    if config.CALLRAIL_API_KEY and config.CALLRAIL_ADS_TRACKER_ID:
        try:
            cr = callrail.summary(y["date"], y["date"], tracker_id=config.CALLRAIL_ADS_TRACKER_ID)
            if y["calls_from_ads"] and cr["total_calls"] == 0:
                flags.append("Google recorded ad calls but CallRail's ads number logged none; check call forwarding")
            if cr["total_calls"] and (cr["answer_rate"] or 0) < 0.6:
                flags.append(
                    f"Only {cr['answered']} of {cr['total_calls']} ad calls were answered yesterday"
                )
        except Exception as exc:  # noqa: BLE001
            flags.append(f"CallRail check failed: {exc}")

    disapproved = [a for a in ad_health() if a["approval"] == "DISAPPROVED" and a["status"] == "ENABLED"]
    if disapproved:
        flags.append(f"{len(disapproved)} enabled ad(s) disapproved")
    pending = store.list_changes("pending")
    if pending:
        flags.append(f"{len(pending)} change(s) waiting for approval")
    return {"date": y["date"], "flags": flags, "yesterday": y, "trailing_avg": {k: round(v, 2) for k, v in avg.items()}, "callrail_ads": cr}
