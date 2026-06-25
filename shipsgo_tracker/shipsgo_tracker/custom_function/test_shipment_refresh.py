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

    def test_to_utc_filter_value_converts_and_margins(self):
        # 2025-06-15 12:00 EDT (UTC-4); minus 1h margin = 11:00 EDT = 15:00 UTC
        with patch(
            "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.get_system_timezone",
            return_value="America/New_York",
        ):
            out = sr._to_utc_filter_value("2025-06-15 12:00:00")
        self.assertEqual(out, "2025-06-15 15:00:00")

    def test_to_utc_filter_value_utc_site(self):
        with patch(
            "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.get_system_timezone",
            return_value="UTC",
        ):
            out = sr._to_utc_filter_value("2025-06-15 12:00:00")
        self.assertEqual(out, "2025-06-15 11:00:00")

    def test_to_utc_filter_value_none(self):
        self.assertIsNone(sr._to_utc_filter_value(None))


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
        if frappe.db.exists("Project", {"project_name": "SHIPSGO-TEST-PULLBACK"}):
            frappe.delete_doc(
                "Project",
                frappe.db.get_value("Project", {"project_name": "SHIPSGO-TEST-PULLBACK"}),
                ignore_permissions=True, force=True,
            )

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

    def test_windowed_filter_is_utc_converted(self):
        """Proves that last_shipment_sync_at is UTC-converted before being sent as
        the filters[updated_at] param. We pin get_system_timezone to 'UTC' so the
        expected value is deterministic: 2025-01-01 11:00:00 (minus 1h margin from noon)."""
        setting = frappe.get_single("ShipsGo Setting")
        setting.last_shipment_sync_at = "2025-01-01 12:00:00"
        setting.save(ignore_permissions=True)

        payload = {"message": "SUCCESS", "shipments": [], "meta": {"more": False}}
        with patch(
            "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.get_system_timezone",
            return_value="UTC",
        ):
            with patch.object(sr.requests, "get", return_value=_resp(200, payload)) as g:
                sr.refresh_active_shipments()

        sent_params = g.call_args.kwargs["params"]
        self.assertIn("filters[updated_at]", sent_params)
        self.assertEqual(sent_params["filters[updated_at]"], "gte:2025-01-01 11:00:00")


class TestRefreshSingle(FrappeTestCase):
    def setUp(self):
        if frappe.db.exists("Project", {"project_name": "SHIPSGO-TEST-SINGLE"}):
            frappe.delete_doc(
                "Project",
                frappe.db.get_value("Project", {"project_name": "SHIPSGO-TEST-SINGLE"}),
                ignore_permissions=True, force=True,
            )

        setting = frappe.get_single("ShipsGo Setting")
        setting.enable = 1
        setting.shipsgo_base_api_url = "https://api.test/v2"
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
        self.project.project_name = "SHIPSGO-TEST-SINGLE"
        self.project.custom_shipsgo_shipment_id = "777001"
        self.project.project_owner = "Administrator"
        self.project.expected_start_date = "2025-01-01"
        self.project.company = frappe.defaults.get_global_default("company")
        self.project.insert(ignore_permissions=True)

    def tearDown(self):
        if frappe.db.exists("Project", self.project.name):
            frappe.delete_doc("Project", self.project.name, ignore_permissions=True, force=True)

    def test_success_updates_project(self):
        payload = {"message": "SUCCESS", "shipments": [{"id": 777001, "status": "ARRIVED",
            "route": {"port_of_loading": {"date_of_loading": "2025-03-10T12:00:00+03:00"},
                      "port_of_discharge": {"date_of_discharge": "2025-03-18T12:00:00+02:00"}}}]}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)) as g:
            result = sr.refresh_single_shipment(self.project.name)
        self.assertEqual(result["status"], "success")
        self.assertEqual(g.call_args.kwargs["params"]["filters[id]"], "eq:777001")
        self.assertEqual(
            frappe.db.get_value("Project", self.project.name, "custom_shipsgo_live_status"), "ARRIVED")

    def test_not_found_when_empty(self):
        with patch.object(sr.requests, "get", return_value=_resp(200, {"message": "SUCCESS", "shipments": []})):
            result = sr.refresh_single_shipment(self.project.name)
        self.assertEqual(result["status"], "not_found")

    def test_not_found_on_failure_message(self):
        payload = {"message": "FAILURE", "shipments": [{"id": 777001, "status": "ARRIVED"}]}
        with patch.object(sr.requests, "get", return_value=_resp(200, payload)):
            result = sr.refresh_single_shipment(self.project.name)
        self.assertEqual(result["status"], "not_found")


class TestTrackingUrlBuilder(FrappeTestCase):
    def test_build_map_deeplink_when_token(self):
        shipment = {"container_number": "ABCD1234567", "tokens": {"map": "tok-uuid"}}
        url = sr._build_tracking_url("6083781", shipment, "https://map.x/ocean/shipments", "https://s.x/q")
        self.assertEqual(url, "https://map.x/ocean/shipments/6083781?token=tok-uuid")

    def test_build_container_search_when_no_token(self):
        shipment = {"container_number": "ABCD1234567", "tokens": {}}
        url = sr._build_tracking_url("6083781", shipment, "https://map.x/ocean/shipments", "https://s.x/q")
        self.assertEqual(url, "https://s.x/q?query=ABCD1234567")

    def test_build_none_when_neither(self):
        shipment = {"container_number": None, "tokens": None}
        url = sr._build_tracking_url("6083781", shipment, "https://map.x/ocean/shipments", "https://s.x/q")
        self.assertIsNone(url)

    def test_resolve_bases_fallback_when_blank(self):
        s = frappe.get_single("ShipsGo Setting")
        s.tracking_map_base_url = None
        s.tracking_public_search_url = None
        s.save(ignore_permissions=True)
        map_base, search_base = sr._resolve_tracking_bases()
        self.assertEqual(map_base, sr.DEFAULT_MAP_DEEPLINK)
        self.assertEqual(search_base, sr.DEFAULT_PUBLIC_SEARCH)

    def test_resolve_bases_uses_setting_and_strips_slash(self):
        s = frappe.get_single("ShipsGo Setting")
        s.tracking_map_base_url = "https://map.custom/ocean/shipments/"
        s.tracking_public_search_url = "https://search.custom/track"
        s.save(ignore_permissions=True)
        map_base, search_base = sr._resolve_tracking_bases()
        self.assertEqual(map_base, "https://map.custom/ocean/shipments")
        self.assertEqual(search_base, "https://search.custom/track")
