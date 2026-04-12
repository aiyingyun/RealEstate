"""
Cash flow analysis model for assumable mortgage properties.
Calculates monthly cash flow, cash-on-cash return, and investment metrics.
"""

import math
import pandas as pd
import numpy as np


def _safe_round(val, ndigits=0):
    """Round val, returning None if NaN/None."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(val, ndigits)


def _estimate_remaining_years(principal: float, monthly_payment: float, annual_rate_pct: float) -> float | None:
    """Estimate remaining amortization term from balance, payment, and rate."""
    if principal <= 0 or monthly_payment <= 0 or annual_rate_pct < 0:
        return None

    monthly_rate = annual_rate_pct / 100 / 12
    if monthly_rate == 0:
        return principal / monthly_payment / 12 if monthly_payment > 0 else None

    # Payment must be high enough to cover at least the current month's interest.
    min_payment = principal * monthly_rate
    if monthly_payment <= min_payment:
        return None

    try:
        n_payments = -math.log(1 - (principal * monthly_rate / monthly_payment)) / math.log(1 + monthly_rate)
    except (ValueError, ZeroDivisionError):
        return None

    if not math.isfinite(n_payments) or n_payments <= 0:
        return None

    return n_payments / 12

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
    monthly_loan_insurance = float(row.get("monthly_loan_insurance") or 0)
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

    total_expenses = pni + monthly_tax + monthly_insurance + monthly_loan_insurance + monthly_hoa + maintenance + mgmt_fee + capex

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

    actual_remaining_years = row.get("remaining_years")
    if actual_remaining_years is not None and not (isinstance(actual_remaining_years, float) and math.isnan(actual_remaining_years)):
        actual_remaining_years = float(actual_remaining_years)
    else:
        actual_remaining_years = None

    estimated_remaining_years = None
    if actual_remaining_years is None:
        estimated_remaining_years = _estimate_remaining_years(
            principal=loan_balance,
            monthly_payment=monthly_payment,
            annual_rate_pct=float(row.get("assumable_rate_pct") or 0),
        )

    effective_remaining_years = actual_remaining_years or estimated_remaining_years

    # Rate comparison: new mortgage rate savings
    assumable_rate = float(row.get("assumable_rate_pct") or 0)
    new_rate = 7.0  # current 30yr fixed approximation
    if assumable_rate > 0 and loan_balance > 0 and effective_remaining_years:
        new_payment = _mortgage_payment(loan_balance, new_rate / 100 / 12, int(round(effective_remaining_years * 12)))
        monthly_rate_savings = new_payment - monthly_payment
    else:
        monthly_rate_savings = None

    return {
        # Income
        "gross_rent": _safe_round(gross_rent),
        "vacancy_loss": _safe_round(vacancy_loss),
        "effective_rent": _safe_round(effective_rent),
        # Expenses
        "pni_payment": _safe_round(pni),
        "monthly_tax": _safe_round(monthly_tax),
        "monthly_insurance": _safe_round(monthly_insurance),
        "monthly_loan_insurance": _safe_round(monthly_loan_insurance),
        "monthly_hoa": _safe_round(monthly_hoa),
        "maintenance": _safe_round(maintenance),
        "mgmt_fee": _safe_round(mgmt_fee),
        "capex": _safe_round(capex),
        "total_expenses": _safe_round(total_expenses),
        # Results
        "monthly_cashflow": _safe_round(monthly_cashflow),
        "annual_cashflow": _safe_round(annual_cashflow),
        "cash_on_cash_pct": _safe_round(cash_on_cash, 2),
        "cap_rate_pct": _safe_round(cap_rate, 2),
        "grm": _safe_round(grm, 1),
        "breakeven_rent": _safe_round(breakeven_rent),
        "equity_needed": _safe_round(equity_needed),
        "effective_remaining_years": _safe_round(effective_remaining_years, 2),
        "estimated_remaining_years": _safe_round(estimated_remaining_years, 2),
        "remaining_years_source": (
            "actual" if actual_remaining_years is not None else
            "estimated_from_balance_payment_rate" if estimated_remaining_years is not None else
            None
        ),
        "monthly_rate_savings": _safe_round(monthly_rate_savings),
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
    # Drop columns from cf_df that already exist in df to avoid duplicates
    overlap = [c for c in cf_df.columns if c in df.columns]
    cf_df = cf_df.drop(columns=overlap)
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
        "avg_equity_needed": round(valid["equity_needed"].mean()) if "equity_needed" in valid.columns else None,
        "avg_coc_return": round(valid["cash_on_cash_pct"].mean(), 2) if "cash_on_cash_pct" in valid.columns else None,
    }
