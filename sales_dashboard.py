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

BLUE = "#378ADD"
DATA_DIR = Path("Data")
DB_PATH = DATA_DIR / "sales_dashboard.sqlite3"
TOTAL_PATTERN = re.compile(
    r"^(total|totals|sum|grand\s*total|ytd|year\s*to\s*date|annual|avg|average|subtotal)s?$",
    re.IGNORECASE,
)
MONTH_PATTERN = re.compile(
    r"^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december|q[1-4])(?:[\s._/-]*\d{2,4})?$",
    re.IGNORECASE,
)

# ── helpers ────────────────────────────────────────────────────────────────────

def fmt_usd(n):
    return f"${n:,.0f}"

def pct(n, t):
    return f"{pct_value(n, t):.1f}%"

def pct_value(n, t):
    return n / t * 100 if t else 0.0

def is_totals_col(header, values, other_cols):
    header_text = str(header).strip()
    if TOTAL_PATTERN.match(header_text):
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
            arr.append(float(re.sub(r"[$,\s]", "", val) or 0))
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
            row[months[ki]] = float(re.sub(r"[$,\s]", "", val) or 0)
        records.append(row)

    if not records:
        raise ValueError("No store rows found.")

    df = pd.DataFrame(records).set_index("License")

    # Deduplicate index: same license appearing twice gets "-2", "-3", etc.
    counts = {}
    new_index = []
    for lic in df.index:
        if lic in counts:
            counts[lic] += 1
            new_index.append(f"{lic}-{counts[lic]}")
        else:
            counts[lic] = 0
            new_index.append(lic)
    df.index = pd.Index(new_index, name=df.index.name)

    return df, months, stripped

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

st.title("Store Sales Dashboard")

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
    if "storage_notice" in st.session_state:
        st.success(st.session_state.storage_notice)
        del st.session_state.storage_notice

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
                st.session_state.storage_notice = f"Loaded {selected_saved.split(' · ')[0]}."
                st.rerun()
        if delete_col.button("Delete", use_container_width=True):
            delete_saved_dataset(saved_options[selected_saved])
            st.session_state.storage_notice = f"Deleted {selected_saved.split(' · ')[0]}."
            st.rerun()

    if st.button("Load demo data"):
        st.session_state.raw_input = sample
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
tab1, tab2 = st.tabs(["📊 All Stores", f"⭐ Top {int(threshold*100)}% Stores"])

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TAB 1 — All stores                                             ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab1:
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
# ║  TAB 2 — Pareto / top X%                                        ║
# ╚══════════════════════════════════════════════════════════════════╝
with tab2:
    top_rev = df.loc[top_lics, months].sum().sum()
    act_pct = pct_value(top_rev, grand)
    top_avg = df.loc[top_lics, months].sum().mean()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stores in Group", f"{len(top_lics)} of {len(all_lics)}")
    c2.metric("Revenue Share", f"{act_pct:.1f}%")
    c3.metric("Group Total", fmt_usd(top_rev))
    c4.metric("Avg Monthly", fmt_usd(top_avg))

    st.divider()

    # Pareto breakdown table
    st.subheader("Pareto breakdown")
    sorted_lics = all_totals.sort_values(ascending=False).index.tolist()
    cum = 0
    pareto_rows = []
    for i, lic in enumerate(sorted_lics, 1):
        tot = all_totals[lic]
        sp = pct_value(tot, grand)
        cum += sp
        pareto_rows.append({
            "#": i,
            "Store Name": df.loc[lic, "Store Name"],
            "License": lic,
            "Total": fmt_usd(tot),
            "Share": f"{sp:.1f}%",
            "Cumulative": f"{min(cum,100):.1f}%",
            "Group": "✅ IN" if lic in top_lics else "out",
        })
    pareto_df = pd.DataFrame(pareto_rows)

    def highlight_in(row):
        if "✅" in str(row["Group"]):
            return ["background-color:#e6f1fb; font-weight:600" if c in ["Store Name","Group","Cumulative"] else "" for c in pareto_df.columns]
        return ["color:#999"] * len(pareto_df.columns)

    st.dataframe(
        pareto_df.style.apply(highlight_in, axis=1),
        use_container_width=True, hide_index=True
    )

    remaining_pct = max(0, 100 - act_pct) if grand else 0.0
    st.caption(f"Remaining {len(all_lics)-len(top_lics)} store{'s' if len(all_lics)-len(top_lics)!=1 else ''} account for {remaining_pct:.1f}% of total revenue.")

    st.divider()

    # Share by store (top group only)
    col_left, col_right = st.columns([1,1])
    with col_left:
        st.subheader("Share by store")
        sel_month2 = st.selectbox("Month", months, index=len(months)-1, key="t2_month")
        sort_by2 = st.selectbox("Sort", SORT_OPTIONS, key="t2_sort")

        all_mt2 = df[sel_month2].sum()
        grp_mt2 = df.loc[top_lics, sel_month2].sum()
        share_df2 = df.loc[top_lics, ["Store Name", sel_month2]].copy()
        share_df2["% of Group"] = share_df2[sel_month2].apply(lambda v: pct_value(v, grp_mt2))
        share_df2["% of All"] = share_df2[sel_month2].apply(lambda v: pct_value(v, all_mt2))

        share_df2 = sort_share_rows(share_df2, sel_month2, sort_by2)

        st.caption(f"Group total for {sel_month2}: **{fmt_usd(grp_mt2)}** · {pct(grp_mt2, all_mt2)} of all stores")
        disp2 = share_df2.reset_index()[["Store Name","License",sel_month2,"% of Group","% of All"]].copy()
        disp2.columns = ["Store Name","License","Revenue","% of Group","% of All Stores"]
        disp2["Revenue"] = disp2["Revenue"].apply(fmt_usd)
        disp2["% of Group"] = disp2["% of Group"].apply(lambda x: f"{x:.1f}%")
        disp2["% of All Stores"] = disp2["% of All Stores"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(disp2, use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("Revenue share")
        if grp_mt2 > 0:
            fig_pie2 = px.pie(
                share_df2.reset_index(), values=sel_month2, names="Store Name",
                color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4
            )
            fig_pie2.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie2.update_layout(showlegend=False, margin=dict(t=10,b=10,l=10,r=10))
            st.plotly_chart(fig_pie2, use_container_width=True)
        else:
            st.info("No revenue for the selected month.")

    share_report2 = build_share_by_store_pdf(
        df, sel_month2, sort_by2,
        top_lics=top_lics, threshold=threshold,
        report_date=report_date
    )
    st.download_button(
        f"⬇ Download Share by Store Report — Top {int(threshold*100)}%",
        data=share_report2,
        file_name=f"share-by-store-top-{int(threshold*100)}pct-{slugify(sel_month2)}-{slugify(sort_by2)}.pdf",
        mime="application/pdf",
        key="t2_share_report"
    )

    st.divider()

    # Monthly totals — group vs all
    st.subheader("Monthly totals — group vs all stores")
    grp_m = df.loc[top_lics, months].sum()
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
    top_names = df.loc[top_lics, "Store Name"].tolist()
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
    pdf_buf2 = build_pdf(df, months, top_lics=top_lics, threshold=threshold, report_date=report_date)
    st.download_button(
        f"⬇ Download PDF Report — Top {int(threshold*100)}%",
        data=pdf_buf2,
        file_name=f"pareto-dashboard-{int(threshold*100)}pct.pdf",
        mime="application/pdf"
    )
