# sales-data
Balaclava Sales Dashboard

## Streamlit contact-log storage

The sales data loads from the shared Google Sheet automatically. The Store Contact Log can also write its team contact log back to Google Sheets when Streamlit secrets are configured.

Preferred option: add these service-account secrets in Streamlit Cloud:

```toml
contact_log_spreadsheet_id = "1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw"
contact_log_worksheet = "Contact Log"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

Share the Google spreadsheet with the service account `client_email` as an Editor.

Alternative option: if a service account JSON is not available, use OAuth refresh-token secrets instead:

```toml
contact_log_spreadsheet_id = "1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw"
contact_log_worksheet = "Contact Log"

[google_oauth]
client_id = "..."
client_secret = "..."
refresh_token = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

The `token_uri` value must be exactly `https://oauth2.googleapis.com/token`; do not use the OAuth Playground URL there. Do not store a short-lived `Authorization: Bearer ...` access token; it expires quickly. If both auth methods are configured, the service account is used first. If neither method is configured, the app falls back to local SQLite, which is not durable on Streamlit Cloud.

## Weekly outreach alerts

The contact form stores next-outreach reminder fields in the `Contact Log` worksheet. To send weekly digest emails:

1. Open the contact-log Google Sheet as `admin@balaclavabrands`.
2. Go to Extensions -> Apps Script.
3. Paste the contents of `contact_outreach_alerts.gs`.
4. Run `createWeeklyOutreachDigestTrigger` once and approve permissions.

The trigger runs every Monday morning and sends one digest per recipient for stores whose `Next Outreach Date` falls within that Monday-Sunday week. DK routes to `danny@balaclavabrands.com`, CH routes to `chris@balaclavabrands.com`, and all alerts CC `geoff@ksavagesupply.com` and `roger@ksavagesupply.com`.

## Order activity

The Order Activity tab auto-loads from the shared Google Sheet tab named `Cultivera Data`. Override this with Streamlit secrets:

```toml
order_sheet_url = "https://docs.google.com/spreadsheets/d/..."
order_sheet_name = "Cultivera Data"
# Or use a worksheet gid instead of a name:
order_sheet_gid = "0"
```

## GrowFlow report sync

`growflow_report_sync.gs` connects to the GrowFlow Wholesale Partner API with Auth0 client credentials, runs a configured GraphQL query, flattens the returned `items` rows, and writes them to a tab in the shared Google Sheet.

### Direct Production inventory API

The Production dashboard can load inventory directly from GrowFlow instead of the manually pasted Inventory worksheet. Add these secrets in Streamlit Cloud:

```toml
growflow_client_id = "..."
growflow_client_secret = "..."
growflow_region_code = "wa"
growflow_license_number = "000021"
growflow_facility_label = "B-9"
```

For multiple GrowFlow licenses/facilities, use `growflow_inventory_sources` instead of the single `growflow_license_number` fields:

```toml
growflow_client_id = "..."
growflow_client_secret = "..."

[[growflow_inventory_sources]]
region_code = "wa"
license_number = "000021"
facility_label = "B-9"

[[growflow_inventory_sources]]
region_code = "wa"
license_number = "000022"
facility_label = "Block 13"
```

When those secrets are present, the dashboard fetches GrowFlow inventory directly and uses the Inventory GID only as a fallback for non-API setups. API results are cached for 5 minutes.

To install:

1. Open the shared Google Sheet.
2. Go to Extensions -> Apps Script.
3. Add a new script file and paste `growflow_report_sync.gs`.
4. In Project Settings -> Script Properties, set `GROWFLOW_CLIENT_ID` and `GROWFLOW_CLIENT_SECRET`.
5. Run either `setupGrowFlowOrdersExampleConfig` or `setupGrowFlowInventoryExampleConfig`, then edit `GROWFLOW_GRAPHQL_VARIABLES_JSON` for the correct `licenseNumber`.
6. Run `checkGrowFlowReportSyncSetup`, then `testGrowFlowGraphQLRequest`.
7. Run `syncGrowFlowReportToSheet` once to write the sheet.
8. Run `createGrowFlowHourlyTrigger` to refresh hourly.

For the Production dashboard inventory tab, run `setupGrowFlowInventoryExampleConfig`. It uses the inventory fields from the GrowFlow query plus `skip` and `take` so the sync can page through more than GrowFlow's max page size. If the inventory is for a specific dashboard facility, set `GROWFLOW_FACILITY_LABEL` to `B-9` or `Block 13`; the sync will write that as a `Facility` column so the dashboard can match recent sales prices by facility. Then use the `GrowFlow Inventory` worksheet gid as the Production dashboard `Inventory GID`.

The main configurable properties are:

```text
GROWFLOW_GRAPHQL_QUERY
GROWFLOW_GRAPHQL_VARIABLES_JSON
GROWFLOW_TARGET_SHEET_NAME
GROWFLOW_PAGINATION_FIELD
GROWFLOW_ARRAY_EXPAND_PATH
GROWFLOW_FACILITY_LABEL
```

`GROWFLOW_PAGINATION_FIELD` should match the GraphQL field with `totalCount` and `items`, such as `orders` or `inventories`. `GROWFLOW_ARRAY_EXPAND_PATH` is optional; for the orders example it is set to `lineItems` so each order line is written with the parent order fields. Access tokens are cached automatically in Script Properties and refreshed before expiry. The sync uses GrowFlow's max page size of 100 and respects `Retry-After` on HTTP 429 rate-limit responses.

## Territory map

The Territory Map tab accepts a store-location CSV/XLSX upload or a Google Sheet with these columns:

```text
License, Store Name, Address, City, State, Zip, Latitude, Longitude
```

Optional columns are `Google Place ID`, `Geocoded At`, and `Geocode Status`. Add `google_maps_api_key` to Streamlit secrets to geocode missing coordinates. Add `google_maps_browser_key` to render the tab with Google Maps in the browser; otherwise the app falls back to an OpenStreetMap-backed Plotly map for already-supplied coordinates.

Retailer-market columns such as `Sales Last Month`, `Sales Rank`, `County`, `Flowers & Prerolls`, `Concentrates & Cartridges`, `Edibles, Topicals, Infused, etc.`, and `UBI` are preserved when present and shown in the Territory Map analytics table.

When no saved locations are present, the app auto-loads locations from the shared Google Sheet tab with gid `1421425539`. Override this with Streamlit secrets:

```toml
territory_location_sheet_url = "https://docs.google.com/spreadsheets/d/..."
territory_location_sheet_gid = "1421425539"
```

The app also loads territory rep assignments from the shared Google Sheet tab with gid `1653796501`. Expected columns are `License`, `Store Name`, `Territory Rep`, and `Territory`; common alternatives such as `License #`, `Store`, `Sales Rep`, `Rep`, `Region`, and `Area` are accepted. License matches are used first, with store-name matching as a fallback.

### Geocoding retailer addresses in Google Sheets

To geocode the retailer address tab directly in Google Sheets:

1. Open the shared Google Sheet.
2. Go to Extensions -> Apps Script.
3. Add a new script file and paste `retailer_geocode.gs`.
4. Run `checkRetailerGeocodeSetup` and confirm the log shows the retailer sheet and a first address.
5. Run `geocodeRetailerAddresses` once and approve permissions.
6. Repeat until all rows have `Latitude` and `Longitude`, or run `createRetailerGeocodeTrigger` to process a batch every 5 minutes.
7. Run `deleteRetailerGeocodeTriggers` when geocoding is complete.

The script targets the retailer tab with gid `1421425539`, geocodes 50 missing-coordinate rows per run, and writes `Latitude`, `Longitude`, `Google Place ID`, `Geocoded At`, and `Geocode Status`.
