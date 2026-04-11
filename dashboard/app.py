"""
Streamlit dashboard for Atlanta Assumable Mortgage Cash Flow Analysis.
Run with: streamlit run dashboard/app.py
"""

import streamlit as st
import pandas as pd
import sys
import os

# Allow imports from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.cashflow import analyze_portfolio, summary_stats, DEFAULTS

st.set_page_config(
    page_title="Atlanta Assumable Mortgage Analyzer",
    page_icon="🏠",
    layout="wide",
)

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "listings_enriched.csv")
FALLBACK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "listings_test.csv")


@st.cache_data(ttl=300)
def load_data(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


# --- Sidebar: Assumptions ---
st.sidebar.header("Analysis Assumptions")

vacancy_rate = st.sidebar.slider("Vacancy Rate", 0.0, 0.20, 0.05, 0.01, format="%.0f%%",
                                  help="% of time property is vacant") / 100 * 100
vacancy_rate = vacancy_rate / 100

maintenance_pct = st.sidebar.slider("Maintenance (% of price/yr)", 0.5, 2.0, 1.0, 0.1) / 100
use_pm = st.sidebar.checkbox("Use Property Manager (8%)", value=False)
capex = st.sidebar.number_input("CapEx Reserve ($/mo)", 0, 500, 100, 25)
current_rate = st.sidebar.number_input("Current 30yr Rate (%)", 5.0, 10.0, 7.0, 0.1)

st.sidebar.divider()
st.sidebar.header("Filters")
max_equity = st.sidebar.number_input("Max Equity Needed ($)", 0, 500000, 200000, 10000)
min_cashflow = st.sidebar.number_input("Min Monthly Cashflow ($)", -2000, 5000, -500, 100)
loan_types = st.sidebar.multiselect("Loan Types", ["VA", "FHA", "CONVENTIONAL", "USDA"],
                                     default=["VA", "FHA", "CONVENTIONAL", "USDA"])
min_beds = st.sidebar.selectbox("Min Bedrooms", [1, 2, 3, 4], index=0)

assumptions = {
    "vacancy_rate": vacancy_rate,
    "maintenance_pct": maintenance_pct,
    "use_property_manager": use_pm,
    "capex_monthly": capex,
}

# --- Main ---
st.title("Atlanta Assumable Mortgage — Cash Flow Dashboard")
st.caption("Data from Withroam.com · Rent estimates from Zillow")

# Load data
data_path = DATA_PATH if os.path.exists(DATA_PATH) else (FALLBACK_PATH if os.path.exists(FALLBACK_PATH) else None)

if data_path is None:
    st.warning("No data found. Run the scraper first:")
    st.code("python scraper/withroam_scraper.py 30327", language="bash")
    st.stop()

raw_df = load_data(data_path)
df = analyze_portfolio(raw_df, assumptions)

# Apply filters
filtered = df.copy()

if "equity_needed" in filtered.columns:
    filtered = filtered[filtered["equity_needed"].fillna(999999) <= max_equity]
if "monthly_cashflow" in filtered.columns:
    filtered = filtered[filtered["monthly_cashflow"].fillna(-9999) >= min_cashflow]
if "loan_type" in filtered.columns and loan_types:
    filtered = filtered[filtered["loan_type"].str.upper().isin(loan_types) | filtered["loan_type"].isna()]
if "beds" in filtered.columns:
    filtered = filtered[filtered["beds"].fillna(0) >= min_beds]

# --- Summary Cards ---
stats = summary_stats(filtered)
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Total Listings", stats.get("total_listings", 0))
with col2:
    pos = stats.get("cashflow_positive", 0)
    total = stats.get("total_listings", 1)
    st.metric("Positive Cash Flow", pos, delta=f"{pos/total*100:.0f}%" if total else "N/A")
with col3:
    avg_cf = stats.get("avg_monthly_cashflow")
    st.metric("Avg Monthly CF", f"${avg_cf:+,}" if avg_cf else "N/A",
              delta="positive" if avg_cf and avg_cf > 0 else "negative",
              delta_color="normal")
with col4:
    avg_eq = stats.get("avg_equity_needed")
    st.metric("Avg Equity Needed", f"${avg_eq:,.0f}" if avg_eq else "N/A")
with col5:
    avg_coc = stats.get("avg_coc_return")
    st.metric("Avg CoC Return", f"{avg_coc:.1f}%" if avg_coc else "N/A")

st.divider()

# --- Main Table ---
st.subheader(f"Listings ({len(filtered)} properties)")

display_cols = [
    "address", "zip", "beds", "baths", "sqft",
    "price", "loan_balance", "assumable_rate_pct", "loan_type", "remaining_years",
    "equity_needed", "monthly_payment",
    "gross_rent", "monthly_cashflow", "annual_cashflow",
    "cash_on_cash_pct", "cap_rate_pct",
    "breakeven_rent", "monthly_rate_savings",
    "rent_source", "url",
]
display_cols = [c for c in display_cols if c in filtered.columns]

display_df = filtered[display_cols].sort_values("monthly_cashflow", ascending=False)

# Color formatting
def color_cashflow(val):
    if pd.isna(val):
        return ""
    color = "#27ae60" if val > 0 else "#e74c3c"
    return f"color: {color}; font-weight: bold"

def color_coc(val):
    if pd.isna(val):
        return ""
    if val > 8:
        return "color: #27ae60; font-weight: bold"
    elif val > 4:
        return "color: #f39c12"
    else:
        return "color: #e74c3c"

styled = display_df.style

col_formats = {}
for col in ["price", "loan_balance", "equity_needed", "monthly_payment",
            "gross_rent", "monthly_cashflow", "annual_cashflow",
            "breakeven_rent", "monthly_rate_savings"]:
    if col in display_df.columns:
        col_formats[col] = "${:,.0f}"

for col in ["assumable_rate_pct", "cash_on_cash_pct", "cap_rate_pct"]:
    if col in display_df.columns:
        col_formats[col] = "{:.2f}%"

styled = styled.format(col_formats, na_rep="—")

if "monthly_cashflow" in display_df.columns:
    styled = styled.applymap(color_cashflow, subset=["monthly_cashflow"])
if "cash_on_cash_pct" in display_df.columns:
    styled = styled.applymap(color_coc, subset=["cash_on_cash_pct"])

st.dataframe(styled, use_container_width=True, height=500)

# --- Property Detail ---
st.divider()
st.subheader("Property Detail")

if len(filtered) > 0:
    addresses = filtered["address"].fillna("Unknown").tolist() if "address" in filtered.columns else list(range(len(filtered)))
    selected = st.selectbox("Select a property", addresses)
    row = filtered[filtered["address"] == selected].iloc[0] if "address" in filtered.columns else filtered.iloc[0]

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.markdown("**Property Info**")
        st.write(f"Address: {row.get('address', '—')}")
        st.write(f"Zip: {row.get('zip', '—')}")
        st.write(f"Beds/Baths: {row.get('beds', '—')} / {row.get('baths', '—')}")
        st.write(f"Sqft: {row.get('sqft', '—'):,.0f}" if pd.notna(row.get('sqft')) else "Sqft: —")
        st.write(f"List Price: ${row.get('price', 0):,.0f}" if pd.notna(row.get('price')) else "Price: —")
        if row.get("url"):
            st.markdown(f"[View on Withroam]({row['url']})")

    with col_b:
        st.markdown("**Mortgage Details**")
        st.write(f"Loan Type: {row.get('loan_type', '—')}")
        st.write(f"Assumable Rate: {row.get('assumable_rate_pct', '—')}%")
        st.write(f"Loan Balance: ${row.get('loan_balance', 0):,.0f}" if pd.notna(row.get('loan_balance')) else "Balance: —")
        st.write(f"Remaining Term: {row.get('remaining_years', '—')} years")
        st.write(f"Monthly P&I: ${row.get('monthly_payment', 0):,.0f}" if pd.notna(row.get('monthly_payment')) else "Monthly P&I: —")
        st.write(f"**Equity Needed: ${row.get('equity_needed', 0):,.0f}**" if pd.notna(row.get('equity_needed')) else "Equity Needed: —")
        if pd.notna(row.get('monthly_rate_savings')) and row.get('monthly_rate_savings', 0) > 0:
            st.write(f"Monthly savings vs {current_rate}% rate: ${row.get('monthly_rate_savings', 0):,.0f}")

    with col_c:
        st.markdown("**Cash Flow Breakdown**")
        income = row.get("gross_rent", 0) or 0
        vacancy = row.get("vacancy_loss", 0) or 0
        expenses_items = {
            "P&I Payment": row.get("pni_payment", 0) or 0,
            "Property Tax": row.get("monthly_tax", 0) or 0,
            "Insurance": row.get("monthly_insurance", 0) or 0,
            "HOA": row.get("monthly_hoa", 0) or 0,
            "Maintenance": row.get("maintenance", 0) or 0,
            "CapEx Reserve": row.get("capex", 0) or 0,
        }

        st.write(f"Gross Rent: ${income:,.0f}/mo")
        st.write(f"Vacancy Loss: -${vacancy:,.0f}/mo")
        st.write("---")
        for name, val in expenses_items.items():
            if val:
                st.write(f"{name}: -${val:,.0f}/mo")
        st.write("---")
        cf = row.get("monthly_cashflow", 0) or 0
        cf_color = "green" if cf > 0 else "red"
        st.markdown(f"**Net Monthly CF: :{cf_color}[${cf:+,.0f}/mo]**")
        st.markdown(f"**Annual CF: ${row.get('annual_cashflow', 0):+,.0f}**" if pd.notna(row.get('annual_cashflow')) else "")
        if pd.notna(row.get('cash_on_cash_pct')):
            st.markdown(f"**Cash-on-Cash: {row.get('cash_on_cash_pct'):.1f}%**")
        st.write(f"Break-even Rent: ${row.get('breakeven_rent', 0):,.0f}/mo" if pd.notna(row.get('breakeven_rent')) else "")
        st.write(f"Rent Source: {row.get('rent_source', '—')}")

# --- Export ---
st.divider()
csv = filtered.to_csv(index=False)
st.download_button("Download Filtered Data (CSV)", csv, "atlanta_listings.csv", "text/csv")
