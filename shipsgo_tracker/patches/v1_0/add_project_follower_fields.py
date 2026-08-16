# Copyright (c) 2026, karuppasamy and contributors
"""Add the Followers table to Project and the Is Shipping Contact flag to Contact."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Project": [
                {
                    "fieldname": "custom_followers_section",
                    "fieldtype": "Section Break",
                    "label": "ShipsGo Followers",
                    "insert_after": "custom_shipsgo_last_synced",
                    "module": "Shipsgo Tracker",
                },
                {
                    "fieldname": "custom_followers",
                    "fieldtype": "Table",
                    "label": "Followers",
                    "options": "Project Follower",
                    "description": "Customer contacts receiving ShipsGo tracking updates for this shipment.",
                    "insert_after": "custom_followers_section",
                    "module": "Shipsgo Tracker",
                },
            ],
            "Contact": [
                {
                    "fieldname": "is_shipping_contact",
                    "fieldtype": "Check",
                    "label": "Is Shipping Contact",
                    "description": "Pre-selects this contact for shipment tracking updates.",
                    "insert_after": "is_billing_contact",
                    "module": "Shipsgo Tracker",
                },
            ],
        },
        ignore_validate=True,
    )
