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

    def test_two_pages(self):
        page1 = {"message": "SUCCESS", "shipments": [{"id": i} for i in range(100)], "meta": {"more": True}}
        page2 = {"message": "SUCCESS", "shipments": [{"id": 100}], "meta": {"more": False}}
        with patch.object(sr.requests, "get", side_effect=[_resp(200, page1), _resp(200, page2)]):
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", "2025-01-01 00:00:00")
        self.assertTrue(ok)
        self.assertEqual(len(shipments), 101)

    def test_429_is_recoverable(self):
        with patch.object(sr.requests, "get", return_value=_resp(429, {})):
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertFalse(ok)
        self.assertEqual(shipments, [])

    def test_timeout_is_recoverable(self):
        with patch.object(sr.requests, "get", side_effect=sr.Timeout()):
            shipments, ok = sr._fetch_shipments("https://api/v2", "tok", None)
        self.assertFalse(ok)
