"""Build the portable normalized statement-history seed from AppFolio PDFs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.parser import AppFolioParser, PARSER_VERSION  # noqa: E402

PROPERTY_TOKENS = {
    "audrey": "audrey",
    "1703": "audrey",
    "mccarley": "mccarley",
    "3674-3680 south": "mccarley",
    "karl": "karl",
    "3795": "karl",
    "margaret": "margaret",
    "4602": "margaret",
    "cassady": "cassady",
    "874-876": "cassady",
}


def property_key(name: str) -> str:
    normalized = " ".join(str(name).lower().split())
    matches = {
        key for token, key in PROPERTY_TOKENS.items() if token in normalized
    }
    if len(matches) != 1:
        raise ValueError(f"Could not uniquely map property heading: {name}")
    return next(iter(matches))


def clean_records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    return json.loads(frame.where(pd.notna(frame), None).to_json(orient="records"))


def build(source_dir: Path, through: str) -> dict:
    parsed_by_period = {}
    skipped = []
    for pdf_path in sorted(source_dir.glob("*.pdf")):
        try:
            parsed = AppFolioParser(pdf_path).parse()
        except ValueError as exc:
            skipped.append({"filename": pdf_path.name, "reason": str(exc)})
            continue
        start = pd.to_datetime(parsed.period_start)
        end = pd.to_datetime(parsed.period_end)
        if start.to_period("M") != end.to_period("M"):
            skipped.append(
                {"filename": pdf_path.name, "reason": "multi-month duplicate report"}
            )
            continue
        period = str(start.to_period("M"))
        if period > through:
            continue
        if period in parsed_by_period:
            raise ValueError(f"More than one monthly statement found for {period}")
        parsed_by_period[period] = parsed

    expected = {str(value) for value in pd.period_range("2023-09", through, freq="M")}
    missing = sorted(expected - set(parsed_by_period))
    if missing:
        raise ValueError(f"Missing monthly statement periods: {', '.join(missing)}")

    sheets = {
        "Statements": [],
        "Transactions": [],
        "Property_Summaries": [],
        "Work_Orders": [],
        "Import_Errors": [],
    }
    for period, parsed in sorted(parsed_by_period.items()):
        statement_id = f"stmt_seed_{period.replace('-', '')}"
        sheets["Statements"].append(
            {
                "statement_id": statement_id,
                "period_start": parsed.period_start,
                "period_end": parsed.period_end,
                "original_filename": parsed.source_path.name,
                "local_filename": "",
                "sha256": parsed.sha256,
                "file_size": parsed.source_path.stat().st_size,
                "imported_at": "portable history seed",
                "revision": 1,
                "status": "active",
                "parser_version": PARSER_VERSION,
                "transaction_count": len(parsed.transactions),
                "validation_status": parsed.validation_status,
                "message": f"{len(parsed.errors)} validation warning(s)"
                if parsed.errors
                else "Validated",
            }
        )
        occurrences = {}
        for row in clean_records(parsed.transactions):
            key = property_key(row["property_name"])
            raw = "|".join(
                str(row.get(field, ""))
                for field in (
                    "date",
                    "reference",
                    "description",
                    "cash_in",
                    "cash_out",
                )
            )
            occurrences[raw] = occurrences.get(raw, 0) + 1
            row.update(
                {
                    "transaction_id": "txn_seed_"
                    + hashlib.sha256(
                        f"{parsed.sha256}|{raw}|{occurrences[raw]}".encode()
                    ).hexdigest()[:20],
                    "statement_id": statement_id,
                    "property_key": key,
                    "unit_id": "",
                    "period_start": parsed.period_start,
                    "period_end": parsed.period_end,
                }
            )
            sheets["Transactions"].append(row)
        for index, row in enumerate(clean_records(parsed.summaries), 1):
            row.update(
                {
                    "summary_id": f"sum_seed_{period.replace('-', '')}_{index}",
                    "statement_id": statement_id,
                    "property_key": property_key(row["property_name"]),
                    "period_start": parsed.period_start,
                    "period_end": parsed.period_end,
                }
            )
            sheets["Property_Summaries"].append(row)
        for index, row in enumerate(clean_records(parsed.work_orders), 1):
            row.update(
                {
                    "work_order_id": f"wo_seed_{period.replace('-', '')}_{index}",
                    "statement_id": statement_id,
                    "property_key": property_key(row["property_name"]),
                }
            )
            sheets["Work_Orders"].append(row)
        for index, error in enumerate(parsed.errors, 1):
            reviewed_november_credit = period == "2023-11"
            sheets["Import_Errors"].append(
                {
                    "error_id": f"err_seed_{period.replace('-', '')}_{index}",
                    "statement_id": statement_id,
                    "severity": error.get("severity", "warning"),
                    "record_type": error.get("record_type", "statement"),
                    "record_key": error.get("record_key", ""),
                    "message": error.get("message", ""),
                    "original_value": "AppFolio property cash summary",
                    "corrected_value": (
                        "Reviewed: transaction ledger balances to the $250 reserve; "
                        "summary discrepancy is caused by the $309.55 management-fee refund."
                        if reviewed_november_credit
                        else ""
                    ),
                    "status": "approved" if reviewed_november_credit else "open",
                    "created_at": "portable history seed",
                }
            )
    return {
        "seed_version": 1,
        "through": through,
        "period_count": len(parsed_by_period),
        "source_note": "Normalized from owner-supplied AppFolio monthly statements; PDFs are not bundled.",
        "skipped_reports": skipped,
        "sheets": sheets,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--through", default="2026-06")
    args = parser.parse_args()
    payload = build(args.source_dir, args.through)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Wrote {payload['period_count']} periods and "
        f"{len(payload['sheets']['Transactions'])} transactions to {args.output}"
    )


if __name__ == "__main__":
    main()
