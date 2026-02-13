# ShipsGo Tracker

Container tracking integration for ERPNext — create and manage ShipsGo ocean shipments directly from your Project records.

## Overview

ShipsGo Tracker bridges ERPNext Projects with [ShipsGo's](https://shipsgo.com) container tracking API. Operations team members can enter tracking details on a Project form and create a ShipsGo shipment with a single click — no context-switching, no manual data entry on external dashboards.

### Key Features

- **One-click shipment creation** from the ERPNext Project form
- **Multi-user credential support** — each employee uses their own ShipsGo API token
- **Carrier validation** — syncs 130+ ocean carriers from ShipsGo and provides a searchable dropdown
- **Smart error handling** — distinguishes retryable errors (rate limits, timeouts) from non-retryable ones (insufficient credits, validation failures)
- **Audit trail** — every shipment creation is logged on the Project timeline
- **Global kill switch** — administrators can enable/disable the integration instantly

## Requirements

- Frappe Framework v15+
- ERPNext v15+
- Active [ShipsGo](https://shipsgo.com) account with API access
- Python 3.10+

## Installation

### Frappe Cloud

Navigate to your site dashboard → **Apps** → **Install App** → search for `shipsgo_tracker`.

### Self-Hosted

```bash
bench get-app https://github.com/promantia-ltd/shipsgo_tracker.git
bench --site your-site.localhost install-app shipsgo_tracker
bench --site your-site.localhost migrate
```

## Setup

### 1. Configure Global Settings

Navigate to **ShipsGo Tracker Settings** from the search bar.

| Field | Description |
|-------|-------------|
| API Base URL | Pre-filled with `https://api.shipsgo.com/v2`. Change only if using a sandbox or custom environment. |
| Enabled | Check this to activate the integration site-wide. |

### 2. Add Employee Tokens

Each operations team member needs their own credential record.

1. Navigate to **ShipsGo User Token** → **New**
2. Select your **User** (auto-fills to current user for non-admins)
3. Paste your **API Token** from the ShipsGo dashboard → *Shipsgo API* section
4. Ensure **Active** is checked
5. Save

> **Note:** Each employee can only view and edit their own token. System Managers can manage all tokens.

### 3. Sync Ocean Carriers

Navigate to **ShipsGo Tracker Settings** → click **Sync Carriers**. This fetches the full list of supported shipping lines from ShipsGo and stores them locally for the Carrier dropdown on Project forms.

Recommended: re-sync periodically (monthly) to pick up any new carriers.

## Usage

1. Open a **Project** record
2. Scroll to the **Shipment Tracking** section
3. Fill in:
   - **Carrier** — select from the dropdown (e.g., MSC, Maersk, ONE)
   - **Track With** — Container Number, Booking Number, or BL Number
   - **Tracking Number** — the actual identifier
4. **Save** the Project
5. Click **ShipsGo → Create Shipment**
6. Confirm the dialog

On success, the ShipsGo Shipment ID is populated, status changes to "Created," and a timeline comment is added.

## Error Handling

| Scenario | What Happens | User Action |
|----------|-------------|-------------|
| Rate limit (429) | Status unchanged, button stays visible | Wait a few seconds, click again |
| Timeout / Network error | Status unchanged, button stays visible | Check network, click again |
| Insufficient credits (402) | Status set to "Failed" | Top up credits on ShipsGo, click "Retry Shipment" |
| Invalid input (400/422) | Status set to "Failed" with error details | Correct the carrier or tracking number, click "Retry Shipment" |
| Shipment already exists (409) | Treated as success, existing ID saved | No action needed |

## DocTypes

| DocType | Type | Purpose |
|---------|------|---------|
| ShipsGo Tracker Settings | Single | Global configuration (API URL, enable/disable) |
| ShipsGo User Token | Regular | Per-employee API credentials (encrypted) |
| ShipsGo Ocean Carrier | Regular | Cached carrier list from ShipsGo API |

## Custom Fields on Project

| Field | Type | Description |
|-------|------|-------------|
| Carrier | Link | Links to ShipsGo Ocean Carrier |
| Track With | Select | Container Number / Booking Number / BL Number |
| Tracking Number | Data | Container, booking, or BL number |
| ShipsGo Shipment ID | Data (Read-Only) | Populated after successful creation |
| Shipment Status | Select (Read-Only) | Not Created / Created / Failed |
| Shipment Error | Small Text (Read-Only) | Error details on failure |

## API Credits

ShipsGo uses a credit-based system:

- **1 credit** per new shipment creation
- **0 credits** for duplicate shipments (same reference + tracking number)
- **0 credits** for all subsequent data queries on existing shipments
- Credits are tied to the individual employee's ShipsGo account

## Limitations

- This app creates shipments on ShipsGo only. It does **not** pull tracking updates back into ERPNext.
- Automated status synchronization and webhook support are not included in the current version.
- Only Master Bill of Lading numbers are supported. House BL numbers will not return tracking data.
- Each project supports one shipment. Multi-container tracking per project is not supported in the current version.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

Please ensure all existing tests pass and add tests for new functionality.

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE) for details.

## Links

- [ShipsGo Website](https://shipsgo.com)
- [ShipsGo API Documentation](https://api.shipsgo.com/docs/v2/)
- [Frappe Framework](https://frappeframework.com)
- [ERPNext](https://erpnext.com)

### Shipsgo Tracker

dev

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app shipsgo_tracker
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/shipsgo_tracker
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit 
