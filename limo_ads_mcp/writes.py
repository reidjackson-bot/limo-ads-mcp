"""Guarded write path.

Every change goes through the same pipeline:
  1. precheck   read current state from the account, compute before/after, enforce hard limits
  2. validate   send the mutate with validate_only=True so Google rejects bad changes up front
  3. route      queue for approval, or apply now if WRITE_MODE=auto and the change is inside the limits
  4. log        every proposal and every applied change lands in the change log with the reason given
"""

import re
from typing import Any, Callable

from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers

from . import ads, config, store

MATCH_TYPES = {"EXACT", "PHRASE", "BROAD"}
STATUSES = {"ENABLED", "PAUSED"}
LEVELS = {"campaign", "ad_group", "ad", "keyword"}


class GuardrailError(ValueError):
    """A change that is refused outright, with or without approval."""


# --------------------------------------------------------------------------- helpers


def _digits(value: Any, name: str) -> str:
    s = str(value).replace("-", "").strip()
    if not s.isdigit():
        raise GuardrailError(f"{name} must be numeric, got {value!r}")
    return s


def _composite(value: Any, name: str) -> tuple[str, str]:
    """IDs written as '<ad_group_id>~<child_id>' for ads and keywords."""
    parts = str(value).split("~")
    if len(parts) != 2:
        raise GuardrailError(f"{name} must look like '<ad_group_id>~<id>', got {value!r}")
    return _digits(parts[0], name), _digits(parts[1], name)


def _clean_keywords(keywords: list[dict]) -> list[dict]:
    if not keywords:
        raise GuardrailError("No keywords given")
    if len(keywords) > 50:
        raise GuardrailError("At most 50 keywords per change")
    out = []
    for kw in keywords:
        text = re.sub(r"\s+", " ", str(kw.get("text", ""))).strip().lower()
        match = str(kw.get("match_type", "PHRASE")).upper()
        if not text or len(text) > 80 or len(text.split()) > 10:
            raise GuardrailError(f"Keyword text not allowed: {kw.get('text')!r}")
        if match not in MATCH_TYPES:
            raise GuardrailError(f"match_type must be one of {sorted(MATCH_TYPES)}")
        out.append({"text": text, "match_type": match})
    return out


def _mutate(service: str, method: str, request_type: str, operations: list, validate_only: bool):
    ga = ads.client()
    svc = ga.get_service(service)
    req = ga.get_type(request_type)
    req.customer_id = ads.customer_id()
    req.operations.extend(operations)
    req.validate_only = validate_only
    try:
        resp = getattr(svc, method)(request=req)
    except GoogleAdsException as exc:
        raise RuntimeError(ads.format_error(exc)) from exc
    return [r.resource_name for r in resp.results] if not validate_only else []


# --------------------------------------------------------------------------- prechecks
# Each returns (summary, before, after, params) with params normalized.


def _pre_negatives(p: dict):
    cid = _digits(p["campaign_id"], "campaign_id")
    kws = _clean_keywords(p["keywords"])
    rows = ads.search(f"SELECT campaign.name FROM campaign WHERE campaign.id = {cid}")
    if not rows:
        raise GuardrailError(f"Campaign {cid} not found")
    name = rows[0]["campaign"]["name"]
    existing = ads.search(
        "SELECT campaign_criterion.keyword.text, campaign_criterion.keyword.match_type "
        f"FROM campaign_criterion WHERE campaign.id = {cid} "
        "AND campaign_criterion.negative = TRUE AND campaign_criterion.type = KEYWORD"
    )
    have = {
        (r["campaign_criterion"]["keyword"]["text"].lower(), r["campaign_criterion"]["keyword"]["match_type"])
        for r in existing
    }
    new = [k for k in kws if (k["text"], k["match_type"]) not in have]
    if not new:
        raise GuardrailError("All of those negatives already exist on the campaign")
    listing = ", ".join(f"{k['text']} [{k['match_type'].lower()}]" for k in new)
    summary = f"Add {len(new)} negative keyword(s) to '{name}': {listing}"
    return summary, {"existing_negatives": len(have)}, {"added": new}, {"campaign_id": cid, "keywords": new}


def _pre_keywords(p: dict):
    ag = _digits(p["ad_group_id"], "ad_group_id")
    kws = _clean_keywords(p["keywords"])
    rows = ads.search(
        f"SELECT ad_group.name, campaign.name FROM ad_group WHERE ad_group.id = {ag}"
    )
    if not rows:
        raise GuardrailError(f"Ad group {ag} not found")
    listing = ", ".join(f"{k['text']} [{k['match_type'].lower()}]" for k in kws)
    summary = (
        f"Add {len(kws)} keyword(s) to ad group '{rows[0]['ad_group']['name']}' "
        f"({rows[0]['campaign']['name']}): {listing}"
    )
    return summary, None, {"added": kws}, {"ad_group_id": ag, "keywords": kws}


def _pre_budget(p: dict):
    cid = _digits(p["campaign_id"], "campaign_id")
    new_amount = round(float(p["daily_budget"]), 2)
    if new_amount <= 0:
        raise GuardrailError("daily_budget must be positive")
    if new_amount > config.MAX_DAILY_BUDGET:
        raise GuardrailError(
            f"{new_amount:.2f}/day exceeds the hard cap of {config.MAX_DAILY_BUDGET:.2f}/day "
            "(MAX_DAILY_BUDGET). Raise the cap in Railway if this is intended."
        )
    rows = ads.search(
        "SELECT campaign.name, campaign_budget.resource_name, campaign_budget.amount_micros, "
        f"campaign_budget.explicitly_shared FROM campaign WHERE campaign.id = {cid}"
    )
    if not rows:
        raise GuardrailError(f"Campaign {cid} not found")
    r = rows[0]
    if r["campaign_budget"].get("explicitly_shared"):
        raise GuardrailError("This campaign uses a shared budget; change it in the UI instead")
    old = ads.micros(r["campaign_budget"].get("amount_micros"))
    pct = round((new_amount - old) / old * 100, 1) if old else None
    summary = f"Change daily budget on '{r['campaign']['name']}' from {old:.2f} to {new_amount:.2f}" + (
        f" ({pct:+.1f}%)" if pct is not None else ""
    )
    return (
        summary,
        {"daily_budget": old},
        {"daily_budget": new_amount, "change_pct": pct},
        {"campaign_id": cid, "daily_budget": new_amount, "budget_resource": r["campaign_budget"]["resource_name"]},
    )


def _pre_status(p: dict):
    level = str(p["level"]).lower()
    status = str(p["status"]).upper()
    if level not in LEVELS:
        raise GuardrailError(f"level must be one of {sorted(LEVELS)}")
    if status not in STATUSES:
        raise GuardrailError("status must be ENABLED or PAUSED (removal is not supported)")
    if level == "campaign":
        cid = _digits(p["id"], "id")
        rows = ads.search(f"SELECT campaign.name, campaign.status FROM campaign WHERE campaign.id = {cid}")
        label, current = (f"campaign '{rows[0]['campaign']['name']}'", rows[0]["campaign"]["status"]) if rows else (None, None)
        norm = {"level": level, "id": cid, "status": status}
    elif level == "ad_group":
        ag = _digits(p["id"], "id")
        rows = ads.search(f"SELECT ad_group.name, ad_group.status FROM ad_group WHERE ad_group.id = {ag}")
        label, current = (f"ad group '{rows[0]['ad_group']['name']}'", rows[0]["ad_group"]["status"]) if rows else (None, None)
        norm = {"level": level, "id": ag, "status": status}
    elif level == "ad":
        ag, ad = _composite(p["id"], "id")
        rows = ads.search(
            "SELECT ad_group.name, ad_group_ad.status FROM ad_group_ad "
            f"WHERE ad_group.id = {ag} AND ad_group_ad.ad.id = {ad}"
        )
        label, current = (f"ad {ad} in {rows[0]['ad_group']['name']}", rows[0]["ad_group_ad"]["status"]) if rows else (None, None)
        norm = {"level": level, "id": f"{ag}~{ad}", "status": status}
    else:
        ag, crit = _composite(p["id"], "id")
        rows = ads.search(
            "SELECT ad_group_criterion.keyword.text, ad_group_criterion.status FROM keyword_view "
            f"WHERE ad_group.id = {ag} AND ad_group_criterion.criterion_id = {crit}"
        )
        label, current = (
            (f"keyword '{rows[0]['ad_group_criterion']['keyword']['text']}'", rows[0]["ad_group_criterion"]["status"])
            if rows
            else (None, None)
        )
        norm = {"level": level, "id": f"{ag}~{crit}", "status": status}
    if label is None:
        raise GuardrailError(f"{level} {p['id']} not found")
    if current == status:
        raise GuardrailError(f"{label} is already {status}")
    summary = f"Set {label} from {current} to {status}"
    return summary, {"status": current}, {"status": status}, norm


# --------------------------------------------------------------------------- executors


def _exec_negatives(p: dict, validate_only: bool):
    ga = ads.client()
    campaign = ga.get_service("CampaignService").campaign_path(ads.customer_id(), p["campaign_id"])
    ops = []
    for kw in p["keywords"]:
        op = ga.get_type("CampaignCriterionOperation")
        c = op.create
        c.campaign = campaign
        c.negative = True
        c.keyword.text = kw["text"]
        c.keyword.match_type = ga.enums.KeywordMatchTypeEnum[kw["match_type"]]
        ops.append(op)
    return _mutate("CampaignCriterionService", "mutate_campaign_criteria", "MutateCampaignCriteriaRequest", ops, validate_only)


def _exec_keywords(p: dict, validate_only: bool):
    ga = ads.client()
    ad_group = ga.get_service("AdGroupService").ad_group_path(ads.customer_id(), p["ad_group_id"])
    ops = []
    for kw in p["keywords"]:
        op = ga.get_type("AdGroupCriterionOperation")
        c = op.create
        c.ad_group = ad_group
        c.status = ga.enums.AdGroupCriterionStatusEnum.ENABLED
        c.keyword.text = kw["text"]
        c.keyword.match_type = ga.enums.KeywordMatchTypeEnum[kw["match_type"]]
        ops.append(op)
    return _mutate("AdGroupCriterionService", "mutate_ad_group_criteria", "MutateAdGroupCriteriaRequest", ops, validate_only)


def _exec_budget(p: dict, validate_only: bool):
    ga = ads.client()
    op = ga.get_type("CampaignBudgetOperation")
    b = op.update
    b.resource_name = p["budget_resource"]
    b.amount_micros = int(round(p["daily_budget"] * 1_000_000))
    ga.copy_from(op.update_mask, protobuf_helpers.field_mask(None, b._pb))
    return _mutate("CampaignBudgetService", "mutate_campaign_budgets", "MutateCampaignBudgetsRequest", [op], validate_only)


def _exec_status(p: dict, validate_only: bool):
    ga = ads.client()
    cid = ads.customer_id()
    level, status = p["level"], p["status"]
    if level == "campaign":
        op = ga.get_type("CampaignOperation")
        op.update.resource_name = ga.get_service("CampaignService").campaign_path(cid, p["id"])
        op.update.status = ga.enums.CampaignStatusEnum[status]
        args = ("CampaignService", "mutate_campaigns", "MutateCampaignsRequest")
    elif level == "ad_group":
        op = ga.get_type("AdGroupOperation")
        op.update.resource_name = ga.get_service("AdGroupService").ad_group_path(cid, p["id"])
        op.update.status = ga.enums.AdGroupStatusEnum[status]
        args = ("AdGroupService", "mutate_ad_groups", "MutateAdGroupsRequest")
    elif level == "ad":
        ag, ad = p["id"].split("~")
        op = ga.get_type("AdGroupAdOperation")
        op.update.resource_name = ga.get_service("AdGroupAdService").ad_group_ad_path(cid, ag, ad)
        op.update.status = ga.enums.AdGroupAdStatusEnum[status]
        args = ("AdGroupAdService", "mutate_ad_group_ads", "MutateAdGroupAdsRequest")
    else:
        ag, crit = p["id"].split("~")
        op = ga.get_type("AdGroupCriterionOperation")
        op.update.resource_name = ga.get_service("AdGroupCriterionService").ad_group_criterion_path(cid, ag, crit)
        op.update.status = ga.enums.AdGroupCriterionStatusEnum[status]
        args = ("AdGroupCriterionService", "mutate_ad_group_criteria", "MutateAdGroupCriteriaRequest")
    ga.copy_from(op.update_mask, protobuf_helpers.field_mask(None, op.update._pb))
    return _mutate(*args, [op], validate_only)


KINDS: dict[str, tuple[Callable, Callable]] = {
    "add_negative_keywords": (_pre_negatives, _exec_negatives),
    "add_keywords": (_pre_keywords, _exec_keywords),
    "set_campaign_budget": (_pre_budget, _exec_budget),
    "set_status": (_pre_status, _exec_status),
}


def _needs_approval(kind: str, params: dict, after: Any) -> str | None:
    """Return the reason approval is required, or None if it can auto-apply."""
    if config.WRITE_MODE != "auto":
        return "WRITE_MODE is 'propose'"
    if kind == "set_campaign_budget":
        pct = (after or {}).get("change_pct")
        if pct is None or abs(pct) > config.MAX_AUTO_BUDGET_CHANGE_PCT:
            return f"budget move exceeds {config.MAX_AUTO_BUDGET_CHANGE_PCT:g}% auto limit"
    if kind == "set_status" and params["level"] == "campaign" and not config.AUTO_CAMPAIGN_STATUS:
        return "campaign-level status changes need approval"
    if kind == "add_keywords":
        return "new keywords add spend exposure"
    return None


# --------------------------------------------------------------------------- public API


def submit(kind: str, params: dict, reason: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise GuardrailError(f"Unknown change kind {kind}")
    if not reason or len(reason.strip()) < 10:
        raise GuardrailError("Give a real reason (it goes in the change log)")
    pre, execute = KINDS[kind]
    summary, before, after, norm = pre(params)
    execute(norm, True)  # Google-side validation; raises on failure

    why = _needs_approval(kind, norm, after)
    if why:
        change_id = store.record(kind, norm, summary, reason, before, after, "pending")
        return {"status": "pending_approval", "change_id": change_id, "summary": summary, "why_queued": why}

    try:
        resources = execute(norm, False)
    except Exception as exc:  # noqa: BLE001
        change_id = store.record(kind, norm, summary, reason, before, after, "failed", {"error": str(exc)})
        return {"status": "failed", "change_id": change_id, "error": str(exc)}
    change_id = store.record(kind, norm, summary, reason, before, after, "applied", {"resources": resources})
    return {"status": "applied", "change_id": change_id, "summary": summary}


def approve(change_id: int) -> dict[str, Any]:
    change = store.get(change_id)
    if not change:
        raise GuardrailError(f"No change {change_id}")
    if change["status"] != "pending":
        raise GuardrailError(f"Change {change_id} is {change['status']}, not pending")
    pre, execute = KINDS[change["kind"]]
    # Re-read live state: the account may have moved since this was proposed.
    summary, _before, _after, norm = pre(change["params"])
    try:
        execute(norm, True)
        resources = execute(norm, False)
    except Exception as exc:  # noqa: BLE001
        store.set_status(change_id, "failed", {"error": str(exc)})
        return {"status": "failed", "change_id": change_id, "error": str(exc)}
    store.set_status(change_id, "applied", {"resources": resources})
    return {"status": "applied", "change_id": change_id, "summary": summary}


def reject(change_id: int, note: str = "") -> dict[str, Any]:
    change = store.get(change_id)
    if not change or change["status"] != "pending":
        raise GuardrailError(f"Change {change_id} is not pending")
    store.set_status(change_id, "rejected", {"note": note})
    return {"status": "rejected", "change_id": change_id}
