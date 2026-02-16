# Copyright (c) 2026, karuppasamy and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ShipsGoUserAccessTokens(Document):
	def validate(self):
		if self.is_default:
			existing = frappe.db.get_value(
				"ShipsGo User Access Tokens",
				{"is_default": 1, "name": ("!=", self.name)},
				"name",
			)
			if existing:
				frappe.db.set_value("ShipsGo User Access Tokens", existing, "is_default", 0)
				frappe.msgprint(
					_("Default flag removed from {0}. Only one default token is allowed.").format(existing),
					alert=True,
				)
