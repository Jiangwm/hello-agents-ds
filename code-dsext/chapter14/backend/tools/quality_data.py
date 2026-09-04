from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from math import isfinite
from typing import assert_never

from backend.models import EvidenceRef
from backend.services.evidence import EvidenceLedger
from backend.tools.quality_types import (
    MissingDataFinding,
    NegativeResultFinding,
    QualityFinding,
    QualityQuery,
    QualityRow,
    QualityToolResult,
)
from backend.tools.workspace import CsvReadResult, ReadOnlyWorkspace


class QualityData:
    def __init__(self, workspace: ReadOnlyWorkspace, ledger: EvidenceLedger) -> None:
        self.workspace = workspace
        self.ledger = ledger

    def load_quality(
        self, query: QualityQuery, evidence_id: str
    ) -> tuple[tuple[QualityRow, ...], EvidenceRef] | QualityToolResult:
        raw = self.workspace.read_csv("quality_batches.csv")
        required = frozenset(
            {"tenant_id", "line_id", "batch_id", "observed_at", "defect_count",
             "units_inspected", "defect_rate", "process_window_id"}
        )
        columns = frozenset(raw.rows[0]) if raw.rows else frozenset()
        product_columns = tuple(
            column for column in ("product_id", "defect_type") if column in columns
        )
        selected = tuple(
            row for row in raw.rows
            if row.get("tenant_id") == query.tenant_id
            and row.get("line_id") == query.line_id
            and any(row.get(column) == query.product for column in product_columns)
        )
        scoped = replace(
            raw,
            query={
                "tenant_id": query.tenant_id,
                "line_id": query.line_id,
                "product_or_defect": query.product,
                "filter_columns": ",".join(product_columns),
            },
            rows=selected,
            no_result=not selected,
        )
        evidence = self.record(scoped, query, evidence_id)
        if not raw.rows:
            return self.missing(
                MissingDataFinding(raw.resource, (), "empty source"), (evidence,)
            )
        missing = tuple(sorted(required - columns))
        if not product_columns:
            missing += ("product_id|defect_type",)
        if missing:
            return self.missing(
                MissingDataFinding(raw.resource, missing, "missing critical columns"),
                (evidence,),
            )
        if not selected:
            return self.result(
                NegativeResultFinding(raw.resource, "valid filters matched no rows"),
                (evidence,),
                ("product_id and defect_type are accepted filter dimensions",),
            )
        parsed = self._parse_rows(raw.resource, selected, evidence)
        match parsed:
            case QualityToolResult():
                return parsed
            case tuple() as rows:
                pass
            case unreachable:
                assert_never(unreachable)
        in_window = tuple(
            sorted(
                (row for row in rows if query.start <= row.observed_at <= query.end),
                key=lambda row: row.observed_at,
            )
        )
        if not in_window:
            return self.result(
                NegativeResultFinding(raw.resource, "valid time window matched no rows"),
                (evidence,),
                ("product_id and defect_type are accepted filter dimensions",),
            )
        return in_window, evidence

    def _parse_rows(
        self,
        resource: str,
        rows: tuple[dict[str, str], ...],
        evidence: EvidenceRef,
    ) -> tuple[QualityRow, ...] | QualityToolResult:
        parsed: list[QualityRow] = []
        try:
            for row in rows:
                observed_at = datetime.fromisoformat(row["observed_at"])
                defects = int(row["defect_count"])
                inspected = int(row["units_inspected"])
                defect_rate = float(row["defect_rate"])
                if inspected <= 0 or defects < 0 or not isfinite(defect_rate):
                    return self.missing(
                        MissingDataFinding(
                            resource, (), "invalid numerator or denominator"
                        ),
                        (evidence,),
                    )
                if abs(defects / inspected - defect_rate) > 1e-12:
                    return self.missing(
                        MissingDataFinding(
                            resource, (), "invalid numerator or denominator"
                        ),
                        (evidence,),
                    )
                parsed.append(
                    QualityRow(
                        observed_at=observed_at,
                        defects=defects,
                        inspected=inspected,
                        process_window_id=row["process_window_id"],
                    )
                )
        except (KeyError, ValueError):
            return self.missing(
                MissingDataFinding(
                    resource, (), "invalid datetime or numeric value"
                ),
                (evidence,),
            )
        return tuple(parsed)

    def record(
        self, result: CsvReadResult, query: QualityQuery, evidence_id: str
    ) -> EvidenceRef:
        evidence = self.ledger.record(
            result, tool_call_id=query.tool_call_id, evidence_id=evidence_id
        ).model_copy(update={"retrieved_at": query.retrieved_at})
        self.ledger.repository.replace_or_upsert(
            "evidence", self.ledger.run_id, evidence.evidence_id,
            evidence.model_dump(mode="json"),
        )
        return evidence

    @staticmethod
    def process_values(
        rows: tuple[dict[str, str], ...]
    ) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]] | MissingDataFinding:
        control: list[tuple[float, float, float]] = []
        affected: list[tuple[float, float, float]] = []
        try:
            for row in rows:
                values = (
                    float(row["pressure_kpa"]), float(row["speed_mpm"]),
                    float(row["temperature_c"]),
                )
                if not all(isfinite(value) for value in values):
                    return MissingDataFinding(
                        "process_timeseries.csv", (), "non-finite numeric value"
                    )
                status = row["parameter_status"]
                if status not in frozenset({"stable", "drift_observed"}):
                    return MissingDataFinding(
                        "process_timeseries.csv", (), "invalid parameter status"
                    )
                (affected if status == "drift_observed" else control).append(values)
        except (KeyError, ValueError):
            return MissingDataFinding(
                "process_timeseries.csv", (), "invalid numeric value"
            )
        return control, affected

    @staticmethod
    def result(
        finding: QualityFinding,
        evidence: tuple[EvidenceRef, ...],
        assumptions: tuple[str, ...],
    ) -> QualityToolResult:
        return QualityToolResult(
            finding=finding,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            evidence=evidence,
            data_quality=("manifest SHA-256 verified",),
            assumptions=assumptions,
        )

    @classmethod
    def missing(
        cls, finding: MissingDataFinding, evidence: tuple[EvidenceRef, ...]
    ) -> QualityToolResult:
        return replace(cls.result(finding, evidence, ()), todo_status="blocked")
