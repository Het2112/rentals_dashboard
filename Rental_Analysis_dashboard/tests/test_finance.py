import pandas as pd
import pytest

from app.finance import (
    calculate_monthly_metrics,
    loan_month,
    monthly_payment,
    summarize_cash_performance,
)


def test_amortization_calculates_principal_and_interest():
    payment = monthly_payment(240000, 6, 30)
    interest, principal, debt, balance = loan_month(
        {
            "original_principal": 240000,
            "origination_date": "2026-01-01",
            "interest_rate": 6,
            "amortization_years": 30,
            "monthly_payment": 0,
        },
        pd.Period("2026-01", freq="M"),
    )
    assert payment == pytest.approx(1438.92, abs=0.02)
    assert interest == pytest.approx(1200, abs=0.01)
    assert principal == pytest.approx(238.92, abs=0.02)
    assert debt == pytest.approx(payment, abs=0.02)
    assert balance == pytest.approx(239761.08, abs=0.02)


def test_metrics_keep_noi_capex_debt_and_transfers_separate():
    tx = pd.DataFrame(
        [
            {
                "date": "2026-06-01",
                "property_id": "p1",
                "cash_in": 2000,
                "cash_out": 0,
                "category": "Rent",
                "financial_classification": "Income",
            },
            {
                "date": "2026-06-02",
                "property_id": "p1",
                "cash_in": 0,
                "cash_out": 200,
                "category": "Repairs",
                "financial_classification": "Maintenance / Operating Expense",
            },
            {
                "date": "2026-06-03",
                "property_id": "p1",
                "cash_in": 0,
                "cash_out": 1000,
                "category": "Owner Distribution",
                "financial_classification": "Owner Distribution",
            },
        ]
    )
    external = pd.DataFrame(
        [
            {
                "date": "2026-06-05",
                "property_id": "p1",
                "amount": 500,
                "financial_classification": "Capital Improvement / CapEx",
            },
        ]
    )
    props = pd.DataFrame(
        [
            {
                "property_id": "p1",
                "name": "Test",
                "purchase_price": 200000,
                "current_value": 250000,
                "down_payment": 40000,
                "expected_monthly_rent": 2000,
            }
        ]
    )
    metrics = calculate_monthly_metrics(
        tx, external, props, pd.DataFrame(), pd.DataFrame()
    ).iloc[0]
    assert metrics.operating_revenue == 2000
    assert metrics.operating_expenses == 200
    assert metrics.noi == 1800
    assert metrics.capex == 500
    assert metrics.cash_flow_after_debt == 1300
    assert metrics.cap_rate_purchase == pytest.approx(0.108)
    assert metrics.collection_rate == 1
    assert metrics.cash_on_cash == pytest.approx(1300 * 12 / 40500)


def test_manual_balance_correction_restarts_amortization():
    interest, principal, debt, balance = loan_month(
        {
            "original_principal": 240000,
            "origination_date": "2020-01-01",
            "interest_rate": 6,
            "amortization_years": 30,
            "monthly_payment": 1500,
            "current_balance": 200000,
            "balance_as_of": "2026-06-01",
        },
        pd.Period("2026-06", freq="M"),
    )
    assert interest == pytest.approx(1000)
    assert principal == pytest.approx(500)
    assert debt == pytest.approx(1500)
    assert balance == pytest.approx(199500)


def test_mortgage_is_included_in_cash_profit_and_summary():
    tx = pd.DataFrame(
        [
            {
                "date": "2026-01-05",
                "property_id": "p1",
                "cash_in": 2000,
                "cash_out": 0,
                "category": "Rent",
                "financial_classification": "Income",
            },
            {
                "date": "2026-01-10",
                "property_id": "p1",
                "cash_in": 0,
                "cash_out": 200,
                "category": "Maintenance",
                "financial_classification": "Maintenance / Operating Expense",
            },
        ]
    )
    properties = pd.DataFrame(
        [
            {
                "property_id": "p1",
                "name": "Rental One",
                "financing_type": "Mortgage",
                "purchase_price": 300000,
                "down_payment": 60000,
            }
        ]
    )
    loans = pd.DataFrame(
        [
            {
                "property_id": "p1",
                "original_principal": 240000,
                "origination_date": "2026-01-01",
                "interest_rate": 6,
                "amortization_years": 30,
                "monthly_payment": 0,
            }
        ]
    )
    metrics = calculate_monthly_metrics(
        tx, pd.DataFrame(), properties, loans, pd.DataFrame()
    )
    month = metrics.iloc[0]
    assert month.noi == 1800
    assert month.mortgage_interest == pytest.approx(1200)
    assert month.mortgage_principal == pytest.approx(238.92, abs=0.02)
    assert month.cash_flow_after_debt == pytest.approx(361.08, abs=0.02)

    summary, label = summarize_cash_performance(
        metrics, properties, loans, "Latest month"
    )
    assert label == "January 2026"
    assert summary.iloc[0].result == "Profit"
    assert summary.iloc[0].setup_status == "Complete"
    assert summary.iloc[0].cash_flow_after_debt == pytest.approx(361.08, abs=0.02)


def test_missing_financing_is_clearly_marked_preliminary():
    metrics = pd.DataFrame(
        [
            {
                "period": "2026-01",
                "property_id": "p1",
                "property_name": "Rental One",
                "rental_income": 2000,
                "other_income": 0,
                "operating_revenue": 2000,
                "operating_expenses": 200,
                "noi": 1800,
                "mortgage_interest": 0,
                "mortgage_principal": 0,
                "debt_service": 0,
                "capex": 0,
                "cash_flow_after_debt": 1800,
            }
        ]
    )
    properties = pd.DataFrame(
        [{"property_id": "p1", "name": "Rental One", "financing_type": ""}]
    )
    summary, _ = summarize_cash_performance(
        metrics, properties, pd.DataFrame(), "Latest month"
    )
    assert summary.iloc[0].setup_status == "Setup needed"
    assert "cash purchase or mortgage" in summary.iloc[0].setup_note


def test_actual_interest_does_not_erase_scheduled_principal():
    tx = pd.DataFrame(
        [
            {
                "date": "2026-01-05",
                "property_id": "p1",
                "cash_in": 2000,
                "cash_out": 0,
                "category": "Rent",
                "financial_classification": "Income",
            }
        ]
    )
    external = pd.DataFrame(
        [
            {
                "date": "2026-01-10",
                "property_id": "p1",
                "amount": 1100,
                "financial_classification": "Mortgage Interest",
            }
        ]
    )
    properties = pd.DataFrame(
        [{"property_id": "p1", "name": "Rental", "financing_type": "Mortgage"}]
    )
    loans = pd.DataFrame(
        [
            {
                "property_id": "p1",
                "original_principal": 240000,
                "origination_date": "2026-01-01",
                "interest_rate": 6,
                "amortization_years": 30,
                "monthly_payment": 0,
            }
        ]
    )
    month = calculate_monthly_metrics(
        tx, external, properties, loans, pd.DataFrame()
    ).iloc[0]
    assert month.mortgage_interest == 1100
    assert month.mortgage_principal == pytest.approx(238.92, abs=0.02)
    assert month.debt_service == pytest.approx(1338.92, abs=0.02)
