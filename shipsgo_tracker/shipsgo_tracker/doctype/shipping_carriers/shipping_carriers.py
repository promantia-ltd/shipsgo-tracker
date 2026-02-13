# Copyright (c) 2026, karuppasamy and contributors
# For license information, please see license.txt
import frappe
import requests
from frappe.model.document import Document

from shipsgo_tracker.shipsgo_tracker.custom_function.project_doc_custom_function import get_access_token


class ShippingCarriers(Document):
	pass


@frappe.whitelist()
def fetch_carrier_list():
	try:
		token, base_url = get_access_token(cron=True)
		url = f"{base_url}/ocean/carriers"

		if not token:
			frappe.log_error(title="ShipsGo Token Missing", message="ShipsGo token not configured")
			return

		headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}

		response = requests.get(url, headers=headers, timeout=30)

		if response.status_code == 200:
			data = response.json()

			if data.get("message") == "SUCCESS":
				carriers = data.get("carriers", [])

				for carrier in carriers:
					scac = carrier.get("scac")
					name = carrier.get("name")
					status = carrier.get("status")

					if not scac:
						continue

					if not frappe.db.exists("Shipping Carriers", {"scac_code": scac}):
						doc = frappe.new_doc("Shipping Carriers")
						doc.carrier_name = name
						doc.scac_code = scac
						doc.status = "Active" if status == "ACTIVE" else "Inactive"

						doc.insert(ignore_permissions=True)
						frappe.db.commit()

			else:
				frappe.log_error(title="ShipsGo Logical Failure", message=frappe.as_json(data))

		else:
			frappe.log_error(title=f"ShipsGo HTTP Error {response.status_code}", message=response.text)

	except requests.exceptions.Timeout:
		frappe.log_error(title="ShipsGo Timeout", message="API request timed out")

	except requests.exceptions.ConnectionError:
		frappe.log_error(title="ShipsGo Connection Error", message="Unable to connect to ShipsGo")

	except Exception:
		frappe.log_error(title="ShipsGo Unknown Error", message=frappe.get_traceback())
