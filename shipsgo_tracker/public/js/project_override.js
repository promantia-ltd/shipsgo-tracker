frappe.ui.form.on("Project", {
	refresh: function (frm) {
		render_tracking_link(frm);
		add_custom_buttons(frm);
	},
});

function render_tracking_link(frm) {
	const field = frm.fields_dict.custom_shipsgo_tracking_link;
	if (!field) return;
	if (!frm.doc.custom_shipsgo_shipment_id) {
		field.$wrapper.html("");
		return;
	}
	const url = frm.doc.custom_shipsgo_tracking_url;
	if (url) {
		field.$wrapper.html(
			`<a href="${frappe.utils.escape_html(url)}" target="_blank" rel="noopener noreferrer" style="color:#1f4fff; font-weight:600;">${__("Open Tracking")}</a>`
		);
		return;
	}
	// No stored link: lazy-load it once per form session (non-blocking, silent on failure).
	if (frm.__shipsgo_tracking_tried) {
		render_tracking_placeholder(frm, field);
		return;
	}
	frm.__shipsgo_tracking_tried = true;
	field.$wrapper.html(`<span class="text-muted">${__("Loading live tracking…")}</span>`);
	frappe.call({
		method: "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.refresh_single_shipment",
		args: { docname: frm.doc.name },
		callback: function (r) {
			const res = r.message || {};
			if (res.status === "success" && res.tracking_url) {
				frm.doc.custom_shipsgo_tracking_url = res.tracking_url;
				field.$wrapper.html(
					`<a href="${frappe.utils.escape_html(res.tracking_url)}" target="_blank" rel="noopener noreferrer" style="color:#1f4fff; font-weight:600;">${__("Open Tracking")}</a>`
				);
			} else {
				render_tracking_placeholder(frm, field);
			}
		},
		error: function () {
			render_tracking_placeholder(frm, field);
		},
	});
}

function render_tracking_placeholder(frm, field) {
	field.$wrapper.html(
		`<div class="text-muted" style="margin-bottom:4px;">${__(
			"Live tracking link is being prepared. If it does not appear, use the button below."
		)}</div>` +
			`<button class="btn btn-xs btn-default" id="shipsgo-open-tracking">${__("Open Tracking")}</button>`
	);
	field.$wrapper.find("#shipsgo-open-tracking").on("click", function () {
		frm.__shipsgo_tracking_tried = false;
		render_tracking_link(frm);
	});
}

// Consolidated function for all buttons
function add_custom_buttons(frm) {
	if (frm.events._super_set_custom_buttons) {
		frm.events._super_set_custom_buttons(frm);
	}

	if (
		frm.doc.custom_carrier &&
		frm.doc.custom_track_with &&
		frm.doc.custom_shipsgo_tracking_number &&
		!(frm.doc.custom_shipment_status === "Created" && frm.doc.custom_shipsgo_shipment_id)
	) {
		let label = "Create Shipment";

		if (frm.doc.custom_shipment_status === "Failed") {
			label = "Retry Shipment";
		} else if (frm.doc.custom_shipment_status === "Not Created") {
			label = "Create Shipment";
		}

		frm.add_custom_button(
			__(label),
			function () {
				frappe.confirm(__("Create a shipment on ShipsGo for this project?"), function () {
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
							} else if (r.message && r.message.status === "linked_existing") {
								frappe.msgprint({
									title: __("Shipment Already Exists"),
									message: __("This shipment is already linked in ShipsGo."),
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

								frm.reload_doc();
							}
						},
					});
				});
			},
			__("Actions")
		);
	}

	if (frm.doc.custom_shipsgo_shipment_id) {
		frm.add_custom_button(
			__("Refresh Tracking"),
			function () {
				frappe.call({
					method: "shipsgo_tracker.shipsgo_tracker.custom_function.shipment_refresh.refresh_single_shipment",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Refreshing shipment tracking from ShipsGo..."),
					callback: function (r) {
						const res = r.message || {};
						if (res.status === "success") {
							frappe.show_alert({
								message: __("Tracking updated — live-map link saved."),
								indicator: "green",
							});
							frm.reload_doc();
						} else if (res.status === "retryable") {
							frappe.msgprint({
								title: __("Temporary Issue"),
								message: res.error,
								indicator: "orange",
							});
						} else {
							frappe.msgprint({
								title: __("Not Updated"),
								message:
									res.error ||
									__("Could not refresh tracking for this shipment."),
								indicator: "red",
							});
						}
					},
				});
			},
			__("Actions")
		);
	}
}
