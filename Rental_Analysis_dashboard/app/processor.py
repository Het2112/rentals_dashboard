"""Application services for imports, user entries, and metric refreshes."""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .finance import calculate_monthly_metrics
from .parser import AppFolioParser, PARSER_VERSION, ParsedStatement
from .schema import FINANCIAL_CLASSIFICATIONS
from .workbook import WorkbookStore


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


class PortfolioManager:
    def __init__(self, excel_path: Path):
        self.store = WorkbookStore(Path(excel_path))
        self.statement_dir = self.store.path.parent / "statements"
        self.statement_dir.mkdir(parents=True, exist_ok=True)

    def analyze_statement(self, pdf_path: Path) -> tuple[ParsedStatement, str, int]:
        parsed = AppFolioParser(pdf_path).parse()
        registry = self.store.read("Statements")
        if (
            not registry.empty
            and parsed.sha256 in registry["sha256"].astype(str).values
        ):
            return parsed, "duplicate", 0
        same_period = (
            registry[
                (registry["period_start"].astype(str) == parsed.period_start)
                & (registry["period_end"].astype(str) == parsed.period_end)
                & (registry["status"].astype(str) == "active")
            ]
            if not registry.empty
            else pd.DataFrame()
        )
        max_revision = pd.to_numeric(
            same_period.get("revision", pd.Series(dtype=float)), errors="coerce"
        ).max()
        revision = int(max_revision if pd.notna(max_revision) else 0) + 1
        return parsed, "revision" if not same_period.empty else "new", revision

    def _property_mapping(
        self, names: list[str]
    ) -> tuple[dict[str, str], pd.DataFrame]:
        properties = self.store.read("Properties")
        mapping = {}
        for name in names:
            normalized = _normalized(name)
            match_id = None
            for prop in properties.to_dict("records"):
                tokens = [
                    _normalized(prop.get("name", "")),
                    _normalized(prop.get("address", "")),
                ]
                if any(
                    token and (token in normalized or normalized in token)
                    for token in tokens
                ):
                    match_id = prop["property_id"]
                    break
            if not match_id:
                match_id = f"prop_{hashlib.sha1(normalized.encode()).hexdigest()[:10]}"
                properties = pd.concat(
                    [
                        properties,
                        pd.DataFrame(
                            [
                                {
                                    "property_id": match_id,
                                    "name": name,
                                    "address": "",
                                    "property_type": "Needs review",
                                    "notes": "Created automatically from statement import",
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            mapping[name] = match_id
        return mapping, properties

    def commit_statement(
        self, parsed: ParsedStatement, source_name: str, allow_revision: bool = False
    ) -> dict:
        _, state, revision = self.analyze_statement(parsed.source_path)
        if state == "duplicate":
            return {
                "status": "duplicate",
                "message": "This exact statement has already been imported.",
            }
        if state == "revision" and not allow_revision:
            raise ValueError(
                "A statement for this period already exists; revision confirmation is required"
            )
        statement_id = _id("stmt")
        names = list(
            parsed.summaries.get("property_name", pd.Series(dtype=str))
            .dropna()
            .unique()
        )
        mapping, properties = self._property_mapping(names)
        period = pd.to_datetime(parsed.period_start)
        year_dir = self.statement_dir / str(period.year)
        year_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{period:%Y-%m}_AppFolio_Owner_Statement_{parsed.period_start}_to_{parsed.period_end}"
        filename = f"{stem}{f'_v{revision}' if revision > 1 else ''}.pdf"
        destination = year_dir / filename
        shutil.copy2(parsed.source_path, destination)
        registry = self.store.read("Statements")
        tx_existing = self.store.read("Transactions")
        sum_existing = self.store.read("Property_Summaries")
        wo_existing = self.store.read("Work_Orders")
        if state == "revision":
            active = registry[
                (registry["period_start"].astype(str) == parsed.period_start)
                & (registry["period_end"].astype(str) == parsed.period_end)
                & (registry["status"].astype(str) == "active")
            ]
            old_ids = set(active["statement_id"].astype(str))
            registry.loc[
                registry["statement_id"].astype(str).isin(old_ids), "status"
            ] = "superseded"
            tx_existing = tx_existing[
                ~tx_existing["statement_id"].astype(str).isin(old_ids)
            ]
            sum_existing = sum_existing[
                ~sum_existing["statement_id"].astype(str).isin(old_ids)
            ]
            wo_existing = wo_existing[
                ~wo_existing["statement_id"].astype(str).isin(old_ids)
            ]
        tx = parsed.transactions.copy()
        tx["statement_id"] = statement_id
        tx["property_id"] = tx["property_name"].map(mapping)
        units = self.store.read("Units")

        def match_unit(row):
            candidates = units[
                units["property_id"].astype(str) == str(row["property_id"])
            ]
            description = _normalized(row.get("description", ""))
            for unit in candidates.to_dict("records"):
                token = _normalized(unit.get("unit_name", ""))
                if token and token in description:
                    return unit["unit_id"]
            return ""

        tx["unit_id"] = tx.apply(match_unit, axis=1)
        tx["period_start"], tx["period_end"] = parsed.period_start, parsed.period_end
        occurrences = {}
        ids = []
        for row in tx.to_dict("records"):
            raw = "|".join(
                str(row.get(k, ""))
                for k in (
                    "property_id",
                    "date",
                    "reference",
                    "description",
                    "cash_in",
                    "cash_out",
                )
            )
            occurrences[raw] = occurrences.get(raw, 0) + 1
            ids.append(
                "txn_"
                + hashlib.sha256(
                    f"{parsed.sha256}|{raw}|{occurrences[raw]}".encode()
                ).hexdigest()[:20]
            )
        tx["transaction_id"] = ids
        summaries = parsed.summaries.copy()
        summaries["statement_id"] = statement_id
        summaries["property_id"] = summaries["property_name"].map(mapping)
        summaries["period_start"], summaries["period_end"] = (
            parsed.period_start,
            parsed.period_end,
        )
        summaries["summary_id"] = [_id("sum") for _ in range(len(summaries))]
        work_orders = parsed.work_orders.copy()
        if not work_orders.empty:
            work_orders["statement_id"] = statement_id
            work_orders["property_id"] = work_orders["property_name"].apply(
                lambda value: next(
                    (
                        pid
                        for name, pid in mapping.items()
                        if _normalized(value) in _normalized(name)
                        or _normalized(name) in _normalized(value)
                    ),
                    "",
                )
            )
            work_orders["work_order_id"] = [_id("wo") for _ in range(len(work_orders))]
        errors = []
        now = datetime.now(timezone.utc).isoformat()
        for error in parsed.errors:
            errors.append(
                {
                    "error_id": _id("err"),
                    "statement_id": statement_id,
                    "severity": error.get("severity", "warning"),
                    "record_type": error.get("record_type", "statement"),
                    "record_key": error.get("record_key", ""),
                    "message": error.get("message", ""),
                    "original_value": "",
                    "corrected_value": "",
                    "status": "open",
                    "created_at": now,
                }
            )
        registry = pd.concat(
            [
                registry,
                pd.DataFrame(
                    [
                        {
                            "statement_id": statement_id,
                            "period_start": parsed.period_start,
                            "period_end": parsed.period_end,
                            "original_filename": source_name,
                            "local_filename": str(
                                destination.relative_to(self.store.path.parent)
                            ),
                            "sha256": parsed.sha256,
                            "file_size": parsed.source_path.stat().st_size,
                            "imported_at": now,
                            "revision": revision,
                            "status": "active",
                            "parser_version": PARSER_VERSION,
                            "transaction_count": len(tx),
                            "validation_status": parsed.validation_status,
                            "message": f"{len(errors)} validation warning(s)"
                            if errors
                            else "Validated",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        replacements = {
            "Properties": properties,
            "Statements": registry,
            "Transactions": pd.concat([tx_existing, tx], ignore_index=True),
            "Property_Summaries": pd.concat(
                [sum_existing, summaries], ignore_index=True
            ),
            "Work_Orders": pd.concat([wo_existing, work_orders], ignore_index=True),
        }
        if errors:
            replacements["Import_Errors"] = pd.concat(
                [self.store.read("Import_Errors"), pd.DataFrame(errors)],
                ignore_index=True,
            )
        self.store.update(replacements)
        self.refresh_metrics()
        return {
            "status": state,
            "statement_id": statement_id,
            "transactions": len(tx),
            "warnings": len(errors),
            "filename": filename,
        }

    def add_external_expenses(self, records: list[dict]) -> tuple[int, int]:
        existing = self.store.read("External_Expenses")
        known = (
            set(existing["dedupe_key"].dropna().astype(str))
            if not existing.empty
            else set()
        )
        accepted, duplicates = [], 0
        for record in records:
            raw = "|".join(
                str(record.get(key, "")).strip().lower()
                for key in (
                    "date",
                    "property_id",
                    "unit_id",
                    "vendor",
                    "description",
                    "amount",
                    "financial_classification",
                )
            )
            key = hashlib.sha256(raw.encode()).hexdigest()
            if key in known:
                duplicates += 1
                continue
            amount = float(record.get("amount") or 0)
            if (
                amount <= 0
                or not record.get("property_id")
                or not record.get("financial_classification")
                or not str(record.get("description", "")).strip()
            ):
                raise ValueError(
                    "Each expense requires a property, description, positive amount, and financial classification"
                )
            if record["financial_classification"] not in FINANCIAL_CLASSIFICATIONS:
                raise ValueError(
                    f"Unknown financial classification: {record['financial_classification']}"
                )
            try:
                pd.to_datetime(record.get("date"), errors="raise")
            except Exception as exc:
                raise ValueError(f"Invalid expense date: {record.get('date')}") from exc
            property_ids = set(
                self.store.read("Properties")["property_id"].dropna().astype(str)
            )
            if str(record["property_id"]) not in property_ids:
                raise ValueError(f"Unknown property_id: {record['property_id']}")
            accepted.append(
                {
                    **record,
                    "expense_id": _id("exp"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source": record.get("source", "UI"),
                    "dedupe_key": key,
                }
            )
            known.add(key)
        if accepted:
            self.store.update(
                {
                    "External_Expenses": pd.concat(
                        [existing, pd.DataFrame(accepted)], ignore_index=True
                    )
                }
            )
            self.refresh_metrics()
        return len(accepted), duplicates

    def refresh_metrics(self) -> pd.DataFrame:
        metrics = calculate_monthly_metrics(
            self.store.read("Transactions"),
            self.store.read("External_Expenses"),
            self.store.read("Properties"),
            self.store.read("Loans"),
            self.store.read("Property_Values"),
        )
        self.store.update({"Monthly_Metrics": metrics})
        self.store.refresh_excel_dashboards(metrics)
        return metrics

    def remap_units(self) -> int:
        transactions, units = self.store.read("Transactions"), self.store.read("Units")
        if transactions.empty or units.empty:
            return 0
        changed = 0
        for index, row in transactions.iterrows():
            candidates = units[
                units["property_id"].astype(str) == str(row["property_id"])
            ]
            description = _normalized(row.get("description", ""))
            match = next(
                (
                    unit["unit_id"]
                    for unit in candidates.to_dict("records")
                    if _normalized(unit.get("unit_name", ""))
                    and _normalized(unit.get("unit_name", "")) in description
                ),
                "",
            )
            if match and str(row.get("unit_id") or "") != str(match):
                transactions.at[index, "unit_id"] = match
                changed += 1
        if changed:
            self.store.update({"Transactions": transactions})
        return changed

    # Backward-compatible API.
    def append_transactions(self, df: pd.DataFrame) -> None:
        self.store.update(
            {
                "Transactions": pd.concat(
                    [self.store.read("Transactions"), df], ignore_index=True
                )
            }
        )
