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
	"""
	Fetches all carriers from ShipsGo API using skip/take pagination.

	API query params: skip (min:0, default:0), take (min:1, max:100, default:25),
	status (ACTIVE|PASSIVE), order_by (e.g. "name,asc").
	"""
	try:
		token, base_url = get_access_token(use_default=True)
		headers = {"X-Shipsgo-User-Token": token, "Content-Type": "application/json"}

		all_carriers = []
		skip = 0
		take = 100  # max allowed by API
		has_more = True
		max_pages = 50  # safety limit to prevent infinite loops

		page = 0
		while has_more and page < max_pages:
			url = f"{base_url}/ocean/carriers?skip={skip}&take={take}"
			response = requests.get(url, headers=headers, timeout=30)

			if response.status_code != 200:
				frappe.log_error(
					title=f"ShipsGo HTTP Error {response.status_code}",
					message=f"skip={skip}, take={take}: {response.text}",
				)
				break

			data = response.json()

			if data.get("message") != "SUCCESS":
				frappe.log_error(title="ShipsGo Logical Failure", message=frappe.as_json(data))
				break

			carriers = data.get("carriers", [])
			if not carriers:
				break

			all_carriers.extend(carriers)

			meta = data.get("meta", {})
			has_more = meta.get("more", False)

			# Secondary check: if fewer records than requested, this is the last page
			if len(carriers) < take:
				has_more = False

			frappe.logger().info(
				f"ShipsGo Carriers: skip={skip}, fetched={len(carriers)}, "
				f"total so far={len(all_carriers)}, more={has_more}"
			)

			skip += take
			page += 1

		# Process all fetched carriers
		created_count = 0
		updated_count = 0

		for carrier in all_carriers:
			scac = carrier.get("scac")
			name = carrier.get("name")
			status = carrier.get("status")

			if not scac:
				continue

			if frappe.db.exists("Shipping Carriers", {"scac_code": scac}):
				doc = frappe.get_doc("Shipping Carriers", scac)
				if doc.carrier_name != name or doc.status != status:
					doc.carrier_name = name
					doc.status = status
					doc.save(ignore_permissions=True)
					updated_count += 1
			else:
				doc = frappe.new_doc("Shipping Carriers")
				doc.carrier_name = name
				doc.scac_code = scac
				doc.status = status
				doc.insert(ignore_permissions=True)
				created_count += 1

		if all_carriers:
			frappe.db.commit()
			frappe.logger().info(
				f"ShipsGo Carriers Sync Complete: fetched={len(all_carriers)}, "
				f"created={created_count}, updated={updated_count}"
			)

	except requests.exceptions.Timeout:
		frappe.log_error(title="ShipsGo Timeout", message="API request timed out")

	except requests.exceptions.ConnectionError:
		frappe.log_error(title="ShipsGo Connection Error", message="Unable to connect to ShipsGo")

	except Exception:
		frappe.log_error(title="ShipsGo Unknown Error", message=frappe.get_traceback())
