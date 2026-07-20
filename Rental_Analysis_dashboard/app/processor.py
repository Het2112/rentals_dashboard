"""Application services for imports, user entries, and metric refreshes."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .finance import calculate_monthly_metrics, monthly_payment
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

    def apply_historical_seed(self, seed_path: Path) -> dict:
        """Idempotently preload owner-provided cumulative property baselines."""
        seed_path = Path(seed_path)
        if not seed_path.exists():
            return {"added": 0, "updated_properties": 0}
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        as_of_date = seed["as_of_date"]
        properties = self.store.read("Properties")
        loans = self.store.read("Loans")
        baselines = self.store.read("Historical_Baselines")
        added = updated = 0
        baseline_changed = False
        for item in seed["properties"]:
            calculated = round(
                _safe_float(item["total_rent"])
                - _safe_float(item["management_fees"])
                - _safe_float(item["maintenance"])
                - _safe_float(item["renovations"])
                - _safe_float(item["utility_deficit"])
                - _safe_float(item["total_debt_tax_insurance"]),
                2,
            )
            if abs(calculated - _safe_float(item["cash_profit_loss"])) > 0.02:
                raise ValueError(
                    f"Historical seed does not reconcile for {item['display_name']}"
                )
            terms = [_normalized(term) for term in item.get("match_terms", [])]
            match_index = None
            for index, prop in properties.iterrows():
                searchable = _normalized(
                    f"{prop.get('name', '')} {prop.get('address', '')}"
                )
                if any(term and term in searchable for term in terms):
                    match_index = index
                    break
            if match_index is None:
                property_id = f"prop_{item['property_key']}"
                properties = pd.concat(
                    [
                        properties,
                        pd.DataFrame(
                            [
                                {
                                    "property_id": property_id,
                                    "name": item["display_name"],
                                    "property_type": item["property_type"],
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
                match_index = properties.index[-1]
            property_id = str(properties.at[match_index, "property_id"])
            seeded_fields = {
                "purchase_date": item["purchase_date"],
                "purchase_price": item["purchase_price"],
                "closing_costs": item["closing_costs"],
                "down_payment": item["down_payment"],
                "initial_renovations": item["renovations"],
                "property_type": item["property_type"],
                "financing_type": "Mortgage",
            }
            property_changed = False
            for field, value in seeded_fields.items():
                current = properties.at[match_index, field]
                if _is_missing(current) or (
                    field == "property_type" and current == "Needs review"
                ):
                    properties.at[match_index, field] = value
                    property_changed = True
            updated += int(property_changed)
            property_loans = loans["property_id"].astype(str) == property_id
            if property_loans.any():
                loan_index = loans[property_loans].index[0]
                if _is_missing(loans.at[loan_index, "interest_rate"]):
                    loans.at[loan_index, "interest_rate"] = item["interest_rate"]
                if _is_missing(loans.at[loan_index, "origination_date"]):
                    loans.at[loan_index, "origination_date"] = item["purchase_date"]
            else:
                loans = pd.concat(
                    [
                        loans,
                        pd.DataFrame(
                            [
                                {
                                    "loan_id": _id("loan"),
                                    "property_id": property_id,
                                    "origination_date": item["purchase_date"],
                                    "interest_rate": item["interest_rate"],
                                    "notes": "Interest rate seeded from June 2025 owner data; loan amount, term, payment, and balance still require confirmation.",
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            baseline_id = f"baseline_{item['property_key']}_{as_of_date}"
            if baseline_id not in set(baselines["baseline_id"].dropna().astype(str)):
                baselines = pd.concat(
                    [
                        baselines,
                        pd.DataFrame(
                            [
                                {
                                    "baseline_id": baseline_id,
                                    "property_id": property_id,
                                    "property_key": item["property_key"],
                                    "as_of_date": as_of_date,
                                    "statement_coverage_start": item.get(
                                        "statement_coverage_start",
                                        item["purchase_date"],
                                    ),
                                    "total_rent": item["total_rent"],
                                    "management_fees": item["management_fees"],
                                    "maintenance": item["maintenance"],
                                    "statement_maintenance": item["maintenance"]
                                    - item["owner_paid_maintenance_estimate"],
                                    "statement_capex": item["renovations"],
                                    "adjusted_maintenance": item["maintenance"],
                                    "adjusted_capex": item["renovations"],
                                    "owner_paid_maintenance": item[
                                        "owner_paid_maintenance_estimate"
                                    ],
                                    "reconciliation_status": "Estimated from owner notes; pending historical statements",
                                    "renovations": item["renovations"],
                                    "utility_deficit": item["utility_deficit"],
                                    "mortgage_principal": item["mortgage_principal"],
                                    "mortgage_interest": item["mortgage_interest"],
                                    "tax_insurance_escrow": item[
                                        "tax_insurance_escrow"
                                    ],
                                    "total_debt_tax_insurance": item[
                                        "total_debt_tax_insurance"
                                    ],
                                    "cash_profit_loss": item["cash_profit_loss"],
                                    "adjusted_cash_profit_loss": item[
                                        "cash_profit_loss"
                                    ],
                                    "notes": item["notes"],
                                    "source": seed["source"],
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
                added += 1
                baseline_changed = True
            else:
                baseline_mask = baselines["baseline_id"].astype(str) == baseline_id
                coverage = baselines.loc[
                    baseline_mask, "statement_coverage_start"
                ].iloc[0]
                if _is_missing(coverage):
                    baselines.loc[
                        baseline_mask, "statement_coverage_start"
                    ] = item.get("statement_coverage_start", item["purchase_date"])
                    baseline_changed = True
        if added or updated or baseline_changed:
            self.store.update(
                {
                    "Properties": properties,
                    "Loans": loans,
                    "Historical_Baselines": baselines,
                }
            )
            self.reconcile_historical_maintenance(refresh=False)
            self.refresh_metrics()
        return {"added": added, "updated_properties": updated}

    def apply_statement_history_seed(self, seed_path: Path) -> dict:
        """Idempotently load normalized monthly history bundled with the app."""
        seed_path = Path(seed_path)
        if not seed_path.exists():
            return {"statements_added": 0, "transactions_added": 0}
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_version = str(payload.get("seed_version", 1))
        settings = self.store.read("App_Settings")
        applied = settings[settings["key"].astype(str) == "statement_history_seed_version"]
        if not applied.empty and str(applied.iloc[-1]["value"]) == seed_version:
            return {"statements_added": 0, "transactions_added": 0}
        sheets = payload.get("sheets", {})
        baselines = self.store.read("Historical_Baselines")
        property_map = {
            str(row["property_key"]): str(row["property_id"])
            for row in baselines.to_dict("records")
        }
        if not property_map:
            raise ValueError("Historical properties must be seeded before statements")

        existing_statements = self.store.read("Statements")
        active_periods = {
            (str(row.get("period_start")), str(row.get("period_end")))
            for row in existing_statements.to_dict("records")
            if str(row.get("status")) == "active"
        }
        existing_hashes = set(existing_statements["sha256"].dropna().astype(str))
        incoming_statements = []
        statement_ids = set(existing_statements["statement_id"].dropna().astype(str))
        accepted_ids = set()
        for row in sheets.get("Statements", []):
            statement_id = str(row["statement_id"])
            if statement_id in statement_ids:
                mask = existing_statements["statement_id"].astype(str) == statement_id
                for key, value in row.items():
                    if key in existing_statements.columns:
                        existing_statements.loc[mask, key] = value
                accepted_ids.add(statement_id)
                continue
            period = (str(row.get("period_start")), str(row.get("period_end")))
            if str(row.get("sha256")) in existing_hashes or period in active_periods:
                continue
            incoming_statements.append(row)
            accepted_ids.add(statement_id)

        replacements = {"Statements": existing_statements}
        if incoming_statements:
            replacements["Statements"] = pd.concat(
                [existing_statements, pd.DataFrame(incoming_statements)],
                ignore_index=True,
            )
        for sheet in (
            "Transactions",
            "Property_Summaries",
            "Work_Orders",
            "Import_Errors",
        ):
            incoming = [
                dict(row)
                for row in sheets.get(sheet, [])
                if str(row.get("statement_id")) in accepted_ids
            ]
            for row in incoming:
                key = row.pop("property_key", None)
                if key is not None:
                    if str(key) not in property_map:
                        raise ValueError(f"Unknown statement seed property: {key}")
                    row["property_id"] = property_map[str(key)]
            if incoming:
                existing = self.store.read(sheet)
                id_column = {
                    "Transactions": "transaction_id",
                    "Property_Summaries": "summary_id",
                    "Work_Orders": "work_order_id",
                    "Import_Errors": "error_id",
                }[sheet]
                new_rows = []
                known = set(existing[id_column].dropna().astype(str))
                for row in incoming:
                    record_id = str(row.get(id_column))
                    if record_id in known:
                        mask = existing[id_column].astype(str) == record_id
                        for key, value in row.items():
                            if key in existing.columns:
                                existing.loc[mask, key] = value
                    else:
                        new_rows.append(row)
                        known.add(record_id)
                replacements[sheet] = (
                    pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
                    if new_rows
                    else existing
                )
        settings = settings[settings["key"].astype(str) != "statement_history_seed_version"]
        replacements["App_Settings"] = pd.concat(
            [
                settings,
                pd.DataFrame(
                    [
                        {
                            "key": "statement_history_seed_version",
                            "value": seed_version,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        self.store.update(replacements)
        self.reconcile_historical_maintenance(refresh=False)
        self.refresh_metrics()
        return {
            "statements_added": len(incoming_statements),
            "transactions_added": sum(
                1
                for row in sheets.get("Transactions", [])
                if str(row.get("statement_id"))
                in {str(item["statement_id"]) for item in incoming_statements}
            ),
        }

    def reconcile_historical_maintenance(self, refresh: bool = True) -> int:
        """Reconcile cumulative maintenance with statement-derived maintenance.

        Until every expected historical statement is present, the owner-provided
        note estimate remains in effect and is clearly marked provisional.
        """
        baselines = self.store.read("Historical_Baselines")
        if baselines.empty:
            return 0
        transactions = self.store.read("Transactions")
        statements = self.store.read("Statements")
        properties = self.store.read("Properties")
        external = self.store.read("External_Expenses")
        active = (
            statements[statements["status"].astype(str) == "active"]
            if not statements.empty
            else statements
        )
        imported_periods = set()
        for statement in active.to_dict("records"):
            try:
                imported_periods.add(
                    str(pd.Period(pd.to_datetime(statement["period_end"]), freq="M"))
                )
            except (TypeError, ValueError):
                continue
        property_map = (
            properties.set_index("property_id").to_dict("index")
            if not properties.empty
            else {}
        )
        generated_ids = {
            f"hist_owner_maintenance_{key}"
            for key in baselines["property_key"].dropna().astype(str)
        }
        external = external[~external["expense_id"].astype(str).isin(generated_ids)]
        generated = []
        changed = 0
        for index, baseline in baselines.iterrows():
            property_id = str(baseline["property_id"])
            cutoff = pd.to_datetime(baseline["as_of_date"])
            purchase = pd.to_datetime(
                property_map.get(property_id, {}).get("purchase_date"), errors="coerce"
            )
            coverage_start = pd.to_datetime(
                baseline.get("statement_coverage_start"), errors="coerce"
            )
            expected_start = coverage_start if pd.notna(coverage_start) else purchase
            expected = (
                {
                    str(period)
                    for period in pd.period_range(expected_start, cutoff, freq="M")
                }
                if pd.notna(expected_start)
                else set()
            )
            missing_periods = sorted(expected - imported_periods)
            subset = transactions[
                transactions["property_id"].astype(str) == property_id
            ].copy()
            if not subset.empty:
                subset["date"] = pd.to_datetime(subset["date"], errors="coerce")
                subset = subset[
                    (subset["date"] <= cutoff)
                    & (subset["date"] >= purchase if pd.notna(purchase) else True)
                ]
            observed_maintenance = (
                pd.to_numeric(
                    subset.loc[
                        subset["category"].astype(str) == "Repairs & Maintenance",
                        "cash_out",
                    ],
                    errors="coerce",
                )
                .fillna(0)
                .sum()
                if not subset.empty
                else 0.0
            )
            observed_capex = (
                pd.to_numeric(
                    subset.loc[
                        subset["financial_classification"].astype(str)
                        == "Capital Improvement / CapEx",
                        "cash_out",
                    ],
                    errors="coerce",
                )
                .fillna(0)
                .sum()
                if not subset.empty
                else 0.0
            )
            baseline_maintenance = _safe_float(baseline["maintenance"])
            baseline_capex = _safe_float(baseline["renovations"])
            property_work_total = baseline_maintenance + baseline_capex
            adjusted_capex = max(baseline_capex, observed_capex)
            adjusted_maintenance = max(
                observed_maintenance,
                max(0.0, property_work_total - adjusted_capex),
            )
            cost_adjustment = max(
                0.0,
                adjusted_maintenance + adjusted_capex - property_work_total,
            )
            adjusted_cash_result = (
                _safe_float(baseline["cash_profit_loss"]) - cost_adjustment
            )
            if not missing_periods and expected:
                owner_paid = max(0.0, adjusted_maintenance - observed_maintenance)
                status = "Reconciled"
                if cost_adjustment > 0.02:
                    status += (
                        f"; statement costs added {cost_adjustment:.2f} above prior baseline"
                    )
            else:
                owner_paid = _safe_float(baseline["owner_paid_maintenance"])
                status = (
                    f"Provisional; awaiting {len(missing_periods)} monthly statement(s). "
                    f"Observed statement maintenance: {observed_maintenance:.2f}"
                )
            baselines.at[index, "statement_maintenance"] = observed_maintenance
            baselines.at[index, "statement_capex"] = observed_capex
            baselines.at[index, "adjusted_maintenance"] = adjusted_maintenance
            baselines.at[index, "adjusted_capex"] = adjusted_capex
            baselines.at[index, "adjusted_cash_profit_loss"] = adjusted_cash_result
            baselines.at[index, "owner_paid_maintenance"] = owner_paid
            baselines.at[index, "reconciliation_status"] = status
            if owner_paid > 0.005:
                key = str(baseline["property_key"])
                expense_id = f"hist_owner_maintenance_{key}"
                dedupe_key = hashlib.sha256(expense_id.encode()).hexdigest()
                generated.append(
                    {
                        "expense_id": expense_id,
                        "date": pd.to_datetime(baseline["as_of_date"])
                        .date()
                        .isoformat(),
                        "property_id": property_id,
                        "unit_id": "",
                        "vendor": "Various owner-paid vendors",
                        "description": "Cumulative owner-paid maintenance through June 2025",
                        "amount": owner_paid,
                        "category": "Repairs & Maintenance",
                        "financial_classification": "Maintenance / Operating Expense",
                        "payment_method": "",
                        "notes": status,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source": "Historical baseline reconciliation",
                        "dedupe_key": dedupe_key,
                    }
                )
            changed += 1
        if generated:
            external = pd.concat([external, pd.DataFrame(generated)], ignore_index=True)
        self.store.update(
            {
                "Historical_Baselines": baselines,
                "External_Expenses": external,
            }
        )
        if refresh:
            self.refresh_metrics()
        return changed

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
        self.reconcile_historical_maintenance(refresh=False)
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

    def delete_external_expense(self, expense_id: str) -> None:
        """Delete a user-entered expense while protecting generated history rows."""
        expenses = self.store.read("External_Expenses")
        match = expenses[expenses["expense_id"].astype(str) == str(expense_id)]
        if match.empty:
            raise ValueError("The selected expense no longer exists")
        if str(match.iloc[0].get("source") or "") == "Historical baseline reconciliation":
            raise ValueError(
                "Reconciled historical maintenance cannot be deleted here; it is regenerated from the June 2025 baseline and statements."
            )
        self.store.update(
            {
                "External_Expenses": expenses[
                    expenses["expense_id"].astype(str) != str(expense_id)
                ]
            }
        )
        self.refresh_metrics()

    def save_property_setup(
        self, property_record: dict, loan_record: dict | None
    ) -> str:
        """Create or update a property and its primary mortgage together."""
        properties = self.store.read("Properties")
        loans = self.store.read("Loans")
        property_id = str(property_record.get("property_id") or _id("prop"))
        name = str(property_record.get("name") or "").strip()
        financing = str(property_record.get("financing_type") or "").strip()
        if not name:
            raise ValueError("Property name is required")
        if financing not in {"Cash purchase", "Mortgage"}:
            raise ValueError(
                "Choose whether the property was purchased with cash or a mortgage"
            )
        property_record = {
            **property_record,
            "property_id": property_id,
            "name": name,
        }
        existing = properties["property_id"].astype(str) == property_id
        if existing.any():
            for key, value in property_record.items():
                if key in properties.columns:
                    properties.loc[existing, key] = value
        else:
            properties = pd.concat(
                [properties, pd.DataFrame([property_record])], ignore_index=True
            )
        property_loans = loans["property_id"].astype(str) == property_id
        if financing == "Cash purchase":
            loans = loans[~property_loans]
        else:
            loan_record = dict(loan_record or {})
            principal = _safe_float(loan_record.get("original_principal"))
            years = _safe_float(
                loan_record.get("amortization_years") or loan_record.get("term_years")
            )
            rate_value = loan_record.get("interest_rate")
            if principal <= 0:
                raise ValueError("Mortgage amount must be greater than zero")
            if _is_missing(rate_value):
                raise ValueError("Mortgage interest rate is required")
            if _is_missing(loan_record.get("origination_date")):
                raise ValueError("Mortgage start date is required")
            if years <= 0:
                raise ValueError("Mortgage term must be greater than zero")
            payment = _safe_float(loan_record.get("monthly_payment"))
            if payment <= 0:
                payment = monthly_payment(principal, _safe_float(rate_value), years)
            existing_loan = (
                loans.loc[property_loans].iloc[0].to_dict()
                if property_loans.any()
                else {}
            )
            completed_loan = {
                **existing_loan,
                **loan_record,
                "loan_id": existing_loan.get("loan_id") or _id("loan"),
                "property_id": property_id,
                "monthly_payment": payment,
            }
            loans = loans[~property_loans]
            loans = pd.concat(
                [loans, pd.DataFrame([completed_loan])], ignore_index=True
            )
        self.store.update({"Properties": properties, "Loans": loans})
        self.refresh_metrics()
        return property_id

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

    def refresh_for_calculation_version(self, version: str) -> bool:
        settings = self.store.read("App_Settings")
        current = settings[settings["key"].astype(str) == "calculation_version"]
        if not current.empty and str(current.iloc[0]["value"]) == str(version):
            return False
        self.refresh_metrics()
        settings = settings[settings["key"].astype(str) != "calculation_version"]
        settings = pd.concat(
            [
                settings,
                pd.DataFrame([{"key": "calculation_version", "value": str(version)}]),
            ],
            ignore_index=True,
        )
        self.store.update({"App_Settings": settings})
        return True

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


def _safe_float(value) -> float:
    try:
        return 0.0 if pd.isna(value) else float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_missing(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
