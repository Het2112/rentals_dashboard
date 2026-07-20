"""Real-estate calculations kept independent from the UI and storage layers."""

from __future__ import annotations

import math

import pandas as pd


def monthly_payment(principal: float, annual_rate: float, years: float) -> float:
    principal, annual_rate, years = (
        float(principal or 0),
        float(annual_rate or 0),
        float(years or 0),
    )
    months = int(years * 12)
    if principal <= 0 or months <= 0:
        return 0.0
    rate = annual_rate / 100 / 12
    return (
        principal / months
        if rate == 0
        else principal * rate / (1 - (1 + rate) ** -months)
    )


def loan_month(loan: dict, period: pd.Period) -> tuple[float, float, float, float]:
    """Return interest, principal, payment, and ending balance for a calendar month."""
    principal = float(loan.get("original_principal") or 0)
    if principal <= 0 or not loan.get("origination_date"):
        return 0.0, 0.0, 0.0, float(loan.get("current_balance") or principal)
    start = pd.Period(pd.to_datetime(loan["origination_date"]), freq="M")
    corrected_balance = _number(loan.get("current_balance"))
    corrected_as_of = loan.get("balance_as_of")
    if corrected_balance > 0 and corrected_as_of and not pd.isna(corrected_as_of):
        correction_period = pd.Period(pd.to_datetime(corrected_as_of), freq="M")
        if period >= correction_period:
            principal = corrected_balance
            start = correction_period
    elapsed = period.ordinal - start.ordinal
    if elapsed < 0:
        return 0.0, 0.0, 0.0, principal
    rate = float(loan.get("interest_rate") or 0) / 100 / 12
    years = float(loan.get("amortization_years") or loan.get("term_years") or 0)
    payment = float(loan.get("monthly_payment") or 0) or monthly_payment(
        principal, rate * 1200, years
    )
    balance = principal
    interest = principal_component = 0.0
    for _ in range(elapsed + 1):
        interest = balance * rate
        principal_component = min(balance, max(0.0, payment - interest))
        balance = max(0.0, balance - principal_component)
    return (
        interest,
        principal_component,
        min(payment, interest + principal_component),
        balance,
    )


def _number(value) -> float:
    try:
        return 0.0 if pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return 0.0


def _missing(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def calculate_monthly_metrics(
    transactions: pd.DataFrame,
    external: pd.DataFrame,
    properties: pd.DataFrame,
    loans: pd.DataFrame,
    values: pd.DataFrame,
) -> pd.DataFrame:
    entries = []
    if not transactions.empty:
        tx = transactions.copy()
        tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
        for row in tx.dropna(subset=["date"]).to_dict("records"):
            classification = str(row.get("financial_classification") or "")
            category = str(row.get("category") or "")
            cash_in, cash_out = (
                _number(row.get("cash_in")),
                _number(row.get("cash_out")),
            )
            if (
                classification in {"Owner Distribution", "Owner Contribution"}
                or category == "Transfer"
            ):
                continue
            entries.append(
                {
                    "period": pd.Period(row["date"], freq="M"),
                    "property_id": row.get("property_id"),
                    "rental_income": cash_in if category == "Rent" else 0,
                    "other_income": cash_in if category != "Rent" else 0,
                    "operating_expenses": cash_out
                    if classification == "Maintenance / Operating Expense"
                    else 0,
                    "capex": cash_out
                    if classification == "Capital Improvement / CapEx"
                    else 0,
                    "mortgage_interest": cash_out
                    if classification == "Mortgage Interest"
                    else 0,
                    "mortgage_principal": cash_out
                    if classification == "Mortgage Principal"
                    else 0,
                }
            )
    if not external.empty:
        ext = external.copy()
        ext["date"] = pd.to_datetime(ext["date"], errors="coerce")
        for row in ext.dropna(subset=["date"]).to_dict("records"):
            classification, amount = (
                str(row.get("financial_classification") or ""),
                _number(row.get("amount")),
            )
            entries.append(
                {
                    "period": pd.Period(row["date"], freq="M"),
                    "property_id": row.get("property_id"),
                    "rental_income": 0,
                    "other_income": 0,
                    "operating_expenses": amount
                    if classification == "Maintenance / Operating Expense"
                    else 0,
                    "capex": amount
                    if classification == "Capital Improvement / CapEx"
                    else 0,
                    "mortgage_interest": amount
                    if classification == "Mortgage Interest"
                    else 0,
                    "mortgage_principal": amount
                    if classification == "Mortgage Principal"
                    else 0,
                }
            )
    columns = [
        "period",
        "property_id",
        "rental_income",
        "other_income",
        "operating_expenses",
        "capex",
        "mortgage_interest",
        "mortgage_principal",
    ]
    if not entries:
        return pd.DataFrame(columns=["period", "property_id", "property_name"])
    base = (
        pd.DataFrame(entries, columns=columns)
        .groupby(["period", "property_id"], dropna=False)
        .sum()
        .reset_index()
    )
    base = base.sort_values(["property_id", "period"])
    base["cumulative_capex"] = base.groupby("property_id", dropna=False)[
        "capex"
    ].cumsum()
    property_map = (
        properties.set_index("property_id").to_dict("index")
        if not properties.empty
        else {}
    )
    loan_map = (
        {key: group.to_dict("records") for key, group in loans.groupby("property_id")}
        if not loans.empty
        else {}
    )
    value_map = {}
    if not values.empty:
        prepared = values.copy()
        prepared["value_date"] = pd.to_datetime(prepared["value_date"], errors="coerce")
        for prop_id, group in (
            prepared.dropna(subset=["value_date"])
            .sort_values("value_date")
            .groupby("property_id")
        ):
            value_map[prop_id] = group.to_dict("records")
    result = []
    for row in base.to_dict("records"):
        prop_id, period = row["property_id"], row["period"]
        prop = property_map.get(prop_id, {})
        # If no explicit debt rows were entered for the month, estimate them from the loan schedule.
        interest, principal, debt, balance = 0.0, 0.0, 0.0, 0.0
        for loan in loan_map.get(prop_id, []):
            i, p, d, b = loan_month(loan, period)
            interest += i
            principal += p
            debt += d
            balance += b
        if row["mortgage_interest"]:
            interest = row["mortgage_interest"]
        if row["mortgage_principal"]:
            principal = row["mortgage_principal"]
        if row["mortgage_interest"] or row["mortgage_principal"]:
            debt = interest + principal
        revenue = row["rental_income"] + row["other_income"]
        noi = revenue - row["operating_expenses"]
        cash_flow = noi - debt - row["capex"]
        current_value = _number(prop.get("current_value"))
        for valuation in value_map.get(prop_id, []):
            if pd.Period(valuation["value_date"], freq="M") <= period:
                current_value = _number(valuation.get("estimated_value"))
        purchase = _number(prop.get("purchase_price"))
        invested = (
            sum(
                _number(prop.get(key))
                for key in (
                    "down_payment",
                    "closing_costs",
                    "initial_renovations",
                    "other_initial_capital",
                )
            )
            + row["cumulative_capex"]
        )
        equity = current_value - balance if current_value else 0.0
        expected = _number(prop.get("expected_monthly_rent"))
        result.append(
            {
                "period": str(period),
                "property_id": prop_id,
                "property_name": prop.get("name", prop_id),
                "rental_income": row["rental_income"],
                "other_income": row["other_income"],
                "operating_revenue": revenue,
                "operating_expenses": row["operating_expenses"],
                "noi": noi,
                "capex": row["capex"],
                "mortgage_interest": interest,
                "mortgage_principal": principal,
                "debt_service": debt,
                "cash_flow_after_debt": cash_flow,
                "expected_rent": expected,
                "collection_rate": row["rental_income"] / expected
                if expected
                else math.nan,
                "expense_ratio": row["operating_expenses"] / revenue
                if revenue
                else math.nan,
                "dscr": noi / debt if debt else math.nan,
                "cap_rate_purchase": noi * 12 / purchase if purchase else math.nan,
                "cap_rate_current": noi * 12 / current_value
                if current_value
                else math.nan,
                "cash_on_cash": cash_flow * 12 / invested if invested else math.nan,
                "estimated_equity": equity,
                "ltv": balance / current_value if current_value else math.nan,
                "return_on_equity": cash_flow * 12 / equity if equity > 0 else math.nan,
            }
        )
    return pd.DataFrame(result)


def summarize_cash_performance(
    metrics: pd.DataFrame,
    properties: pd.DataFrame,
    loans: pd.DataFrame,
    period_scope: str = "Trailing 12 months",
) -> tuple[pd.DataFrame, str]:
    """Return investor-friendly cash results and their reporting-period label.

    Cash profit/loss is spendable cash: operating revenue minus operating expenses,
    principal, interest, and capital improvements. It differs from taxable profit
    and from NOI.
    """
    columns = [
        "property_id",
        "property_name",
        "rental_income",
        "other_income",
        "operating_revenue",
        "operating_expenses",
        "noi",
        "mortgage_interest",
        "mortgage_principal",
        "debt_service",
        "capex",
        "cash_flow_after_debt",
        "months",
        "result",
        "setup_status",
        "setup_note",
    ]
    if metrics.empty:
        return pd.DataFrame(columns=columns), "No activity"
    data = metrics.copy()
    data["period_date"] = pd.to_datetime(
        data["period"].astype(str) + "-01", errors="coerce"
    )
    data = data.dropna(subset=["period_date"])
    if data.empty:
        return pd.DataFrame(columns=columns), "No valid periods"
    latest = data["period_date"].max()
    if period_scope == "Latest month":
        data = data[data["period_date"] == latest]
        label = latest.strftime("%B %Y")
    elif period_scope == "Year to date":
        data = data[data["period_date"].dt.year == latest.year]
        label = f"January–{latest.strftime('%B %Y')}"
    elif period_scope == "All time":
        label = (
            f"{data['period_date'].min().strftime('%B %Y')}–{latest.strftime('%B %Y')}"
        )
    else:
        data = data[data["period_date"] >= latest - pd.DateOffset(months=11)]
        label = f"Trailing 12 months through {latest.strftime('%B %Y')}"
    number_columns = [
        "rental_income",
        "other_income",
        "operating_revenue",
        "operating_expenses",
        "noi",
        "mortgage_interest",
        "mortgage_principal",
        "debt_service",
        "capex",
        "cash_flow_after_debt",
    ]
    for column in number_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
    summary = data.groupby(["property_id", "property_name"], as_index=False)[
        number_columns
    ].sum()
    month_counts = data.groupby("property_id")["period"].nunique().to_dict()
    property_rows = (
        properties.set_index("property_id").to_dict("index")
        if not properties.empty
        else {}
    )
    loan_rows = (
        {key: group.to_dict("records") for key, group in loans.groupby("property_id")}
        if not loans.empty
        else {}
    )
    statuses, notes, results, months = [], [], [], []
    for row in summary.to_dict("records"):
        prop_id = row["property_id"]
        prop = property_rows.get(prop_id, {})
        financing = str(prop.get("financing_type") or "").strip()
        property_loans = loan_rows.get(prop_id, [])
        if financing == "Cash purchase":
            status, note = "Complete", "Cash purchase"
        elif financing == "Mortgage":
            loan = property_loans[0] if property_loans else {}
            missing = []
            if _number(loan.get("original_principal")) <= 0:
                missing.append("loan amount")
            rate_value = loan.get("interest_rate")
            if _missing(rate_value):
                missing.append("interest rate")
            start_value = loan.get("origination_date")
            if _missing(start_value):
                missing.append("loan start date")
            if _number(loan.get("amortization_years") or loan.get("term_years")) <= 0:
                missing.append("loan term")
            status = "Complete" if not missing else "Setup needed"
            note = (
                "Mortgage included" if not missing else "Missing " + ", ".join(missing)
            )
        else:
            status, note = "Setup needed", "Choose cash purchase or mortgage"
        cash_result = _number(row["cash_flow_after_debt"])
        results.append(
            "Profit"
            if cash_result > 0.005
            else "Loss"
            if cash_result < -0.005
            else "Break-even"
        )
        statuses.append(status)
        notes.append(note)
        months.append(month_counts.get(prop_id, 0))
    summary["months"] = months
    summary["result"] = results
    summary["setup_status"] = statuses
    summary["setup_note"] = notes
    return summary.reindex(columns=columns), label
