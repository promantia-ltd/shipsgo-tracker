# Copyright (c) 2026, karuppasamy and contributors
"""ShipsGo customer followers — register/remove customer Contacts on a shipment.

Intent lives on the Project Follower row (`enabled`); ShipsGo's actual state lives
in `status`. The reconciler closes the gap between them and is safe to re-run.
"""

import frappe
import requests
from requests.exceptions import ConnectionError, Timeout

FOLLOWERS_PATH = "/ocean/shipments/{shipment_id}/followers"
TIMEOUT = 30


@frappe.whitelist()
def default_contact_for_customer(customer):
    """Contact to pre-fill on a new follower row, or None when ambiguous or absent.

    Exactly one flagged shipping contact wins. Two or more is deliberately None —
    silently picking one of several recipients for a customer-facing email is worse
    than asking the specialist to choose.
    """
    if not customer:
        return None

    linked = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
        pluck="parent",
    )
    if not linked:
        return None

    shipping = frappe.get_all(
        "Contact", filters={"name": ("in", linked), "is_shipping_contact": 1}, pluck="name"
    )
    if len(shipping) == 1:
        return shipping[0]
    if shipping:
        return None

    primary = frappe.db.get_value("Customer", customer, "customer_primary_contact")
    if primary and primary in linked and frappe.db.get_value("Contact", primary, "email_id"):
        return primary
    return None


def _add_follower(base_url, token, shipment_id, email):
    """POST a follower. Returns (follower_id, registered_email, error).

    200 and 409 are the same success branch: both carry the follower object with the
    id needed for later removal, so a retry after a timeout recovers rather than
    stranding the follower.
    """
    url = f"{base_url}{FOLLOWERS_PATH.format(shipment_id=shipment_id)}"
    headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json={"follower": email}, headers=headers, timeout=TIMEOUT)
    except (Timeout, ConnectionError) as exc:
        return None, None, f"Network error: {exc}"

    if resp.status_code in (200, 409):
        follower = (resp.json() or {}).get("follower") or {}
        if not follower.get("id"):
            return None, None, f"HTTP {resp.status_code} carried no follower id"
        return follower["id"], follower.get("email") or email, None

    if resp.status_code == 403:
        return None, None, "ShipsGo token is not permitted to add followers (HTTP 403)"
    return None, None, f"HTTP {resp.status_code}: {resp.text[:200]}"


def _remove_follower(base_url, token, shipment_id, follower_id):
    """DELETE a follower. Returns an error string, or None on success."""
    path = FOLLOWERS_PATH.format(shipment_id=shipment_id)
    url = f"{base_url}{path}/{follower_id}"
    try:
        resp = requests.delete(url, headers={"X-Shipsgo-User-Token": token}, timeout=TIMEOUT)
    except (Timeout, ConnectionError) as exc:
        return f"Network error: {exc}"

    if resp.status_code == 200:
        return None
    if resp.status_code == 403:
        return "ShipsGo token is not permitted to remove followers (HTTP 403)"
    return f"HTTP {resp.status_code}: {resp.text[:200]}"
