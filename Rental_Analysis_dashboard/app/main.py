"""Streamlit entry point for the private rental portfolio dashboard."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
import sys

# Streamlit executes this file as a script. In that mode it may add ``app/``
# rather than the repository root to sys.path (notably on macOS), which makes
# absolute imports such as ``app.processor`` fail unless the project happened
# to be installed editable. Bootstrap the root before importing local modules.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from app.processor import PortfolioManager  # noqa: E402
from app.finance import summarize_cash_performance  # noqa: E402
from app.schema import FINANCIAL_CLASSIFICATIONS  # noqa: E402

DATA_DIR = ROOT / "data"
WORKBOOK = DATA_DIR / "Rental_Portfolio.xlsx"

st.set_page_config(page_title="Rental Portfolio Tracker", page_icon="🏠", layout="wide")
manager = PortfolioManager(WORKBOOK)
store = manager.store


def money(value) -> str:
    amount = float(value or 0)
    return f"-${abs(amount):,.2f}" if amount < 0 else f"${amount:,.2f}"


def ui_number(value) -> float:
    try:
        return 0.0 if pd.isna(value) else float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def ui_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.date() if pd.notna(parsed) else pd.Timestamp.today().date()


def portfolio_overview() -> None:
    st.title("My Rental Investment Summary")
    st.caption(
        "A simple cash view of whether your rentals put money in your pocket or required additional cash."
    )
    metrics = store.read("Monthly_Metrics")
    if metrics.empty:
        st.info(
            "Import an AppFolio statement and complete property details to begin analysis."
        )
        return
    scope = st.segmented_control(
        "Time period",
        ["Latest month", "Year to date", "Trailing 12 months", "All time"],
        default="Trailing 12 months",
    )
    summary, period_label = summarize_cash_performance(
        metrics,
        store.read("Properties"),
        store.read("Loans"),
        scope or "Trailing 12 months",
    )
    if summary.empty:
        st.info("There is no activity in this period.")
        return
    total_income = summary["operating_revenue"].sum()
    operating_costs = summary["operating_expenses"].sum()
    mortgage_payments = summary["debt_service"].sum()
    improvements = summary["capex"].sum()
    total_costs = operating_costs + mortgage_payments + improvements
    cash_result = summary["cash_flow_after_debt"].sum()
    result_word = (
        "PROFIT"
        if cash_result > 0.005
        else "LOSS"
        if cash_result < -0.005
        else "BREAK-EVEN"
    )
    incomplete = summary[summary["setup_status"] != "Complete"]
    headline = "Cash profit / loss" if incomplete.empty else "Preliminary cash result"
    st.subheader(period_label)
    hero, income_col, cost_col, equity_col = st.columns([1.5, 1, 1, 1])
    hero.metric(headline, money(cash_result), result_word)
    income_col.metric("Rent and other income", money(total_income))
    cost_col.metric("All cash costs", money(total_costs))
    equity_col.metric(
        "Mortgage principal paid",
        money(summary["mortgage_principal"].sum()),
        help="Principal reduces your cash today but builds property equity.",
    )
    if incomplete.empty:
        if cash_result >= 0:
            st.success(
                f"Your rentals generated {money(cash_result)} in cash profit for this period."
            )
        else:
            st.error(
                f"Your rentals used {money(abs(cash_result))} more cash than they generated for this period."
            )
    else:
        names = ", ".join(incomplete["property_name"].astype(str))
        st.warning(
            f"This result is preliminary because financing setup is incomplete for: {names}. "
            "Complete each property's cash-or-mortgage information so mortgage payments are not treated as zero."
        )
    st.info(
        "Accuracy reminder: add taxes, insurance, HOA, repairs, and other costs you paid directly under Owner-Paid Expenses. "
        "Unrealized property appreciation is not counted as cash profit."
    )
    investor_table = summary.assign(
        **{
            "Property": summary["property_name"],
            "Result": summary["result"],
            "Cash profit / loss": summary["cash_flow_after_debt"],
            "Income": summary["operating_revenue"],
            "Operating costs": summary["operating_expenses"],
            "Mortgage payments": summary["debt_service"],
            "Capital improvements": summary["capex"],
            "Setup": summary["setup_status"],
        }
    )[
        [
            "Property",
            "Result",
            "Cash profit / loss",
            "Income",
            "Operating costs",
            "Mortgage payments",
            "Capital improvements",
            "Setup",
        ]
    ].sort_values("Cash profit / loss", ascending=False)
    st.subheader("Profit or loss by property")
    st.dataframe(
        investor_table,
        width="stretch",
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column, format="$%.2f")
            for column in [
                "Cash profit / loss",
                "Income",
                "Operating costs",
                "Mortgage payments",
                "Capital improvements",
            ]
        },
    )
    st.plotly_chart(
        px.bar(
            investor_table,
            x="Property",
            y="Cash profit / loss",
            color="Result",
            color_discrete_map={
                "Profit": "#2e8b57",
                "Loss": "#c0392b",
                "Break-even": "#7f8c8d",
            },
            title="Cash profit or loss by property",
        ),
        width="stretch",
    )
    with st.expander("How this cash profit/loss is calculated"):
        st.markdown(
            """
            **Cash profit/loss = rent and other income − operating costs − mortgage
            principal and interest − capital improvements.**

            Owner contributions and distributions are transfers and are excluded.
            Mortgage principal reduces cash profit but builds equity. Capital
            improvements reduce cash in the period but do not reduce NOI. This is a
            cash-investment view, not taxable income.
            """
        )


def performance(unit_level: bool = False) -> None:
    title = "Unit Performance" if unit_level else "Property Performance"
    st.title(title)
    if unit_level:
        tx, units, external = (
            store.read("Transactions"),
            store.read("Units"),
            store.read("External_Expenses"),
        )
        if units.empty:
            st.info("Add units before using unit-level analysis.")
            return
        options = units.set_index("unit_id")["unit_name"].to_dict()
        selected = st.selectbox(
            "Unit", options, format_func=lambda value: options[value]
        )
        data = tx[tx["unit_id"].astype(str) == str(selected)].copy()
        owner = external[external["unit_id"].astype(str) == str(selected)].copy()
        rows = []
        for row in data.to_dict("records"):
            if row.get("financial_classification") in {
                "Owner Distribution",
                "Owner Contribution",
            }:
                continue
            rows.append(
                {
                    "date": row.get("date"),
                    "income": float(row.get("cash_in") or 0),
                    "expense": float(row.get("cash_out") or 0),
                    "description": row.get("description"),
                    "source": "Statement",
                }
            )
        for row in owner.to_dict("records"):
            rows.append(
                {
                    "date": row.get("date"),
                    "income": 0,
                    "expense": float(row.get("amount") or 0),
                    "description": row.get("description"),
                    "source": "Owner-paid",
                }
            )
        ledger = pd.DataFrame(rows)
        if ledger.empty:
            st.info(
                "No transactions have been matched to this unit. Ensure the unit name matches the address used in statement descriptions."
            )
            return
        ledger["date"] = pd.to_datetime(ledger["date"], errors="coerce")
        cols = st.columns(3)
        cols[0].metric("Income", money(ledger["income"].sum()))
        cols[1].metric("Expenses", money(ledger["expense"].sum()))
        cols[2].metric("Net", money(ledger["income"].sum() - ledger["expense"].sum()))
        monthly = (
            ledger.assign(period=ledger["date"].dt.to_period("M").astype(str))
            .groupby("period", as_index=False)[["income", "expense"]]
            .sum()
        )
        st.plotly_chart(
            px.bar(
                monthly,
                x="period",
                y=["income", "expense"],
                barmode="group",
                title=options[selected],
            ),
            width="stretch",
        )
        st.dataframe(
            ledger.sort_values("date", ascending=False),
            width="stretch",
            hide_index=True,
        )
        return
    metrics = store.read("Monthly_Metrics")
    properties = store.read("Properties")
    if properties.empty:
        st.info("Import a statement or add a property first.")
        return
    options = properties.set_index("property_id")["name"].to_dict()
    selected = st.selectbox(
        "Property", options, format_func=lambda value: options[value]
    )
    scope = st.segmented_control(
        "Time period",
        ["Latest month", "Year to date", "Trailing 12 months", "All time"],
        default="Trailing 12 months",
        key="property_scope",
    )
    data = metrics[metrics["property_id"].astype(str) == str(selected)].copy()
    if data.empty:
        st.info("No financial activity has been recorded for this property.")
        return
    summary, period_label = summarize_cash_performance(
        data, properties, store.read("Loans"), scope or "Trailing 12 months"
    )
    result = summary.iloc[0]
    cash_result = float(result["cash_flow_after_debt"])
    st.subheader(f"{result['result']}: {money(abs(cash_result))} · {period_label}")
    if result["setup_status"] != "Complete":
        st.warning(
            f"Preliminary result — {result['setup_note']}. Complete Property Setup before relying on this result."
        )
    income = float(result["operating_revenue"])
    operating = float(result["operating_expenses"])
    mortgage = float(result["debt_service"])
    capex = float(result["capex"])
    cols = st.columns(5)
    cols[0].metric("Income", money(income))
    cols[1].metric("Operating costs", money(operating))
    cols[2].metric("Mortgage payments", money(mortgage))
    cols[3].metric("Improvements", money(capex))
    cols[4].metric("Cash profit / loss", money(cash_result))
    numeric = [
        "operating_revenue",
        "operating_expenses",
        "debt_service",
        "capex",
        "cash_flow_after_debt",
    ]
    data[numeric] = data[numeric].apply(pd.to_numeric, errors="coerce").fillna(0)
    friendly = data.rename(
        columns={
            "period": "Month",
            "operating_revenue": "Income",
            "operating_expenses": "Operating costs",
            "debt_service": "Mortgage payments",
            "capex": "Capital improvements",
            "cash_flow_after_debt": "Cash profit / loss",
        }
    )
    st.plotly_chart(
        px.bar(
            friendly,
            x="Month",
            y=[
                "Income",
                "Operating costs",
                "Mortgage payments",
                "Capital improvements",
                "Cash profit / loss",
            ],
            barmode="group",
            title=f"Monthly cash performance — {options[selected]}",
        ),
        width="stretch",
    )
    with st.expander("Advanced investor metrics"):
        latest = data.sort_values("period").iloc[-1]
        advanced = pd.DataFrame(
            [
                [
                    "NOI",
                    latest.get("noi"),
                    "Income minus operating costs, before mortgage and improvements",
                ],
                ["DSCR", latest.get("dscr"), "NOI divided by mortgage payments"],
                [
                    "Cap rate on purchase",
                    latest.get("cap_rate_purchase"),
                    "Annualized NOI divided by purchase price",
                ],
                [
                    "Cash-on-cash return",
                    latest.get("cash_on_cash"),
                    "Annualized cash result divided by cash invested",
                ],
                [
                    "Estimated equity",
                    latest.get("estimated_equity"),
                    "Current value minus estimated loan balance",
                ],
                [
                    "Loan-to-value",
                    latest.get("ltv"),
                    "Estimated loan balance divided by current value",
                ],
            ],
            columns=["Metric", "Value", "Meaning"],
        )
        st.dataframe(advanced, width="stretch", hide_index=True)


def income_and_expenses() -> None:
    st.title("Income and Expenses")
    tx, external = store.read("Transactions"), store.read("External_Expenses")
    if tx.empty and external.empty:
        st.info("No income or expenses have been imported.")
        return
    rows = []
    for row in tx.to_dict("records"):
        if (
            row.get("financial_classification")
            in {"Owner Distribution", "Owner Contribution"}
            or row.get("category") == "Transfer"
        ):
            continue
        rows.append(
            {
                "date": row.get("date"),
                "property": row.get("property_name"),
                "category": row.get("category"),
                "income": float(row.get("cash_in") or 0),
                "expense": float(row.get("cash_out") or 0),
                "source": "Statement",
            }
        )
    names = store.read("Properties").set_index("property_id")["name"].to_dict()
    for row in external.to_dict("records"):
        rows.append(
            {
                "date": row.get("date"),
                "property": names.get(row.get("property_id"), row.get("property_id")),
                "category": row.get("category"),
                "income": 0,
                "expense": float(row.get("amount") or 0),
                "source": "Owner-paid",
            }
        )
    ledger = pd.DataFrame(rows)
    ledger["date"] = pd.to_datetime(ledger["date"], errors="coerce")
    expenses = ledger.groupby("category", as_index=False)["expense"].sum()
    st.plotly_chart(
        px.pie(
            expenses[expenses["expense"] > 0],
            names="category",
            values="expense",
            title="Expenses by category",
        ),
        width="stretch",
    )
    monthly = (
        ledger.assign(period=ledger["date"].dt.to_period("M").astype(str))
        .groupby("period", as_index=False)[["income", "expense"]]
        .sum()
    )
    st.plotly_chart(
        px.bar(
            monthly,
            x="period",
            y=["income", "expense"],
            barmode="group",
            title="Monthly income and expenses",
        ),
        width="stretch",
    )
    st.dataframe(
        ledger.sort_values("date", ascending=False), width="stretch", hide_index=True
    )


def debt_and_equity() -> None:
    st.title("Debt and Equity")
    metrics = store.read("Monthly_Metrics")
    loans = store.read("Loans")
    if metrics.empty:
        st.info("Import activity and add loan/property valuation details first.")
        return
    latest = (
        metrics.sort_values("period").groupby("property_id", as_index=False).tail(1)
    )
    for column in [
        "estimated_equity",
        "ltv",
        "mortgage_interest",
        "mortgage_principal",
        "debt_service",
        "dscr",
    ]:
        latest[column] = pd.to_numeric(latest[column], errors="coerce")
    st.plotly_chart(
        px.bar(
            latest,
            x="property_name",
            y="estimated_equity",
            title="Estimated equity by property",
        ),
        width="stretch",
    )
    st.dataframe(
        latest[
            [
                "property_name",
                "estimated_equity",
                "ltv",
                "mortgage_interest",
                "mortgage_principal",
                "debt_service",
                "dscr",
            ]
        ],
        width="stretch",
        hide_index=True,
    )
    st.subheader("Loan records")
    st.dataframe(loans, width="stretch", hide_index=True)


def import_statements() -> None:
    st.title("Statements and Imports")
    uploads = st.file_uploader(
        "Upload one or more AppFolio owner statements",
        type=["pdf"],
        accept_multiple_files=True,
    )
    if uploads and st.button("Analyze statements", type="primary"):
        previews = []
        for upload in uploads:
            temp_dir = Path(tempfile.mkdtemp(prefix="rental-import-"))
            path = temp_dir / upload.name
            path.write_bytes(upload.getvalue())
            try:
                parsed, state, revision = manager.analyze_statement(path)
                previews.append(
                    {
                        "upload": upload.name,
                        "path": str(path),
                        "parsed": parsed,
                        "state": state,
                        "revision": revision,
                    }
                )
            except Exception as exc:
                st.error(f"{upload.name}: {exc}")
        st.session_state["statement_previews"] = previews
    previews = st.session_state.get("statement_previews", [])
    for index, item in enumerate(previews):
        parsed = item["parsed"]
        with st.container(border=True):
            st.subheader(item["upload"])
            st.write(
                f"Period: {parsed.period_start} to {parsed.period_end} · Status: **{item['state']}** · Transactions: {len(parsed.transactions)} · Properties: {len(parsed.summaries)} · Warnings: {len(parsed.errors)}"
            )
            if parsed.errors:
                st.dataframe(
                    pd.DataFrame(parsed.errors), hide_index=True, width="stretch"
                )
            if item["state"] == "duplicate":
                st.info("Already imported; no action is needed.")
            elif st.button(
                "Import" if item["state"] == "new" else "Import revised statement",
                key=f"commit_{index}",
            ):
                try:
                    result = manager.commit_statement(
                        parsed,
                        item["upload"],
                        allow_revision=item["state"] == "revision",
                    )
                    st.success(
                        f"Imported {result['transactions']} transactions as {result['filename']}."
                    )
                    previews[index]["state"] = "duplicate"
                except Exception as exc:
                    st.error(str(exc))
    st.subheader("Import history")
    st.dataframe(store.read("Statements"), width="stretch", hide_index=True)


def manage_properties() -> None:
    st.title("Property Setup")
    st.caption(
        "Tell us how each property was purchased so profit/loss includes the mortgage correctly."
    )
    tab_property, tab_unit, tab_loan, tab_value = st.tabs(
        ["Property setup", "Units", "Advanced loan details", "Property values"]
    )
    properties = store.read("Properties")
    with tab_property:
        property_choices = {"__new__": "Add a new property"}
        if not properties.empty:
            property_choices.update(
                properties.set_index("property_id")["name"].astype(str).to_dict()
            )
        selected_property = st.selectbox(
            "Which property do you want to set up?",
            property_choices,
            format_func=lambda value: property_choices[value],
        )
        existing = (
            properties[properties["property_id"].astype(str) == str(selected_property)]
            .iloc[0]
            .to_dict()
            if selected_property != "__new__"
            else {}
        )
        loans = store.read("Loans")
        existing_loans = (
            loans[loans["property_id"].astype(str) == str(selected_property)]
            if selected_property != "__new__"
            else pd.DataFrame()
        )
        existing_loan = (
            existing_loans.iloc[0].to_dict() if not existing_loans.empty else {}
        )
        financing_default = str(existing.get("financing_type") or "")
        if financing_default not in {"Cash purchase", "Mortgage"}:
            financing_default = "Mortgage" if existing_loan else "Choose one"
        with st.form(f"property_setup_{selected_property}"):
            st.subheader("1. Property")
            name = st.text_input(
                "Property name*", value=str(existing.get("name") or "")
            )
            address = st.text_input("Address", value=str(existing.get("address") or ""))
            property_types = ["Single-family", "Duplex", "Fourplex", "Other"]
            existing_type = str(existing.get("property_type") or "Single-family")
            kind = st.selectbox(
                "Property type",
                property_types,
                index=property_types.index(existing_type)
                if existing_type in property_types
                else 0,
            )
            purchase_date = st.date_input(
                "Purchase date", value=ui_date(existing.get("purchase_date"))
            )
            purchase_col, value_col, rent_col = st.columns(3)
            purchase_price = purchase_col.number_input(
                "Purchase price",
                min_value=0.0,
                value=ui_number(existing.get("purchase_price")),
                step=1000.0,
            )
            current_value = value_col.number_input(
                "Current estimated value",
                min_value=0.0,
                value=ui_number(existing.get("current_value")),
                step=1000.0,
            )
            expected_rent = rent_col.number_input(
                "Expected monthly rent",
                min_value=0.0,
                value=ui_number(existing.get("expected_monthly_rent")),
                step=100.0,
            )
            st.subheader("2. Your cash invested")
            cash_col1, cash_col2, cash_col3, cash_col4 = st.columns(4)
            down_payment = cash_col1.number_input(
                "Down payment",
                min_value=0.0,
                value=ui_number(existing.get("down_payment")),
            )
            closing_costs = cash_col2.number_input(
                "Closing costs",
                min_value=0.0,
                value=ui_number(existing.get("closing_costs")),
            )
            renovations = cash_col3.number_input(
                "Initial renovations",
                min_value=0.0,
                value=ui_number(existing.get("initial_renovations")),
            )
            other_capital = cash_col4.number_input(
                "Other initial cash",
                min_value=0.0,
                value=ui_number(existing.get("other_initial_capital")),
            )
            st.subheader("3. Financing")
            financing_options = ["Choose one", "Cash purchase", "Mortgage"]
            financing = st.selectbox(
                "How was this property purchased?*",
                financing_options,
                index=financing_options.index(financing_default),
                help="Mortgage details are required to calculate your true cash profit or loss.",
            )
            st.markdown("**Complete these fields when Mortgage is selected:**")
            loan_col1, loan_col2, loan_col3 = st.columns(3)
            principal = loan_col1.number_input(
                "Original mortgage amount",
                min_value=0.0,
                value=ui_number(existing_loan.get("original_principal")),
                step=1000.0,
            )
            rate = loan_col2.number_input(
                "Interest rate (%)",
                min_value=0.0,
                max_value=30.0,
                value=ui_number(existing_loan.get("interest_rate")),
                step=0.125,
                help="Enter 6.5 for a 6.5% mortgage rate.",
            )
            amortization = loan_col3.number_input(
                "Amortization period (years)",
                min_value=1,
                max_value=50,
                value=int(ui_number(existing_loan.get("amortization_years")) or 30),
            )
            loan_col4, loan_col5, loan_col6 = st.columns(3)
            loan_start = loan_col4.date_input(
                "Mortgage start date",
                value=ui_date(
                    existing_loan.get("origination_date")
                    or existing.get("purchase_date")
                ),
            )
            payment = loan_col5.number_input(
                "Monthly principal + interest",
                min_value=0.0,
                value=ui_number(existing_loan.get("monthly_payment")),
                help="Leave at 0 and the dashboard will calculate it from the loan amount, rate, and term.",
            )
            current_balance = loan_col6.number_input(
                "Current mortgage balance",
                min_value=0.0,
                value=ui_number(existing_loan.get("current_balance")),
            )
            balance_date = st.date_input(
                "Balance as of",
                value=ui_date(existing_loan.get("balance_as_of")),
            )
            with st.expander("Optional annual property information"):
                annual_col1, annual_col2, annual_col3 = st.columns(3)
                annual_taxes = annual_col1.number_input(
                    "Annual property taxes",
                    min_value=0.0,
                    value=ui_number(existing.get("annual_taxes")),
                )
                annual_insurance = annual_col2.number_input(
                    "Annual insurance",
                    min_value=0.0,
                    value=ui_number(existing.get("annual_insurance")),
                )
                annual_hoa = annual_col3.number_input(
                    "Annual HOA",
                    min_value=0.0,
                    value=ui_number(existing.get("annual_hoa")),
                )
                st.caption(
                    "These are reference values. Record actual owner-paid tax, insurance, and HOA payments under Owner-Paid Expenses for cash profit/loss."
                )
            notes = st.text_area("Notes", value=str(existing.get("notes") or ""))
            if st.form_submit_button("Save property setup", type="primary"):
                property_record = {
                    "property_id": ""
                    if selected_property == "__new__"
                    else selected_property,
                    "name": name,
                    "address": address,
                    "property_type": kind,
                    "financing_type": financing,
                    "purchase_date": purchase_date.isoformat(),
                    "purchase_price": purchase_price,
                    "closing_costs": closing_costs,
                    "initial_renovations": renovations,
                    "down_payment": down_payment,
                    "other_initial_capital": other_capital,
                    "current_value": current_value,
                    "annual_taxes": annual_taxes,
                    "annual_insurance": annual_insurance,
                    "annual_hoa": annual_hoa,
                    "expected_monthly_rent": expected_rent,
                    "notes": notes,
                }
                loan_record = (
                    {
                        "original_principal": principal,
                        "origination_date": loan_start.isoformat(),
                        "interest_rate": rate,
                        "term_years": amortization,
                        "amortization_years": amortization,
                        "monthly_payment": payment,
                        "current_balance": current_balance,
                        "balance_as_of": balance_date.isoformat(),
                    }
                    if financing == "Mortgage"
                    else None
                )
                try:
                    manager.save_property_setup(property_record, loan_record)
                    st.success("Property and mortgage details saved.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    prop_options = (
        properties.set_index("property_id")["name"].to_dict()
        if not properties.empty
        else {}
    )
    with tab_unit:
        st.dataframe(store.read("Units"), width="stretch", hide_index=True)
        if prop_options:
            with st.form("unit_form"):
                prop = st.selectbox(
                    "Property", prop_options, format_func=lambda x: prop_options[x]
                )
                unit = st.text_input("Unit name or address*")
                rent = st.number_input("Expected monthly rent", min_value=0.0)
                if st.form_submit_button("Add unit") and unit:
                    units = store.read("Units")
                    store.update(
                        {
                            "Units": pd.concat(
                                [
                                    units,
                                    pd.DataFrame(
                                        [
                                            {
                                                "unit_id": f"unit_{uuid.uuid4().hex[:10]}",
                                                "property_id": prop,
                                                "unit_name": unit,
                                                "expected_monthly_rent": rent,
                                            }
                                        ]
                                    ),
                                ],
                                ignore_index=True,
                            )
                        }
                    )
                    matched = manager.remap_units()
                    st.success(
                        f"Unit added; matched {matched} existing transaction(s)."
                    )
                    st.rerun()
    with tab_loan:
        loans_table = store.read("Loans")
        edited_loans = st.data_editor(
            loans_table,
            width="stretch",
            hide_index=True,
            disabled=["loan_id", "property_id"],
            key="loan_editor",
        )
        if st.button("Save loan balance or detail changes"):
            store.update({"Loans": edited_loans})
            manager.refresh_metrics()
            st.success("Loan details saved.")
            st.rerun()
        if prop_options:
            with st.form("loan_form"):
                prop = st.selectbox(
                    "Property",
                    prop_options,
                    format_func=lambda x: prop_options[x],
                    key="loan_prop",
                )
                principal = st.number_input("Original principal", min_value=0.0)
                start = st.date_input("Origination date")
                rate = st.number_input(
                    "Interest rate (%)", min_value=0.0, max_value=30.0, step=0.125
                )
                term = st.number_input("Loan term (years)", min_value=1, value=30)
                amort = st.number_input("Amortization (years)", min_value=1, value=30)
                payment = st.number_input(
                    "Monthly principal and interest (0 = calculate)", min_value=0.0
                )
                if st.form_submit_button("Add loan"):
                    loans = store.read("Loans")
                    row = {
                        "loan_id": f"loan_{uuid.uuid4().hex[:10]}",
                        "property_id": prop,
                        "original_principal": principal,
                        "origination_date": start.isoformat(),
                        "interest_rate": rate,
                        "term_years": term,
                        "amortization_years": amort,
                        "monthly_payment": payment,
                    }
                    store.update(
                        {
                            "Loans": pd.concat(
                                [loans, pd.DataFrame([row])], ignore_index=True
                            )
                        }
                    )
                    manager.refresh_metrics()
                    st.success("Loan added.")
                    st.rerun()
    with tab_value:
        st.dataframe(store.read("Property_Values"), width="stretch", hide_index=True)
        if prop_options:
            with st.form("value_form"):
                prop = st.selectbox(
                    "Property",
                    prop_options,
                    format_func=lambda x: prop_options[x],
                    key="value_prop",
                )
                value_date = st.date_input("Valuation date")
                amount = st.number_input("Estimated value", min_value=0.0)
                notes = st.text_input("Notes")
                if st.form_submit_button("Add valuation"):
                    vals = store.read("Property_Values")
                    row = {
                        "value_id": f"val_{uuid.uuid4().hex[:10]}",
                        "property_id": prop,
                        "value_date": value_date.isoformat(),
                        "estimated_value": amount,
                        "notes": notes,
                    }
                    store.update(
                        {
                            "Property_Values": pd.concat(
                                [vals, pd.DataFrame([row])], ignore_index=True
                            )
                        }
                    )
                    manager.refresh_metrics()
                    st.success("Valuation added.")
                    st.rerun()


def external_expenses() -> None:
    st.title("Owner-Paid Expenses")
    properties, units = store.read("Properties"), store.read("Units")
    if properties.empty:
        st.warning("Add or import properties first.")
        return
    prop_options = properties.set_index("property_id")["name"].to_dict()
    with st.form("expense_form"):
        expense_date = st.date_input("Date")
        prop = st.selectbox(
            "Property", prop_options, format_func=lambda x: prop_options[x]
        )
        eligible = units[units["property_id"].astype(str) == str(prop)]
        unit_options = {
            "": "Property-wide",
            **(
                eligible.set_index("unit_id")["unit_name"].to_dict()
                if not eligible.empty
                else {}
            ),
        }
        unit = st.selectbox("Unit", unit_options, format_func=lambda x: unit_options[x])
        vendor = st.text_input("Vendor")
        description = st.text_input("Description*")
        amount = st.number_input("Amount*", min_value=0.0, step=1.0)
        category = st.text_input("Category", value="Repairs & Maintenance")
        classification = st.selectbox(
            "Financial classification",
            FINANCIAL_CLASSIFICATIONS,
            help="Maintenance reduces NOI. Capital improvements are excluded from NOI and tracked as CapEx.",
        )
        payment = st.text_input("Payment method")
        notes = st.text_area("Notes")
        if st.form_submit_button("Add expense", type="primary"):
            try:
                added, duplicates = manager.add_external_expenses(
                    [
                        {
                            "date": expense_date.isoformat(),
                            "property_id": prop,
                            "unit_id": unit,
                            "vendor": vendor,
                            "description": description,
                            "amount": amount,
                            "category": category,
                            "financial_classification": classification,
                            "payment_method": payment,
                            "notes": notes,
                            "source": "UI",
                        }
                    ]
                )
                st.success(
                    f"Added {added} expense(s); skipped {duplicates} duplicate(s)."
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    template = pd.DataFrame(
        columns=[
            "date",
            "property_id",
            "unit_id",
            "vendor",
            "description",
            "amount",
            "category",
            "financial_classification",
            "payment_method",
            "notes",
        ]
    )
    st.download_button(
        "Download CSV template",
        template.to_csv(index=False),
        "external_expenses_template.csv",
        "text/csv",
    )
    upload = st.file_uploader("Upload completed CSV or Excel", type=["csv", "xlsx"])
    if upload:
        uploaded = (
            pd.read_csv(upload)
            if upload.name.lower().endswith(".csv")
            else pd.read_excel(upload)
        )
        st.dataframe(uploaded, width="stretch", hide_index=True)
        if st.button("Validate and import expense rows"):
            try:
                records = uploaded.fillna("").to_dict("records")
                for record in records:
                    record["source"] = upload.name
                added, duplicates = manager.add_external_expenses(records)
                st.success(f"Added {added}; skipped {duplicates} duplicate(s).")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.subheader("Recorded owner-paid items")
    st.dataframe(store.read("External_Expenses"), width="stretch", hide_index=True)


def review() -> None:
    st.title("Import Review")
    errors = store.read("Import_Errors")
    open_errors = (
        errors[errors["status"].astype(str) == "open"] if not errors.empty else errors
    )
    if open_errors.empty:
        st.success("No open import warnings.")
        return
    st.dataframe(open_errors, width="stretch", hide_index=True)
    selected = st.selectbox(
        "Warning",
        open_errors["error_id"],
        format_func=lambda value: open_errors.loc[
            open_errors["error_id"] == value, "message"
        ].iloc[0],
    )
    corrected = st.text_input("Correction or review note")
    action = st.radio("Resolution", ["Approved", "Ignored"])
    if st.button("Resolve warning"):
        errors.loc[errors["error_id"] == selected, ["corrected_value", "status"]] = [
            corrected,
            action.lower(),
        ]
        store.update({"Import_Errors": errors})
        st.success("Warning resolved.")
        st.rerun()


page = st.sidebar.radio(
    "Navigate",
    [
        "Investor Summary",
        "Property Performance",
        "Unit Performance",
        "Income & Expenses",
        "Debt & Equity",
        "Statements and Imports",
        "Property Setup",
        "Owner-Paid Expenses",
        "Import Review",
        "Data & Settings",
    ],
)
if page == "Investor Summary":
    portfolio_overview()
elif page == "Property Performance":
    performance()
elif page == "Unit Performance":
    performance(True)
elif page == "Income & Expenses":
    income_and_expenses()
elif page == "Debt & Equity":
    debt_and_equity()
elif page == "Statements and Imports":
    import_statements()
elif page == "Property Setup":
    manage_properties()
elif page == "Owner-Paid Expenses":
    external_expenses()
elif page == "Import Review":
    review()
else:
    st.title("Data and Settings")
    st.write(f"Workbook: `{WORKBOOK}`")
    st.download_button(
        "Download current Excel workbook",
        WORKBOOK.read_bytes(),
        WORKBOOK.name,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    if st.button("Recalculate all metrics and Excel charts"):
        manager.refresh_metrics()
        st.success("Metrics refreshed.")
