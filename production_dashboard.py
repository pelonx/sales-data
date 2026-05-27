import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import sqlite3
import os
from datetime import datetime
from io import StringIO
from urllib.parse import urlparse

st.set_page_config(page_title="Production Sales", layout="wide")

# ── Constants ─────────────────────────────────────────────────────────────────
BRAND_VENDORS   = {"Minglewood Brands", "SALISH SEA INDUSTRIES L.L.C."}
EXCLUDE_VENDORS = {"CONFIDENCE ANALYTICS"}

BRANDS_PROD = ["K. Savage", "Mayfield", "Leisure Land", "Clout King"]

BRAND_COLORS = {
    "K. Savage":   "#4CE89C",
    "Mayfield":    "#E8844C",
    "Leisure Land":"#4C9BE8",
    "Clout King":  "#E84C9B",
    "Unassigned":  "#888888",
}
FACILITY_COLORS = {
    "Block 13": "#4CE89C",
    "B-9":      "#9B6BE8",
}

def fmt_usd(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "$0"
    return f"${v:,.0f}"

def fmt_g(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "0 g"
    return f"{v:,.0f} g"

def _shade_hex(hex_color: str, factor: float) -> str:
    """factor < 1 darkens toward black; factor > 1 lightens toward white."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if factor > 1:
        t = min(factor - 1, 1)
        r = int(r + (255 - r) * t)
        g = int(g + (255 - g) * t)
        b = int(b + (255 - b) * t)
    else:
        r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{min(r,255):02x}{min(g,255):02x}{min(b,255):02x}"

# ── SQLite persistence ────────────────────────────────────────────────────────
def _db_path() -> str:
    d = os.path.expanduser("~/.streamlit_prod")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "assignments.db")

def _init_db():
    con = sqlite3.connect(_db_path())
    con.execute(
        "CREATE TABLE IF NOT EXISTS strain_brands "
        "(strain TEXT PRIMARY KEY, brand TEXT NOT NULL)"
    )
    con.commit()
    con.close()

def load_strain_map() -> dict:
    _init_db()
    con = sqlite3.connect(_db_path())
    rows = con.execute("SELECT strain, brand FROM strain_brands").fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def save_strain_map(mapping: dict):
    _init_db()
    con = sqlite3.connect(_db_path())
    con.execute("DELETE FROM strain_brands")
    con.executemany(
        "INSERT INTO strain_brands (strain, brand) VALUES (?, ?)",
        mapping.items(),
    )
    con.commit()
    con.close()

def _init_settings_db():
    con = sqlite3.connect(_db_path())
    con.execute(
        "CREATE TABLE IF NOT EXISTS settings "
        "(key TEXT PRIMARY KEY, value TEXT)"
    )
    con.commit()
    con.close()

def load_settings() -> dict:
    _init_settings_db()
    con = sqlite3.connect(_db_path())
    rows = con.execute("SELECT key, value FROM settings").fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def save_setting(key: str, value: str):
    _init_settings_db()
    con = sqlite3.connect(_db_path())
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()
    con.close()

# ── Data loading ──────────────────────────────────────────────────────────────
def load_assignments_from_sheet(url: str, gid: str) -> dict:
    """Load strain→brand mapping from a two-column sheet tab (Strain, Brand)."""
    try:
        raw = load_tab(url, gid)
        raw.columns = [c.strip() for c in raw.columns]
        if "Strain" not in raw.columns or "Brand" not in raw.columns:
            return {}
        raw = raw[["Strain", "Brand"]].dropna()
        return dict(zip(raw["Strain"].astype(str).str.strip(),
                        raw["Brand"].astype(str).str.strip()))
    except Exception:
        return {}

def _csv_url(url: str, gid: str) -> str:
    url = url.strip()
    if "output=csv" in url or "format=csv" in url:
        return url
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    try:
        sheet_id = parts[parts.index("d") + 1]
    except (ValueError, IndexError):
        raise ValueError("Could not parse sheet ID from URL.")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

@st.cache_data(ttl=300, show_spinner=False)
def load_tab(url: str, gid: str) -> pd.DataFrame:
    csv_url = _csv_url(url, gid)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(csv_url, headers=headers, allow_redirects=True, timeout=15)
    if resp.status_code != 200:
        raise ValueError(
            f"Google returned HTTP {resp.status_code}. "
            "Try File → Share → Publish to web → select tab → CSV → paste that URL."
        )
    raw = pd.read_csv(StringIO(resp.text)).dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        raise ValueError("Tab is empty or could not be parsed.")
    return raw

def _clean_numeric(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(r"[$,]", "", regex=True)
        .str.strip()
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0)
    )

def parse_tab(raw: pd.DataFrame, facility: str) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip() for c in df.columns]
    if "Transfer Date" in df.columns:
        df["Transfer Date"] = pd.to_datetime(df["Transfer Date"], errors="coerce")
    for col in ["Units", "Price", "Total", "Cost"]:
        if col in df.columns:
            df[col] = _clean_numeric(df[col])
    for col in ["Vendor", "Strain", "Product", "Units UOM", "Type", "Category"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    df["Facility"] = facility
    df["Month"] = df["Transfer Date"].dt.to_period("M").astype(str)
    return df

# ── Sidebar — Data Source ─────────────────────────────────────────────────────
_saved = load_settings()

with st.sidebar:
    st.header("Data Source")
    st.caption(
        "Paste the Google Sheet URL, then enter the GID for each tab. "
        "Find the GID by clicking the tab in Sheets — it appears in the URL as `#gid=XXXXXXX`."
    )
    sheet_url = st.text_input(
        "Google Sheet URL",
        value=_saved.get("sheet_url", ""),
        placeholder="https://docs.google.com/spreadsheets/d/…",
        key="prod_url",
    )
    col_a, col_b = st.columns(2)
    gid_b13      = col_a.text_input("Block 13 GID",    value=_saved.get("gid_b13",      "0"), key="prod_gid_b13")
    gid_b9       = col_b.text_input("B-9 GID",         value=_saved.get("gid_b9",       ""),  key="prod_gid_b9")
    gid_assign   = st.text_input(   "Assignments GID",  value=_saved.get("gid_assign",   ""),
                                    placeholder="GID of a tab with Strain and Brand columns",
                                    key="prod_gid_assign")

    if st.button("Load / Refresh", type="primary", use_container_width=True):
        if sheet_url.strip():
            save_setting("sheet_url",  sheet_url.strip())
            save_setting("gid_b13",    gid_b13.strip())
            save_setting("gid_b9",     gid_b9.strip())
            save_setting("gid_assign", gid_assign.strip())
        st.cache_data.clear()
        st.rerun()

# ── Load data ─────────────────────────────────────────────────────────────────
if not sheet_url:
    st.info("Paste the Google Sheet URL in the sidebar to get started.")
    st.stop()

dfs, load_errors = [], []
for facility, gid in [("Block 13", gid_b13), ("B-9", gid_b9)]:
    if not gid.strip():
        continue
    try:
        raw = load_tab(sheet_url, gid.strip())
        dfs.append(parse_tab(raw, facility))
    except Exception as e:
        load_errors.append(f"**{facility}**: {e}")

for err in load_errors:
    st.error(err)

if not dfs:
    st.warning("No data loaded. Check the sheet URL and GIDs.")
    st.stop()

all_df = pd.concat(dfs, ignore_index=True)
all_df = all_df[~all_df["Vendor"].isin(EXCLUDE_VENDORS)]

# ── Sidebar — Brand Assignments ───────────────────────────────────────────────
# All strains except explicitly excluded vendors
_brand_strains = sorted(
    all_df[~all_df["Vendor"].isin(EXCLUDE_VENDORS)]["Strain"]
    .dropna()
    .unique()
    .tolist()
)

strain_map = load_strain_map()

# If an assignments sheet tab is configured, it takes priority over SQLite
if gid_assign.strip() and sheet_url.strip():
    _sheet_map = load_assignments_from_sheet(sheet_url.strip(), gid_assign.strip())
    if _sheet_map:
        strain_map = _sheet_map

with st.sidebar:
    st.divider()
    st.header("Brand Assignments")
    if gid_assign.strip():
        st.caption("Assignments loaded from sheet. Edit the Assignments tab in Google Sheets, then click Load / Refresh.")
    else:
        st.caption("Assign strains to brands below, or upload a file. Add an Assignments GID above to load from the sheet automatically.")

    # ── Import / Export ───────────────────────────────────────────────
    if strain_map:
        _export_df = pd.DataFrame(
            list(strain_map.items()), columns=["Strain", "Brand"]
        ).sort_values("Strain")
        st.download_button(
            "⬇ Download assignments (.csv)",
            data=_export_df.to_csv(index=False),
            file_name="strain_assignments.csv",
            mime="text/csv",
            use_container_width=True,
        )

    _upload = st.file_uploader(
        "Upload assignments (.csv or .json)",
        type=["csv", "json"],
        key="assignment_upload",
        label_visibility="collapsed",
    )
    if _upload is not None:
        try:
            if _upload.name.endswith(".json"):
                import json as _json
                _imported = _json.loads(_upload.read().decode())
                if not isinstance(_imported, dict):
                    st.error("JSON must be a {strain: brand} object.")
                    _imported = None
            else:
                _imp_df = pd.read_csv(_upload)
                if "Strain" not in _imp_df.columns or "Brand" not in _imp_df.columns:
                    st.error("CSV must have 'Strain' and 'Brand' columns.")
                    _imported = None
                else:
                    _imported = dict(zip(_imp_df["Strain"].astype(str), _imp_df["Brand"].astype(str)))
            if _imported:
                save_strain_map(_imported)
                strain_map = _imported
                st.success(f"Imported {len(_imported)} assignments.")
                st.rerun()
        except Exception as _e:
            st.error(f"Import failed: {_e}")

    if not _brand_strains:
        st.info("Load data first to see available strains.")
    else:
        _new_assignments: dict[str, str] = {}
        for brand in BRANDS_PROD:
            current = [s for s, b in strain_map.items() if b == brand and s in _brand_strains]
            selected = st.multiselect(
                brand,
                options=_brand_strains,
                default=current,
                key=f"assign_{brand}",
            )
            for s in selected:
                _new_assignments[s] = brand

        # Conflict detection
        _strain_counts: dict[str, list] = {}
        for s, b in _new_assignments.items():
            _strain_counts.setdefault(s, []).append(b)
        _conflicts = {s: bs for s, bs in _strain_counts.items() if len(bs) > 1}
        if _conflicts:
            conflict_lines = "; ".join(
                f"{s} → {', '.join(bs)}" for s, bs in _conflicts.items()
            )
            st.warning(f"Conflict: strain assigned to multiple brands — {conflict_lines}")

        if st.button("Save Assignments", use_container_width=True, disabled=bool(_conflicts)):
            save_strain_map(_new_assignments)
            strain_map = _new_assignments
            st.success("Saved.")

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("<h1 style='color:#e3e3d8'>Production Sales</h1>", unsafe_allow_html=True)

_facilities_loaded = sorted(all_df["Facility"].dropna().unique().tolist())
_fac_options = _facilities_loaded + (["Both"] if len(_facilities_loaded) > 1 else [])
sel_facility = st.radio(
    "Facility",
    _fac_options,
    index=_fac_options.index("Both") if "Both" in _fac_options else 0,
    horizontal=True,
    label_visibility="collapsed",
    key="sel_facility",
)
st.divider()

# Apply facility filter for display
display_df = all_df if sel_facility == "Both" else all_df[all_df["Facility"] == sel_facility]

# Brand Sales = any row whose strain is assigned to a brand (not excluded)
_assigned_strains = set(strain_map.keys())
brand_df = display_df[
    display_df["Strain"].isin(_assigned_strains) &
    ~display_df["Vendor"].isin(EXCLUDE_VENDORS)
].copy()
brand_df["Brand"] = brand_df["Strain"].map(strain_map)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_brand, tab_wholesale = st.tabs(["🏷️ Brand Sales", "🏪 Wholesale"])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Brand Sales                                               ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_brand:
    if brand_df.empty:
        st.info("No Brand Sales records found in the loaded data.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        bf1, bf2, bf3, bf4 = st.columns([2, 2, 1, 1])
        _b_brands = ["All"] + sorted(brand_df["Brand"].dropna().unique().tolist())
        _b_types  = ["All"] + sorted(brand_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _b_dates  = brand_df["Transfer Date"].dropna()
        _b_min = _b_dates.min().date() if not _b_dates.empty else datetime.now().date()
        _b_max = _b_dates.max().date() if not _b_dates.empty else datetime.now().date()

        sel_b_brand = bf1.selectbox("Brand",   _b_brands, key="bs_brand")
        sel_b_type  = bf2.selectbox("Product", _b_types,  key="bs_type")
        b_from = bf3.date_input("From", value=_b_min, min_value=_b_min, max_value=_b_max, key="bs_from")
        b_to   = bf4.date_input("To",   value=_b_max, min_value=_b_min, max_value=_b_max, key="bs_to")

        bview = brand_df.copy()
        if sel_b_brand != "All":
            bview = bview[bview["Brand"] == sel_b_brand]
        if sel_b_type != "All":
            bview = bview[bview["Product"] == sel_b_type]
        bview = bview[bview["Transfer Date"].dt.date.between(b_from, b_to)]

        bview_g = bview[bview["Units UOM"] == "Grams"]

        # ── KPIs ──────────────────────────────────────────────────────────────
        b_rev    = bview["Total"].sum()
        b_grams  = bview_g["Units"].sum()
        b_ppg    = (bview_g["Total"].sum() / b_grams) if b_grams > 0 else 0
        b_strains = bview["Strain"].nunique()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Revenue", fmt_usd(b_rev))
        k2.metric("Total Volume",  fmt_g(b_grams))
        k3.metric("Avg $/gram",    f"${b_ppg:.2f}")
        k4.metric("Strains",       b_strains)

        st.divider()

        # ── Strain summary table ───────────────────────────────────────────────
        st.subheader("Strain by Brand")
        strain_tbl = (
            bview.groupby(["Brand", "Product", "Strain", "Units UOM"])
            .agg(Units=("Units", "sum"), Revenue=("Total", "sum"))
            .reset_index()
        )
        strain_tbl["$/gram"] = strain_tbl.apply(
            lambda r: round(r["Revenue"] / r["Units"], 2)
            if r["Units UOM"] == "Grams" and r["Units"] > 0 else pd.NA,
            axis=1,
        )
        strain_tbl = strain_tbl.sort_values(["Brand", "Revenue"], ascending=[True, False])

        st.dataframe(
            strain_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Revenue":  st.column_config.NumberColumn("Revenue",  format="$%.0f"),
                "Units":    st.column_config.NumberColumn("Units",    format="%.0f"),
                "$/gram":   st.column_config.NumberColumn("$/gram",   format="$%.2f"),
            },
        )

        st.divider()

        # ── Revenue by strain chart ────────────────────────────────────────────
        st.subheader("Revenue by Strain")
        strain_chart = (
            bview_g.groupby(["Strain", "Brand"])["Total"]
            .sum().reset_index()
            .sort_values("Total", ascending=True)
        )
        if not strain_chart.empty:
            fig_s = px.bar(
                strain_chart,
                x="Total", y="Strain", color="Brand",
                orientation="h",
                color_discrete_map=BRAND_COLORS,
                text=strain_chart["Total"].apply(fmt_usd),
            )
            fig_s.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", showlegend=True, legend_title="Brand",
                height=max(350, len(strain_chart) * 24),
                margin=dict(l=0, r=60, t=10, b=10),
                xaxis_title="", yaxis_title="",
            )
            fig_s.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_s, use_container_width=True)

        st.divider()

        # ── $/gram by strain ───────────────────────────────────────────────────
        st.subheader("$/gram by Strain")
        ppg_data = (
            bview_g.groupby(["Strain", "Brand", "Product"])
            .apply(lambda g: g["Total"].sum() / g["Units"].sum() if g["Units"].sum() > 0 else 0)
            .reset_index(name="$/gram")
        )
        if not ppg_data.empty:
            _ppg_types = sorted(ppg_data["Product"].unique().tolist())
            _n_t = max(len(_ppg_types), 1)
            _ppg_cmap = {
                t: _shade_hex("#4CE89C", 0.5 + (i / max(_n_t - 1, 1)) * 0.9)
                for i, t in enumerate(_ppg_types)
            }
            # Sort product types by their median $/gram so gradient maps cheapest→most expensive
            _prod_order = (
                ppg_data.groupby("Product")["$/gram"].median()
                .sort_values().index.tolist()
            )
            _ppg_cmap = {
                t: _shade_hex("#4CE89C", 0.45 + (i / max(len(_prod_order) - 1, 1)) * 0.85)
                for i, t in enumerate(_prod_order)
            }
            # Sort strains by total $/gram (sum of segments) descending → highest at top
            _strain_order = (
                ppg_data.groupby("Strain")["$/gram"].sum()
                .sort_values(ascending=True).index.tolist()
            )
            ppg_data["Strain"] = pd.Categorical(ppg_data["Strain"], categories=_strain_order, ordered=True)
            ppg_data["Product"] = pd.Categorical(ppg_data["Product"], categories=_prod_order, ordered=True)
            ppg_data = ppg_data.sort_values(["Strain", "Product"])
            ppg_data["_label"] = ppg_data.apply(
                lambda r: f"${r['$/gram']:.2f}  {r['Product']}", axis=1
            )
            fig_ppg = px.bar(
                ppg_data,
                x="$/gram", y="Strain", color="Product",
                orientation="h", barmode="stack",
                color_discrete_map=_ppg_cmap,
                text="_label",
                custom_data=["Brand", "Product"],
            )
            fig_ppg.update_traces(
                textposition="inside", insidetextanchor="middle",
                hovertemplate="%{customdata[1]}<br>%{y} (%{customdata[0]})<br>$/gram: %{x:.2f}<extra></extra>",
                cliponaxis=False,
            )
            fig_ppg.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", showlegend=True, legend_title="Product",
                height=max(350, ppg_data["Strain"].nunique() * 28),
                margin=dict(l=0, r=20, t=10, b=10),
                xaxis_title="$ per gram", yaxis_title="",
            )
            st.plotly_chart(fig_ppg, use_container_width=True)

        st.divider()

        # ── Monthly revenue trend ──────────────────────────────────────────────
        st.subheader("Monthly Revenue")
        monthly = (
            bview_g[bview_g["Month"].str.match(r"\d{4}-\d{2}")]
            .groupby(["Month", "Brand"])["Total"]
            .sum().reset_index()
            .sort_values("Month")
        )
        if not monthly.empty:
            fig_m = px.bar(
                monthly, x="Month", y="Total", color="Brand",
                barmode="group",
                color_discrete_map=BRAND_COLORS,
                text=monthly["Total"].apply(fmt_usd),
            )
            fig_m.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", height=350,
                margin=dict(l=0, r=20, t=10, b=10),
                xaxis_title="", yaxis_title="Revenue ($)",
                legend_title="Brand",
            )
            fig_m.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_m, use_container_width=True)
        else:
            st.caption("Monthly trend unavailable — Transfer Date not parsed.")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Wholesale                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_wholesale:
    ws_df = display_df[
        ~display_df["Strain"].isin(_assigned_strains) &
        ~display_df["Vendor"].isin(EXCLUDE_VENDORS)
    ].copy()

    if ws_df.empty:
        st.info("No Wholesale records found in the loaded data.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        wf1, wf2, wf3, wf4 = st.columns([2, 2, 1, 1])
        _w_vendors = ["All"] + sorted(ws_df["Vendor"].dropna().unique().tolist())
        _w_types   = ["All"] + sorted(ws_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _w_dates   = ws_df["Transfer Date"].dropna()
        _w_min = _w_dates.min().date() if not _w_dates.empty else datetime.now().date()
        _w_max = _w_dates.max().date() if not _w_dates.empty else datetime.now().date()

        sel_w_vendor = wf1.selectbox("Vendor",   _w_vendors, key="ws_vendor")
        sel_w_type   = wf2.selectbox("Product",  _w_types,   key="ws_type")
        w_from = wf3.date_input("From", value=_w_min, min_value=_w_min, max_value=_w_max, key="ws_from")
        w_to   = wf4.date_input("To",   value=_w_max, min_value=_w_min, max_value=_w_max, key="ws_to")

        wview = ws_df.copy()
        if sel_w_vendor != "All":
            wview = wview[wview["Vendor"] == sel_w_vendor]
        if sel_w_type != "All":
            wview = wview[wview["Product"] == sel_w_type]
        wview = wview[wview["Transfer Date"].dt.date.between(w_from, w_to)]

        wview_g = wview[wview["Units UOM"] == "Grams"]

        # ── KPIs ──────────────────────────────────────────────────────────────
        w_rev   = wview["Total"].sum()
        w_grams = wview_g["Units"].sum()
        w_ppg   = (wview_g["Total"].sum() / w_grams) if w_grams > 0 else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Revenue", fmt_usd(w_rev))
        k2.metric("Total Volume",  fmt_g(w_grams))
        k3.metric("Avg $/gram",    f"${w_ppg:.2f}")
        k4.metric("Vendors",       wview["Vendor"].nunique())

        st.divider()

        # ── Strain summary table ───────────────────────────────────────────────
        st.subheader("Strain Summary")
        w_strain_tbl = (
            wview.groupby(["Vendor", "Product", "Strain", "Units UOM"])
            .agg(Units=("Units", "sum"), Revenue=("Total", "sum"))
            .reset_index()
        )
        w_strain_tbl["$/gram"] = w_strain_tbl.apply(
            lambda r: round(r["Revenue"] / r["Units"], 2)
            if r["Units UOM"] == "Grams" and r["Units"] > 0 else pd.NA,
            axis=1,
        )
        w_strain_tbl = w_strain_tbl.sort_values("Revenue", ascending=False)

        st.dataframe(
            w_strain_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "Units":   st.column_config.NumberColumn("Units",   format="%.0f"),
                "$/gram":  st.column_config.NumberColumn("$/gram",  format="$%.2f"),
            },
        )

        st.divider()

        # ── $/gram by strain ───────────────────────────────────────────────────
        st.subheader("$/gram by Strain")
        w_ppg_data = (
            wview_g.groupby(["Strain", "Product"])
            .apply(lambda g: g["Total"].sum() / g["Units"].sum() if g["Units"].sum() > 0 else 0)
            .reset_index(name="$/gram")
        )
        if not w_ppg_data.empty:
            _w_prods = sorted(w_ppg_data["Product"].unique().tolist())
            _n_wt = max(len(_w_prods), 1)
            _w_type_cmap = {
                t: _shade_hex("#4CE89C", 0.5 + (i / max(_n_wt - 1, 1)) * 0.9)
                for i, t in enumerate(_w_prods)
            }
            _w_prod_order = (
                w_ppg_data.groupby("Product")["$/gram"].median()
                .sort_values().index.tolist()
            )
            _w_type_cmap = {
                t: _shade_hex("#4CE89C", 0.45 + (i / max(len(_w_prod_order) - 1, 1)) * 0.85)
                for i, t in enumerate(_w_prod_order)
            }
            _w_strain_order = (
                w_ppg_data.groupby("Strain")["$/gram"].sum()
                .sort_values(ascending=True).index.tolist()
            )
            w_ppg_data["Strain"] = pd.Categorical(w_ppg_data["Strain"], categories=_w_strain_order, ordered=True)
            w_ppg_data["Product"] = pd.Categorical(w_ppg_data["Product"], categories=_w_prod_order, ordered=True)
            w_ppg_data = w_ppg_data.sort_values(["Strain", "Product"])
            w_ppg_data["_label"] = w_ppg_data.apply(
                lambda r: f"${r['$/gram']:.2f}  {r['Product']}", axis=1
            )
            fig_wppg = px.bar(
                w_ppg_data, x="$/gram", y="Strain", color="Product",
                orientation="h", barmode="stack",
                color_discrete_map=_w_type_cmap,
                text="_label",
            )
            fig_wppg.update_traces(
                textposition="inside", insidetextanchor="middle",
                hovertemplate="%{text}<br>$/gram: %{x:.2f}<extra></extra>",
                cliponaxis=False,
            )
            fig_wppg.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", legend_title="Product",
                height=max(300, w_ppg_data["Strain"].nunique() * 28),
                margin=dict(l=0, r=20, t=10, b=10),
                xaxis_title="$ per gram", yaxis_title="",
            )
            st.plotly_chart(fig_wppg, use_container_width=True)

        st.divider()

        # ── Volume by vendor ───────────────────────────────────────────────────
        st.subheader("Volume by Vendor")
        w_vol = (
            wview_g.groupby("Vendor")["Units"].sum()
            .reset_index(name="Grams")
            .sort_values("Grams", ascending=True)
        )
        if not w_vol.empty:
            fig_vol = px.bar(
                w_vol, x="Grams", y="Vendor", orientation="h",
                text=w_vol["Grams"].apply(fmt_g),
                color_discrete_sequence=["#4C9BE8"],
            )
            fig_vol.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8",
                height=max(300, len(w_vol) * 30),
                margin=dict(l=0, r=60, t=10, b=10),
                xaxis_title="Grams", yaxis_title="",
            )
            fig_vol.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_vol, use_container_width=True)
