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
