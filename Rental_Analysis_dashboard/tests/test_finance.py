import pandas as pd
import pytest

from app.finance import calculate_monthly_metrics, loan_month, monthly_payment


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
