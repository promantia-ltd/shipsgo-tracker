// Copyright (c) 2026, karuppasamy and contributors
// For license information, please see license.txt

frappe.ui.form.on("ShipsGo Setting", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Sync Carriers"), function () {
				frappe.call({
					method: "shipsgo_tracker.shipsgo_tracker.doctype.shipping_carriers.shipping_carriers.fetch_carrier_list",
					freeze: true,
					freeze_message: __("Fetching carriers from ShipsGo..."),
					callback: function () {
						frappe.show_alert({
							message: __("Carrier sync complete."),
							indicator: "green",
						});
					},
					error: function () {
						frappe.show_alert({
							message: __("Carrier sync failed. Check Error Log for details."),
							indicator: "red",
						});
					},
				});
			});
		}
	},
});
