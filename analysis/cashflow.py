"""
Cash flow analysis model for assumable mortgage properties.
Calculates monthly cash flow, cash-on-cash return, and investment metrics.
"""

import pandas as pd
import numpy as np

# Assumptions (can be overridden per analysis)
DEFAULTS = {
    "vacancy_rate": 0.05,          # 5% vacancy / credit loss
    "maintenance_pct": 0.01,       # 1% of property value per year
    "management_fee_pct": 0.08,    # 8% of gross rent (if using property manager)
    "use_property_manager": False, # DIY by default
    "capex_monthly": 100,          # Capital expenditure reserve per month
}


def calculate_cashflow(row: dict, assumptions: dict = None) -> dict:
    """
    Calculate monthly cash flow for a single property.

    Args:
        row: Dict with property fields (price, loan_balance, monthly_payment,
             monthly_tax, monthly_insurance, monthly_hoa, rent_estimate, equity_needed)
        assumptions: Override default assumptions

    Returns:
        Dict with full cash flow breakdown
    """
    cfg = {**DEFAULTS, **(assumptions or {})}

    price = float(row.get("price") or 0)
    loan_balance = float(row.get("loan_balance") or 0)
    monthly_payment = float(row.get("monthly_payment") or 0)
    monthly_tax = float(row.get("monthly_tax") or 0)
    monthly_insurance = float(row.get("monthly_insurance") or 0)
    monthly_hoa = float(row.get("monthly_hoa") or 0)
    rent_estimate = float(row.get("rent_estimate") or 0)
    equity_needed = float(row.get("equity_needed") or (price - loan_balance) if price and loan_balance else 0)

    # --- Income ---
    gross_rent = rent_estimate
    vacancy_loss = gross_rent * cfg["vacancy_rate"]
    effective_rent = gross_rent - vacancy_loss

    # --- Expenses ---
    # Mortgage (P&I) — already known from assumable loan
    pni = monthly_payment

    # Property tax — estimate if missing
    if monthly_tax == 0 and price > 0:
        monthly_tax = price * 0.011 / 12  # ~1.1% effective rate for Atlanta

    # Insurance — estimate if missing
    if monthly_insurance == 0 and price > 0:
        monthly_insurance = price * 0.005 / 12  # ~0.5% of value/yr

    # Maintenance reserve
    maintenance = (price * cfg["maintenance_pct"]) / 12 if price > 0 else cfg["capex_monthly"]

    # Property management fee
    mgmt_fee = effective_rent * cfg["management_fee_pct"] if cfg["use_property_manager"] else 0

    # CapEx reserve
    capex = cfg["capex_monthly"]

    total_expenses = pni + monthly_tax + monthly_insurance + monthly_hoa + maintenance + mgmt_fee + capex

    # --- Cash Flow ---
    monthly_cashflow = effective_rent - total_expenses
    annual_cashflow = monthly_cashflow * 12

    # --- Returns ---
    cash_on_cash = (annual_cashflow / equity_needed * 100) if equity_needed > 0 else None

    # Gross rent multiplier
    grm = price / (gross_rent * 12) if gross_rent > 0 and price > 0 else None

    # Cap rate (NOI / price)
    noi_monthly = effective_rent - (monthly_tax + monthly_insurance + monthly_hoa + maintenance + mgmt_fee)
    noi_annual = noi_monthly * 12
    cap_rate = (noi_annual / price * 100) if price > 0 else None

    # Break-even rent (min rent needed for positive cash flow)
    breakeven_rent = total_expenses / (1 - cfg["vacancy_rate"])

    # Rate comparison: new mortgage rate savings
    assumable_rate = float(row.get("assumable_rate_pct") or 0)
    new_rate = 7.0  # current 30yr fixed approximation
    if assumable_rate > 0 and loan_balance > 0:
        remaining_years = float(row.get("remaining_years") or 25)
        new_payment = _mortgage_payment(loan_balance, new_rate / 100 / 12, remaining_years * 12)
        monthly_rate_savings = new_payment - monthly_payment
    else:
        monthly_rate_savings = 0

    return {
        # Income
        "gross_rent": round(gross_rent),
        "vacancy_loss": round(vacancy_loss),
        "effective_rent": round(effective_rent),
        # Expenses
        "pni_payment": round(pni),
        "monthly_tax": round(monthly_tax),
        "monthly_insurance": round(monthly_insurance),
        "monthly_hoa": round(monthly_hoa),
        "maintenance": round(maintenance),
        "mgmt_fee": round(mgmt_fee),
        "capex": round(capex),
        "total_expenses": round(total_expenses),
        # Results
        "monthly_cashflow": round(monthly_cashflow),
        "annual_cashflow": round(annual_cashflow),
        "cash_on_cash_pct": round(cash_on_cash, 2) if cash_on_cash is not None else None,
        "cap_rate_pct": round(cap_rate, 2) if cap_rate is not None else None,
        "grm": round(grm, 1) if grm is not None else None,
        "breakeven_rent": round(breakeven_rent),
        "equity_needed": round(equity_needed),
        "monthly_rate_savings": round(monthly_rate_savings),
        "cashflow_positive": monthly_cashflow > 0,
    }


def _mortgage_payment(principal: float, monthly_rate: float, n_payments: int) -> float:
    """Standard amortization payment formula."""
    if monthly_rate == 0:
        return principal / n_payments
    return principal * monthly_rate * (1 + monthly_rate) ** n_payments / ((1 + monthly_rate) ** n_payments - 1)


def analyze_portfolio(df: pd.DataFrame, assumptions: dict = None) -> pd.DataFrame:
    """
    Run cash flow analysis on all listings in the DataFrame.
    Returns DataFrame with analysis columns appended.
    """
    results = []
    for _, row in df.iterrows():
        cf = calculate_cashflow(row.to_dict(), assumptions)
        results.append(cf)

    cf_df = pd.DataFrame(results)
    return pd.concat([df.reset_index(drop=True), cf_df], axis=1)


def summary_stats(df: pd.DataFrame) -> dict:
    """Return portfolio-level summary statistics."""
    cf_col = "monthly_cashflow"
    if cf_col not in df.columns:
        return {}

    valid = df[df[cf_col].notna()]
    pos = valid[valid[cf_col] > 0]
    neg = valid[valid[cf_col] <= 0]

    return {
        "total_listings": len(df),
        "cashflow_positive": len(pos),
        "cashflow_negative": len(neg),
        "avg_monthly_cashflow": round(valid[cf_col].mean()),
        "median_monthly_cashflow": round(valid[cf_col].median()),
        "best_cashflow": round(valid[cf_col].max()),
        "worst_cashflow": round(valid[cf_col].min()),
        "avg_equity_needed": round(valid["equity_needed"].mean()) if "equity_needed" in valid else None,
        "avg_coc_return": round(valid["cash_on_cash_pct"].mean(), 2) if "cash_on_cash_pct" in valid else None,
    }
