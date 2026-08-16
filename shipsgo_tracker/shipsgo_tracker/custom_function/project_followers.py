# Copyright (c) 2026, karuppasamy and contributors
"""ShipsGo customer followers — register/remove customer Contacts on a shipment.

Intent lives on the Project Follower row (`enabled`); ShipsGo's actual state lives
in `status`. The reconciler closes the gap between them and is safe to re-run.
"""

import frappe


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
