"""Command-line statement importer for users who prefer folders over the UI."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.processor import PortfolioManager


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import AppFolio statements into Rental_Portfolio.xlsx"
    )
    parser.add_argument(
        "paths", nargs="+", type=Path, help="PDF files or directories containing PDFs"
    )
    parser.add_argument(
        "--allow-revisions",
        action="store_true",
        help="Import changed PDFs for existing periods",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manager = PortfolioManager(root / "data" / "Rental_Portfolio.xlsx")
    manager.apply_historical_seed(root / "data" / "historical_seed_june_2025.json")
    manager.apply_statement_history_seed(
        root / "data" / "statement_history_through_2026_06.json"
    )
    manager.refresh_for_calculation_version("4.0.0")
    pdfs = []
    for path in args.paths:
        pdfs.extend(sorted(path.glob("*.pdf")) if path.is_dir() else [path])
    for pdf in pdfs:
        parsed, state, _ = manager.analyze_statement(pdf)
        if state == "duplicate":
            print(f"SKIP duplicate: {pdf}")
            continue
        if state == "revision" and not args.allow_revisions:
            print(f"SKIP revision (use --allow-revisions): {pdf}")
            continue
        result = manager.commit_statement(
            parsed, pdf.name, allow_revision=args.allow_revisions
        )
        print(
            f"{result['status'].upper()}: {pdf} -> {result['filename']} ({result['transactions']} transactions, {result['warnings']} warnings)"
        )


if __name__ == "__main__":
    main()
