import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
import hashlib
import json
import math
import re
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "sales_dashboard_matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, PageBreak, Image
)
from datetime import datetime, timedelta

st.set_page_config(page_title="Store Sales Dashboard", layout="wide")
# ── Password guard (active when 'password' key exists in secrets) ──────────────
if "password" in st.secrets:
    if not st.session_state.get("authenticated"):
        st.title("Store Sales Dashboard")
        pwd = st.text_input("Password", type="password")
        if st.button("Sign in", type="primary"):
            if pwd == st.secrets["password"]:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

    with st.sidebar:
        if st.button("Sign out", key="sidebar_signout"):
            st.session_state["authenticated"] = False
            st.rerun()
# ───────────────────────────────────────────────────────────────────────────────

# ── Page header ────────────────────────────────────────────────────────────────
_logo_path = Path(__file__).parent / "logo.png"
if _logo_path.exists():
    import base64 as _b64
    _logo_b64 = _b64.b64encode(_logo_path.read_bytes()).decode()
    st.markdown(f"""
<div style="display:flex; align-items:center; gap:10px; margin-bottom:8px">
    <img src="data:image/png;base64,{_logo_b64}" height="60">
    <h1 style="margin:0; line-height:1.2; color:#e3e3d8">Store Sales Dashboard</h1>
</div>""", unsafe_allow_html=True)
else:
    st.markdown("<h1>Store Sales Dashboard</h1>", unsafe_allow_html=True)
st.divider()

BLUE = "#378ADD"
DATA_DIR = Path("Data")
DB_PATH = DATA_DIR / "sales_dashboard.sqlite3"
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw/edit?usp=sharing"
DEFAULT_SHEET_GID = "0"
CONTACT_LOG_WORKSHEET = "Contact Log"
CONTACT_LOG_COLUMNS = [
    "License", "Store Name", "Month", "Revenue",
    "Date Contacted", "Commitment", "Cadence",
    "Committed Amount", "Notes", "Initials",
    "Person Contacted", "Contact Method",
    "Next Outreach", "Next Outreach Date",
    "Alert Recipient", "Alert CC", "Alert Sent Week",
    "Saved At",
]
ALERT_RECIPIENTS = {
    "DK": "danny@balaclavabrands.com",
    "CH": "chris@balaclavabrands.com",
}
ALERT_CC = "geoff@ksavagesupply.com, roger@ksavagesupply.com"
ALERT_OPTIONS = ["", "2 Weeks", "4 Weeks", "Other"]
TERRITORY_BRANDS = ["K. Savage", "Mayfield", "Leisure Land"]
TERRITORY_LOCATION_COLUMNS = [
    "License", "Store Name", "Address", "City", "State", "Zip",
    "Latitude", "Longitude", "Google Place ID", "Geocoded At", "Geocode Status",
    "License Type", "County", "Sales Last Month", "Sales Rank",
    "Flowers & Prerolls", "Concentrates & Cartridges",
    "Edibles, Topicals, Infused, etc.", "UBI",
]
TERRITORY_MAP_COLORS = {
    "Pitch Mayfield": "#7C5CFF",
    "Mayfield placed": "#E8844C",
    "Carries Mayfield": "#E8844C",
    "Maintain K. Savage": "#FF5AA5",
    "Carries K. Savage": "#FF5AA5",
    "K Savage Lapsed": "#FFD23F",
    "Leisure Land Placed": "#89CFF0",
    "K. Savage blocked": "#D84A4A",
    "Open Lane - High Priority": "#006D2C",
    "Open Lane - Medium Priority": "#31A354",
    "Open Lane - Low Priority": "#A1D99B",
    "No recent brand": "#6E7781",
    "Needs location": "#A8ADB3",
}
TERRITORY_SELECTOR_EXCLUDED_CATEGORIES = {"Needs location"}
TOTAL_PATTERN = re.compile(
    r"^(total|totals|sum|grand\s*total|ytd|year\s*to\s*date|annual|avg|average|subtotal)s?$",
    re.IGNORECASE,
)
NON_REVENUE_PATTERN = re.compile(
    r"(drop\s*date|date|notes?|comments?|status|category|type)$",
    re.IGNORECASE,
)
MONTH_PATTERN = re.compile(
    r"^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december|q[1-4])(?:[\s._/-]*\d{2,4})?$",
    re.IGNORECASE,
)
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
MANIFEST_REFERENCE_COLUMNS = [
    "Manifest Reference #",
    "Manifest Reference",
    "Manifest Ref #",
    "Manifest Ref",
    "Manifest #",
    "Manifest Number",
]
MANIFEST_DATE_COLUMNS = [
    "Manifested Date",
    "Transfer Date",
    "Submitted Date",
]
MONTH_NUMS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

# ── helpers ────────────────────────────────────────────────────────────────────

def fmt_usd(n):
    return f"${n:,.0f}"

def pct(n, t):
    return f"{pct_value(n, t):.1f}%"

def pct_value(n, t):
    return n / t * 100 if t else 0.0

def parse_amount(value, strict=True):
    cleaned = re.sub(r"[$,\s]", "", str(value or ""))
    if cleaned.lower() in {"", "nan", "none"}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        if strict:
            raise ValueError(f"Could not parse numeric sales value: {value!r}")
        return 0.0

def first_existing_column(df, candidates):
    if df is None:
        return None
    columns = {str(c).strip().lower(): c for c in getattr(df, "columns", [])}
    for candidate in candidates:
        found = columns.get(str(candidate).strip().lower())
        if found is not None:
            return found
    return None

def clean_reference(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none"}:
        return ""
    try:
        number = float(text)
    except Exception:
        return text
    if number.is_integer():
        return str(int(number))
    return text

def license_match_key(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.lower() in {"", "nan", "none"}:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    text = re.sub(r"^(LICENSE|LIC)\s*#?\s*", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    text = re.sub(r"^(LICENSE|LIC)", "", text)
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text

def store_match_key(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.lower() in {"", "nan", "none"}:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text)

def related_match_key(left, right, min_len=5):
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < min_len:
        return False
    return left in right or right in left

def contact_match_keys(*values):
    keys = set()
    for value in values:
        lic_key = license_match_key(value)
        store_key = store_match_key(value)
        for key in (lic_key, store_key):
            if len(key) >= 5:
                keys.add(key)
        raw = str(value or "").upper()
        for token in re.findall(r"[A-Z]*\d[A-Z0-9]*", raw):
            key = license_match_key(token)
            if len(key) >= 5:
                keys.add(key)
    return keys

def normalize_year(year_text):
    year = int(year_text)
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year

def parse_month_header(header):
    text = re.sub(r"\s+", " ", str(header).strip())
    text = re.sub(r"(\d{2,4})\.\d+$", r"\1", text)
    month_words = "|".join(MONTH_NUMS)
    patterns = [
        rf"^({month_words})[\s._/-]+(\d{{2,4}})$",
        rf"^(\d{{2,4}})[\s._/-]+({month_words})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        first, second = match.groups()
        if first.lower() in MONTH_NUMS:
            return MONTH_NUMS[first.lower()], normalize_year(second)
        return MONTH_NUMS[second.lower()], normalize_year(first)
    return None

def normalize_month_headers(headers):
    normalized = [str(h).strip() for h in headers]
    parsed = []
    for i, header in enumerate(normalized):
        parsed_header = parse_month_header(header)
        if parsed_header:
            month_num, year = parsed_header
            parsed.append((i, month_num, year))

    if not parsed:
        return normalized

    month_seq = [month_num for _, month_num, _ in parsed]
    years = {year for _, _, year in parsed}
    block_count = len(parsed) // 12
    if (
        block_count > 1
        and len(parsed) % 12 == 0
        and month_seq == list(range(1, 13)) * block_count
        and len(years) == 1
    ):
        end_year = next(iter(years))
        start_year = end_year - block_count + 1
        for order, (idx, month_num, _) in enumerate(parsed):
            normalized[idx] = f"{MONTH_ABBR[month_num]} {start_year + (order // 12)}"
        return normalized

    for idx, month_num, year in parsed:
        normalized[idx] = f"{MONTH_ABBR[month_num]} {year}"
    return normalized

def canonical_month_label(label):
    parsed = parse_month_header(label)
    if not parsed:
        return str(label or "").strip()
    month_num, year = parsed
    return f"{MONTH_ABBR[month_num]} {year}"

def sheet_id_from_url(sheet_url):
    parsed = urlparse(str(sheet_url or ""))
    match = SHEET_ID_PATTERN.search(parsed.path)
    return match.group(1) if match else ""

def secret_value(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

def service_account_info():
    for key in ("gcp_service_account", "google_service_account"):
        try:
            if key in st.secrets:
                info = dict(st.secrets[key])
                if "private_key" in info:
                    info["private_key"] = str(info["private_key"]).replace("\\n", "\n")
                return info
        except Exception:
            pass
    return None

def oauth_info():
    for key in ("google_oauth", "gcp_oauth"):
        try:
            if key in st.secrets:
                info = dict(st.secrets[key])
                required = ("client_id", "client_secret", "refresh_token")
                if all(str(info.get(k, "")).strip() for k in required):
                    return info
        except Exception:
            pass
    return None

def contact_auth_mode():
    if service_account_info() is not None:
        return "service_account"
    if oauth_info() is not None:
        return "oauth"
    return None

def contact_sheet_configured():
    return contact_auth_mode() is not None

def contact_sheet_id():
    configured = secret_value("contact_log_spreadsheet_id") or secret_value("contact_log_sheet_id")
    return configured or sheet_id_from_url(DEFAULT_SHEET_URL)

def contact_worksheet_name():
    return secret_value("contact_log_worksheet", CONTACT_LOG_WORKSHEET) or CONTACT_LOG_WORKSHEET

def is_totals_col(header, values, other_cols):
    header_text = str(header).strip()
    if TOTAL_PATTERN.match(header_text):
        return True
    if NON_REVENUE_PATTERN.search(header_text):
        return True
    if MONTH_PATTERN.match(header_text):
        return False
    if len(other_cols) < 2:
        return False
    if not any(abs(v) > 0.01 for v in values):
        return False
    row_sums = [sum(c[i] for c in other_cols) for i in range(len(values))]
    return all(
        abs(v - row_sums[i]) <= max(0.01, abs(row_sums[i]) * 0.001)
        for i, v in enumerate(values)
    )

def parse_input(text):
    """Parse tab-separated text. Returns (df, months, stripped_cols) or raises."""
    rows = [r.split("\t") for r in text.strip().splitlines()]
    if len(rows) < 2:
        raise ValueError("Need at least a header row and one data row.")
    headers = [h.strip() for h in rows[0]]
    if len(headers) < 3:
        raise ValueError("Expected at least 3 columns: License, Store Name, and month columns.")

    raw_month_headers = normalize_month_headers(headers[2:])
    data_rows = rows[1:]

    col_arrays = []
    for j in range(len(raw_month_headers)):
        arr = []
        for r in data_rows:
            val = r[j + 2] if j + 2 < len(r) else "0"
            arr.append(parse_amount(val, strict=False))
        col_arrays.append(arr)

    stripped = []
    keep_indices = []
    for j, h in enumerate(raw_month_headers):
        others = [col_arrays[k] for k in range(len(col_arrays)) if k != j]
        if is_totals_col(h, col_arrays[j], others):
            stripped.append(h)
        else:
            keep_indices.append(j)

    months = [raw_month_headers[j] for j in keep_indices]
    if not months:
        raise ValueError("No month columns found after stripping totals columns.")

    records = []
    for r in data_rows:
        lic = r[0].strip() if len(r) > 0 else ""
        name = r[1].strip() if len(r) > 1 else lic
        if not lic:
            continue
        row = {"License": lic, "Store Name": name or lic}
        for ki, j in enumerate(keep_indices):
            val = r[j + 2] if j + 2 < len(r) else "0"
            row[months[ki]] = parse_amount(val)
        records.append(row)

    if not records:
        raise ValueError("No store rows found.")

    # Check for exact duplicate rows before aggregation
    _seen_rows: set = set()
    exact_dup_ids: list = []
    deduped_records = []
    for rec in records:
        _key = tuple(rec.get(k) for k in ["License", "Store Name"] + months)
        if _key in _seen_rows:
            exact_dup_ids.append(f"{rec['License']} · {rec['Store Name']}")
        else:
            _seen_rows.add(_key)
            deduped_records.append(rec)
    records = deduped_records

    # Aggregate duplicate licenses: sum revenue columns, combine distinct store names
    agg: dict = {}
    for rec in records:
        lic = rec["License"]
        if lic not in agg:
            agg[lic] = rec.copy()
        else:
            existing_name = agg[lic]["Store Name"]
            new_name = rec["Store Name"]
            if new_name and new_name not in existing_name:
                agg[lic]["Store Name"] = existing_name + " / " + new_name
            for m in months:
                agg[lic][m] = agg[lic].get(m, 0) + rec.get(m, 0)

    df = pd.DataFrame(list(agg.values())).set_index("License")
    return df, months, stripped, exact_dup_ids

def google_sheet_csv_url(sheet_url, gid="0"):
    sheet_url = sheet_url.strip()
    if not sheet_url:
        raise ValueError("Enter a Google Sheets URL.")

    parsed = urlparse(sheet_url)
    qs = parse_qs(parsed.query)
    fragment_qs = parse_qs(parsed.fragment)
    url_gid = (
        qs.get("gid", [None])[0]
        or fragment_qs.get("gid", [None])[0]
        or str(gid or "0").strip()
        or "0"
    )

    if "docs.google.com" not in parsed.netloc:
        if sheet_url.lower().endswith(".csv"):
            return sheet_url
        raise ValueError("Enter a Google Sheets share URL or a direct CSV URL.")

    if "/pub" in parsed.path and qs.get("output", [""])[0].lower() == "csv":
        return sheet_url

    match = SHEET_ID_PATTERN.search(parsed.path)
    if not match:
        raise ValueError("Could not find the spreadsheet ID in that Google Sheets URL.")

    sheet_id = match.group(1)
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    return base if url_gid in ("0", "", None) else f"{base}&gid={url_gid}"

_SHEET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

def _fetch_sheet_csv(csv_url: str) -> str:
    """Fetch a Google Sheets CSV URL. Falls back to /pub format if /export returns 4xx."""
    import requests as _req
    resp = _req.get(csv_url, headers=_SHEET_HEADERS, allow_redirects=True, timeout=15)
    if resp.status_code == 200:
        return resp.text
    # Auto-convert export URL → pub URL and retry
    if "/export?" in csv_url:
        qs_part = csv_url.split("/export?", 1)[1]
        gid_val = None
        for part in qs_part.split("&"):
            if part.startswith("gid="):
                gid_val = part[4:]
        base_pub = csv_url.split("/export?")[0] + "/pub?single=true&output=csv"
        pub_url = base_pub if not gid_val or gid_val == "0" else f"{base_pub}&gid={gid_val}"
        resp2 = _req.get(pub_url, headers=_SHEET_HEADERS, allow_redirects=True, timeout=15)
        if resp2.status_code == 200:
            return resp2.text
    raise ValueError(
        f"Google returned HTTP {resp.status_code}. "
        f"Make sure the sheet is shared as 'Anyone with the link can view', or use a "
        f"Publish-to-web CSV URL: File → Share → Publish to web → choose sheet → CSV → Publish. "
        f"(URL tried: {csv_url})"
    )

@st.cache_data(ttl=300, show_spinner=False)
def load_google_sheet_as_tsv(sheet_url, gid="0"):
    from io import StringIO as _StringIO
    csv_url = google_sheet_csv_url(sheet_url, gid)
    text = _fetch_sheet_csv(csv_url)
    sheet_df = pd.read_csv(_StringIO(text)).dropna(how="all").dropna(axis=1, how="all")
    if sheet_df.empty:
        raise ValueError("The selected sheet is empty.")
    if len(sheet_df.columns) < 3:
        raise ValueError("Expected at least 3 columns: License, Store Name, and month columns.")
    sheet_df.columns = [str(c).strip() for c in sheet_df.columns]
    return sheet_df.to_csv(sep="\t", index=False).strip(), csv_url, sheet_df.shape

def load_sheet_into_session(sheet_url, gid, clear_cache=False):
    if clear_cache:
        load_google_sheet_as_tsv.clear()
    sheet_text, _, sheet_shape = load_google_sheet_as_tsv(sheet_url, gid)
    parse_input(sheet_text)
    st.session_state.raw_input = sheet_text
    st.session_state.data_source_label = f"Google Sheet · {sheet_shape[0]} rows · {sheet_shape[1]} columns"
    return sheet_shape

def compute_pareto(df, months, threshold=0.80):
    totals = df[months].sum(axis=1).sort_values(ascending=False)
    grand = totals.sum()
    if grand <= 0:
        return totals.index.tolist(), grand
    cum = 0
    top_lics = []
    for lic, val in totals.items():
        top_lics.append(lic)
        cum += val
        if cum / grand >= threshold:
            break
    return top_lics, grand

def find_last_month_col(months):
    """Return the column name matching the previous calendar month, or the last column."""
    today = datetime.now()
    prev_month = today.month - 1 if today.month > 1 else 12
    abbrevs = {
        1: ["jan"], 2: ["feb"], 3: ["mar"], 4: ["apr"],
        5: ["may"], 6: ["jun"], 7: ["jul"], 8: ["aug"],
        9: ["sep", "sept"], 10: ["oct"], 11: ["nov"], 12: ["dec"],
    }
    targets = abbrevs[prev_month]
    for col in reversed(months):
        if any(t in col.lower() for t in targets):
            return col
    return months[-1]

def last_n_month_cols(months, n=3):
    """Return up to the last n columns ending at the previous calendar month."""
    anchor = find_last_month_col(months)
    idx = months.index(anchor)
    return months[max(0, idx - n + 1): idx + 1]

def month_col_to_ts(col):
    import calendar as _cal
    for fmt in ("%b %y", "%b %Y", "%B %y", "%B %Y", "%b-%y", "%b-%Y"):
        try:
            dt = pd.to_datetime(str(col).strip(), format=fmt)
            last = _cal.monthrange(dt.year, dt.month)[1]
            return pd.Timestamp(dt.year, dt.month, last)
        except Exception:
            pass
    return None

def find_latest_populated_month_col(df, months, target_month=None):
    """Return target month when populated, otherwise the latest populated month before it."""
    if not months:
        return None
    target_month = target_month if target_month in months else find_last_month_col(months)

    def month_total(month):
        return pd.to_numeric(df[month], errors="coerce").fillna(0).sum()

    if target_month in months and month_total(target_month) > 0.01:
        return target_month

    target_ts = month_col_to_ts(target_month)
    candidates = []
    for month in months:
        month_ts = month_col_to_ts(month)
        if target_ts is None or month_ts is None or month_ts <= target_ts:
            candidates.append(month)

    for month in reversed(candidates or months):
        if month_total(month) > 0.01:
            return month
    return target_month

def contact_status_by_license(contact_log_df):
    status_map = {}
    if contact_log_df is None or contact_log_df.empty:
        return status_map

    contact_sort = pd.to_datetime(
        contact_log_df.get("Saved At", pd.Series(dtype=str)),
        errors="coerce",
    )
    contact_log_df = contact_log_df.assign(_saved_sort=contact_sort).sort_values("_saved_sort")
    for _, row in contact_log_df.iterrows():
        lic_key = license_match_key(row.get("License", ""))
        if not lic_key:
            continue
        commitment = str(row.get("Commitment", "")).strip().lower()
        has_contact_details = any(
            str(row.get(field, "")).strip()
            for field in ["Date Contacted", "Notes", "Initials", "Person Contacted", "Contact Method"]
        )
        if commitment in {"yes", "y", "true", "1"}:
            status_map[lic_key] = "Committed"
        elif has_contact_details and commitment in {"no", "n", "false", "0"}:
            status_map[lic_key] = "Contacted - No Commitment"
        elif has_contact_details:
            status_map[lic_key] = "Contacted"
        else:
            status_map[lic_key] = "Not Contacted"
    return status_map

def build_lapsed_store_df(df, months, contact_log_df=None):
    month_ts_map = {m: month_col_to_ts(m) for m in months}
    dated_months = sorted(
        [(m, ts) for m, ts in month_ts_map.items() if ts is not None],
        key=lambda x: x[1],
    )
    contact_status_map = contact_status_by_license(contact_log_df)

    rows = []
    for lic in df.index:
        active_months = []
        for month, ts in dated_months:
            month_revenue = float(df.loc[lic, month])
            if month_revenue > 0:
                active_months.append((month, ts, month_revenue))
        if not active_months:
            continue

        last_month, last_ts, last_month_revenue = active_months[-1]
        recent_active_revenues = [v for _, _, v in active_months[-3:]]
        monthly_run_rate = (
            sum(recent_active_revenues) / len(recent_active_revenues)
            if recent_active_revenues else 0
        )
        rows.append({
            "Store": df.loc[lic, "Store Name"],
            "License": str(lic),
            "Last_Active": last_ts,
            "Last_Active_Label": last_month,
            "Last_Month_Revenue": last_month_revenue,
            "Monthly_Run_Rate": monthly_run_rate,
            "Active_Months": len(active_months),
            "Contact_Status": contact_status_map.get(license_match_key(lic), "Not Contacted"),
            "Revenue": df.loc[lic, months].sum(),
        })
    return pd.DataFrame(
        rows if rows else [],
        columns=[
            "Store", "License", "Last_Active", "Last_Active_Label",
            "Last_Month_Revenue", "Monthly_Run_Rate", "Active_Months",
            "Contact_Status", "Revenue",
        ],
    )

def filter_lapsed_store_df(lapsed_totals, days=180, today=None):
    if lapsed_totals is None or lapsed_totals.empty:
        return pd.DataFrame(columns=list(lapsed_totals.columns) + ["Days_Inactive"] if lapsed_totals is not None else [])

    today = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.now().normalize()
    window_start = today - pd.Timedelta(days=int(days))
    lapse_cutoff = today - pd.Timedelta(days=30)
    lapsed_df = lapsed_totals[
        lapsed_totals["Last_Active"].notna()
        & (lapsed_totals["Last_Active"] >= window_start)
        & (lapsed_totals["Last_Active"] < lapse_cutoff)
    ].copy()
    if lapsed_df.empty:
        lapsed_df["Days_Inactive"] = []
        return lapsed_df

    lapsed_df["Last_Active"] = pd.to_datetime(lapsed_df["Last_Active"], errors="coerce")
    lapsed_df["Days_Inactive"] = (today - lapsed_df["Last_Active"]).dt.days
    for col in ["Revenue", "Monthly_Run_Rate", "Last_Month_Revenue"]:
        lapsed_df[col] = pd.to_numeric(lapsed_df[col], errors="coerce").fillna(0)
    return lapsed_df.sort_values(
        ["Monthly_Run_Rate", "Days_Inactive", "Revenue"],
        ascending=[False, False, False],
    )

SORT_OPTIONS = ["Highest first", "Lowest first", "Store name", "License #"]

def sort_share_rows(share_df, revenue_col, sort_by):
    if sort_by == "Highest first":
        return share_df.sort_values(revenue_col, ascending=False)
    if sort_by == "Lowest first":
        return share_df.sort_values(revenue_col, ascending=True)
    if sort_by == "Store name":
        return share_df.sort_values("Store Name")
    if sort_by == "License #":
        return share_df.sort_index()
    return share_df

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "report"

def _enrich_order_df(odf: pd.DataFrame) -> pd.DataFrame:
    def _brand(sub):
        s = str(sub).strip()
        if s.startswith("LL"):   return "Leisure Land"
        if s.startswith("MF"):   return "Mayfield"
        if s.startswith("KS"):   return "K. Savage"
        if s.startswith("Bulk"): return "Bulk"
        return "Other"
    odf = odf.copy()
    odf["Brand"] = odf["Sub Product Line"].apply(_brand)
    for date_col in ("Submitted Date", "Manifested Date", "Transfer Date", "Estimated delivery date"):
        if date_col in odf.columns:
            odf[date_col] = pd.to_datetime(odf[date_col], errors="coerce")
    if "License #" in odf.columns:
        odf["License #"] = odf["License #"].apply(
            lambda x: str(int(float(x))) if pd.notna(x) and str(x).strip() not in ("", "nan") else ""
        )
    return odf

def parse_orders(file_obj) -> pd.DataFrame:
    name = getattr(file_obj, "name", "")
    if name.lower().endswith(".csv"):
        raw = pd.read_csv(file_obj)
    else:
        raw = pd.read_excel(file_obj, engine="openpyxl")
    return _enrich_order_df(raw)

@st.cache_data(ttl=300, show_spinner=False)
def load_order_sheet_as_df(sheet_url, gid="0"):
    from io import StringIO as _StringIO
    csv_url = google_sheet_csv_url(sheet_url, gid)
    text = _fetch_sheet_csv(csv_url)
    raw = pd.read_csv(_StringIO(text)).dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        raise ValueError("The order sheet is empty.")
    return raw, raw.shape

def load_order_sheet_into_session(sheet_url, gid, clear_cache=False):
    if clear_cache:
        load_order_sheet_as_df.clear()
    raw, shape = load_order_sheet_as_df(sheet_url, gid)
    st.session_state["order_df"] = _enrich_order_df(raw)
    st.session_state["order_data_label"] = f"Google Sheet · {shape[0]} rows · {shape[1]} columns"
    return shape

def storage_path():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DB_PATH

def init_storage():
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                raw_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license TEXT NOT NULL,
                store_name TEXT NOT NULL,
                contact_month TEXT NOT NULL,
                revenue TEXT,
                date_contacted TEXT,
                commitment_made TEXT,
                committed_cadence TEXT,
                cadence_notes TEXT,
                committed_amount TEXT,
                notes TEXT,
                initials TEXT,
                person_contacted TEXT,
                contact_method TEXT,
                saved_at TEXT NOT NULL,
                UNIQUE(license, contact_month)
            )
        """)
        for col in (
            "initials TEXT", "person_contacted TEXT", "contact_method TEXT",
            "next_outreach TEXT", "next_outreach_date TEXT",
            "alert_recipient TEXT", "alert_cc TEXT", "alert_sent_week TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE contact_log ADD COLUMN {col}")
            except Exception:
                pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_locations (
                license_key TEXT PRIMARY KEY,
                license TEXT NOT NULL,
                store_name TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                zip TEXT,
                latitude REAL,
                longitude REAL,
                google_place_id TEXT,
                geocoded_at TEXT,
                geocode_status TEXT,
                license_type TEXT,
                county TEXT,
                sales_last_month TEXT,
                sales_rank TEXT,
                flowers_prerolls TEXT,
                concentrates_cartridges TEXT,
                edibles_topicals_infused TEXT,
                ubi TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        for col in (
            "license_type TEXT", "county TEXT", "sales_last_month TEXT", "sales_rank TEXT",
            "flowers_prerolls TEXT", "concentrates_cartridges TEXT",
            "edibles_topicals_infused TEXT", "ubi TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE store_locations ADD COLUMN {col}")
            except Exception:
                pass

def get_setting(key: str, default: str = "") -> str:
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default

def set_setting(key: str, value: str):
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

def _territory_clean_cell(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none"} else text

def _first_source_col(df, aliases):
    source_cols = {str(c).strip().lower(): c for c in getattr(df, "columns", [])}
    for alias in aliases:
        found = source_cols.get(str(alias).strip().lower())
        if found is not None:
            return found
    return None

def _coerce_coord(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none"}:
        return None
    try:
        return float(text)
    except Exception:
        return None

def normalize_store_locations(raw_df):
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=TERRITORY_LOCATION_COLUMNS)

    aliases = {
        "License": ["License", "License #", "license", "license_number"],
        "Store Name": ["Store Name", "Store", "Client", "Retailer", "Account", "Business Name"],
        "Address": ["Address", "Street Address", "Address 1", "Line 1", "Street"],
        "City": ["City", "Town"],
        "State": ["State", "Province", "Region"],
        "Zip": ["Zip", "ZIP", "Zip Code", "Postal Code"],
        "Latitude": ["Latitude", "Lat", "lat"],
        "Longitude": ["Longitude", "Lng", "Lon", "Long", "longitude", "lng", "lon"],
        "Google Place ID": ["Google Place ID", "Place ID", "place_id", "google_place_id"],
        "Geocoded At": ["Geocoded At", "geocoded_at"],
        "Geocode Status": ["Geocode Status", "geocode_status", "Status"],
        "License Type": ["License Type", "license_type"],
        "County": ["County"],
        "Sales Last Month": ["Sales Last Month", "Market Sales Last Month", "Monthly Sales"],
        "Sales Rank": ["Sales Rank", "Rank"],
        "Flowers & Prerolls": ["Flowers & Prerolls", "Flower Rank", "Flowers and Prerolls"],
        "Concentrates & Cartridges": ["Concentrates & Cartridges", "Concentrate Rank"],
        "Edibles, Topicals, Infused, etc.": [
            "Edibles, Topicals, Infused, etc.", "Edibles Rank", "Topicals Rank", "Infused Rank",
        ],
        "UBI": ["UBI"],
    }

    out = pd.DataFrame(index=raw_df.index)
    for target, source_names in aliases.items():
        source = _first_source_col(raw_df, source_names)
        out[target] = raw_df[source] if source is not None else ""

    for col in TERRITORY_LOCATION_COLUMNS:
        if col not in {"Latitude", "Longitude"}:
            out[col] = out[col].apply(_territory_clean_cell)
    out["Latitude"] = out["Latitude"].apply(_coerce_coord)
    out["Longitude"] = out["Longitude"].apply(_coerce_coord)
    out["License"] = out["License"].apply(clean_reference)
    out["Store Name"] = out["Store Name"].where(out["Store Name"].str.strip().ne(""), out["License"])
    out["_license_key"] = out["License"].apply(license_match_key)
    out = out[out["_license_key"].ne("")]
    out = out.drop_duplicates("_license_key", keep="last").drop(columns=["_license_key"])
    return out[TERRITORY_LOCATION_COLUMNS].reset_index(drop=True)

def read_store_location_file(file_obj):
    name = getattr(file_obj, "name", "")
    if name.lower().endswith(".csv"):
        raw = pd.read_csv(file_obj)
    else:
        raw = pd.read_excel(file_obj, engine="openpyxl")
    return normalize_store_locations(raw)

@st.cache_data(ttl=300, show_spinner=False)
def load_location_sheet_as_df(sheet_url, gid="0"):
    from io import StringIO as _StringIO
    csv_url = google_sheet_csv_url(sheet_url, gid)
    text = _fetch_sheet_csv(csv_url)
    raw = pd.read_csv(_StringIO(text)).dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        raise ValueError("The location sheet is empty.")
    return normalize_store_locations(raw), raw.shape

@st.cache_data(ttl=60, show_spinner=False)
def load_store_locations():
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        cutoff = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        conn.execute("""
            UPDATE store_locations
            SET latitude = NULL,
                longitude = NULL,
                geocode_status = 'Google geocode expired; refresh required'
            WHERE COALESCE(google_place_id, '') <> ''
              AND COALESCE(geocoded_at, '') <> ''
              AND geocoded_at < ?
        """, (cutoff,))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT license, store_name, address, city, state, zip,
                   latitude, longitude, google_place_id, geocoded_at, geocode_status,
                   license_type, county, sales_last_month, sales_rank,
                   flowers_prerolls, concentrates_cartridges,
                   edibles_topicals_infused, ubi
            FROM store_locations
            ORDER BY store_name COLLATE NOCASE, license
        """).fetchall()
    if not rows:
        return pd.DataFrame(columns=TERRITORY_LOCATION_COLUMNS)
    frame = pd.DataFrame([dict(row) for row in rows]).rename(columns={
        "license": "License",
        "store_name": "Store Name",
        "address": "Address",
        "city": "City",
        "state": "State",
        "zip": "Zip",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "google_place_id": "Google Place ID",
        "geocoded_at": "Geocoded At",
        "geocode_status": "Geocode Status",
        "license_type": "License Type",
        "county": "County",
        "sales_last_month": "Sales Last Month",
        "sales_rank": "Sales Rank",
        "flowers_prerolls": "Flowers & Prerolls",
        "concentrates_cartridges": "Concentrates & Cartridges",
        "edibles_topicals_infused": "Edibles, Topicals, Infused, etc.",
        "ubi": "UBI",
    })
    return normalize_store_locations(frame)

def save_store_locations(locations_df):
    normalized = normalize_store_locations(locations_df)
    now = datetime.now().isoformat(timespec="seconds")
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("DELETE FROM store_locations")
        conn.executemany("""
            INSERT INTO store_locations
                (license_key, license, store_name, address, city, state, zip,
                 latitude, longitude, google_place_id, geocoded_at, geocode_status,
                 license_type, county, sales_last_month, sales_rank,
                 flowers_prerolls, concentrates_cartridges,
                 edibles_topicals_infused, ubi, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                license_match_key(row["License"]), row["License"], row["Store Name"],
                row["Address"], row["City"], row["State"], row["Zip"],
                row["Latitude"], row["Longitude"], row["Google Place ID"],
                row["Geocoded At"], row["Geocode Status"],
                row["License Type"], row["County"], row["Sales Last Month"], row["Sales Rank"],
                row["Flowers & Prerolls"], row["Concentrates & Cartridges"],
                row["Edibles, Topicals, Infused, etc."], row["UBI"], now,
            )
            for _, row in normalized.iterrows()
        ])
    load_store_locations.clear()
    return len(normalized)

def clear_store_locations():
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("DELETE FROM store_locations")
    load_store_locations.clear()

def google_maps_server_key():
    return (
        secret_value("google_maps_api_key")
        or secret_value("GOOGLE_MAPS_API_KEY")
        or secret_value("google_maps_server_key")
    )

def google_maps_browser_key():
    return (
        secret_value("google_maps_browser_key")
        or secret_value("GOOGLE_MAPS_BROWSER_KEY")
        or google_maps_server_key()
    )

def location_address_query(row):
    address = str(row.get("Address", "")).strip()
    if address and re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", address):
        return address
    parts = [row.get("Address", ""), row.get("City", ""), row.get("State", ""), row.get("Zip", "")]
    return ", ".join([str(p).strip() for p in parts if str(p).strip()])

def parse_market_sales(value):
    return parse_amount(value, strict=False)

def geocode_store_locations(locations_df, api_key, limit=25):
    import requests as _req

    updated = normalize_store_locations(locations_df).copy()
    limit = int(limit or 0)
    successes = 0
    attempted = 0
    for idx, row in updated.iterrows():
        if attempted >= limit:
            break
        if pd.notna(row.get("Latitude")) and pd.notna(row.get("Longitude")):
            continue
        address = location_address_query(row)
        if not row.get("Address") or not address:
            updated.at[idx, "Geocode Status"] = "Missing street address"
            continue

        attempted += 1
        try:
            resp = _req.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": api_key},
                timeout=15,
            )
            data = resp.json()
        except Exception as exc:
            updated.at[idx, "Geocode Status"] = f"Request failed: {exc}"
            continue

        status = data.get("status", "UNKNOWN")
        if status == "OK" and data.get("results"):
            result = data["results"][0]
            loc = result.get("geometry", {}).get("location", {})
            updated.at[idx, "Latitude"] = _coerce_coord(loc.get("lat"))
            updated.at[idx, "Longitude"] = _coerce_coord(loc.get("lng"))
            updated.at[idx, "Google Place ID"] = result.get("place_id", "")
            updated.at[idx, "Geocoded At"] = datetime.now().isoformat(timespec="seconds")
            updated.at[idx, "Geocode Status"] = "OK"
            successes += 1
        else:
            updated.at[idx, "Geocode Status"] = status
    return updated, {"attempted": attempted, "successes": successes}

def build_revenue_store_profile(df, months):
    if df is None or df.empty:
        return pd.DataFrame(columns=["License Key", "License", "Revenue Store", "Revenue Total", "Latest Month Revenue"])
    profile = df.reset_index()[["License", "Store Name"]].copy()
    profile["License"] = profile["License"].apply(clean_reference)
    profile["License Key"] = profile["License"].apply(license_match_key)
    profile = profile.rename(columns={"Store Name": "Revenue Store"})
    profile["Revenue Total"] = df[months].sum(axis=1).values if months else 0
    latest_month = find_latest_populated_month_col(df, months) if months else None
    profile["Latest Month Revenue"] = df[latest_month].values if latest_month else 0
    return profile

def build_brand_store_profile(order_df, active_days=120):
    columns = [
        "License Key", "Order Store", "Orders", "Last Order", "Total Units", "Brand Revenue"
    ] + TERRITORY_BRANDS + ["K. Savage Last Order", "K. Savage Historical Revenue"]
    if order_df is None or order_df.empty:
        return pd.DataFrame(columns=columns)

    odf = order_df.copy()
    required = {"License #", "Client", "Brand", "Line Total", "Units", "Order #", "Submitted Date"}
    if not required.issubset(set(odf.columns)):
        return pd.DataFrame(columns=columns)

    odf = odf[odf["Brand"].isin(TERRITORY_BRANDS)].copy()
    odf["Line Total"] = pd.to_numeric(odf["Line Total"], errors="coerce").fillna(0)
    odf["Units"] = pd.to_numeric(odf["Units"], errors="coerce").fillna(0)
    odf["Submitted Date"] = pd.to_datetime(odf["Submitted Date"], errors="coerce")
    odf = odf[(odf["Line Total"] > 0) & odf["Submitted Date"].notna()]
    if odf.empty:
        return pd.DataFrame(columns=columns)

    odf["License"] = odf["License #"].apply(clean_reference)
    odf["License Key"] = odf["License"].apply(license_match_key)
    odf = odf[odf["License Key"].ne("")]
    k_savage_history = (
        odf[odf["Brand"].eq("K. Savage")]
        .groupby("License Key")
        .agg(
            K_Savage_Last_Order=("Submitted Date", "max"),
            K_Savage_Historical_Revenue=("Line Total", "sum"),
        )
        .reset_index()
        .rename(columns={
            "K_Savage_Last_Order": "K. Savage Last Order",
            "K_Savage_Historical_Revenue": "K. Savage Historical Revenue",
        })
    )

    as_of = odf["Submitted Date"].max()
    cutoff = as_of - pd.Timedelta(days=int(active_days))
    active_odf = odf[odf["Submitted Date"] >= cutoff].copy()
    if active_odf.empty:
        out = k_savage_history.copy()
        for col in columns:
            if col not in out.columns:
                out[col] = 0
        return out[columns]

    brand_pivot = (
        active_odf.pivot_table(
            index="License Key", columns="Brand", values="Line Total",
            aggfunc="sum", fill_value=0,
        )
        .reset_index()
    )
    brand_pivot.columns.name = None
    for brand in TERRITORY_BRANDS:
        if brand not in brand_pivot.columns:
            brand_pivot[brand] = 0

    totals = (
        active_odf.groupby("License Key")
        .agg(
            Orders=("Order #", "nunique"),
            Last_Order=("Submitted Date", "max"),
            Total_Units=("Units", "sum"),
            Brand_Revenue=("Line Total", "sum"),
        )
        .reset_index()
    )
    names = (
        active_odf.sort_values("Submitted Date")
        .drop_duplicates("License Key", keep="last")[["License Key", "Client"]]
        .rename(columns={"Client": "Order Store"})
    )
    out = totals.merge(names, on="License Key", how="left").merge(brand_pivot, on="License Key", how="left")
    out = out.merge(k_savage_history, on="License Key", how="outer")
    out = out.rename(columns={
        "Last_Order": "Last Order",
        "Total_Units": "Total Units",
        "Brand_Revenue": "Brand Revenue",
    })
    return out[columns]

def build_territory_store_table(locations_df, revenue_df, months, order_df, active_days):
    locations = normalize_store_locations(locations_df)
    if locations.empty:
        return pd.DataFrame()
    locations = locations.copy()
    locations["License Key"] = locations["License"].apply(license_match_key)
    revenue_profile = build_revenue_store_profile(revenue_df, months)
    brand_profile = build_brand_store_profile(order_df, active_days)

    stores = locations.merge(revenue_profile, on="License Key", how="left", suffixes=("", "_Revenue"))
    stores = stores.merge(brand_profile, on="License Key", how="left")
    stores["License"] = stores["License"].combine_first(stores.get("License_Revenue", ""))
    store_names = stores["Store Name"].fillna("").astype(str)
    if "Revenue Store" in stores:
        store_names = store_names.where(store_names.str.strip().ne(""), stores["Revenue Store"].fillna("").astype(str))
    if "Order Store" in stores:
        store_names = store_names.where(store_names.str.strip().ne(""), stores["Order Store"].fillna("").astype(str))
    stores["Store Name"] = store_names.where(store_names.str.strip().ne(""), stores["License"].astype(str))
    for brand in TERRITORY_BRANDS:
        stores[brand] = pd.to_numeric(stores.get(brand, 0), errors="coerce").fillna(0)
        stores[f"Carries {brand}"] = stores[brand] > 0
    stores["Orders"] = pd.to_numeric(stores.get("Orders", 0), errors="coerce").fillna(0).astype(int)
    stores["Total Units"] = pd.to_numeric(stores.get("Total Units", 0), errors="coerce").fillna(0)
    stores["Brand Revenue"] = pd.to_numeric(stores.get("Brand Revenue", 0), errors="coerce").fillna(0)
    stores["Revenue Total"] = pd.to_numeric(stores.get("Revenue Total", 0), errors="coerce").fillna(0)
    stores["Latest Month Revenue"] = pd.to_numeric(stores.get("Latest Month Revenue", 0), errors="coerce").fillna(0)
    stores["K. Savage Last Order"] = pd.to_datetime(stores.get("K. Savage Last Order"), errors="coerce")
    stores["K. Savage Historical Revenue"] = pd.to_numeric(
        stores.get("K. Savage Historical Revenue", 0),
        errors="coerce",
    ).fillna(0)
    stores["K Savage Lapsed"] = (stores["K. Savage Historical Revenue"] > 0) & (~stores["Carries K. Savage"])
    stores["Market Sales Last Month"] = stores["Sales Last Month"].apply(parse_market_sales)
    stores["Active Brands"] = stores.apply(
        lambda r: ", ".join([brand for brand in TERRITORY_BRANDS if r.get(f"Carries {brand}", False)]) or "None",
        axis=1,
    )
    return stores

def haversine_miles(lat1, lon1, lat2, lon2):
    radius = 3958.7613
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def territory_recommendation(row):
    if pd.isna(row.get("Latitude")) or pd.isna(row.get("Longitude")):
        return "Needs location"
    if row.get("K Savage Lapsed", False):
        return "K Savage Lapsed"
    if row.get("Carries Mayfield", False):
        return "Mayfield placed"
    if row.get("Nearby K. Savage", 0) > 0 and row.get("Nearby Mayfield", 0) == 0:
        return "Pitch Mayfield"
    if row.get("Nearby K. Savage", 0) > 0 and not row.get("Carries K. Savage", False):
        return "K. Savage blocked"
    if row.get("Carries K. Savage", False):
        return "Maintain K. Savage"
    return "Open lane"

def assign_open_lane_priority(stores):
    stores["Priority Level"] = ""
    stores["Priority Score"] = 0.0
    if stores.empty or "Market Sales Last Month" not in stores:
        return stores

    open_lane = stores["Recommendation"].eq("Open lane")
    if not open_lane.any():
        return stores

    sales = pd.to_numeric(stores.loc[open_lane, "Market Sales Last Month"], errors="coerce").fillna(0)
    if sales.empty:
        return stores

    if len(sales) == 1:
        scores = pd.Series(1.0, index=sales.index)
    else:
        scores = (sales.rank(method="first") - 1) / (len(sales) - 1)
    stores.loc[open_lane, "Priority Score"] = scores
    stores.loc[open_lane & (stores["Priority Score"] >= 0.75), "Priority Level"] = "High"
    stores.loc[
        open_lane
        & stores["Priority Level"].eq("")
        & (stores["Priority Score"] >= 0.40),
        "Priority Level",
    ] = "Medium"
    stores.loc[open_lane & stores["Priority Level"].eq(""), "Priority Level"] = "Low"
    return stores

def territory_map_category(row):
    rec = row.get("Recommendation", "")
    if rec == "Needs location":
        return "Needs location"
    if row.get("Carries K. Savage", False):
        return "Carries K. Savage"
    if rec == "K Savage Lapsed" or row.get("K Savage Lapsed", False):
        return "K Savage Lapsed"
    if row.get("Carries Leisure Land", False):
        return "Leisure Land Placed"
    if rec == "Pitch Mayfield":
        return "Pitch Mayfield"
    if rec == "Mayfield placed":
        return "Mayfield placed"
    if rec == "Maintain K. Savage":
        return "Maintain K. Savage"
    if rec == "Open lane":
        priority = str(row.get("Priority Level", "") or "Low").title()
        return f"Open Lane - {priority} Priority"
    if row.get("Carries Mayfield", False):
        return "Carries Mayfield"
    if rec == "K. Savage blocked":
        return "K. Savage blocked"
    return "No recent brand"

def enrich_territory_proximity(stores_df, radius_miles):
    if stores_df is None or stores_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    stores = stores_df.copy().reset_index(drop=True)
    stores["Nearby Stores"] = 0
    stores["Nearby K. Savage"] = 0
    stores["Nearby Mayfield"] = 0
    stores["Nearest Store"] = ""
    stores["Nearest Distance"] = None
    neighbor_details = {idx: [] for idx in stores.index}
    pair_rows = []

    valid = stores[stores["Latitude"].notna() & stores["Longitude"].notna()]
    valid_indices = valid.index.tolist()
    for pos, i in enumerate(valid_indices):
        left = stores.loc[i]
        for j in valid_indices[pos + 1:]:
            right = stores.loc[j]
            distance = haversine_miles(left["Latitude"], left["Longitude"], right["Latitude"], right["Longitude"])
            if distance > radius_miles:
                continue

            left_brands = left["Active Brands"]
            right_brands = right["Active Brands"]
            pair_rows.append({
                "Store A": left["Store Name"],
                "License A": left["License"],
                "Brands A": left_brands,
                "Store B": right["Store Name"],
                "License B": right["License"],
                "Brands B": right_brands,
                "Distance (mi)": distance,
            })

            for target_idx, neighbor in ((i, right), (j, left)):
                stores.at[target_idx, "Nearby Stores"] += 1
                if neighbor.get("Carries K. Savage", False):
                    stores.at[target_idx, "Nearby K. Savage"] += 1
                if neighbor.get("Carries Mayfield", False):
                    stores.at[target_idx, "Nearby Mayfield"] += 1
                current_nearest = stores.at[target_idx, "Nearest Distance"]
                if current_nearest is None or distance < current_nearest:
                    stores.at[target_idx, "Nearest Distance"] = distance
                    stores.at[target_idx, "Nearest Store"] = neighbor["Store Name"]
                neighbor_details[target_idx].append(
                    f"{neighbor['Store Name']} ({distance:.2f} mi; {neighbor['Active Brands']})"
                )

    stores["Nearby Detail"] = stores.index.map(
        lambda idx: "; ".join(neighbor_details[idx][:4]) if neighbor_details[idx] else ""
    )
    stores["Recommendation"] = stores.apply(territory_recommendation, axis=1)
    stores = assign_open_lane_priority(stores)
    stores["Map Category"] = stores.apply(territory_map_category, axis=1)
    stores["Designation"] = stores["Map Category"]
    pairs = pd.DataFrame(pair_rows).sort_values("Distance (mi)") if pair_rows else pd.DataFrame(
        columns=["Store A", "License A", "Brands A", "Store B", "License B", "Brands B", "Distance (mi)"]
    )
    return stores, pairs

def render_google_territory_map(map_df, height=540):
    key = google_maps_browser_key()
    if not key:
        return False
    points = []
    for _, row in map_df.dropna(subset=["Latitude", "Longitude"]).iterrows():
        points.append({
            "lat": float(row["Latitude"]),
            "lng": float(row["Longitude"]),
            "store": str(row.get("Store Name", "")),
            "license": str(row.get("License", "")),
            "brands": str(row.get("Active Brands", "")),
            "recommendation": str(row.get("Recommendation", "")),
            "priority": str(row.get("Priority Level", "")),
            "marketSales": float(row.get("Market Sales Last Month", 0) or 0),
            "nearby": str(row.get("Nearby Detail", "")),
            "color": TERRITORY_MAP_COLORS.get(row.get("Map Category"), "#6E7781"),
        })
    if not points:
        return False

    html = f"""
    <div id="territory-map" style="height:{height}px;width:100%;border-radius:6px;overflow:hidden"></div>
    <script>
      const territoryPoints = {json.dumps(points)};
      const esc = (value) => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      function initTerritoryMap() {{
        const map = new google.maps.Map(document.getElementById("territory-map"), {{
          center: {{lat: territoryPoints[0].lat, lng: territoryPoints[0].lng}},
          zoom: 11,
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: true
        }});
        const bounds = new google.maps.LatLngBounds();
        const info = new google.maps.InfoWindow();
        territoryPoints.forEach((point) => {{
          const marker = new google.maps.Marker({{
            position: {{lat: point.lat, lng: point.lng}},
            map,
            title: point.store,
            icon: {{
              path: google.maps.SymbolPath.CIRCLE,
              scale: 8,
              fillColor: point.color,
              fillOpacity: 0.95,
              strokeColor: "#ffffff",
              strokeWeight: 2
            }}
          }});
          marker.addListener("click", () => {{
            info.setContent(`
              <div style="font-family:Arial,sans-serif;max-width:280px">
                <div style="font-weight:700;margin-bottom:4px">${{esc(point.store)}}</div>
                <div>License: ${{esc(point.license)}}</div>
                <div>Brands: ${{esc(point.brands)}}</div>
                <div>Recommendation: <b>${{esc(point.recommendation)}}</b></div>
                ${{point.priority ? `<div>Priority: <b>${{esc(point.priority)}}</b></div>` : ""}}
                <div>Market sales: $${{Number(point.marketSales || 0).toLocaleString(undefined, {{maximumFractionDigits: 0}})}}</div>
                ${{point.nearby ? `<div style="margin-top:6px">Nearby: ${{esc(point.nearby)}}</div>` : ""}}
              </div>
            `);
            info.open(map, marker);
          }});
          bounds.extend(marker.getPosition());
        }});
        if (territoryPoints.length > 1) {{
          map.fitBounds(bounds, 60);
        }} else {{
          map.setZoom(14);
        }}
      }}
      window.initTerritoryMap = initTerritoryMap;
    </script>
    <script async defer src="https://maps.googleapis.com/maps/api/js?key={key}&callback=initTerritoryMap"></script>
    """
    components.html(html, height=height + 8)
    return True

def render_plotly_territory_map(map_df):
    plotted = map_df.dropna(subset=["Latitude", "Longitude"]).copy()
    if plotted.empty:
        return False
    fig = px.scatter_mapbox(
        plotted,
        lat="Latitude",
        lon="Longitude",
        color="Map Category",
        color_discrete_map=TERRITORY_MAP_COLORS,
        hover_name="Store Name",
        hover_data={
            "License": True,
            "Active Brands": True,
            "Recommendation": True,
            "Priority Level": True,
            "Market Sales Last Month": ":$,.0f",
            "Nearby K. Savage": True,
            "Nearby Mayfield": True,
            "Latitude": False,
            "Longitude": False,
            "Map Category": False,
        },
        zoom=10,
        height=540,
    )
    fig.update_traces(marker=dict(size=11, opacity=0.9))
    fig.update_layout(
        mapbox_style="open-street-map",
        margin=dict(l=0, r=0, t=0, b=0),
        legend_title=None,
    )
    st.plotly_chart(fig, width="stretch")
    return True

def list_saved_datasets():
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, name, created_at, updated_at
            FROM saved_datasets
            ORDER BY updated_at DESC, name COLLATE NOCASE
        """).fetchall()
    return [dict(row) for row in rows]

def get_saved_dataset(dataset_id):
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        row = conn.execute(
            "SELECT raw_text FROM saved_datasets WHERE id = ?",
            (dataset_id,)
        ).fetchone()
    return row[0] if row else ""

def save_dataset(name, raw_text):
    init_storage()
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("""
            INSERT INTO saved_datasets (name, raw_text, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                raw_text = excluded.raw_text,
                updated_at = excluded.updated_at
        """, (name, raw_text, now, now))

def delete_saved_dataset(dataset_id):
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("DELETE FROM saved_datasets WHERE id = ?", (dataset_id,))

def _clean_cell(value):
    if pd.isna(value):
        return ""
    return str(value).strip()

def _contact_row_from_save_dict(row, saved_at):
    return {
        "License": _clean_cell(row.get("license")),
        "Store Name": _clean_cell(row.get("store_name")),
        "Month": canonical_month_label(row.get("contact_month")),
        "Revenue": _clean_cell(row.get("revenue")),
        "Date Contacted": _clean_cell(row.get("date_contacted")),
        "Commitment": _clean_cell(row.get("commitment_made")),
        "Cadence": _clean_cell(row.get("committed_cadence")),
        "Committed Amount": _clean_cell(row.get("committed_amount")),
        "Notes": _clean_cell(row.get("notes")),
        "Initials": _clean_cell(row.get("initials")),
        "Person Contacted": _clean_cell(row.get("person_contacted")),
        "Contact Method": _clean_cell(row.get("contact_method")),
        "Next Outreach": _clean_cell(row.get("next_outreach")),
        "Next Outreach Date": _clean_cell(row.get("next_outreach_date")),
        "Alert Recipient": _clean_cell(row.get("alert_recipient")),
        "Alert CC": _clean_cell(row.get("alert_cc")),
        "Alert Sent Week": _clean_cell(row.get("alert_sent_week")),
        "Saved At": saved_at,
    }

def _normalize_contact_df(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=CONTACT_LOG_COLUMNS)

    aliases = {
        "License": ["License", "license"],
        "Store Name": ["Store Name", "store_name", "Client", "Store"],
        "Month": ["Month", "contact_month"],
        "Revenue": ["Revenue", "revenue"],
        "Date Contacted": ["Date Contacted", "date_contacted"],
        "Commitment": ["Commitment", "Commitment Made", "commitment_made"],
        "Cadence": ["Cadence", "Committed Cadence", "committed_cadence"],
        "Committed Amount": ["Committed Amount", "Committed $ Amount", "Amount", "committed_amount"],
        "Notes": ["Notes", "notes"],
        "Initials": ["Initials", "initials"],
        "Person Contacted": ["Person Contacted", "person_contacted"],
        "Contact Method": ["Contact Method", "contact_method"],
        "Next Outreach": ["Next Outreach", "Next Outreach Alert", "next_outreach"],
        "Next Outreach Date": ["Next Outreach Date", "Next Outreach Alert Date", "next_outreach_date"],
        "Alert Recipient": ["Alert Recipient", "Alert Email", "alert_recipient"],
        "Alert CC": ["Alert CC", "alert_cc"],
        "Alert Sent Week": ["Alert Sent Week", "alert_sent_week"],
        "Saved At": ["Saved At", "saved_at"],
    }

    out = pd.DataFrame()
    source_cols = {str(c).strip(): c for c in df.columns}
    for target, source_names in aliases.items():
        source = next((source_cols[name] for name in source_names if name in source_cols), None)
        out[target] = df[source] if source is not None else ""

    out = out.fillna("").astype(str)
    out["License"] = out["License"].str.strip()
    out["Store Name"] = out["Store Name"].str.strip()
    out["Month"] = out["Month"].apply(canonical_month_label)
    commitment_values = {
        "y": "Yes", "yes": "Yes", "true": "Yes", "1": "Yes",
        "n": "No", "no": "No", "false": "No", "0": "No",
    }
    out["Commitment"] = out["Commitment"].str.strip().apply(
        lambda v: commitment_values.get(str(v).lower(), str(v).strip())
    )
    out = out[(out["License"] != "") & (out["Store Name"] != "")]
    return out[CONTACT_LOG_COLUMNS]

def _contact_df_to_save_rows(df):
    normalized = _normalize_contact_df(df)
    rows = []
    for _, row in normalized.iterrows():
        rows.append({
            "license": row["License"],
            "store_name": row["Store Name"],
            "contact_month": row["Month"],
            "revenue": row["Revenue"],
            "date_contacted": row["Date Contacted"],
            "commitment_made": row["Commitment"],
            "committed_cadence": row["Cadence"],
            "committed_amount": row["Committed Amount"],
            "notes": row["Notes"],
            "initials": row["Initials"],
            "person_contacted": row["Person Contacted"],
            "contact_method": row["Contact Method"],
            "next_outreach": row["Next Outreach"],
            "next_outreach_date": row["Next Outreach Date"],
            "alert_recipient": row["Alert Recipient"],
            "alert_cc": row["Alert CC"],
            "alert_sent_week": row["Alert Sent Week"],
        })
    return rows

def _meaningful_contact_rows(df):
    normalized = _normalize_contact_df(df)
    if normalized.empty:
        return []
    meaningful = (
        normalized["Commitment"].str.lower().eq("yes")
        | normalized["Cadence"].str.strip().ne("")
        | normalized["Committed Amount"].str.strip().ne("")
        | normalized["Notes"].str.strip().ne("")
        | normalized["Initials"].str.strip().ne("")
        | normalized["Person Contacted"].str.strip().ne("")
        | normalized["Contact Method"].str.strip().ne("")
        | normalized["Next Outreach"].str.strip().ne("")
        | normalized["Next Outreach Date"].str.strip().ne("")
        | normalized["Alert Recipient"].str.strip().ne("")
    )
    return _contact_df_to_save_rows(normalized[meaningful])

def _source_has_any_column(df, names):
    source_cols = {str(c).strip().lower() for c in getattr(df, "columns", [])}
    return any(str(name).strip().lower() in source_cols for name in names)

def _restore_contact_rows(df, import_all=False):
    normalized = _normalize_contact_df(df)
    if normalized.empty:
        return []
    is_team_log_backup = _source_has_any_column(df, ["Saved At", "saved_at"])
    if import_all or is_team_log_backup:
        return _contact_df_to_save_rows(normalized)
    return _meaningful_contact_rows(normalized)

def _contact_log_from_sqlite() -> pd.DataFrame:
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT license, store_name, contact_month, revenue,
                   date_contacted, commitment_made, committed_cadence,
                   committed_amount, notes, initials, person_contacted,
                   contact_method, next_outreach, next_outreach_date,
                   alert_recipient, alert_cc, alert_sent_week, saved_at
            FROM contact_log
            ORDER BY saved_at DESC
        """).fetchall()
    if not rows:
        return pd.DataFrame(columns=CONTACT_LOG_COLUMNS)
    return _normalize_contact_df(pd.DataFrame([dict(r) for r in rows]))

def _upsert_contact_log_sqlite(rows: list[dict]):
    init_storage()
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(storage_path()) as conn:
        conn.executemany("""
            INSERT INTO contact_log
                (license, store_name, contact_month, revenue,
                 date_contacted, commitment_made, committed_cadence,
                 committed_amount, notes, initials, person_contacted,
                 contact_method, next_outreach, next_outreach_date,
                 alert_recipient, alert_cc, alert_sent_week, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(license, contact_month) DO UPDATE SET
                store_name        = excluded.store_name,
                revenue           = excluded.revenue,
                date_contacted    = excluded.date_contacted,
                commitment_made   = excluded.commitment_made,
                committed_cadence = excluded.committed_cadence,
                committed_amount  = excluded.committed_amount,
                notes             = excluded.notes,
                initials          = excluded.initials,
                person_contacted  = excluded.person_contacted,
                contact_method    = excluded.contact_method,
                next_outreach     = excluded.next_outreach,
                next_outreach_date = excluded.next_outreach_date,
                alert_recipient   = excluded.alert_recipient,
                alert_cc          = excluded.alert_cc,
                alert_sent_week   = excluded.alert_sent_week,
                saved_at          = excluded.saved_at
        """, [
            (r["license"], r["store_name"], canonical_month_label(r["contact_month"]), r.get("revenue"),
             r.get("date_contacted"), r.get("commitment_made"), r.get("committed_cadence"),
             r.get("committed_amount"), r.get("notes"), r.get("initials"),
             r.get("person_contacted"), r.get("contact_method"),
             r.get("next_outreach"), r.get("next_outreach_date"),
             r.get("alert_recipient"), r.get("alert_cc"), r.get("alert_sent_week"), now)
            for r in rows
        ])

def _contact_sheet_client():
    try:
        import gspread
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials as OAuthCredentials
        from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    except ImportError as exc:
        raise RuntimeError("Google Sheets contact logging requires gspread and google-auth.") from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    service_info = service_account_info()
    if service_info:
        creds = ServiceAccountCredentials.from_service_account_info(service_info, scopes=scopes)
        return gspread.authorize(creds)

    user_info = oauth_info()
    if user_info:
        token_uri = str(user_info.get("token_uri", "https://oauth2.googleapis.com/token")).strip()
        if token_uri != "https://oauth2.googleapis.com/token":
            raise RuntimeError(
                "Invalid google_oauth.token_uri. Use exactly "
                "'https://oauth2.googleapis.com/token' or remove token_uri from Streamlit secrets."
            )
        creds = OAuthCredentials(
            token=user_info.get("access_token") or user_info.get("token"),
            refresh_token=user_info["refresh_token"],
            token_uri=token_uri,
            client_id=user_info["client_id"],
            client_secret=user_info["client_secret"],
            scopes=scopes,
        )
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise RuntimeError(
                    "Could not refresh Google OAuth credentials. Check google_oauth.client_id, "
                    "client_secret, refresh_token, and token_uri in Streamlit secrets."
                ) from exc
        return gspread.authorize(creds)

    raise RuntimeError("Google Sheets contact logging is not configured in Streamlit secrets.")

def _worksheet_update(worksheet, values):
    try:
        worksheet.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
    except TypeError:
        worksheet.update("A1", values, value_input_option="USER_ENTERED")

@st.cache_resource(show_spinner=False)
def _contact_worksheet():
    client = _contact_sheet_client()
    spreadsheet = client.open_by_key(contact_sheet_id())
    title = contact_worksheet_name()
    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        worksheet = spreadsheet.add_worksheet(title=title, rows=500, cols=len(CONTACT_LOG_COLUMNS))
    values = worksheet.get_all_values()
    if not values:
        _worksheet_update(worksheet, [CONTACT_LOG_COLUMNS])
    return worksheet

def _contact_log_from_sheet() -> pd.DataFrame:
    worksheet = _contact_worksheet()
    values = worksheet.get_all_values()
    if not values or len(values) == 1:
        return pd.DataFrame(columns=CONTACT_LOG_COLUMNS)
    headers = values[0]
    rows = [
        row[:len(headers)] + [""] * max(0, len(headers) - len(row))
        for row in values[1:]
    ]
    return _normalize_contact_df(pd.DataFrame(rows, columns=headers))

def _write_contact_log_sheet(df):
    worksheet = _contact_worksheet()
    normalized = _normalize_contact_df(df)
    rows = normalized.fillna("").astype(str).values.tolist()
    values = [CONTACT_LOG_COLUMNS] + rows
    old_row_count = len(worksheet.get_all_values())
    if old_row_count > len(values):
        values.extend([[""] * len(CONTACT_LOG_COLUMNS) for _ in range(old_row_count - len(values))])
    _worksheet_update(worksheet, values)

def _upsert_contact_log_sheet(rows: list[dict]):
    now = datetime.now().isoformat(timespec="seconds")
    existing = _contact_log_from_sheet()
    incoming = pd.DataFrame([_contact_row_from_save_dict(r, now) for r in rows], columns=CONTACT_LOG_COLUMNS)
    combined = pd.concat([existing, incoming], ignore_index=True)
    combined["Month"] = combined["Month"].apply(canonical_month_label)
    combined["_key"] = combined["License"].astype(str) + "||" + combined["Month"].astype(str)
    combined = combined.drop_duplicates("_key", keep="last").drop(columns=["_key"])
    combined = combined.sort_values("Saved At", ascending=False)
    _write_contact_log_sheet(combined)

def contact_log_backend_label():
    mode = contact_auth_mode()
    if mode == "service_account":
        return f"Google Sheets · {contact_worksheet_name()} · service account"
    if mode == "oauth":
        return f"Google Sheets · {contact_worksheet_name()} · OAuth"
    return "Local SQLite fallback"

def upsert_contact_log_rows(rows: list[dict]):
    """Insert or replace contact log entries, unique per license+month."""
    if contact_sheet_configured():
        result = _upsert_contact_log_sheet(rows)
    else:
        result = _upsert_contact_log_sqlite(rows)
    load_contact_log.clear()
    return result

@st.cache_data(ttl=60, show_spinner=False)
def load_contact_log() -> pd.DataFrame:
    if contact_sheet_configured():
        return _contact_log_from_sheet()
    return _contact_log_from_sqlite()

def delete_contact_log_entry(license_id: str, month: str):
    if contact_sheet_configured():
        current = _contact_log_from_sheet()
        if current.empty:
            return
        key_month = canonical_month_label(month)
        keep = ~(
            (current["License"].astype(str) == str(license_id))
            & (current["Month"].apply(canonical_month_label) == key_month)
        )
        _write_contact_log_sheet(current[keep])
        load_contact_log.clear()
        return
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute(
            "DELETE FROM contact_log WHERE license = ? AND contact_month = ?",
            (license_id, month)
        )
    load_contact_log.clear()

def clear_contact_log():
    if contact_sheet_configured():
        _write_contact_log_sheet(pd.DataFrame(columns=CONTACT_LOG_COLUMNS))
        load_contact_log.clear()
        return
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("DELETE FROM contact_log")
    load_contact_log.clear()

# ── Chart helpers (return PNG BytesIO for embedding in PDF) ───────────────────

CHART_PALETTE = ["#378ADD", "#5BA8E5", "#88C4F0", "#B5D4F4", "#D8EAFC",
                 "#2C6DAF", "#1A4F8A", "#0C3366", "#F4A623", "#F7C56A"]

def _fig_buf(fig, w_in, h_in):
    """Save figure as PNG and return (buffer, w_in, h_in) so callers can size Image correctly."""
    fig.set_size_inches(w_in, h_in)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf, w_in, h_in

def _palette(n):
    """Cycle CHART_PALETTE to cover any number of series."""
    return [CHART_PALETTE[i % len(CHART_PALETTE)] for i in range(n)]

def chart_pie(df, lics, month):
    n = len(lics)
    ncol = 3 if n > 20 else 2 if n > 8 else 1
    legend_rows = -(-n // ncol)          # ceiling division
    w = 7.0
    pie_h = 3.6
    legend_h = legend_rows * 0.20 + 0.5  # ~0.20" per row + padding
    h = round(min(pie_h + legend_h, 9.5), 2)  # cap at portrait page height

    vals = df.loc[lics, month]
    names = df.loc[lics, "Store Name"].values
    fig, ax = plt.subplots()
    if vals.sum() <= 0:
        ax.text(0.5, 0.5, "No revenue for selected month", ha="center", va="center", fontsize=10)
        ax.set_axis_off()
        ax.set_title(f"Revenue Share — {month}", fontsize=9, pad=8)
        return _fig_buf(fig, w, h)
    wedges, _, autotexts = ax.pie(
        vals, labels=None, autopct="%1.1f%%",
        colors=_palette(n), startangle=140,
        wedgeprops=dict(linewidth=0.5, edgecolor="white"),
        pctdistance=0.8,
    )
    for at in autotexts:
        at.set_fontsize(6)
    ax.legend(wedges, names,
              loc="upper center", bbox_to_anchor=(0.5, -0.04),
              ncol=ncol, fontsize=6.5, frameon=False,
              columnspacing=0.8, handlelength=1.2)
    ax.set_title(f"Revenue Share — {month}", fontsize=9, pad=8)
    return _fig_buf(fig, w, h)

def chart_pie_from_rows(rows, value_col, label_col, month, title_prefix="Revenue Share"):
    n = len(rows)
    ncol = 3 if n > 20 else 2 if n > 8 else 1
    legend_rows = -(-n // ncol) if n else 0
    w = 7.0
    pie_h = 3.6
    legend_h = legend_rows * 0.20 + 0.5
    h = round(min(pie_h + legend_h, 9.5), 2)

    fig, ax = plt.subplots()
    vals = rows[value_col] if not rows.empty else pd.Series(dtype=float)
    if vals.sum() <= 0:
        ax.text(0.5, 0.5, "No revenue for selected month", ha="center", va="center", fontsize=10)
        ax.set_axis_off()
        ax.set_title(f"{title_prefix} — {month}", fontsize=9, pad=8)
        return _fig_buf(fig, w, h)

    wedges, _, autotexts = ax.pie(
        vals, labels=None, autopct="%1.1f%%",
        colors=_palette(n), startangle=140,
        wedgeprops=dict(linewidth=0.5, edgecolor="white"),
        pctdistance=0.8,
    )
    for at in autotexts:
        at.set_fontsize(6)
    ax.legend(wedges, rows[label_col].tolist(),
              loc="upper center", bbox_to_anchor=(0.5, -0.04),
              ncol=ncol, fontsize=6.5, frameon=False,
              columnspacing=0.8, handlelength=1.2)
    ax.set_title(f"{title_prefix} — {month}", fontsize=9, pad=8)
    return _fig_buf(fig, w, h)

def chart_monthly_bar(df, top_lics, months):
    grp = df.loc[top_lics, months].sum()
    all_ = df[months].sum()
    x = range(len(months))
    fig, ax = plt.subplots()
    w = 0.38
    ax.bar([i - w/2 for i in x], [grp[m] for m in months], w,
           label="Top stores", color="#378ADD")
    ax.bar([i + w/2 for i in x], [all_[m] for m in months], w,
           label="All stores", color="#B5D4F4")
    ax.set_xticks(list(x))
    ax.set_xticklabels(months, rotation=30, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(fontsize=7, frameon=False)
    ax.set_title("Monthly Totals — Group vs All Stores", fontsize=9, pad=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, linewidth=0.4, color="#eeeeee")
    ax.set_axisbelow(True)
    return _fig_buf(fig, 7.0, 3.5)

def chart_store_trends(df, lics, months):
    fig, ax = plt.subplots()
    for i, lic in enumerate(lics):
        name = df.loc[lic, "Store Name"]
        ax.plot(months, [df.loc[lic, m] for m in months],
                marker="o", markersize=3, linewidth=1.5,
                color=CHART_PALETTE[i % len(CHART_PALETTE)], label=name)
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(months, rotation=30, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(fontsize=6.5, frameon=False, loc="upper left",
              bbox_to_anchor=(1, 1))
    ax.set_title("Store Trends", fontsize=9, pad=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, linewidth=0.4, color="#eeeeee")
    ax.set_axisbelow(True)
    return _fig_buf(fig, 7.0, 3.23)

# ── PDF builder ────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def build_pdf(df, months, top_lics=None, threshold=None, report_date=None):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.6*inch, bottomMargin=0.6*inch
    )

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", fontSize=16, fontName="Helvetica-Bold", spaceAfter=2, textColor=colors.HexColor(BLUE))
    H2 = ParagraphStyle("H2", fontSize=11, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4, textColor=colors.HexColor("#1a1a1a"))
    SMALL = ParagraphStyle("SMALL", fontSize=8, textColor=colors.HexColor("#666666"), spaceAfter=10)
    FOOTER = ParagraphStyle("FOOTER", fontSize=7, textColor=colors.HexColor("#aaaaaa"))

    hdr_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor(BLUE)),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f9ff")]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e0e8f0")),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ])

    all_lics = df.index.tolist()
    all_totals = df[months].sum(axis=1)
    grand = all_totals.sum()
    month_totals = df[months].sum()
    avg_month = month_totals.mean()
    peak_month = month_totals.idxmax()
    now = report_date or datetime.now().strftime("%B %d, %Y")

    if top_lics:
        title = f"Store Sales — Top {int(threshold*100)}% Dashboard"
        top_rev = df.loc[top_lics, months].sum().sum()
        act_pct = pct_value(top_rev, grand)
        subtitle = f"Generated {now}  ·  {len(top_lics)} of {len(all_lics)} stores  ·  {act_pct:.1f}% of total revenue"
    else:
        title = "Store Sales Dashboard"
        subtitle = f"Generated {now}  ·  {len(all_lics)} stores  ·  {len(months)} months"

    story = []
    story.append(Paragraph(title, H1))
    story.append(Paragraph(subtitle, SMALL))

    # Metric summary row
    top_store_lic = all_totals.idxmax()
    top_store_name = df.loc[top_store_lic, "Store Name"]
    metrics = [
        ["Total Revenue", fmt_usd(grand)],
        ["Avg Monthly", fmt_usd(avg_month)],
        ["Peak Month", peak_month],
        ["Top Store", top_store_name],
    ]
    if top_lics:
        grp_rev = df.loc[top_lics, months].sum().sum()
        grp_avg = df.loc[top_lics, months].sum().mean()
        metrics = [
            ["Stores in Group", f"{len(top_lics)} of {len(all_lics)}"],
            ["Revenue Share", pct(grp_rev, grand)],
            ["Group Total", fmt_usd(grp_rev)],
            ["Avg Monthly", fmt_usd(grp_avg)],
        ]

    metric_data = [[Paragraph(f"<b>{m[1]}</b><br/><font size='7' color='#666'>{m[0]}</font>", styles["Normal"]) for m in metrics]]
    metric_table = Table(metric_data, colWidths=[1.8*inch]*4)
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f0f6ff")),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#c0d8f0")),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.HexColor("#c0d8f0")),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
    ]))
    story.append(metric_table)

    # ── Pareto breakdown (only for pareto dashboard) ───────────────────────────
    if top_lics:
        story.append(Paragraph(f"Pareto Breakdown — All Stores Ranked", H2))
        sorted_lics = all_totals.sort_values(ascending=False).index.tolist()
        cum = 0
        p_rows = [["#", "Store", "License", "Total", "Share", "Cumulative", "Group"]]
        for i, lic in enumerate(sorted_lics):
            tot = all_totals[lic]
            sp = pct_value(tot, grand)
            cum += sp
            in_g = lic in top_lics
            p_rows.append([
                str(i+1),
                df.loc[lic, "Store Name"],
                lic,
                fmt_usd(tot),
                f"{sp:.1f}%",
                f"{min(cum,100):.1f}%",
                "IN" if in_g else "out",
            ])
        pt = Table(p_rows, colWidths=[0.3*inch, 2.4*inch, 0.8*inch, 0.9*inch, 0.7*inch, 0.8*inch, 0.5*inch])
        pt.setStyle(hdr_style)
        # Highlight IN rows
        for i, lic in enumerate(sorted_lics, 1):
            if lic in top_lics:
                pt.setStyle(TableStyle([("TEXTCOLOR", (6,i), (6,i), colors.HexColor("#0C447C")), ("FONTNAME", (6,i), (6,i), "Helvetica-Bold")]))
        story.append(pt)
        story.append(PageBreak())
        # Re-add header on page 2
        story.append(Paragraph(title, H1))
        story.append(Paragraph(subtitle, SMALL))

    # ── Share by store (last month) ────────────────────────────────────────────
    last_m = months[-1]
    focus_lics = top_lics if top_lics else all_lics
    last_m_group = df.loc[focus_lics, last_m].sum()
    last_m_all = df[last_m].sum()
    sorted_share = df.loc[focus_lics].sort_values(last_m, ascending=False)

    story.append(Paragraph(f"Share by Store — {last_m}", H2))
    if top_lics:
        story.append(Paragraph(f"Group total: {fmt_usd(last_m_group)}  ·  {pct(last_m_group, last_m_all)} of all stores", SMALL))
        s_rows = [["Store", "License", "Revenue", "% of Group", "% of All Stores"]]
        for lic in sorted_share.index:
            v = df.loc[lic, last_m]
            s_rows.append([df.loc[lic, "Store Name"], lic, fmt_usd(v), pct(v, last_m_group), pct(v, last_m_all)])
    else:
        story.append(Paragraph(f"Total: {fmt_usd(last_m_all)}", SMALL))
        s_rows = [["Store", "License", "Revenue", "% of Total"]]
        for lic in sorted_share.index:
            v = df.loc[lic, last_m]
            s_rows.append([df.loc[lic, "Store Name"], lic, fmt_usd(v), pct(v, last_m_all)])

    col_w = [2.6*inch, 0.8*inch, 0.9*inch, 0.9*inch] + ([0.9*inch] if top_lics else [])
    st_tbl = Table(s_rows, colWidths=col_w)
    st_tbl.setStyle(hdr_style)
    for i in range(1, len(s_rows)):
        for j in range(2, len(s_rows[0])):
            st_tbl.setStyle(TableStyle([("ALIGN", (j,i), (j,i), "RIGHT")]))
    story.append(st_tbl)

    if top_lics:
        story.append(PageBreak())
        story.append(Paragraph(f"Revenue Share — {last_m}", H2))
        _buf, _w, _h = chart_pie(df, sorted_share.index.tolist(), last_m)
        story.append(Image(_buf, width=_w*inch, height=_h*inch))

    # ── Monthly totals ─────────────────────────────────────────────────────────
    story.append(Paragraph("Monthly Totals", H2))
    if top_lics:
        grp_m = df.loc[top_lics, months].sum()
        all_m = df[months].sum()
        avg2 = grp_m.mean()
        m_rows = [["Month", "Group Total", "All Stores", "Group Share"]]
        for m in months:
            m_rows.append([m, fmt_usd(grp_m[m]), fmt_usd(all_m[m]), pct(grp_m[m], all_m[m])])
        m_rows.append(["Total", fmt_usd(grp_m.sum()), fmt_usd(all_m.sum()), pct(grp_m.sum(), all_m.sum())])
        mc_w = [1.2*inch, 1.2*inch, 1.2*inch, 1.0*inch]
    else:
        avg2 = month_totals.mean()
        m_rows = [["Month", "Total Revenue", "vs Average"]]
        for m in months:
            diff = month_totals[m] - avg2
            m_rows.append([m, fmt_usd(month_totals[m]), ("+" if diff >= 0 else "") + fmt_usd(diff)])
        m_rows.append(["Total", fmt_usd(month_totals.sum()), ""])
        mc_w = [1.2*inch, 1.4*inch, 1.4*inch]

    mt_tbl = Table(m_rows, colWidths=mc_w)
    mt_tbl.setStyle(hdr_style)
    for i in range(1, len(m_rows)):
        for j in range(1, len(m_rows[0])):
            mt_tbl.setStyle(TableStyle([("ALIGN", (j,i), (j,i), "RIGHT")]))
    # Bold total row
    mt_tbl.setStyle(TableStyle([("FONTNAME", (0, len(m_rows)-1), (-1, len(m_rows)-1), "Helvetica-Bold")]))
    story.append(mt_tbl)

    if top_lics:
        story.append(PageBreak())
        story.append(Paragraph("Monthly Totals — Group vs All Stores", H2))
        _buf, _w, _h = chart_monthly_bar(df, top_lics, months)
        story.append(Image(_buf, width=_w*inch, height=_h*inch))
        story.append(PageBreak())
        story.append(Paragraph("Store Trends", H2))
        _buf, _w, _h = chart_store_trends(df, top_lics, months)
        story.append(Image(_buf, width=_w*inch, height=_h*inch))

    # ── Full period all-stores ─────────────────────────────────────────────────
    story.append(Paragraph("Full Period — All Stores", H2))
    all_sorted = all_totals.sort_values(ascending=False)
    a_rows = [["#", "Store", "License", "Total Revenue", "% of Grand Total"]]
    for i, (lic, tot) in enumerate(all_sorted.items(), 1):
        a_rows.append([str(i), df.loc[lic, "Store Name"], lic, fmt_usd(tot), pct(tot, grand)])
    a_rows.append(["", "Grand Total", "", fmt_usd(grand), pct(grand, grand)])
    at_tbl = Table(a_rows, colWidths=[0.3*inch, 2.6*inch, 0.8*inch, 1.1*inch, 1.1*inch])
    at_tbl.setStyle(hdr_style)
    for i in range(1, len(a_rows)):
        at_tbl.setStyle(TableStyle([("ALIGN", (3,i), (4,i), "RIGHT")]))
    at_tbl.setStyle(TableStyle([("FONTNAME", (0, len(a_rows)-1), (-1, len(a_rows)-1), "Helvetica-Bold")]))
    story.append(at_tbl)

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd")))
    story.append(Paragraph(f"Store Sales Dashboard  ·  {now}", FOOTER))

    doc.build(story)
    return buf.getvalue()

@st.cache_data(show_spinner=False)
def build_share_by_store_pdf(df, month, sort_by, top_lics=None, threshold=None, report_date=None):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.6*inch, bottomMargin=0.6*inch
    )

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("ShareH1", fontSize=16, fontName="Helvetica-Bold", spaceAfter=2, textColor=colors.HexColor(BLUE))
    H2 = ParagraphStyle("ShareH2", fontSize=11, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4, textColor=colors.HexColor("#1a1a1a"))
    SMALL = ParagraphStyle("ShareSmall", fontSize=8, textColor=colors.HexColor("#666666"), spaceAfter=10)
    FOOTER = ParagraphStyle("ShareFooter", fontSize=7, textColor=colors.HexColor("#aaaaaa"))

    hdr_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor(BLUE)),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f9ff")]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e0e8f0")),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ])

    now = report_date or datetime.now().strftime("%B %d, %Y")
    all_lics = df.index.tolist()
    focus_lics = top_lics if top_lics else all_lics
    all_total = df[month].sum()
    focus_total = df.loc[focus_lics, month].sum()

    share_df = df.loc[focus_lics, ["Store Name", month]].copy()
    share_df.index.name = "License"
    if top_lics:
        share_df["% of Group"] = share_df[month].apply(lambda v: pct_value(v, focus_total))
        share_df["% of All"] = share_df[month].apply(lambda v: pct_value(v, all_total))
    else:
        share_df["Share"] = share_df[month].apply(lambda v: pct_value(v, all_total))
    share_df = sort_share_rows(share_df, month, sort_by)

    if top_lics:
        title = f"Share by Store — Top {int(threshold*100)}% Stores"
        subtitle = f"Generated {now}  ·  Month: {month}  ·  Sort: {sort_by}  ·  {pct(focus_total, all_total)} of all stores"
        metrics = [
            ["Stores", f"{len(focus_lics)} of {len(all_lics)}"],
            ["Group Total", fmt_usd(focus_total)],
            ["All Stores", fmt_usd(all_total)],
            ["Group Share", pct(focus_total, all_total)],
        ]
    else:
        title = "Share by Store Report"
        subtitle = f"Generated {now}  ·  Month: {month}  ·  Sort: {sort_by}"
        metrics = [
            ["Stores", str(len(focus_lics))],
            ["Month Total", fmt_usd(all_total)],
            ["Highest Store", df.loc[share_df[month].idxmax(), "Store Name"] if len(share_df) else "N/A"],
            ["Sort", sort_by],
        ]

    story = [Paragraph(title, H1), Paragraph(subtitle, SMALL)]
    metric_data = [[Paragraph(f"<b>{m[1]}</b><br/><font size='7' color='#666'>{m[0]}</font>", styles["Normal"]) for m in metrics]]
    metric_table = Table(metric_data, colWidths=[1.8*inch]*4)
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f0f6ff")),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#c0d8f0")),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.HexColor("#c0d8f0")),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
    ]))
    story.append(metric_table)

    story.append(Paragraph(f"Share by Store — {month}", H2))
    if top_lics:
        story.append(Paragraph(f"Group total: {fmt_usd(focus_total)}  ·  {pct(focus_total, all_total)} of all stores", SMALL))
        s_rows = [["Store", "License", "Revenue", "% of Group", "% of All Stores"]]
        for lic, row in share_df.iterrows():
            s_rows.append([row["Store Name"], lic, fmt_usd(row[month]), f"{row['% of Group']:.1f}%", f"{row['% of All']:.1f}%"])
    else:
        story.append(Paragraph(f"Total: {fmt_usd(all_total)}", SMALL))
        s_rows = [["Store", "License", "Revenue", "% of Total"]]
        for lic, row in share_df.iterrows():
            s_rows.append([row["Store Name"], lic, fmt_usd(row[month]), f"{row['Share']:.1f}%"])

    col_w = [2.6*inch, 0.8*inch, 0.9*inch, 0.9*inch] + ([0.9*inch] if top_lics else [])
    share_tbl = Table(s_rows, colWidths=col_w, repeatRows=1)
    share_tbl.setStyle(hdr_style)
    for i in range(1, len(s_rows)):
        for j in range(2, len(s_rows[0])):
            share_tbl.setStyle(TableStyle([("ALIGN", (j,i), (j,i), "RIGHT")]))
    story.append(share_tbl)

    story.append(PageBreak())
    story.append(Paragraph(f"Revenue Share — {month}", H2))
    story.append(Paragraph(f"Sort: {sort_by}", SMALL))
    chart_rows = share_df.reset_index()
    _buf, _w, _h = chart_pie_from_rows(chart_rows, month, "Store Name", month)
    story.append(Image(_buf, width=_w*inch, height=_h*inch))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd")))
    story.append(Paragraph(f"Share by Store Report  ·  {now}", FOOTER))

    doc.build(story)
    return buf.getvalue()

# ── Streamlit UI ───────────────────────────────────────────────────────────────

st.markdown(f"""
<style>
  .metric-card {{background:#f0f6ff;border-radius:8px;padding:12px 16px;margin-bottom:4px}}
  .metric-label {{font-size:11px;color:#111;margin:0}}
  .metric-value {{font-size:22px;font-weight:600;color:#111;margin:0}}
  [data-testid="stMetric"] label,
  [data-testid="stMetric"] label p,
  [data-testid="stWidgetLabel"],
  [data-testid="stWidgetLabel"] label,
  [data-testid="stWidgetLabel"] p,
  [data-testid="stWidgetLabel"] span {{
    color:#111 !important;
  }}
  [data-testid="stExpander"] [data-testid="stWidgetLabel"],
  [data-testid="stExpander"] [data-testid="stWidgetLabel"] label,
  [data-testid="stExpander"] [data-testid="stWidgetLabel"] p,
  [data-testid="stExpander"] [data-testid="stWidgetLabel"] span {{
    color:#F7F8FA !important;
  }}
  div[class*="st-key-territory_designations_"] button,
  div[class*="st-key-territory_designations_"] button *,
  div[class*="st-key-territory_designation_"] [data-testid="stWidgetLabel"],
  div[class*="st-key-territory_designation_"] [data-testid="stWidgetLabel"] label,
  div[class*="st-key-territory_designation_"] [data-testid="stWidgetLabel"] p,
  div[class*="st-key-territory_designation_"] [data-testid="stWidgetLabel"] span {{
    color:#FFFFFF !important;
  }}
  [data-testid="stMetric"] label {{font-size:12px !important}}
</style>
""", unsafe_allow_html=True)

# ── Sidebar: data input ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Monthly Store Data Input")
    st.caption("Paste tab-separated data copied from Excel or Google Sheets. Column A = license, Column B = store name. Totals/Sum/YTD columns are auto-detected and removed.")

    sample = "\n".join([
        "License\tStore Name\tJan\tFeb\tMar\tApr\tMay\tJun\tTotal",
        "LIC-001\tGreenleaf Capitol Hill\t18400\t19800\t17200\t21000\t22400\t20100\t118900",
        "LIC-002\tPine Ave Dispensary\t9200\t8800\t9600\t10400\t11200\t10800\t60000",
        "LIC-003\tHarbor View Collective\t31000\t33500\t29800\t35000\t37200\t34600\t201100",
        "LIC-004\tCascade Cannabis Co.\t14300\t15100\t13800\t16200\t17400\t15900\t92700",
        "LIC-005\tOlympia Roots\t6700\t7200\t6400\t7800\t8100\t7500\t43700",
        "LIC-006\tSoundside Market\t22100\t23400\t21000\t25000\t26800\t24300\t142600",
        "LIC-007\tEverett Green House\t5100\t5400\t4900\t5800\t6200\t5700\t33100",
        "LIC-008\tBellingham Bloom\t11200\t11800\t10600\t12400\t13100\t12000\t71100",
    ])

    if "raw_input" not in st.session_state:
        st.session_state.raw_input = ""
    if "default_sheet_checked" not in st.session_state:
        st.session_state.default_sheet_checked = False
    if not st.session_state.raw_input and not st.session_state.default_sheet_checked:
        try:
            load_sheet_into_session(DEFAULT_SHEET_URL, DEFAULT_SHEET_GID)
        except Exception as e:
            st.session_state.default_sheet_error = str(e)
        st.session_state.default_sheet_checked = True

    if "storage_notice" in st.session_state:
        st.success(st.session_state.storage_notice)
        del st.session_state.storage_notice

    st.subheader("Data Source")
    if st.session_state.get("default_sheet_error"):
        st.error(f"Automatic Google Sheet load failed: {st.session_state.default_sheet_error}")
    elif st.session_state.get("data_source_label"):
        st.caption(st.session_state.data_source_label)
    if st.button("Refresh Google Sheet", width="stretch"):
        try:
            sheet_shape = load_sheet_into_session(DEFAULT_SHEET_URL, DEFAULT_SHEET_GID, clear_cache=True)
        except Exception as e:
            st.error(f"Could not load sheet: {e}")
        else:
            st.session_state.pop("default_sheet_error", None)
            st.session_state.storage_notice = f"Refreshed Google Sheet with {sheet_shape[0]} rows and {sheet_shape[1]} columns."
            st.rerun()

    with st.expander("Use a different sheet"):
        sheet_url = st.text_input("Sheet URL", value=DEFAULT_SHEET_URL)
        sheet_gid = st.text_input("Worksheet gid", value=DEFAULT_SHEET_GID, help="Use the gid from the sheet tab URL. Leave 0 for the first sheet.")
        if st.button("Load alternate sheet", width="stretch"):
            try:
                sheet_shape = load_sheet_into_session(sheet_url, sheet_gid, clear_cache=True)
            except Exception as e:
                st.error(f"Could not load sheet: {e}")
            else:
                st.session_state.storage_notice = f"Loaded alternate sheet with {sheet_shape[0]} rows and {sheet_shape[1]} columns."
                st.rerun()

    saved_datasets = list_saved_datasets()
    if saved_datasets:
        st.subheader("Saved Data")
        saved_options = {
            f"{row['name']} · {row['updated_at'].replace('T', ' ')[:16]}": row["id"]
            for row in saved_datasets
        }
        selected_saved = st.selectbox("Dataset", list(saved_options.keys()), key="saved_dataset")
        load_col, delete_col = st.columns(2)
        if load_col.button("Load", width="stretch"):
            loaded_text = get_saved_dataset(saved_options[selected_saved])
            if loaded_text:
                st.session_state.raw_input = loaded_text
                st.session_state.data_source_label = f"Saved dataset · {selected_saved.split(' · ')[0]}"
                st.session_state.storage_notice = f"Loaded {selected_saved.split(' · ')[0]}."
                st.rerun()
        if delete_col.button("Delete", width="stretch"):
            delete_saved_dataset(saved_options[selected_saved])
            st.session_state.storage_notice = f"Deleted {selected_saved.split(' · ')[0]}."
            st.rerun()

    if st.button("Load demo data"):
        st.session_state.raw_input = sample
        st.session_state.data_source_label = "Demo data"
    raw_input = st.text_area("Paste data here", height=220, placeholder="License\tStore Name\tJan\tFeb...", key="raw_input")

    with st.form("save_dataset_form"):
        dataset_name = st.text_input("Dataset name", placeholder="Example: Q2 retailer sales")
        save_clicked = st.form_submit_button("Save current data")
    if save_clicked:
        clean_name = dataset_name.strip()
        if not clean_name:
            st.warning("Enter a dataset name before saving.")
        elif not raw_input.strip():
            st.warning("Paste or load data before saving.")
        else:
            try:
                parse_input(raw_input)
            except Exception as e:
                st.error(f"Fix the pasted data before saving: {e}")
            else:
                save_dataset(clean_name, raw_input.strip())
                st.session_state.storage_notice = f"Saved {clean_name}."
                st.rerun()

    threshold = st.select_slider("Pareto threshold", options=[0.7, 0.8, 0.9], value=0.8, format_func=lambda x: f"{int(x*100)}%")

    st.divider()
    st.subheader("Order Data")

    # Auto-load from saved default sheet on first run of this session
    if not st.session_state.get("order_sheet_checked"):
        _saved_url = get_setting("order_sheet_url")
        _saved_gid = get_setting("order_sheet_gid", "0")
        if _saved_url and "order_df" not in st.session_state:
            try:
                load_order_sheet_into_session(_saved_url, _saved_gid)
            except Exception as _e:
                st.session_state["order_sheet_error"] = str(_e)
        st.session_state["order_sheet_checked"] = True

    if st.session_state.get("order_sheet_error"):
        st.error(f"Order sheet load failed: {st.session_state['order_sheet_error']}")

    _odf = st.session_state.get("order_df")
    if _odf is not None:
        st.caption(st.session_state.get("order_data_label", f"✅ {len(_odf)} lines · {_odf['Order #'].nunique()} orders"))
        _saved_url = get_setting("order_sheet_url")
        if _saved_url:
            if st.button("Refresh Order Sheet", width="stretch"):
                try:
                    _shape = load_order_sheet_into_session(
                        _saved_url, get_setting("order_sheet_gid", "0"), clear_cache=True
                    )
                    st.session_state.pop("order_sheet_error", None)
                    st.session_state["order_data_label"] = f"Google Sheet · {_shape[0]} rows · {_shape[1]} columns"
                    st.rerun()
                except Exception as _e:
                    st.error(f"Could not refresh: {_e}")
        if st.button("Clear order data", width="stretch"):
            del st.session_state["order_df"]
            st.session_state.pop("order_data_label", None)
            st.rerun()

    with st.expander("Link a Google Sheet" if not get_setting("order_sheet_url") else "Change order sheet"):
        _cur_url = get_setting("order_sheet_url", "")
        _cur_gid = get_setting("order_sheet_gid", "0")
        st.caption("Tip: if you get a 400 error, use a Publish-to-web CSV URL — in Google Sheets: File → Share → Publish to web → select sheet → CSV → Publish.")
        _new_url = st.text_input("Google Sheet URL or published CSV URL", value=_cur_url, key="order_sheet_url_input",
                                  placeholder="https://docs.google.com/spreadsheets/d/…")
        _new_gid = st.text_input("Worksheet gid", value=_cur_gid, key="order_sheet_gid_input",
                                  help="gid from the sheet tab URL; 0 for first sheet")
        if st.button("Load & save as default", width="stretch", key="load_order_sheet"):
            if not _new_url.strip():
                st.warning("Enter a sheet URL.")
            else:
                try:
                    _shape = load_order_sheet_into_session(_new_url.strip(), _new_gid.strip(), clear_cache=True)
                    set_setting("order_sheet_url", _new_url.strip())
                    set_setting("order_sheet_gid", _new_gid.strip() or "0")
                    st.session_state.pop("order_sheet_error", None)
                    st.session_state["order_data_label"] = f"Google Sheet · {_shape[0]} rows · {_shape[1]} columns"
                    st.rerun()
                except Exception as _e:
                    st.error(f"Could not load sheet: {_e}")
        if _cur_url and st.button("Remove default sheet", width="stretch", key="remove_order_sheet"):
            set_setting("order_sheet_url", "")
            st.rerun()

    st.caption("Or upload a file:")
    order_file = st.file_uploader("Order file", type=["xlsx", "xls", "csv"],
                                   key="order_file_upload", label_visibility="collapsed")
    if order_file is not None:
        try:
            st.session_state["order_df"] = parse_orders(order_file)
            st.session_state["order_data_label"] = f"File · {len(st.session_state['order_df'])} lines"
            st.session_state.pop("order_sheet_error", None)
            st.rerun()
        except Exception as _oe:
            st.error(f"Could not read order file: {_oe}")

# ── Parse ──────────────────────────────────────────────────────────────────────
df, months, stripped, rev_exact_dup_ids = None, [], [], []
if raw_input.strip():
    try:
        df, months, stripped, rev_exact_dup_ids = parse_input(raw_input)
    except Exception as e:
        st.error(str(e))

if df is None:
    st.info("Paste your data in the sidebar to get started, or click **Load demo data**.")
    st.stop()

if stripped:
    _stripped_names = ", ".join('"' + s + '"' for s in stripped)
    st.warning(f"Auto-removed column{'s' if len(stripped)>1 else ''}: {_stripped_names}")

if rev_exact_dup_ids:
    st.warning(
        f"⚠️ Revenue data: {len(rev_exact_dup_ids)} exact duplicate row{'s' if len(rev_exact_dup_ids)!=1 else ''} removed — "
        f"{'; '.join(rev_exact_dup_ids)}"
    )

_order_df_check = st.session_state.get("order_df")
if _order_df_check is not None:
    _ord_dup_key = _order_df_check.fillna("<blank>").astype(str).agg("\x1f".join, axis=1)
    _ord_dup_mask = _ord_dup_key.duplicated(keep=False)
    if _ord_dup_mask.any():
        _ord_dup_rows = _order_df_check[_ord_dup_mask].copy()
        _ord_dup_rows["_dup_key"] = _ord_dup_key[_ord_dup_mask]
        _ord_dup_groups = []
        for _, _group in _ord_dup_rows.groupby("_dup_key", sort=False):
            _first = _group.iloc[0]
            _row_labels = []
            for _idx in _group.index:
                try:
                    _row_labels.append(str(int(_idx) + 2))
                except Exception:
                    _row_labels.append(str(_idx))
            _row_nums = ", ".join(_row_labels)
            _parts = [
                str(_first.get("Order #", "")).strip(),
                str(_first.get("Product", "")).strip(),
                str(_first.get("Client", "")).strip(),
                str(_first.get("License #", "")).strip(),
                str(_first.get("Submitted Date", "")).strip(),
            ]
            _ord_dup_groups.append(" · ".join([p for p in _parts if p]) + f" · rows {_row_nums}")
        _ord_dup_preview = "; ".join(_ord_dup_groups[:8])
        if len(_ord_dup_groups) > 8:
            _ord_dup_preview += f"; +{len(_ord_dup_groups) - 8} more"
        st.warning(
            f"⚠️ Order data: {len(_ord_dup_groups)} fully duplicate row group{'s' if len(_ord_dup_groups)!=1 else ''} detected "
            f"({len(_ord_dup_rows)} rows involved; identical across all columns) — {_ord_dup_preview}"
        )

top_lics, grand = compute_pareto(df, months, threshold)
all_lics = df.index.tolist()
all_totals = df[months].sum(axis=1)
month_totals = df[months].sum()
avg_month = month_totals.mean()
peak_month = month_totals.idxmax()
top_store = all_totals.idxmax()
report_date = datetime.now().strftime("%B %d, %Y")

# Pre-compute window/pareto so all tabs can reference w_top_lics
_default_window = last_n_month_cols(months, 3)
_ss_window = st.session_state.get("t2_window", _default_window)
window_months = [m for m in _ss_window if m in months] or _default_window
w_top_lics, _ = compute_pareto(df, window_months, threshold)

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_contact, tab_sales, tab_territory, tab_orders, tab_mom = st.tabs([
    "📋 Store Contact Form",
    "📊 Sales by Store",
    "🗺️ Territory Map",
    "📦 Order Activity",
    "📅 Month over Month",
])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Sales by Store                                            ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_sales:
    view_mode = st.radio(
        "View", [f"Top {int(threshold*100)}%", "All Stores"],
        horizontal=True, key="sales_view_mode"
    )

    if view_mode == f"Top {int(threshold*100)}%":
        # ── Time window filter ────────────────────────────────────────────────
        window_months = st.multiselect(
            "Time window", months, default=window_months, key="t2_window",
            help="Months included in Pareto ranking and group metrics"
        )
        if not window_months:
            window_months = _default_window
        w_top_lics, _ = compute_pareto(df, window_months, threshold)

        w_totals = df[window_months].sum(axis=1)
        w_grand = w_totals.sum()
        top_rev = df.loc[w_top_lics, window_months].sum().sum()
        act_pct = pct_value(top_rev, w_grand)
        top_avg = df.loc[w_top_lics, window_months].sum().mean()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stores in Group", f"{len(w_top_lics)} of {len(all_lics)}")
        c2.metric("Revenue Share", f"{act_pct:.1f}%")
        c3.metric("Group Total", fmt_usd(top_rev))
        c4.metric("Avg Monthly", fmt_usd(top_avg))

        st.divider()

        # Pareto breakdown table
        st.subheader("Pareto breakdown")
        sorted_lics = w_totals.sort_values(ascending=False).index.tolist()
        cum = 0
        pareto_rows = []
        for i, lic in enumerate(sorted_lics, 1):
            tot = w_totals[lic]
            sp = pct_value(tot, w_grand)
            cum += sp
            pareto_rows.append({
                "#": i,
                "Store Name": df.loc[lic, "Store Name"],
                "License": lic,
                "Total": fmt_usd(tot),
                "Share": f"{sp:.1f}%",
                "Cumulative": f"{min(cum,100):.1f}%",
                "Group": "✅ IN" if lic in w_top_lics else "out",
            })
        pareto_df = pd.DataFrame(pareto_rows)

        def highlight_in(row):
            if "✅" in str(row["Group"]):
                return ["font-weight:600; color:#1558b0"] * len(pareto_df.columns)
            return [""] * len(pareto_df.columns)

        st.dataframe(pareto_df.style.apply(highlight_in, axis=1), width="stretch", hide_index=True)

        remaining_pct = max(0, 100 - act_pct) if w_grand else 0.0
        st.caption(f"Remaining {len(all_lics)-len(w_top_lics)} store{'s' if len(all_lics)-len(w_top_lics)!=1 else ''} account for {remaining_pct:.1f}% of total revenue · window: {', '.join(window_months)}")

        st.divider()

        # Share by store (top group only)
        st.subheader("Share by store")
        fc1, fc2, fc3, fc4 = st.columns([1, 1, 1, 2])
        _from_default = months.index(window_months[0])
        _to_default   = months.index(window_months[-1])
        from_month = fc1.selectbox("From", months, index=_from_default, key="t2_from")
        to_month   = fc2.selectbox("To",   months, index=_to_default,   key="t2_to")
        sort_by2   = fc3.selectbox("Sort", SORT_OPTIONS, key="t2_sort")
        search2    = fc4.text_input("Search", placeholder="Store name or license…", key="t2_search")

        fi, ti = months.index(from_month), months.index(to_month)
        if fi > ti:
            fi, ti = ti, fi
        range_months = months[fi: ti + 1]
        range_label  = from_month if fi == ti else f"{from_month} – {to_month}"

        share_df2 = df.loc[w_top_lics, ["Store Name"] + range_months].copy()
        share_df2["_rev"] = share_df2[range_months].sum(axis=1)
        all_rev_range = df[range_months].sum(axis=1).sum()
        grp_rev_range = share_df2["_rev"].sum()
        share_df2["% of Group"] = share_df2["_rev"].apply(lambda v: pct_value(v, grp_rev_range))
        share_df2["% of All"]   = share_df2["_rev"].apply(lambda v: pct_value(v, all_rev_range))
        share_df2 = sort_share_rows(share_df2, "_rev", sort_by2)
        share_df2.insert(0, "#", range(1, len(share_df2) + 1))
        if search2:
            mask = (
                share_df2["Store Name"].str.contains(search2, case=False, na=False)
                | share_df2.index.str.contains(search2, case=False)
            )
            share_df2 = share_df2[mask]

        st.caption(f"Group total {range_label}: **{fmt_usd(grp_rev_range)}** · {pct(grp_rev_range, all_rev_range)} of all stores")
        disp2 = share_df2.reset_index()[["#", "Store Name", "License", "_rev", "% of Group", "% of All"]].copy()
        disp2.columns = ["#", "Store Name", "License", "Revenue", "% of Group", "% of All Stores"]
        disp2["Revenue"] = disp2["Revenue"].apply(fmt_usd)
        disp2["% of Group"]      = disp2["% of Group"].apply(lambda x: f"{x:.1f}%")
        disp2["% of All Stores"] = disp2["% of All Stores"].apply(lambda x: f"{x:.1f}%")
        _ord_df_ref = st.session_state.get("order_df")
        if _ord_df_ref is not None:
            _ord_lics = set(_ord_df_ref["License #"].dropna().astype(str))
            disp2.insert(3, "Order", disp2["License"].apply(lambda l: "✅" if str(l) in _ord_lics else ""))
        st.dataframe(disp2, width="stretch", hide_index=True)

        st.subheader("Revenue share")
        if grp_rev_range > 0 and not share_df2.empty:
            fig_pie2 = px.pie(
                share_df2.reset_index(), values="_rev", names="Store Name",
                color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4
            )
            fig_pie2.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie2.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_pie2, width="stretch")
        else:
            st.info("No revenue for the selected period.")

        share_report2 = build_share_by_store_pdf(df, to_month, sort_by2, top_lics=w_top_lics, threshold=threshold, report_date=report_date)
        st.download_button(
            f"⬇ Download Share by Store Report — Top {int(threshold*100)}%",
            data=share_report2,
            file_name=f"share-by-store-top-{int(threshold*100)}pct-{slugify(range_label)}-{slugify(sort_by2)}.pdf",
            mime="application/pdf", key="t2_share_report"
        )

        st.divider()

        st.subheader("Monthly totals — group vs all stores")
        grp_m = df.loc[w_top_lics, months].sum()
        all_m = df[months].sum()
        fig_bar2 = go.Figure()
        fig_bar2.add_trace(go.Bar(x=months, y=[grp_m[m] for m in months], name=f"Top {int(threshold*100)}% stores", marker_color=BLUE))
        fig_bar2.add_trace(go.Bar(x=months, y=[all_m[m] for m in months], name="All stores", marker_color="#B5D4F4"))
        fig_bar2.update_layout(barmode="group", yaxis_tickformat="$,.0f", height=300,
            margin=dict(t=10, b=10), plot_bgcolor="white", yaxis=dict(gridcolor="#eee"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_bar2, width="stretch")

        mt2 = pd.DataFrame({
            "Month": months,
            "Group": [fmt_usd(grp_m[m]) for m in months],
            "All Stores": [fmt_usd(all_m[m]) for m in months],
            "Group Share": [pct(grp_m[m], all_m[m]) for m in months],
        })
        st.dataframe(mt2, width="stretch", hide_index=True)

        st.divider()

        st.subheader("Store trends")
        top_names = df.loc[w_top_lics, "Store Name"].tolist()
        sel_stores2 = st.multiselect("Select stores", top_names, default=top_names, key="t2_trend")
        if sel_stores2:
            lic_map2 = {v: k for k, v in df["Store Name"].to_dict().items()}
            sel_lics2 = [lic_map2[s] for s in sel_stores2 if s in lic_map2]
            fig_line2 = go.Figure()
            palette = px.colors.qualitative.Set1
            for i, lic in enumerate(sel_lics2):
                fig_line2.add_trace(go.Scatter(
                    x=months, y=[df.loc[lic, m] for m in months],
                    name=df.loc[lic, "Store Name"], mode="lines+markers",
                    line=dict(color=palette[i % len(palette)], width=2)
                ))
            fig_line2.update_layout(yaxis_tickformat="$,.0f", height=300, margin=dict(t=10, b=10),
                plot_bgcolor="white", yaxis=dict(gridcolor="#eee"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(fig_line2, width="stretch")

        st.divider()
        pdf_buf2 = build_pdf(df, months, top_lics=w_top_lics, threshold=threshold, report_date=report_date)
        st.download_button(f"⬇ Download PDF Report — Top {int(threshold*100)}%",
            data=pdf_buf2, file_name=f"pareto-dashboard-{int(threshold*100)}pct.pdf", mime="application/pdf")

    else:  # All Stores
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Revenue", fmt_usd(grand))
        c2.metric("Avg Monthly", fmt_usd(avg_month))
        c3.metric("Peak Month", peak_month)
        c4.metric("Top Store", df.loc[top_store, "Store Name"])

        st.divider()

        col_left, col_right = st.columns([1, 1])
        with col_left:
            st.subheader("Share by store")
            sel_month = st.selectbox("Month", months, index=len(months)-1, key="t1_month")
            sort_by = st.selectbox("Sort", SORT_OPTIONS, key="t1_sort")
            month_total = df[sel_month].sum()
            share_df = df[["Store Name", sel_month]].copy()
            share_df["Share"] = share_df[sel_month].apply(lambda v: pct_value(v, month_total))
            share_df.index.name = "License"
            share_df = sort_share_rows(share_df, sel_month, sort_by)
            st.caption(f"Total for {sel_month}: **{fmt_usd(month_total)}**")
            display = share_df.reset_index()[["Store Name", "License", sel_month, "Share"]].copy()
            display.columns = ["Store Name", "License", "Revenue", "Share %"]
            display["Revenue"] = display["Revenue"].apply(fmt_usd)
            display["Share %"] = display["Share %"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(display, width="stretch", hide_index=True)

        with col_right:
            st.subheader("Revenue share")
            if month_total > 0:
                fig_pie = px.pie(share_df.reset_index(), values=sel_month, names="Store Name",
                    color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4)
                fig_pie.update_traces(textposition="inside", textinfo="percent+label")
                fig_pie.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig_pie, width="stretch")
            else:
                st.info("No revenue for the selected month.")

        share_report = build_share_by_store_pdf(df, sel_month, sort_by, report_date=report_date)
        st.download_button("⬇ Download Share by Store Report", data=share_report,
            file_name=f"share-by-store-{slugify(sel_month)}-{slugify(sort_by)}.pdf",
            mime="application/pdf", key="t1_share_report")

        st.divider()

        st.subheader("Monthly totals")
        fig_bar = go.Figure(go.Bar(x=months, y=[month_totals[m] for m in months],
            marker_color=BLUE, text=[fmt_usd(month_totals[m]) for m in months], textposition="outside"))
        fig_bar.update_layout(yaxis_tickformat="$,.0f", margin=dict(t=20, b=20), height=320,
            plot_bgcolor="white", yaxis=dict(gridcolor="#eee"))
        st.plotly_chart(fig_bar, width="stretch")

        mt = pd.DataFrame({
            "Month": months,
            "Total": [fmt_usd(month_totals[m]) for m in months],
            "vs Avg": [("+" if month_totals[m] >= avg_month else "") + fmt_usd(month_totals[m] - avg_month) for m in months],
        })
        st.dataframe(mt, width="stretch", hide_index=True)

        st.divider()

        st.subheader("Store trends")
        store_options = df["Store Name"].tolist()
        default_sel = df["Store Name"].loc[all_totals.sort_values(ascending=False).index[:min(6, len(all_lics))]].tolist()
        sel_stores = st.multiselect("Select stores", store_options, default=default_sel, key="t1_trend")
        if sel_stores:
            lic_map = {v: k for k, v in df["Store Name"].to_dict().items()}
            sel_lics = [lic_map[s] for s in sel_stores if s in lic_map]
            fig_line = go.Figure()
            palette = px.colors.qualitative.Set1
            for i, lic in enumerate(sel_lics):
                fig_line.add_trace(go.Scatter(x=months, y=[df.loc[lic, m] for m in months],
                    name=df.loc[lic, "Store Name"], mode="lines+markers",
                    line=dict(color=palette[i % len(palette)], width=2)))
            fig_line.update_layout(yaxis_tickformat="$,.0f", height=320, margin=dict(t=10, b=10),
                plot_bgcolor="white", yaxis=dict(gridcolor="#eee"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(fig_line, width="stretch")

        st.divider()
        pdf_buf = build_pdf(df, months, report_date=report_date)
        st.download_button("⬇ Download PDF Report", data=pdf_buf,
            file_name="store-sales-dashboard.pdf", mime="application/pdf")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Store Contact Form                                        ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_contact:
    requested_contact_month = find_last_month_col(months)
    contact_month = find_latest_populated_month_col(df, months, requested_contact_month)
    today_date = datetime.now().date()

    try:
        _saved_log = load_contact_log()
    except Exception as e:
        st.error(f"Could not load saved contact entries: {e}")
        _saved_log = pd.DataFrame(columns=CONTACT_LOG_COLUMNS)

    cf_view = st.radio(
        "Show",
        ["Top 30 Stores", "Lapsed Priority", "All Stores"],
        horizontal=True,
        key="cf_view_mode",
    )

    all_lics_sorted = df[contact_month].sort_values(ascending=False).index.tolist()
    top30_lics = all_lics_sorted[:30]
    cf_display_by_lic = {}
    cf_log_revenue_by_lic = {}

    if cf_view == "Top 30 Stores":
        cf_pool = top30_lics
        st.caption(f"Top 30 stores by **{contact_month}** revenue · Ranked highest to lowest")
        if contact_month != requested_contact_month:
            st.caption(f"Using **{contact_month}** because **{requested_contact_month}** has no loaded revenue yet.")
    elif cf_view == "Lapsed Priority":
        lapsed_c1, lapsed_c2 = st.columns([1, 1])
        cf_lapsed_window = lapsed_c1.number_input(
            "Lapsed within the last N days",
            min_value=31,
            max_value=1095,
            value=180,
            step=1,
            key="cf_lapsed_days",
            help="Stores whose last active month ended between 30 and N days ago.",
        )
        cf_lapsed_totals = build_lapsed_store_df(df, months, _saved_log)
        cf_lapsed_df = filter_lapsed_store_df(cf_lapsed_totals, cf_lapsed_window)
        if cf_lapsed_df.empty:
            cf_pool = []
            lapsed_c2.caption("No lapsed stores in this window.")
            st.info(f"No stores lapsed within the last {cf_lapsed_window} days.")
        else:
            cf_lapsed_count = lapsed_c2.number_input(
                "Stores shown",
                min_value=1,
                max_value=len(cf_lapsed_df),
                value=min(30, len(cf_lapsed_df)),
                step=1,
                key="cf_lapsed_count",
            )
            cf_lapsed_df = cf_lapsed_df.head(int(cf_lapsed_count))
            cf_pool = cf_lapsed_df["License"].astype(str).tolist()
            for _, _lapsed_row in cf_lapsed_df.iterrows():
                _lic = str(_lapsed_row["License"])
                _risk = float(_lapsed_row["Monthly_Run_Rate"])
                _days = int(_lapsed_row["Days_Inactive"])
                _last_active = str(_lapsed_row["Last_Active_Label"])
                cf_display_by_lic[_lic] = (
                    f"{fmt_usd(_risk)}/mo risk · {_days} days inactive · last active {_last_active}"
                )
                cf_log_revenue_by_lic[_lic] = f"{fmt_usd(_risk)}/mo risk"
            st.caption(
                f"Top {len(cf_pool)} lapsed store{'s' if len(cf_pool) != 1 else ''} by estimated monthly revenue at risk."
            )
    else:
        cf_pool = all_lics_sorted
        st.caption(f"All {len(all_lics_sorted)} stores by **{contact_month}** revenue · Ranked highest to lowest")

    for _lic in cf_pool:
        if _lic not in cf_display_by_lic:
            cf_display_by_lic[_lic] = fmt_usd(df.loc[_lic, contact_month])
        if _lic not in cf_log_revenue_by_lic:
            cf_log_revenue_by_lic[_lic] = fmt_usd(df.loc[_lic, contact_month])

    AMOUNT_OPTIONS = [
        "", "$500–$1,000", "$1,000–$2,500", "$2,500–$5,000",
        "$5,000–$10,000", "$10,000–$15,000", "$15,000–$25,000",
    ]
    CADENCE_OPTIONS = ["", "Weekly", "Bi-Weekly", "Monthly", "Other"]

    INITIALS_OPTIONS = ["", "DK", "CH"]

    METHOD_OPTIONS = ["", "In-person", "Phone", "Email"]

    if st.session_state.get("contact_log_notice"):
        st.success(st.session_state.pop("contact_log_notice"))
    if st.session_state.get("contact_log_warning"):
        st.warning(st.session_state.pop("contact_log_warning"))

    def _lic_key(lic):
        return license_match_key(lic)

    def _store_key_for_lic(lic):
        try:
            return store_match_key(df.loc[lic, "Store Name"])
        except Exception:
            return ""

    def _match_keys_for_lic(lic):
        try:
            return contact_match_keys(lic, df.loc[lic, "Store Name"])
        except Exception:
            return contact_match_keys(lic)

    # Saved entries for this month pre-populate widgets.
    _saved_map_by_license: dict = {}
    _saved_map_by_store: dict = {}
    _logged_lics: set = set()
    _logged_stores: set = set()
    _logged_contact_keys: set = set()
    _logged_contact_key_list: list = []
    if not _saved_log.empty:
        _saved_log = _saved_log.copy()
        _saved_log["_saved_sort"] = pd.to_datetime(_saved_log["Saved At"], errors="coerce")
        _saved_log = _saved_log.sort_values("_saved_sort")
        _logged_lics = {k for k in _saved_log["License"].apply(_lic_key) if k}
        _logged_stores = {k for k in _saved_log["Store Name"].apply(store_match_key) if k}
        for _, _r in _saved_log.iterrows():
            _logged_contact_keys.update(contact_match_keys(_r.get("License", ""), _r.get("Store Name", "")))
        _logged_contact_key_list = sorted(_logged_contact_keys, key=len, reverse=True)
        _contact_month_key = canonical_month_label(contact_month)
        _saved_month_keys = _saved_log["Month"].apply(canonical_month_label)
        for _, _r in _saved_log[_saved_month_keys == _contact_month_key].iterrows():
            _lic_saved_key = _lic_key(_r["License"])
            _store_saved_key = store_match_key(_r["Store Name"])
            if _lic_saved_key:
                _saved_map_by_license[_lic_saved_key] = _r.to_dict()
            if _store_saved_key:
                _saved_map_by_store[_store_saved_key] = _r.to_dict()

    def _saved_entry(lic):
        return (
            _saved_map_by_license.get(_lic_key(lic))
            or _saved_map_by_store.get(_store_key_for_lic(lic))
            or {}
        )

    def _has_logged_contact(lic):
        lic_key = _lic_key(lic)
        store_key = _store_key_for_lic(lic)
        match_keys = _match_keys_for_lic(lic)
        return (
            lic_key in _logged_lics
            or store_key in _logged_stores
            or bool(match_keys & _logged_contact_keys)
            or any(
                related_match_key(match_key, saved_key, min_len=5)
                for match_key in match_keys
                for saved_key in _logged_contact_key_list
            )
        )

    def _saved(lic, field, default=""):
        v = _saved_entry(lic).get(field, default)
        return v if v is not None else default

    def _sel_idx(options, value):
        return options.index(value) if value in options else 0

    def _date_or_default(value, default):
        if value is None or str(value).strip() in ("", "None", "nan"):
            return default
        if hasattr(value, "date") and not isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return default

    def _alert_date_for(interval, base_date, custom_date=None):
        base_date = _date_or_default(base_date, today_date)
        if interval == "2 Weeks":
            return base_date + timedelta(days=14)
        if interval == "4 Weeks":
            return base_date + timedelta(days=28)
        if interval == "Other":
            return _date_or_default(custom_date, base_date)
        return None

    def _set_resolved_alert_date(lic, interval, alert_date):
        key = f"cf_{lic}_alert_resolved_date"
        if interval and alert_date:
            st.session_state[key] = alert_date.isoformat()
            return alert_date
        st.session_state.pop(key, None)
        return None

    def _current_alert_date(lic, interval, base_date):
        resolved = st.session_state.get(f"cf_{lic}_alert_resolved_date")
        alert_date = _date_or_default(resolved, None) if resolved else None
        if alert_date:
            return alert_date
        return _alert_date_for(
            interval,
            base_date,
            st.session_state.get(f"cf_{lic}_alert_date"),
        )

    def _contact_row_for_lic(lic):
        commitment = st.session_state.get(f"cf_{lic}_commitment", "No")
        cadence = st.session_state.get(f"cf_{lic}_cadence", "")
        amount = st.session_state.get(f"cf_{lic}_amount", "")
        notes = st.session_state.get(f"cf_{lic}_notes", "")
        initials = st.session_state.get(f"cf_{lic}_initials", "")
        person = st.session_state.get(f"cf_{lic}_person", "")
        method = st.session_state.get(f"cf_{lic}_method", "")
        alert_interval = st.session_state.get(f"cf_{lic}_alert_interval", "")
        contacted_date = st.session_state.get(f"cf_{lic}_date", today_date)
        alert_date = _current_alert_date(lic, alert_interval, contacted_date)
        alert_recipient = ALERT_RECIPIENTS.get(initials, "") if alert_interval else ""
        alert_cc = ALERT_CC if alert_recipient else ""
        return {
            "license":           lic,
            "store_name":        df.loc[lic, "Store Name"],
            "contact_month":     contact_month,
            "revenue":           cf_log_revenue_by_lic.get(lic, fmt_usd(df.loc[lic, contact_month])),
            "date_contacted":    str(contacted_date),
            "initials":          initials,
            "person_contacted":  person,
            "contact_method":    method,
            "commitment_made":   commitment,
            "committed_cadence": cadence,
            "committed_amount":  amount,
            "notes":             notes,
            "next_outreach":     alert_interval,
            "next_outreach_date": alert_date.isoformat() if alert_date else "",
            "alert_recipient":   alert_recipient,
            "alert_cc":          alert_cc,
            "alert_sent_week":   _saved(lic, "Alert Sent Week"),
        }, bool(alert_interval and not alert_recipient)

    # Search filter
    contact_search = st.text_input(
        "Search stores", placeholder="Store name or license…", key="contact_search"
    )
    _q = contact_search.lower()
    display_lics = [
        lic for lic in cf_pool
        if not _q
        or _q in df.loc[lic, "Store Name"].lower()
        or _q in lic.lower()
    ]
    _visible_saved_matches = sum(1 for lic in display_lics if _has_logged_contact(lic))
    if not _saved_log.empty:
        with st.expander("Contact log match diagnostics"):
            st.caption(
                f"{len(_saved_log)} saved contact entr{'y' if len(_saved_log) == 1 else 'ies'} loaded · "
                f"{_visible_saved_matches} matched in current visible store list"
            )
            _visible_sample = pd.DataFrame([
                {
                    "Visible Store": df.loc[lic, "Store Name"],
                    "Visible License": lic,
                    "License Key": _lic_key(lic),
                    "Store Key": _store_key_for_lic(lic),
                    "Match Keys": ", ".join(sorted(_match_keys_for_lic(lic))[:4]),
                    "Matched": _has_logged_contact(lic),
                }
                for lic in display_lics[:10]
            ])
            _logged_sample = _saved_log[["Store Name", "License", "Month"]].head(10).copy()
            _logged_sample["License Key"] = _logged_sample["License"].apply(_lic_key)
            _logged_sample["Store Key"] = _logged_sample["Store Name"].apply(store_match_key)
            _logged_sample["Match Keys"] = _logged_sample.apply(
                lambda r: ", ".join(sorted(contact_match_keys(r["License"], r["Store Name"]))[:4]),
                axis=1,
            )
            d1, d2 = st.columns(2)
            d1.dataframe(_visible_sample, width="stretch", hide_index=True)
            d2.dataframe(_logged_sample, width="stretch", hide_index=True)

    for rank, lic in enumerate(display_lics, 1):
        store_name = df.loc[lic, "Store Name"]
        revenue = cf_display_by_lic.get(lic, fmt_usd(df.loc[lic, contact_month]))
        has_saved = _has_logged_contact(lic)
        label = f"{'✅ ' if has_saved else ''}#{rank}  {store_name}  ·  {lic}  ·  {revenue}"
        with st.expander(label):
            with st.form(f"store_contact_form_{lic}", clear_on_submit=False):
                _date_default = today_date
                _date_str = _saved(lic, "Date Contacted")
                if _date_str:
                    try:
                        _date_default = datetime.strptime(str(_date_str)[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass

                r1a, r1b, r1c, r1d = st.columns(4)
                r1a.date_input("Date Contacted", value=_date_default,
                               format="MM/DD/YYYY", key=f"cf_{lic}_date")
                cur_initials = r1b.selectbox("Initials", INITIALS_OPTIONS,
                                             index=_sel_idx(INITIALS_OPTIONS, _saved(lic, "Initials")),
                                             key=f"cf_{lic}_initials")
                r1c.text_input("Person Contacted", value=_saved(lic, "Person Contacted"),
                               key=f"cf_{lic}_person")
                r1d.selectbox("Contact Method", METHOD_OPTIONS,
                              index=_sel_idx(METHOD_OPTIONS, _saved(lic, "Contact Method")),
                              key=f"cf_{lic}_method")

                r2a, r2b, r2c = st.columns(3)
                r2a.selectbox("Commitment Made", ["No", "Yes"],
                              index=_sel_idx(["No", "Yes"], _saved(lic, "Commitment", "No")),
                              key=f"cf_{lic}_commitment")
                r2b.selectbox("Committed Cadence", CADENCE_OPTIONS,
                              index=_sel_idx(CADENCE_OPTIONS, _saved(lic, "Cadence")),
                              key=f"cf_{lic}_cadence")
                r2c.selectbox("Committed Amount", AMOUNT_OPTIONS,
                              index=_sel_idx(AMOUNT_OPTIONS, _saved(lic, "Committed Amount")),
                              key=f"cf_{lic}_amount")

                _saved_alert_interval = _saved(lic, "Next Outreach")
                _saved_alert_date = _date_or_default(_saved(lic, "Next Outreach Date"), today_date + timedelta(days=14))
                alert_cols = st.columns([1, 1, 2])
                alert_interval = alert_cols[0].selectbox(
                    "Next Outreach Alert",
                    ALERT_OPTIONS,
                    index=_sel_idx(ALERT_OPTIONS, _saved_alert_interval),
                    key=f"cf_{lic}_alert_interval",
                )
                _contacted_for_alert = st.session_state.get(f"cf_{lic}_date", _date_default)
                custom_alert_date = alert_cols[1].date_input(
                    "Custom Outreach Date",
                    value=_saved_alert_date,
                    format="MM/DD/YYYY",
                    key=f"cf_{lic}_alert_date",
                    help="Used when Next Outreach Alert is Other.",
                )
                next_alert_date = _alert_date_for(alert_interval, _contacted_for_alert, custom_alert_date)
                alert_cols[2].caption("Weekly digest routes from DK/CH initials when saved.")
                _set_resolved_alert_date(lic, alert_interval, next_alert_date)

                st.text_area("Notes", value=_saved(lic, "Notes"),
                             height=120, key=f"cf_{lic}_notes")

                save_this_store = st.form_submit_button(
                    "💾 Save This Store",
                    width="stretch",
                    type="primary",
                )
            if save_this_store:
                row_to_save, alert_missing_recipient = _contact_row_for_lic(lic)
                if row_to_save:
                    try:
                        upsert_contact_log_rows([row_to_save])
                    except Exception as e:
                        st.error(f"Could not save contact log: {e}")
                    else:
                        st.session_state["contact_log_notice"] = f"Saved {store_name} to team log."
                        if alert_missing_recipient:
                            st.session_state["contact_log_warning"] = "Alert was not routed because initials were missing or not DK/CH."
                        st.rerun()
                else:
                    st.info("No entry to save for this store.")

    # Build CSV from current widget state
    _csv_rows = []
    for lic in cf_pool:
        _csv_alert_interval = st.session_state.get(f"cf_{lic}_alert_interval", "")
        _csv_alert_date = _current_alert_date(
            lic,
            _csv_alert_interval,
            st.session_state.get(f"cf_{lic}_date", today_date),
        )
        _csv_alert_recipient = (
            ALERT_RECIPIENTS.get(st.session_state.get(f"cf_{lic}_initials", ""), "")
            if _csv_alert_interval else ""
        )
        _csv_rows.append({
            "License":           lic,
            "Store Name":        df.loc[lic, "Store Name"],
            "Month":             contact_month,
            "Revenue":           cf_log_revenue_by_lic.get(lic, fmt_usd(df.loc[lic, contact_month])),
            "Date Contacted":    str(st.session_state.get(f"cf_{lic}_date", today_date)),
            "Initials":          st.session_state.get(f"cf_{lic}_initials", ""),
            "Person Contacted":  st.session_state.get(f"cf_{lic}_person", ""),
            "Contact Method":    st.session_state.get(f"cf_{lic}_method", ""),
            "Commitment Made":   st.session_state.get(f"cf_{lic}_commitment", "No"),
            "Committed Cadence": st.session_state.get(f"cf_{lic}_cadence", ""),
            "Committed Amount":  st.session_state.get(f"cf_{lic}_amount", ""),
            "Next Outreach":     _csv_alert_interval,
            "Next Outreach Date": _csv_alert_date.isoformat() if _csv_alert_date else "",
            "Alert Recipient":   _csv_alert_recipient,
            "Alert CC":          ALERT_CC if _csv_alert_recipient else "",
            "Notes":             st.session_state.get(f"cf_{lic}_notes", ""),
        })
    dl_col, reset_col = st.columns([2, 1])
    dl_col.download_button(
        "⬇ Download as CSV",
        data=pd.DataFrame(_csv_rows).to_csv(index=False),
        file_name=f"store-contacts-{slugify(cf_view)}-{slugify(contact_month)}.csv",
        mime="text/csv",
        width="stretch",
    )

    if reset_col.button("Reset", width="stretch"):
        for lic in cf_pool:
            for field in ("date", "initials", "person", "method",
                          "commitment", "cadence", "amount", "alert_interval",
                          "alert_date", "alert_date_display", "alert_resolved_date", "notes"):
                st.session_state.pop(f"cf_{lic}_{field}", None)
        st.rerun()

    # ── Team Log ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Team Contact Log")
    st.caption("All saved contact entries across all months, visible to the entire team.")
    st.caption(f"Storage: **{contact_log_backend_label()}**")
    if not contact_sheet_configured():
        st.warning("Contact log is using local SQLite fallback. On Streamlit Cloud this is not durable; configure Google Sheets secrets before relying on it.")

    with st.expander("Restore contact log from CSV"):
        restore_file = st.file_uploader(
            "Upload team-contact-log.csv or store-contacts CSV",
            type=["csv"],
            key="contact_log_restore_upload",
        )
        restore_import_all = st.checkbox(
            "Import every valid row",
            value=False,
            key="contact_log_restore_all",
            help="Use for full backups or older CSVs where date-only rows are intentional. Leave off for form exports with blank/default rows.",
        )
        if st.button("Import CSV to Team Log", width="stretch", key="contact_log_restore_btn"):
            if restore_file is None:
                st.warning("Choose a CSV file first.")
            else:
                try:
                    restore_df = pd.read_csv(restore_file)
                    restore_rows = _restore_contact_rows(restore_df, import_all=restore_import_all)
                    if not restore_rows:
                        st.warning("No contact entries found in that CSV. If this backup contains date-only rows, check **Import every valid row** and try again.")
                    else:
                        upsert_contact_log_rows(restore_rows)
                        st.success(f"Imported {len(restore_rows)} contact entr{'y' if len(restore_rows)==1 else 'ies'}.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Could not import contact CSV: {e}")

    try:
        log_df = load_contact_log()
    except Exception as e:
        st.error(f"Could not load team contact log: {e}")
        log_df = pd.DataFrame(columns=CONTACT_LOG_COLUMNS)
    if not log_df.empty and "Saved At" in log_df.columns:
        log_df = (
            log_df.assign(_saved_sort=pd.to_datetime(log_df["Saved At"], errors="coerce"))
            .sort_values("_saved_sort", ascending=False, na_position="last", kind="mergesort")
            .drop(columns=["_saved_sort"])
        )
    if log_df.empty:
        st.info("No entries saved yet. Fill in the form above and click **Save to Team Log**.")
    else:
        # Search / filter
        filter_col, dl_log_col, clear_col = st.columns([3, 2, 1])
        search = filter_col.text_input("Filter by store name or license", placeholder="Search…", label_visibility="collapsed")
        display_log_df = log_df.copy()
        if search:
            mask = (
                display_log_df["Store Name"].str.contains(search, case=False, na=False)
                | display_log_df["License"].str.contains(search, case=False, na=False)
            )
            display_log_df = display_log_df[mask]

        st.dataframe(
            display_log_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Saved At": st.column_config.TextColumn("Saved At"),
                "Store Name": st.column_config.TextColumn(width="large"),
                "Notes": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(f"{len(display_log_df)} entr{'y' if len(display_log_df)==1 else 'ies'}")

        log_csv = display_log_df.to_csv(index=False)
        dl_log_col.download_button(
            "⬇ Download Full Log (CSV)",
            data=log_csv,
            file_name="team-contact-log.csv",
            mime="text/csv",
            width="stretch",
        )
        if clear_col.button("Clear All", width="stretch"):
            try:
                clear_contact_log()
            except Exception as e:
                st.error(f"Could not clear contact log: {e}")
            else:
                st.rerun()

        # ── Edit / Delete individual entry ────────────────────────────────────
        st.divider()
        with st.expander("Edit or Delete an Entry"):
            entry_labels = [
                f"{row['Store Name']}  ·  {row['License']}  ·  {row['Month']}"
                for _, row in log_df.iterrows()
            ]
            sel_label = st.selectbox("Select entry", entry_labels, key="log_edit_select")
            sel_idx = entry_labels.index(sel_label)
            sel_row = log_df.iloc[sel_idx]

            def _opt_idx(opts, val):
                try:
                    return opts.index(str(val)) if str(val) in opts else 0
                except ValueError:
                    return 0

            def _date_val(v):
                if pd.isna(v) or str(v).strip() in ("", "None", "nan"):
                    return datetime.now().date()
                try:
                    return datetime.strptime(str(v), "%Y-%m-%d").date()
                except Exception:
                    return datetime.now().date()

            ea1, ea2, ea3, ea4 = st.columns(4)
            ed_date      = ea1.date_input("Date Contacted", value=_date_val(sel_row["Date Contacted"]),
                                           format="MM/DD/YYYY", key="log_ed_date")
            ed_initials  = ea2.selectbox("Initials", INITIALS_OPTIONS,
                                          index=_opt_idx(INITIALS_OPTIONS, sel_row["Initials"]),
                                          key="log_ed_initials")
            ed_person    = ea3.text_input("Person Contacted",
                                           value=("" if pd.isna(sel_row["Person Contacted"]) else str(sel_row["Person Contacted"])),
                                           key="log_ed_person")
            ed_method    = ea4.selectbox("Contact Method", METHOD_OPTIONS,
                                          index=_opt_idx(METHOD_OPTIONS, sel_row["Contact Method"]),
                                          key="log_ed_method")

            eb1, eb2, eb3 = st.columns(3)
            ed_commit    = eb1.selectbox("Commitment Made", ["No", "Yes"],
                                          index=_opt_idx(["No", "Yes"], sel_row["Commitment"]),
                                          key="log_ed_commit")
            ed_cadence   = eb2.selectbox("Committed Cadence", CADENCE_OPTIONS,
                                          index=_opt_idx(CADENCE_OPTIONS, sel_row["Cadence"]),
                                          key="log_ed_cadence")
            ed_amount    = eb3.selectbox("Committed Amount", AMOUNT_OPTIONS,
                                          index=_opt_idx(AMOUNT_OPTIONS, sel_row["Committed Amount"]),
                                          key="log_ed_amount")

            ec1, ec2, ec3 = st.columns([1, 1, 2])
            ed_alert_interval = ec1.selectbox(
                "Next Outreach Alert",
                ALERT_OPTIONS,
                index=_opt_idx(ALERT_OPTIONS, sel_row.get("Next Outreach", "")),
                key="log_ed_alert_interval",
            )
            _ed_saved_alert_date = _date_val(sel_row.get("Next Outreach Date", ""))
            if ed_alert_interval == "Other":
                ed_alert_date = ec2.date_input(
                    "Next Outreach Date",
                    value=_ed_saved_alert_date,
                    format="MM/DD/YYYY",
                    key="log_ed_alert_date",
                )
            else:
                ed_alert_date = _alert_date_for(ed_alert_interval, ed_date)
                ec2.text_input(
                    "Next Outreach Date",
                    value=ed_alert_date.strftime("%m/%d/%Y") if ed_alert_date else "",
                    disabled=True,
                    key="log_ed_alert_date_display",
                )
            ed_alert_recipient = ALERT_RECIPIENTS.get(ed_initials, "") if ed_alert_interval else ""
            if ed_alert_interval and ed_alert_recipient:
                ec3.caption(f"Monday digest to {ed_alert_recipient}; CC {ALERT_CC}")
            elif ed_alert_interval:
                ec3.warning("Select DK or CH initials to route the alert.")
            else:
                ec3.caption("No follow-up alert scheduled.")

            ed_notes = st.text_area("Notes",
                                     value=("" if pd.isna(sel_row["Notes"]) else str(sel_row["Notes"])),
                                     height=120, key="log_ed_notes")

            save_ed_col, del_ed_col = st.columns([1, 1])
            if save_ed_col.button("Save Changes", type="primary", width="stretch", key="log_ed_save"):
                try:
                    upsert_contact_log_rows([{
                        "license":           sel_row["License"],
                        "store_name":        sel_row["Store Name"],
                        "contact_month":     sel_row["Month"],
                        "revenue":           sel_row.get("Revenue"),
                        "date_contacted":    ed_date.strftime("%Y-%m-%d"),
                        "commitment_made":   ed_commit,
                        "committed_cadence": ed_cadence,
                        "committed_amount":  ed_amount,
                        "notes":             ed_notes,
                        "initials":          ed_initials,
                        "person_contacted":  ed_person,
                        "contact_method":    ed_method,
                        "next_outreach":     ed_alert_interval,
                        "next_outreach_date": ed_alert_date.isoformat() if ed_alert_date else "",
                        "alert_recipient":   ed_alert_recipient,
                        "alert_cc":          ALERT_CC if ed_alert_recipient else "",
                        "alert_sent_week":   sel_row.get("Alert Sent Week", ""),
                    }])
                except Exception as e:
                    st.error(f"Could not update entry: {e}")
                else:
                    st.success("Entry updated.")
                    st.rerun()
            if del_ed_col.button("Delete Entry", type="secondary", width="stretch", key="log_ed_delete"):
                try:
                    delete_contact_log_entry(sel_row["License"], sel_row["Month"])
                except Exception as e:
                    st.error(f"Could not delete entry: {e}")
                else:
                    st.success(f"Deleted entry for {sel_row['Store Name']} · {sel_row['Month']}.")
                    st.rerun()

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Territory Map                                             ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_territory:
    st.subheader("Territory Map")
    if st.session_state.get("territory_notice"):
        st.success(st.session_state.pop("territory_notice"))
    if st.session_state.get("territory_warning"):
        st.warning(st.session_state.pop("territory_warning"))

    locations = load_store_locations()
    coord_ready = (
        locations["Latitude"].notna() & locations["Longitude"].notna()
        if not locations.empty else pd.Series(dtype=bool)
    )

    with st.expander("Location Data", expanded=locations.empty):
        template_df = pd.DataFrame([{
            "License": "LIC-001",
            "Store Name": "Example Retailer",
            "Address": "123 Main St",
            "City": "Seattle",
            "State": "WA",
            "Zip": "98101",
            "Latitude": "",
            "Longitude": "",
        }])
        st.download_button(
            "Download Location Template",
            data=template_df.to_csv(index=False).encode("utf-8"),
            file_name="store_locations_template.csv",
            mime="text/csv",
            width="stretch",
        )

        upload_col, sheet_col = st.columns(2)
        with upload_col:
            loc_file = st.file_uploader(
                "Upload Locations",
                type=["csv", "xlsx"],
                key="territory_location_upload",
                help="Expected columns: License, Store Name, Address, City, State, Zip, Latitude, Longitude.",
            )
            if loc_file is not None:
                upload_id = hashlib.md5(loc_file.getvalue()).hexdigest()
                if st.session_state.get("territory_location_upload_id") != upload_id:
                    try:
                        uploaded_locations = read_store_location_file(loc_file)
                        saved_count = save_store_locations(uploaded_locations)
                    except Exception as exc:
                        st.error(f"Could not read location file: {exc}")
                    else:
                        st.session_state["territory_location_upload_id"] = upload_id
                        st.session_state["territory_notice"] = f"Saved {saved_count} store location rows."
                        st.rerun()

        with sheet_col:
            loc_sheet_url = st.text_input(
                "Location Sheet URL",
                value=get_setting("location_sheet_url", ""),
                key="territory_location_sheet_url",
                placeholder="https://docs.google.com/spreadsheets/d/...",
            )
            loc_sheet_gid = st.text_input(
                "Location Worksheet gid",
                value=get_setting("location_sheet_gid", "0"),
                key="territory_location_sheet_gid",
            )
            if st.button("Load Location Sheet", width="stretch", key="territory_load_location_sheet"):
                if not loc_sheet_url.strip():
                    st.warning("Enter a location sheet URL.")
                else:
                    try:
                        load_location_sheet_as_df.clear()
                        sheet_locations, sheet_shape = load_location_sheet_as_df(
                            loc_sheet_url.strip(), loc_sheet_gid.strip() or "0"
                        )
                        saved_count = save_store_locations(sheet_locations)
                        set_setting("location_sheet_url", loc_sheet_url.strip())
                        set_setting("location_sheet_gid", loc_sheet_gid.strip() or "0")
                    except Exception as exc:
                        st.error(f"Could not load location sheet: {exc}")
                    else:
                        st.session_state["territory_notice"] = (
                            f"Loaded {saved_count} locations from {sheet_shape[0]} rows."
                        )
                        st.rerun()

        if not locations.empty:
            st.caption(f"Saved locations: {len(locations):,} stores · {int(coord_ready.sum()):,} mapped")
            if st.button("Clear Saved Locations", type="secondary", width="stretch", key="territory_clear_locations"):
                clear_store_locations()
                st.session_state.pop("territory_location_upload_id", None)
                st.session_state["territory_notice"] = "Cleared saved store locations."
                st.rerun()

    locations = load_store_locations()
    if locations.empty:
        st.info("Load a store location CSV or Google Sheet to enable the territory map.")
    else:
        coord_ready = locations["Latitude"].notna() & locations["Longitude"].notna()
        api_key = google_maps_server_key()
        missing_geocode = locations[
            (~coord_ready)
            & locations["Address"].astype(str).str.strip().ne("")
        ]
        with st.expander("Geocode Missing Coordinates", expanded=False):
            if api_key:
                stale_count = locations["Geocode Status"].astype(str).str.contains("expired", case=False, na=False).sum()
                st.caption(
                    f"{len(missing_geocode):,} address row(s) are missing coordinates. "
                    f"{stale_count:,} Google-geocoded row(s) need refresh after the 30-day cache window."
                )
                geocode_limit = st.number_input(
                    "Max addresses to geocode now",
                    min_value=1,
                    max_value=max(1, min(250, len(missing_geocode))),
                    value=max(1, min(25, len(missing_geocode))) if len(missing_geocode) else 1,
                    step=1,
                    key="territory_geocode_limit",
                )
                if st.button(
                    "Geocode Missing Addresses",
                    type="primary",
                    width="stretch",
                    key="territory_geocode_btn",
                    disabled=len(missing_geocode) == 0,
                ):
                    updated_locations, summary = geocode_store_locations(locations, api_key, geocode_limit)
                    save_store_locations(updated_locations)
                    st.session_state["territory_notice"] = (
                        f"Geocoded {summary['successes']} of {summary['attempted']} attempted address rows."
                    )
                    st.rerun()
            else:
                st.caption("Add `google_maps_api_key` to Streamlit secrets to geocode missing addresses.")

        ord_df = st.session_state.get("order_df")
        if ord_df is None:
            st.warning("Order data is not loaded, so brand-carrying recommendations cannot be calculated yet.")

        control_cols = st.columns([1, 1, 1, 2])
        radius_miles = control_cols[0].selectbox(
            "Radius",
            options=[0.25, 0.5, 1.0],
            format_func=lambda v: f"{v:g} mi",
            key="territory_radius",
        )
        active_days = control_cols[1].number_input(
            "Brand Window",
            min_value=30,
            max_value=365,
            value=120,
            step=30,
            key="territory_active_days",
            help="Paid orders inside this many days from the latest order date count as active brand placement.",
        )
        include_missing = control_cols[2].checkbox(
            "Show Unmapped",
            value=not bool(coord_ready.any()),
            key="territory_include_missing",
        )
        search_term = control_cols[3].text_input(
            "Search",
            placeholder="Store name, license, city...",
            key="territory_search",
        )

        stores = build_territory_store_table(locations, df, months, ord_df, active_days)
        stores, nearby_pairs = enrich_territory_proximity(stores, radius_miles)

        m1, m2, m3, m4, m5 = st.columns(5)
        pitch_count = int((stores["Recommendation"] == "Pitch Mayfield").sum())
        conflict_count = int(((stores["Nearby K. Savage"] > 0) & (~stores["Carries K. Savage"])).sum())
        mapped_count = int((stores["Latitude"].notna() & stores["Longitude"].notna()).sum())
        need_coords = int(((stores["Latitude"].isna() | stores["Longitude"].isna()) & stores["Address"].astype(str).str.strip().ne("")).sum())
        market_sales = float(stores["Market Sales Last Month"].sum())
        m1.metric("Retailers Loaded", f"{len(stores):,}")
        m2.metric("Mapped Stores", f"{mapped_count:,}")
        m3.metric("Need Coordinates", f"{need_coords:,}")
        m4.metric("Pitch Mayfield", f"{pitch_count:,}")
        m5.metric("Market Sales", fmt_usd(market_sales))

        filter_cols = st.columns([1, 1])
        brand_filter = filter_cols[0].selectbox("Brand", ["All"] + TERRITORY_BRANDS, key="territory_brand_filter")
        use_google_map = filter_cols[1].checkbox(
            "Use Google Maps",
            value=bool(google_maps_browser_key()),
            disabled=not bool(google_maps_browser_key()),
            key="territory_use_google_map",
            help="Uses `google_maps_browser_key` when present; otherwise falls back to the geocoding key.",
        )

        category_values = set(stores["Map Category"].dropna().astype(str))
        selector_category_values = category_values - TERRITORY_SELECTOR_EXCLUDED_CATEGORIES
        designation_options = [
            category for category in TERRITORY_MAP_COLORS
            if category in selector_category_values
        ]
        designation_options.extend(sorted(selector_category_values - set(designation_options)))
        selected_designations = []
        if designation_options:
            dot_styles = []
            for designation in designation_options:
                designation_key = f"territory_designation_{slugify(designation)}"
                pin_color = TERRITORY_MAP_COLORS.get(designation, "#6E7781")
                dot_styles.append(f"""
                  div[class*="st-key-{designation_key}"] [data-testid="stWidgetLabel"] p::before {{
                    content:"";
                    display:inline-block;
                    width:0.65rem;
                    height:0.65rem;
                    border-radius:999px;
                    background:{pin_color};
                    border:1px solid rgba(255,255,255,0.75);
                    margin-right:0.45rem;
                    box-shadow:0 0 0 1px rgba(17,24,39,0.20);
                    vertical-align:-0.05rem;
                  }}
                """)
            st.markdown(f"<style>{''.join(dot_styles)}</style>", unsafe_allow_html=True)

            action_cols = st.columns([1, 1, 4])
            select_all_designations = action_cols[0].button("All", key="territory_designations_all")
            clear_designations = action_cols[1].button("None", key="territory_designations_none")
            for designation in designation_options:
                designation_key = f"territory_designation_{slugify(designation)}"
                if select_all_designations:
                    st.session_state[designation_key] = True
                elif clear_designations:
                    st.session_state[designation_key] = False

            designation_cols = st.columns(3)
            for idx, designation in enumerate(designation_options):
                designation_key = f"territory_designation_{slugify(designation)}"
                if designation_cols[idx % 3].checkbox(designation, value=True, key=designation_key):
                    selected_designations.append(designation)

        filtered_stores = stores.copy()
        unmapped_mask = filtered_stores["Latitude"].isna() | filtered_stores["Longitude"].isna()
        if selected_designations:
            designation_mask = filtered_stores["Map Category"].isin(selected_designations)
            if include_missing:
                designation_mask = designation_mask | unmapped_mask
            filtered_stores = filtered_stores[designation_mask]
        elif designation_options:
            filtered_stores = filtered_stores[unmapped_mask] if include_missing else filtered_stores.iloc[0:0]
        if brand_filter != "All":
            brand_mask = filtered_stores[f"Carries {brand_filter}"]
            if brand_filter == "Mayfield":
                brand_mask = brand_mask | filtered_stores["Recommendation"].eq("Pitch Mayfield")
            filtered_stores = filtered_stores[brand_mask]
        if search_term.strip():
            q = search_term.strip().lower()
            filtered_stores = filtered_stores[
                filtered_stores["Store Name"].astype(str).str.lower().str.contains(q, na=False)
                | filtered_stores["License"].astype(str).str.lower().str.contains(q, na=False)
                | filtered_stores["City"].astype(str).str.lower().str.contains(q, na=False)
            ]
        if not include_missing:
            filtered_stores = filtered_stores[
                filtered_stores["Latitude"].notna() & filtered_stores["Longitude"].notna()
            ]

        mapped_filtered = filtered_stores[
            filtered_stores["Latitude"].notna() & filtered_stores["Longitude"].notna()
        ]
        if mapped_filtered.empty:
            st.info("No mapped stores match the current filters. Geocode addresses to enable the map and proximity signals; the retailer table below still shows loaded rows.")
        else:
            rendered_google = render_google_territory_map(mapped_filtered) if use_google_map else False
            if not rendered_google:
                render_plotly_territory_map(mapped_filtered)

        st.subheader("Placement Signals")
        table_cols = [
            "Designation", "Recommendation", "Store Name", "License", "City", "County",
            "Priority Level", "Market Sales Last Month", "Sales Rank", "Active Brands",
            "K Savage Lapsed", "K. Savage Last Order", "K. Savage Historical Revenue",
            "Nearby K. Savage", "Nearby Mayfield", "Nearest Store", "Nearest Distance",
            "Nearby Detail", "K. Savage", "Mayfield", "Leisure Land",
            "Orders", "Last Order", "Brand Revenue", "Revenue Total",
            "Flowers & Prerolls", "Concentrates & Cartridges",
            "Edibles, Topicals, Infused, etc.",
        ]
        display_cols = [col for col in table_cols if col in filtered_stores.columns]
        display_stores = filtered_stores[display_cols].sort_values(
            ["Recommendation", "Nearby K. Savage", "Market Sales Last Month", "Brand Revenue"],
            ascending=[True, False, False, False],
        )
        st.dataframe(
            display_stores,
            width="stretch",
            hide_index=True,
            column_config={
                "Nearest Distance": st.column_config.NumberColumn("Nearest Distance", format="%.2f mi"),
                "K. Savage": st.column_config.NumberColumn("K. Savage", format="$%.0f"),
                "Mayfield": st.column_config.NumberColumn("Mayfield", format="$%.0f"),
                "Leisure Land": st.column_config.NumberColumn("Leisure Land", format="$%.0f"),
                "Brand Revenue": st.column_config.NumberColumn("Brand Revenue", format="$%.0f"),
                "Revenue Total": st.column_config.NumberColumn("Revenue Total", format="$%.0f"),
                "K. Savage Historical Revenue": st.column_config.NumberColumn("K. Savage History", format="$%.0f"),
                "Market Sales Last Month": st.column_config.NumberColumn("Market Sales", format="$%.0f"),
                "Last Order": st.column_config.DatetimeColumn("Last Order", format="MM/DD/YYYY"),
                "K. Savage Last Order": st.column_config.DatetimeColumn("K. Savage Last", format="MM/DD/YYYY"),
            },
        )
        st.download_button(
            "Download Territory Signals",
            data=stores.to_csv(index=False).encode("utf-8"),
            file_name="territory_signals.csv",
            mime="text/csv",
            width="stretch",
        )

        with st.expander("Nearby Store Pairs"):
            if nearby_pairs.empty:
                st.caption("No store pairs found inside the selected radius.")
            else:
                st.dataframe(
                    nearby_pairs,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Distance (mi)": st.column_config.NumberColumn("Distance", format="%.2f mi"),
                    },
                )

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Order Activity                                            ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_orders:
    BRANDS = ["K. Savage", "Mayfield", "Leisure Land"]
    BRAND_COLORS = {
        "K. Savage":    "#4CE89C",
        "Mayfield":     "#E8844C",
        "Leisure Land": "#4C9BE8",
    }

    ord_df = st.session_state.get("order_df")
    if ord_df is None:
        st.info("Upload an order detail file (.xlsx or .csv) in the sidebar to enable this tab.")
        st.stop()

    # Compute date bounds from full dataset before any filtering
    _all_dates = ord_df["Submitted Date"].dropna()
    _min_date = _all_dates.min().date() if not _all_dates.empty else datetime.now().date()
    _max_date = _all_dates.max().date() if not _all_dates.empty else datetime.now().date()

    released_source = ord_df.copy()

    # Exclude Bulk from all views
    ord_df = ord_df[ord_df["Brand"] != "Bulk"]

    # ── Filters ───────────────────────────────────────────────────────────────
    fc1, fc2, fc3, _ = st.columns([1, 1, 1, 1])
    status_opts = ["All"] + sorted(ord_df["Status"].dropna().unique().tolist())
    status_filter = fc1.selectbox("Status", status_opts, key="ord_status")
    date_from = fc2.date_input("From", value=_min_date, min_value=_min_date, max_value=_max_date, key="ord_from")
    date_to   = fc3.date_input("To",   value=_max_date, min_value=_min_date, max_value=_max_date, key="ord_to")

    view = ord_df.copy()
    if status_filter != "All":
        view = view[view["Status"] == status_filter]
    if "Submitted Date" in view.columns:
        view = view[
            view["Submitted Date"].dt.date.between(
                min(date_from, date_to), max(date_from, date_to)
            )
        ]

    # ── Brand KPI cards ───────────────────────────────────────────────────────
    st.subheader("Brand Summary")
    b_cols = st.columns(3)
    for bcol, brand in zip(b_cols, BRANDS):
        bdf = view[view["Brand"] == brand]
        bcol.metric(brand, fmt_usd(bdf["Line Total"].sum()))
        bcol.caption(f"{bdf['Order #'].nunique()} orders · {int(bdf['Units'].sum()):,} units")

    st.divider()

    # ── Brand comparison charts ───────────────────────────────────────────────
    st.subheader("Brand Comparison")
    brand_summary = (
        view.groupby("Brand")
        .agg(Revenue=("Line Total", "sum"), Units=("Units", "sum"), Orders=("Order #", "nunique"))
        .reindex(BRANDS)
        .reset_index()
    )
    ch1, ch2 = st.columns(2)
    fig_rev = px.bar(
        brand_summary, x="Revenue", y="Brand", orientation="h",
        color="Brand", color_discrete_map=BRAND_COLORS, text_auto="$.0f",
    )
    fig_rev.update_layout(showlegend=False, margin=dict(t=10, b=10), height=220,
                          xaxis_tickprefix="$", xaxis_tickformat=",")
    fig_rev.update_yaxes(autorange="reversed")
    ch1.plotly_chart(fig_rev, width="stretch")

    fig_units = px.bar(
        brand_summary, x="Units", y="Brand", orientation="h",
        color="Brand", color_discrete_map=BRAND_COLORS, text_auto=True,
    )
    fig_units.update_layout(showlegend=False, margin=dict(t=10, b=10), height=220)
    fig_units.update_yaxes(autorange="reversed")
    ch2.plotly_chart(fig_units, width="stretch")

    st.divider()

    # ── Order timeline ────────────────────────────────────────────────────────
    st.subheader("Order Timeline")
    if "Submitted Date" in view.columns and view["Submitted Date"].notna().any():
        timeline = (
            view.dropna(subset=["Submitted Date"])
            .groupby(["Submitted Date", "Brand"])["Order #"]
            .nunique()
            .reset_index()
            .rename(columns={"Order #": "Orders"})
        )
        fig_time = px.bar(
            timeline, x="Submitted Date", y="Orders", color="Brand",
            color_discrete_map=BRAND_COLORS, barmode="stack",
        )
        fig_time.update_layout(margin=dict(t=10, b=10), height=300,
                                xaxis_title=None, legend_title=None)
        st.plotly_chart(fig_time, width="stretch")

    st.divider()

    # ── Top products per brand (exclude $0 samples) ───────────────────────────
    st.subheader("Top Products by Brand")
    paid_view = view[view["Line Total"] > 0]
    brand_tabs = st.tabs(BRANDS)
    for btab, brand in zip(brand_tabs, BRANDS):
        with btab:
            bdf = paid_view[paid_view["Brand"] == brand]
            top_prods = (
                bdf.groupby("Product")
                .agg(Units=("Units", "sum"), Revenue=("Line Total", "sum"))
                .sort_values("Units", ascending=False)
                .head(15)
                .reset_index()
            )
            if not top_prods.empty:
                top_prods["Revenue"] = top_prods["Revenue"].apply(fmt_usd)
                fig_prod = px.bar(
                    top_prods, x="Units", y="Product", orientation="h",
                    color_discrete_sequence=[BRAND_COLORS.get(brand, BLUE)],
                    text_auto=True,
                )
                fig_prod.update_layout(
                    showlegend=False, margin=dict(t=10, b=10),
                    height=max(300, len(top_prods) * 32),
                )
                fig_prod.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_prod, width="stretch")
            else:
                st.info(f"No paid {brand} lines in current selection.")

    st.divider()

    # ── Store-level activity ───────────────────────────────────────────────────
    st.subheader("Store Activity")

    # Summary table: one row per store, columns per brand + totals
    store_summary = (
        paid_view.groupby(["Client", "License #", "Brand"])
        .agg(Units=("Units", "sum"), Revenue=("Line Total", "sum"))
        .reset_index()
    )
    store_pivot = store_summary.pivot_table(
        index=["Client", "License #"],
        columns="Brand",
        values="Revenue",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    store_pivot.columns.name = None

    store_totals = (
        paid_view.groupby(["Client", "License #"])
        .agg(
            Total_Units=("Units", "sum"),
            Total_Revenue=("Line Total", "sum"),
            Orders=("Order #", "nunique"),
            Last_Order=("Submitted Date", "max"),
        )
        .reset_index()
    )
    # Most recent order number per store
    _last_idx = paid_view.groupby(["Client", "License #"])["Submitted Date"].idxmax()
    _last_order_nums = (
        paid_view.loc[_last_idx, ["Client", "License #", "Order #"]]
        .rename(columns={"Order #": "Last_Order_Num"})
        .reset_index(drop=True)
    )
    store_table = store_totals.merge(store_pivot, on=["Client", "License #"], how="left")
    store_table = store_table.merge(_last_order_nums, on=["Client", "License #"], how="left")
    for brand in BRANDS:
        if brand not in store_table.columns:
            store_table[brand] = 0
    store_table = store_table.sort_values("Last_Order", ascending=False)

    # Lapsed stores — derived from monthly sheet data (full history back to Jan 2024)
    try:
        _contact_log_for_lapsed = load_contact_log()
    except Exception:
        _contact_log_for_lapsed = pd.DataFrame(columns=CONTACT_LOG_COLUMNS)
    _lapsed_totals = build_lapsed_store_df(df, months, _contact_log_for_lapsed)
    # Search
    store_search = st.text_input("Search stores", placeholder="Store name or license…", key="ord_store_search")
    if store_search:
        _q = store_search.lower()
        store_table = store_table[
            store_table["Client"].str.lower().str.contains(_q, na=False)
            | store_table["License #"].astype(str).str.contains(_q, na=False)
        ]

    disp_store = store_table.rename(columns={
        "Client": "Store", "License #": "License",
        "Total_Units": "Total Units", "Total_Revenue": "Revenue",
        "Last_Order": "Last Order", "Last_Order_Num": "Order #",
    })[["Store", "License", "Orders", "Last Order", "Order #", "Revenue", "Total Units"] + BRANDS]
    st.dataframe(
        disp_store,
        width="stretch",
        hide_index=True,
        column_config={
            "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
            "Last Order": st.column_config.DatetimeColumn("Last Order", format="MM/DD/YYYY"),
            **{brand: st.column_config.NumberColumn(brand, format="$%.0f") for brand in BRANDS},
        },
    )

    release_title_col, release_totals_col = st.columns([1.2, 3])
    release_title_col.markdown("#### Released from Balaclava")
    manifest_col = first_existing_column(released_source, MANIFEST_REFERENCE_COLUMNS)
    if manifest_col is None:
        release_totals_col.caption("No manifest reference column")
        st.info("No manifest reference column found in the order data.")
    else:
        released_view = released_source.copy()
        if status_filter != "All" and "Status" in released_view.columns:
            released_view = released_view[released_view["Status"] == status_filter]
        released_view["_Manifest Reference"] = released_view[manifest_col].apply(clean_reference)
        released_view = released_view[
            released_view["_Manifest Reference"].astype(str).str.strip().ne("")
        ].copy()
        released_view["_Sales"] = pd.to_numeric(
            released_view.get("Line Total", 0), errors="coerce"
        ).fillna(0)
        released_view["_Units"] = pd.to_numeric(
            released_view.get("Units", 0), errors="coerce"
        ).fillna(0)
        released_view = released_view[released_view["_Sales"] > 0].copy()
        release_date_filter_applied = False

        if released_view.empty:
            release_totals_col.caption("No manifested sales")
            st.info("No manifested sales in the current filters.")
        else:
            manifest_date_col = first_existing_column(released_view, MANIFEST_DATE_COLUMNS)
            if manifest_date_col:
                released_view["_Release Date"] = pd.to_datetime(
                    released_view[manifest_date_col], errors="coerce"
                )
            else:
                released_view["_Release Date"] = pd.NaT
            if "Submitted Date" in released_view.columns:
                released_view["_Release Date"] = released_view["_Release Date"].fillna(
                    released_view["Submitted Date"]
                )

            release_dates = released_view["_Release Date"].dropna()
            if release_dates.empty:
                release_from_default = min(date_from, date_to)
                release_to_default = max(date_from, date_to)
            else:
                release_from_default = release_dates.min().date()
                release_to_default = release_dates.max().date()
            for release_key, release_default in (
                ("released_from_date", release_from_default),
                ("released_to_date", release_to_default),
            ):
                existing_release_date = st.session_state.get(release_key)
                try:
                    outside_range = (
                        existing_release_date is not None
                        and (
                            existing_release_date < release_from_default
                            or existing_release_date > release_to_default
                        )
                    )
                except TypeError:
                    outside_range = True
                if outside_range:
                    st.session_state[release_key] = release_default
            rel_f1, rel_f2, _ = st.columns([1, 1, 3])
            release_from = rel_f1.date_input(
                "Release Date From",
                value=release_from_default,
                min_value=release_from_default,
                max_value=release_to_default,
                key="released_from_date",
            )
            release_to = rel_f2.date_input(
                "Release Date To",
                value=release_to_default,
                min_value=release_from_default,
                max_value=release_to_default,
                key="released_to_date",
            )
            release_start, release_end = min(release_from, release_to), max(release_from, release_to)
            released_view = released_view[
                released_view["_Release Date"].dt.date.between(release_start, release_end)
            ].copy()
            release_date_filter_applied = True

        if not released_view.empty:
            brand_totals = released_view.groupby("Brand")["_Sales"].sum()
            preferred_brand_order = BRANDS + ["Bulk", "Other"]
            ordered_brands = [
                brand for brand in preferred_brand_order
                if brand in brand_totals.index and brand_totals[brand] > 0
            ]
            ordered_brands += [
                brand for brand in sorted(brand_totals.index)
                if brand not in ordered_brands and brand_totals[brand] > 0
            ]
            release_totals_col.markdown(
                "<div style='padding-top:0.55rem;text-align:right'>"
                + " &nbsp; ".join(
                    f"<span><strong>{brand}</strong> {fmt_usd(brand_totals[brand])}</span>"
                    for brand in ordered_brands
                )
                + "</div>",
                unsafe_allow_html=True,
            )

            released_summary = (
                released_view.groupby(["_Release Date", "Client", "License #", "Brand"], dropna=False)
                .agg(
                    Sales=("_Sales", "sum"),
                    Units=("_Units", "sum"),
                    Orders=("Order #", "nunique"),
                    Manifests=("_Manifest Reference", "nunique"),
                    Manifest_Refs=("_Manifest Reference", lambda s: ", ".join(sorted(set(s)))),
                )
                .reset_index()
                .rename(columns={
                    "_Release Date": "Date",
                    "Client": "Store",
                    "License #": "License",
                    "Manifest_Refs": "Manifest Reference #",
                })
                .sort_values(["Date", "Store", "Brand"], ascending=[False, True, True])
            )
            released_summary["Date"] = pd.to_datetime(
                released_summary["Date"], errors="coerce"
            ).dt.date
            st.dataframe(
                released_summary[[
                    "Date", "Store", "License", "Brand", "Sales", "Units",
                    "Orders", "Manifests", "Manifest Reference #",
                ]],
                width="stretch",
                hide_index=True,
                column_config={
                    "Date": st.column_config.DateColumn("Date", format="MM/DD/YYYY"),
                    "Sales": st.column_config.NumberColumn("Sales", format="$%.0f"),
                    "Units": st.column_config.NumberColumn("Units", format="%.0f"),
                },
            )
            st.caption(
                f"{len(released_summary)} release row{'s' if len(released_summary) != 1 else ''} · "
                f"{fmt_usd(released_summary['Sales'].sum())} manifested sales"
            )
        elif release_date_filter_applied:
            release_totals_col.caption("No manifested sales in release date range")
            st.info("No manifested sales in the selected release date range.")

    st.markdown("#### Lapsed Stores")
    _lapsed_window = st.number_input(
        "Lapsed within the last N days", min_value=31, max_value=1095,
        value=180, step=1, key="lapsed_days",
        help="Show stores whose last active month ended between 30 and N days ago."
    )
    _today = pd.Timestamp.now().normalize()
    lapsed_df = filter_lapsed_store_df(_lapsed_totals, _lapsed_window, _today)

    if lapsed_df.empty:
        st.info(f"No stores lapsed within the last {_lapsed_window} days.")
    else:
        lapsed_df["Last_Active"] = pd.to_datetime(lapsed_df["Last_Active"], errors="coerce")
        lapsed_df["Days_Inactive"] = (_today - lapsed_df["Last_Active"]).dt.days
        lapsed_df["Revenue"] = pd.to_numeric(lapsed_df["Revenue"], errors="coerce").fillna(0)
        lapsed_df["Monthly_Run_Rate"] = pd.to_numeric(
            lapsed_df["Monthly_Run_Rate"], errors="coerce"
        ).fillna(0)
        lapsed_df["Last_Month_Revenue"] = pd.to_numeric(
            lapsed_df["Last_Month_Revenue"], errors="coerce"
        ).fillna(0)
        _bucket_order = ["31-60 days", "61-90 days", "91-180 days", "181+ days"]
        lapsed_df["Aging_Bucket"] = pd.cut(
            lapsed_df["Days_Inactive"],
            bins=[30, 60, 90, 180, float("inf")],
            labels=_bucket_order,
            right=True,
        )
        _risk_total = lapsed_df["Monthly_Run_Rate"].sum()
        _top_risk = lapsed_df.sort_values("Monthly_Run_Rate", ascending=False).head(1)
        _top_risk_store = _top_risk.iloc[0]["Store"] if not _top_risk.empty else "n/a"
        _top_risk_value = _top_risk.iloc[0]["Monthly_Run_Rate"] if not _top_risk.empty else 0

        st.caption(
            f"{len(lapsed_df)} store{'s' if len(lapsed_df) != 1 else ''} — last active month between 30 and {_lapsed_window} days ago, prioritized by estimated monthly revenue at risk"
        )

        lm1, lm2, lm3, lm4 = st.columns(4)
        lm1.metric("Lapsed Stores", f"{len(lapsed_df):,}")
        lm2.metric("Est. Monthly Risk", fmt_usd(_risk_total))
        lm3.metric("Avg Days Inactive", f"{lapsed_df['Days_Inactive'].mean():.0f}")
        lm4.metric("Top Risk Store", str(_top_risk_store)[:28], fmt_usd(_top_risk_value))

        _dark_chart_bg = "#0E1117"
        _dark_chart_grid = "rgba(255,255,255,0.16)"
        _dark_chart_text = "#F7F8FA"

        st.markdown("##### Lapse Aging")
        _bucket_summary = (
            lapsed_df.groupby("Aging_Bucket", observed=False)
            .agg(
                Stores=("License", "nunique"),
                Est_Monthly_Risk=("Monthly_Run_Rate", "sum"),
            )
            .reindex(_bucket_order)
            .fillna(0)
            .reset_index()
            .rename(columns={"Aging_Bucket": "Aging Bucket"})
        )
        fig_lapsed_age = go.Figure()
        fig_lapsed_age.add_trace(go.Bar(
            x=_bucket_summary["Aging Bucket"],
            y=_bucket_summary["Stores"],
            name="Stores",
            marker_color=BLUE,
            text=[f"{int(v):,}" for v in _bucket_summary["Stores"]],
            textposition="outside",
            textfont=dict(color=_dark_chart_text),
        ))
        fig_lapsed_age.add_trace(go.Scatter(
            x=_bucket_summary["Aging Bucket"],
            y=_bucket_summary["Est_Monthly_Risk"],
            name="Est. monthly risk",
            mode="lines+markers+text",
            yaxis="y2",
            line=dict(color="#E8844C", width=3),
            marker=dict(size=8),
            text=[fmt_usd(v) if v else "" for v in _bucket_summary["Est_Monthly_Risk"]],
            textposition="top center",
            textfont=dict(color=_dark_chart_text),
        ))
        fig_lapsed_age.update_layout(
            height=320,
            margin=dict(t=10, b=10),
            plot_bgcolor=_dark_chart_bg,
            paper_bgcolor=_dark_chart_bg,
            font=dict(color=_dark_chart_text),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(gridcolor=_dark_chart_grid),
            yaxis=dict(title="Stores", gridcolor=_dark_chart_grid, rangemode="tozero"),
            yaxis2=dict(
                title="Est. monthly risk",
                overlaying="y",
                side="right",
                tickprefix="$",
                tickformat=",",
                showgrid=False,
                rangemode="tozero",
            ),
            hoverlabel=dict(bgcolor="#1C2028", font_color=_dark_chart_text),
        )
        st.plotly_chart(fig_lapsed_age, width="stretch")

        st.markdown("##### Revenue-at-Risk Pareto")
        if len(lapsed_df) > 5:
            _pareto_n = st.slider(
                "Stores shown",
                min_value=5,
                max_value=min(30, len(lapsed_df)),
                value=min(15, len(lapsed_df)),
                step=1,
                key="lapsed_pareto_n",
            )
        else:
            _pareto_n = len(lapsed_df)
        _pareto_df = (
            lapsed_df.sort_values("Monthly_Run_Rate", ascending=False)
            .head(_pareto_n)
            .copy()
        )
        _pareto_df["Risk_Label"] = _pareto_df["Monthly_Run_Rate"].apply(fmt_usd)
        _pareto_share = pct(_pareto_df["Monthly_Run_Rate"].sum(), _risk_total)
        st.caption(f"Top {len(_pareto_df)} store{'s' if len(_pareto_df) != 1 else ''} represent {_pareto_share} of estimated monthly lapsed revenue.")
        fig_lapsed_pareto = px.bar(
            _pareto_df.sort_values("Monthly_Run_Rate", ascending=True),
            x="Monthly_Run_Rate",
            y="Store",
            orientation="h",
            color="Contact_Status",
            color_discrete_map={
                "Not Contacted": "#7A7F86",
                "Contacted": BLUE,
                "Contacted - No Commitment": "#E8844C",
                "Committed": "#2EAD69",
            },
            text="Risk_Label",
            hover_data={
                "License": True,
                "Contact_Status": True,
                "Days_Inactive": ":,.0f",
                "Last_Active_Label": True,
                "Last_Month_Revenue": ":$,.0f",
                "Revenue": ":$,.0f",
                "Monthly_Run_Rate": ":$,.0f",
                "Risk_Label": False,
            },
        )
        fig_lapsed_pareto.update_layout(
            height=max(320, len(_pareto_df) * 34),
            margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor=_dark_chart_bg,
            paper_bgcolor=_dark_chart_bg,
            font=dict(color=_dark_chart_text),
            xaxis=dict(title="Estimated monthly revenue at risk", tickprefix="$", tickformat=",", gridcolor=_dark_chart_grid),
            yaxis=dict(title=None),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hoverlabel=dict(bgcolor="#1C2028", font_color=_dark_chart_text),
        )
        fig_lapsed_pareto.update_traces(
            textposition="outside",
            textfont=dict(color=_dark_chart_text),
            cliponaxis=False,
        )
        st.plotly_chart(fig_lapsed_pareto, width="stretch")

        st.markdown("##### Outreach Priority")
        _scatter_df = lapsed_df.rename(columns={
            "Days_Inactive": "Days Inactive",
            "Monthly_Run_Rate": "Monthly Run Rate",
            "Revenue": "All-time Revenue",
            "Last_Active_Label": "Last Active Month",
            "Last_Month_Revenue": "Last Active Revenue",
            "Contact_Status": "Contact Status",
        })
        fig_lapsed_scatter = px.scatter(
            _scatter_df,
            x="Days Inactive",
            y="Monthly Run Rate",
            size="All-time Revenue",
            color="Contact Status",
            hover_name="Store",
            color_discrete_map={
                "Not Contacted": "#7A7F86",
                "Contacted": BLUE,
                "Contacted - No Commitment": "#E8844C",
                "Committed": "#2EAD69",
            },
            hover_data={
                "License": True,
                "Last Active Month": True,
                "Last Active Revenue": ":$,.0f",
                "All-time Revenue": ":$,.0f",
                "Monthly Run Rate": ":$,.0f",
                "Days Inactive": ":,.0f",
                "Contact Status": False,
            },
            size_max=44,
        )
        fig_lapsed_scatter.update_layout(
            height=420,
            margin=dict(t=10, b=10),
            plot_bgcolor=_dark_chart_bg,
            paper_bgcolor=_dark_chart_bg,
            font=dict(color=_dark_chart_text),
            xaxis=dict(title="Days since last active month", gridcolor=_dark_chart_grid),
            yaxis=dict(title="Estimated monthly revenue at risk", tickprefix="$", tickformat=",", gridcolor=_dark_chart_grid),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hoverlabel=dict(bgcolor="#1C2028", font_color=_dark_chart_text),
        )
        st.plotly_chart(fig_lapsed_scatter, width="stretch")

        st.dataframe(
            lapsed_df.rename(columns={
                "Last_Active": "Last Active Month",
                "Revenue": "All-time Revenue",
                "Monthly_Run_Rate": "Est. Monthly Risk",
                "Last_Month_Revenue": "Last Active Revenue",
                "Active_Months": "Active Months",
                "Contact_Status": "Contact Status",
                "Days_Inactive": "Days Inactive",
            })[[
                "Store", "License", "Last Active Month", "Days Inactive",
                "Contact Status", "Est. Monthly Risk", "Last Active Revenue",
                "All-time Revenue", "Active Months",
            ]],
            width="stretch",
            hide_index=True,
            column_config={
                "All-time Revenue": st.column_config.NumberColumn("All-time Revenue", format="$%.0f"),
                "Est. Monthly Risk": st.column_config.NumberColumn("Est. Monthly Risk", format="$%.0f"),
                "Last Active Revenue": st.column_config.NumberColumn("Last Active Revenue", format="$%.0f"),
                "Last Active Month": st.column_config.DatetimeColumn("Last Active Month", format="MMM YYYY"),
            },
        )

    st.divider()

    # ── Orders by Store ───────────────────────────────────────────────────────
    st.subheader("Orders by Store")

    obs_f1, obs_f2, obs_f3 = st.columns([2, 1, 1])
    _obs_stores = ["All Stores"] + sorted(paid_view["Client"].dropna().unique().tolist())
    obs_store = obs_f1.selectbox("Store", _obs_stores, index=0, key="obs_store")
    _obs_dates = paid_view["Submitted Date"].dropna()
    _obs_min = _obs_dates.min().date() if not _obs_dates.empty else _min_date
    _obs_max = _obs_dates.max().date() if not _obs_dates.empty else _max_date
    obs_from = obs_f2.date_input("From", value=_obs_min, min_value=_obs_min, max_value=_obs_max, key="obs_from")
    obs_to   = obs_f3.date_input("To",   value=_obs_max, min_value=_obs_min, max_value=_obs_max, key="obs_to")

    obs_view = paid_view.copy()
    if obs_store != "All Stores":
        obs_view = obs_view[obs_view["Client"] == obs_store]
    obs_view = obs_view[obs_view["Submitted Date"].dt.date.between(obs_from, obs_to)]

    obs_totals = (
        obs_view.groupby(["Client", "License #", "Order #", "Submitted Date"])
        .agg(Revenue=("Line Total", "sum"), Units=("Units", "sum"))
        .reset_index()
    )
    obs_pivot = obs_view.pivot_table(
        index=["Client", "License #", "Order #", "Submitted Date"],
        columns="Brand",
        values="Line Total",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    obs_pivot.columns.name = None
    obs_table = obs_totals.merge(obs_pivot, on=["Client", "License #", "Order #", "Submitted Date"], how="left")
    for brand in BRANDS:
        if brand not in obs_table.columns:
            obs_table[brand] = 0
    obs_table = obs_table.sort_values("Submitted Date", ascending=False)
    obs_table["Submitted Date"] = obs_table["Submitted Date"].dt.strftime("%m/%d/%Y")
    obs_table = obs_table.rename(columns={"Client": "Store", "Submitted Date": "Date"})

    st.dataframe(
        obs_table[["Store", "License #", "Order #", "Date", "Revenue", "Units"] + BRANDS],
        width="stretch",
        hide_index=True,
        column_config={
            "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
            **{brand: st.column_config.NumberColumn(brand, format="$%.0f") for brand in BRANDS},
        },
    )
    st.caption(f"{obs_table['Order #'].nunique()} orders · {fmt_usd(obs_table['Revenue'].sum())} total")

    st.divider()

    # Store drill-down
    st.subheader("Store Order Detail")
    store_names = sorted(paid_view["Client"].dropna().unique().tolist())
    selected_store = st.selectbox("Select store", ["All Stores"] + store_names, index=0, key="ord_store_select")
    if selected_store:
        store_orders = (
            paid_view.copy() if selected_store == "All Stores"
            else paid_view[paid_view["Client"] == selected_store].copy()
        )

        # Filters
        sf1, sf2, sf3 = st.columns([2, 1, 1])
        def _ord_sort_key(x):
            import re as _re
            m = _re.search(r"(\d+)$", str(x))
            return int(m.group(1)) if m else str(x)
        order_nums = sorted(store_orders["Order #"].dropna().unique().tolist(), key=_ord_sort_key)
        sel_orders = sf1.multiselect("Filter by Order #", order_nums, placeholder="All orders", key="sod_order_filter")
        _so_dates = store_orders["Submitted Date"].dropna()
        _so_min = _so_dates.min().date() if not _so_dates.empty else _min_date
        _so_max = _so_dates.max().date() if not _so_dates.empty else _max_date
        sod_from = sf2.date_input("From", value=_so_min, min_value=_so_min, max_value=_so_max, key="sod_from")
        sod_to   = sf3.date_input("To",   value=_so_max, min_value=_so_min, max_value=_so_max, key="sod_to")

        if sel_orders:
            store_orders = store_orders[store_orders["Order #"].isin(sel_orders)]
        store_orders = store_orders[
            store_orders["Submitted Date"].dt.date.between(sod_from, sod_to)
        ]

        store_orders = store_orders.sort_values("Submitted Date", ascending=False)
        store_orders["Submitted Date"] = store_orders["Submitted Date"].dt.strftime("%m/%d/%Y")
        store_orders["Line Total"] = store_orders["Line Total"].apply(fmt_usd)
        detail_cols = ["Order #", "Submitted Date", "Brand", "Product", "Units", "Line Total", "Status"]
        if selected_store == "All Stores":
            detail_cols = ["Client"] + detail_cols
        detail_cols = [c for c in detail_cols if c in store_orders.columns]
        st.dataframe(
            store_orders[detail_cols],
            width="stretch",
            hide_index=True,
        )

    st.divider()

    # ── Sample drops by store ─────────────────────────────────────────────────
    st.subheader("Sample Drops by Store")
    sample_view = view[view["Line Total"] <= 0]
    if sample_view.empty:
        st.info("No sample lines in the current date/status selection.")
    else:
        sample_pivot = sample_view.pivot_table(
            index=["Client", "License #"],
            columns="Brand",
            values="Units",
            aggfunc="sum",
            fill_value=0,
        ).reset_index()
        sample_pivot.columns.name = None
        for brand in BRANDS:
            if brand not in sample_pivot.columns:
                sample_pivot[brand] = 0

        sample_totals = (
            sample_view.groupby(["Client", "License #"])
            .agg(
                Total_Units=("Units", "sum"),
                Drops=("Order #", "nunique"),
                Last_Drop=("Submitted Date", "max"),
            )
            .reset_index()
        )
        sample_totals["Last_Drop"] = sample_totals["Last_Drop"].dt.strftime("%m/%d/%Y")
        sample_table = sample_totals.merge(sample_pivot, on=["Client", "License #"], how="left")
        sample_table = sample_table.sort_values("Total_Units", ascending=False)
        sample_table = sample_table.rename(columns={
            "Client": "Store", "License #": "License",
            "Total_Units": "Total Units", "Last_Drop": "Last Drop",
        })[["Store", "License", "Drops", "Last Drop", "Total Units"] + BRANDS]

        st.dataframe(sample_table, width="stretch", hide_index=True)

        # Product breakdown
        with st.expander("Sample product detail"):
            sample_prods = (
                sample_view.groupby(["Client", "Brand", "Product"])["Units"]
                .sum()
                .reset_index()
                .sort_values(["Client", "Units"], ascending=[True, False])
                .rename(columns={"Client": "Store"})
            )
            st.dataframe(sample_prods, width="stretch", hide_index=True)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Month over Month                                          ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_mom:
    _ord_df_mom = st.session_state.get("order_df")
    if _ord_df_mom is None:
        st.info("Link a Google Sheet in the sidebar (Order Activity tab) to enable current-month data.")
    elif len(months) < 1:
        st.info("Need at least one month of revenue data.")
    else:
        _today = datetime.now()
        _pm = _today.month - 1 if _today.month > 1 else 12
        _mom_abbrevs = {
            1: ["jan"], 2: ["feb"], 3: ["mar"], 4: ["apr"],
            5: ["may"], 6: ["jun"], 7: ["jul"], 8: ["aug"],
            9: ["sep", "sept"], 10: ["oct"], 11: ["nov"], 12: ["dec"],
        }
        def _find_month_idx(m_num, fallback):
            tgts = _mom_abbrevs[m_num]
            for col in reversed(months):
                if any(t in col.lower() for t in tgts):
                    return months.index(col)
            return fallback
        _prev_idx = _find_month_idx(_pm, len(months) - 1)

        # Date bounds from order sheet
        _ord_dates = _ord_df_mom["Submitted Date"].dropna()
        _ord_min = _ord_dates.min().date() if not _ord_dates.empty else _today.date()
        _ord_max = _ord_dates.max().date() if not _ord_dates.empty else _today.date()
        # Default current-month window: first to last day with data in current calendar month
        import calendar as _cal
        _cm_first = _today.replace(day=1).date()
        _cm_last  = _today.replace(day=_cal.monthrange(_today.year, _today.month)[1]).date()
        _cm_from_default = max(_ord_min, _cm_first)
        _cm_to_default   = min(_ord_max, _cm_last)
        if _cm_from_default > _cm_to_default:
            _latest_order_month_start = _ord_max.replace(day=1)
            _cm_from_default = max(_ord_min, _latest_order_month_start)
            _cm_to_default = _ord_max
        for _mom_key, _mom_default in (("mom_from", _cm_from_default), ("mom_to", _cm_to_default)):
            _mom_existing = st.session_state.get(_mom_key)
            try:
                _mom_outside_range = (
                    _mom_existing is not None
                    and (_mom_existing < _ord_min or _mom_existing > _ord_max)
                )
            except TypeError:
                _mom_outside_range = True
            if _mom_outside_range:
                st.session_state[_mom_key] = _mom_default

        mc1, mc2, mc3, _ = st.columns([2, 1, 1, 2])
        prev_month  = mc1.selectbox("Last month", months, index=_prev_idx, key="mom_base")
        _cm_from    = mc2.date_input("Current from", value=_cm_from_default,
                                      min_value=_ord_min, max_value=_ord_max, key="mom_from")
        _cm_to      = mc3.date_input("Current to",   value=_cm_to_default,
                                      min_value=_ord_min, max_value=_ord_max, key="mom_to")

        _curr_label = f"{_cm_from.strftime('%b %-d')} – {_cm_to.strftime('%b %-d, %Y')}"
        _curr_paid = _ord_df_mom[
            (_ord_df_mom["Submitted Date"].dt.date >= _cm_from) &
            (_ord_df_mom["Submitted Date"].dt.date <= _cm_to)   &
            (_ord_df_mom["Brand"] != "Bulk")                    &
            (_ord_df_mom["Line Total"] > 0)
        ]
        # Group by License # only — avoid multi-row joins from Client name variations
        _curr_rev = _curr_paid.groupby("License #")["Line Total"].sum().reset_index()
        _curr_rev.columns = ["License", "Current Month"]
        # Store name: take the first Client value per license from the order sheet
        _curr_names = (
            _curr_paid.drop_duplicates("License #")[["License #", "Client"]]
            .rename(columns={"License #": "License", "Client": "_ord_name"})
        )
        _curr_rev = _curr_rev.merge(_curr_names, on="License", how="left")
        _mom_brand_colors = {
            "K. Savage": "#4CE89C",
            "Mayfield": "#E8844C",
            "Leisure Land": "#4C9BE8",
            "Other": "#B7BCC6",
            "No Current Orders": "#7A7F86",
        }
        _mom_brand_cols = ["K. Savage", "Mayfield", "Leisure Land"]
        _mom_extra_brands = sorted(
            b for b in _curr_paid["Brand"].dropna().unique().tolist()
            if b not in set(_mom_brand_cols + ["Bulk"])
        )
        _mom_brand_cols += _mom_extra_brands
        if _curr_paid.empty:
            _curr_brand_rev = pd.DataFrame(columns=["License"] + _mom_brand_cols)
        else:
            _curr_brand_rev = (
                _curr_paid.pivot_table(
                    index="License #",
                    columns="Brand",
                    values="Line Total",
                    aggfunc="sum",
                    fill_value=0,
                )
                .reset_index()
                .rename(columns={"License #": "License"})
            )
        _curr_brand_rev["License"] = _curr_brand_rev.get("License", pd.Series(dtype=str)).astype(str)
        for _brand in _mom_brand_cols:
            if _brand not in _curr_brand_rev.columns:
                _curr_brand_rev[_brand] = 0

        # Revenue dashboard: last-month column + store name, keyed by license
        _rev = df[[prev_month, "Store Name"]].copy()
        _rev.index.name = "License"
        _rev = _rev.reset_index()
        _rev["License"] = _rev["License"].astype(str)

        # Outer join so neither source drops rows
        mom = _curr_rev.merge(_rev, on="License", how="outer")
        # Prefer revenue-dashboard store name; fall back to order-sheet name
        mom["Store Name"] = mom["Store Name"].combine_first(mom["_ord_name"])
        mom = mom.drop(columns=["_ord_name"])
        mom = mom.rename(columns={prev_month: "Last Month"})
        mom["Current Month"] = mom["Current Month"].fillna(0)
        mom["Last Month"]    = mom["Last Month"].fillna(0)
        mom["$ Change"] = mom["Current Month"] - mom["Last Month"]
        mom["% Change"] = mom.apply(
            lambda r: (r["$ Change"] / r["Last Month"] * 100) if r["Last Month"] != 0 else None,
            axis=1,
        )
        mom = mom.sort_values("Last Month", ascending=False)

        # ── Summary KPIs ──────────────────────────────────────────────────────
        total_base   = mom["Last Month"].sum()
        total_curr   = mom["Current Month"].sum()
        total_change = total_curr - total_base
        total_pct    = (total_change / total_base * 100) if total_base else 0
        gainers = (mom["$ Change"] > 0).sum()
        losers  = (mom["$ Change"] < 0).sum()

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric(prev_month,    fmt_usd(total_base))
        k2.metric(_curr_label,   fmt_usd(total_curr))
        k3.metric("$ Change",    fmt_usd(total_change), delta=fmt_usd(total_change))
        k4.metric("% Change",    f"{total_pct:+.1f}%",  delta=f"{total_pct:+.1f}%")
        k5.metric("Stores",      f"▲ {gainers}  ▼ {losers}")
        _raw_curr_total = _curr_paid["Line Total"].sum()
        st.caption(
            f"Current month ({_curr_label}) · {len(_curr_paid)} paid lines · "
            f"raw order-sheet total: {fmt_usd(_raw_curr_total)} · "
            f"shown in table: {fmt_usd(total_curr)} · "
            f"order sheet date range: "
            f"{_ord_df_mom['Submitted Date'].min().strftime('%m/%d/%Y') if not _ord_df_mom['Submitted Date'].isna().all() else 'N/A'}"
            f" – "
            f"{_ord_df_mom['Submitted Date'].max().strftime('%m/%d/%Y') if not _ord_df_mom['Submitted Date'].isna().all() else 'N/A'}"
        )

        st.divider()

        # ── Store-level table ─────────────────────────────────────────────────
        mom_search = st.text_input("Search stores", placeholder="Store name or license…", key="mom_search")
        disp_mom = mom.copy()
        if mom_search:
            _q = mom_search.lower()
            disp_mom = disp_mom[
                disp_mom["Store Name"].str.lower().str.contains(_q, na=False)
                | disp_mom["License"].astype(str).str.contains(_q, na=False)
            ]

        def _mom_row_style(row):
            pct = row.get("% Change")
            if pct is not None and not pd.isna(pct):
                if pct <= -25:
                    return ["background-color: rgba(255,150,150,0.25)"] * len(row)
                if pct >= 25:
                    return ["background-color: rgba(100,220,130,0.25)"] * len(row)
            else:
                # Last Month = $0 — use $ Change direction
                chg = row.get("$ Change", 0) or 0
                if chg > 0:
                    return ["background-color: rgba(100,220,130,0.25)"] * len(row)
                if chg < 0:
                    return ["background-color: rgba(255,150,150,0.25)"] * len(row)
            return [""] * len(row)

        styled_mom = (
            disp_mom.rename(columns={"Last Month": prev_month, "Current Month": _curr_label})
            .style
            .apply(_mom_row_style, axis=1)
            .format({
                prev_month:    "${:,.0f}",
                _curr_label:   "${:,.0f}",
                "$ Change":    "${:,.0f}",
                "% Change":    lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            })
        )
        st.dataframe(styled_mom, width="stretch", hide_index=True)
        st.caption(f"{len(disp_mom)} store{'s' if len(disp_mom) != 1 else ''}")

        # ── Top movers chart ──────────────────────────────────────────────────
        st.divider()
        st.subheader("Top Movers")
        n_movers = 15
        top_up   = mom[mom["$ Change"] > 0].nlargest(n_movers,  "$ Change")
        top_down = mom[mom["$ Change"] < 0].nsmallest(n_movers, "$ Change")
        movers   = pd.concat([top_up, top_down]).sort_values("$ Change", ascending=True)

        if not movers.empty:
            try:
                _mom_contact_log = load_contact_log()
                _mom_contact_status = contact_status_by_license(_mom_contact_log)
            except Exception:
                _mom_contact_status = {}

            movers_chart = movers.copy()
            movers_chart["License"] = movers_chart["License"].astype(str)
            movers_chart["Contact Status"] = movers_chart["License"].apply(
                lambda lic: _mom_contact_status.get(license_match_key(lic), "Not Contacted")
            )
            movers_chart["Store Label"] = movers_chart.apply(
                lambda r: (
                    f"{r['Store Name']} 🔴"
                    if r["Contact Status"] == "Not Contacted" and r["$ Change"] < 0
                    else r["Store Name"]
                ),
                axis=1,
            )
            movers_chart = movers_chart.merge(
                _curr_brand_rev[["License"] + _mom_brand_cols],
                on="License",
                how="left",
            )
            for _brand in _mom_brand_cols:
                movers_chart[_brand] = pd.to_numeric(movers_chart[_brand], errors="coerce").fillna(0)

            movers_chart["Change Label"] = movers_chart["$ Change"].apply(
                lambda v: f"Δ {'+' if v >= 0 else ''}{fmt_usd(v)}"
            )
            movers_chart["Last Month Label"] = movers_chart["Last Month"].apply(
                lambda v: f"Last {fmt_usd(v)}"
            )
            movers_chart["Mover Label"] = movers_chart.apply(
                lambda r: f"{r['Change Label']} · {r['Last Month Label']}",
                axis=1,
            )
            _plot_brand_cols = list(_mom_brand_cols)
            if (movers_chart["Current Month"] <= 0).any():
                movers_chart["No Current Orders"] = 0
                _plot_brand_cols.append("No Current Orders")
            _max_abs_change = max(movers_chart["$ Change"].abs().max(), 1)
            _x_limit = _max_abs_change * 1.55

            fig_mom = go.Figure()
            for _brand in _plot_brand_cols:
                _segment_values = []
                _share_text = []
                _customdata = []
                for _, _row in movers_chart.iterrows():
                    _change = float(_row["$ Change"])
                    _current_total = float(_row["Current Month"])
                    if _brand == "No Current Orders":
                        _brand_value = 0
                        _share = 100 if _current_total <= 0 else 0
                        _segment = _change if _current_total <= 0 else 0
                    else:
                        _brand_value = float(_row[_brand])
                        _share = (_brand_value / _current_total * 100) if _current_total else 0
                        _segment = _change * (_brand_value / _current_total) if _current_total else 0
                    _segment_values.append(_segment)
                    _share_text.append(f"{_share:.0f}%" if abs(_segment) >= _max_abs_change * 0.08 and _share >= 12 else "")
                    _customdata.append([
                        _row["License"],
                        _row["Current Month"],
                        _row["Last Month"],
                        _change,
                        _share,
                        _row["Change Label"],
                        _brand_value,
                    ])
                fig_mom.add_trace(go.Bar(
                    x=_segment_values,
                    y=movers_chart["Store Label"],
                    name=_brand,
                    orientation="h",
                    marker_color=_mom_brand_colors.get(_brand, "#B7BCC6"),
                    text=_share_text,
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(color="#111", size=11),
                    customdata=_customdata,
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "License: %{customdata[0]}<br>"
                        f"{_brand} current revenue: " + "%{customdata[6]:$,.0f}<br>"
                        "Brand share: %{customdata[4]:.1f}%<br>"
                        "Change segment: %{x:$,.0f}<br>"
                        "Current: %{customdata[1]:$,.0f}<br>"
                        f"{prev_month}: " + "%{customdata[2]:$,.0f}<br>"
                        "Change: %{customdata[5]}<extra></extra>"
                    ),
                ))

            fig_mom.add_trace(go.Scatter(
                x=movers_chart["$ Change"],
                y=movers_chart["Store Label"],
                mode="text",
                name=f"Change and {prev_month}",
                text=movers_chart["Mover Label"],
                textposition=[
                    "middle right" if v >= 0 else "middle left"
                    for v in movers_chart["$ Change"]
                ],
                textfont=dict(color="#F7F8FA", size=11),
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Change: %{x:$,.0f}<br>"
                    f"{prev_month}: " + "%{customdata:$,.0f}<extra></extra>"
                ),
                customdata=movers_chart["Last Month"],
                showlegend=False,
            ))
            fig_mom.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8",
                barmode="relative",
                height=max(340, len(movers_chart) * 34),
                margin=dict(l=0, r=150, t=10, b=10),
                xaxis=dict(
                    title="$ change, segmented by current-month brand share",
                    tickprefix="$",
                    tickformat=",",
                    gridcolor="rgba(255,255,255,0.14)",
                    zeroline=True,
                    zerolinecolor="rgba(255,255,255,0.75)",
                    zerolinewidth=2,
                    range=[-_x_limit, _x_limit],
                ),
                yaxis=dict(
                    title="",
                    categoryorder="array",
                    categoryarray=movers_chart["Store Label"].tolist(),
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, title=None),
                hoverlabel=dict(bgcolor="#1C2028", font_color="#F7F8FA"),
            )
            st.plotly_chart(fig_mom, width="stretch")
