from __future__ import annotations

from dataclasses import replace
from typing import assert_never

from backend.models import EvidenceRef
from backend.services.evidence import EvidenceLedger
from backend.tools.quality_data import QualityData
from backend.tools.quality_types import (
    ControlComparison,
    MissingDataFinding,
    NegativeResultFinding,
    OnsetFinding,
    ParameterDrift,
    ProcessDriftFinding,
    QualityFinding,
    QualityOperation,
    QualityQuery,
    QualityRow,
    QualityTodo,
    QualityToolResult,
    RateWindow,
)
from backend.tools.workspace import ReadOnlyWorkspace


class QualityAnalysisTool:
    def __init__(self, workspace: ReadOnlyWorkspace, ledger: EvidenceLedger) -> None:
        self._workspace = workspace
        self._data = QualityData(workspace, ledger)

    def detect_onset(self, query: QualityQuery) -> QualityToolResult:
        loaded = self._data.load_quality(query, query.evidence_id)
        match loaded:
            case QualityToolResult():
                return loaded
            case (rows, evidence):
                onset = self._onset_index(rows)
                if onset is None:
                    finding: QualityFinding = NegativeResultFinding(
                        "quality_batches.csv", "no significant increase"
                    )
                else:
                    finding = OnsetFinding(
                        observed_at=rows[onset].observed_at,
                        baseline=self._rate(rows[:onset]),
                        affected=self._rate(rows[onset:]),
                    )
                return self._data.result(
                    finding,
                    (evidence,),
                    ("defect_type is the product proxy when product_id is absent",),
                )
            case unreachable:
                assert_never(unreachable)

    def compare_control(self, query: QualityQuery) -> QualityToolResult:
        result = self.detect_onset(query)
        match result.finding:
            case OnsetFinding(baseline=control, affected=affected):
                comparison = ControlComparison(
                    control=control,
                    affected=affected,
                    rate_ratio=affected.defect_rate / control.defect_rate,
                )
                return replace(result, finding=comparison)
            case MissingDataFinding() | NegativeResultFinding():
                return result
            case ControlComparison() | ProcessDriftFinding():
                return result
            case unreachable:
                assert_never(unreachable)

    def analyze_process_drift(self, query: QualityQuery) -> QualityToolResult:
        loaded = self._data.load_quality(query, f"{query.evidence_id}-quality")
        match loaded:
            case QualityToolResult():
                return loaded
            case (quality_rows, quality_evidence):
                process_ids = frozenset(row.process_window_id for row in quality_rows)
            case unreachable:
                assert_never(unreachable)
        raw = self._workspace.read_csv("process_timeseries.csv")
        required = frozenset(
            {
                "tenant_id", "line_id", "process_window_id", "timestamp",
                "temperature_c", "pressure_kpa", "speed_mpm", "parameter_status",
            }
        )
        columns = frozenset(raw.rows[0]) if raw.rows else frozenset()
        process_evidence = self._data.record(
            replace(
                raw,
                query={
                    "tenant_id": query.tenant_id,
                    "line_id": query.line_id,
                    "product": query.product,
                },
            ),
            query,
            f"{query.evidence_id}-process",
        )
        if not raw.rows:
            return self._data.missing(
                MissingDataFinding("process_timeseries.csv", (), "empty source"),
                (process_evidence,),
            )
        missing = tuple(sorted(required - columns))
        if missing:
            return self._data.missing(
                MissingDataFinding(
                    "process_timeseries.csv", missing, "missing critical columns"
                ),
                (process_evidence,),
            )
        rows = tuple(
            row for row in raw.rows
            if row.get("tenant_id") == query.tenant_id
            and row.get("line_id") == query.line_id
            and row.get("process_window_id") in process_ids
        )
        if not rows:
            negative = replace(raw, rows=(), no_result=True)
            negative_evidence = self._data.record(
                negative, query, f"{query.evidence_id}-process"
            )
            return self._data.result(
                NegativeResultFinding(
                    "process_timeseries.csv", "valid filters matched no rows"
                ),
                (quality_evidence, negative_evidence),
                ("parameter_status defines normal control",),
            )
        values = self._data.process_values(rows)
        match values:
            case MissingDataFinding() as finding:
                return self._data.missing(
                    finding,
                    (quality_evidence, process_evidence),
                )
            case (control, affected):
                if not control or not affected:
                    return self._data.missing(
                        MissingDataFinding(
                            "process_timeseries.csv", (),
                            "normal or drift control group absent",
                        ),
                        (quality_evidence, process_evidence),
                    )
                parameters = tuple(
                    ParameterDrift(
                        parameter=name,
                        control_mean=sum(base) / len(base),
                        affected_mean=sum(changed) / len(changed),
                        drift=sum(changed) / len(changed) - sum(base) / len(base),
                    )
                    for name, base, changed in zip(
                        ("pressure_kpa", "speed_mpm", "temperature_c"),
                        zip(*control, strict=True),
                        zip(*affected, strict=True),
                        strict=True,
                    )
                )
                return self._data.result(
                    ProcessDriftFinding(parameters),
                    (quality_evidence, process_evidence),
                    ("parameter_status defines normal control",),
                )
            case unreachable:
                assert_never(unreachable)

    def execute(self, todo: QualityTodo) -> QualityToolResult:
        match todo.operation:
            case QualityOperation.DETECT_ONSET:
                return self.detect_onset(todo.query)
            case QualityOperation.COMPARE_CONTROL:
                return self.compare_control(todo.query)
            case QualityOperation.ANALYZE_PROCESS_DRIFT:
                return self.analyze_process_drift(todo.query)
            case unreachable:
                assert_never(unreachable)

    @staticmethod
    def _rate(rows: tuple[QualityRow, ...]) -> RateWindow:
        numerator = sum(row.defects for row in rows)
        denominator = sum(row.inspected for row in rows)
        return RateWindow(
            rows[0].observed_at, rows[-1].observed_at,
            numerator, denominator, numerator / denominator,
        )

    @classmethod
    def _onset_index(cls, rows: tuple[QualityRow, ...]) -> int | None:
        for index in range(3, len(rows)):
            baseline = cls._rate(rows[:index])
            affected = cls._rate(rows[index:])
            if affected.defect_rate >= 2 * baseline.defect_rate and (
                affected.defect_rate - baseline.defect_rate >= 0.005
            ):
                return index
        return None

__all__ = [
    "ControlComparison", "MissingDataFinding", "NegativeResultFinding",
    "OnsetFinding", "ParameterDrift", "ProcessDriftFinding",
    "QualityAnalysisTool", "QualityOperation", "QualityQuery", "QualityTodo",
    "QualityToolResult", "RateWindow",
]
