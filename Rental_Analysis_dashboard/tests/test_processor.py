import shutil

import pandas as pd
import pytest

from app.processor import PortfolioManager


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
