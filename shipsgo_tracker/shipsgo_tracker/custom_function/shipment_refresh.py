# Copyright (c) 2026, karuppasamy and contributors
import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import get_system_timezone, now_datetime
from requests.exceptions import ConnectionError, Timeout

from shipsgo_tracker.shipsgo_tracker.custom_function.project_doc_custom_function import (
    get_access_token,
)

OCEAN_LIST_PATH = "/ocean/shipments"
PAGE_LIMIT = 50  # safety cap on pagination loops
PAGE_SIZE = 100  # max take allowed by the API


def _to_system_datetime(value):
    """Convert a ShipsGo ISO-8601 timestamp (often offset-aware) to a naive
    datetime in the site's system timezone, for a Frappe Datetime field."""
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt
    system_tz = ZoneInfo(get_system_timezone())
    return dt.astimezone(system_tz).replace(tzinfo=None)


def map_shipment_fields(shipment):
    """Map a ShipsGo shipment object to Project field updates.

    Always returns live status + last_synced. ETA/ETD are included only when the
    shipment has a `route` (absent for NEW/INPROGRESS/UNTRACKED)."""
    updates = {
        "custom_shipsgo_live_status": shipment.get("status"),
        "custom_shipsgo_last_synced": now_datetime(),
    }
    route = shipment.get("route") or None
    if route:
        pol = route.get("port_of_loading") or {}
        pod = route.get("port_of_discharge") or {}
        etd = _to_system_datetime(pol.get("date_of_loading"))
        eta = _to_system_datetime(pod.get("date_of_discharge"))
        if etd:
            updates["custom_shipsgo_etd"] = etd
        if eta:
            updates["custom_shipsgo_eta"] = eta
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
        except (Timeout, ConnectionError):
            return shipments, False
        if resp.status_code == 429:
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
    return shipments, True


@frappe.whitelist()
def refresh_active_shipments():
    """Scheduled sync: read all shipments via the default token, match to Projects
    by shipment id, write ETA/ETD/live-status. Advances the sync window only on a
    fully successful pass. Skips silently if the integration is disabled or no
    default token is configured (so a scheduled run never crashes)."""
    try:
        token, base_url = get_access_token(use_default=True)
    except frappe.ValidationError:
        return {"updated": 0, "ok": True}

    projects = frappe.get_all(
        "Project",
        filters={"custom_shipsgo_shipment_id": ["is", "set"]},
        fields=["name", "custom_shipsgo_shipment_id"],
    )
    id_to_project = {str(p.custom_shipsgo_shipment_id): p.name for p in projects}
    if not id_to_project:
        return {"updated": 0, "ok": True}

    setting = frappe.get_single("ShipsGo Setting")
    # First run (last_shipment_sync_at is None) => full back-fill of ALL shipments.
    # Subsequent runs are windowed to only what changed since the last successful sync.
    updated_since = setting.last_shipment_sync_at
    run_started = now_datetime()

    shipments, ok = _fetch_shipments(base_url, token, updated_since)

    updated = 0
    for shipment in shipments:
        project = id_to_project.get(str(shipment.get("id")))
        if not project:
            continue
        for field, value in map_shipment_fields(shipment).items():
            frappe.db.set_value("Project", project, field, value, update_modified=False)
        updated += 1

    # Advance the window only on a clean pass, so a failed/partial run keeps the
    # backlog for the next run.
    if ok:
        frappe.db.set_single_value("ShipsGo Setting", "last_shipment_sync_at", run_started)
    frappe.db.commit()
    return {"updated": updated, "ok": ok}


@frappe.whitelist()
def refresh_single_shipment(docname):
    """Manual 'Refresh Tracking' button. Reads one shipment via the default token
    using filters[id]. Uses the default token (not the session user's) because the
    default Administrator token has org-wide read visibility and most users have no
    token of their own — see spec section 7.5."""
    # get_access_token(use_default=True) throws a clear message if disabled / no default token.
    token, base_url = get_access_token(use_default=True)

    shipment_id = frappe.db.get_value("Project", docname, "custom_shipsgo_shipment_id")
    if not shipment_id:
        frappe.throw(_("No ShipsGo shipment is linked to this project."))

    headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}
    params = {"filters[id]": f"eq:{shipment_id}", "take": 1}
    try:
        resp = requests.get(f"{base_url}{OCEAN_LIST_PATH}", headers=headers, params=params, timeout=30)
    except (Timeout, ConnectionError):
        return {"status": "retryable", "error": "Network error contacting ShipsGo."}
    if resp.status_code == 429:
        return {"status": "retryable", "error": "Too many requests. Please try again shortly."}
    if resp.status_code != 200:
        frappe.log_error(title=f"ShipsGo single HTTP {resp.status_code}", message=resp.text)
        return {"status": "retryable", "error": "ShipsGo returned an unexpected response."}
    payload = resp.json()
    shipments = payload.get("shipments") or []
    if payload.get("message") != "SUCCESS" or not shipments:
        return {"status": "not_found", "error": "Shipment not visible to the ShipsGo token."}

    shipment = shipments[0]
    for field, value in map_shipment_fields(shipment).items():
        frappe.db.set_value("Project", docname, field, value, update_modified=False)
    frappe.db.commit()
    return {"status": "success", "live_status": shipment.get("status")}
