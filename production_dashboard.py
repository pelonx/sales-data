import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from datetime import datetime
from io import StringIO
from urllib.parse import urlparse, parse_qs

st.set_page_config(page_title="Production Sales", layout="wide")

# ── Constants ─────────────────────────────────────────────────────────────────
BRAND_VENDORS   = {"Minglewood Brands", "SALISH SEA INDUSTRIES L.L.C."}
EXCLUDE_VENDORS = {"CONFIDENCE ANALYTICS"}

BRAND_LABEL = {
    "Minglewood Brands":            "Minglewood",
    "SALISH SEA INDUSTRIES L.L.C.": "Salish Sea",
}
BRAND_COLORS = {
    "Minglewood": "#E8844C",
    "Salish Sea": "#4C9BE8",
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

# ── Data loading ──────────────────────────────────────────────────────────────
def _csv_url(url: str, gid: str) -> str:
    url = url.strip()
    # Already a publish-to-web CSV URL
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

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Data Source")
    st.caption(
        "Paste the Google Sheet URL, then enter the GID for each tab. "
        "Find the GID by clicking the tab in Sheets — it appears in the URL as `#gid=XXXXXXX`."
    )
    sheet_url = st.text_input(
        "Google Sheet URL",
        placeholder="https://docs.google.com/spreadsheets/d/…",
        key="prod_url",
    )
    col_a, col_b = st.columns(2)
    gid_b13 = col_a.text_input("Block 13 GID", value="0",  key="prod_gid_b13")
    gid_b9  = col_b.text_input("B-9 GID",      value="",   key="prod_gid_b9")

    if st.button("Load / Refresh", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Load ──────────────────────────────────────────────────────────────────────
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

# ── Header ────────────────────────────────────────────────────────────────────
facilities_loaded = all_df["Facility"].unique().tolist()
st.markdown(
    f"<h1 style='color:#e3e3d8'>Production Sales · {' & '.join(sorted(facilities_loaded))}</h1>",
    unsafe_allow_html=True,
)
st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_brand, tab_wholesale = st.tabs(["🏷️ Brand Sales", "🏪 Wholesale"])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Brand Sales                                               ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_brand:
    brand_df = all_df[all_df["Vendor"].isin(BRAND_VENDORS)].copy()
    brand_df["Brand"] = brand_df["Vendor"].map(BRAND_LABEL)

    if brand_df.empty:
        st.info("No Brand Sales records found in the loaded data.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        bf1, bf2, bf3, bf4 = st.columns([2, 2, 1, 1])
        _b_facilities = ["All"] + sorted(brand_df["Facility"].unique().tolist())
        _b_brands     = ["All"] + sorted(brand_df["Brand"].dropna().unique().tolist())
        _b_dates      = brand_df["Transfer Date"].dropna()
        _b_min = _b_dates.min().date() if not _b_dates.empty else datetime.now().date()
        _b_max = _b_dates.max().date() if not _b_dates.empty else datetime.now().date()

        sel_b_facility = bf1.selectbox("Facility", _b_facilities, key="bs_facility")
        sel_b_brand    = bf2.selectbox("Brand",    _b_brands,     key="bs_brand")
        b_from = bf3.date_input("From", value=_b_min, min_value=_b_min, max_value=_b_max, key="bs_from")
        b_to   = bf4.date_input("To",   value=_b_max, min_value=_b_min, max_value=_b_max, key="bs_to")

        bview = brand_df.copy()
        if sel_b_facility != "All":
            bview = bview[bview["Facility"] == sel_b_facility]
        if sel_b_brand != "All":
            bview = bview[bview["Brand"] == sel_b_brand]
        bview = bview[bview["Transfer Date"].dt.date.between(b_from, b_to)]

        bview_g = bview[bview["Units UOM"] == "Grams"]

        # ── KPIs ──────────────────────────────────────────────────────────────
        b_rev   = bview["Total"].sum()
        b_grams = bview_g["Units"].sum()
        b_ppg   = (bview_g["Total"].sum() / b_grams) if b_grams > 0 else 0
        b_strains = bview["Strain"].nunique()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Revenue",  fmt_usd(b_rev))
        k2.metric("Total Volume",   fmt_g(b_grams))
        k3.metric("Avg $/gram",     f"${b_ppg:.2f}")
        k4.metric("Strains",        b_strains)

        st.divider()

        # ── Strain summary table ───────────────────────────────────────────────
        st.subheader("Strain by Brand")
        strain_tbl = (
            bview_g.groupby(["Brand", "Strain", "Product"])
            .agg(Grams=("Units", "sum"), Revenue=("Total", "sum"))
            .reset_index()
            .rename(columns={"Product": "Grade"})
        )
        strain_tbl["$/gram"] = (
            strain_tbl["Revenue"] / strain_tbl["Grams"].replace(0, pd.NA)
        ).round(2)
        strain_tbl = strain_tbl.sort_values(["Brand", "Revenue"], ascending=[True, False])

        st.dataframe(
            strain_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "Grams":   st.column_config.NumberColumn("Grams",   format="%.0f g"),
                "$/gram":  st.column_config.NumberColumn("$/gram",  format="$%.2f"),
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
        ppg_chart = (
            bview_g.groupby(["Strain", "Brand"])
            .apply(lambda g: g["Total"].sum() / g["Units"].sum() if g["Units"].sum() > 0 else 0)
            .reset_index(name="$/gram")
            .sort_values("$/gram", ascending=True)
        )
        if not ppg_chart.empty:
            fig_ppg = px.bar(
                ppg_chart,
                x="$/gram", y="Strain", color="Brand",
                orientation="h",
                color_discrete_map=BRAND_COLORS,
                text=ppg_chart["$/gram"].apply(lambda v: f"${v:.2f}"),
            )
            fig_ppg.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", showlegend=True, legend_title="Brand",
                height=max(350, len(ppg_chart) * 24),
                margin=dict(l=0, r=60, t=10, b=10),
                xaxis_title="$ per gram", yaxis_title="",
            )
            fig_ppg.update_traces(textposition="outside", cliponaxis=False)
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
    ws_df = all_df[~all_df["Vendor"].isin(BRAND_VENDORS)].copy()

    if ws_df.empty:
        st.info("No Wholesale records found in the loaded data.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        wf1, wf2, wf3, wf4 = st.columns([2, 2, 1, 1])
        _w_facilities = ["All"] + sorted(ws_df["Facility"].unique().tolist())
        _w_vendors    = ["All"] + sorted(ws_df["Vendor"].dropna().unique().tolist())
        _w_dates      = ws_df["Transfer Date"].dropna()
        _w_min = _w_dates.min().date() if not _w_dates.empty else datetime.now().date()
        _w_max = _w_dates.max().date() if not _w_dates.empty else datetime.now().date()

        sel_w_facility = wf1.selectbox("Facility", _w_facilities, key="ws_facility")
        sel_w_vendor   = wf2.selectbox("Vendor",   _w_vendors,    key="ws_vendor")
        w_from = wf3.date_input("From", value=_w_min, min_value=_w_min, max_value=_w_max, key="ws_from")
        w_to   = wf4.date_input("To",   value=_w_max, min_value=_w_min, max_value=_w_max, key="ws_to")

        wview = ws_df.copy()
        if sel_w_facility != "All":
            wview = wview[wview["Facility"] == sel_w_facility]
        if sel_w_vendor != "All":
            wview = wview[wview["Vendor"] == sel_w_vendor]
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
            wview_g.groupby(["Vendor", "Strain", "Product"])
            .agg(Grams=("Units", "sum"), Revenue=("Total", "sum"))
            .reset_index()
            .rename(columns={"Product": "Grade"})
        )
        w_strain_tbl["$/gram"] = (
            w_strain_tbl["Revenue"] / w_strain_tbl["Grams"].replace(0, pd.NA)
        ).round(2)
        w_strain_tbl = w_strain_tbl.sort_values("$/gram", ascending=False)

        st.dataframe(
            w_strain_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "Grams":   st.column_config.NumberColumn("Grams",   format="%.0f g"),
                "$/gram":  st.column_config.NumberColumn("$/gram",  format="$%.2f"),
            },
        )

        st.divider()

        # ── $/gram by strain ───────────────────────────────────────────────────
        st.subheader("$/gram by Strain")
        w_ppg_chart = (
            wview_g.groupby("Strain")
            .apply(lambda g: g["Total"].sum() / g["Units"].sum() if g["Units"].sum() > 0 else 0)
            .reset_index(name="$/gram")
            .sort_values("$/gram", ascending=True)
        )
        if not w_ppg_chart.empty:
            fig_wppg = px.bar(
                w_ppg_chart, x="$/gram", y="Strain", orientation="h",
                text=w_ppg_chart["$/gram"].apply(lambda v: f"${v:.2f}"),
                color_discrete_sequence=["#4CE89C"],
            )
            fig_wppg.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8",
                height=max(300, len(w_ppg_chart) * 24),
                margin=dict(l=0, r=60, t=10, b=10),
                xaxis_title="$ per gram", yaxis_title="",
            )
            fig_wppg.update_traces(textposition="outside", cliponaxis=False)
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
