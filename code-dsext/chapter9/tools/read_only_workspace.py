from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


ALLOWED_SUFFIXES = frozenset({".json", ".jsonl", ".csv", ".md", ".txt"})
SENSITIVE_MARKERS = (".env", "secret", "credential")
SENSITIVE_SUFFIXES = frozenset({".key", ".pem"})
PRODUCT_FIELD_ALIASES = ("product", "product_id", "产品")
LINE_FIELD_ALIASES = ("line", "line_id", "产线")
DATE_FIELD_ALIASES = (
    "inspection_date",
    "date",
    "production_date",
    "timestamp",
    "event_time",
    "日期",
)


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    path: str
    hash: str
    observed_at: str
    status: str
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReadOnlyWorkspace:
    def __init__(self, data_root: str | Path, state_dir: str | Path):
        root = Path(data_root).expanduser()
        if not root.exists() or not root.is_dir():
            raise ValueError("data_root 必须是存在的目录")
        if root.is_symlink():
            raise ValueError("data_root 不能是符号链接")

        self.data_root = root.resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        try:
            self.state_dir.relative_to(self.data_root)
        except ValueError:
            pass
        else:
            raise ValueError("state_dir 必须位于只读 data_root 之外")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_index_path = self.state_dir / "evidence_index.json"
        self.audit_log_path = self.state_dir / "audit.jsonl"
        self._lock = threading.RLock()
        self._ensure_index()

    def list_files(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for candidate in sorted(self.data_root.rglob("*")):
            if not candidate.is_file() or self._is_in_state_dir(candidate):
                continue
            try:
                resolved = self._resolve(candidate.relative_to(self.data_root).as_posix())
            except ValueError:
                continue
            files.append(
                {
                    "path": resolved.relative_to(self.data_root).as_posix(),
                    "bytes": resolved.stat().st_size,
                    "hash": self._sha256(resolved),
                }
            )
        manifest_hash = self._stable_hash(files)
        evidence = self._record_evidence(
            path=".",
            content_hash=manifest_hash,
            operation="list_files",
            summary={"file_count": len(files), "kind": "workspace_manifest"},
        )
        return {
            "files": files,
            "summary": {"file_count": len(files), "kind": "workspace_manifest"},
            "evidence": evidence.to_dict(),
        }

    def inspect_json(self, relative_path: str) -> dict[str, Any]:
        path = self._resolve(relative_path, allowed_suffixes={".json", ".jsonl"})
        content_hash = self._sha256(path)
        try:
            if path.suffix.lower() == ".json":
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                summary = self._json_summary(payload)
            else:
                with path.open("r", encoding="utf-8") as handle:
                    line_count = sum(1 for line in handle if line.strip())
                summary = {"kind": "jsonl", "record_count": line_count}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法解析 JSON 文件：{relative_path}") from exc
        summary["path"] = path.relative_to(self.data_root).as_posix()
        evidence = self._record_evidence(
            path=summary["path"],
            content_hash=content_hash,
            operation="inspect_json",
            summary=summary,
        )
        return {"summary": summary, "evidence": evidence.to_dict()}

    def inspect_csv_metadata(
        self, relative_path: str, sample_size: int = 5
    ) -> dict[str, Any]:
        path = self._resolve(relative_path, allowed_suffixes={".csv"})
        bounded_sample_size = self._bounded_sample_size(sample_size)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                if not headers:
                    raise ValueError("CSV 缺少表头")
                sampled_rows: list[dict[str, str]] = []
                row_count = 0
                for row in reader:
                    row_count += 1
                    if len(sampled_rows) < bounded_sample_size:
                        sampled_rows.append(dict(row))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ValueError(f"无法读取 CSV 文件：{relative_path}") from exc

        summary = {
            "kind": "csv_metadata",
            "path": path.relative_to(self.data_root).as_posix(),
            "columns": headers,
            "row_count": row_count,
            "sampled_row_count": len(sampled_rows),
            "sample_missing_rates": (
                self._missing_rates(sampled_rows, headers) if sampled_rows else {}
            ),
            "sample_numeric_fields": self._numeric_fields(headers, sampled_rows),
        }
        evidence = self._record_evidence(
            path=summary["path"],
            content_hash=self._sha256(path),
            operation="inspect_csv_metadata",
            summary=summary,
        )
        return {"summary": summary, "evidence": evidence.to_dict()}

    def profile_quality_csv(
        self,
        relative_path: str,
        *,
        product: str | None = None,
        line: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        self.inspect_csv_metadata(relative_path)
        path, headers, rows, content_hash = self._read_quality_csv(relative_path)
        source_row_count = len(rows)
        rows, scope, applied_fields = self._filter_quality_rows(
            headers,
            rows,
            product=product,
            line=line,
            start_date=start_date,
            end_date=end_date,
        )
        numeric_fields = self._numeric_fields(headers, rows)
        quality_field = self._quality_field(numeric_fields)
        date_field = self._date_field(headers)
        quality_by_date = self._group_mean(rows, date_field, quality_field)
        summary = {
            "kind": "quality_profile",
            "path": path.relative_to(self.data_root).as_posix(),
            "source_row_count": source_row_count,
            "row_count": len(rows),
            "scope": scope,
            "applied_fields": applied_fields,
            "columns": headers,
            "date_range": self._date_range(rows, date_field),
            "missing_rates": self._missing_rates(rows, headers),
            "metadata_audited": True,
            "sample_audited_rows": min(5, len(rows)),
            "numeric_fields": numeric_fields,
            "quality_field": quality_field,
            "quality_mean": self._mean_field(rows, quality_field),
            "quality_by_date": quality_by_date,
            "largest_drop": self._largest_drop(quality_by_date),
            "quality_by_shift": self._group_mean(rows, "shift", quality_field),
            "quality_by_equipment": self._group_mean(rows, "equipment_id", quality_field),
            "quality_by_program_version": self._group_mean(
                rows, "program_version", quality_field
            ),
        }
        evidence = self._record_evidence(
            path=summary["path"],
            content_hash=content_hash,
            operation="profile_quality_csv",
            summary=summary,
        )
        return {"summary": summary, "evidence": evidence.to_dict()}

    def compare_quality_factors(
        self,
        relative_path: str,
        baseline_end: str,
        anomaly_start: str,
        *,
        product: str | None = None,
        line: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        baseline_date = self._parse_date(baseline_end, "baseline_end")
        anomaly_date = self._parse_date(anomaly_start, "anomaly_start")
        if baseline_date >= anomaly_date:
            raise ValueError("baseline_end 必须早于 anomaly_start")

        self.inspect_csv_metadata(relative_path)
        path, headers, rows, content_hash = self._read_quality_csv(relative_path)
        source_row_count = len(rows)
        rows, scope, applied_fields = self._filter_quality_rows(
            headers,
            rows,
            product=product,
            line=line,
            start_date=start_date,
            end_date=end_date,
        )
        date_field = self._date_field(headers)
        quality_field = self._quality_field(self._numeric_fields(headers, rows))
        baseline_rows = [
            row for row in rows if self._parse_date(row[date_field], date_field) <= baseline_date
        ]
        anomaly_rows = [
            row for row in rows if self._parse_date(row[date_field], date_field) >= anomaly_date
        ]
        if not baseline_rows or not anomaly_rows:
            raise ValueError("基线或异常窗口没有可比较记录")

        factor_fields = [
            name
            for name in ("shift", "material_code", "equipment_id", "program_version")
            if name in headers
        ]
        numeric_fields = [
            name
            for name in (quality_field, "pass_rate", "temperature_c")
            if name is not None and name in headers
        ]
        differences = {
            name: {
                "baseline_mean": self._mean_field(baseline_rows, name),
                "anomaly_mean": self._mean_field(anomaly_rows, name),
                "difference": self._rounded_difference(baseline_rows, anomaly_rows, name),
            }
            for name in numeric_fields
        }
        factors = {
            name: {
                "baseline": self._group_mean(baseline_rows, name, quality_field),
                "anomaly": self._group_mean(anomaly_rows, name, quality_field),
            }
            for name in factor_fields
        }
        summary = {
            "kind": "quality_factor_comparison",
            "path": path.relative_to(self.data_root).as_posix(),
            "source_row_count": source_row_count,
            "row_count": len(rows),
            "scope": scope,
            "applied_fields": applied_fields,
            "date_field": date_field,
            "baseline_end": baseline_date.isoformat(),
            "anomaly_start": anomaly_date.isoformat(),
            "baseline_row_count": len(baseline_rows),
            "anomaly_row_count": len(anomaly_rows),
            "metadata_audited": True,
            "sample_audited_rows": min(5, len(rows)),
            "quality_differences": differences,
            "factor_quality_means": factors,
            "interpretation": "仅报告脱敏样例中的相关性差异，不构成因果结论。",
        }
        evidence = self._record_evidence(
            path=summary["path"],
            content_hash=content_hash,
            operation="compare_quality_factors",
            summary=summary,
        )
        return {"summary": summary, "evidence": evidence.to_dict()}

    def validate_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self._lock:
            index = self._load_index()
            record = index.get(evidence_id)
            if record is None:
                raise KeyError(f"未找到证据：{evidence_id}")
            updated = self._validate_record(record)
            index[evidence_id] = updated
            self._save_index(index)
            self._append_audit(
                path=updated["path"],
                content_hash=updated["hash"],
                operation="validate_evidence",
                summary={"evidence_id": evidence_id, "status": updated["status"]},
            )
            return updated

    def validate_all_evidence(self) -> dict[str, Any]:
        with self._lock:
            index = self._load_index()
            results = []
            for evidence_id in sorted(index):
                updated = self._validate_record(index[evidence_id])
                index[evidence_id] = updated
                results.append(updated)
            self._save_index(index)
            counts: dict[str, int] = defaultdict(int)
            for item in results:
                counts[item["status"]] += 1
            self._append_audit(
                path=".",
                content_hash=self._stable_hash(results),
                operation="validate_all_evidence",
                summary={"evidence_count": len(results), "status_counts": dict(counts)},
            )
            return {"evidence": results, "status_counts": dict(counts)}

    def _read_quality_csv(
        self, relative_path: str
    ) -> tuple[Path, list[str], list[dict[str, str]], str]:
        path = self._resolve(relative_path, allowed_suffixes={".csv"})
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                if not headers:
                    raise ValueError("CSV 缺少表头")
                rows = [dict(row) for row in reader]
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ValueError(f"无法读取 CSV 文件：{relative_path}") from exc
        if not rows:
            raise ValueError("CSV 不能是空数据集")
        return path, headers, rows, self._sha256(path)

    def _filter_quality_rows(
        self,
        headers: list[str],
        rows: list[dict[str, str]],
        *,
        product: str | None,
        line: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[list[dict[str, str]], dict[str, str | None], dict[str, str]]:
        normalized_product = self._normalize_scope_value(product, "product")
        normalized_line = self._normalize_scope_value(line, "line")
        normalized_start = self._parse_optional_date(start_date, "start_date")
        normalized_end = self._parse_optional_date(end_date, "end_date")
        if normalized_start and normalized_end and normalized_start > normalized_end:
            raise ValueError("start_date 不能晚于 end_date")

        product_field = self._first_header(headers, PRODUCT_FIELD_ALIASES)
        line_field = self._first_header(headers, LINE_FIELD_ALIASES)
        date_field = self._first_header(headers, DATE_FIELD_ALIASES)
        if normalized_product is not None and product_field is None:
            raise ValueError("CSV 缺少可用于产品筛选的字段")
        if normalized_line is not None and line_field is None:
            raise ValueError("CSV 缺少可用于产线筛选的字段")
        if (normalized_start or normalized_end) and date_field is None:
            raise ValueError("CSV 缺少可用于日期筛选的字段")
        applied_fields = {
            name: field
            for name, value, field in (
                ("product", normalized_product, product_field),
                ("line", normalized_line, line_field),
                ("date", normalized_start or normalized_end, date_field),
            )
            if value is not None and field is not None
        }
        scope = {
            "product": normalized_product,
            "line": normalized_line,
            "start_date": normalized_start.isoformat() if normalized_start else None,
            "end_date": normalized_end.isoformat() if normalized_end else None,
        }

        filtered_rows = []
        for row in rows:
            if product_field and normalized_product is not None:
                if row.get(product_field, "").strip() != normalized_product:
                    continue
            if line_field and normalized_line is not None:
                if row.get(line_field, "").strip() != normalized_line:
                    continue
            if date_field and (normalized_start or normalized_end):
                raw_date = row.get(date_field, "").strip()
                if not raw_date:
                    continue
                row_date = self._parse_date(raw_date, date_field)
                if normalized_start and row_date < normalized_start:
                    continue
                if normalized_end and row_date > normalized_end:
                    continue
            filtered_rows.append(row)
        if not filtered_rows:
            raise ValueError("筛选范围内没有匹配记录")
        return filtered_rows, scope, applied_fields

    @staticmethod
    def _bounded_sample_size(sample_size: int) -> int:
        if isinstance(sample_size, bool) or not isinstance(sample_size, int):
            raise ValueError("sample_size 必须是正整数")
        if sample_size < 1:
            raise ValueError("sample_size 必须是正整数")
        return min(sample_size, 5)

    @staticmethod
    def _normalize_scope_value(value: str | None, field_name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是非空字符串")
        return value.strip()

    @classmethod
    def _parse_optional_date(cls, value: str | None, field_name: str) -> datetime.date | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是 ISO 日期")
        return cls._parse_date(value.strip(), field_name)

    @staticmethod
    def _first_header(headers: list[str], aliases: Iterable[str]) -> str | None:
        return next((alias for alias in aliases if alias in headers), None)

    def _resolve(
        self, relative_path: str, allowed_suffixes: set[str] | None = None
    ) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("路径必须是非空相对路径")
        requested = Path(relative_path)
        windows_requested = PureWindowsPath(relative_path)
        if requested.is_absolute() or windows_requested.is_absolute():
            raise ValueError("拒绝绝对路径")
        if ".." in requested.parts or ".." in windows_requested.parts:
            raise ValueError("拒绝路径越界")
        if any(self._is_sensitive(part) for part in requested.parts):
            raise ValueError("拒绝敏感路径")
        suffixes = allowed_suffixes or ALLOWED_SUFFIXES
        if requested.suffix.lower() not in suffixes:
            raise ValueError("不允许的文件类型")

        candidate = self.data_root.joinpath(*requested.parts)
        if not candidate.exists() or not candidate.is_file():
            raise ValueError("文件不存在")
        try:
            relative = candidate.relative_to(self.data_root)
            for index in range(1, len(relative.parts) + 1):
                if self.data_root.joinpath(*relative.parts[:index]).is_symlink():
                    raise ValueError("拒绝符号链接")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.data_root)
        except (OSError, ValueError) as exc:
            raise ValueError("拒绝工作区外路径或符号链接") from exc
        return resolved

    def _record_evidence(
        self,
        path: str,
        content_hash: str,
        operation: str,
        summary: dict[str, Any],
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            evidence_id=f"EV-{uuid.uuid4().hex}",
            path=path,
            hash=content_hash,
            observed_at=self._now(),
            status="valid",
            summary=summary,
        )
        with self._lock:
            index = self._load_index()
            index[record.evidence_id] = record.to_dict()
            self._save_index(index)
            self._append_audit(
                path=path,
                content_hash=content_hash,
                operation=operation,
                summary=summary,
            )
        return record

    def _validate_record(self, record: dict[str, Any]) -> dict[str, Any]:
        updated = dict(record)
        if updated["path"] == ".":
            current_hash = self._workspace_manifest_hash()
            updated["status"] = "valid" if current_hash == updated["hash"] else "invalidated"
            updated["summary"] = {
                **updated["summary"],
                "validation": "workspace manifest checked",
            }
            return updated
        try:
            path = self._resolve(updated["path"])
        except ValueError:
            updated["status"] = "missing"
            updated["summary"] = {**updated["summary"], "validation": "file missing or blocked"}
            return updated
        current_hash = self._sha256(path)
        updated["status"] = "valid" if current_hash == updated["hash"] else "invalidated"
        updated["summary"] = {
            **updated["summary"],
            "validation": "hash matched" if updated["status"] == "valid" else "hash changed",
        }
        return updated

    def _workspace_manifest_hash(self) -> str:
        files = []
        for candidate in sorted(self.data_root.rglob("*")):
            if not candidate.is_file() or self._is_in_state_dir(candidate):
                continue
            try:
                resolved = self._resolve(candidate.relative_to(self.data_root).as_posix())
            except ValueError:
                continue
            files.append(
                {
                    "path": resolved.relative_to(self.data_root).as_posix(),
                    "bytes": resolved.stat().st_size,
                    "hash": self._sha256(resolved),
                }
            )
        return self._stable_hash(files)

    def _ensure_index(self) -> None:
        with self._lock:
            if not self.evidence_index_path.exists():
                self._save_index({})
            else:
                self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        try:
            with self.evidence_index_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("证据索引损坏，拒绝继续使用") from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, dict) for key, value in payload.items()
        ):
            raise ValueError("证据索引格式无效")
        return payload

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        temporary = self.evidence_index_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.evidence_index_path)

    def _append_audit(
        self, path: str, content_hash: str, operation: str, summary: dict[str, Any]
    ) -> None:
        entry = {
            "path": path,
            "time": self._now(),
            "hash": content_hash,
            "operation": operation,
            "summary": summary,
        }
        with self.audit_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    @staticmethod
    def _json_summary(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return {"kind": "object", "key_count": len(payload), "keys": sorted(payload)[:50]}
        if isinstance(payload, list):
            return {"kind": "array", "record_count": len(payload)}
        return {"kind": type(payload).__name__}

    @staticmethod
    def _numeric_fields(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
        numeric = []
        for name in headers:
            values = [row.get(name, "").strip() for row in rows if row.get(name, "").strip()]
            if values and all(ReadOnlyWorkspace._as_float(value) is not None for value in values):
                numeric.append(name)
        return numeric

    @staticmethod
    def _quality_field(numeric_fields: list[str]) -> str:
        preferred = ("pass_rate", "yield_rate", "quality_score", "defect_rate")
        for field in preferred:
            if field in numeric_fields:
                return field
        if not numeric_fields:
            raise ValueError("CSV 中没有可聚合的数值质量字段")
        return numeric_fields[0]

    @staticmethod
    def _date_field(headers: list[str]) -> str:
        for field in DATE_FIELD_ALIASES:
            if field in headers:
                return field
        raise ValueError("CSV 缺少可比较的日期字段")

    @staticmethod
    def _parse_date(value: str, field_name: str) -> datetime.date:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ValueError(f"{field_name} 不是 ISO 日期") from exc

    @staticmethod
    def _as_float(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _mean_field(cls, rows: Iterable[dict[str, str]], field: str) -> float | None:
        values = [cls._as_float(row.get(field, "")) for row in rows]
        usable = [value for value in values if value is not None]
        return round(sum(usable) / len(usable), 4) if usable else None

    @classmethod
    def _group_mean(
        cls, rows: Iterable[dict[str, str]], group_field: str, value_field: str
    ) -> dict[str, float | None]:
        groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            group = row.get(group_field, "").strip() or "<missing>"
            groups[group].append(row)
        return {name: cls._mean_field(groups[name], value_field) for name in sorted(groups)}

    @staticmethod
    def _date_range(rows: list[dict[str, str]], date_field: str) -> dict[str, str]:
        dates = sorted(
            ReadOnlyWorkspace._parse_date(row[date_field], date_field).isoformat()
            for row in rows
            if row.get(date_field, "").strip()
        )
        if not dates:
            raise ValueError("CSV 日期字段没有有效值")
        return {"start": dates[0], "end": dates[-1]}

    @staticmethod
    def _missing_rates(
        rows: list[dict[str, str]], headers: list[str]
    ) -> dict[str, float]:
        row_count = len(rows)
        if row_count == 0:
            return {}
        return {
            name: round(
                sum(not row.get(name, "").strip() for row in rows) / row_count,
                4,
            )
            for name in headers
        }

    @staticmethod
    def _largest_drop(quality_by_date: dict[str, float | None]) -> dict[str, Any] | None:
        ordered = [
            (date, value)
            for date, value in sorted(quality_by_date.items())
            if value is not None
        ]
        if len(ordered) < 2:
            return None
        candidates = [
            (current[1] - previous[1], previous[0], current[0])
            for previous, current in zip(ordered, ordered[1:])
        ]
        difference, baseline_date, anomaly_date = min(candidates)
        return {
            "baseline_date": baseline_date,
            "anomaly_date": anomaly_date,
            "quality_change": round(difference, 4),
        }

    @classmethod
    def _rounded_difference(
        cls,
        baseline_rows: list[dict[str, str]],
        anomaly_rows: list[dict[str, str]],
        field: str,
    ) -> float | None:
        baseline = cls._mean_field(baseline_rows, field)
        anomaly = cls._mean_field(anomaly_rows, field)
        if baseline is None or anomaly is None:
            return None
        return round(anomaly - baseline, 4)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _stable_hash(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _is_in_state_dir(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self.state_dir)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_sensitive(part: str) -> bool:
        lowered = part.casefold()
        return (
            any(marker in lowered for marker in SENSITIVE_MARKERS)
            or lowered.endswith(tuple(SENSITIVE_SUFFIXES))
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
