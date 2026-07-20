from app.parser import AppFolioParser


def test_sample_statement_extracts_and_reconciles(sample_pdf):
    parsed = AppFolioParser(sample_pdf).parse()

    assert parsed.period_start == "2026-06-01"
    assert parsed.period_end == "2026-06-30"
    assert len(parsed.summaries) == 5
    assert len(parsed.transactions) == 59
    assert len(parsed.work_orders) == 3
    assert parsed.errors == []
    assert round(parsed.summaries["cash_in"].sum(), 2) == 9941.32
    assert round(parsed.transactions["cash_in"].sum(), 2) == 9941.32
    assert round(parsed.transactions["cash_out"].sum(), 2) == 10140.88


def test_multiline_pages_and_work_order_amounts(sample_pdf):
    parsed = AppFolioParser(sample_pdf).parse()

    assert parsed.transactions["property_name"].nunique() == 5
    assert (
        parsed.transactions["description"].str.contains("Rent Income", case=False).any()
    )
    assert sorted(parsed.work_orders["amount"].tolist()) == [0.0, 330.0, 425.85]


def test_currency_parser():
    clean = AppFolioParser.clean_currency
    assert clean("$1,234.56") == 1234.56
    assert clean("(42.25)") == -42.25
    assert clean("") == 0
    assert clean("not money") == 0


def test_old_packet_header_does_not_become_part_of_property_name():
    text = """
    ERA REAL SOLUTIONS REALTY COMPANY
    MEHTA HET
    Owner Statement
    -- Sep 01, 2023 - Sep 30, 2023
    874-876 N CASSADY AVE - 874-876 N Cassady Ave, Columbus, OH 43219
    Property Manager: Donny Thompson
    Property Cash Summary
    """
    assert AppFolioParser._property_heading(text) == (
        "874-876 N CASSADY AVE - 874-876 N Cassady Ave, Columbus, OH 43219"
    )


def test_property_work_is_classified_for_investor_reporting():
    classify = AppFolioParser._category
    assert classify("Pest Control", "Bill") == (
        "Repairs & Maintenance",
        "Maintenance / Operating Expense",
    )
    assert classify("R & R Plumbing Expenses only", "Bill") == (
        "Repairs & Maintenance",
        "Maintenance / Operating Expense",
    )
    assert classify("Roof Replacement - final draw", "Bill") == (
        "Capital Improvements",
        "Capital Improvement / CapEx",
    )
    assert classify("Water heater repair", "Bill") == (
        "Repairs & Maintenance",
        "Maintenance / Operating Expense",
    )
