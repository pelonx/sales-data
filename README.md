# sales-data
Balaclava Sales Dashboard

## Streamlit contact-log storage

The sales data loads from the shared Google Sheet automatically. The Store Contact Form can also write its team contact log back to Google Sheets when Streamlit secrets are configured.

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

## Territory map

The Territory Map tab accepts a store-location CSV/XLSX upload or a Google Sheet with these columns:

```text
License, Store Name, Address, City, State, Zip, Latitude, Longitude
```

Optional columns are `Google Place ID`, `Geocoded At`, and `Geocode Status`. Add `google_maps_api_key` to Streamlit secrets to geocode missing coordinates. Add `google_maps_browser_key` to render the tab with Google Maps in the browser; otherwise the app falls back to an OpenStreetMap-backed Plotly map for already-supplied coordinates.

Retailer-market columns such as `Sales Last Month`, `Sales Rank`, `County`, `Flowers & Prerolls`, `Concentrates & Cartridges`, `Edibles, Topicals, Infused, etc.`, and `UBI` are preserved when present and shown in the Territory Map analytics table.
