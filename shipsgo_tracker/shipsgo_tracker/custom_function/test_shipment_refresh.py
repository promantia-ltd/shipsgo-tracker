# Copyright (c) 2026, karuppasamy and contributors
import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from shipsgo_tracker.shipsgo_tracker.custom_function import shipment_refresh as sr


class TestShipmentMapping(FrappeTestCase):
    def test_to_system_datetime_strips_offset(self):
        # 2025-05-24T12:00:00+01:00 == 11:00 UTC; with system tz pinned to UTC
        # the stored naive value must be 11:00, proving the offset was applied
        # (not merely dropped).
        with patch(
            "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.get_system_timezone",
            return_value="UTC",
        ):
            result = sr._to_system_datetime("2025-05-24T12:00:00+01:00")
        self.assertIsInstance(result, datetime.datetime)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result, datetime.datetime(2025, 5, 24, 11, 0, 0))

    def test_to_system_datetime_none(self):
        self.assertIsNone(sr._to_system_datetime(None))

    def test_map_with_route(self):
        shipment = {
            "id": 1002,
            "status": "BOOKED",
            "route": {
                "port_of_loading": {"date_of_loading": "2025-03-10T12:00:00+03:00"},
                "port_of_discharge": {"date_of_discharge": "2025-03-18T12:00:00+02:00"},
            },
        }
        updates = sr.map_shipment_fields(shipment)
        self.assertEqual(updates["custom_shipsgo_live_status"], "BOOKED")
        self.assertIn("custom_shipsgo_etd", updates)
        self.assertIn("custom_shipsgo_eta", updates)
        self.assertIn("custom_shipsgo_last_synced", updates)
        self.assertIsInstance(updates["custom_shipsgo_etd"], datetime.datetime)
        self.assertIsInstance(updates["custom_shipsgo_eta"], datetime.datetime)

    def test_map_inprogress_route_null(self):
        shipment = {"id": 1001, "status": "INPROGRESS", "route": None}
        updates = sr.map_shipment_fields(shipment)
        self.assertEqual(updates["custom_shipsgo_live_status"], "INPROGRESS")
        self.assertNotIn("custom_shipsgo_etd", updates)
        self.assertNotIn("custom_shipsgo_eta", updates)


def _resp(status_code=200, payload=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = payload or {}
    m.text = frappe.as_json(payload or {})
    return m


class TestFetchShipments(FrappeTestCase):
    def test_single_page(self):
        payload = {"message": "SUCCESS", "shipments": [{"id": 1}], "meta": {"more": False}}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)) as g:
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertTrue(ok)
        self.assertEqual(len(shipments), 1)
        self.assertEqual(g.call_count, 1)

    def test_first_run_omits_updated_at_filter(self):
        payload = {"message": "SUCCESS", "shipments": [], "meta": {"more": False}}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)) as g:
            sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertNotIn("filters[updated_at]", g.call_args.kwargs["params"])

    def test_windowed_includes_updated_at_filter(self):
        payload = {"message": "SUCCESS", "shipments": [], "meta": {"more": False}}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)) as g:
            sr._fetch_shipments("https://api/v2", "tok", "2025-01-01 00:00:00")
        self.assertIn("filters[updated_at]", g.call_args.kwargs["params"])
        self.assertEqual(g.call_args.kwargs["params"]["filters[updated_at]"], "gte:2025-01-01 00:00:00")

    def test_two_pages(self):
        page1 = {"message": "SUCCESS", "shipments": [{"id": i} for i in range(100)], "meta": {"more": True}}
        page2 = {"message": "SUCCESS", "shipments": [{"id": 100}], "meta": {"more": False}}
        with patch.object(sr.requests, "get", side_effect=[_resp(200, page1), _resp(200, page2)]) as g:
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", "2025-01-01 00:00:00")
        self.assertTrue(ok)
        self.assertEqual(len(shipments), 101)
        self.assertEqual(g.call_args_list[1].kwargs["params"]["skip"], sr.PAGE_SIZE)

    def test_429_is_recoverable(self):
        with patch.object(sr.requests, "get", return_value=_resp(429, {})) as g:
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertFalse(ok)
        self.assertEqual(shipments, [])
        self.assertEqual(g.call_count, 1)

    def test_timeout_is_recoverable(self):
        with patch.object(sr.requests, "get", side_effect=sr.Timeout()):
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertFalse(ok)
        self.assertEqual(shipments, [])

    def test_connection_error_is_recoverable(self):
        with patch.object(sr.requests, "get", side_effect=sr.ConnectionError()):
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertFalse(ok)
        self.assertEqual(shipments, [])


class TestRefreshActive(FrappeTestCase):
    def setUp(self):
        setting = frappe.get_single("ShipsGo Setting")
        setting.enable = 1
        setting.shipsgo_base_api_url = "https://api.test/v2"
        setting.last_shipment_sync_at = None
        setting.save(ignore_permissions=True)

        if not frappe.db.exists("ShipsGo User Access Tokens", "Administrator"):
            tok = frappe.new_doc("ShipsGo User Access Tokens")
            tok.user = "Administrator"
            tok.access_token = "secret-token"
            tok.active = 1
            tok.is_default = 1
            tok.insert(ignore_permissions=True)
        else:
            doc = frappe.get_doc("ShipsGo User Access Tokens", "Administrator")
            doc.active = 1
            doc.is_default = 1
            doc.access_token = "secret-token"
            doc.save(ignore_permissions=True)

        self.project = frappe.new_doc("Project")
        self.project.project_name = "SHIPSGO-TEST-PULLBACK"
        self.project.custom_shipsgo_shipment_id = "555001"
        self.project.project_owner = "Administrator"
        self.project.expected_start_date = "2025-01-01"
        self.project.company = frappe.defaults.get_global_default("company")
        self.project.insert(ignore_permissions=True)

    def tearDown(self):
        if frappe.db.exists("Project", self.project.name):
            frappe.delete_doc("Project", self.project.name, ignore_permissions=True, force=True)

    def test_updates_matching_project(self):
        payload = {
            "message": "SUCCESS",
            "shipments": [{
                "id": 555001, "status": "SAILING",
                "route": {
                    "port_of_loading": {"date_of_loading": "2025-03-10T12:00:00+03:00"},
                    "port_of_discharge": {"date_of_discharge": "2025-03-18T12:00:00+02:00"},
                },
            }],
            "meta": {"more": False},
        }
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)):
            result = sr.refresh_active_shipments()
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            frappe.db.get_value("Project", self.project.name, "custom_shipsgo_live_status"),
            "SAILING",
        )
        self.assertIsNotNone(
            frappe.db.get_value("Project", self.project.name, "custom_shipsgo_eta")
        )
        self.assertIsNotNone(frappe.get_single("ShipsGo Setting").last_shipment_sync_at)

    def test_recoverable_error_does_not_advance_window(self):
        with patch.object(sr.requests, "get", return_value=_resp(429, {})):
            result = sr.refresh_active_shipments()
        self.assertFalse(result["ok"])
        self.assertIsNone(frappe.get_single("ShipsGo Setting").last_shipment_sync_at)

    def test_disabled_integration_skips(self):
        setting = frappe.get_single("ShipsGo Setting")
        setting.enable = 0
        setting.save(ignore_permissions=True)
        result = sr.refresh_active_shipments()
        self.assertEqual(result["updated"], 0)
        self.assertTrue(result["ok"])

    def test_route_null_updates_status_only(self):
        # pre-seed an ETA so we can prove route-null does NOT overwrite it
        frappe.db.set_value("Project", self.project.name, "custom_shipsgo_eta",
                            "2025-01-01 00:00:00", update_modified=False)
        payload = {"message": "SUCCESS",
                   "shipments": [{"id": 555001, "status": "INPROGRESS", "route": None}],
                   "meta": {"more": False}}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)):
            result = sr.refresh_active_shipments()
        self.assertEqual(
            frappe.db.get_value("Project", self.project.name, "custom_shipsgo_live_status"),
            "INPROGRESS")
        self.assertIsNotNone(
            frappe.db.get_value("Project", self.project.name, "custom_shipsgo_eta"))
        self.assertEqual(result["updated"], 1)
        self.assertTrue(result["ok"])
