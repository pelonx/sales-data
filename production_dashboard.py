import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import sqlite3
import os
import re
import json
from datetime import datetime
from io import BytesIO, StringIO
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

st.set_page_config(page_title="Production Sales", layout="wide")

PDF_EXPORT_VERSION = "portrait-v3"
PDF_MARGIN = 0.35 * inch
PDF_PAGE_SIZE = (8.5 * inch, 11 * inch)
PDF_USABLE_WIDTH = PDF_PAGE_SIZE[0] - (2 * PDF_MARGIN)
PDF_USABLE_HEIGHT = PDF_PAGE_SIZE[1] - (2 * PDF_MARGIN)

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
FACILITY_SORT_ORDER = {
    "B-9": 0,
    "Block 13": 1,
    "Unassigned": 99,
}
SALE_TYPE_COLORS = {
    "Brand Sales": "#4CE89C",
    "Wholesale": "#4C9BE8",
}
PRODUCT_COLORS = {
    "A Grade": "#4CE89C",
    "B Grade": "#4C9BE8",
    "Trim":    "#E8844C",
}
DEFAULT_COSTS_GID = "154377878"
DEFAULT_INVENTORY_GID = "1120425056"
GROWFLOW_TOKEN_URL = "https://token.growflow.com/oauth/token"
GROWFLOW_GRAPHQL_URL = "https://partnerapi.growflow.com/"
GROWFLOW_AUDIENCE = "https://growflow.com"
GROWFLOW_MAX_PAGE_SIZE = 100
GROWFLOW_INVENTORY_QUERY = """
query Inventories($regionCode: String!, $licenseNumber: String, $skip: Int, $take: Int) {
  inventories(regionCode: $regionCode, licenseNumber: $licenseNumber, skip: $skip, take: $take) {
    totalCount
    items {
      birthDate
      complianceId
      remainingQuantity
      status
      unit
      createTimestamp
      product {
        name
        id
        size
        unit
        traceabilityTypeName
        strain { name id }
      }
      room { id name }
    }
  }
}
""".strip()
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
COST_TREND_DERIVED_COLUMNS = {
    "Processing Labor": [
        "624 Wages - Buck",
        "628.1 Contract Labor - Trim",
    ],
    "Production Labor": [
        "622 Wages - Grow",
    ],
    "Consulting": [
        "606 Consulting",
    ],
    "Total Direct Labor": [
        "Total 620 Direct Labor",
    ],
}

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

INVENTORY_COLUMN_ALIASES = [
    ("product.name", "Product"),
    ("product.strain.name", "Strain"),
    ("remainingQuantity", "Quantity"),
    ("product.traceabilityTypeName", "Type"),
    ("room.name", "Room"),
    ("room.id", "Room ID"),
    ("status", "Status"),
    ("unit", "Unit"),
    ("complianceId", "Compliance ID"),
    ("birthDate", "Birth Date"),
    ("createTimestamp", "Created At"),
    ("product.size", "Product Size"),
    ("product.unit", "Product Unit"),
]

def fmt_usd(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "$0"
    return f"${v:,.0f}"

def fmt_g(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "0 g"
    return f"{v:,.0f} g"

def pct_value(n, t):
    return n / t * 100 if t else 0.0

def slugify_filename(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return slug or "section"

def pdf_value(value, column: str = ""):
    if value is None or pd.isna(value):
        return ""
    column_key = str(column).casefold()
    if isinstance(value, (int, float)):
        if "$/gram" in column_key or "price" in column_key:
            return f"${value:,.2f}"
        if any(token in column_key for token in [
            "amount",
            "revenue",
            "total",
            "income",
            "cost",
            "profit",
            "expenses",
            "estimated",
        ]):
            return f"${value:,.0f}"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    text = str(value)
    return text if len(text) <= 70 else f"{text[:67]}..."

def pdf_table_data(df: pd.DataFrame, max_rows: int = 120) -> tuple[list[list[str]], int]:
    if df is None or df.empty:
        return [["No rows"]], 0
    export_df = df.head(max_rows).copy()
    headers = [str(col) for col in export_df.columns]
    rows = [
        [pdf_value(row[col], col) for col in export_df.columns]
        for _, row in export_df.iterrows()
    ]
    return [headers, *rows], max(0, len(df) - len(export_df))

def build_section_pdf(title: str, table_df: pd.DataFrame | None = None, fig=None) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PDF_PAGE_SIZE,
        leftMargin=PDF_MARGIN,
        rightMargin=PDF_MARGIN,
        topMargin=PDF_MARGIN,
        bottomMargin=PDF_MARGIN,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(str(title), styles["Title"]), Spacer(1, 0.15 * inch)]

    if fig is not None:
        try:
            image_bytes = fig.to_image(format="png", width=1600, height=900, scale=2)
            chart = Image(BytesIO(image_bytes))
            chart._restrictSize(PDF_USABLE_WIDTH, min(PDF_USABLE_HEIGHT - 1.2 * inch, 5.9 * inch))
            story.extend([chart, Spacer(1, 0.18 * inch)])
        except Exception as err:
            story.extend([
                Paragraph(
                    f"Chart image could not be rendered in this environment: {err}",
                    styles["Italic"],
                ),
                Spacer(1, 0.12 * inch),
            ])

    if table_df is not None:
        table_data, truncated_rows = pdf_table_data(table_df)
        col_count = max(1, len(table_data[0]))
        table = Table(
            table_data,
            repeatRows=1,
            colWidths=[PDF_USABLE_WIDTH / col_count] * col_count,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 6),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d0d0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f6f6")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
        if truncated_rows:
            story.extend([
                Spacer(1, 0.12 * inch),
                Paragraph(f"{truncated_rows:,} additional rows omitted from this PDF.", styles["Italic"]),
            ])

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def section_pdf_signature(title: str, table_df: pd.DataFrame | None = None) -> str:
    if table_df is None:
        return f"{PDF_EXPORT_VERSION}|{title}|none"
    try:
        sample = pd.concat([table_df.head(20), table_df.tail(20)]).drop_duplicates()
        data_hash = int(pd.util.hash_pandas_object(sample, index=True).sum())
    except Exception:
        data_hash = hash(str(table_df.head(5).to_dict()))
    return f"{PDF_EXPORT_VERSION}|{title}|{table_df.shape}|{tuple(map(str, table_df.columns))}|{data_hash}"

def section_pdf_export(
    title: str,
    key: str,
    table_df: pd.DataFrame | None = None,
    fig=None,
):
    state_key = f"pdf_export_{key}"
    err_key = f"pdf_export_error_{key}"
    sig_key = f"pdf_export_signature_{key}"
    signature = section_pdf_signature(title, table_df)
    if st.session_state.get(sig_key) != signature:
        st.session_state.pop(state_key, None)
        st.session_state.pop(err_key, None)
        st.session_state[sig_key] = signature

    c1, c2 = st.columns([1, 5])
    if c1.button("Prepare PDF", key=f"prepare_pdf_{key}"):
        try:
            st.session_state[state_key] = build_section_pdf(title, table_df=table_df, fig=fig)
            st.session_state.pop(err_key, None)
        except Exception as err:
            st.session_state.pop(state_key, None)
            st.session_state[err_key] = str(err)
    if state_key in st.session_state:
        c1.download_button(
            "Download PDF",
            data=st.session_state[state_key],
            file_name=f"{slugify_filename(title)}.pdf",
            mime="application/pdf",
            key=f"download_pdf_{key}",
        )
    if err_key in st.session_state:
        c2.caption(f"PDF export unavailable: {st.session_state[err_key]}")

def metrics_pdf_table(metrics: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Metric": label, "Value": value, "Detail": detail}
            for label, value, detail in metrics
        ]
    )

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

def format_date_range_value(date_value):
    return f"{date_value:%b} {date_value.day}, {date_value.year}"

def show_active_date_range(start_date, end_date):
    st.caption(
        "Date range: "
        f"{format_date_range_value(start_date)} to {format_date_range_value(end_date)}"
    )

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

def normalize_inventory_facility(value) -> str:
    text = str(value or "").strip()
    normalized = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if normalized in {"b13", "block13"}:
        return "Block 13"
    if normalized in {"b9", "b09"}:
        return "B-9"
    return text or "Unassigned"

def normalize_inventory_column_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

def inventory_column_has_values(series: pd.Series) -> bool:
    cleaned = series.dropna().astype(str).str.strip()
    cleaned = cleaned[cleaned.ne("") & cleaned.str.casefold().ne("nan")]
    return not cleaned.empty

def apply_inventory_column_aliases(inventory: pd.DataFrame) -> pd.DataFrame:
    out = inventory.copy()
    columns_by_key = {
        normalize_inventory_column_key(col): col
        for col in out.columns
    }
    for source, target in INVENTORY_COLUMN_ALIASES:
        source_col = columns_by_key.get(normalize_inventory_column_key(source))
        if not source_col:
            continue
        if target in out.columns and inventory_column_has_values(out[target]):
            continue
        out[target] = out[source_col]
        columns_by_key[normalize_inventory_column_key(target)] = target
    return out

def find_inventory_facility_column(inventory: pd.DataFrame) -> str:
    named_candidates = [
        "Facility",
        "Source",
        "Location",
    ]
    for col in named_candidates:
        if col in inventory.columns:
            return col

    best_col = ""
    best_score = 0
    unnamed_cols = [
        col for col in inventory.columns
        if str(col).strip().casefold().startswith("unnamed")
    ]
    for col in unnamed_cols:
        normalized = inventory[col].apply(normalize_inventory_facility)
        score = normalized.isin({"B-9", "Block 13"}).sum()
        if score > best_score:
            best_col = col
            best_score = score
    return best_col

def parse_inventory_tab(raw: pd.DataFrame) -> pd.DataFrame:
    inventory = raw.copy()
    inventory.columns = [str(c).strip() for c in inventory.columns]
    inventory = apply_inventory_column_aliases(inventory)
    required = {"Product", "Strain", "Quantity"}
    missing = required - set(inventory.columns)
    if missing:
        raise ValueError(f"Inventory tab must include: {', '.join(sorted(missing))}.")

    inventory = inventory.dropna(how="all").copy()
    for col in [
        "Type",
        "Category",
        "Product",
        "Strain",
        "Status",
        "Unit",
        "Room",
        "Room ID",
        "Compliance ID",
        "Product Unit",
    ]:
        if col in inventory.columns:
            inventory[col] = inventory[col].astype(str).str.strip()

    for col in ["Quantity", "Age (Weeks)", "Avg Sales/Week", "# Packages", "Stock Coverage Ratio"]:
        if col in inventory.columns:
            inventory[col] = _clean_numeric(inventory[col])

    for col in ["Birth Date", "Created At"]:
        if col in inventory.columns:
            inventory[col] = pd.to_datetime(inventory[col], errors="coerce")

    facility_col = find_inventory_facility_column(inventory)
    if facility_col:
        inventory["Facility"] = inventory[facility_col].apply(normalize_inventory_facility)
    else:
        inventory["Facility"] = "Unassigned"

    inventory["Product"] = inventory["Product"].map(PRODUCT_ALIASES).fillna(inventory["Product"])
    inventory = inventory[
        inventory["Product"].ne("")
        & inventory["Product"].str.lower().ne("nan")
        & inventory["Strain"].ne("")
        & inventory["Strain"].str.lower().ne("nan")
    ].copy()
    return inventory

def latest_sales_ppg_summary(
    source_df: pd.DataFrame,
    group_cols: list[str],
    price_col: str,
    date_col: str,
) -> pd.DataFrame:
    out_cols = [*group_cols, price_col, date_col]
    if source_df.empty:
        return pd.DataFrame(columns=out_cols)
    required = {"Units UOM", "Units", "Total", "Transfer Date", *group_cols}
    if not required.issubset(source_df.columns):
        return pd.DataFrame(columns=out_cols)

    grams = source_df[source_df["Units UOM"].astype(str).str.strip().eq("Grams")].copy()
    if grams.empty:
        return pd.DataFrame(columns=out_cols)

    grams["Units"] = pd.to_numeric(grams["Units"], errors="coerce")
    grams["Total"] = pd.to_numeric(grams["Total"], errors="coerce")
    grams["Transfer Date"] = pd.to_datetime(grams["Transfer Date"], errors="coerce")
    grams = grams[(grams["Units"] > 0) & (grams["Total"] > 0)].copy()
    if grams.empty:
        return pd.DataFrame(columns=out_cols)

    grams[price_col] = grams["Total"] / grams["Units"]
    grams = grams[grams[price_col].notna() & (grams[price_col] > 0)].copy()
    grams["_source_order"] = range(len(grams))
    grams = grams.sort_values(
        ["Transfer Date", "_source_order"],
        ascending=[False, False],
        na_position="last",
    )
    latest = grams.drop_duplicates(subset=group_cols, keep="first").copy()
    latest[date_col] = latest["Transfer Date"]
    return latest[out_cols]

def add_inventory_revenue_estimates(inventory_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    inventory = inventory_df.copy()
    has_facility_pricing = "Facility" in inventory.columns and "Facility" in sales_df.columns
    if has_facility_pricing:
        facility_exact = latest_sales_ppg_summary(
            sales_df,
            ["Facility", "Product", "Strain", "Brand"],
            "Facility Exact $/gram",
            "Facility Exact Price Date",
        )
        facility_product_strain = latest_sales_ppg_summary(
            sales_df,
            ["Facility", "Product", "Strain"],
            "Facility Product + Strain $/gram",
            "Facility Product + Strain Price Date",
        )
        facility_product_brand = latest_sales_ppg_summary(
            sales_df,
            ["Facility", "Product", "Brand"],
            "Facility Product + Brand $/gram",
            "Facility Product + Brand Price Date",
        )
        facility_product = latest_sales_ppg_summary(
            sales_df,
            ["Facility", "Product"],
            "Facility Product $/gram",
            "Facility Product Price Date",
        )

        inventory = inventory.merge(facility_exact, on=["Facility", "Product", "Strain", "Brand"], how="left")
        inventory = inventory.merge(facility_product_strain, on=["Facility", "Product", "Strain"], how="left")
        inventory = inventory.merge(facility_product_brand, on=["Facility", "Product", "Brand"], how="left")
        inventory = inventory.merge(facility_product, on=["Facility", "Product"], how="left")
        price_sources = [
            ("Facility Exact $/gram", "Facility Exact Price Date", "Facility + Product + Strain + Brand"),
            (
                "Facility Product + Strain $/gram",
                "Facility Product + Strain Price Date",
                "Facility + Product + Strain",
            ),
            (
                "Facility Product + Brand $/gram",
                "Facility Product + Brand Price Date",
                "Facility + Product + Brand",
            ),
            ("Facility Product $/gram", "Facility Product Price Date", "Facility + Product"),
        ]
    else:
        exact = latest_sales_ppg_summary(
            sales_df, ["Product", "Strain", "Brand"], "Exact $/gram", "Exact Price Date"
        )
        product_strain = latest_sales_ppg_summary(
            sales_df, ["Product", "Strain"], "Product + Strain $/gram", "Product + Strain Price Date"
        )
        product_brand = latest_sales_ppg_summary(
            sales_df, ["Product", "Brand"], "Product + Brand $/gram", "Product + Brand Price Date"
        )
        product = latest_sales_ppg_summary(
            sales_df, ["Product"], "Product $/gram", "Product Price Date"
        )

        inventory = inventory.merge(exact, on=["Product", "Strain", "Brand"], how="left")
        inventory = inventory.merge(product_strain, on=["Product", "Strain"], how="left")
        inventory = inventory.merge(product_brand, on=["Product", "Brand"], how="left")
        inventory = inventory.merge(product, on=["Product"], how="left")
        price_sources = [
            ("Exact $/gram", "Exact Price Date", "Product + Strain + Brand"),
            ("Product + Strain $/gram", "Product + Strain Price Date", "Product + Strain"),
            ("Product + Brand $/gram", "Product + Brand Price Date", "Product + Brand"),
            ("Product $/gram", "Product Price Date", "Product"),
        ]
    inventory["Recent $/gram"] = pd.NA
    inventory["Price Date"] = pd.NaT
    inventory["Price Source"] = "No sales match"
    for col, source_date_col, label in price_sources:
        missing_price = inventory["Recent $/gram"].isna()
        has_price = inventory[col].notna()
        price_mask = missing_price & has_price
        inventory.loc[price_mask, "Recent $/gram"] = inventory.loc[price_mask, col]
        inventory.loc[price_mask, "Price Date"] = inventory.loc[price_mask, source_date_col]
        inventory.loc[price_mask, "Price Source"] = label

    inventory["Recent $/gram"] = pd.to_numeric(inventory["Recent $/gram"], errors="coerce")
    inventory["Estimated Revenue"] = inventory["Quantity"] * inventory["Recent $/gram"].fillna(0)
    return inventory

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

def add_cost_trend_derived_columns(monthly: pd.DataFrame) -> pd.DataFrame:
    for label, source_cols in COST_TREND_DERIVED_COLUMNS.items():
        monthly[label] = 0.0
        for col in source_cols:
            if col in monthly.columns:
                monthly[label] += monthly[col]
    return monthly

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
        selected_companies = []
        if companies:
            st.caption("Company")
            company_cols = st.columns(len(companies))
            for col, company in zip(company_cols, companies):
                key_part = re.sub(r"[^a-z0-9]+", "_", company.casefold()).strip("_")
                if col.checkbox(company, value=True, key=f"cost_company_{key_part}"):
                    selected_companies.append(company)

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
    show_active_date_range(start_date, end_date)

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
    section_pdf_export(
        "Costs KPIs",
        "costs_kpis",
        table_df=metrics_pdf_table([
            ("Total Income", fmt_usd(income), ""),
            ("COGS", fmt_usd(cogs), f"{pct_value(cogs, income):.1f}% of income"),
            ("Gross Profit", fmt_usd(gross_profit), f"{pct_value(gross_profit, income):.1f}% margin"),
            ("Expenses", fmt_usd(expenses), f"{pct_value(expenses, income):.1f}% of income"),
            ("Net Income", fmt_usd(net_income), f"{pct_value(net_income, income):.1f}% margin"),
        ]),
    )

    st.divider()

    st.subheader("Monthly Performance")
    monthly = (
        costs_view.groupby("Statement Month", as_index=False)
        .sum(numeric_only=True)
        .sort_values("Statement Month")
    )
    monthly = add_cost_trend_derived_columns(monthly)
    trend_cols = [
        col for col in [
            *COST_TREND_COLUMNS,
            *COST_TREND_DERIVED_COLUMNS.keys(),
        ]
        if col in monthly.columns
    ]
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
        section_pdf_export(
            "Costs Monthly Performance",
            "costs_monthly_performance",
            table_df=trend,
            fig=fig,
        )

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
        section_pdf_export(
            "Costs Top Cost Lines",
            "costs_top_lines",
            table_df=top_detail,
            fig=fig_costs,
        )
    else:
        st.caption("No nonzero cost lines for the selected filters.")

    st.divider()

    st.subheader("Monthly Statement")
    summary_cols = ["Month", "Company"] + [
        col for col in COST_SUMMARY_COLUMNS if col in costs_view.columns
    ]
    statement_df = costs_view.sort_values(["Statement Month", "Company"])[summary_cols]
    st.dataframe(
        statement_df,
        width="stretch",
        hide_index=True,
        column_config={
            col: st.column_config.NumberColumn(col, format="$%.0f")
            for col in summary_cols
            if col not in {"Month", "Company"}
        },
    )
    section_pdf_export(
        "Costs Monthly Statement",
        "costs_monthly_statement",
        table_df=statement_df,
    )

    with st.expander("Detailed income statement"):
        detail_cols = [
            col for col in costs_view.columns
            if col != "Statement Month"
        ]
        income_statement_df = costs_view.sort_values(["Statement Month", "Company"])[detail_cols]
        st.dataframe(
            income_statement_df,
            width="stretch",
            hide_index=True,
            column_config={
                col: st.column_config.NumberColumn(col, format="$%.0f")
                for col in detail_cols
                if col not in {"Month", "Company"}
            },
        )
        section_pdf_export(
            "Detailed Income Statement",
            "costs_detailed_income_statement",
            table_df=income_statement_df,
        )

def render_analytics_tab(combined_df: pd.DataFrame):
    if combined_df.empty:
        st.info("No Brand Sales or Wholesale records found.")
        return

    analytics_df = combined_df.copy()
    analytics_df["Transfer Date"] = pd.to_datetime(
        analytics_df["Transfer Date"],
        errors="coerce",
    )
    analytics_df["Transfer Day"] = analytics_df["Transfer Date"].dt.date
    analytics_df = analytics_df.dropna(subset=["Transfer Day"]).copy()
    if analytics_df.empty:
        st.caption("Analytics unavailable -- Transfer Date not parsed.")
        return

    brands = sorted(
        analytics_df["Brand"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
    )
    products = sorted(
        analytics_df["Product"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
    )
    min_date, max_date, from_default, to_default = current_ytd_date_bounds(
        analytics_df["Transfer Day"]
    )

    af1, af2, af3, af4 = st.columns([2, 2, 1, 1])
    selected_brands = af1.multiselect(
        "Brand",
        options=brands,
        default=brands,
        key="analytics_brands",
    )
    selected_products = af2.multiselect(
        "Product Types",
        options=products,
        default=products,
        key="analytics_product_types",
    )
    from_date = af3.date_input(
        "From",
        value=from_default,
        min_value=min_date,
        max_value=max_date,
        key="analytics_from",
    )
    to_date = af4.date_input(
        "To",
        value=to_default,
        min_value=min_date,
        max_value=max_date,
        key="analytics_to",
    )
    start_date, end_date = sorted([from_date, to_date])
    show_active_date_range(start_date, end_date)

    if not selected_brands or not selected_products:
        st.caption("Select at least one brand and product type.")
        return

    filtered = analytics_df[
        analytics_df["Brand"].isin(selected_brands)
        & analytics_df["Product"].isin(selected_products)
        & analytics_df["Transfer Day"].between(start_date, end_date)
    ].copy()
    if filtered.empty:
        st.caption("No analytics data for the selected filters.")
        return

    filtered["Month"] = filtered["Transfer Date"].dt.to_period("M").astype(str)
    monthly = (
        filtered[filtered["Month"].str.match(r"\d{4}-\d{2}")]
        .groupby(["Month", "Sale Type"], as_index=False)
        .agg(Revenue=("Total", "sum"))
        .sort_values(["Month", "Sale Type"])
    )
    if monthly.empty:
        st.caption("Monthly analytics unavailable for the selected filters.")
        return

    monthly["Month Total"] = monthly.groupby("Month")["Revenue"].transform("sum")
    monthly["% of Month"] = monthly.apply(
        lambda row: pct_value(row["Revenue"], row["Month Total"]),
        axis=1,
    )
    monthly["Share Label"] = monthly["% of Month"].map(lambda value: f"{value:.0f}%")

    st.subheader("Monthly Sales Mix")
    fig_mix = px.bar(
        monthly,
        x="Month",
        y="Revenue",
        color="Sale Type",
        barmode="stack",
        text="Share Label",
        color_discrete_map=SALE_TYPE_COLORS,
        custom_data=["Revenue", "% of Month", "Month Total"],
    )
    fig_mix.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e3e3d8", height=390,
        margin=dict(l=0, r=20, t=10, b=10),
        xaxis_title="", yaxis_title="Revenue ($)",
        legend_title="Sale Type",
    )
    fig_mix.update_yaxes(tickprefix="$")
    fig_mix.update_traces(
        textposition="inside",
        insidetextanchor="middle",
        cliponaxis=False,
        hovertemplate=(
            "%{x}<br>%{legendgroup}: %{customdata[0]:$,.0f}"
            "<br>Share: %{customdata[1]:.1f}%"
            "<br>Month total: %{customdata[2]:$,.0f}<extra></extra>"
        ),
    )
    st.plotly_chart(fig_mix, width="stretch")

    monthly_export = monthly[[
        "Month",
        "Sale Type",
        "Revenue",
        "Month Total",
        "% of Month",
    ]].copy()
    section_pdf_export(
        "Analytics Monthly Sales Mix",
        "analytics_monthly_sales_mix",
        table_df=monthly_export,
        fig=fig_mix,
    )

def render_inventory_tab(
    inventory_df: pd.DataFrame,
    inventory_error: str,
    sales_df: pd.DataFrame,
    strain_map: dict,
    selected_facility: str,
):
    if inventory_error:
        st.error(inventory_error)
        return
    if inventory_df.empty:
        st.info("No inventory data loaded.")
        return

    inventory_view = inventory_df.copy()
    if selected_facility != "Both":
        inventory_view = inventory_view[inventory_view["Facility"] == selected_facility].copy()
    if inventory_view.empty:
        st.caption("No inventory data for the selected facility.")
        return

    inventory_view["Brand"] = inventory_view["Strain"].map(strain_map).where(
        lambda s: s.isin(BRANDS_NAMED),
        "Unassigned",
    )
    inventory_view = add_inventory_revenue_estimates(inventory_view, sales_df)

    inventory_product_key = f"inventory_product_types_{slugify_filename(selected_facility)}"
    selected_products = product_type_multiselect(inventory_view, inventory_product_key)
    if not selected_products:
        st.caption("No product types selected.")
        return
    inventory_view = inventory_view[inventory_view["Product"].isin(selected_products)].copy()
    if inventory_view.empty:
        st.caption("No inventory rows for the selected product types.")
        return

    priced = inventory_view[inventory_view["Recent $/gram"].notna()].copy()
    total_quantity = inventory_view["Quantity"].sum()
    priced_quantity = priced["Quantity"].sum()
    estimated_revenue = inventory_view["Estimated Revenue"].sum()
    weighted_recent_ppg = estimated_revenue / priced_quantity if priced_quantity > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Estimated Unrealized Revenue", fmt_usd(estimated_revenue))
    k2.metric("Priced Quantity", fmt_g(priced_quantity), f"{pct_value(priced_quantity, total_quantity):.1f}% of inventory")
    k3.metric("Weighted Recent $/gram", f"${weighted_recent_ppg:.2f}")
    k4.metric("Inventory Rows", f"{len(inventory_view):,}", f"{len(priced):,} priced")
    section_pdf_export(
        "Inventory KPIs",
        "inventory_kpis",
        table_df=metrics_pdf_table([
            ("Estimated Unrealized Revenue", fmt_usd(estimated_revenue), ""),
            ("Priced Quantity", fmt_g(priced_quantity), f"{pct_value(priced_quantity, total_quantity):.1f}% of inventory"),
            ("Weighted Recent $/gram", f"${weighted_recent_ppg:.2f}", ""),
            ("Inventory Rows", f"{len(inventory_view):,}", f"{len(priced):,} priced"),
        ]),
    )

    st.divider()

    product_summary = (
        inventory_view.groupby(["Product", "Brand"], as_index=False)
        .agg(
            Quantity=("Quantity", "sum"),
            Estimated_Revenue=("Estimated Revenue", "sum"),
        )
    )
    product_summary["Weighted Recent $/gram"] = product_summary.apply(
        lambda row: row["Estimated_Revenue"] / row["Quantity"] if row["Quantity"] > 0 else 0,
        axis=1,
    )

    st.subheader("Estimated Value by Product")
    valued_summary = product_summary[product_summary["Estimated_Revenue"] > 0].copy()
    if not valued_summary.empty:
        fig_inv = px.bar(
            valued_summary.sort_values("Estimated_Revenue", ascending=True),
            x="Estimated_Revenue",
            y="Product",
            color="Brand",
            orientation="h",
            text=valued_summary.sort_values("Estimated_Revenue", ascending=True)["Estimated_Revenue"].apply(fmt_usd),
            color_discrete_map=BRAND_COLORS,
        )
        fig_inv.update_layout(
            barmode="stack",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e3e3d8",
            height=max(320, valued_summary["Product"].nunique() * 70),
            margin=dict(l=0, r=70, t=10, b=10),
            xaxis_title="Estimated Unrealized Revenue", yaxis_title="",
            legend_title="Brand",
        )
        fig_inv.update_xaxes(tickprefix="$")
        fig_inv.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_inv, width="stretch")
        section_pdf_export(
            "Inventory Estimated Value by Product",
            "inventory_value_by_product",
            table_df=valued_summary,
            fig=fig_inv,
        )
    else:
        st.caption("No selected inventory rows have a matching recent historical sales price.")

    st.divider()

    st.subheader("Inventory Detail")
    detail_cols = [
        "Facility",
        "Type",
        "Category",
        "Product",
        "Strain",
        "Brand",
        "Quantity",
        "Unit",
        "Status",
        "Room",
        "Compliance ID",
        "Birth Date",
        "Created At",
        "Recent $/gram",
        "Estimated Revenue",
        "Price Date",
        "Price Source",
        "Age (Weeks)",
        "Avg Sales/Week",
        "# Packages",
        "Stock Coverage Ratio",
    ]
    detail_cols = [col for col in detail_cols if col in inventory_view.columns]
    inventory_detail = inventory_view.copy()
    inventory_detail["_Facility Sort"] = (
        inventory_detail["Facility"].map(FACILITY_SORT_ORDER).fillna(98)
    )
    inventory_detail = inventory_detail.sort_values(
        ["_Facility Sort", "Product", "Estimated Revenue"],
        ascending=[True, True, False],
    )[detail_cols]
    st.dataframe(
        inventory_detail,
        width="stretch",
        hide_index=True,
        column_config={
            "Quantity": st.column_config.NumberColumn("Quantity", format="%.0f"),
            "Recent $/gram": st.column_config.NumberColumn("Recent $/gram", format="$%.2f"),
            "Estimated Revenue": st.column_config.NumberColumn("Estimated Revenue", format="$%.0f"),
            "Price Date": st.column_config.DateColumn("Price Date", format="YYYY-MM-DD"),
            "Birth Date": st.column_config.DateColumn("Birth Date", format="YYYY-MM-DD"),
            "Created At": st.column_config.DatetimeColumn("Created At", format="YYYY-MM-DD HH:mm"),
            "Age (Weeks)": st.column_config.NumberColumn("Age (Weeks)", format="%.1f"),
            "Avg Sales/Week": st.column_config.NumberColumn("Avg Sales/Week", format="%.2f"),
            "# Packages": st.column_config.NumberColumn("# Packages", format="%.0f"),
            "Stock Coverage Ratio": st.column_config.NumberColumn("Stock Coverage Ratio", format="%.1f"),
        },
    )
    section_pdf_export(
        "Inventory Detail",
        "inventory_detail",
        table_df=inventory_detail,
    )

    unpriced = inventory_view[
        inventory_view["Recent $/gram"].isna()
        & (inventory_view["Quantity"] > 0)
    ].copy()
    if not unpriced.empty:
        with st.expander("Inventory without a sales price match"):
            unpriced_detail = unpriced.sort_values("Quantity", ascending=False)[detail_cols]
            st.dataframe(
                unpriced_detail,
                width="stretch",
                hide_index=True,
                column_config={
                    "Quantity": st.column_config.NumberColumn("Quantity", format="%.0f"),
                    "Recent $/gram": st.column_config.NumberColumn("Recent $/gram", format="$%.2f"),
                    "Estimated Revenue": st.column_config.NumberColumn("Estimated Revenue", format="$%.0f"),
                    "Price Date": st.column_config.DateColumn("Price Date", format="YYYY-MM-DD"),
                    "Birth Date": st.column_config.DateColumn("Birth Date", format="YYYY-MM-DD"),
                    "Created At": st.column_config.DatetimeColumn("Created At", format="YYYY-MM-DD HH:mm"),
                },
            )
            section_pdf_export(
                "Inventory without a Sales Price Match",
                "inventory_unpriced",
                table_df=unpriced_detail,
            )

def render_material_ppg_metrics(view_g: pd.DataFrame, key_prefix: str, title: str):
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
    section_pdf_export(
        title,
        f"{key_prefix}_material_ppg_metrics",
        table_df=ppg[["Product", "$/gram", "Grams", "Revenue"]],
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
    section_pdf_export(
        f"{key_prefix.title()} PPG Over Time",
        f"{key_prefix}_ppg_over_time",
        table_df=trend,
        fig=fig,
    )

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
    "gid_inventory": ("production_gid_inventory", "prod_gid_inventory", "PRODUCTION_GID_INVENTORY"),
}

def secret_or_env(*names: str, default: str = "") -> str:
    for name in names:
        try:
            value = st.secrets.get(name, "")
            if str(value).strip():
                return str(value).strip()
        except Exception:
            pass
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default

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
def growflow_secret_value(name: str):
    try:
        return st.secrets.get(name)
    except Exception:
        return None

def growflow_source_value(source, *names: str, default: str = "") -> str:
    for name in names:
        try:
            value = source.get(name, "")
        except Exception:
            value = ""
        if str(value).strip():
            return str(value).strip()
    return default

def growflow_inventory_sources_config() -> tuple[tuple[str, str, str], ...]:
    default_region = secret_or_env("growflow_region_code", "GROWFLOW_REGION_CODE", default="wa")
    sources = []
    raw_sources = growflow_secret_value("growflow_inventory_sources")
    if isinstance(raw_sources, str) and raw_sources.strip():
        try:
            raw_sources = json.loads(raw_sources)
        except json.JSONDecodeError:
            raw_sources = []
    if isinstance(raw_sources, dict):
        if any(isinstance(value, dict) for value in raw_sources.values()):
            raw_sources = list(raw_sources.values())
        else:
            raw_sources = [raw_sources]
    if raw_sources:
        for source in raw_sources:
            license_number = growflow_source_value(source, "license_number", "licenseNumber")
            if not license_number:
                continue
            region_code = growflow_source_value(source, "region_code", "regionCode", default=default_region)
            facility_label = growflow_source_value(source, "facility_label", "facilityLabel", "facility")
            sources.append((region_code, license_number, facility_label))

    if not sources:
        license_number = secret_or_env("growflow_license_number", "GROWFLOW_LICENSE_NUMBER")
        if license_number:
            sources.append((
                default_region,
                license_number,
                secret_or_env("growflow_facility_label", "GROWFLOW_FACILITY_LABEL"),
            ))

    deduped = []
    seen = set()
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        deduped.append(source)
    return tuple(deduped)

def growflow_inventory_api_config() -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    client_id = secret_or_env("growflow_client_id", "GROWFLOW_CLIENT_ID")
    client_secret = secret_or_env("growflow_client_secret", "GROWFLOW_CLIENT_SECRET")
    return client_id, client_secret, growflow_inventory_sources_config()

def growflow_inventory_api_ready() -> bool:
    client_id, client_secret, sources = growflow_inventory_api_config()
    return bool(client_id and client_secret and sources)

def growflow_api_error_preview(response: requests.Response, limit: int = 500) -> str:
    text = response.text[:limit]
    return re.sub(r'"access_token"\s*:\s*"[^"]+"', '"access_token":"REDACTED"', text)

def growflow_post_json(url: str, payload: dict, label: str, headers: dict | None = None, timeout: int = 30) -> dict:
    response = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"{label} failed with HTTP {response.status_code}: {growflow_api_error_preview(response)}")
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"{label} did not return valid JSON: {response.text[:300]}") from exc

def fetch_growflow_access_token(client_id: str, client_secret: str) -> str:
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": GROWFLOW_AUDIENCE,
        "grant_type": "client_credentials",
    }
    data = growflow_post_json(GROWFLOW_TOKEN_URL, payload, "GrowFlow token request", timeout=20)
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise ValueError("GrowFlow token request succeeded, but no access_token was returned.")
    return token

def flatten_growflow_inventory_item(item: dict, facility_label: str) -> dict:
    product = item.get("product") or {}
    strain = product.get("strain") or {}
    room = item.get("room") or {}
    row = {
        "birthDate": item.get("birthDate"),
        "complianceId": item.get("complianceId"),
        "remainingQuantity": item.get("remainingQuantity"),
        "status": item.get("status"),
        "unit": item.get("unit"),
        "createTimestamp": item.get("createTimestamp"),
        "product.name": product.get("name"),
        "product.id": product.get("id"),
        "product.size": product.get("size"),
        "product.unit": product.get("unit"),
        "product.traceabilityTypeName": product.get("traceabilityTypeName"),
        "product.strain.name": strain.get("name"),
        "product.strain.id": strain.get("id"),
        "room.id": room.get("id"),
        "room.name": room.get("name"),
    }
    if facility_label:
        row["Facility"] = facility_label
    return row

def fetch_growflow_inventory_source(token: str, region_code: str, license_number: str, facility_label: str) -> list[dict]:
    rows = []
    skip = 0
    take = GROWFLOW_MAX_PAGE_SIZE
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        variables = {
            "regionCode": region_code,
            "licenseNumber": license_number,
            "skip": skip,
            "take": take,
        }
        data = growflow_post_json(
            GROWFLOW_GRAPHQL_URL,
            {"query": GROWFLOW_INVENTORY_QUERY, "variables": variables},
            "GrowFlow inventory request",
            headers=headers,
        )
        if data.get("errors"):
            preview = json.dumps(data["errors"])[:800]
            raise ValueError(f"GrowFlow inventory GraphQL errors: {preview}")
        connection = ((data.get("data") or {}).get("inventories") or {})
        items = connection.get("items") or []
        rows.extend(flatten_growflow_inventory_item(item or {}, facility_label) for item in items)

        total_count = connection.get("totalCount")
        try:
            total_count = int(total_count)
        except (TypeError, ValueError):
            total_count = None
        if not items or len(items) < take or (total_count is not None and len(rows) >= total_count):
            break
        skip += len(items)
    return rows

@st.cache_data(ttl=300, show_spinner=False)
def load_growflow_inventory_api(
    client_id: str,
    client_secret: str,
    sources: tuple[tuple[str, str, str], ...],
) -> pd.DataFrame:
    token = fetch_growflow_access_token(client_id, client_secret)
    rows = []
    for region_code, license_number, facility_label in sources:
        rows.extend(fetch_growflow_inventory_source(token, region_code, license_number, facility_label))
    if not rows:
        raise ValueError("GrowFlow inventory returned no rows.")
    return pd.DataFrame(rows)

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
_default_gid_inventory = saved_or_configured_setting(_saved, "gid_inventory", DEFAULT_INVENTORY_GID)
_growflow_client_id, _growflow_client_secret, _growflow_inventory_sources = growflow_inventory_api_config()
_growflow_inventory_ready = bool(
    _growflow_client_id and _growflow_client_secret and _growflow_inventory_sources
)

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
    gid_inventory = st.text_input(
        "Inventory GID" if not _growflow_inventory_ready else "Inventory GID fallback",
        value=_default_gid_inventory,
        key="prod_gid_inventory",
    )
    if _growflow_inventory_ready:
        source_word = "source" if len(_growflow_inventory_sources) == 1 else "sources"
        st.caption(
            f"Inventory source: GrowFlow API ({len(_growflow_inventory_sources)} {source_word}); "
            "cached for 5 minutes."
        )
    elif _growflow_client_id or _growflow_client_secret or _growflow_inventory_sources:
        st.caption(
            "GrowFlow inventory API is partially configured. Add client id, client secret, "
            "and license number to Streamlit secrets to bypass the Inventory GID."
        )

    if st.button("Load / Refresh", type="primary", width="stretch"):
        if sheet_url.strip():
            save_setting("sheet_url",  sheet_url.strip())
            save_setting("gid_b13",    gid_b13.strip())
            save_setting("gid_b9",     gid_b9.strip())
            save_setting("gid_assign", gid_assign.strip())
            save_setting("gid_costs",  gid_costs.strip())
            save_setting("gid_inventory", gid_inventory.strip())
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

inventory_df = pd.DataFrame()
inventory_error = ""
if _growflow_inventory_ready:
    try:
        inventory_raw = load_growflow_inventory_api(
            _growflow_client_id,
            _growflow_client_secret,
            _growflow_inventory_sources,
        )
        inventory_df = parse_inventory_tab(inventory_raw)
    except Exception as e:
        inventory_error = f"**GrowFlow Inventory API**: {e}"
elif gid_inventory.strip():
    try:
        inventory_raw = load_tab(sheet_url, gid_inventory.strip())
        inventory_df = parse_inventory_tab(inventory_raw)
    except Exception as e:
        inventory_error = f"**Inventory**: {e}"

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
tab_brand, tab_wholesale, tab_both, tab_analytics, tab_inventory, tab_costs = st.tabs([
    "🏷️ Brand Sales",
    "🏪 Wholesale",
    "📊 Both",
    "📈 Analytics",
    "📦 Inventory",
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
        b_start, b_end = sorted([b_from, b_to])
        show_active_date_range(b_start, b_end)

        bview = named_df.copy()
        if sel_b_brand != "All":
            bview = bview[bview["Brand"] == sel_b_brand]
        if sel_b_type != "All":
            bview = bview[bview["Product"] == sel_b_type]
        _b_in_range = bview["Transfer Date"].dt.date.between(b_start, b_end).fillna(True)
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
        section_pdf_export(
            "Brand Sales KPIs",
            "brand_kpis",
            table_df=metrics_pdf_table([
                ("Total Revenue", fmt_usd(b_rev), ""),
                ("Total Volume", fmt_g(b_grams), ""),
                ("Avg $/gram", f"${b_ppg:.2f}", ""),
                ("Strains", f"{b_strains:,}", ""),
            ]),
        )
        render_material_ppg_metrics(
            bview_g,
            "brand",
            "Brand Sales Product $/gram Metrics",
        )

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
        section_pdf_export(
            "Brand Sales Strain by Brand",
            "brand_strain_by_brand",
            table_df=strain_tbl,
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
                    diagnostic_df = _d2[_dcols + ["Brand"]]
                    st.dataframe(diagnostic_df, width="stretch", hide_index=True)
                    section_pdf_export(
                        "Brand Sales Raw Data Lookup",
                        "brand_raw_data_lookup",
                        table_df=diagnostic_df,
                    )
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
            section_pdf_export(
                "Brand Sales Revenue by Strain",
                "brand_revenue_by_strain",
                table_df=strain_chart,
                fig=fig_s,
            )

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
                section_pdf_export(
                    "Brand Sales $/gram by Strain",
                    "brand_ppg_by_strain",
                    table_df=ppg_data,
                    fig=fig_ppg,
                )
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
            section_pdf_export(
                "Brand Sales Monthly Revenue",
                "brand_monthly_revenue",
                table_df=monthly,
                fig=fig_m,
            )
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
        w_start, w_end = sorted([w_from, w_to])
        show_active_date_range(w_start, w_end)

        wview = ws_df.copy()
        if sel_w_vendor != "All":
            wview = wview[wview["Vendor"] == sel_w_vendor]
        if sel_w_type != "All":
            wview = wview[wview["Product"] == sel_w_type]
        _w_in_range = wview["Transfer Date"].dt.date.between(w_start, w_end).fillna(True)
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
        section_pdf_export(
            "Wholesale KPIs",
            "wholesale_kpis",
            table_df=metrics_pdf_table([
                ("Total Revenue", fmt_usd(w_rev), ""),
                ("Total Volume", fmt_g(w_grams), ""),
                ("Avg $/gram", f"${w_ppg:.2f}", ""),
                ("Vendors", f"{wview['Vendor'].nunique():,}", ""),
            ]),
        )
        render_material_ppg_metrics(
            wview_g,
            "wholesale",
            "Wholesale Product $/gram Metrics",
        )

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
        section_pdf_export(
            "Wholesale Strain Summary",
            "wholesale_strain_summary",
            table_df=w_strain_tbl,
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
                section_pdf_export(
                    "Wholesale $/gram by Strain",
                    "wholesale_ppg_by_strain",
                    table_df=w_ppg_data,
                    fig=fig_wppg,
                )
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
            section_pdf_export(
                "Wholesale Volume by Vendor",
                "wholesale_volume_by_vendor",
                table_df=w_vol,
                fig=fig_vol,
            )

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
        c_start, c_end = sorted([c_from, c_to])
        show_active_date_range(c_start, c_end)

        cview = combined_df.copy()
        if sel_c_sale_type != "All":
            cview = cview[cview["Sale Type"] == sel_c_sale_type]
        if sel_c_type != "All":
            cview = cview[cview["Product"] == sel_c_type]
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
        section_pdf_export(
            "Combined KPIs",
            "both_kpis",
            table_df=metrics_pdf_table([
                ("Total Revenue", fmt_usd(c_rev), ""),
                ("Total Volume", fmt_g(c_grams), ""),
                ("Avg $/gram", f"${c_ppg:.2f}", ""),
                ("Vendors", f"{cview['Vendor'].nunique():,}", ""),
            ]),
        )
        render_material_ppg_metrics(
            cview_g,
            "both",
            "Combined Product $/gram Metrics",
        )

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
        section_pdf_export(
            "Combined Strain Summary",
            "both_combined_strain_summary",
            table_df=c_strain_tbl,
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
            section_pdf_export(
                "Combined Revenue by Strain",
                "both_revenue_by_strain",
                table_df=c_strain_chart,
                fig=fig_c_s,
            )

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
                section_pdf_export(
                    "Combined $/gram by Strain",
                    "both_ppg_by_strain",
                    table_df=c_ppg_data,
                    fig=fig_c_ppg,
                )
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
            section_pdf_export(
                "Combined Monthly Revenue",
                "both_monthly_revenue",
                table_df=c_monthly,
                fig=fig_c_m,
            )
        else:
            st.caption("Monthly trend unavailable — Transfer Date not parsed.")

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Analytics                                                ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_analytics:
    render_analytics_tab(combined_df)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Inventory                                                ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_inventory:
    render_inventory_tab(
        inventory_df,
        inventory_error,
        combined_df,
        strain_map,
        sel_facility,
    )

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB — Costs                                                     ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab_costs:
    render_costs_tab(costs_df, costs_error)
