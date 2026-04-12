"""
Dash dashboard for Atlanta Assumable Mortgage Cash Flow Analysis.
Run with: python app.py
"""

import os
import sys

import pandas as pd
import dash
from dash import dcc, html, dash_table, Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash.dash_table.Format import Format, Group, Scheme
from dash.dash_table import FormatTemplate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from analysis.cashflow import analyze_portfolio, summary_stats
from project_paths import (
    LISTINGS_ENRICHED_PATH,
    ZILLOW_HISTORY_PATH,
    REALTYAPI_GRAPH_POINTS_PATH,
    REALTYAPI_GRAPH_SUMMARY_PATH,
)


def load_raw() -> pd.DataFrame:
    if os.path.exists(LISTINGS_ENRICHED_PATH):
        return pd.read_csv(LISTINGS_ENRICHED_PATH)
    return pd.DataFrame()


def load_history() -> pd.DataFrame:
    for p in [REALTYAPI_GRAPH_POINTS_PATH, ZILLOW_HISTORY_PATH]:
        if os.path.exists(p):
            return pd.read_csv(p)
    return pd.DataFrame()


def load_history_summary() -> pd.DataFrame:
    if os.path.exists(REALTYAPI_GRAPH_SUMMARY_PATH):
        return pd.read_csv(REALTYAPI_GRAPH_SUMMARY_PATH)
    return pd.DataFrame()


def enrich_raw_with_history_summary(raw_df: pd.DataFrame, summary_df: pd.DataFrame) -> pd.DataFrame:
    """Append current graph summary metrics, such as rent zestimate, onto listings."""
    if raw_df.empty or summary_df.empty:
        return raw_df

    rent_summary = summary_df[summary_df["_which"] == "rent_zestimate_history"].copy()
    if rent_summary.empty or "DataPoints_Current_Rent_Zestimate" not in rent_summary.columns:
        return raw_df

    rent_summary = rent_summary[["_input_address", "DataPoints_Current_Rent_Zestimate"]].drop_duplicates("_input_address")
    merged = raw_df.merge(
        rent_summary,
        left_on="address",
        right_on="_input_address",
        how="left",
        suffixes=("", "_summary"),
    )
    drop_cols = [column for column in ["_input_address_summary"] if column in merged.columns]
    if drop_cols:
        merged = merged.drop(columns=drop_cols)
    return merged


def normalize_raw_columns(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Create stable dashboard-facing columns from multiple source fields."""
    if raw_df.empty:
        return raw_df

    raw_df = raw_df.copy()
    if "zillow_link" not in raw_df.columns:
        raw_df["zillow_link"] = raw_df.get("PropertyZillowURL")
        if "zillow_url" in raw_df.columns:
            raw_df["zillow_link"] = raw_df["zillow_link"].fillna(raw_df["zillow_url"])

    current_rent = pd.to_numeric(raw_df.get("DataPoints_Current_Rent_Zestimate"), errors="coerce")
    if "rent_estimate" in raw_df.columns:
        raw_df["rent_estimate_original"] = raw_df["rent_estimate"]
        raw_df["rent_estimate"] = current_rent.fillna(pd.to_numeric(raw_df["rent_estimate"], errors="coerce"))
    else:
        raw_df["rent_estimate"] = current_rent

    if "rent_source" not in raw_df.columns:
        raw_df["rent_source"] = None
    has_realtyapi_rent = current_rent.notna()
    raw_df.loc[has_realtyapi_rent, "rent_source"] = "realtyapi_graph_current_rent_zestimate"
    return raw_df


HISTORY_DF = load_history()
HISTORY_SUMMARY_DF = load_history_summary()
RAW_DF = normalize_raw_columns(enrich_raw_with_history_summary(load_raw(), HISTORY_SUMMARY_DF))

# ── Column display config ──────────────────────────────────────────────────────
COL_RENAME = {
    "address":                    "地址 / Address",
    "zip":                        "邮编 / Zip",
    "beds":                       "卧室 / Beds",
    "baths":                      "浴室 / Baths",
    "sqft":                       "面积 / Sqft",
    "yearBuilt":                  "建造年份 / Year Built",
    "price":                      "售价 / Price",
    "zestimate":                  "Zillow估值 / Zestimate",
    "DataPoints_Current_Rent_Zestimate": "当前租金估值 / Current Rent Zestimate",
    "daysOnZillow":               "上市天数 / Days Listed",
    "loan_balance":               "贷款余额 / Loan Balance",
    "assumable_rate_pct":         "可承接利率 / Rate%",
    "loan_type":                  "贷款类型 / Type",
    "remaining_years":            "剩余年限 / Yrs",
    "equity_needed":              "所需首付 / Equity",
    "monthly_payment":            "月还款 / P&I",
    "monthly_loan_insurance":     "贷款保险 / Loan Insurance",
    "gross_rent":                 "预估租金 / Rent",
    "monthly_cashflow":           "月现金流 / Monthly CF",
    "annual_cashflow":            "年现金流 / Annual CF",
    "cash_on_cash_pct":           "现金回报率 / CoC%",
    "cap_rate_pct":               "资本化率 / Cap%",
    "breakeven_rent":             "平衡租金 / Breakeven",
    "monthly_rate_savings":       "利率节省 / Rate Savings",
    "rent_source":                "租金来源 / Rent Source",
    "url":                        "Roam链接 / Roam URL",
    "zillow_link":                "Zillow链接 / Zillow URL",
}

DISPLAY_ORDER = list(COL_RENAME.keys())

DOLLAR_COLS = {"price", "zestimate", "DataPoints_Current_Rent_Zestimate", "loan_balance", "equity_needed", "monthly_payment",
               "gross_rent", "monthly_cashflow", "annual_cashflow",
               "breakeven_rent", "monthly_rate_savings"}
PCT_COLS    = {"assumable_rate_pct", "cash_on_cash_pct", "cap_rate_pct"}
LINK_COLS = {"url", "zillow_link"}


def build_table_columns(cols_present: list[str]) -> list[dict]:
    """Build DataTable column config while preserving numeric sorting."""
    columns = []
    for col in cols_present:
        name = COL_RENAME.get(col, col)
        if col in LINK_COLS:
            columns.append({"name": name, "id": name, "presentation": "markdown"})
        elif col in DOLLAR_COLS:
            columns.append({
                "name": name,
                "id": name,
                "type": "numeric",
                "format": FormatTemplate.money(0),
            })
        elif col in PCT_COLS:
            columns.append({
                "name": name,
                "id": name,
                "type": "numeric",
                "format": Format(precision=2, scheme=Scheme.fixed),
            })
        else:
            columns.append({"name": name, "id": name})
    return columns


def get_property_history(row: pd.Series) -> pd.DataFrame:
    hist = HISTORY_DF.copy()
    required_match_cols = {"_input_address", "_input_url", "_input_zpid", "listing_url", "zillow_url", "zpid"}
    if hist.empty or not any(col in hist.columns for col in required_match_cols):
        return hist

    masks = []
    address = row.get("address")
    listing_url = row.get("url")
    zillow_url = row.get("PropertyZillowURL") or row.get("zillow_url")
    zpid = row.get("PropertyZPID") or row.get("zpid")

    if address and "_input_address" in hist.columns:
        masks.append(hist["_input_address"] == address)
    if zillow_url and "_input_url" in hist.columns:
        masks.append(hist["_input_url"] == zillow_url)
    if pd.notna(zpid) and "_input_zpid" in hist.columns:
        masks.append(hist["_input_zpid"].astype(str) == str(int(zpid) if isinstance(zpid, float) else zpid))
    if listing_url and "listing_url" in hist.columns:
        masks.append(hist["listing_url"] == listing_url)
    if zillow_url and "zillow_url" in hist.columns:
        masks.append(hist["zillow_url"] == zillow_url)
    if pd.notna(zpid) and "zpid" in hist.columns:
        masks.append(hist["zpid"].astype(str) == str(int(zpid) if isinstance(zpid, float) else zpid))

    if not masks:
        return pd.DataFrame()

    mask = masks[0]
    for extra in masks[1:]:
        mask = mask | extra

    hist = hist[mask].copy()
    if hist.empty:
        return hist

    if "_which" in hist.columns:
        hist["history_type"] = hist["_which"].replace({
            "rent_zestimate_history": "rent",
            "zestimate_history": "sale",
        })
    if "y" in hist.columns:
        hist["point_value"] = pd.to_numeric(hist["y"], errors="coerce")
    if "x" in hist.columns:
        hist["point_date_parsed"] = pd.to_datetime(pd.to_numeric(hist["x"], errors="coerce"), unit="ms", errors="coerce")
        hist["point_date"] = hist["point_date_parsed"].dt.strftime("%Y-%m-%d")
    elif "point_date" in hist.columns:
        hist["point_date_parsed"] = pd.to_datetime(hist["point_date"], errors="coerce")
    else:
        hist["point_date_parsed"] = pd.NaT
    if "_series_index" in hist.columns:
        hist["point_index"] = pd.to_numeric(hist["_series_index"], errors="coerce")
    elif "point_index" not in hist.columns:
        hist["point_index"] = range(len(hist))
    hist = hist.sort_values(
        by=["point_date_parsed", "point_index"],
        ascending=[True, True],
        na_position="last",
    )
    return hist


def get_property_history_summary(row: pd.Series) -> pd.DataFrame:
    hist = HISTORY_SUMMARY_DF.copy()
    if hist.empty:
        return hist

    masks = []
    address = row.get("address")
    zillow_url = row.get("PropertyZillowURL") or row.get("zillow_url")
    zpid = row.get("PropertyZPID") or row.get("zpid")

    if address and "_input_address" in hist.columns:
        masks.append(hist["_input_address"] == address)
    if zillow_url and "_input_url" in hist.columns:
        masks.append(hist["_input_url"] == zillow_url)
    if pd.notna(zpid) and "_input_zpid" in hist.columns:
        masks.append(hist["_input_zpid"].astype(str) == str(int(zpid) if isinstance(zpid, float) else zpid))

    if not masks:
        return pd.DataFrame()

    mask = masks[0]
    for extra in masks[1:]:
        mask = mask | extra

    return hist[mask].copy()


def build_history_figure(hist: pd.DataFrame, history_type: str, title: str, color: str):
    fig = go.Figure()
    required_cols = {"history_type", "point_value"}
    if hist.empty or not required_cols.issubset(hist.columns):
        subset = pd.DataFrame()
    else:
        subset = hist[hist["history_type"] == history_type].copy()
        subset = subset[subset["point_value"].notna()].copy()
    if subset.empty:
        fig.update_layout(
            title=title,
            height=280,
            margin=dict(l=20, r=20, t=50, b=20),
            template="plotly_white",
            annotations=[
                dict(
                    text="No history saved yet",
                    x=0.5,
                    y=0.5,
                    xref="paper",
                    yref="paper",
                    showarrow=False,
                    font=dict(size=14, color="#7f8c8d"),
                )
            ],
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig

    x_values = subset["point_date_parsed"] if "point_date_parsed" in subset.columns else subset["point_index"]
    hover_dates = subset["point_date"].fillna("Unknown date")

    fig.add_trace(go.Scatter(
        x=x_values,
        y=subset["point_value"],
        mode="lines+markers",
        line=dict(color=color, width=3),
        marker=dict(size=7),
        customdata=hover_dates,
        hovertemplate="%{customdata}<br>$%{y:,.0f}<extra></extra>",
        name=title,
    ))
    fig.update_layout(
        title=title,
        height=280,
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    fig.update_xaxes(title=None)
    return fig

# ── App ────────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="Atlanta 可承接房贷 / Assumable Mortgage",
)

# ── Sidebar ────────────────────────────────────────────────────────────────────
sidebar = html.Div([
    dbc.Card([
        dbc.CardHeader(html.B("分析参数 / Assumptions")),
        dbc.CardBody([
            html.Label("空置率 / Vacancy Rate", className="fw-semibold mt-1"),
            dcc.Slider(id="vacancy", min=0, max=20, step=1, value=5,
                       marks={0:"0%", 5:"5%", 10:"10%", 20:"20%"},
                       tooltip={"placement":"bottom","always_visible":True},
                       className="mb-3"),

        html.Label("维护预算 / Maintenance Reserve (%/yr)", className="fw-semibold"),
            dcc.Slider(id="maintenance", min=0.5, max=2.0, step=0.1, value=1.0,
                       marks={0.5:"0.5%", 1.0:"1%", 2.0:"2%"},
                       tooltip={"placement":"bottom","always_visible":True},
                       className="mb-3"),

            html.Label("资本支出储备 / CapEx ($/mo)", className="fw-semibold"),
            dbc.Input(id="capex", type="number", value=100, min=0, max=500, step=25,
                      className="mb-3"),

            dbc.Checklist(id="use-pm",
                          options=[{"label": "使用物业管理 (8%) / Use Property Manager (8%)",
                                    "value": "yes"}],
                          value=[], className="mb-3"),

            html.Label("当前市场利率 / Current 30yr Rate (%)", className="fw-semibold"),
            dbc.Input(id="current-rate", type="number", value=7.0, min=5.0, max=10.0, step=0.1,
                      className="mb-0"),
        ]),
    ], className="shadow-sm mb-3"),

    dbc.Card([
        dbc.CardHeader(html.B("筛选条件 / Filters")),
        dbc.CardBody([
            html.Label("最大首付 / Max Equity Needed ($)", className="fw-semibold mt-1"),
            dbc.Input(id="max-equity", type="number", value=200000, min=0, max=500000, step=10000,
                      className="mb-3"),

            html.Label("最低月现金流 / Min Monthly CF ($)", className="fw-semibold"),
            dbc.Input(id="min-cf", type="number", value=-500, min=-2000, max=5000, step=100,
                      className="mb-3"),

            html.Label("贷款类型 / Loan Types", className="fw-semibold"),
            dcc.Dropdown(id="loan-types",
                         options=[{"label": t, "value": t} for t in ["VA","FHA","CONVENTIONAL","USDA"]],
                         value=["VA","FHA","CONVENTIONAL","USDA"],
                         multi=True, className="mb-3"),

            html.Label("最少卧室数 / Min Bedrooms", className="fw-semibold"),
            dcc.Dropdown(id="min-beds",
                         options=[{"label": str(n), "value": n} for n in [1,2,3,4]],
                         value=1, clearable=False, className="mb-0"),
        ]),
    ], className="shadow-sm"),
], className="h-100")

# ── Metric card helper ─────────────────────────────────────────────────────────
def metric_card(card_id, label):
    return dbc.Card([
        dbc.CardBody([
            html.P(label, className="text-muted small mb-1"),
            html.H4(id=card_id, className="mb-0 fw-bold"),
        ])
    ], className="shadow-sm text-center")


def assumption_badge():
    return html.Span(
        "Assumption",
        className="ms-2 px-2 py-1 rounded small fw-semibold",
        style={"backgroundColor": "#fff3bf", "color": "#7a5d00"},
    )

# ── Layout ─────────────────────────────────────────────────────────────────────
app.layout = dbc.Container([
    # Header
    dbc.Row([
        dbc.Col([
            html.H2("🏠 Atlanta 可承接房贷现金流分析", className="mb-0"),
            html.H5("Atlanta Assumable Mortgage — Cash Flow Dashboard",
                    className="text-muted mb-0"),
            html.Small("数据来源 Withroam.com · 租金估算来自 Zillow / "
                       "Data from Withroam.com · Rent estimates from Zillow",
                       className="text-secondary"),
        ])
    ], className="py-3 border-bottom mb-3"),

    dbc.Row([
        # Sidebar
        dbc.Col(sidebar, width=3),

        # Main content
        dbc.Col([
            # Summary cards
            dbc.Row([
                dbc.Col(metric_card("card-total",    "总房源 / Total Listings"),    width=2),
                dbc.Col(metric_card("card-pos",      "正现金流 / Positive CF"),     width=2),
                dbc.Col(metric_card("card-avg-cf",   "平均月现金流 / Avg Monthly CF"), width=3),
                dbc.Col(metric_card("card-avg-eq",   "平均首付 / Avg Equity"),      width=3),
                dbc.Col(metric_card("card-avg-coc",  "现金回报率 / Avg CoC%"),      width=2),
            ], className="mb-3 g-2"),

            # Table
            html.H5(id="table-title", className="mt-2 mb-1"),
            dash_table.DataTable(
                id="main-table",
                sort_action="native",
                filter_action="native",
                page_action="native",
                page_size=20,
                style_table={"overflowX": "auto"},
                style_header={
                    "backgroundColor": "#2c3e50",
                    "color": "white",
                    "fontWeight": "bold",
                    "fontSize": "12px",
                    "whiteSpace": "normal",
                    "height": "auto",
                },
                style_cell={
                    "fontSize": "12px",
                    "padding": "6px 10px",
                    "whiteSpace": "nowrap",
                    "overflow": "hidden",
                    "textOverflow": "ellipsis",
                    "maxWidth": "200px",
                },
                style_data_conditional=[
                    {"if": {"row_index": "odd"},
                     "backgroundColor": "#f8f9fa"},
                ],
                tooltip_delay=0,
                tooltip_duration=None,
            ),

            # Property detail
            html.Hr(),
            html.H5("房源详情 / Property Detail", className="mb-2"),
            html.Div(id="property-detail"),

            # Download
            html.Hr(),
            dbc.Button("下载筛选结果 CSV / Download CSV",
                       id="download-btn", color="primary", outline=True, size="sm"),
            dcc.Download(id="download"),
        ], width=9),
    ]),
], fluid=True)


# ── Shared filter logic ────────────────────────────────────────────────────────
def apply_filters(vacancy, maintenance, capex, use_pm, max_equity, min_cf,
                  loan_types, min_beds):
    if RAW_DF.empty:
        return pd.DataFrame(), {}

    assumptions = {
        "vacancy_rate": (5 if vacancy is None else vacancy) / 100,
        "maintenance_pct": (1.0 if maintenance is None else maintenance) / 100,
        "capex_monthly": 100 if capex is None else capex,
        "use_property_manager": bool(use_pm),
    }
    df = analyze_portfolio(RAW_DF, assumptions)

    if "equity_needed" in df.columns:
        df = df[df["equity_needed"].fillna(999999) <= (999999 if max_equity is None else max_equity)]
    if "monthly_cashflow" in df.columns:
        df = df[df["monthly_cashflow"].fillna(-9999) >= (-9999 if min_cf is None else min_cf)]
    if "loan_type" in df.columns and loan_types:
        df = df[df["loan_type"].str.upper().isin(loan_types) | df["loan_type"].isna()]
    if "beds" in df.columns and min_beds:
        df = df[df["beds"].fillna(0) >= min_beds]

    df = df.sort_values("monthly_cashflow", ascending=False)
    stats = summary_stats(df)
    return df, stats


# ── Callback: update table + cards ────────────────────────────────────────────
@app.callback(
    Output("card-total",   "children"),
    Output("card-pos",     "children"),
    Output("card-avg-cf",  "children"),
    Output("card-avg-eq",  "children"),
    Output("card-avg-coc", "children"),
    Output("table-title",  "children"),
    Output("main-table",   "columns"),
    Output("main-table",   "data"),
    Output("main-table",   "style_data_conditional"),
    Input("vacancy",       "value"),
    Input("maintenance",   "value"),
    Input("capex",         "value"),
    Input("use-pm",        "value"),
    Input("max-equity",    "value"),
    Input("min-cf",        "value"),
    Input("loan-types",    "value"),
    Input("min-beds",      "value"),
)
def update_table(vacancy, maintenance, capex, use_pm, max_equity, min_cf, loan_types, min_beds):
    df, stats = apply_filters(vacancy, maintenance, capex, use_pm,
                              max_equity, min_cf, loan_types, min_beds)

    # Cards
    total   = len(RAW_DF)
    filtered_total = stats.get("total_listings", 0)
    pos     = stats.get("cashflow_positive", 0)
    avg_cf  = stats.get("avg_monthly_cashflow")
    avg_eq  = stats.get("avg_equity_needed")
    avg_coc = stats.get("avg_coc_return")

    card_total   = str(total)
    card_pos     = f"{pos}  ({pos/filtered_total*100:.0f}%)" if filtered_total else "0"
    card_avg_cf  = f"${avg_cf:+,}" if avg_cf is not None else "—"
    card_avg_eq  = f"${avg_eq:,.0f}" if avg_eq is not None else "—"
    card_avg_coc = f"{avg_coc:.1f}%" if avg_coc is not None else "—"

    title = f"房源列表 / Listings（筛选后 {len(df)} / 总计 {total}）"

    if df.empty:
        return card_total, card_pos, card_avg_cf, card_avg_eq, card_avg_coc, title, [], [], []

    # Build display df
    cols_present = [c for c in DISPLAY_ORDER if c in df.columns]
    disp = df[cols_present].copy()

    for col in cols_present:
        if col in DOLLAR_COLS or col in PCT_COLS:
            disp[col] = pd.to_numeric(disp[col], errors="coerce")

    disp = disp.rename(columns={k: v for k, v in COL_RENAME.items() if k in disp.columns})
    columns = build_table_columns(cols_present)

    # Linkify URL column
    for source_col in ["url", "zillow_link"]:
        link_col = COL_RENAME.get(source_col)
        if link_col not in disp.columns:
            continue
        disp[link_col] = disp[link_col].apply(
            lambda v: f"[链接/Link]({v})" if v and v != "—" else "—"
        )

    # Conditional formatting
    cf_col = COL_RENAME.get("monthly_cashflow")
    cond = [{"if": {"row_index": "odd"}, "backgroundColor": "#f8f9fa"}]

    if cf_col in disp.columns:
        cond += [
            {"if": {"filter_query": f'{{{cf_col}}} > 0',
                    "column_id": cf_col},
             "color": "#27ae60", "fontWeight": "bold"},
            {"if": {"filter_query": f'{{{cf_col}}} < 0',
                    "column_id": cf_col},
             "color": "#e74c3c", "fontWeight": "bold"},
        ]

    return (card_total, card_pos, card_avg_cf, card_avg_eq, card_avg_coc,
            title, columns, disp.to_dict("records"), cond)


# ── Callback: property detail ──────────────────────────────────────────────────
@app.callback(
    Output("property-detail", "children"),
    Input("main-table", "active_cell"),
    Input("main-table", "data"),
    State("vacancy",    "value"),
    State("maintenance","value"),
    State("capex",      "value"),
    State("use-pm",     "value"),
    State("max-equity", "value"),
    State("min-cf",     "value"),
    State("loan-types", "value"),
    State("min-beds",   "value"),
    State("current-rate","value"),
)
def update_detail(active_cell, table_data, vacancy, maintenance, capex, use_pm,
                  max_equity, min_cf, loan_types, min_beds, current_rate):
    if not active_cell or not table_data:
        return html.P("点击表格中的任意行查看详情 / Click any row in the table to see details.",
                      className="text-muted")

    row_idx = active_cell["row"]
    if row_idx >= len(table_data):
        return ""

    # Re-derive full row from filtered df (display df has formatted strings)
    df, _ = apply_filters(vacancy, maintenance, capex, use_pm,
                          max_equity, min_cf, loan_types, min_beds)
    if df.empty or row_idx >= len(df):
        return ""

    row = df.iloc[row_idx]

    def fmt_dollar(v):
        return f"${v:,.0f}" if pd.notna(v) else "—"

    def fmt_pct(v):
        return f"{v:.2f}%" if pd.notna(v) else "—"

    neighborhood = row.get("PropertyAddress_neighborhood")
    zillow_url = row.get("PropertyZillowURL") or row.get("zillow_url")
    zestimate = row.get("zestimate")
    year_built = row.get("yearBuilt")
    days_listed = row.get("daysOnZillow")
    effective_remaining_years = row.get("effective_remaining_years")
    estimated_remaining_years = row.get("estimated_remaining_years")
    remaining_years_source = row.get("remaining_years_source")
    history_summary_df = get_property_history_summary(row)
    rent_summary = history_summary_df[history_summary_df["_which"] == "rent_zestimate_history"].head(1)
    current_rent_zestimate = (
        rent_summary["DataPoints_Current_Rent_Zestimate"].iloc[0]
        if not rent_summary.empty and "DataPoints_Current_Rent_Zestimate" in rent_summary.columns
        else row.get("DataPoints_Current_Rent_Zestimate")
    )

    info_col = dbc.Col([
        html.H6("房产信息 / Property Info", className="text-primary fw-bold"),
        html.P([html.B("地址 / Address: "), str(row.get("address", "—"))]),
        *([html.P([html.B("社区 / Neighborhood: "), str(neighborhood)])]
          if pd.notna(neighborhood) and neighborhood else []),
        html.P([html.B("邮编 / Zip: "), str(row.get("zip", "—"))]),
        html.P([html.B("卧室/浴室 / Beds/Baths: "),
                f"{row.get('beds','—')} / {row.get('baths','—')}"]),
        html.P([html.B("面积 / Sqft: "),
                f"{row.get('sqft',0):,.0f}" if pd.notna(row.get("sqft")) else "—"]),
        *([html.P([html.B("建造年份 / Year Built: "), str(int(year_built))])]
          if pd.notna(year_built) and year_built else []),
        *([html.P([html.B("上市天数 / Days Listed: "), str(int(days_listed))])]
          if pd.notna(days_listed) and days_listed else []),
        html.P([html.B("售价 / Price: "), fmt_dollar(row.get("price"))]),
        *([html.P([html.B("Zillow估值 / Zestimate: "), fmt_dollar(zestimate)])]
          if pd.notna(zestimate) and zestimate else []),
        *([html.P([html.B("当前租金估值 / Current Rent Zestimate: "), fmt_dollar(current_rent_zestimate)])]
          if pd.notna(current_rent_zestimate) and current_rent_zestimate else []),
        html.Div([
            html.Div(
                html.A("在 Withroam 查看 / View on Withroam", href=row.get("url", "#"),
                       target="_blank")
            ) if row.get("url") else "",
            html.Div(
                html.A("在 Zillow 查看 / View on Zillow", href=zillow_url,
                       target="_blank"),
                className="mt-1",
            ) if zillow_url else "",
        ]),
    ], width=4)

    mortgage_col = dbc.Col([
        html.H6("贷款详情 / Mortgage Details", className="text-primary fw-bold"),
        html.P([html.B("贷款类型 / Loan Type: "), str(row.get("loan_type", "—"))]),
        html.P([html.B("可承接利率 / Assumable Rate: "), fmt_pct(row.get("assumable_rate_pct"))]),
        html.P([html.B("贷款余额 / Loan Balance: "), fmt_dollar(row.get("loan_balance"))]),
        html.P([
            html.B("剩余年限 / Remaining Term: "),
            (
                f"{row.get('remaining_years'):,.0f} 年/years"
                if pd.notna(row.get("remaining_years")) else
                f"{effective_remaining_years:,.1f} 年/years"
                if pd.notna(effective_remaining_years) else
                "—"
            ),
            assumption_badge() if remaining_years_source == "estimated_from_balance_payment_rate" else "",
        ]),
        html.P([html.B("月还款(本息) / Monthly P&I: "), fmt_dollar(row.get("monthly_payment"))]),
        html.P([html.B("所需首付 / Equity Needed: "),
                html.Span(fmt_dollar(row.get("equity_needed")),
                          className="fw-bold text-danger")]),
        html.P([html.B(f"vs 市场利率({current_rate}%)月节省 / Rate Savings/mo: "),
                fmt_dollar(row.get("monthly_rate_savings")),
                assumption_badge() if remaining_years_source == "estimated_from_balance_payment_rate" else ""])
        if (pd.notna(row.get("monthly_rate_savings")) and
                (row.get("monthly_rate_savings") or 0) > 0) else "",
    ], width=4)

    cf = row.get("monthly_cashflow", 0) or 0
    cf_color = "success" if cf > 0 else "danger"
    expenses_items = [
        ("本息还款 / P&I",       row.get("pni_payment", 0) or 0),
        ("房产税 / Property Tax", row.get("monthly_tax", 0) or 0),
        ("保险 / Insurance",     row.get("monthly_insurance", 0) or 0),
        ("贷款保险 / Loan Insurance", row.get("monthly_loan_insurance", 0) or 0),
        ("HOA管理费 / HOA",      row.get("monthly_hoa", 0) or 0),
    ]

    cashflow_col = dbc.Col([
        html.H6("现金流明细 / Cash Flow Breakdown", className="text-primary fw-bold"),
        html.P([html.B("预估租金 / Gross Rent: "),
                f"${row.get('gross_rent',0):,.0f}/月(mo)"]),
        html.P([html.B("空置损失 / Vacancy: "),
                f"-${row.get('vacancy_loss',0):,.0f}/月(mo)",
                assumption_badge()]),
        html.Hr(className="my-1"),
        *[html.P(f"{name}: -${val:,.0f}/月(mo)", className="mb-1")
          for name, val in expenses_items if val],
        *([html.P([
            "物业管理 / Management Fee: ",
            f"-${row.get('mgmt_fee',0):,.0f}/月(mo)",
            assumption_badge(),
        ], className="mb-1")]
          if (row.get("mgmt_fee", 0) or 0) else []),
        html.P([
            "维护预算 / Maintenance Reserve: ",
            f"-${row.get('maintenance',0):,.0f}/月(mo)",
            assumption_badge(),
        ], className="mb-1") if (row.get("maintenance", 0) or 0) else "",
        html.P([
            "资本支出 / CapEx: ",
            f"-${row.get('capex',0):,.0f}/月(mo)",
            assumption_badge(),
        ], className="mb-1") if row.get("capex") is not None else "",
        html.Hr(className="my-1"),
        dbc.Alert([
            html.B("月净现金流 / Net Monthly CF: "),
            f"${cf:+,.0f}/月(mo)"
        ], color=cf_color, className="py-1 mb-1"),
        html.P([html.B("年净现金流 / Annual CF: "),
                f"${row.get('annual_cashflow',0):+,.0f}"]),
        html.P([html.B("现金回报率 / Cash-on-Cash: "),
                fmt_pct(row.get("cash_on_cash_pct"))]),
        html.P([html.B("平衡租金 / Breakeven Rent: "),
                fmt_dollar(row.get("breakeven_rent")) + "/月(mo)",
                assumption_badge()]),
        html.P([html.B("租金来源 / Rent Source: "), str(row.get("rent_source","—"))]),
    ], width=4)

    history_df = get_property_history(row)
    history_empty = (
        history_df.empty or
        "point_value" not in history_df.columns or
        history_df["point_value"].notna().sum() == 0
    )
    rent_fig = build_history_figure(history_df, "rent", "Rent Estimate History", "#16a085")
    sale_fig = build_history_figure(history_df, "sale", "Sale / Zestimate History", "#2c3e50")

    history_section = dbc.Card([
        dbc.CardBody([
            html.H6("租金与估值历史 / Rent And Valuation History", className="text-primary fw-bold mb-3"),
            html.P(
                "No saved history for this listing yet. Run step 2 enrichment to populate it."
                if history_empty else
                "Saved history is shown below when available.",
                className="text-muted mb-3",
            ),
            dbc.Row([
                dbc.Col(dcc.Graph(figure=rent_fig, config={"displayModeBar": False}), width=6),
                dbc.Col(dcc.Graph(figure=sale_fig, config={"displayModeBar": False}), width=6),
            ], className="g-2"),
        ])
    ], className="shadow-sm mt-3")

    return html.Div([
        dbc.Row([info_col, mortgage_col, cashflow_col]),
        history_section,
    ])


# ── Callback: download ─────────────────────────────────────────────────────────
@app.callback(
    Output("download", "data"),
    Input("download-btn", "n_clicks"),
    State("vacancy",    "value"),
    State("maintenance","value"),
    State("capex",      "value"),
    State("use-pm",     "value"),
    State("max-equity", "value"),
    State("min-cf",     "value"),
    State("loan-types", "value"),
    State("min-beds",   "value"),
    prevent_initial_call=True,
)
def download_csv(__, vacancy, maintenance, capex, use_pm,
                 max_equity, min_cf, loan_types, min_beds):
    df, _ = apply_filters(vacancy, maintenance, capex, use_pm,
                          max_equity, min_cf, loan_types, min_beds)
    return dcc.send_data_frame(df.to_csv, "atlanta_listings.csv", index=False)


if __name__ == "__main__":
    app.run(debug=True, port=8050)
