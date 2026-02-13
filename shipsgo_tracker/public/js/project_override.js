frappe.ui.form.on("Project", {
	refresh: function (frm) {
		frappe.call({
			method: "frappe.client.get_value",
			args: {
				doctype: "ShipsGo Setting",
				fieldname: "shipsgo_dashboard_api_url_copy",
			},
			callback: function (r) {
				if (r.message && r.message.shipsgo_dashboard_api_url_copy) {
					let base_url = r.message.shipsgo_dashboard_api_url_copy;
					let tracking_url = `${base_url}/dashboard/track-and-trace/my-shipments`;

					frm.fields_dict.custom_shipsgo_tracking_link.$wrapper.html(
						`<a href="${tracking_url}" target="_blank" style="color:#1f4fff; font-weight:600;">
							Open Tracking Link
						</a>`
					);
				}
			},
		});
	},
	set_custom_buttons(frm) {
		if (frm.events._super_set_custom_buttons) {
			frm.events._super_set_custom_buttons(frm);
		}

		if (frm.doc.custom_carrier && frm.doc.custom_track_with && frm.doc.custom_track_with) {
			let label = "Create Shipment";

			if (
				frm.doc.custom_shipment_status === "Created" &&
				frm.doc.custom_shipsgo_shipment_id
			) {
				return;
			} else if (frm.doc.custom_shipment_status === "Failed") {
				label = "Retry Shipment";
			} else if (frm.doc.custom_shipment_status === "Not Created") {
				label = "Create Shipment";
			}

			frm.add_custom_button(
				__(label),
				function () {
					frappe.confirm(
						__("Create a shipment on ShipsGo for this project?"),
						function () {
							frappe.call({
								method: "shipsgo_tracker.shipsgo_tracker.custom_function.project_doc_custom_function.create_shipment",
								args: {
									docname: frm.doc.name,
								},
								freeze: true,
								freeze_message: __("Creating shipment on ShipsGo..."),

								callback: function (r) {
									if (r.message && r.message.status === "success") {
										frappe.show_alert({
											message: __("Shipment created successfully."),
											indicator: "green",
										});

										frm.reload_doc();
									} else if (
										r.message &&
										r.message.status === "linked_existing"
									) {
										frappe.msgprint({
											title: __("Shipment Already Exists"),
											message: __(
												"This shipment is already linked in ShipsGo."
											),
											indicator: "blue",
										});

										frm.reload_doc();
									} else if (r.message && r.message.status === "retryable") {
										frappe.msgprint({
											title: __("Temporary Issue"),
											message:
												r.message?.error ||
												__(
													"Too many requests. Please wait a few seconds and try again."
												),
											indicator: "orange",
										});
									} else {
										frappe.msgprint({
											title: __("Shipment Creation Failed"),
											message:
												r.message?.error ||
												__(
													"An unknown error occurred. Please check the Error Log for more details."
												),
											indicator: "red",
										});
									}
								},
							});
						}
					);
				},
				__("Actions")
			);
		}
	},
});
