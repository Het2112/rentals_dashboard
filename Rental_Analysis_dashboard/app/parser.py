"""Deterministic parser for AppFolio owner-statement PDF packets."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

PARSER_VERSION = "2.0.0"
MONEY_RE = r"-?\(?\$?[\d,]+\.\d{2}\)?"
SUMMARY_LABELS = {
    "Beginning Balance": "beginning_balance",
    "Cash In": "cash_in",
    "Cash Out": "cash_out",
    "Management Fees": "management_fees",
    "Owner Disbursements": "owner_disbursements",
    "Ending Cash Balance": "ending_balance",
    "Unpaid Bills": "unpaid_bills",
    "Property Reserve": "property_reserve",
    "Work Order Estimates": "work_order_estimates",
    "Net Owner Funds": "net_owner_funds",
}


@dataclass
class ParsedStatement:
    source_path: Path
    sha256: str
    period_start: str
    period_end: str
    transactions: pd.DataFrame
    summaries: pd.DataFrame
    work_orders: pd.DataFrame
    errors: list[dict] = field(default_factory=list)

    @property
    def validation_status(self) -> str:
        return "warning" if self.errors else "valid"


class AppFolioParser:
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)

    def get_file_hash(self) -> str:
        return hashlib.sha256(self.file_path.read_bytes()).hexdigest()

    @staticmethod
    def clean_currency(value) -> float:
        if value is None or pd.isna(value) or str(value).strip() in {"", "--"}:
            return 0.0
        text = str(value).replace("$", "").replace(",", "").replace("\n", "").strip()
        negative = text.startswith("(") and text.endswith(")")
        text = text.replace("(", "").replace(")", "")
        try:
            number = float(text)
            return -number if negative else number
        except ValueError:
            return 0.0

    @staticmethod
    def _period(text: str) -> tuple[str, str]:
        match = re.search(
            r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+-\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",
            text,
        )
        if not match:
            raise ValueError("Could not find the owner-statement period in the PDF")
        start = datetime.strptime(match.group(1), "%b %d, %Y").date().isoformat()
        end = datetime.strptime(match.group(2), "%b %d, %Y").date().isoformat()
        return start, end

    @staticmethod
    def _property_heading(text: str) -> str | None:
        if "Property Cash Summary" not in text:
            return None
        prefix = text.split("Property Cash Summary", 1)[0]
        lines = [line.strip() for line in prefix.splitlines() if line.strip()]
        lines = [line for line in lines if not line.startswith("Property Manager:")]
        if not lines:
            return None
        # Headers sometimes wrap an address across two lines.
        return " ".join(lines).strip()

    @staticmethod
    def _summary(text: str, property_name: str) -> dict:
        section = text.split("Property Cash Summary", 1)[1].split("Transactions", 1)[0]
        result = {"property_name": property_name}
        for label, key in SUMMARY_LABELS.items():
            match = re.search(
                rf"(?m)^\s*{re.escape(label)}\s+({MONEY_RE})\s*$", section
            )
            result[key] = (
                AppFolioParser.clean_currency(match.group(1)) if match else 0.0
            )
        return result

    @staticmethod
    def _category(description: str, transaction_type: str) -> tuple[str, str]:
        value = description.lower()
        if "owner distribution" in value:
            return "Owner Distribution", "Owner Distribution"
        if "transfer " in value:
            return "Transfer", "Owner Distribution"
        if "rent income" in value:
            return "Rent", "Income"
        if "management fee" in value:
            return "Management Fees", "Maintenance / Operating Expense"
        if "water" in value or "utility" in value:
            return "Utilities", "Maintenance / Operating Expense"
        if any(
            word in value for word in ("repair", "maintenance", "pest", "grass", "lawn")
        ):
            return "Repairs & Maintenance", "Maintenance / Operating Expense"
        if "tax" in value:
            return "Taxes", "Maintenance / Operating Expense"
        if "insurance" in value:
            return "Insurance", "Maintenance / Operating Expense"
        if transaction_type.lower().endswith("receipt") or "income" in value:
            return "Other Income", "Income"
        return "Other Operating Expense", "Maintenance / Operating Expense"

    def _transaction_rows(self, page, property_name: str) -> list[dict]:
        rows = []
        for table in page.extract_tables() or []:
            if not table or not table[0]:
                continue
            header = [str(cell or "").replace("\n", " ").strip() for cell in table[0]]
            if "Date" not in header or "Description" not in header:
                continue
            for raw in table[1:]:
                values = [str(cell or "").replace("\n", " ").strip() for cell in raw]
                values += [""] * (8 - len(values))
                if not values[0] or values[0].lower().startswith(("total", "date")):
                    continue
                try:
                    parsed_date = (
                        datetime.strptime(values[0], "%m/%d/%Y").date().isoformat()
                    )
                except ValueError:
                    continue
                description = values[4]
                category, classification = self._category(description, values[2])
                rows.append(
                    {
                        "property_name": property_name,
                        "date": parsed_date,
                        "payee_payer": values[1],
                        "transaction_type": values[2],
                        "reference": values[3],
                        "description": description,
                        "cash_in": self.clean_currency(values[5]),
                        "cash_out": abs(self.clean_currency(values[6])),
                        "balance": self.clean_currency(values[7]),
                        "category": category,
                        "financial_classification": classification,
                        "review_status": "approved",
                    }
                )
        return rows

    @staticmethod
    def _work_order(text: str) -> dict | None:
        number = re.search(r"Work Order #\s+(\S+)", text)
        if not number:
            return None

        def field(label: str) -> str:
            match = re.search(rf"(?m)^\s*{re.escape(label)}\s+([^\n]+)", text)
            return match.group(1).strip() if match else ""

        account = ""
        amount = 0.0
        detail = re.search(rf"(?m)^\s*\d{{4}}:\s*([^\n]+?)\s+(--|{MONEY_RE})\s*$", text)
        if detail:
            account = detail.group(1).strip()
            amount = AppFolioParser.clean_currency(detail.group(2))
        job_match = re.search(r"Job Site\s+([^\n]+)", text)
        job_site = job_match.group(1).strip() if job_match else field("Job Site")
        return {
            "property_name": job_site,
            "work_order_number": number.group(1),
            "status": field("Status"),
            "created_on": field("Created On"),
            "completed_on": field("Completed On"),
            "account": account,
            "amount": amount,
            "over_limit": "Exceeds the maintenance limit" in text,
        }

    def parse(self) -> ParsedStatement:
        if not self.file_path.exists():
            raise FileNotFoundError(self.file_path)
        transactions, summaries, work_orders = [], [], []
        current_property = ""
        with pdfplumber.open(self.file_path) as pdf:
            texts = [(page.extract_text(layout=True) or "") for page in pdf.pages]
            period_start, period_end = self._period("\n".join(texts[:2]))
            for page, text in zip(pdf.pages, texts):
                heading = self._property_heading(text)
                if heading:
                    current_property = heading
                    summaries.append(self._summary(text, current_property))
                if current_property and "Work Order #" not in text:
                    transactions.extend(self._transaction_rows(page, current_property))
            work_order_pages = []
            for text in texts:
                if "Work Order #" in text:
                    if work_order_pages:
                        work_order = self._work_order("\n".join(work_order_pages))
                        if work_order:
                            work_orders.append(work_order)
                    work_order_pages = [text]
                elif work_order_pages:
                    work_order_pages.append(text)
            if work_order_pages:
                work_order = self._work_order("\n".join(work_order_pages))
                if work_order:
                    work_orders.append(work_order)

        tx = pd.DataFrame(transactions)
        summary = pd.DataFrame(summaries)
        orders = pd.DataFrame(work_orders)
        errors = self._validate(tx, summary)
        return ParsedStatement(
            self.file_path,
            self.get_file_hash(),
            period_start,
            period_end,
            tx,
            summary,
            orders,
            errors,
        )

    @staticmethod
    def _validate(transactions: pd.DataFrame, summaries: pd.DataFrame) -> list[dict]:
        errors = []
        if transactions.empty:
            errors.append(
                {
                    "severity": "error",
                    "record_type": "statement",
                    "message": "No transactions extracted",
                }
            )
            return errors
        if summaries.empty:
            errors.append(
                {
                    "severity": "error",
                    "record_type": "statement",
                    "message": "No property summaries extracted",
                }
            )
            return errors
        for summary in summaries.to_dict("records"):
            subset = transactions[
                transactions["property_name"] == summary["property_name"]
            ]
            actual_in = round(
                pd.to_numeric(subset["cash_in"], errors="coerce").fillna(0).sum(), 2
            )
            actual_out = round(
                pd.to_numeric(subset["cash_out"], errors="coerce").fillna(0).sum(), 2
            )
            expected_in = round(float(summary["cash_in"]), 2)
            expected_out = round(
                abs(float(summary["cash_out"]))
                + abs(float(summary["management_fees"]))
                + abs(float(summary["owner_disbursements"])),
                2,
            )
            if (
                abs(actual_in - expected_in) > 0.02
                or abs(actual_out - expected_out) > 0.02
            ):
                errors.append(
                    {
                        "severity": "warning",
                        "record_type": "property_summary",
                        "record_key": summary["property_name"],
                        "message": f"Transactions ({actual_in:.2f} in/{actual_out:.2f} out) do not reconcile with summary ({expected_in:.2f} in/{expected_out:.2f} out)",
                    }
                )
        return errors

    # Backward-compatible convenience used by the original pipeline.
    def parse_and_validate(self) -> pd.DataFrame:
        return self.parse().transactions
