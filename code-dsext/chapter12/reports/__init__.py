from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks import file_sha256


def write_evaluation_artifacts(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    cases_path: str | Path,
    traces_path: str | Path,
) -> dict[str, Any]:
    target = Path(output_dir)
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"输出目录必须为空：{target}")
    target.mkdir(parents=True, exist_ok=True)
    source_manifest = {
        "cases": {
            "path": str(Path(cases_path).resolve()),
            "sha256": file_sha256(cases_path),
        },
        "traces": {
            "path": str(Path(traces_path).resolve()),
            "sha256": file_sha256(traces_path),
        },
    }
    payload = deepcopy(dict(report))
    payload["source_manifest"] = source_manifest
    payload["artifact_fingerprint"] = _fingerprint(payload)
    json_path = target / "evaluation_report.json"
    markdown_path = target / "evaluation_report.md"
    audit_path = target / "audit.jsonl"
    manifest_path = target / "manifest.json"
    json_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        build_markdown_report(payload),
        encoding="utf-8",
    )
    recorded_at = datetime.now(timezone.utc).isoformat()
    audit_payload = {
        "event": "industrial_agent_evaluation_completed",
        "report_fingerprint": payload["report_fingerprint"],
        "artifact_fingerprint": payload["artifact_fingerprint"],
        "cases_sha256": source_manifest["cases"]["sha256"],
        "traces_sha256": source_manifest["traces"]["sha256"],
        "safety_gate_passed": payload["safety_gate"]["passed"],
        "release_status": payload["release_status"],
        "production_control": "prohibited",
    }
    audit_record = {
        "event_id": "AUD-" + _fingerprint(audit_payload)[:16],
        "recorded_at": recorded_at,
        "status": "completed",
        "payload": audit_payload,
    }
    audit_path.write_text(
        json.dumps(audit_record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "created_at": recorded_at,
        "status": "completed",
        "release_status": payload["release_status"],
        "report_fingerprint": payload["report_fingerprint"],
        "artifact_fingerprint": payload["artifact_fingerprint"],
        "inputs": source_manifest,
        "outputs": {
            "evaluation_report.json": file_sha256(json_path),
            "evaluation_report.md": file_sha256(markdown_path),
            "audit.jsonl": file_sha256(audit_path),
        },
    }
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "output_dir": str(target.resolve()),
        "report_path": str(json_path.resolve()),
        "markdown_path": str(markdown_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "audit_path": str(audit_path.resolve()),
        "report": payload,
    }


def build_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# 工业数据分析智能体评测报告",
        "",
        f"- 评测模式：`{report['evaluation_mode']}`",
        f"- 发布状态：`{report['release_status']}`",
        f"- 生产控制：`{report['production_control']}`",
        f"- 报告指纹：`{report['report_fingerprint']}`",
        "",
        "## 智能体分数与置信区间",
        "",
        "| 智能体 | 样例数 | 通过率 | 95% CI | 平均质量分 | 安全门禁 |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for agent, summary in report["agent_summaries"].items():
        interval = summary["ci95"]
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(agent),
                    str(summary["case_count"]),
                    _percent(summary["pass_rate"]),
                    (
                        f"{_percent(interval['lower'])}"
                        f"–{_percent(interval['upper'])}"
                    ),
                    f"{summary['mean_quality_score']:.3f}",
                    "通过" if summary["safety_gate_passed"] else "阻断",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 分层结果",
            "",
            "| 层级 | 轨迹数 | 通过率 | 95% CI | 平均质量分 |",
            "| --- | ---: | ---: | --- | ---: |",
        ]
    )
    for level, summary in report["level_summaries"].items():
        interval = summary["pass_rate_ci95"]
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(level),
                    str(summary["count"]),
                    _percent(summary["pass_rate"]),
                    (
                        f"{_percent(interval['lower'])}"
                        f"–{_percent(interval['upper'])}"
                    ),
                    f"{summary['mean_quality_score']:.3f}",
                )
            )
            + " |"
        )
    gate = report["safety_gate"]
    lines.extend(
        [
            "",
            "## 安全硬门禁",
            "",
            f"- 状态：{'通过' if gate['passed'] else '阻断'}",
            f"- 策略：`{gate['failure_mode']}`",
            f"- 高风险/L4 轨迹数：{gate['high_risk_evaluated_count']}",
        ]
    )
    if gate["failures"]:
        lines.append("- 门禁失败：")
        for failure in gate["failures"]:
            lines.append(
                "  - "
                f"{_cell(failure['agent_version'])}/"
                f"{_cell(failure['case_id'])}："
                + ", ".join(failure["violations"])
            )
    comparison = report.get("pairwise_comparison")
    if isinstance(comparison, Mapping):
        interval = comparison["decisive_win_rate_ci95"]
        lines.extend(
            [
                "",
                "## 盲化成对比较",
                "",
                (
                    f"- `{comparison['first_agent']}` 相对 "
                    f"`{comparison['second_agent']}`："
                    f"{comparison['wins']} 胜 / "
                    f"{comparison['ties']} 平 / "
                    f"{comparison['losses']} 负"
                ),
                (
                    "- 决胜样例胜率 95% CI："
                    f"{_percent(interval['lower'])}"
                    f"–{_percent(interval['upper'])}"
                ),
                f"- 答案顺序：`{comparison['ordering']}`",
            ]
        )
        for title, key in (
            ("按任务层级", "by_level"),
            ("按设备类型", "by_device_type"),
            ("按风险等级", "by_risk_level"),
            ("按失败类型", "by_failure_type"),
        ):
            lines.extend(
                [
                    "",
                    f"### {title}",
                    "",
                    "| 切片 | 数量 | 胜 | 平 | 负 |",
                    "| --- | ---: | ---: | ---: | ---: |",
                ]
            )
            slices = comparison[key]
            if slices:
                for value, counts in slices.items():
                    lines.append(
                        "| "
                        + " | ".join(
                            (
                                _cell(value),
                                str(counts["count"]),
                                str(counts["wins"]),
                                str(counts["ties"]),
                                str(counts["losses"]),
                            )
                        )
                        + " |"
                    )
            else:
                lines.append("| 无 | 0 | 0 | 0 | 0 |")
    lines.extend(["", "## 失败案例", ""])
    failures = report["failure_cases"]
    if failures:
        for failure in failures:
            lines.append(
                "- "
                f"`{failure['agent_version']}` / `{failure['case_id']}`"
                f"（{failure['level']}，质量分 "
                f"{failure['quality_score']:.3f}）："
                + ", ".join(failure["failed_checks"])
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 改进建议", ""])
    for agent, recommendations in report["improvement_recommendations"].items():
        if recommendations:
            lines.append(f"- `{agent}`：")
            lines.extend(
                f"  - {_cell(recommendation)}"
                for recommendation in recommendations
            )
        else:
            lines.append(f"- `{agent}`：无")
    lines.extend(["", "## 人工审核与 Judge 覆盖", ""])
    overrides = report["review_summary"]["judge_overrides"]
    if overrides:
        for override in overrides:
            lines.append(
                "- "
                f"`{override['case_id']}`：Judge="
                f"`{override['judge_decision']}`，专家="
                f"`{override['expert_decision']}`；"
                f"{_cell(override['rationale'])}"
            )
    else:
        lines.append("- 无 Judge 覆盖记录")
    lines.extend(
        [
            "",
            "> 本报告来自脱敏静态轨迹的离线确定性评分，"
            "不代表生产部署许可；最终结论仍需人工审批。",
            "",
        ]
    )
    return "\n".join(lines)


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _percent(value: float) -> str:
    return f"{float(value):.1%}"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = ["build_markdown_report", "write_evaluation_artifacts"]
