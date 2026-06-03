import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import sqlite3
import os
import re
from datetime import datetime
from io import StringIO
from urllib.parse import urlparse

st.set_page_config(page_title="Production Sales", layout="wide")

# ── Password guard ────────────────────────────────────────────────────────────
def _configured_password() -> str:
    for key in ("production_password", "password"):
        try:
            value = st.secrets.get(key, "")
            if str(value).strip():
                return str(value)
        except Exception:
            pass
    return os.environ.get("PRODUCTION_PASSWORD", "")

_password = _configured_password()
if _password:
    if not st.session_state.get("production_authenticated"):
        st.title("Production Sales")
        pwd = st.text_input("Password", type="password")
        if st.button("Sign in", type="primary"):
            if pwd == _password:
                st.session_state["production_authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

    with st.sidebar:
        if st.button("Sign out", key="production_sidebar_signout"):
            st.session_state["production_authenticated"] = False
            st.rerun()

# ── Constants ─────────────────────────────────────────────────────────────────
BRAND_VENDORS   = {"Minglewood Brands", "SALISH SEA INDUSTRIES L.L.C."}
EXCLUDE_VENDORS = {"CONFIDENCE ANALYTICS"}

BRANDS_NAMED      = ["K. Savage", "Mayfield", "Leisure Land", "Clout King"]  # shown in Brand Sales tab
BRANDS_PROD       = BRANDS_NAMED
BRAND_VENDOR_KEYS = {v.casefold() for v in BRAND_VENDORS}
EXCLUDE_VENDOR_KEYS = {v.casefold() for v in EXCLUDE_VENDORS}

BRAND_COLORS = {
    "K. Savage":   "#4CE89C",
    "Mayfield":    "#E8844C",
    "Leisure Land":"#4C9BE8",
    "Clout King":  "#E84C9B",
    "Wholesale":   "#A0A0C8",
    "Unassigned":  "#888888",
}
FACILITY_COLORS = {
    "Block 13": "#4CE89C",
    "B-9":      "#9B6BE8",
}
PRODUCT_COLORS = {
    "A Grade": "#4CE89C",
    "B Grade": "#4C9BE8",
    "Trim":    "#E8844C",
}
DEFAULT_COSTS_GID = "154377878"
COST_SUMMARY_COLUMNS = [
    "Total Income",
    "Total Cost of Goods Sold",
    "Gross Profit",
    "Total Expenses",
    "Net Operating Income",
    "Total Other Income / Expenses",
    "Net Income",
]
COST_SECTION_COLUMNS = {
    "Income",
    "Cost of Goods Sold",
    "601 Direct Costs",
    "620 Direct Labor",
    "640 Inventory Compliance",
    "660 Overhead",
    "680 Supplies & Materials",
    "Expenses",
    "Other Income / Expenses",
}
COST_TREND_COLUMNS = [
    "Total Income",
    "Total Cost of Goods Sold",
    "Total Expenses",
    "Net Income",
]

# Canonical product names — aliases are collapsed to the canonical form
PRODUCT_ALIASES = {
    "Flower Lot - A's": "A Grade",
    "Flower -A Grade":  "A Grade",
    "Flower - A Grade": "A Grade",
    "A Grand":          "A Grade",
    "A Grade":          "A Grade",
    "Flower Lot - B's": "B Grade",
    "B Grade":          "B Grade",
    "Trim":             "Trim",
    "Trim Material":    "Trim",
}

def fmt_usd(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "$0"
    return f"${v:,.0f}"

def fmt_g(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "0 g"
    return f"{v:,.0f} g"

def pct_value(n, t):
    return n / t * 100 if t else 0.0

def normalize_vendor_name(vendor) -> str:
    text = re.sub(r"\s+", " ", str(vendor or "")).strip()
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", text).strip()

def clean_currency_value(value):
    text = str(value or "").strip()
    if text.lower() in {"", "nan", "none", "$ -", "-", "—"}:
        return 0.0
    negative_parentheses = bool(re.fullmatch(r"\(.*\)", text))
    cleaned = re.sub(r"[\$,]", "", text)
    cleaned = re.sub(r"^\((.*)\)$", r"\1", cleaned).strip()
    if cleaned in {"", "-", "—"}:
        return 0.0
    try:
        amount = float(cleaned)
    except ValueError:
        return 0.0
    return -abs(amount) if negative_parentheses else amount

def clean_currency_series(series: pd.Series) -> pd.Series:
    return series.apply(clean_currency_value)

def current_ytd_date_bounds(date_values):
    today = datetime.now().date()
    ytd_start = today.replace(month=1, day=1)
    dates = pd.to_datetime(date_values, errors="coerce").dropna()
    if dates.empty:
        return ytd_start, today, ytd_start, today
    data_min = dates.min().date()
    data_max = dates.max().date()
    return min(data_min, ytd_start), max(data_max, today), ytd_start, today

def product_type_multiselect(source_df: pd.DataFrame, key: str) -> list[str]:
    products = sorted(
        source_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
    )
    if not products:
        return []
    return st.multiselect(
        "Product Types",
        options=products,
        default=products,
        key=key,
    )

def strain_ppg_data(source_df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if source_df.empty:
        return pd.DataFrame(columns=[*group_cols, "$/gram"])
    summary = (
        source_df.groupby(group_cols, as_index=False)
        .agg(Revenue=("Total", "sum"), Units=("Units", "sum"))
    )
    summary = summary[summary["Units"] > 0].copy()
    summary["$/gram"] = summary["Revenue"] / summary["Units"]
    return summary[[*group_cols, "$/gram"]]

def parse_costs_tab(raw: pd.DataFrame) -> pd.DataFrame:
    costs = raw.copy()
    costs.columns = [str(c).strip() for c in costs.columns]
    if "Month" not in costs.columns or "Company" not in costs.columns:
        raise ValueError("Costs tab must include Month and Company columns.")

    costs = costs.dropna(how="all").copy()
    costs["Month"] = costs["Month"].astype(str).str.strip()
    costs["Company"] = costs["Company"].astype(str).str.strip()
    costs = costs[
        costs["Month"].ne("")
        & costs["Company"].ne("")
        & costs["Month"].str.lower().ne("nan")
        & costs["Company"].str.lower().ne("nan")
    ].copy()
    costs["Statement Month"] = pd.to_datetime(
        costs["Month"],
        format="%b-%y",
        errors="coerce",
    )

    for col in costs.columns:
        if col not in {"Month", "Company", "Statement Month"}:
            costs[col] = clean_currency_series(costs[col])
    return costs

def columns_between(columns: list[str], start: str, end: str) -> list[str]:
    try:
        start_i = columns.index(start)
        end_i = columns.index(end)
    except ValueError:
        return []
    if end_i <= start_i:
        return []
    return columns[start_i + 1:end_i]

def cost_detail_columns(costs_df: pd.DataFrame) -> tuple[list[str], list[str]]:
    columns = list(costs_df.columns)
    cogs_cols = columns_between(columns, "Cost of Goods Sold", "Total Cost of Goods Sold")
    expense_cols = columns_between(columns, "Expenses", "Total Expenses")

    def detail_only(cols):
        return [
            col for col in cols
            if col not in COST_SECTION_COLUMNS and not col.startswith("Total ")
        ]

    return detail_only(cogs_cols), detail_only(expense_cols)

def cost_detail_summary(costs_view: pd.DataFrame, columns: list[str], cost_type: str) -> pd.DataFrame:
    available = [col for col in columns if col in costs_view.columns]
    if costs_view.empty or not available:
        return pd.DataFrame(columns=["Cost Type", "Line Item", "Amount"])
    detail = (
        costs_view[available]
        .sum()
        .reset_index()
        .rename(columns={"index": "Line Item", 0: "Amount"})
    )
    detail["Cost Type"] = cost_type
    detail = detail[detail["Amount"].abs() > 0].copy()
    return detail[["Cost Type", "Line Item", "Amount"]]

def render_costs_tab(
    costs_df: pd.DataFrame,
    costs_error: str = "",
    selected_companies=None,
):
    if costs_error:
        st.error(costs_error)
        return
    if costs_df.empty:
        st.info("No costs data loaded.")
        return

    valid_months = costs_df["Statement Month"].dropna()
    if valid_months.empty:
        st.warning("Costs data loaded, but Month values could not be parsed.")
        return

    companies = sorted(costs_df["Company"].dropna().unique().tolist())
    if selected_companies is None:
        selected_companies = companies
    min_date, max_date, from_default, to_default = current_ytd_date_bounds(valid_months)
    c1, c2 = st.columns(2)
    from_date = c1.date_input(
        "From",
        value=from_default,
        min_value=min_date,
        max_value=max_date,
        key="cost_from",
    )
    to_date = c2.date_input(
        "To",
        value=to_default,
        min_value=min_date,
        max_value=max_date,
        key="cost_to",
    )
    start_date, end_date = sorted([from_date, to_date])

    costs_view = costs_df[
        costs_df["Company"].isin(selected_companies)
        & costs_df["Statement Month"].dt.date.between(start_date, end_date).fillna(False)
    ].copy()
    if costs_view.empty:
        st.caption("No costs data for the selected filters.")
        return

    income = costs_view.get("Total Income", pd.Series(dtype=float)).sum()
    cogs = costs_view.get("Total Cost of Goods Sold", pd.Series(dtype=float)).sum()
    gross_profit = costs_view.get("Gross Profit", pd.Series(dtype=float)).sum()
    expenses = costs_view.get("Total Expenses", pd.Series(dtype=float)).sum()
    net_income = costs_view.get("Net Income", pd.Series(dtype=float)).sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Income", fmt_usd(income))
    k2.metric("COGS", fmt_usd(cogs), f"{pct_value(cogs, income):.1f}% of income")
    k3.metric("Gross Profit", fmt_usd(gross_profit), f"{pct_value(gross_profit, income):.1f}% margin")
    k4.metric("Expenses", fmt_usd(expenses), f"{pct_value(expenses, income):.1f}% of income")
    k5.metric("Net Income", fmt_usd(net_income), f"{pct_value(net_income, income):.1f}% margin")

    st.divider()

    st.subheader("Monthly Performance")
    trend_cols = [col for col in COST_TREND_COLUMNS if col in costs_view.columns]
    monthly = (
        costs_view.groupby("Statement Month", as_index=False)[trend_cols]
        .sum()
        .sort_values("Statement Month")
    )
    trend = monthly.melt(
        id_vars="Statement Month",
        value_vars=trend_cols,
        var_name="Metric",
        value_name="Amount",
    )
    if not trend.empty:
        fig = px.line(
            trend,
            x="Statement Month",
            y="Amount",
            color="Metric",
            markers=True,
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e3e3d8", height=360,
            margin=dict(l=0, r=20, t=10, b=10),
            xaxis_title="", yaxis_title="Amount",
            legend_title="Metric",
        )
        fig.update_yaxes(tickprefix="$")
        fig.update_xaxes(tickformat="%b %Y")
        fig.update_traces(
            hovertemplate="%{x|%b %Y}<br>%{fullData.name}: $%{y:,.0f}<extra></extra>"
        )
        st.plotly_chart(fig, width="stretch")

    st.divider()

    cogs_cols, expense_cols = cost_detail_columns(costs_df)
    detail = pd.concat(
        [
            cost_detail_summary(costs_view, cogs_cols, "COGS"),
            cost_detail_summary(costs_view, expense_cols, "Expenses"),
        ],
        ignore_index=True,
    )
    detail = detail.sort_values("Amount", ascending=False)
    st.subheader("Top Cost Lines")
    if not detail.empty:
        top_detail = detail.head(15).sort_values("Amount", ascending=True)
        fig_costs = px.bar(
            top_detail,
            x="Amount",
            y="Line Item",
            color="Cost Type",
            orientation="h",
            text=top_detail["Amount"].apply(fmt_usd),
            color_discrete_map={"COGS": "#4C9BE8", "Expenses": "#E8844C"},
        )
        fig_costs.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e3e3d8",
            height=max(360, len(top_detail) * 28),
            margin=dict(l=0, r=70, t=10, b=10),
            xaxis_title="Amount", yaxis_title="",
            legend_title="Cost Type",
        )
        fig_costs.update_xaxes(tickprefix="$")
        fig_costs.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_costs, width="stretch")
    else:
        st.caption("No nonzero cost lines for the selected filters.")

    st.divider()

    st.subheader("Monthly Statement")
    summary_cols = ["Month", "Company"] + [
        col for col in COST_SUMMARY_COLUMNS if col in costs_view.columns
    ]
    st.dataframe(
        costs_view.sort_values(["Statement Month", "Company"])[summary_cols],
        width="stretch",
        hide_index=True,
        column_config={
            col: st.column_config.NumberColumn(col, format="$%.0f")
            for col in summary_cols
            if col not in {"Month", "Company"}
        },
    )

    with st.expander("Detailed income statement"):
        detail_cols = [
            col for col in costs_view.columns
            if col != "Statement Month"
        ]
        st.dataframe(
            costs_view.sort_values(["Statement Month", "Company"])[detail_cols],
            width="stretch",
            hide_index=True,
            column_config={
                col: st.column_config.NumberColumn(col, format="$%.0f")
                for col in detail_cols
                if col not in {"Month", "Company"}
            },
        )

def render_material_ppg_metrics(view_g: pd.DataFrame):
    if view_g.empty or not {"Product", "Total", "Units"}.issubset(view_g.columns):
        return

    ppg = view_g.copy()
    ppg["Product"] = ppg["Product"].replace("nan", pd.NA)
    ppg = (
        ppg.dropna(subset=["Product"])
        .groupby("Product", as_index=False)
        .agg(Revenue=("Total", "sum"), Grams=("Units", "sum"))
    )
    ppg = ppg[ppg["Grams"] > 0].copy()
    if ppg.empty:
        return

    ppg["$/gram"] = ppg["Revenue"] / ppg["Grams"]
    ppg = ppg.sort_values("$/gram", ascending=False)

    for start in range(0, len(ppg), 4):
        row = ppg.iloc[start:start + 4]
        cols = st.columns(len(row))
        for col, (_, item) in zip(cols, row.iterrows()):
            col.metric(
                str(item["Product"]),
                f"${item['$/gram']:.2f}/g",
                fmt_g(item["Grams"]),
            )

def render_ppg_over_time_chart(source_df: pd.DataFrame, key_prefix: str):
    trend_df = source_df[source_df["Units UOM"] == "Grams"].copy()
    if trend_df.empty:
        st.caption("No gram-denominated sales available for PPG trend.")
        return

    trend_df["Product"] = trend_df["Product"].replace("nan", pd.NA)
    transfer_dates = pd.to_datetime(
        trend_df["Transfer Date"],
        errors="coerce",
    )
    trend_df["Transfer Day"] = transfer_dates.dt.date
    trend_df["Transfer Month"] = transfer_dates.dt.to_period("M").dt.to_timestamp()
    trend_df = trend_df.dropna(subset=["Product", "Transfer Day", "Transfer Month"])
    if trend_df.empty:
        st.caption("PPG trend unavailable — Transfer Date not parsed.")
        return

    facilities = ["All"] + sorted(trend_df["Facility"].dropna().unique().tolist())
    dates = trend_df["Transfer Day"].dropna()
    min_date, max_date, from_default, to_default = current_ytd_date_bounds(dates)

    c1, c2, c3 = st.columns([2, 1, 1])
    selected_facility = c1.selectbox(
        "Facility",
        facilities,
        key=f"{key_prefix}_ppg_trend_facility",
    )
    from_date = c2.date_input(
        "From",
        value=from_default,
        min_value=min_date,
        max_value=max_date,
        key=f"{key_prefix}_ppg_trend_from",
    )
    to_date = c3.date_input(
        "To",
        value=to_default,
        min_value=min_date,
        max_value=max_date,
        key=f"{key_prefix}_ppg_trend_to",
    )
    start_date, end_date = sorted([from_date, to_date])

    products = sorted(trend_df["Product"].dropna().unique().tolist())
    selected_products = []
    toggle_cols = st.columns(min(4, len(products)))
    for i, product in enumerate(products):
        if toggle_cols[i % len(toggle_cols)].checkbox(
            product,
            value=True,
            key=f"{key_prefix}_ppg_trend_product_{i}",
        ):
            selected_products.append(product)

    filtered = trend_df[
        trend_df["Transfer Day"].between(start_date, end_date)
        & trend_df["Product"].isin(selected_products)
    ].copy()
    if selected_facility != "All":
        filtered = filtered[filtered["Facility"] == selected_facility]

    if filtered.empty:
        st.caption("No PPG trend data for the selected filters.")
        return

    trend = (
        filtered.groupby(["Transfer Month", "Product"], as_index=False)
        .agg(Revenue=("Total", "sum"), Grams=("Units", "sum"))
    )
    trend = trend[trend["Grams"] > 0].copy()
    trend["$/gram"] = trend["Revenue"] / trend["Grams"]
    trend["Date"] = pd.to_datetime(trend["Transfer Month"])
    trend = trend.sort_values(["Date", "Product"])

    fig = px.line(
        trend,
        x="Date",
        y="$/gram",
        color="Product",
        markers=True,
        color_discrete_map=PRODUCT_COLORS,
        hover_data={"Revenue": ":$,.0f", "Grams": ":,.0f", "Date": False},
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e3e3d8", height=360,
        margin=dict(l=0, r=20, t=10, b=10),
        xaxis_title="", yaxis_title="Avg $/gram",
        legend_title="Material",
    )
    fig.update_yaxes(tickprefix="$")
    fig.update_xaxes(tickformat="%b %Y")
    fig.update_traces(
        hovertemplate="%{x|%b %Y}<br>%{fullData.name}: $%{y:.2f}/g"
        "<br>Revenue: $%{customdata[0]:,.0f}"
        "<br>Grams: %{customdata[1]:,.0f}<extra></extra>"
    )
    st.plotly_chart(fig, width="stretch")

def is_brand_vendor(vendor) -> bool:
    return normalize_vendor_name(vendor).casefold() in BRAND_VENDOR_KEYS

def is_excluded_vendor(vendor) -> bool:
    return normalize_vendor_name(vendor).casefold() in EXCLUDE_VENDOR_KEYS

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

def ppg_band_chart(ppg_data: pd.DataFrame, product_col="Product", brand_col=None):
    data = ppg_data.copy()
    data = data[pd.to_numeric(data["$/gram"], errors="coerce").fillna(0) > 0].copy()
    if data.empty:
        return None

    prod_order = (
        data.groupby(product_col)["$/gram"].median()
        .sort_values()
        .index.tolist()
    )
    product_colors = {
        product: _shade_hex("#4CE89C", 0.45 + (i / max(len(prod_order) - 1, 1)) * 0.85)
        for i, product in enumerate(prod_order)
    }

    strain_order = (
        data.groupby("Strain")["$/gram"].max()
        .sort_values(ascending=True)
        .index.tolist()
    )

    band_rows = []
    for strain, group in data.groupby("Strain", sort=False):
        group = group.sort_values(["$/gram", product_col], ascending=[True, True])
        last_price = 0.0
        for _, row in group.iterrows():
            price = float(row["$/gram"])
            width = max(price - last_price, 0)
            if width <= 0:
                continue
            band_rows.append({
                "Strain": strain,
                product_col: row[product_col],
                "Brand": row.get(brand_col, "") if brand_col else "",
                "$/gram": price,
                "Band Start": last_price,
                "Band Width": width,
                "Label": f"${price:.2f}",
            })
            last_price = max(last_price, price)

    band_data = pd.DataFrame(band_rows)
    if band_data.empty:
        return None

    fig = go.Figure()
    for product in prod_order:
        sub = band_data[band_data[product_col] == product]
        if sub.empty:
            continue
        custom_cols = [product_col, "$/gram", "Brand", "Band Start", "Band Width"]
        fig.add_trace(go.Bar(
            x=sub["Band Width"],
            y=sub["Strain"],
            base=sub["Band Start"],
            orientation="h",
            name=product,
            marker_color=product_colors.get(product, "#4CE89C"),
            text=sub["Label"],
            customdata=sub[custom_cols],
            hovertemplate=(
                "%{customdata[0]}<br>"
                "%{y}<br>"
                "%{customdata[2]}<br>"
                "Average $/gram: $%{customdata[1]:.2f}"
                "<extra></extra>"
            ),
        ))

    fig.update_traces(textposition="inside", insidetextanchor="middle", cliponaxis=False)
    fig.update_layout(
        barmode="overlay",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e3e3d8", showlegend=True, legend_title="Product",
        height=max(350, len(strain_order) * 28),
        margin=dict(l=0, r=20, t=10, b=10),
        xaxis_title="$ per gram", yaxis_title="",
        xaxis=dict(tickprefix="$"),
        yaxis=dict(categoryorder="array", categoryarray=strain_order),
    )
    return fig

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

PROD_CONFIG_KEYS = {
    "sheet_url": ("production_sheet_url", "prod_sheet_url", "PRODUCTION_SHEET_URL"),
    "gid_b13": ("production_gid_b13", "prod_gid_b13", "PRODUCTION_GID_B13"),
    "gid_b9": ("production_gid_b9", "prod_gid_b9", "PRODUCTION_GID_B9"),
    "gid_assign": ("production_gid_assign", "prod_gid_assign", "PRODUCTION_GID_ASSIGN"),
    "gid_costs": ("production_gid_costs", "prod_gid_costs", "PRODUCTION_GID_COSTS"),
}

def _config_secret_or_env(key: str, default: str = "") -> str:
    for candidate in PROD_CONFIG_KEYS.get(key, ()):
        try:
            value = st.secrets.get(candidate, "")
            if str(value).strip():
                return str(value).strip()
        except Exception:
            pass
        value = os.environ.get(candidate)
        if value and value.strip():
            return value.strip()
    return default

def saved_or_configured_setting(settings: dict, key: str, default: str = "") -> str:
    saved = str(settings.get(key, "") or "").strip()
    if saved:
        return saved
    return _config_secret_or_env(key, default)

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
_default_sheet_url = saved_or_configured_setting(_saved, "sheet_url")
_default_gid_b13 = saved_or_configured_setting(_saved, "gid_b13", "0")
_default_gid_b9 = saved_or_configured_setting(_saved, "gid_b9")
_default_gid_assign = saved_or_configured_setting(_saved, "gid_assign")
_default_gid_costs = saved_or_configured_setting(_saved, "gid_costs", DEFAULT_COSTS_GID)

with st.sidebar:
    st.header("Data Source")
    st.caption(
        "Paste the Google Sheet URL, then enter the GID for each tab. "
        "Find the GID by clicking the tab in Sheets — it appears in the URL as `#gid=XXXXXXX`."
    )
    sheet_url = st.text_input(
        "Google Sheet URL",
        value=_default_sheet_url,
        placeholder="https://docs.google.com/spreadsheets/d/…",
        key="prod_url",
    )
    col_a, col_b = st.columns(2)
    gid_b13      = col_a.text_input("Block 13 GID",    value=_default_gid_b13, key="prod_gid_b13")
    gid_b9       = col_b.text_input("B-9 GID",         value=_default_gid_b9,  key="prod_gid_b9")
    gid_assign   = st.text_input(   "Assignments GID",  value=_default_gid_assign,
                                    placeholder="GID of a tab with Strain and Brand columns",
                                    key="prod_gid_assign")
    gid_costs = st.text_input(
        "Costs GID",
        value=_default_gid_costs,
        key="prod_gid_costs",
    )

    if st.button("Load / Refresh", type="primary", width="stretch"):
        if sheet_url.strip():
            save_setting("sheet_url",  sheet_url.strip())
            save_setting("gid_b13",    gid_b13.strip())
            save_setting("gid_b9",     gid_b9.strip())
            save_setting("gid_assign", gid_assign.strip())
            save_setting("gid_costs",  gid_costs.strip())
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
all_df = all_df[~all_df["Vendor"].apply(is_excluded_vendor)].copy()
all_df["Product"] = all_df["Product"].map(PRODUCT_ALIASES).fillna(all_df["Product"])
_row_ppg = all_df["Total"].div(all_df["Units"]).where(all_df["Units"] > 0)
_low_price_a_grade = (
    (all_df["Product"] == "A Grade")
    & (all_df["Units UOM"] == "Grams")
    & (_row_ppg < 1)
)
all_df = all_df[~_low_price_a_grade].copy()

costs_df = pd.DataFrame()
costs_error = ""
if gid_costs.strip():
    try:
        costs_raw = load_tab(sheet_url, gid_costs.strip())
        costs_df = parse_costs_tab(costs_raw)
    except Exception as e:
        costs_error = f"**Costs**: {e}"

# ── Sidebar — Brand Assignments ───────────────────────────────────────────────
# Brand assignments label brand-vendor rows by named brand. Non-brand vendors
# are classified as Wholesale regardless of strain assignment.
_brand_strains = sorted(
    all_df[all_df["Vendor"].apply(is_brand_vendor)]["Strain"]
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
            width="stretch",
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

        if st.button("Save Assignments", width="stretch", disabled=bool(_conflicts)):
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

_cost_companies = sorted(costs_df["Company"].dropna().unique().tolist()) if not costs_df.empty else []
selected_cost_companies = _cost_companies
if _cost_companies:
    st.caption("Company")
    selected_cost_companies = []
    _company_cols = st.columns(len(_cost_companies))
    for col, company in zip(_company_cols, _cost_companies):
        key_part = re.sub(r"[^a-z0-9]+", "_", company.casefold()).strip("_")
        if col.checkbox(company, value=True, key=f"global_company_{key_part}"):
            selected_cost_companies.append(company)
st.divider()

# Apply facility filter for display
display_df = all_df if sel_facility == "Both" else all_df[all_df["Facility"] == sel_facility]

# Brand Sales = rows sold to brand vendors. Wholesale = all other vendors.
brand_df = display_df[display_df["Vendor"].apply(is_brand_vendor)].copy()
brand_df["Brand"] = brand_df["Strain"].map(strain_map).where(
    lambda s: s.isin(BRANDS_NAMED),
    "Unassigned",
)
named_df = brand_df
ws_df = display_df[~display_df["Vendor"].apply(is_brand_vendor)].copy()
ws_df["Brand"] = "Wholesale"
combined_df = pd.concat(
    [
        named_df.assign(**{"Sale Type": "Brand Sales"}),
        ws_df.assign(**{"Sale Type": "Wholesale"}),
    ],
    ignore_index=True,
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_brand, tab_wholesale, tab_both, tab_costs = st.tabs([
    "🏷️ Brand Sales",
    "🏪 Wholesale",
    "📊 Both",
    "💸 Costs",
])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Brand Sales                                               ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_brand:
    if named_df.empty:
        st.info("No Brand Sales records found for Minglewood Brands or Salish Sea Industries.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        bf1, bf2, bf3, bf4 = st.columns([2, 2, 1, 1])
        _b_brands = ["All"] + sorted(named_df["Brand"].dropna().unique().tolist())
        _b_types  = ["All"] + sorted(named_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _b_min, _b_max, _b_from_default, _b_to_default = current_ytd_date_bounds(named_df["Transfer Date"])

        sel_b_brand = bf1.selectbox("Brand",   _b_brands, key="bs_brand")
        sel_b_type  = bf2.selectbox("Product", _b_types,  key="bs_type")
        b_from = bf3.date_input("From", value=_b_from_default, min_value=_b_min, max_value=_b_max, key="bs_from")
        b_to   = bf4.date_input("To",   value=_b_to_default,   min_value=_b_min, max_value=_b_max, key="bs_to")

        bview = named_df.copy()
        if sel_b_brand != "All":
            bview = bview[bview["Brand"] == sel_b_brand]
        if sel_b_type != "All":
            bview = bview[bview["Product"] == sel_b_type]
        _b_in_range = bview["Transfer Date"].dt.date.between(b_from, b_to).fillna(True)
        bview = bview[_b_in_range]

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
        render_material_ppg_metrics(bview_g)

        st.divider()

        # ── Strain summary table ───────────────────────────────────────────────
        st.subheader("Strain by Brand")
        _sf1, _sf2, _sf3 = st.columns([2, 2, 2])
        _tbl_brands   = ["All"] + sorted(bview["Brand"].dropna().unique().tolist())
        _tbl_products = ["All"] + sorted(bview["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _tbl_strains  = ["All"] + sorted(bview["Strain"].dropna().unique().tolist())
        _tbl_brand   = _sf1.selectbox("Brand",   _tbl_brands,   key="tbl_brand")
        _tbl_product = _sf2.selectbox("Product", _tbl_products, key="tbl_product")
        _tbl_strain  = _sf3.selectbox("Strain",  _tbl_strains,  key="tbl_strain")

        _tview = bview.copy()
        if _tbl_brand   != "All": _tview = _tview[_tview["Brand"]   == _tbl_brand]
        if _tbl_product != "All": _tview = _tview[_tview["Product"] == _tbl_product]
        if _tbl_strain  != "All": _tview = _tview[_tview["Strain"]  == _tbl_strain]

        strain_tbl = (
            _tview.groupby(["Brand", "Product", "Strain", "Units UOM"])
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
            width="stretch",
            hide_index=True,
            column_config={
                "Revenue":  st.column_config.NumberColumn("Revenue",  format="$%.0f"),
                "Units":    st.column_config.NumberColumn("Units",    format="%.0f"),
                "$/gram":   st.column_config.NumberColumn("$/gram",   format="$%.2f"),
            },
        )

        # ── Diagnostic: raw data search ────────────────────────────────────────
        with st.expander("🔍 Raw data lookup (troubleshoot missing rows)"):
            _diag_q = st.text_input("Search strain name", key="diag_strain_search")
            if _diag_q:
                _dcols = ["Facility", "Vendor", "Strain", "Product", "Units UOM", "Units", "Total", "Transfer Date", "Month"]
                _m = lambda df: df["Strain"].str.contains(_diag_q, case=False, na=False)
                _d1 = display_df[_m(display_df)]
                _d2 = brand_df[_m(brand_df)]
                _d3 = bview[_m(bview)]
                _d4 = _tview[_m(_tview)]
                st.markdown(
                    f"| Stage | Rows |\n|---|---|\n"
                    f"| Raw (display_df) | {len(_d1)} |\n"
                    f"| Brand-vendor rows (brand_df) | {len(_d2)} |\n"
                    f"| After tab filters (bview) | {len(_d3)} |\n"
                    f"| After table filters (_tview) | {len(_d4)} |"
                )
                st.caption(f"Active tab filters — Brand: `{sel_b_brand}` · Product: `{sel_b_type}` · Table brand: `{_tbl_brand}` · Table product: `{_tbl_product}` · Table strain: `{_tbl_strain}`")
                if not _d2.empty:
                    st.dataframe(_d2[_dcols + ["Brand"]], width="stretch", hide_index=True)
                else:
                    _close = [k for k in strain_map if _diag_q.lower() in k.lower()]
                    st.warning(f"strain_map keys matching '{_diag_q}': {_close or 'none — not assigned'}")

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
            st.plotly_chart(fig_s, width="stretch")

        st.divider()

        # ── $/gram by strain ───────────────────────────────────────────────────
        st.subheader("$/gram by Strain")
        selected_b_ppg_products = product_type_multiselect(bview_g, "brand_ppg_products")
        b_ppg_view_g = bview_g[bview_g["Product"].isin(selected_b_ppg_products)].copy()
        ppg_data = strain_ppg_data(b_ppg_view_g, ["Strain", "Brand", "Product"])
        if not ppg_data.empty:
            fig_ppg = ppg_band_chart(ppg_data, product_col="Product", brand_col="Brand")
            if fig_ppg is not None:
                st.plotly_chart(fig_ppg, width="stretch")
        else:
            st.caption("No gram-denominated sales for the selected product types.")

        st.divider()

        # ── $/gram over time ──────────────────────────────────────────────────
        st.subheader("PPG Over Time")
        btrend = named_df.copy()
        if sel_b_brand != "All":
            btrend = btrend[btrend["Brand"] == sel_b_brand]
        render_ppg_over_time_chart(btrend, "brand")

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
            st.plotly_chart(fig_m, width="stretch")
        else:
            st.caption("Monthly trend unavailable — Transfer Date not parsed.")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Wholesale                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_wholesale:
    if ws_df.empty:
        st.info("No Wholesale records found for non-brand vendors.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        wf1, wf2, wf3, wf4 = st.columns([2, 2, 1, 1])
        _w_vendors = ["All"] + sorted(ws_df["Vendor"].dropna().unique().tolist())
        _w_types   = ["All"] + sorted(ws_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _w_min, _w_max, _w_from_default, _w_to_default = current_ytd_date_bounds(ws_df["Transfer Date"])

        sel_w_vendor = wf1.selectbox("Vendor",   _w_vendors, key="ws_vendor")
        sel_w_type   = wf2.selectbox("Product",  _w_types,   key="ws_type")
        w_from = wf3.date_input("From", value=_w_from_default, min_value=_w_min, max_value=_w_max, key="ws_from")
        w_to   = wf4.date_input("To",   value=_w_to_default,   min_value=_w_min, max_value=_w_max, key="ws_to")

        wview = ws_df.copy()
        if sel_w_vendor != "All":
            wview = wview[wview["Vendor"] == sel_w_vendor]
        if sel_w_type != "All":
            wview = wview[wview["Product"] == sel_w_type]
        _w_in_range = wview["Transfer Date"].dt.date.between(w_from, w_to).fillna(True)
        wview = wview[_w_in_range]

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
        render_material_ppg_metrics(wview_g)

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
            width="stretch",
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
        selected_w_ppg_products = product_type_multiselect(wview_g, "wholesale_ppg_products")
        w_ppg_view_g = wview_g[wview_g["Product"].isin(selected_w_ppg_products)].copy()
        w_ppg_data = strain_ppg_data(w_ppg_view_g, ["Strain", "Product"])
        if not w_ppg_data.empty:
            fig_wppg = ppg_band_chart(w_ppg_data, product_col="Product")
            if fig_wppg is not None:
                st.plotly_chart(fig_wppg, width="stretch")
        else:
            st.caption("No gram-denominated sales for the selected product types.")

        st.divider()

        # ── $/gram over time ──────────────────────────────────────────────────
        st.subheader("PPG Over Time")
        wtrend = ws_df.copy()
        if sel_w_vendor != "All":
            wtrend = wtrend[wtrend["Vendor"] == sel_w_vendor]
        render_ppg_over_time_chart(wtrend, "wholesale")

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
            st.plotly_chart(fig_vol, width="stretch")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Both                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_both:
    if combined_df.empty:
        st.info("No Brand Sales or Wholesale records found.")
    else:
        # ── Filters ───────────────────────────────────────────────────────────
        cf1, cf2, cf3, cf4 = st.columns([2, 2, 1, 1])
        _c_sale_types = ["All"] + sorted(combined_df["Sale Type"].dropna().unique().tolist())
        _c_types = ["All"] + sorted(combined_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _c_min, _c_max, _c_from_default, _c_to_default = current_ytd_date_bounds(combined_df["Transfer Date"])

        sel_c_sale_type = cf1.selectbox("Sale Type", _c_sale_types, key="both_sale_type")
        sel_c_type = cf2.selectbox("Product", _c_types, key="both_type")
        c_from = cf3.date_input("From", value=_c_from_default, min_value=_c_min, max_value=_c_max, key="both_from")
        c_to = cf4.date_input("To", value=_c_to_default, min_value=_c_min, max_value=_c_max, key="both_to")

        cview = combined_df.copy()
        if sel_c_sale_type != "All":
            cview = cview[cview["Sale Type"] == sel_c_sale_type]
        if sel_c_type != "All":
            cview = cview[cview["Product"] == sel_c_type]
        c_start, c_end = sorted([c_from, c_to])
        _c_in_range = cview["Transfer Date"].dt.date.between(c_start, c_end).fillna(True)
        cview = cview[_c_in_range]

        cview_g = cview[cview["Units UOM"] == "Grams"]

        # ── KPIs ──────────────────────────────────────────────────────────────
        c_rev = cview["Total"].sum()
        c_grams = cview_g["Units"].sum()
        c_ppg = (cview_g["Total"].sum() / c_grams) if c_grams > 0 else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Revenue", fmt_usd(c_rev))
        k2.metric("Total Volume", fmt_g(c_grams))
        k3.metric("Avg $/gram", f"${c_ppg:.2f}")
        k4.metric("Vendors", cview["Vendor"].nunique())
        render_material_ppg_metrics(cview_g)

        st.divider()

        # ── Combined strain summary table ─────────────────────────────────────
        st.subheader("Combined Strain Summary")
        _cf1, _cf2, _cf3, _cf4 = st.columns([2, 2, 2, 2])
        _ctbl_sale_types = ["All"] + sorted(cview["Sale Type"].dropna().unique().tolist())
        _ctbl_brands = ["All"] + sorted(cview["Brand"].dropna().unique().tolist())
        _ctbl_products = ["All"] + sorted(cview["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
        _ctbl_strains = ["All"] + sorted(cview["Strain"].dropna().unique().tolist())

        _ctbl_sale_type = _cf1.selectbox("Sale Type", _ctbl_sale_types, key="both_tbl_sale_type")
        _ctbl_brand = _cf2.selectbox("Brand", _ctbl_brands, key="both_tbl_brand")
        _ctbl_product = _cf3.selectbox("Product", _ctbl_products, key="both_tbl_product")
        _ctbl_strain = _cf4.selectbox("Strain", _ctbl_strains, key="both_tbl_strain")

        _ctview = cview.copy()
        if _ctbl_sale_type != "All": _ctview = _ctview[_ctview["Sale Type"] == _ctbl_sale_type]
        if _ctbl_brand != "All": _ctview = _ctview[_ctview["Brand"] == _ctbl_brand]
        if _ctbl_product != "All": _ctview = _ctview[_ctview["Product"] == _ctbl_product]
        if _ctbl_strain != "All": _ctview = _ctview[_ctview["Strain"] == _ctbl_strain]

        c_strain_tbl = (
            _ctview.groupby(["Sale Type", "Brand", "Vendor", "Product", "Strain", "Units UOM"])
            .agg(Units=("Units", "sum"), Revenue=("Total", "sum"))
            .reset_index()
        )
        c_strain_tbl["$/gram"] = c_strain_tbl.apply(
            lambda r: round(r["Revenue"] / r["Units"], 2)
            if r["Units UOM"] == "Grams" and r["Units"] > 0 else pd.NA,
            axis=1,
        )
        c_strain_tbl = c_strain_tbl.sort_values("Revenue", ascending=False)

        st.dataframe(
            c_strain_tbl,
            width="stretch",
            hide_index=True,
            column_config={
                "Revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "Units": st.column_config.NumberColumn("Units", format="%.0f"),
                "$/gram": st.column_config.NumberColumn("$/gram", format="$%.2f"),
            },
        )

        st.divider()

        # ── Revenue by strain chart ────────────────────────────────────────────
        st.subheader("Revenue by Strain")
        c_strain_chart = (
            cview_g.groupby(["Strain", "Brand"])["Total"]
            .sum().reset_index()
            .sort_values("Total", ascending=True)
        )
        if not c_strain_chart.empty:
            fig_c_s = px.bar(
                c_strain_chart,
                x="Total", y="Strain", color="Brand",
                orientation="h",
                color_discrete_map=BRAND_COLORS,
                text=c_strain_chart["Total"].apply(fmt_usd),
            )
            fig_c_s.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", showlegend=True, legend_title="Brand",
                height=max(350, len(c_strain_chart) * 24),
                margin=dict(l=0, r=60, t=10, b=10),
                xaxis_title="", yaxis_title="",
            )
            fig_c_s.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_c_s, width="stretch")

        st.divider()

        # ── $/gram by strain ───────────────────────────────────────────────────
        st.subheader("$/gram by Strain")
        selected_c_ppg_products = product_type_multiselect(cview_g, "both_ppg_products")
        c_ppg_view_g = cview_g[cview_g["Product"].isin(selected_c_ppg_products)].copy()
        c_ppg_data = strain_ppg_data(c_ppg_view_g, ["Strain", "Brand", "Product"])
        if not c_ppg_data.empty:
            fig_c_ppg = ppg_band_chart(c_ppg_data, product_col="Product", brand_col="Brand")
            if fig_c_ppg is not None:
                st.plotly_chart(fig_c_ppg, width="stretch")
        else:
            st.caption("No gram-denominated sales for the selected product types.")

        st.divider()

        # ── $/gram over time ──────────────────────────────────────────────────
        st.subheader("PPG Over Time")
        ctrend = combined_df.copy()
        if sel_c_sale_type != "All":
            ctrend = ctrend[ctrend["Sale Type"] == sel_c_sale_type]
        render_ppg_over_time_chart(ctrend, "both")

        st.divider()

        # ── Monthly revenue trend ──────────────────────────────────────────────
        st.subheader("Monthly Revenue")
        c_monthly = (
            cview_g[cview_g["Month"].str.match(r"\d{4}-\d{2}")]
            .groupby(["Month", "Brand"])["Total"]
            .sum().reset_index()
            .sort_values("Month")
        )
        if not c_monthly.empty:
            fig_c_m = px.bar(
                c_monthly, x="Month", y="Total", color="Brand",
                barmode="group",
                color_discrete_map=BRAND_COLORS,
                text=c_monthly["Total"].apply(fmt_usd),
            )
            fig_c_m.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e3e3d8", height=350,
                margin=dict(l=0, r=20, t=10, b=10),
                xaxis_title="", yaxis_title="Revenue ($)",
                legend_title="Brand",
            )
            fig_c_m.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_c_m, width="stretch")
        else:
            st.caption("Monthly trend unavailable — Transfer Date not parsed.")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Costs                                                     ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_costs:
    render_costs_tab(costs_df, costs_error, selected_cost_companies)
