# Copyright (c) 2026, karuppasamy and contributors
"""Rename Contact.is_shipping_contact to custom_is_shipping_contact.

The original field was named to sit alongside the stock is_primary_contact and
is_billing_contact. Custom fields on standard doctypes should carry the custom_
prefix so they cannot collide with a field Frappe adds later.

Self-contained: creates the new field itself rather than relying on
add_project_follower_fields, which will not re-run on sites that already applied it.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

OLD = "is_shipping_contact"
NEW = "custom_is_shipping_contact"


def execute():
    create_custom_fields(
        {
            "Contact": [
                {
                    "fieldname": NEW,
                    "fieldtype": "Check",
                    "label": "Is Shipping Contact",
                    "description": "Pre-selects this contact for shipment tracking updates.",
                    "insert_after": "is_billing_contact",
                    "module": "Shipsgo Tracker",
                }
            ]
        },
        ignore_validate=True,
    )

    old_field = frappe.db.exists("Custom Field", {"dt": "Contact", "fieldname": OLD})
    if not old_field:
        return

    if frappe.db.has_column("Contact", OLD) and frappe.db.has_column("Contact", NEW):
        frappe.db.sql(
            f"update `tabContact` set `{NEW}` = `{OLD}` where ifnull(`{OLD}`, 0) = 1"
        )

    frappe.delete_doc("Custom Field", old_field, ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="Contact")
