import re

import frappe
import requests
from frappe import _
from requests.exceptions import ConnectionError, Timeout


def validate_shipment_tracking(doc, method):
	"""
	Validate shipment tracking fields based on Track With selection
	"""

	if not doc.custom_carrier_code:
		frappe.throw(_("Carrier Code is required."))

	track_with = doc.custom_track_with

	if not track_with:
		return

	tracking_number = doc.custom_shipsgo_tracking_number

	if track_with == "Container Number":
		if not tracking_number:
			frappe.throw(_("Container Number is required when Track With is 'Container Number'."))

		# ShipsGo container format: ^[A-Z]{4}[0-9]{7}$
		pattern = r"^[A-Z]{4}[0-9]{7}$"

		if not re.match(pattern, tracking_number):
			frappe.throw(
				_(
					"Invalid Container Number format.<br>"
					"Expected format: 4 uppercase letters followed by 7 digits (Example: ABCD1234567)"
				)
			)

	elif track_with == "MBL / Booking Number":
		if not tracking_number:
			frappe.throw(_("MBL / Booking Number is required when Track With is 'MBL / Booking Number'."))

		# ShipsGo booking format: ^[a-zA-Z0-9/-]+$
		pattern = r"^[a-zA-Z0-9/-]+$"

		if not re.match(pattern, tracking_number):
			frappe.throw(
				_("Invalid MBL / Booking Number format.<br>" "Only letters, numbers, / and - are allowed.")
			)


@frappe.whitelist()
def create_shipment(docname):
	doc = frappe.get_doc("Project", docname)

	if not doc.custom_carrier or not doc.custom_shipsgo_tracking_number:
		frappe.throw("Carrier and Tracking Number are required.")

	shipsgo_token, base_url = get_access_token()

	url = f"{base_url}/ocean/shipments"

	payload = {
		"reference": doc.name,
		"carrier": doc.custom_carrier_code,
	}

	if doc.custom_track_with == "Container Number":
		payload["container_number"] = doc.custom_shipsgo_tracking_number
	elif doc.custom_track_with == "MBL / Booking Number":
		payload["booking_number"] = doc.custom_shipsgo_tracking_number

	headers = {"Content-Type": "application/json", "X-Shipsgo-User-Token": shipsgo_token}

	try:
		response = requests.post(url, json=payload, headers=headers, timeout=30)
		data = response.json()

		if response.status_code in [200, 201]:
			if data.get("message") != "SUCCESS":
				return {"status": "failed", "error": data.get("message", "Unexpected API response")}

			shipment_data = data.get("shipment") or {}
			shipment_id = shipment_data.get("id")

			if not shipment_id:
				return {"status": "failed", "error": "Shipment created but ID missing in response."}

			doc.db_set("custom_shipsgo_shipment_id", shipment_id)
			doc.db_set("custom_shipment_status", "Created")
			doc.db_set("custom_shipment_error", "")

			doc.add_comment("Comment", f"Shipment created on ShipsGo. ID: {shipment_id}")

			return {"status": "success", "shipment_id": shipment_id}

		elif response.status_code == 409:
			shipment_data = data.get("shipment") or {}
			shipment_id = shipment_data.get("id")

			doc.db_set("custom_shipsgo_shipment_id", shipment_id)
			doc.db_set("custom_shipment_status", "Created")
			doc.db_set("custom_shipment_error", "")

			doc.add_comment("Comment", f"Existing shipment linked (HTTP 409). ID: {shipment_id}")

			return {"status": "linked_existing", "shipment_id": shipment_id}

		elif response.status_code == 429:
			return {
				"status": "retryable",
				"error": "Too many requests. Please wait a few seconds and try again.",
			}

		elif response.status_code == 402:
			doc.db_set("custom_shipment_status", "Failed")
			doc.db_set("custom_shipment_error", "Insufficient ShipsGo credits.")

			return {"status": "failed", "error": "ShipsGo account has insufficient credits."}

		elif response.status_code in [400, 422]:
			doc.db_set("custom_shipment_status", "Failed")
			doc.db_set("custom_shipment_error", response.text)

			return {"status": "failed", "error": "Invalid carrier or tracking number."}

		elif response.status_code >= 500:
			doc.db_set("custom_shipment_status", "Failed")
			doc.db_set("custom_shipment_error", response.text)

			return {"status": "failed", "error": "ShipsGo server error. Please retry later."}

		else:
			doc.db_set("custom_shipment_status", "Failed")
			doc.db_set("custom_shipment_error", response.text)

			return {"status": "failed", "error": response.text}

	except requests.exceptions.Timeout:
		return {"status": "retryable", "error": "Request timed out. Please try again."}

	except requests.exceptions.ConnectionError:
		return {"status": "retryable", "error": "Unable to connect to ShipsGo."}

	except Exception as e:
		doc.db_set("custom_shipment_status", "Failed")
		doc.db_set("custom_shipment_error", str(e))
		frappe.log_error(frappe.get_traceback(), "Shipment Creation Failed")

		return {"status": "failed", "error": str(e)}


def get_access_token(fetch_call=False):
	shipsgo_setting = frappe.get_single("ShipsGo Setting")

	if not shipsgo_setting.enable:
		frappe.throw("ShipsGo Integration is disabled in Settings")

	if fetch_call:
		current_user = frappe.db.get_value("ShipsGo User Access Tokens", {"default": 1}, "name")

		if not current_user:
			frappe.throw("No default access token user is configured.")
	else:
		current_user = frappe.session.user

	token_doc = frappe.get_doc("ShipsGo User Access Tokens", current_user)

	if not token_doc or not token_doc.access_token:
		frappe.throw(f"ShipsGo token is not configured for user: {current_user}")

	if not token_doc.active:
		frappe.throw(f"ShipsGo token is not active for user: {current_user}")

	shipsgo_token = token_doc.get_password("access_token")

	base_url = shipsgo_setting.shipsgo_base_api_url

	return shipsgo_token, base_url
