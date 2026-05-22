import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
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
from datetime import datetime

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

    raw_month_headers = headers[2:]
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
    return df, months, stripped

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
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={url_gid}"

@st.cache_data(ttl=300, show_spinner=False)
def load_google_sheet_as_tsv(sheet_url, gid="0"):
    csv_url = google_sheet_csv_url(sheet_url, gid)
    sheet_df = pd.read_csv(csv_url).dropna(how="all").dropna(axis=1, how="all")
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
    if "Submitted Date" in odf.columns:
        odf["Submitted Date"] = pd.to_datetime(odf["Submitted Date"], errors="coerce")
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
    import requests as _req
    csv_url = google_sheet_csv_url(sheet_url, gid)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = _req.get(csv_url, headers=headers, allow_redirects=True, timeout=15)
    if resp.status_code != 200:
        raise ValueError(
            f"Google returned HTTP {resp.status_code}. "
            f"Try using a Publish-to-web CSV URL instead: in Google Sheets go to "
            f"File → Share → Publish to web → choose the sheet → CSV → Publish, "
            f"then paste that URL here. (URL tried: {csv_url})"
        )
    from io import StringIO as _StringIO
    raw = pd.read_csv(_StringIO(resp.text)).dropna(how="all").dropna(axis=1, how="all")
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
        for col in ("initials TEXT", "person_contacted TEXT", "contact_method TEXT"):
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

def upsert_contact_log_rows(rows: list[dict]):
    """Insert or replace contact log entries (unique per license+month)."""
    init_storage()
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(storage_path()) as conn:
        conn.executemany("""
            INSERT INTO contact_log
                (license, store_name, contact_month, revenue,
                 date_contacted, commitment_made, committed_cadence,
                 committed_amount, notes, initials, person_contacted,
                 contact_method, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                saved_at          = excluded.saved_at
        """, [
            (r["license"], r["store_name"], r["contact_month"], r.get("revenue"),
             r.get("date_contacted"), r.get("commitment_made"), r.get("committed_cadence"),
             r.get("committed_amount"), r.get("notes"), r.get("initials"),
             r.get("person_contacted"), r.get("contact_method"), now)
            for r in rows
        ])

def load_contact_log() -> pd.DataFrame:
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT license, store_name, contact_month, revenue,
                   date_contacted, commitment_made, committed_cadence,
                   committed_amount, notes, initials, person_contacted,
                   contact_method, saved_at
            FROM contact_log
            ORDER BY saved_at DESC
        """).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows], columns=[
        "license", "store_name", "contact_month", "revenue",
        "date_contacted", "commitment_made", "committed_cadence",
        "committed_amount", "notes", "initials", "person_contacted",
        "contact_method", "saved_at",
    ]).rename(columns={
        "license": "License", "store_name": "Store Name",
        "contact_month": "Month", "revenue": "Revenue",
        "date_contacted": "Date Contacted", "commitment_made": "Commitment",
        "committed_cadence": "Cadence",
        "committed_amount": "Committed Amount", "notes": "Notes",
        "initials": "Initials", "person_contacted": "Person Contacted",
        "contact_method": "Contact Method", "saved_at": "Saved At",
    })

def delete_contact_log_entry(license_id: str, month: str):
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute(
            "DELETE FROM contact_log WHERE license = ? AND contact_month = ?",
            (license_id, month)
        )

def clear_contact_log():
    init_storage()
    with sqlite3.connect(storage_path()) as conn:
        conn.execute("DELETE FROM contact_log")

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
  .metric-label {{font-size:11px;color:#666;margin:0}}
  .metric-value {{font-size:22px;font-weight:600;color:#111;margin:0}}
  [data-testid="stMetric"] label {{font-size:12px !important}}
</style>
""", unsafe_allow_html=True)

# ── Sidebar: data input ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Data Input")
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
    if st.button("Refresh Google Sheet", use_container_width=True):
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
        if st.button("Load alternate sheet", use_container_width=True):
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
        if load_col.button("Load", use_container_width=True):
            loaded_text = get_saved_dataset(saved_options[selected_saved])
            if loaded_text:
                st.session_state.raw_input = loaded_text
                st.session_state.data_source_label = f"Saved dataset · {selected_saved.split(' · ')[0]}"
                st.session_state.storage_notice = f"Loaded {selected_saved.split(' · ')[0]}."
                st.rerun()
        if delete_col.button("Delete", use_container_width=True):
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
            if st.button("Refresh Order Sheet", use_container_width=True):
                try:
                    _shape = load_order_sheet_into_session(
                        _saved_url, get_setting("order_sheet_gid", "0"), clear_cache=True
                    )
                    st.session_state.pop("order_sheet_error", None)
                    st.session_state["order_data_label"] = f"Google Sheet · {_shape[0]} rows · {_shape[1]} columns"
                    st.rerun()
                except Exception as _e:
                    st.error(f"Could not refresh: {_e}")
        if st.button("Clear order data", use_container_width=True):
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
        if st.button("Load & save as default", use_container_width=True, key="load_order_sheet"):
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
        if _cur_url and st.button("Remove default sheet", use_container_width=True, key="remove_order_sheet"):
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
df, months, stripped = None, [], []
if raw_input.strip():
    try:
        df, months, stripped = parse_input(raw_input)
    except Exception as e:
        st.error(str(e))

if df is None:
    st.info("Paste your data in the sidebar to get started, or click **Load demo data**.")
    st.stop()

if stripped:
    st.warning(f"Auto-removed column{'s' if len(stripped)>1 else ''}: {', '.join(f'"{s}"' for s in stripped)}")

top_lics, grand = compute_pareto(df, months, threshold)
all_lics = df.index.tolist()
all_totals = df[months].sum(axis=1)
month_totals = df[months].sum()
avg_month = month_totals.mean()
peak_month = month_totals.idxmax()
top_store = all_totals.idxmax()
report_date = datetime.now().strftime("%B %d, %Y")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_contact, tab_top, tab_all, tab_orders = st.tabs([
    "📋 Store Contact Form",
    f"⭐ Top {int(threshold*100)}% Stores",
    "📊 All Stores",
    "📦 Order Activity",
])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — All stores                                               ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_all:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Revenue", fmt_usd(grand))
    c2.metric("Avg Monthly", fmt_usd(avg_month))
    c3.metric("Peak Month", peak_month)
    c4.metric("Top Store", df.loc[top_store, "Store Name"])

    st.divider()

    # Share by store
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
        st.dataframe(display, use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("Revenue share")
        if month_total > 0:
            fig_pie = px.pie(
                share_df.reset_index(), values=sel_month, names="Store Name",
                color_discrete_sequence=px.colors.qualitative.Set2,
                hole=0.4
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No revenue for the selected month.")

    share_report = build_share_by_store_pdf(df, sel_month, sort_by, report_date=report_date)
    st.download_button(
        "⬇ Download Share by Store Report",
        data=share_report,
        file_name=f"share-by-store-{slugify(sel_month)}-{slugify(sort_by)}.pdf",
        mime="application/pdf",
        key="t1_share_report"
    )

    st.divider()

    # Monthly totals chart
    st.subheader("Monthly totals")
    fig_bar = go.Figure(go.Bar(
        x=months, y=[month_totals[m] for m in months],
        marker_color=BLUE, text=[fmt_usd(month_totals[m]) for m in months],
        textposition="outside"
    ))
    fig_bar.update_layout(
        yaxis_tickformat="$,.0f", margin=dict(t=20, b=20), height=320,
        plot_bgcolor="white", yaxis=dict(gridcolor="#eee")
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # Monthly table
    mt = pd.DataFrame({
        "Month": months,
        "Total": [fmt_usd(month_totals[m]) for m in months],
        "vs Avg": [("+" if month_totals[m] >= avg_month else "") + fmt_usd(month_totals[m] - avg_month) for m in months],
    })
    st.dataframe(mt, use_container_width=True, hide_index=True)

    st.divider()

    # Store trends
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
            fig_line.add_trace(go.Scatter(
                x=months, y=[df.loc[lic, m] for m in months],
                name=df.loc[lic, "Store Name"], mode="lines+markers",
                line=dict(color=palette[i % len(palette)], width=2)
            ))
        fig_line.update_layout(
            yaxis_tickformat="$,.0f", height=320, margin=dict(t=10, b=10),
            plot_bgcolor="white", yaxis=dict(gridcolor="#eee"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig_line, use_container_width=True)

    st.divider()
    pdf_buf = build_pdf(df, months, report_date=report_date)
    st.download_button(
        "⬇ Download PDF Report",
        data=pdf_buf,
        file_name="store-sales-dashboard.pdf",
        mime="application/pdf"
    )

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Pareto / top X%                                          ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_top:
    # ── Time window filter ────────────────────────────────────────────────────
    _default_window = last_n_month_cols(months, 3)
    window_months = st.multiselect(
        "Time window", months, default=_default_window, key="t2_window",
        help="Months included in Pareto ranking and group metrics"
    )
    if not window_months:
        window_months = _default_window

    w_totals = df[window_months].sum(axis=1)
    w_grand = w_totals.sum()
    w_top_lics, _ = compute_pareto(df, window_months, threshold)

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

    st.dataframe(
        pareto_df.style.apply(highlight_in, axis=1),
        use_container_width=True, hide_index=True
    )

    remaining_pct = max(0, 100 - act_pct) if w_grand else 0.0
    st.caption(f"Remaining {len(all_lics)-len(w_top_lics)} store{'s' if len(all_lics)-len(w_top_lics)!=1 else ''} account for {remaining_pct:.1f}% of total revenue · window: {', '.join(window_months)}")

    st.divider()

    # Share by store (top group only)
    st.subheader("Share by store")

    # ── Controls ─────────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([1, 1, 1, 2])
    _from_default = months.index(window_months[0])
    _to_default   = months.index(window_months[-1])
    from_month = fc1.selectbox("From", months, index=_from_default, key="t2_from")
    to_month   = fc2.selectbox("To",   months, index=_to_default,   key="t2_to")
    sort_by2   = fc3.selectbox("Sort", SORT_OPTIONS, key="t2_sort")
    search2    = fc4.text_input("Search", placeholder="Store name or license…", key="t2_search")

    # Resolve range (swap if user picks From > To)
    fi, ti = months.index(from_month), months.index(to_month)
    if fi > ti:
        fi, ti = ti, fi
    range_months = months[fi: ti + 1]
    range_label  = from_month if fi == ti else f"{from_month} – {to_month}"

    # Revenue summed across range
    share_df2 = df.loc[w_top_lics, ["Store Name"] + range_months].copy()
    share_df2["_rev"] = share_df2[range_months].sum(axis=1)
    all_rev_range = df[range_months].sum(axis=1).sum()
    grp_rev_range = share_df2["_rev"].sum()
    share_df2["% of Group"] = share_df2["_rev"].apply(lambda v: pct_value(v, grp_rev_range))
    share_df2["% of All"]   = share_df2["_rev"].apply(lambda v: pct_value(v, all_rev_range))

    # Sort then search-filter (rank reflects sorted order before filtering)
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
    st.dataframe(disp2, use_container_width=True, hide_index=True)

    st.subheader("Revenue share")
    if grp_rev_range > 0 and not share_df2.empty:
        fig_pie2 = px.pie(
            share_df2.reset_index(), values="_rev", names="Store Name",
            color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4
        )
        fig_pie2.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie2.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_pie2, use_container_width=True)
    else:
        st.info("No revenue for the selected period.")

    share_report2 = build_share_by_store_pdf(
        df, to_month, sort_by2,
        top_lics=w_top_lics, threshold=threshold,
        report_date=report_date
    )
    st.download_button(
        f"⬇ Download Share by Store Report — Top {int(threshold*100)}%",
        data=share_report2,
        file_name=f"share-by-store-top-{int(threshold*100)}pct-{slugify(range_label)}-{slugify(sort_by2)}.pdf",
        mime="application/pdf",
        key="t2_share_report"
    )

    st.divider()

    # Monthly totals — group vs all
    st.subheader("Monthly totals — group vs all stores")
    grp_m = df.loc[w_top_lics, months].sum()
    all_m = df[months].sum()
    fig_bar2 = go.Figure()
    fig_bar2.add_trace(go.Bar(x=months, y=[grp_m[m] for m in months], name=f"Top {int(threshold*100)}% stores", marker_color=BLUE))
    fig_bar2.add_trace(go.Bar(x=months, y=[all_m[m] for m in months], name="All stores", marker_color="#B5D4F4"))
    fig_bar2.update_layout(
        barmode="group", yaxis_tickformat="$,.0f", height=300,
        margin=dict(t=10, b=10), plot_bgcolor="white",
        yaxis=dict(gridcolor="#eee"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02)
    )
    st.plotly_chart(fig_bar2, use_container_width=True)

    mt2 = pd.DataFrame({
        "Month": months,
        "Group": [fmt_usd(grp_m[m]) for m in months],
        "All Stores": [fmt_usd(all_m[m]) for m in months],
        "Group Share": [pct(grp_m[m], all_m[m]) for m in months],
    })
    st.dataframe(mt2, use_container_width=True, hide_index=True)

    st.divider()

    # Trend chart (top stores only)
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
        fig_line2.update_layout(
            yaxis_tickformat="$,.0f", height=300, margin=dict(t=10, b=10),
            plot_bgcolor="white", yaxis=dict(gridcolor="#eee"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig_line2, use_container_width=True)

    st.divider()
    pdf_buf2 = build_pdf(df, months, top_lics=w_top_lics, threshold=threshold, report_date=report_date)
    st.download_button(
        f"⬇ Download PDF Report — Top {int(threshold*100)}%",
        data=pdf_buf2,
        file_name=f"pareto-dashboard-{int(threshold*100)}pct.pdf",
        mime="application/pdf"
    )

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Store Contact Form                                        ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_contact:
    contact_month = find_last_month_col(months)
    today_date = datetime.now().date()

    st.caption(f"Top 30 stores by **{contact_month}** revenue · Ranked highest to lowest")

    top30_lics = (
        df[contact_month]
        .sort_values(ascending=False)
        .head(30)
        .index.tolist()
    )

    AMOUNT_OPTIONS = [
        "", "$500–$1,000", "$1,000–$2,500", "$2,500–$5,000",
        "$5,000–$10,000", "$10,000–$15,000", "$15,000–$25,000",
    ]
    CADENCE_OPTIONS = ["", "Weekly", "Bi-Weekly", "Monthly", "Other"]

    INITIALS_OPTIONS = ["", "DK", "CH"]

    METHOD_OPTIONS = ["", "In-person", "Phone", "Email"]

    # Load saved entries for this month to pre-populate widgets
    _saved_log = load_contact_log()
    _saved_map: dict = {}
    if not _saved_log.empty:
        for _, _r in _saved_log[_saved_log["Month"] == contact_month].iterrows():
            _saved_map[_r["License"]] = _r.to_dict()

    def _saved(lic, field, default=""):
        v = _saved_map.get(lic, {}).get(field, default)
        return v if v is not None else default

    def _sel_idx(options, value):
        return options.index(value) if value in options else 0

    # Search filter
    contact_search = st.text_input(
        "Search stores", placeholder="Store name or license…", key="contact_search"
    )
    _q = contact_search.lower()
    display_lics = [
        lic for lic in top30_lics
        if not _q
        or _q in df.loc[lic, "Store Name"].lower()
        or _q in lic.lower()
    ]

    for rank, lic in enumerate(display_lics, 1):
        store_name = df.loc[lic, "Store Name"]
        revenue = fmt_usd(df.loc[lic, contact_month])
        has_saved = lic in _saved_map
        label = f"{'✅ ' if has_saved else ''}#{rank}  {store_name}  ·  {lic}  ·  {revenue}"
        with st.expander(label):
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
            r1b.selectbox("Initials", INITIALS_OPTIONS,
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

            st.text_area("Notes", value=_saved(lic, "Notes"),
                         height=120, key=f"cf_{lic}_notes")

    st.divider()
    save_col, dl_col, reset_col = st.columns([2, 2, 1])

    if save_col.button("💾 Save to Team Log", use_container_width=True, type="primary"):
        rows_to_save = []
        for lic in top30_lics:
            commitment = st.session_state.get(f"cf_{lic}_commitment", "No")
            cadence    = st.session_state.get(f"cf_{lic}_cadence", "")
            amount     = st.session_state.get(f"cf_{lic}_amount", "")
            notes      = st.session_state.get(f"cf_{lic}_notes", "")
            has_entry  = (
                commitment == "Yes"
                or bool(str(cadence or "").strip())
                or bool(str(amount or "").strip())
                or bool(str(notes or "").strip())
            )
            if has_entry:
                rows_to_save.append({
                    "license":           lic,
                    "store_name":        df.loc[lic, "Store Name"],
                    "contact_month":     contact_month,
                    "revenue":           fmt_usd(df.loc[lic, contact_month]),
                    "date_contacted":    str(st.session_state.get(f"cf_{lic}_date", today_date)),
                    "initials":          st.session_state.get(f"cf_{lic}_initials", ""),
                    "person_contacted":  st.session_state.get(f"cf_{lic}_person", ""),
                    "contact_method":    st.session_state.get(f"cf_{lic}_method", ""),
                    "commitment_made":   commitment,
                    "committed_cadence": cadence,
                    "committed_amount":  amount,
                    "notes":             notes,
                })
        if rows_to_save:
            upsert_contact_log_rows(rows_to_save)
            st.success(f"Saved {len(rows_to_save)} entr{'y' if len(rows_to_save)==1 else 'ies'} to team log.")
        else:
            st.info("No entries to save — fill in at least one field per store.")

    # Build CSV from current widget state
    _csv_rows = []
    for lic in top30_lics:
        _csv_rows.append({
            "License":           lic,
            "Store Name":        df.loc[lic, "Store Name"],
            "Month":             contact_month,
            "Revenue":           fmt_usd(df.loc[lic, contact_month]),
            "Date Contacted":    str(st.session_state.get(f"cf_{lic}_date", today_date)),
            "Initials":          st.session_state.get(f"cf_{lic}_initials", ""),
            "Person Contacted":  st.session_state.get(f"cf_{lic}_person", ""),
            "Contact Method":    st.session_state.get(f"cf_{lic}_method", ""),
            "Commitment Made":   st.session_state.get(f"cf_{lic}_commitment", "No"),
            "Committed Cadence": st.session_state.get(f"cf_{lic}_cadence", ""),
            "Committed Amount":  st.session_state.get(f"cf_{lic}_amount", ""),
            "Notes":             st.session_state.get(f"cf_{lic}_notes", ""),
        })
    dl_col.download_button(
        "⬇ Download as CSV",
        data=pd.DataFrame(_csv_rows).to_csv(index=False),
        file_name=f"store-contacts-{slugify(contact_month)}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    if reset_col.button("Reset", use_container_width=True):
        for lic in top30_lics:
            for field in ("date", "initials", "person", "method",
                          "commitment", "cadence", "amount", "notes"):
                st.session_state.pop(f"cf_{lic}_{field}", None)
        st.rerun()

    # ── Team Log ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Team Contact Log")
    st.caption("All saved contact entries across all months, visible to the entire team.")

    log_df = load_contact_log()
    if log_df.empty:
        st.info("No entries saved yet. Fill in the form above and click **Save to Team Log**.")
    else:
        # Search / filter
        filter_col, dl_log_col, clear_col = st.columns([3, 2, 1])
        search = filter_col.text_input("Filter by store name or license", placeholder="Search…", label_visibility="collapsed")
        if search:
            mask = (
                log_df["Store Name"].str.contains(search, case=False, na=False)
                | log_df["License"].str.contains(search, case=False, na=False)
            )
            log_df = log_df[mask]

        st.dataframe(
            log_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Saved At": st.column_config.TextColumn("Saved At"),
                "Store Name": st.column_config.TextColumn(width="large"),
                "Notes": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(f"{len(log_df)} entr{'y' if len(log_df)==1 else 'ies'}")

        log_csv = log_df.to_csv(index=False)
        dl_log_col.download_button(
            "⬇ Download Full Log (CSV)",
            data=log_csv,
            file_name="team-contact-log.csv",
            mime="text/csv",
            use_container_width=True,
        )
        if clear_col.button("Clear All", use_container_width=True):
            clear_contact_log()
            st.rerun()

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
    ch1.plotly_chart(fig_rev, use_container_width=True)

    fig_units = px.bar(
        brand_summary, x="Units", y="Brand", orientation="h",
        color="Brand", color_discrete_map=BRAND_COLORS, text_auto=True,
    )
    fig_units.update_layout(showlegend=False, margin=dict(t=10, b=10), height=220)
    fig_units.update_yaxes(autorange="reversed")
    ch2.plotly_chart(fig_units, use_container_width=True)

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
        st.plotly_chart(fig_time, use_container_width=True)

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
                st.plotly_chart(fig_prod, use_container_width=True)
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
        values="Units",
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
        )
        .reset_index()
    )
    store_table = store_totals.merge(store_pivot, on=["Client", "License #"], how="left")
    for brand in BRANDS:
        if brand not in store_table.columns:
            store_table[brand] = 0
    store_table = store_table.sort_values("Total_Revenue", ascending=False)
    store_table["Total_Revenue"] = store_table["Total_Revenue"].apply(fmt_usd)

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
    })[["Store", "License", "Orders", "Revenue", "Total Units"] + BRANDS]
    st.dataframe(disp_store, use_container_width=True, hide_index=True)

    st.divider()

    # Store drill-down
    st.subheader("Store Order Detail")
    _top_store = (
        paid_view.groupby("Client")["Line Total"].sum().idxmax()
        if not paid_view.empty else None
    )
    store_names = (
        paid_view.groupby("Client")["Line Total"].sum()
        .sort_values(ascending=False).index.tolist()
    )
    _default_idx = store_names.index(_top_store) if _top_store in store_names else 0
    selected_store = st.selectbox("Select store", store_names, index=_default_idx, key="ord_store_select")
    if selected_store:
        store_orders = paid_view[paid_view["Client"] == selected_store].copy()
        store_orders["Submitted Date"] = store_orders["Submitted Date"].dt.strftime("%m/%d/%Y")
        store_orders["Line Total"] = store_orders["Line Total"].apply(fmt_usd)
        detail_cols = ["Order #", "Submitted Date", "Brand", "Product", "Units", "Line Total", "Status"]
        detail_cols = [c for c in detail_cols if c in store_orders.columns]
        st.dataframe(
            store_orders[detail_cols].sort_values("Order #"),
            use_container_width=True,
            hide_index=True,
        )
