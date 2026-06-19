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
