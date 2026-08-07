from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping

try:
    from ..context import InvestigationContextBuilder
    from ..notes import InvestigationNoteStore
    from ..tools import ReadOnlyWorkspace
except ImportError:
    from context import InvestigationContextBuilder
    from notes import InvestigationNoteStore
    from tools import ReadOnlyWorkspace


@dataclass(frozen=True)
class InvestigationTarget:
    investigation_id: str
    objective: str
    product: str
    line: str
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        if not self.investigation_id.strip():
            raise ValueError("investigation_id 不能为空")
        if not self.objective.strip():
            raise ValueError("objective 不能为空")
        if not self.product.strip() or not self.line.strip():
            raise ValueError("product 和 line 不能为空")
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if start > end:
            raise ValueError("start_date 不能晚于 end_date")


class LongHorizonYieldInvestigation:
    STATE_VERSION = 1
    QUALITY_NAMES = (
        "quality_profile.csv",
        "quality_history.csv",
        "quality.csv",
        "yield_history.csv",
    )
    DICTIONARY_NAMES = (
        "data_dictionary.json",
        "data_dictionary.csv",
        "dictionary.json",
        "dictionary.csv",
    )
    REVIEW_NAME = "week_later_quality.csv"
    DIMENSIONS = {
        "shift": ("shift", "班次"),
        "material": ("material", "material_code", "material_batch", "物料"),
        "equipment": ("equipment", "equipment_id", "machine", "设备"),
        "program_version": ("program_version", "program", "程序版本"),
        "parameter": (
            "parameter",
            "process_parameter",
            "temperature",
            "temperature_c",
            "pressure",
            "工艺参数",
        ),
    }
    YIELD_FIELDS = (
        "yield_rate",
        "first_pass_yield",
        "fpy",
        "pass_rate",
        "良率",
        "一次合格率",
    )

    def __init__(
        self,
        data_root: str | Path,
        state_dir: str | Path,
        target: InvestigationTarget | Mapping[str, Any],
    ) -> None:
        requested_data_root = Path(data_root).expanduser()
        if requested_data_root.is_symlink():
            raise ValueError("data_root 不能是符号链接")
        self.data_root = requested_data_root.resolve()
        self.state_dir = Path(state_dir).resolve()
        try:
            self.state_dir.relative_to(self.data_root)
        except ValueError:
            pass
        else:
            raise ValueError("state_dir 必须位于只读 data_root 之外")
        self.target = (
            target
            if isinstance(target, InvestigationTarget)
            else InvestigationTarget(**dict(target))
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = (
            self.state_dir / f"{self.target.investigation_id}.json"
        ).resolve()
        if self.state_path.parent != self.state_dir:
            raise ValueError("investigation_id 不得包含路径或越界片段")
        recovered = self.state_path.exists()
        self.workspace = ReadOnlyWorkspace(self.data_root, self.state_dir / "workspace")
        self.notes = InvestigationNoteStore(self.state_dir / "notes")
        self.context_builder = InvestigationContextBuilder()
        self.state = self._load_or_create_state()
        if recovered:
            invalidated = self._invalidate_changed_evidence()
            self.state["recovery_invalidated_evidence_ids"] = sorted(invalidated)
            self._save_state()

    def day_1_scope(self) -> dict[str, Any]:
        guard = self._guard_phase("day_1", {"initialized", "day_1_blocked"})
        if guard is not None:
            return guard
        dictionary_path = self._find_existing(self.DICTIONARY_NAMES)
        quality_path = self._find_existing(self.QUALITY_NAMES)
        if quality_path is None:
            return self._blocked("day_1", "未找到质量画像 CSV")

        rows, quality_result = self._read_csv(quality_path)
        scoped = self._scope_rows(rows)
        if not scoped:
            return self._blocked("day_1", "目标产品、产线和日期范围内没有质量数据")

        yield_field = self._first_field(scoped, self.YIELD_FIELDS)
        yields = [
            value
            for row in scoped
            if (value := self._number(row.get(yield_field))) is not None
        ]
        if not yield_field or not yields:
            return self._blocked("day_1", "质量画像缺少可计算的良率字段")

        split = max(1, len(yields) // 2)
        baseline_values = yields[:split]
        recent_values = yields[split:] or yields[-1:]
        baseline = round(fmean(baseline_values), 6)
        recent = round(fmean(recent_values), 6)
        delta = round(recent - baseline, 6)
        quality_evidence = self._store_tool_result(quality_result)
        profile_summary = dict(quality_result["summary"])
        largest_drop = dict(profile_summary.get("largest_drop") or {})
        self.state["comparison_window"] = {
            "baseline_end": largest_drop.get(
                "baseline_date", self.target.start_date
            ),
            "anomaly_start": largest_drop.get(
                "anomaly_date", self.target.end_date
            ),
        }
        evidence_ids = [quality_evidence["evidence_id"]]
        if dictionary_path is not None:
            relative_dictionary = dictionary_path.relative_to(self.data_root).as_posix()
            if dictionary_path.suffix.lower() in {".json", ".jsonl"}:
                dictionary_result = self.workspace.inspect_json(relative_dictionary)
            else:
                dictionary_result = self.workspace.inspect_csv_metadata(
                    relative_dictionary
                )
            evidence_ids.append(
                self._store_tool_result(dictionary_result)["evidence_id"]
            )

        facts = [
            {
                "fact_id": "F-BASELINE",
                "statement": f"基线窗口平均良率为 {baseline:.4f}。",
                "status": "verified",
                "evidence_ids": tuple(evidence_ids),
                "metrics": {
                    "row_count": len(scoped),
                    "baseline_yield": baseline,
                    "date_range": profile_summary.get("date_range"),
                    "missing_rates": profile_summary.get("missing_rates"),
                },
            },
            {
                "fact_id": "F-YIELD-SHIFT",
                "statement": (
                    f"后半窗口平均良率为 {recent:.4f}，"
                    f"相对基线变化 {delta:+.4f}。"
                ),
                "status": "verified",
                "evidence_ids": (quality_evidence["evidence_id"],),
                "metrics": {
                    "recent_yield": recent,
                    "delta": delta,
                    "largest_drop": largest_drop,
                },
            },
        ]
        self.state["facts"] = facts
        self.state["phase"] = "day_1_completed"
        self._record_notes("fact", facts)
        self._save_state()
        return self._stage_result(
            "day_1", "completed", facts=facts, evidence_ids=evidence_ids
        )

    def day_2_hypotheses(self) -> dict[str, Any]:
        guard = self._guard_phase("day_2", {"day_1_completed", "day_2_blocked"})
        if guard is not None:
            return guard
        quality_path = self._find_existing(self.QUALITY_NAMES)
        if quality_path is None:
            return self._blocked("day_2", "未找到质量画像 CSV")
        rows, _ = self._read_csv(quality_path)
        rows = self._scope_rows(rows)
        yield_field = self._first_field(rows, self.YIELD_FIELDS)
        if not rows or not yield_field:
            return self._blocked("day_2", "没有可比较的目标质量数据")

        relative_quality = quality_path.relative_to(self.data_root).as_posix()
        comparison_window = self.state.get("comparison_window", {})
        baseline_end = str(
            comparison_window.get("baseline_end", self.target.start_date)
        )
        anomaly_start = str(
            comparison_window.get("anomaly_start", self.target.end_date)
        )
        if baseline_end < anomaly_start:
            comparison = self.workspace.compare_quality_factors(
                relative_quality,
                baseline_end=baseline_end,
                anomaly_start=anomaly_start,
                product=self.target.product,
                line=self.target.line,
                start_date=self.target.start_date,
                end_date=self.target.end_date,
            )
        else:
            comparison = self.workspace.profile_quality_csv(
                relative_quality,
                product=self.target.product,
                line=self.target.line,
                start_date=self.target.start_date,
                end_date=self.target.end_date,
            )
        evidence = self._store_tool_result(comparison)
        hypotheses: list[dict[str, Any]] = []
        for dimension, aliases in self.DIMENSIONS.items():
            field = self._first_field(rows, aliases)
            grouped = self._group_yields(rows, field, yield_field) if field else {}
            support: list[str] = []
            counter: list[str] = []
            missing: list[str] = []
            status = "insufficient_evidence"
            observed_spread: float | None = None
            if len(grouped) >= 2:
                ordered = sorted(grouped.items(), key=lambda item: item[1])
                spread = round(ordered[-1][1] - ordered[0][1], 6)
                observed_spread = spread
                support.append(
                    f"{field} 分组均值极差为 {spread:.4f}"
                    f"（{ordered[0][0]} 至 {ordered[-1][0]}）。"
                )
                if abs(spread) <= 0.01:
                    counter.append("分组良率极差不超过 0.010，当前数据不支持明显分层。")
                    status = "deprioritized"
                else:
                    status = "candidate"
            else:
                missing.append(f"缺少至少两个可比较的 {dimension} 分组。")
            hypotheses.append(
                {
                    "hypothesis_id": f"H-{dimension.upper().replace('_', '-')}",
                    "dimension": dimension,
                    "claim": f"{dimension} 差异可能与良率波动相关。",
                    "status": status,
                    "observed_spread": observed_spread,
                    "supporting_evidence": support,
                    "counter_evidence": counter,
                    "missing_data": missing,
                    "minimum_next_action": (
                        f"在同产品同产线内固定其他条件，补充或复核 {dimension} 分组对照；"
                        "只验证相关性，不改写生产参数。"
                    ),
                    "evidence_ids": (evidence["evidence_id"],),
                }
            )

        self.state["hypotheses"] = hypotheses
        self.state["phase"] = "day_2_completed"
        self._record_notes("hypothesis", hypotheses)
        self._save_state()
        return self._stage_result(
            "day_2",
            "completed",
            hypotheses=hypotheses,
            evidence_ids=[evidence["evidence_id"]],
        )

    def day_3_validation_plan(
        self,
        approver: str | None = None,
        approved: bool | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        guard = self._guard_phase(
            "day_3", {"day_2_completed", "awaiting_human_approval"}
        )
        if guard is not None:
            return guard
        hypotheses = [
            item
            for item in self.state.get("hypotheses", [])
            if item.get("status") in {"candidate", "insufficient_evidence"}
        ]
        todos = [
            {
                "todo_id": f"T-{index:02d}",
                "hypothesis_id": item["hypothesis_id"],
                "action": item["minimum_next_action"],
                "owner": "待人工指定",
                "due_date": self.target.end_date,
                "status": "proposed",
                "depends_on": "人工批准离线验证边界",
            }
            for index, item in enumerate(hypotheses, 1)
        ]
        risks = [
            {
                "risk_id": "R-CAUSALITY",
                "statement": "观察性分组差异只能支持相关性，不能确认为因果。",
                "severity": "high",
                "mitigation": "仅输出相关性结论，需受控验证和人工复核。",
                "status": "open",
            },
            {
                "risk_id": "R-PRODUCTION-WRITE",
                "statement": "智能体不得写入 PLC、MES、DCS 或生产参数。",
                "severity": "critical",
                "mitigation": "维持只读工具和人工审批闸门。",
                "status": "controlled_by_human_gate",
            },
        ]
        self.state["todos"] = todos
        self.state["risks"] = risks
        self._record_notes("todo", todos)
        self._record_notes("risk", risks)
        if approved is None:
            self.state["phase"] = "awaiting_human_approval"
            self.build_context(force_compression=True)
            self._save_state()
            return self._stage_result(
                "day_3",
                "awaiting_human_approval",
                message="验证计划仅为提案，尚未执行，也不是确认结论。",
                todos=todos,
                risks=risks,
            )
        if not approver or not approver.strip():
            raise ValueError("提供人工决定时 approver 不能为空")

        decision = {
            "decision_id": f"D-{len(self.state.get('decisions', [])) + 1:02d}",
            "approver": approver.strip(),
            "approved": bool(approved),
            "comment": comment,
            "scope": "仅批准或拒绝离线验证计划，不授权生产写入",
        }
        self.state.setdefault("decisions", []).append(decision)
        self.state["human_decision_count"] += 1
        self.state["phase"] = (
            "validation_plan_approved" if approved else "validation_plan_rejected"
        )
        self._record_notes("decision", [decision])
        self.build_context(force_compression=True)
        self._save_state()
        return self._stage_result(
            "day_3",
            self.state["phase"],
            message=(
                "验证计划已获人工批准，但仍未执行、不得视为确认结论。"
                if approved
                else "验证计划已被人工拒绝，未执行。"
            ),
            decision=decision,
            todos=todos,
            risks=risks,
        )

    def week_later_review(self) -> dict[str, Any]:
        guard = self._guard_phase(
            "week_later", {"validation_plan_approved", "week_later_reviewed"}
        )
        if guard is not None:
            return guard
        self._restore_from_notes()
        invalidated_ids = self._invalidate_changed_evidence()

        review_path = self._find_existing((self.REVIEW_NAME,))
        review: dict[str, Any]
        if review_path is None:
            review = {
                "status": "missing_new_data",
                "statement": "未找到一周后质量结果，保留现有假设状态。",
            }
        else:
            rows, result = self._read_csv(review_path, enforce_target_dates=False)
            rows = self._scope_rows(rows, enforce_dates=False)
            yield_field = self._first_field(rows, self.YIELD_FIELDS)
            values = [
                value
                for row in rows
                if yield_field
                and (value := self._number(row.get(yield_field))) is not None
            ]
            evidence = self._store_tool_result(result)
            review = {
                "status": "reviewed" if values else "insufficient_evidence",
                "mean_yield": round(fmean(values), 6) if values else None,
                "row_count": len(rows),
                "evidence_id": evidence["evidence_id"],
                "statement": (
                    "已更新一周后质量结果；该观察只能更新相关性判断，"
                    "不能证明任何候选因素导致良率变化。"
                ),
            }
        if review.get("status") == "reviewed":
            review_evidence_id = str(review["evidence_id"])
            hypotheses = self.state.get("hypotheses", [])
            for hypothesis in hypotheses:
                if hypothesis.get("status") == "candidate":
                    hypothesis["status"] = "needs_controlled_validation"
                    hypothesis["week_later_evidence_ids"] = [review_evidence_id]
                    hypothesis["week_later_observation"] = (
                        "复盘窗口良率发生变化，但多个条件同步变化，"
                        "仍需单变量离线对照验证。"
                    )
            ranked_signals = sorted(
                (
                    hypothesis
                    for hypothesis in hypotheses
                    if hypothesis.get("status") == "needs_controlled_validation"
                    if isinstance(hypothesis.get("observed_spread"), (int, float))
                ),
                key=lambda item: abs(float(item["observed_spread"])),
                reverse=True,
            )
            review["early_signals"] = [
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "dimension": item["dimension"],
                    "observed_spread": item["observed_spread"],
                }
                for item in ranked_signals[:3]
            ]
            self._record_notes("hypothesis", hypotheses)
        self.state["week_later_review"] = review
        self.state["phase"] = "week_later_reviewed"
        self._record_notes("summary", [review])
        self._save_state()
        return self._stage_result(
            "week_later",
            "completed",
            invalidated_evidence_ids=sorted(invalidated_ids),
            review=review,
        )

    def build_context(self, force_compression: bool = False) -> dict[str, Any]:
        payload = {
            "target": asdict(self.target),
            "phase": self.state["phase"],
            "facts": self.state.get("facts", []),
            "hypotheses": self.state.get("hypotheses", []),
            "todos": self.state.get("todos", []),
            "risks": self.state.get("risks", []),
            "decisions": self.state.get("decisions", []),
        }
        context = self._build_external_context(payload, force_compression)
        compressed = force_compression or bool(context.get("compressed"))
        if compressed:
            self.state["compression_count"] += 1
            summary = {
                "phase": self.state["phase"],
                "fact_count": len(payload["facts"]),
                "hypothesis_count": len(payload["hypotheses"]),
                "todo_count": len(payload["todos"]),
                "decision_count": len(payload["decisions"]),
            }
            self._record_notes("summary", [summary])
        self.state["last_context"] = context
        self._save_state()
        return context

    def get_report(self) -> dict[str, Any]:
        return {
            "target": asdict(self.target),
            "phase": self.state["phase"],
            "facts": self.state.get("facts", []),
            "hypotheses": self.state.get("hypotheses", []),
            "todos": self.state.get("todos", []),
            "risks": self.state.get("risks", []),
            "decisions": self.state.get("decisions", []),
            "week_later_review": self.state.get("week_later_review"),
            "compression_count": self.state["compression_count"],
            "recovery_count": self.state["recovery_count"],
            "human_decision_count": self.state["human_decision_count"],
            "note_counts": self.notes.counts(
                scenario_id=self.target.investigation_id
            ),
            "evidence_count": len(self.state.get("evidence", {})),
            "audit_log_path": str(self.workspace.audit_log_path),
            "state_path": str(self.state_path),
        }

    @classmethod
    def run_three_day_demo(
        cls,
        data_root: str | Path,
        state_dir: str | Path,
        target: InvestigationTarget | Mapping[str, Any],
        *,
        approver: str = "教学审核人",
        approved: bool | None = None,
        comment: str = "批准离线验证计划",
    ) -> dict[str, Any]:
        first_session = cls(data_root, state_dir, target)
        day_1 = first_session.day_1_scope()
        if day_1["status"] != "completed":
            return {
                "status": day_1["status"],
                "day_1": day_1,
                "day_2": None,
                "day_3": None,
                "week_later": None,
                "report": first_session.get_report(),
            }
        second_session = cls(data_root, state_dir, target)
        day_2 = second_session.day_2_hypotheses()
        if day_2["status"] != "completed":
            return {
                "status": day_2["status"],
                "day_1": day_1,
                "day_2": day_2,
                "day_3": None,
                "week_later": None,
                "report": second_session.get_report(),
            }
        day_3 = second_session.day_3_validation_plan(
            approver=approver, approved=approved, comment=comment
        )
        final_session = second_session
        week_later = None
        if day_3["status"] == "validation_plan_approved":
            final_session = cls(data_root, state_dir, target)
            week_later = final_session.week_later_review()
        overall_status = day_3["status"]
        if week_later is not None:
            overall_status = (
                "completed"
                if week_later["status"] == "completed"
                else week_later["status"]
            )
        return {
            "status": overall_status,
            "day_1": day_1,
            "day_2": day_2,
            "day_3": day_3,
            "week_later": week_later,
            "report": final_session.get_report(),
        }

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            state = self._validated_state(payload)
            if state.get("target") != asdict(self.target):
                raise ValueError("已有状态的调查目标与本次 target 不一致")
            state["recovery_count"] = int(state.get("recovery_count", 0)) + 1
            self._write_json(self.state_path, state)
            return state
        state = {
            "schema_version": self.STATE_VERSION,
            "target": asdict(self.target),
            "phase": "initialized",
            "facts": [],
            "hypotheses": [],
            "todos": [],
            "risks": [],
            "decisions": [],
            "evidence": {},
            "compression_count": 0,
            "recovery_count": 0,
            "human_decision_count": 0,
        }
        self._write_json(self.state_path, state)
        return state

    @classmethod
    def _validated_state(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("调查状态必须是 JSON 对象")
        if payload.get("schema_version") != cls.STATE_VERSION:
            raise ValueError("调查状态 schema_version 不受支持")
        required_lists = ("facts", "hypotheses", "todos", "risks", "decisions")
        for name in required_lists:
            if not isinstance(payload.get(name), list):
                raise ValueError(f"调查状态字段 {name} 必须是列表")
        if not isinstance(payload.get("target"), dict):
            raise ValueError("调查状态字段 target 必须是对象")
        if not isinstance(payload.get("evidence"), dict):
            raise ValueError("调查状态字段 evidence 必须是对象")
        if not isinstance(payload.get("phase"), str) or not payload["phase"].strip():
            raise ValueError("调查状态字段 phase 必须是非空字符串")
        for name in (
            "compression_count",
            "recovery_count",
            "human_decision_count",
        ):
            value = payload.get(name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"调查状态字段 {name} 必须是非负整数")
        return dict(payload)

    def _save_state(self) -> None:
        self._write_json(self.state_path, self.state)

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _find_existing(self, names: Iterable[str]) -> Path | None:
        for name in names:
            requested = self.data_root / name
            if requested.is_symlink():
                continue
            candidate = requested.resolve()
            if candidate.parent == self.data_root and candidate.is_file():
                return candidate
        return None

    def _read_csv(
        self,
        path: Path,
        *,
        enforce_target_dates: bool = True,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        relative_path = path.relative_to(self.data_root).as_posix()
        profile = self.workspace.profile_quality_csv(
            relative_path,
            product=self.target.product,
            line=self.target.line,
            start_date=self.target.start_date if enforce_target_dates else None,
            end_date=self.target.end_date if enforce_target_dates else None,
        )
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle)), profile

    def _scope_rows(
        self,
        rows: Iterable[Mapping[str, str]],
        *,
        enforce_dates: bool = True,
    ) -> list[dict[str, str]]:
        source_rows = [dict(row) for row in rows]
        product_field = self._first_field(
            source_rows, ("product", "product_id", "产品")
        )
        line_field = self._first_field(source_rows, ("line", "line_id", "产线"))
        date_field = self._first_field(
            source_rows,
            ("inspection_date", "date", "production_date", "日期"),
        )
        scoped: list[dict[str, str]] = []
        for row in source_rows:
            product = str(row.get(product_field, "")).strip()
            line = str(row.get(line_field, "")).strip()
            day = str(row.get(date_field, "")).strip()
            if product_field and product != self.target.product:
                continue
            if line_field and line != self.target.line:
                continue
            if enforce_dates and date_field:
                if not day or not (
                    self.target.start_date <= day <= self.target.end_date
                ):
                    continue
            scoped.append(row)
        if date_field:
            scoped.sort(key=lambda row: str(row.get(date_field, "")).strip())
        return scoped

    @classmethod
    def _first_field(
        cls, rows: Iterable[Mapping[str, str]], aliases: Iterable[str]
    ) -> str:
        keys = {key for row in rows for key in row}
        return next((alias for alias in aliases if alias in keys), "")

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip()
        try:
            number = float(text.rstrip("%"))
        except ValueError:
            return None
        if text.endswith("%"):
            number /= 100.0
        return number

    def _group_yields(
        self, rows: Iterable[Mapping[str, str]], field: str, yield_field: str
    ) -> dict[str, float]:
        values: dict[str, list[float]] = {}
        for row in rows:
            group = str(row.get(field, "")).strip()
            value = self._number(row.get(yield_field))
            if group and value is not None:
                values.setdefault(group, []).append(value)
        return {
            group: round(fmean(group_values), 6)
            for group, group_values in sorted(values.items())
        }

    def _store_tool_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        evidence = dict(result["evidence"])
        evidence_id = str(evidence["evidence_id"])
        self.state.setdefault("evidence", {})[evidence_id] = evidence
        self.state.setdefault("tool_summaries", []).append(dict(result["summary"]))
        return evidence

    def _validate_evidence(self) -> set[str]:
        validation = self.workspace.validate_all_evidence()
        state_ids = set(self.state.get("evidence", {}))
        invalidated = {
            item["evidence_id"]
            for item in validation["evidence"]
            if item["evidence_id"] in state_ids
            if item["status"] in {"invalidated", "missing"}
        }
        indexed_ids = {item["evidence_id"] for item in validation["evidence"]}
        missing_from_index = state_ids - indexed_ids
        invalidated.update(missing_from_index)
        for evidence_id in missing_from_index:
            previous = dict(self.state["evidence"][evidence_id])
            previous["status"] = "missing"
            previous["validation"] = "evidence index entry missing"
            self.state["evidence"][evidence_id] = previous
        for item in validation["evidence"]:
            if item["evidence_id"] in self.state.get("evidence", {}):
                self.state["evidence"][item["evidence_id"]] = item
        return invalidated

    def _invalidate_changed_evidence(self) -> set[str]:
        invalidated_ids = self._validate_evidence()
        if not invalidated_ids:
            return invalidated_ids
        for fact in self.state.get("facts", []):
            if set(fact.get("evidence_ids", ())) & invalidated_ids:
                fact["status"] = "invalidated"
                self._mark_note_for_review("fact", fact)
        for hypothesis in self.state.get("hypotheses", []):
            if set(hypothesis.get("evidence_ids", ())) & invalidated_ids:
                hypothesis["status"] = "needs_review"
                self._mark_note_for_review("hypothesis", hypothesis)
        return invalidated_ids

    def _record_notes(
        self,
        note_type: str,
        records: Iterable[Mapping[str, Any]],
    ) -> None:
        for record in records:
            payload = dict(record)
            identity = (
                payload.get("fact_id")
                or payload.get("hypothesis_id")
                or payload.get("decision_id")
                or payload.get("todo_id")
                or payload.get("risk_id")
            )
            if not identity:
                note_count = len(
                    self.notes.list(scenario_id=self.target.investigation_id)
                )
                identity = (
                    f"{payload.get('phase', self.state['phase'])}-{note_count}"
                )
            identity = str(identity)
            note_id = f"{self.target.investigation_id}-{note_type}-{identity}"
            fields = self._note_fields(note_type, payload)
            title = str(
                payload.get("statement")
                or payload.get("claim")
                or payload.get("action")
                or identity
            )[:120]
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if self.notes.get(note_id) is not None:
                self.notes.update(
                    note_id,
                    title=title,
                    content=content,
                    record=payload,
                    **fields,
                )
            else:
                self.notes.create(
                    scenario_id=self.target.investigation_id,
                    note_type=note_type,
                    title=title,
                    content=content,
                    note_id=note_id,
                    record=payload,
                    **fields,
                )

    def _restore_from_notes(self) -> None:
        restored_records = self.notes.list(
            scenario_id=self.target.investigation_id
        )
        if not restored_records:
            return
        sections = {
            "fact": "facts",
            "hypothesis": "hypotheses",
            "todo": "todos",
            "risk": "risks",
            "decision": "decisions",
        }
        recovered: dict[str, list[dict[str, Any]]] = {
            section: [] for section in sections.values()
        }
        for note in restored_records:
            if note.note_type in sections and isinstance(note.fields.get("record"), dict):
                recovered[sections[note.note_type]].append(dict(note.fields["record"]))
        for section, records in recovered.items():
            if records:
                self.state[section] = records
        self.state["recovered_note_count"] = len(restored_records)

    def _build_external_context(
        self, payload: Mapping[str, Any], force_compression: bool
    ) -> dict[str, Any]:
        builder = (
            InvestigationContextBuilder(max_notes=6, max_chars=3_000)
            if force_compression
            else self.context_builder
        )
        built = builder.build(
            target=asdict(self.target),
            state={
                "stage": self.state["phase"],
                "limitations": [
                    "只读离线分析",
                    "观察性结果不得解释为因果",
                    "计划需人工批准",
                ],
            },
            notes=self.notes.list(scenario_id=self.target.investigation_id),
            tool_summaries=self.state.get("tool_summaries", []),
            current_question=(
                "压缩历史并形成待人工审批的最小离线验证计划"
                if force_compression
                else f"继续 {self.state['phase']} 调查"
            ),
        )
        return built.to_dict()

    def _note_fields(
        self, note_type: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if note_type == "fact":
            return {"evidence_ids": list(payload.get("evidence_ids", []))}
        if note_type == "hypothesis":
            evidence_ids = list(payload.get("evidence_ids", []))
            return {
                "supporting_evidence_ids": (
                    evidence_ids if payload.get("supporting_evidence") else []
                ),
                "counter_evidence_ids": (
                    evidence_ids if payload.get("counter_evidence") else []
                ),
                "missing_data": "；".join(payload.get("missing_data", [])) or "无",
                "next_action": str(payload.get("minimum_next_action", "待人工指定")),
            }
        if note_type == "decision":
            return {
                "human_confirmed": True,
                "approver": str(payload["approver"]),
            }
        if note_type == "todo":
            return {
                "owner": str(payload.get("owner", "待人工指定")),
                "due_date": str(payload.get("due_date", self.target.end_date)),
                "dependencies": [str(payload.get("depends_on", "人工审批"))],
            }
        if note_type == "risk":
            return {
                "severity": str(payload.get("severity", "medium")),
                "mitigation": str(payload.get("mitigation", "人工复核")),
            }
        retained = [
            note.id
            for note in self.notes.list(scenario_id=self.target.investigation_id)
            if note.note_type != "summary"
        ]
        return {
            "stage": str(payload.get("phase", self.state["phase"])),
            "retained_note_ids": retained,
            "compressed_count": int(self.state.get("compression_count", 0)),
        }

    def _mark_note_for_review(
        self,
        note_type: str,
        record: Mapping[str, Any],
    ) -> None:
        identity = str(
            record.get("fact_id") or record.get("hypothesis_id")
        )
        note_id = f"{self.target.investigation_id}-{note_type}-{identity}"
        if self.notes.get(note_id) is None:
            return
        changes = {
            "status": "superseded",
            "review_status": "needs_review",
            "validation_status": "invalidated",
            "record": dict(record),
        }
        self.notes.update(note_id, **changes)

    def _guard_phase(
        self,
        stage: str,
        allowed_phases: set[str],
    ) -> dict[str, Any] | None:
        current_phase = str(self.state.get("phase", ""))
        if current_phase in allowed_phases:
            return None
        expected = "、".join(sorted(allowed_phases))
        return self._stage_result(
            stage,
            "blocked",
            message=(
                f"当前阶段 {current_phase!r} 不能执行 {stage}；"
                f"允许的前序阶段为：{expected}。"
            ),
            current_phase=current_phase,
        )

    def _blocked(self, stage: str, message: str) -> dict[str, Any]:
        self.state["phase"] = f"{stage}_blocked"
        self._save_state()
        return self._stage_result(stage, "blocked", message=message)

    def _stage_result(self, stage: str, status: str, **items: Any) -> dict[str, Any]:
        return {
            "investigation_id": self.target.investigation_id,
            "stage": stage,
            "status": status,
            **items,
        }
