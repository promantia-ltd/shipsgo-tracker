# Copyright (c) 2026, karuppasamy and contributors
"""ShipsGo customer followers — register/remove customer Contacts on a shipment.

Intent lives on the Project Follower row (`enabled`); ShipsGo's actual state lives
in `status`. The reconciler closes the gap between them and is safe to re-run.
"""

import frappe
import requests
from requests.exceptions import ConnectionError, Timeout

from shipsgo_tracker.shipsgo_tracker.custom_function.project_doc_custom_function import (
    get_access_token,
)

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

    # Whitelisted: confirm the caller may read this Customer before returning one of
    # its contacts.
    frappe.has_permission("Customer", doc=customer, throw=True)

    linked = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
        pluck="parent",
    )
    if not linked:
        return None

    shipping = frappe.get_all(
        "Contact", filters={"name": ("in", linked), "custom_is_shipping_contact": 1}, pluck="name"
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
        try:
            payload = resp.json() or {}
        except ValueError:
            # A proxy or gateway can return HTML with a 200. Report it on the row
            # rather than letting it escape as an exception.
            return None, None, f"HTTP {resp.status_code} with a non-JSON body"
        follower = payload.get("follower") or {}
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


def _set(row_name, **values):
    """Write read-only row fields without touching the parent's modified stamp."""
    frappe.db.set_value("Project Follower", row_name, values, update_modified=False)


def _needs_sync(row):
    """True when intent and ShipsGo state disagree."""
    return (row.enabled and row.status != "Active") or (not row.enabled and row.status == "Active")


def _process_row(row, shipment_id, base_url, token):
    """Move one row from its current state toward its intent."""
    if row.enabled:
        email = frappe.db.get_value("Contact", row.contact, "email_id") if row.contact else None
        if not email:
            _set(row.name, status="Failed", last_error="Contact has no email address")
            return

        follower_id, registered, error = _add_follower(base_url, token, shipment_id, email)
        if error:
            _set(row.name, status="Failed", last_error=error)
            return
        _set(
            row.name,
            status="Active",
            shipsgo_follower_id=follower_id,
            email=registered,
            last_error="",
        )
        return

    if not row.shipsgo_follower_id:
        _set(row.name, status="Failed", last_error="No ShipsGo follower id stored; cannot remove")
        return

    error = _remove_follower(base_url, token, shipment_id, row.shipsgo_follower_id)
    if error:
        # Deliberately stays Active: intent and reality diverge, and that must be visible.
        _set(row.name, last_error=error)
        return
    _set(row.name, status="Removed", shipsgo_follower_id=0, last_error="")


def sync_project_followers(project_name):
    """Reconcile one Project's follower rows against ShipsGo. Safe to re-run."""
    rows = frappe.get_all(
        "Project Follower",
        filters={"parent": project_name, "parenttype": "Project"},
        fields=["name", "enabled", "contact", "email", "shipsgo_follower_id", "status"],
    )
    pending = [row for row in rows if _needs_sync(row)]
    if not pending:
        return {"processed": 0}

    shipment_id = frappe.db.get_value("Project", project_name, "custom_shipsgo_shipment_id")
    if not shipment_id:
        # Normal state, not an error: the shipment may not have been created yet.
        return {"processed": 0}

    try:
        token, base_url = get_access_token(use_default=True)
    except Exception as exc:
        frappe.log_error(title="ShipsGo followers: token unavailable", message=str(exc))
        return {"processed": 0}

    for row in pending:
        try:
            _process_row(row, shipment_id, base_url, token)
        except Exception:
            frappe.log_error(
                title=f"ShipsGo follower sync failed: {project_name}",
                message=frappe.get_traceback(),
            )
    return {"processed": len(pending)}


def validate_follower_rows(doc, method=None):
    """Block deleting a follower row that is still registered at ShipsGo.

    Deleting the row destroys the follower id, and with it the only way to stop the
    customer receiving ShipsGo emails. Unticking first performs the removal and
    leaves the row safe to delete.
    """
    before = doc.get_doc_before_save()
    if not before:
        return

    surviving = {row.name for row in (doc.get("custom_followers") or [])}
    for old in before.get("custom_followers") or []:
        if old.name in surviving:
            continue
        if old.status == "Active" and old.shipsgo_follower_id:
            frappe.throw(
                frappe._(
                    "{0} is still receiving ShipsGo updates. Untick <b>Send Updates</b> "
                    "and save to stop them, then delete the row."
                ).format(frappe.bold(old.contact or frappe._("This contact"))),
                title=frappe._("Follower still registered"),
            )


def sync_on_project_update(doc, method=None):
    """on_update hook. Must never prevent the Project from saving."""
    try:
        if sync_project_followers(doc.name).get("processed"):
            # Rows are written with db_set, which does not touch the in-memory doc.
            # Without this the browser would render the pre-sync row state and the
            # specialist would see "Pending" on a follower that actually registered.
            doc.reload()
    except Exception:
        frappe.log_error(
            title=f"ShipsGo follower sync failed on save: {doc.name}",
            message=frappe.get_traceback(),
        )


def sync_all_project_followers():
    """Daily catch-up: registers rows whose shipment now exists, retries failures.

    Needed because `create_shipment` writes the shipment id with `db_set`, which does
    not fire `on_update` — so a follower enabled before the shipment existed would
    otherwise never register.
    """
    projects = frappe.db.sql_list(
        """
        select distinct parent from `tabProject Follower`
        where parenttype = 'Project'
          and ((enabled = 1 and status != 'Active') or (enabled = 0 and status = 'Active'))
        """
    )
    for name in projects:
        try:
            sync_project_followers(name)
        except Exception:
            frappe.log_error(
                title=f"ShipsGo follower daily sync failed: {name}",
                message=frappe.get_traceback(),
            )
        frappe.db.commit()
    return {"projects": len(projects)}
