# Copyright (c) 2026, karuppasamy and contributors
import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, get_system_timezone, now_datetime
from requests.exceptions import ConnectionError, Timeout

from shipsgo_tracker.shipsgo_tracker.custom_function.project_doc_custom_function import (
    get_access_token,
)

OCEAN_LIST_PATH = "/ocean/shipments"
PAGE_LIMIT = 50  # safety cap on pagination loops
PAGE_SIZE = 100  # max take allowed by the API

DEFAULT_MAP_DEEPLINK = "https://map.shipsgo.com/ocean/shipments"


def _resolve_map_base():
    """Map deep-link base from ShipsGo Setting, falling back to the constant when blank."""
    setting = frappe.get_single("ShipsGo Setting")
    map_base = (setting.get("tracking_map_base_url") or "").strip() or DEFAULT_MAP_DEEPLINK
    return map_base.rstrip("/")


def _build_tracking_url(shipment_id, shipment, map_base):
    """ShipsGo map deep-link when a map token is present; else None (Option B — no
    public container-search fallback). tokens.map is DETAILS-only."""
    map_token = (shipment.get("tokens") or {}).get("map")
    if map_token:
        return f"{map_base}/{shipment_id}?token={map_token}"
    return None


def _to_utc_filter_value(value, margin_hours=1):
    """Convert a naive system-timezone datetime (e.g. last_shipment_sync_at) to a
    UTC 'YYYY-MM-DD HH:MM:SS' string for ShipsGo's filters[updated_at] (which is UTC),
    minus an overlap margin so a transition landing on the boundary is never missed."""
    if not value:
        return None
    dt = get_datetime(value) - datetime.timedelta(hours=margin_hours)
    system_tz = ZoneInfo(get_system_timezone())
    dt_utc = dt.replace(tzinfo=system_tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S")


def _to_utc_string(value):
    """Convert a ShipsGo offset-aware ISO-8601 timestamp (port-local) to a
    'DD-MM-YYYY HH:MM UTC' display string. Naive input is assumed already UTC."""
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%d-%m-%Y %H:%M") + " UTC"


def map_shipment_fields(shipment):
    """Map a ShipsGo shipment to Project field updates. Always returns live status
    + last_synced. The five UTC date strings are included only when the shipment has
    a `route` (absent for NEW/INPROGRESS/UNTRACKED) — never overwrite with None.

    ETA (primary) prefers ShipsGo's ML prediction (`date_of_discharge_predicted`)
    and falls back to the carrier's scheduled `date_of_discharge`; the carrier-current
    and carrier-original variants are stored alongside in their own fields."""
    updates = {"custom_shipsgo_last_synced": now_datetime()}
    status = shipment.get("status")
    if status:
        updates["custom_shipsgo_live_status"] = status
    route = shipment.get("route") or None
    if route:
        pol = route.get("port_of_loading") or {}
        pod = route.get("port_of_discharge") or {}
        values = {
            "custom_shipsgo_etd": _to_utc_string(pol.get("date_of_loading")),
            "custom_shipsgo_etd_initial": _to_utc_string(pol.get("date_of_loading_initial")),
            "custom_shipsgo_eta": _to_utc_string(
                pod.get("date_of_discharge_predicted") or pod.get("date_of_discharge")
            ),
            "custom_shipsgo_eta_carrier": _to_utc_string(pod.get("date_of_discharge")),
            "custom_shipsgo_eta_initial": _to_utc_string(pod.get("date_of_discharge_initial")),
        }
        for field, val in values.items():
            if val:
                updates[field] = val
    return updates


def _fetch_shipments(base_url, token, updated_since):
    """Return (shipments, ok). ok=False means a recoverable error occurred
    (429 / network / non-200 / non-SUCCESS), so the caller should not advance
    the sync window. Omits the updated_at filter on the first run (updated_since
    is falsy) to fully back-fill existing shipments."""
    headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}
    shipments = []
    skip = 0
    for _page in range(PAGE_LIMIT):
        params = {"take": PAGE_SIZE, "skip": skip, "order_by": "updated_at,desc"}
        if updated_since:
            params["filters[updated_at]"] = f"gte:{updated_since}"
        try:
            resp = requests.get(f"{base_url}{OCEAN_LIST_PATH}", headers=headers, params=params, timeout=30)
        except (Timeout, ConnectionError) as exc:
            frappe.log_error(title="ShipsGo list network error", message=str(exc))
            return shipments, False
        if resp.status_code == 429:
            frappe.log_error(title="ShipsGo list HTTP 429", message="Rate limited by ShipsGo.")
            return shipments, False
        if resp.status_code != 200:
            frappe.log_error(title=f"ShipsGo list HTTP {resp.status_code}", message=resp.text)
            return shipments, False
        data = resp.json()
        if data.get("message") != "SUCCESS":
            frappe.log_error(title="ShipsGo list logical failure", message=frappe.as_json(data))
            return shipments, False
        batch = data.get("shipments", [])
        shipments.extend(batch)
        meta = data.get("meta") or {}
        if not meta.get("more") or len(batch) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    else:
        frappe.log_error(
            title="ShipsGo pagination hit PAGE_LIMIT",
            message=f"Stopped after {PAGE_LIMIT} pages; results may be truncated.",
        )
    return shipments, True


@frappe.whitelist()
def refresh_active_shipments():
    """Scheduled sync: read all shipments via the default token (bulk LIST), match to
    Projects by shipment id, and write the five UTC ETA/ETD strings, live status, and the
    resolved container number. The tracking URL (token map deep-link) is DETAILS-only and
    is NOT written here — it is captured on Project form open / via the Refresh button.
    Advances the sync window only on a fully successful pass; logs failures to Error Log."""
    try:
        token, base_url = get_access_token(use_default=True)
    except frappe.ValidationError:
        return {"updated": 0, "ok": True}

    projects = frappe.get_all(
        "Project",
        filters={"custom_shipsgo_shipment_id": ["is", "set"]},
        fields=["name", "custom_shipsgo_shipment_id"],
    )
    by_id = {str(p.custom_shipsgo_shipment_id): p for p in projects}
    if not by_id:
        return {"updated": 0, "ok": True}

    setting = frappe.get_single("ShipsGo Setting")
    # First run (last_shipment_sync_at is None) => full back-fill; later runs windowed.
    updated_since = _to_utc_filter_value(setting.last_shipment_sync_at)
    run_started = now_datetime()

    shipments, ok = _fetch_shipments(base_url, token, updated_since)

    updated = 0
    for shipment in shipments:
        project = by_id.get(str(shipment.get("id")))
        if not project:
            continue
        updates = map_shipment_fields(shipment)
        container = shipment.get("container_number")
        if container:
            updates["custom_shipsgo_container_number"] = container
        # Option B: the tracking URL (token map deep-link) is DETAILS-only, captured
        # on Project form open / via the Refresh button — never written by the scheduler.
        for field, value in updates.items():
            frappe.db.set_value("Project", project.name, field, value, update_modified=False)
        updated += 1

    if ok:
        frappe.db.set_single_value("ShipsGo Setting", "last_shipment_sync_at", run_started)
    else:
        frappe.log_error(
            title="ShipsGo scheduled sync completed with errors",
            message=f"updated={updated}; recoverable error during fetch; window not advanced.",
        )
    frappe.db.commit()
    return {"updated": updated, "ok": ok}


@frappe.whitelist()
def refresh_single_shipment(docname):
    """Manual 'Refresh Tracking' button + empty-state 'Open Tracking' loader.
    Reads ONE shipment via the DETAILS endpoint (default token) — which returns
    status + route + tokens.map in one call — and persists ETA/ETD/live-status,
    the resolved container number, and the tracking URL (map deep-link preferred)."""
    token, base_url = get_access_token(use_default=True)

    shipment_id = frappe.db.get_value("Project", docname, "custom_shipsgo_shipment_id")
    if not shipment_id:
        frappe.throw(_("No ShipsGo shipment is linked to this project."))

    shipment, status = _fetch_shipment_details(base_url, token, shipment_id)
    if status == "retryable":
        return {"status": "retryable", "error": "Could not reach ShipsGo. Please try again shortly."}
    if status != "success":
        return {"status": "not_found", "error": "Shipment not found on ShipsGo."}

    updates = map_shipment_fields(shipment)
    container = shipment.get("container_number")
    if container:
        updates["custom_shipsgo_container_number"] = container
    map_base = _resolve_map_base()
    url = _build_tracking_url(shipment_id, shipment, map_base)
    if url:
        updates["custom_shipsgo_tracking_url"] = url

    for field, value in updates.items():
        frappe.db.set_value("Project", docname, field, value, update_modified=False)
    frappe.db.commit()
    return {"status": "success", "live_status": shipment.get("status"), "tracking_url": url}


def _fetch_shipment_details(base_url, token, shipment_id):
    """GET /ocean/shipments/{id}. Returns (shipment|None, status) where status is
    'success' | 'not_found' | 'retryable'."""
    headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}
    try:
        resp = requests.get(f"{base_url}{OCEAN_LIST_PATH}/{shipment_id}", headers=headers, timeout=30)
    except (Timeout, ConnectionError):
        return None, "retryable"
    if resp.status_code == 429:
        return None, "retryable"
    if resp.status_code == 404:
        return None, "not_found"
    if resp.status_code != 200:
        frappe.log_error(title=f"ShipsGo details HTTP {resp.status_code}", message=resp.text)
        return None, "retryable"
    payload = resp.json()
    shipment = payload.get("shipment") or {}
    if payload.get("message") != "SUCCESS" or not shipment:
        return None, "not_found"
    return shipment, "success"
