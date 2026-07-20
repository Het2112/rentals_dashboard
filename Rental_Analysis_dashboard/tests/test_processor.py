import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.processor import PortfolioManager


def test_streamlit_entrypoint_bootstraps_project_root():
    root = Path(__file__).resolve().parents[1]
    script = (
        "import runpy,sys; "
        f"sys.path=[p for p in sys.path if p != {str(root)!r}]; "
        f"runpy.run_path({str(root / 'app' / 'main.py')!r}, run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root.parent,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError: No module named 'app'" not in result.stderr


def test_import_duplicate_revision_and_registry(tmp_path, sample_pdf):
    manager = PortfolioManager(tmp_path / "data" / "Rental_Portfolio.xlsx")
    original = tmp_path / "statement.pdf"
    shutil.copy2(sample_pdf, original)
    parsed, state, revision = manager.analyze_statement(original)
    assert (state, revision) == ("new", 1)
    result = manager.commit_statement(parsed, original.name)
    assert result["transactions"] == 59
    assert manager.store.read("Statements").iloc[0]["status"] == "active"
    assert len(manager.store.read("Properties")) == 5
    assert len(manager.store.read("Transactions")) == 59

    assert manager.analyze_statement(original)[1] == "duplicate"
    revised = tmp_path / "revised.pdf"
    revised.write_bytes(original.read_bytes() + b"\n% revised fixture\n")
    parsed_revision, state, revision = manager.analyze_statement(revised)
    assert (state, revision) == ("revision", 2)
    with pytest.raises(ValueError):
        manager.commit_statement(parsed_revision, revised.name)
    manager.commit_statement(parsed_revision, revised.name, allow_revision=True)
    registry = manager.store.read("Statements")
    assert sorted(registry["status"].tolist()) == ["active", "superseded"]
    assert len(manager.store.read("Transactions")) == 59


def test_external_expense_deduplication_and_classification(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    manager.store.update(
        {"Properties": pd.DataFrame([{"property_id": "p1", "name": "One"}])}
    )
    record = {
        "date": "2026-07-01",
        "property_id": "p1",
        "unit_id": "",
        "vendor": "Vendor",
        "description": "Roof",
        "amount": 5000,
        "category": "Roof",
        "financial_classification": "Capital Improvement / CapEx",
    }
    assert manager.add_external_expenses([record]) == (1, 0)
    assert manager.add_external_expenses([record]) == (0, 1)
    metrics = manager.store.read("Monthly_Metrics").iloc[0]
    assert float(metrics["noi"]) == 0
    assert float(metrics["capex"]) == 5000
    assert float(metrics["cash_flow_after_debt"]) == -5000

    invalid = {**record, "description": "", "amount": 1}
    with pytest.raises(ValueError, match="description"):
        manager.add_external_expenses([invalid])

    expense_id = manager.store.read("External_Expenses").iloc[0]["expense_id"]
    manager.delete_external_expense(expense_id)
    assert manager.store.read("External_Expenses").empty
    with pytest.raises(ValueError, match="no longer exists"):
        manager.delete_external_expense(expense_id)


def test_property_setup_saves_interest_and_calculated_payment(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    property_id = manager.save_property_setup(
        {
            "name": "Investor Rental",
            "financing_type": "Mortgage",
            "purchase_price": 300000,
            "down_payment": 60000,
        },
        {
            "original_principal": 240000,
            "origination_date": "2026-01-01",
            "interest_rate": 6.5,
            "amortization_years": 30,
            "term_years": 30,
            "monthly_payment": 0,
        },
    )
    saved_property = manager.store.read("Properties").iloc[0]
    saved_loan = manager.store.read("Loans").iloc[0]
    assert saved_property["property_id"] == property_id
    assert saved_property["financing_type"] == "Mortgage"
    assert float(saved_loan["interest_rate"]) == 6.5
    assert float(saved_loan["monthly_payment"]) == pytest.approx(1516.96, abs=0.02)


def test_owner_historical_seed_is_valid_idempotent_and_preloaded(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    seed = (
        Path(__file__).resolve().parents[1] / "data" / "historical_seed_june_2025.json"
    )
    first = manager.apply_historical_seed(seed)
    second = manager.apply_historical_seed(seed)
    assert first == {"added": 5, "updated_properties": 5}
    assert second == {"added": 0, "updated_properties": 0}
    assert len(manager.store.read("Properties")) == 5
    baselines = manager.store.read("Historical_Baselines")
    assert len(baselines) == 5
    assert pd.to_numeric(baselines["cash_profit_loss"]).sum() == pytest.approx(
        -68964.64
    )
    rates = sorted(pd.to_numeric(manager.store.read("Loans")["interest_rate"]).tolist())
    assert rates == [7.13, 7.49, 7.49, 7.49, 8.0]
    owner_paid = manager.store.read("External_Expenses")
    assert pd.to_numeric(owner_paid["amount"]).sum() == pytest.approx(10375.45)
    assert set(owner_paid["source"]) == {"Historical baseline reconciliation"}
    assert manager.refresh_for_calculation_version("test-v1") is True
    assert manager.refresh_for_calculation_version("test-v1") is False


def test_portable_statement_seed_populates_fresh_clone_through_june_2026(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    data_dir = Path(__file__).resolve().parents[1] / "data"
    manager.apply_historical_seed(data_dir / "historical_seed_june_2025.json")
    first = manager.apply_statement_history_seed(
        data_dir / "statement_history_through_2026_06.json"
    )
    second = manager.apply_statement_history_seed(
        data_dir / "statement_history_through_2026_06.json"
    )

    assert first == {"statements_added": 34, "transactions_added": 1621}
    assert second == {"statements_added": 0, "transactions_added": 0}
    assert len(manager.store.read("Statements")) == 34
    assert len(manager.store.read("Transactions")) == 1621
    assert manager.store.read("Transactions")["property_id"].nunique() == 5
    metrics = manager.store.read("Monthly_Metrics")
    assert (metrics["period"].min(), metrics["period"].max()) == (
        "2023-09",
        "2026-06",
    )
    baselines = manager.store.read("Historical_Baselines")
    assert set(baselines["reconciliation_status"].str.split(";").str[0]) == {
        "Reconciled"
    }
    assert pd.to_numeric(baselines["owner_paid_maintenance"]).sum() == pytest.approx(
        8335.51
    )
    assert pd.to_numeric(baselines["adjusted_cash_profit_loss"]).sum() == pytest.approx(
        -72886.24
    )


def test_generated_historical_expense_cannot_be_deleted(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    data_dir = Path(__file__).resolve().parents[1] / "data"
    manager.apply_historical_seed(data_dir / "historical_seed_june_2025.json")
    generated = manager.store.read("External_Expenses").iloc[0]
    with pytest.raises(ValueError, match="cannot be deleted"):
        manager.delete_external_expense(generated["expense_id"])


def test_historical_maintenance_reconciles_to_owner_paid_difference(tmp_path):
    manager = PortfolioManager(tmp_path / "Rental_Portfolio.xlsx")
    manager.store.update(
        {
            "Properties": pd.DataFrame(
                [
                    {
                        "property_id": "p1",
                        "name": "One",
                        "purchase_date": "2025-06-01",
                    }
                ]
            ),
            "Historical_Baselines": pd.DataFrame(
                [
                    {
                        "baseline_id": "baseline_one_2025-06-30",
                        "property_id": "p1",
                        "property_key": "one",
                        "as_of_date": "2025-06-30",
                        "maintenance": 100,
                        "owner_paid_maintenance": 75,
                    }
                ]
            ),
            "Statements": pd.DataFrame(
                [
                    {
                        "statement_id": "s1",
                        "period_start": "2025-06-01",
                        "period_end": "2025-06-30",
                        "status": "active",
                    }
                ]
            ),
            "Transactions": pd.DataFrame(
                [
                    {
                        "transaction_id": "t1",
                        "statement_id": "s1",
                        "property_id": "p1",
                        "date": "2025-06-15",
                        "cash_out": 60,
                        "category": "Repairs & Maintenance",
                    }
                ]
            ),
        }
    )

    assert manager.reconcile_historical_maintenance() == 1
    baseline = manager.store.read("Historical_Baselines").iloc[0]
    expense = manager.store.read("External_Expenses").iloc[0]
    assert baseline["reconciliation_status"] == "Reconciled"
    assert float(baseline["statement_maintenance"]) == 60
    assert float(baseline["owner_paid_maintenance"]) == 40
    assert float(expense["amount"]) == 40
    assert expense["financial_classification"] == "Maintenance / Operating Expense"
