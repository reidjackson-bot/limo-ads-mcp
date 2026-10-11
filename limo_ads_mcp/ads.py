"""Google Ads client and GAQL helpers."""

from functools import lru_cache
from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf.json_format import MessageToDict

from . import config


@lru_cache(maxsize=1)
def client() -> GoogleAdsClient:
    settings = {
        "developer_token": config.GOOGLE_ADS_DEVELOPER_TOKEN,
        "client_id": config.GOOGLE_ADS_CLIENT_ID,
        "client_secret": config.GOOGLE_ADS_CLIENT_SECRET,
        "refresh_token": config.GOOGLE_ADS_REFRESH_TOKEN,
        "use_proto_plus": True,
    }
    missing = [k for k, v in settings.items() if v in (None, "")]
    if missing:
        raise RuntimeError(f"Google Ads credentials missing: {', '.join(missing)}")
    if config.GOOGLE_ADS_LOGIN_CUSTOMER_ID:
        settings["login_customer_id"] = config.GOOGLE_ADS_LOGIN_CUSTOMER_ID.replace("-", "")
    return GoogleAdsClient.load_from_dict(settings)


def customer_id() -> str:
    if not config.GOOGLE_ADS_CUSTOMER_ID:
        raise RuntimeError("GOOGLE_ADS_CUSTOMER_ID is not set")
    return config.GOOGLE_ADS_CUSTOMER_ID


def format_error(exc: GoogleAdsException) -> str:
    parts = [f"Google Ads request {exc.request_id} failed:"]
    for err in exc.failure.errors:
        field = ""
        if err.location and err.location.field_path_elements:
            field = " at " + ".".join(e.field_name for e in err.location.field_path_elements)
        parts.append(f"- {err.message}{field}")
    return "\n".join(parts)


def search(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Run GAQL against the locked account and return rows as plain dicts."""
    svc = client().get_service("GoogleAdsService")
    rows: list[dict[str, Any]] = []
    try:
        stream = svc.search_stream(customer_id=customer_id(), query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(
                    MessageToDict(row._pb, preserving_proto_field_name=True)
                )
                if limit and len(rows) >= limit:
                    return rows
    except GoogleAdsException as exc:
        raise RuntimeError(format_error(exc)) from exc
    return rows


def micros(value: Any) -> float:
    """Convert a micros field (string or int) to currency units."""
    try:
        return round(int(value) / 1_000_000, 2)
    except (TypeError, ValueError):
        return 0.0


def date_clause(start_date: str | None, end_date: str | None, days: int) -> str:
    """GAQL date filter: explicit range when given, else a rolling window ending yesterday."""
    import re
    from datetime import date, timedelta

    if start_date and end_date:
        for d in (start_date, end_date):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                raise ValueError(f"Dates must be YYYY-MM-DD, got {d!r}")
        return f"segments.date BETWEEN '{start_date}' AND '{end_date}'"

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
