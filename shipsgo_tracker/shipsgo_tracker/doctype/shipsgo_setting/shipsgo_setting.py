# # Copyright (c) 2026, karuppasamy and contributors
# # For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ShipsGoSetting(Document):
	def onload(self):
		current_user = frappe.session.user
		user_roles = frappe.get_roles(current_user)

		privileged_roles = ["System Manager"]

		if any(role in user_roles for role in privileged_roles):
			for row in self.user_access_token:
				if row.access_token:
					row.access_token = row.get_password("access_token")

		else:
			filtered_rows = []

			for row in self.user_access_token:
				if row.user == current_user:
					row.access_token = row.get_password("access_token")
					filtered_rows.append(row)
				if row not in filtered_rows:
					filtered_rows.append(row)

			self.user_access_token = filtered_rows
