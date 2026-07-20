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
    principal = _number(loan.get("original_principal"))
    if principal <= 0 or _missing(loan.get("origination_date")):
        return 0.0, 0.0, 0.0, _number(loan.get("current_balance"))
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
    rate = _number(loan.get("interest_rate")) / 100 / 12
    years = _number(loan.get("amortization_years") or loan.get("term_years"))
    payment = _number(loan.get("monthly_payment")) or monthly_payment(
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


def _is_maintenance(category, description="") -> bool:
    value = f"{category or ''} {description or ''}".lower()
    return any(
        word in value
        for word in (
            "repair",
            "maintenance",
            "pest",
            "grass",
            "lawn",
            "plumb",
            "appliance",
            "cleaning",
        )
    )


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
                    "maintenance": cash_out
                    if classification == "Maintenance / Operating Expense"
                    and _is_maintenance(category, row.get("description"))
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
                    "maintenance": amount
                    if classification == "Maintenance / Operating Expense"
                    and _is_maintenance(
                        row.get("category"), row.get("description")
                    )
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
        "maintenance",
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
                "maintenance": row["maintenance"],
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
                "maintenance_pct_rent": row["maintenance"] / row["rental_income"]
                if row["rental_income"]
                else math.nan,
                "capex_pct_rent": row["capex"] / row["rental_income"]
                if row["rental_income"]
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
    baselines: pd.DataFrame | None = None,
    statements: pd.DataFrame | None = None,
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
        "maintenance",
        "noi",
        "mortgage_interest",
        "mortgage_principal",
        "debt_service",
        "capex",
        "maintenance_pct_rent",
        "capex_pct_rent",
        "cash_flow_after_debt",
        "months",
        "result",
        "setup_status",
        "setup_note",
    ]
    baselines = baselines if baselines is not None else pd.DataFrame()
    statements = statements if statements is not None else pd.DataFrame()
    if metrics.empty and (
        baselines.empty or period_scope not in {"Since purchase", "All time"}
    ):
        return pd.DataFrame(columns=columns), "No activity"
    data = metrics.copy() if not metrics.empty else pd.DataFrame()
    if not data.empty:
        data["period_date"] = pd.to_datetime(
            data["period"].astype(str) + "-01", errors="coerce"
        )
    property_names = (
        properties.set_index("property_id")["name"].astype(str).to_dict()
        if not properties.empty
        else {}
    )
    since_purchase = period_scope in {"Since purchase", "All time"}
    if since_purchase and not baselines.empty:
        baseline_rows = []
        for baseline in baselines.to_dict("records"):
            property_id = baseline["property_id"]
            cutoff = pd.to_datetime(baseline["as_of_date"])
            if not data.empty:
                data = data[
                    ~(
                        (data["property_id"].astype(str) == str(property_id))
                        & (data["period_date"] <= cutoff)
                    )
                ]
            revenue = _number(baseline.get("total_rent"))
            maintenance = (
                _number(baseline.get("adjusted_maintenance"))
                if not _missing(baseline.get("adjusted_maintenance"))
                else _number(baseline.get("maintenance"))
            )
            capex = (
                _number(baseline.get("adjusted_capex"))
                if not _missing(baseline.get("adjusted_capex"))
                else _number(baseline.get("renovations"))
            )
            operating = (
                _number(baseline.get("management_fees"))
                + maintenance
                + _number(baseline.get("utility_deficit"))
            )
            baseline_rows.append(
                {
                    "period": str(pd.Period(cutoff, freq="M")),
                    "period_date": cutoff,
                    "property_id": property_id,
                    "property_name": property_names.get(
                        property_id, baseline.get("property_key", property_id)
                    ),
                    "rental_income": revenue,
                    "other_income": 0,
                    "operating_revenue": revenue,
                    "operating_expenses": operating,
                    "maintenance": maintenance,
                    "noi": revenue - operating,
                    "mortgage_interest": _number(baseline.get("mortgage_interest")),
                    "mortgage_principal": _number(baseline.get("mortgage_principal")),
                    "debt_service": _number(baseline.get("total_debt_tax_insurance")),
                    "capex": capex,
                    "cash_flow_after_debt": _number(
                        baseline.get("adjusted_cash_profit_loss")
                    )
                    if not _missing(baseline.get("adjusted_cash_profit_loss"))
                    else _number(baseline.get("cash_profit_loss")),
                }
            )
        data = pd.concat([data, pd.DataFrame(baseline_rows)], ignore_index=True)
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
    elif since_purchase:
        label = f"Since purchase through {latest.strftime('%B %Y')}"
    else:
        data = data[data["period_date"] >= latest - pd.DateOffset(months=11)]
        label = f"Trailing 12 months through {latest.strftime('%B %Y')}"
    number_columns = [
        "rental_income",
        "other_income",
        "operating_revenue",
        "operating_expenses",
        "maintenance",
        "noi",
        "mortgage_interest",
        "mortgage_principal",
        "debt_service",
        "capex",
        "cash_flow_after_debt",
    ]
    for column in number_columns:
        if column not in data:
            data[column] = 0.0
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
    summary = data.groupby(["property_id", "property_name"], as_index=False)[
        number_columns
    ].sum()
    summary["maintenance_pct_rent"] = summary.apply(
        lambda row: row["maintenance"] / row["rental_income"]
        if row["rental_income"]
        else math.nan,
        axis=1,
    )
    summary["capex_pct_rent"] = summary.apply(
        lambda row: row["capex"] / row["rental_income"]
        if row["rental_income"]
        else math.nan,
        axis=1,
    )
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
    baseline_cutoffs = (
        {
            str(row["property_id"]): pd.Period(
                pd.to_datetime(row["as_of_date"]), freq="M"
            )
            for row in baselines.to_dict("records")
        }
        if not baselines.empty
        else {}
    )
    imported_periods = set()
    if not statements.empty:
        active_statements = statements[statements["status"].astype(str) == "active"]
        for statement in active_statements.to_dict("records"):
            parsed = pd.to_datetime(statement.get("period_end"), errors="coerce")
            if pd.notna(parsed):
                imported_periods.add(str(pd.Period(parsed, freq="M")))
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
        if since_purchase:
            cutoff = baseline_cutoffs.get(str(prop_id))
            if cutoff is None and not _missing(prop.get("purchase_date")):
                cutoff = pd.Period(pd.to_datetime(prop["purchase_date"]), freq="M") - 1
            if cutoff is not None:
                expected = {
                    str(period)
                    for period in pd.period_range(
                        cutoff + 1, pd.Period(latest, freq="M"), freq="M"
                    )
                }
                missing_statements = expected - imported_periods
                if missing_statements:
                    coverage_note = (
                        f"missing {len(missing_statements)} monthly statement(s)"
                    )
                    note = f"{note}; {coverage_note}"
                    if status == "Complete":
                        status = "Data incomplete"
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
