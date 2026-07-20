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
from app.schema import FINANCIAL_CLASSIFICATIONS  # noqa: E402

DATA_DIR = ROOT / "data"
WORKBOOK = DATA_DIR / "Rental_Portfolio.xlsx"

st.set_page_config(page_title="Rental Portfolio Tracker", page_icon="🏠", layout="wide")
manager = PortfolioManager(WORKBOOK)
store = manager.store


def money(value) -> str:
    return f"${float(value or 0):,.2f}"


def portfolio_overview() -> None:
    st.title("Rental Portfolio Overview")
    metrics = store.read("Monthly_Metrics")
    if metrics.empty:
        st.info(
            "Import an AppFolio statement and complete property details to begin analysis."
        )
        return
    for col in [
        "operating_revenue",
        "operating_expenses",
        "noi",
        "capex",
        "cash_flow_after_debt",
    ]:
        metrics[col] = pd.to_numeric(metrics[col], errors="coerce").fillna(0)
    latest = metrics[
        metrics["period"].astype(str) == metrics["period"].astype(str).max()
    ]
    cols = st.columns(5)
    for widget, label, column in zip(
        cols,
        ["Revenue", "Operating expenses", "NOI", "CapEx", "Cash flow"],
        [
            "operating_revenue",
            "operating_expenses",
            "noi",
            "capex",
            "cash_flow_after_debt",
        ],
    ):
        widget.metric(label, money(latest[column].sum()))
    period_dates = pd.to_datetime(metrics["period"].astype(str) + "-01")
    latest_date = period_dates.max()
    ytd = metrics[period_dates.dt.year == latest_date.year]
    trailing = metrics[period_dates >= latest_date - pd.DateOffset(months=11)]
    st.caption(
        f"Year to date: Revenue {money(ytd['operating_revenue'].sum())} · NOI {money(ytd['noi'].sum())} · "
        f"Cash flow {money(ytd['cash_flow_after_debt'].sum())} | Trailing 12 months: "
        f"Revenue {money(trailing['operating_revenue'].sum())} · NOI {money(trailing['noi'].sum())} · "
        f"Cash flow {money(trailing['cash_flow_after_debt'].sum())}"
    )
    monthly = metrics.groupby("period", as_index=False)[
        ["operating_revenue", "operating_expenses", "noi", "cash_flow_after_debt"]
    ].sum()
    st.plotly_chart(
        px.line(
            monthly,
            x="period",
            y=[
                "operating_revenue",
                "operating_expenses",
                "noi",
                "cash_flow_after_debt",
            ],
            markers=True,
            title="Monthly portfolio performance",
        ),
        width="stretch",
    )
    by_property = metrics.groupby("property_name", as_index=False)[
        ["noi", "cash_flow_after_debt"]
    ].sum()
    st.plotly_chart(
        px.bar(
            by_property,
            x="property_name",
            y=["noi", "cash_flow_after_debt"],
            barmode="group",
            title="Property contribution",
        ),
        width="stretch",
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
    data = metrics[metrics["property_id"].astype(str) == str(selected)].copy()
    if data.empty:
        st.info("No financial activity has been recorded for this property.")
        return
    numeric = [
        "operating_revenue",
        "operating_expenses",
        "noi",
        "capex",
        "cash_flow_after_debt",
    ]
    data[numeric] = data[numeric].apply(pd.to_numeric, errors="coerce").fillna(0)
    st.plotly_chart(
        px.line(data, x="period", y=numeric, markers=True, title=options[selected]),
        width="stretch",
    )
    display = [
        "period",
        *numeric,
        "collection_rate",
        "expense_ratio",
        "dscr",
        "cap_rate_purchase",
        "cap_rate_current",
        "cash_on_cash",
        "estimated_equity",
        "ltv",
        "return_on_equity",
    ]
    st.dataframe(data.reindex(columns=display), width="stretch", hide_index=True)


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
    st.title("Properties, Units, Loans, and Values")
    tab_property, tab_unit, tab_loan, tab_value = st.tabs(
        ["Properties", "Units", "Loans", "Valuations"]
    )
    properties = store.read("Properties")
    with tab_property:
        st.caption(
            "Edit imported property names and add acquisition, valuation, tax, insurance, and expected-rent details directly below."
        )
        edited_properties = st.data_editor(
            properties,
            width="stretch",
            hide_index=True,
            disabled=["property_id"],
            key="property_editor",
        )
        if st.button("Save property changes"):
            store.update({"Properties": edited_properties})
            manager.refresh_metrics()
            st.success("Property details saved.")
            st.rerun()
        st.subheader("Add another property")
        with st.form("property_form"):
            name = st.text_input("Property name*")
            address = st.text_input("Address")
            kind = st.selectbox(
                "Type", ["Single-family", "Duplex", "Fourplex", "Other"]
            )
            purchase_date = st.date_input("Purchase date")
            numeric_names = [
                "Purchase price",
                "Closing costs",
                "Initial renovations",
                "Down payment",
                "Other initial capital",
                "Current value",
                "Annual taxes",
                "Annual insurance",
                "Annual HOA",
                "Expected monthly rent",
            ]
            values = {
                label: st.number_input(label, min_value=0.0, step=100.0)
                for label in numeric_names
            }
            notes = st.text_area("Notes")
            if st.form_submit_button("Add property", type="primary"):
                if not name:
                    st.error("Property name is required.")
                else:
                    row = {
                        "property_id": f"prop_{uuid.uuid4().hex[:10]}",
                        "name": name,
                        "address": address,
                        "property_type": kind,
                        "purchase_date": purchase_date.isoformat(),
                        "notes": notes,
                    }
                    keys = [
                        "purchase_price",
                        "closing_costs",
                        "initial_renovations",
                        "down_payment",
                        "other_initial_capital",
                        "current_value",
                        "annual_taxes",
                        "annual_insurance",
                        "annual_hoa",
                        "expected_monthly_rent",
                    ]
                    row.update(dict(zip(keys, values.values())))
                    store.update(
                        {
                            "Properties": pd.concat(
                                [properties, pd.DataFrame([row])], ignore_index=True
                            )
                        }
                    )
                    manager.refresh_metrics()
                    st.success("Property added.")
                    st.rerun()
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
        "Portfolio Overview",
        "Property Performance",
        "Unit Performance",
        "Income & Expenses",
        "Debt & Equity",
        "Statements and Imports",
        "Properties, Units, Loans & Values",
        "Owner-Paid Expenses",
        "Import Review",
        "Data & Settings",
    ],
)
if page == "Portfolio Overview":
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
elif page == "Properties, Units, Loans & Values":
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
