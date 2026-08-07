from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping, Sequence


REQUIRED_SCHEMAS: dict[str, tuple[str, ...]] = {
    "batches.csv": (
        "batch_id",
        "production_date",
        "product_id",
        "line_id",
        "shift",
        "output_qty",
        "mes_received_at",
    ),
    "inspections.csv": (
        "batch_id",
        "inspected_qty",
        "first_pass_qualified_qty",
        "recorded_at",
    ),
    "defects.csv": (
        "batch_id",
        "defect_code",
        "defect_name",
        "defect_qty",
    ),
}

SENSITIVE_COLUMNS = {
    "api_key",
    "customer_id",
    "customer_name",
    "device_key",
    "formula",
    "password",
    "recipe",
    "secret",
    "token",
}


@dataclass(frozen=True)
class InputBundle:
    batches: tuple[dict[str, str], ...]
    inspections: tuple[dict[str, str], ...]
    defects: tuple[dict[str, str], ...]
    source_manifest: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ValidationOutcome:
    status: str
    issues: tuple[str, ...]
    missing_rate: float
    late_record_count: int
    target_batch_count: int
    source_manifest: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "missing_rate": self.missing_rate,
            "late_record_count": self.late_record_count,
            "target_batch_count": self.target_batch_count,
            "source_manifest": list(self.source_manifest),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{path.name} 缺少表头")
        fieldnames = tuple(field.strip() for field in reader.fieldnames)
        rows = tuple(
            {
                key.strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            for row in reader
        )
    return fieldnames, rows


def _parse_date(value: str, field: str, issues: list[str]) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(f"{field} 不是 YYYY-MM-DD 日期：{value or '<空>'}")
        return None


def _parse_datetime(value: str, field: str, issues: list[str]) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        issues.append(f"{field} 不是 ISO 8601 时间：{value or '<空>'}")
        return None


def _parse_nonnegative_int(value: str, field: str, issues: list[str]) -> int | None:
    try:
        number = int(value)
    except ValueError:
        issues.append(f"{field} 不是整数：{value or '<空>'}")
        return None
    if number < 0:
        issues.append(f"{field} 不能为负数：{number}")
        return None
    return number


def validate_inputs(
    input_dir: Path,
    report_date: date,
    policy: Mapping[str, object],
) -> tuple[ValidationOutcome, InputBundle | None]:
    issues: list[str] = []
    loaded: dict[str, tuple[dict[str, str], ...]] = {}
    manifest: list[dict[str, object]] = []
    missing_cells = 0
    required_cells = 0

    for filename, required_fields in REQUIRED_SCHEMAS.items():
        path = input_dir / filename
        if not path.is_file():
            issues.append(f"缺少输入文件：{filename}")
            continue
        try:
            fieldnames, rows = _read_csv(path)
        except (csv.Error, OSError, ValueError) as error:
            issues.append(f"{filename} 无法读取：{error}")
            continue

        manifest.append(
            {
                "file": filename,
                "sha256": sha256_file(path),
                "row_count": len(rows),
            }
        )
        lowered_fields = {field.lower() for field in fieldnames}
        sensitive_fields = sorted(lowered_fields & SENSITIVE_COLUMNS)
        if sensitive_fields:
            issues.append(
                f"{filename} 含禁止接入的敏感字段：{', '.join(sensitive_fields)}"
            )
        missing_headers = [
            field for field in required_fields if field not in fieldnames
        ]
        if missing_headers:
            issues.append(
                f"{filename} 缺少必填字段：{', '.join(missing_headers)}"
            )
        if not rows and filename != "defects.csv":
            issues.append(f"{filename} 没有数据行")

        available_required = [
            field for field in required_fields if field in fieldnames
        ]
        required_cells += len(rows) * len(available_required)
        missing_cells += sum(
            1
            for row in rows
            for field in available_required
            if not row.get(field, "")
        )
        loaded[filename] = rows

    missing_rate = (
        round(missing_cells / required_cells, 6) if required_cells else 1.0
    )
    maximum_missing_rate = float(policy.get("max_missing_rate", 0.02))
    if missing_rate > maximum_missing_rate:
        issues.append(
            "必填单元格缺失率 "
            f"{missing_rate:.2%} 超过上限 {maximum_missing_rate:.2%}"
        )

    if set(loaded) != set(REQUIRED_SCHEMAS):
        outcome = ValidationOutcome(
            status="blocked",
            issues=tuple(issues),
            missing_rate=missing_rate,
            late_record_count=0,
            target_batch_count=0,
            source_manifest=tuple(manifest),
        )
        return outcome, None

    batches = loaded["batches.csv"]
    inspections = loaded["inspections.csv"]
    defects = loaded["defects.csv"]

    batch_ids: set[str] = set()
    target_batch_ids: set[str] = set()
    comparison_batch_ids: set[str] = set()
    output_by_batch: dict[str, int] = {}
    comparison_date = report_date - timedelta(days=1)
    for row_number, row in enumerate(batches, start=2):
        batch_id = row.get("batch_id", "")
        if batch_id in batch_ids:
            issues.append(f"batches.csv:{row_number} 批次号重复：{batch_id}")
        batch_ids.add(batch_id)

        production_date = _parse_date(
            row.get("production_date", ""),
            f"batches.csv:{row_number}.production_date",
            issues,
        )
        output_qty = _parse_nonnegative_int(
            row.get("output_qty", ""),
            f"batches.csv:{row_number}.output_qty",
            issues,
        )
        if output_qty is not None:
            output_by_batch[batch_id] = output_qty
        if production_date == report_date:
            target_batch_ids.add(batch_id)
        elif production_date == comparison_date:
            comparison_batch_ids.add(batch_id)
        _parse_datetime(
            row.get("mes_received_at", ""),
            f"batches.csv:{row_number}.mes_received_at",
            issues,
        )

    minimum_batches = int(policy.get("min_target_batches", 1))
    if len(target_batch_ids) < minimum_batches:
        issues.append(
            f"{report_date.isoformat()} 仅有 {len(target_batch_ids)} 个批次，"
            f"少于最低数据量 {minimum_batches}"
        )
    minimum_comparison_batches = int(
        policy.get("min_comparison_batches", 1)
    )
    if len(comparison_batch_ids) < minimum_comparison_batches:
        issues.append(
            f"{comparison_date.isoformat()} 仅有 "
            f"{len(comparison_batch_ids)} 个环比批次，"
            f"少于最低数据量 {minimum_comparison_batches}"
        )

    inspection_ids: set[str] = set()
    inspection_quantities: dict[str, tuple[int, int]] = {}
    late_record_count = 0
    late_cutoff_text = str(policy.get("late_after", "08:00"))
    try:
        late_cutoff_time = time.fromisoformat(late_cutoff_text)
    except ValueError:
        issues.append(f"工作流 late_after 配置无效：{late_cutoff_text}")
        late_cutoff_time = time(8, 0)
    late_cutoff = datetime.combine(
        report_date + timedelta(days=1), late_cutoff_time
    )

    for row_number, row in enumerate(inspections, start=2):
        batch_id = row.get("batch_id", "")
        if batch_id in inspection_ids:
            issues.append(f"inspections.csv:{row_number} 批次号重复：{batch_id}")
        inspection_ids.add(batch_id)
        if batch_id not in batch_ids:
            issues.append(
                f"inspections.csv:{row_number} 引用了未知批次：{batch_id}"
            )

        inspected_qty = _parse_nonnegative_int(
            row.get("inspected_qty", ""),
            f"inspections.csv:{row_number}.inspected_qty",
            issues,
        )
        qualified_qty = _parse_nonnegative_int(
            row.get("first_pass_qualified_qty", ""),
            f"inspections.csv:{row_number}.first_pass_qualified_qty",
            issues,
        )
        if (
            inspected_qty is not None
            and qualified_qty is not None
            and qualified_qty > inspected_qty
        ):
            issues.append(
                f"inspections.csv:{row_number} 一次合格数大于检验数"
            )
        if inspected_qty is not None and qualified_qty is not None:
            inspection_quantities[batch_id] = (
                inspected_qty,
                qualified_qty,
            )
        if (
            inspected_qty is not None
            and batch_id in output_by_batch
            and inspected_qty != output_by_batch[batch_id]
        ):
            issues.append(
                f"inspections.csv:{row_number} 检验数与批次产量不一致：{batch_id}"
            )

        recorded_at = _parse_datetime(
            row.get("recorded_at", ""),
            f"inspections.csv:{row_number}.recorded_at",
            issues,
        )
        if (
            batch_id in target_batch_ids
            and recorded_at is not None
            and recorded_at > late_cutoff
        ):
            late_record_count += 1

    relevant_batch_ids = target_batch_ids | comparison_batch_ids
    missing_inspections = sorted(relevant_batch_ids - inspection_ids)
    if missing_inspections:
        issues.append(
            "日报或环比批次缺少检验记录：" + ", ".join(missing_inspections)
        )

    defect_totals: dict[str, int] = defaultdict(int)
    for row_number, row in enumerate(defects, start=2):
        batch_id = row.get("batch_id", "")
        if batch_id not in batch_ids:
            issues.append(f"defects.csv:{row_number} 引用了未知批次：{batch_id}")
        defect_qty = _parse_nonnegative_int(
            row.get("defect_qty", ""),
            f"defects.csv:{row_number}.defect_qty",
            issues,
        )
        if defect_qty is not None:
            defect_totals[batch_id] += defect_qty

    inspection_by_batch = {
        row["batch_id"]: row for row in inspections if row.get("batch_id")
    }
    for batch_id in sorted(relevant_batch_ids):
        inspection = inspection_by_batch.get(batch_id)
        quantities = inspection_quantities.get(batch_id)
        if inspection is None or quantities is None:
            continue
        inspected_qty, qualified_qty = quantities
        expected_defects = inspected_qty - qualified_qty
        if defect_totals.get(batch_id, 0) != expected_defects:
            issues.append(
                f"{batch_id} 缺陷明细合计 {defect_totals.get(batch_id, 0)} "
                f"与不合格数 {expected_defects} 不一致"
            )

    maximum_late_records = policy.get("max_late_records")
    if (
        maximum_late_records is not None
        and late_record_count > int(maximum_late_records)
    ):
        issues.append(
            f"迟到记录 {late_record_count} 条，超过上限 {maximum_late_records}"
        )

    bundle = InputBundle(
        batches=batches,
        inspections=inspections,
        defects=defects,
        source_manifest=tuple(manifest),
    )
    outcome = ValidationOutcome(
        status="passed" if not issues else "blocked",
        issues=tuple(issues),
        missing_rate=missing_rate,
        late_record_count=late_record_count,
        target_batch_count=len(target_batch_ids),
        source_manifest=tuple(manifest),
    )
    return outcome, bundle


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def calculate_metric_hash(metrics: Mapping[str, object]) -> str:
    payload = {
        key: value for key, value in metrics.items() if key != "metric_hash"
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _group_metrics(
    dimension: str,
    target_batches: Sequence[dict[str, str]],
    inspections_by_batch: Mapping[str, dict[str, str]],
    defects_by_batch: Mapping[str, int],
) -> list[dict[str, object]]:
    groups: dict[str, dict[str, int]] = defaultdict(
        lambda: {"output_qty": 0, "qualified_qty": 0, "defect_qty": 0}
    )
    for batch in target_batches:
        batch_id = batch["batch_id"]
        inspection = inspections_by_batch[batch_id]
        group = groups[batch[dimension]]
        group["output_qty"] += int(batch["output_qty"])
        group["qualified_qty"] += int(
            inspection["first_pass_qualified_qty"]
        )
        group["defect_qty"] += defects_by_batch.get(batch_id, 0)

    return [
        {
            dimension: group_name,
            **values,
            "first_pass_yield": _ratio(
                values["qualified_qty"], values["output_qty"]
            ),
            "defect_rate": _ratio(
                values["defect_qty"], values["output_qty"]
            ),
        }
        for group_name, values in sorted(groups.items())
    ]


def calculate_metrics(
    bundle: InputBundle,
    report_date: date,
    standards: Sequence[Mapping[str, object]],
    validation: ValidationOutcome,
) -> dict[str, object]:
    if validation.status != "passed":
        raise ValueError("数据校验未通过，禁止计算日报指标")

    previous_date = report_date - timedelta(days=1)
    target_batches = sorted(
        (
            row
            for row in bundle.batches
            if row["production_date"] == report_date.isoformat()
        ),
        key=lambda row: row["batch_id"],
    )
    previous_batches = [
        row
        for row in bundle.batches
        if row["production_date"] == previous_date.isoformat()
    ]
    target_ids = {row["batch_id"] for row in target_batches}
    previous_ids = {row["batch_id"] for row in previous_batches}
    inspections_by_batch = {
        row["batch_id"]: row for row in bundle.inspections
    }

    defects_by_batch: dict[str, int] = defaultdict(int)
    defect_names: dict[str, str] = {}
    current_defects: dict[str, int] = defaultdict(int)
    previous_defects: dict[str, int] = defaultdict(int)
    defects_per_batch: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in bundle.defects:
        batch_id = row["batch_id"]
        defect_code = row["defect_code"]
        defect_qty = int(row["defect_qty"])
        defect_names[defect_code] = row["defect_name"]
        defects_by_batch[batch_id] += defect_qty
        defects_per_batch[batch_id][defect_code] += defect_qty
        if batch_id in target_ids:
            current_defects[defect_code] += defect_qty
        elif batch_id in previous_ids:
            previous_defects[defect_code] += defect_qty

    total_output = sum(int(row["output_qty"]) for row in target_batches)
    qualified_count = sum(
        int(inspections_by_batch[row["batch_id"]]["first_pass_qualified_qty"])
        for row in target_batches
    )
    inspected_count = sum(
        int(inspections_by_batch[row["batch_id"]]["inspected_qty"])
        for row in target_batches
    )
    previous_output = sum(int(row["output_qty"]) for row in previous_batches)

    top_defects: list[dict[str, object]] = []
    all_defect_codes = set(current_defects) | set(previous_defects)
    for defect_code in all_defect_codes:
        current_qty = current_defects.get(defect_code, 0)
        previous_qty = previous_defects.get(defect_code, 0)
        current_rate = _ratio(current_qty, total_output)
        previous_rate = _ratio(previous_qty, previous_output)
        top_defects.append(
            {
                "defect_code": defect_code,
                "defect_name": defect_names.get(defect_code, defect_code),
                "current_qty": current_qty,
                "previous_qty": previous_qty,
                "current_defect_rate": current_rate,
                "previous_defect_rate": previous_rate,
                "change_percentage_points": round(
                    (current_rate - previous_rate) * 100, 4
                ),
            }
        )
    top_defects.sort(
        key=lambda item: (-int(item["current_qty"]), str(item["defect_code"]))
    )

    standards_by_product = {
        str(item["product_id"]): item for item in standards
    }
    anomalies: list[dict[str, object]] = []
    batch_metrics: list[dict[str, object]] = []
    for batch in target_batches:
        batch_id = batch["batch_id"]
        inspection = inspections_by_batch[batch_id]
        output_qty = int(batch["output_qty"])
        qualified_qty = int(inspection["first_pass_qualified_qty"])
        defect_qty = defects_by_batch.get(batch_id, 0)
        first_pass_yield = _ratio(qualified_qty, int(inspection["inspected_qty"]))
        defect_rate = _ratio(defect_qty, output_qty)
        standard = standards_by_product.get(batch["product_id"])
        triggers: list[dict[str, object]] = []
        if standard is None:
            triggers.append(
                {
                    "rule": "quality_standard_missing",
                    "operator": "missing",
                    "actual": batch["product_id"],
                    "limit": None,
                    "standard_id": "",
                }
            )
        else:
            minimum_yield = float(standard["minimum_first_pass_yield"])
            maximum_defect_rate = float(standard["maximum_defect_rate"])
            if first_pass_yield < minimum_yield:
                triggers.append(
                    {
                        "rule": "minimum_first_pass_yield",
                        "operator": "<",
                        "actual": first_pass_yield,
                        "limit": minimum_yield,
                        "standard_id": standard["standard_id"],
                    }
                )
            if defect_rate > maximum_defect_rate:
                triggers.append(
                    {
                        "rule": "maximum_defect_rate",
                        "operator": ">",
                        "actual": defect_rate,
                        "limit": maximum_defect_rate,
                        "standard_id": standard["standard_id"],
                    }
                )

        dominant_defect_code = ""
        if defects_per_batch.get(batch_id):
            dominant_defect_code = sorted(
                defects_per_batch[batch_id].items(),
                key=lambda item: (-item[1], item[0]),
            )[0][0]
        batch_metric = {
            "batch_id": batch_id,
            "product_id": batch["product_id"],
            "line_id": batch["line_id"],
            "shift": batch["shift"],
            "output_qty": output_qty,
            "qualified_qty": qualified_qty,
            "first_pass_yield": first_pass_yield,
            "defect_qty": defect_qty,
            "defect_rate": defect_rate,
            "dominant_defect_code": dominant_defect_code,
        }
        batch_metrics.append(batch_metric)
        if triggers:
            anomalies.append(
                {
                    "exception_id": (
                        f"EXC-{report_date.strftime('%Y%m%d')}-"
                        f"{len(anomalies) + 1:03d}"
                    ),
                    **batch_metric,
                    "triggers": triggers,
                }
            )

    metrics: dict[str, object] = {
        "report_date": report_date.isoformat(),
        "comparison_date": previous_date.isoformat(),
        "total_output": total_output,
        "inspected_count": inspected_count,
        "qualified_count": qualified_count,
        "first_pass_yield": _ratio(qualified_count, inspected_count),
        "defect_count": sum(current_defects.values()),
        "defect_rate": _ratio(sum(current_defects.values()), total_output),
        "grouped_defect_rates": {
            "by_line": _group_metrics(
                "line_id",
                target_batches,
                inspections_by_batch,
                defects_by_batch,
            ),
            "by_product": _group_metrics(
                "product_id",
                target_batches,
                inspections_by_batch,
                defects_by_batch,
            ),
            "by_shift": _group_metrics(
                "shift",
                target_batches,
                inspections_by_batch,
                defects_by_batch,
            ),
        },
        "top_defects": top_defects[:5],
        "batch_metrics": batch_metrics,
        "anomalies": anomalies,
        "data_quality": {
            "status": validation.status,
            "missing_rate": validation.missing_rate,
            "late_record_count": validation.late_record_count,
            "target_batch_count": validation.target_batch_count,
        },
    }
    metrics["metric_hash"] = calculate_metric_hash(metrics)
    return metrics
