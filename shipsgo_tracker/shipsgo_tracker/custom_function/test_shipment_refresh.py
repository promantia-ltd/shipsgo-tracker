# Copyright (c) 2026, karuppasamy and contributors
import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from shipsgo_tracker.shipsgo_tracker.custom_function import shipment_refresh as sr


class TestShipmentMapping(FrappeTestCase):
    def test_to_system_datetime_strips_offset(self):
        # 2025-05-24T12:00:00+01:00 == 11:00 UTC
        result = sr._to_system_datetime("2025-05-24T12:00:00+01:00")
        self.assertIsInstance(result, datetime.datetime)
        self.assertIsNone(result.tzinfo)

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

    def test_map_inprogress_route_null(self):
        shipment = {"id": 1001, "status": "INPROGRESS", "route": None}
        updates = sr.map_shipment_fields(shipment)
        self.assertEqual(updates["custom_shipsgo_live_status"], "INPROGRESS")
        self.assertNotIn("custom_shipsgo_etd", updates)
        self.assertNotIn("custom_shipsgo_eta", updates)
