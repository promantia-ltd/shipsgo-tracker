# Copyright (c) 2026, karuppasamy and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ShipsGoUserAccessTokens(Document):
	def before_save(self):
		"""
		Ensure only one token is set as Default per user.
		If this token is marked as default, unset the previous default.
		"""
		if self.default:
			# Unset default for all other tokens of the same user
			frappe.db.sql(
				"""
                UPDATE `tabShipsGo User Access Tokens`
                SET `default` = 0
                WHERE name != %s
                """,
				(self.name),
			)
