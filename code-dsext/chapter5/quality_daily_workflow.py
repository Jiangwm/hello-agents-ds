from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from quality_metrics import (
    ValidationOutcome,
    calculate_metric_hash,
    calculate_metrics,
    sha256_file,
    validate_inputs,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_KNOWLEDGE_DIR = BASE_DIR / "knowledge"
DEFAULT_WORKFLOW_FILE = BASE_DIR / "workflows" / "quality_daily_workflow.json"
DEFAULT_PROMPT_FILE = BASE_DIR / "prompts" / "quality_daily_report.md"
REQUIRED_NODE_IDS = {
    "input_validation",
    "statistics",
    "knowledge_retrieval",
    "report_generation",
    "human_approval",
    "message_distribution",
    "audit_archive",
}


class WorkflowError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_workflow_definition(path: Path) -> dict[str, object]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise WorkflowError("工作流定义必须是 JSON 对象")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise WorkflowError("工作流定义缺少 nodes 列表")
    node_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    missing_nodes = sorted(REQUIRED_NODE_IDS - node_ids)
    if missing_nodes:
        raise WorkflowError(
            "工作流缺少验收节点：" + ", ".join(missing_nodes)
        )
    if len(node_ids) != len(nodes):
        raise WorkflowError("工作流节点 ID 缺失或重复")
    if not isinstance(payload.get("data_quality_policy"), dict):
        raise WorkflowError("工作流缺少 data_quality_policy")
    return payload


def _load_knowledge(
    knowledge_dir: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    standards_payload = _read_json(knowledge_dir / "quality_standards.json")
    cases_payload = _read_json(knowledge_dir / "defect_cases.json")
    if (
        not isinstance(standards_payload, dict)
        or not isinstance(standards_payload.get("standards"), list)
    ):
        raise WorkflowError("quality_standards.json 缺少 standards 列表")
    if (
        not isinstance(cases_payload, dict)
        or not isinstance(cases_payload.get("cases"), list)
    ):
        raise WorkflowError("defect_cases.json 缺少 cases 列表")
    standards = [
        item
        for item in standards_payload["standards"]
        if isinstance(item, dict)
    ]
    cases = [
        item for item in cases_payload["cases"] if isinstance(item, dict)
    ]
    return standards, cases


def _transition(
    state: dict[str, object],
    target: str,
    reason: str,
) -> None:
    transitions = state.setdefault("state_transitions", [])
    if not isinstance(transitions, list):
        raise WorkflowError("state_transitions 格式损坏")
    transitions.append(
        {
            "from": state.get("status", ""),
            "to": target,
            "reason": reason,
            "at": _now(),
        }
    )
    state["status"] = target


def _record_node(
    audit: dict[str, object],
    node_id: str,
    status: str,
    detail: str,
) -> None:
    node_trace = audit.setdefault("node_trace", [])
    if not isinstance(node_trace, list):
        raise WorkflowError("node_trace 格式损坏")
    node_trace.append(
        {
            "node_id": node_id,
            "status": status,
            "detail": detail,
            "at": _now(),
        }
    )


def _record_event(
    audit: dict[str, object],
    event: str,
    actor: str,
    detail: str,
) -> None:
    events = audit.setdefault("events", [])
    if not isinstance(events, list):
        raise WorkflowError("events 格式损坏")
    events.append(
        {
            "event": event,
            "actor": actor,
            "detail": detail,
            "at": _now(),
        }
    )


def _build_run_id(
    report_date: date,
    manifest: Sequence[Mapping[str, object]],
) -> str:
    source = "|".join(
        f"{item.get('role') or Path(str(item.get('file'))).name}:"
        f"{item.get('sha256')}"
        for item in manifest
    )
    suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"QUALITY-{report_date.strftime('%Y%m%d')}-{suffix}"


def _build_source_manifest(
    input_manifest: Sequence[Mapping[str, object]],
    workflow_file: Path,
    knowledge_dir: Path,
    prompt_file: Path,
) -> list[dict[str, object]]:
    manifest = [dict(item) for item in input_manifest]
    for path, role in (
        (workflow_file, "workflow_definition"),
        (knowledge_dir / "quality_standards.json", "quality_standard"),
        (knowledge_dir / "defect_cases.json", "historical_case"),
        (prompt_file, "prompt"),
    ):
        manifest.append(
            {
                "file": str(path.resolve()),
                "role": role,
                "sha256": sha256_file(path),
            }
        )
    return manifest


def _retrieve_context(
    metrics: Mapping[str, object],
    standards: Sequence[Mapping[str, object]],
    cases: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    standards_by_product = {
        str(item["product_id"]): item for item in standards
    }
    cases_by_defect = {
        str(item["defect_code"]): item for item in cases
    }
    matches: list[dict[str, object]] = []
    exceptions: list[dict[str, object]] = []
    report_date = date.fromisoformat(str(metrics["report_date"]))
    anomalies = metrics.get("anomalies", [])
    if not isinstance(anomalies, list):
        raise WorkflowError("metrics.anomalies 格式损坏")

    for anomaly in anomalies:
        if not isinstance(anomaly, dict):
            continue
        standard = standards_by_product.get(str(anomaly["product_id"]))
        defect_case = cases_by_defect.get(
            str(anomaly["dominant_defect_code"])
        )
        matches.append(
            {
                "exception_id": anomaly["exception_id"],
                "batch_id": anomaly["batch_id"],
                "standard": standard,
                "historical_case": defect_case,
                "retrieval_status": (
                    "matched"
                    if standard is not None and defect_case is not None
                    else "partial"
                ),
            }
        )
        exceptions.append(
            {
                "exception_id": anomaly["exception_id"],
                "batch_id": anomaly["batch_id"],
                "product_id": anomaly["product_id"],
                "dominant_defect_code": anomaly["dominant_defect_code"],
                "triggered_rules": anomaly["triggers"],
                "standard_id": (
                    standard.get("standard_id") if standard else ""
                ),
                "case_id": defect_case.get("case_id") if defect_case else "",
                "owner_role": (
                    defect_case.get("owner_role")
                    if defect_case
                    else "质量工程师"
                ),
                "due_date": (report_date + timedelta(days=1)).isoformat(),
                "status": "open",
                "resolution": "",
                "evidence_ref": "",
                "closed_by": "",
                "closed_at": "",
            }
        )

    context = {
        "matches": matches,
        "retrieval_count": len(matches),
    }
    return context, exceptions


def _validate_narrative(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise WorkflowError("人工修订说明不能为空")
    if any(character.isdigit() for character in normalized) or re.search(
        r"[%％]", normalized
    ):
        raise WorkflowError(
            "人工修订说明不得包含数字或百分号；数值只能来自确定性指标区块"
        )
    return normalized


def _generate_prompt_narrative(
    context: Mapping[str, object],
    prompt_text: str,
) -> str:
    template_name = (
        "offline_with_anomalies"
        if int(context.get("retrieval_count", 0)) > 0
        else "offline_without_anomalies"
    )
    pattern = (
        rf"\[{template_name}\]\s*(.*?)\s*\[/{template_name}\]"
    )
    match = re.search(pattern, prompt_text, flags=re.DOTALL)
    if match is None:
        raise WorkflowError(f"提示词缺少离线模板：{template_name}")
    return _validate_narrative(match.group(1))


def _percentage(value: object) -> str:
    return f"{float(value):.2%}"


def _render_group_table(
    title: str,
    key: str,
    rows: Sequence[Mapping[str, object]],
) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| 分组 | 产量 | 一次合格数 | 一次合格率 | 缺陷数 | 缺陷率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row[key]} | {row['output_qty']} | {row['qualified_qty']} | "
            f"{_percentage(row['first_pass_yield'])} | {row['defect_qty']} | "
            f"{_percentage(row['defect_rate'])} |"
        )
    lines.append("")
    return lines


def _render_report(
    metrics: Mapping[str, object],
    context: Mapping[str, object],
    exceptions: Sequence[Mapping[str, object]],
    report_status: str,
    report_version: int,
    narrative: str,
    reviewer: str = "",
    review_comment: str = "",
) -> str:
    grouped = metrics["grouped_defect_rates"]
    if not isinstance(grouped, dict):
        raise WorkflowError("grouped_defect_rates 格式损坏")
    top_defects = metrics["top_defects"]
    anomalies = metrics["anomalies"]
    if not isinstance(top_defects, list) or not isinstance(anomalies, list):
        raise WorkflowError("缺陷指标格式损坏")

    lines = [
        f"# {metrics['report_date']} 质量日报",
        "",
        f"> 状态：{report_status} ｜ 报告版本：v{report_version} ｜ "
        f"指标摘要：`{metrics['metric_hash']}`",
        "",
        "## 质量工程师摘要",
        "",
        narrative,
        "",
        "## 核心确定性指标",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 产量 | {metrics['total_output']} |",
        f"| 检验数 | {metrics['inspected_count']} |",
        f"| 一次合格数 | {metrics['qualified_count']} |",
        f"| 一次合格率 | {_percentage(metrics['first_pass_yield'])} |",
        f"| 缺陷数 | {metrics['defect_count']} |",
        f"| 缺陷率 | {_percentage(metrics['defect_rate'])} |",
        f"| 必填数据缺失率 | "
        f"{_percentage(metrics['data_quality']['missing_rate'])} |",
        f"| 迟到记录数 | "
        f"{metrics['data_quality']['late_record_count']} |",
        "",
        f"对比日：{metrics['comparison_date']}。所有数值由统计节点生成，"
        "叙述节点无权改写。",
        "",
        "## Top-5 缺陷与日环比",
        "",
        "| 排名 | 缺陷 | 当日数量 | 对比日数量 | 当日缺陷率 | 环比变化 |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for index, item in enumerate(top_defects, start=1):
        lines.append(
            f"| {index} | {item['defect_name']}（{item['defect_code']}） | "
            f"{item['current_qty']} | {item['previous_qty']} | "
            f"{_percentage(item['current_defect_rate'])} | "
            f"{float(item['change_percentage_points']):+.2f} 个百分点 |"
        )
    lines.extend(["", "## 分组缺陷率", ""])
    lines.extend(
        _render_group_table(
            "按产线", "line_id", grouped.get("by_line", [])
        )
    )
    lines.extend(
        _render_group_table(
            "按产品", "product_id", grouped.get("by_product", [])
        )
    )
    lines.extend(
        _render_group_table(
            "按班次", "shift", grouped.get("by_shift", [])
        )
    )

    lines.extend(
        [
            "## 异常批次与闭环任务",
            "",
            "| 异常单 | 批次 | 一次合格率 | 缺陷率 | 主缺陷 | 责任角色 | 状态 |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    exception_by_id = {
        str(item["exception_id"]): item for item in exceptions
    }
    for anomaly in anomalies:
        exception = exception_by_id[str(anomaly["exception_id"])]
        lines.append(
            f"| {anomaly['exception_id']} | {anomaly['batch_id']} | "
            f"{_percentage(anomaly['first_pass_yield'])} | "
            f"{_percentage(anomaly['defect_rate'])} | "
            f"{anomaly['dominant_defect_code']} | "
            f"{exception['owner_role']} | {exception['status']} |"
        )
    if not anomalies:
        lines.append("| - | - | - | - | - | - | 无异常 |")

    lines.extend(["", "## 标准与历史案例检索", ""])
    matches = context.get("matches", [])
    if isinstance(matches, list) and matches:
        for match in matches:
            if not isinstance(match, dict):
                continue
            standard = match.get("standard")
            historical_case = match.get("historical_case")
            standard_id = (
                standard.get("standard_id")
                if isinstance(standard, dict)
                else "未匹配"
            )
            case_id = (
                historical_case.get("case_id")
                if isinstance(historical_case, dict)
                else "未匹配"
            )
            lines.append(
                f"- {match['exception_id']}：标准 `{standard_id}`；"
                f"历史案例 `{case_id}`；检索状态 `{match['retrieval_status']}`。"
            )
    else:
        lines.append("- 无异常批次，不执行历史案例检索。")

    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 已确认事实：上表数值、阈值命中与输入版本。",
            "- 数据支持的假设：历史案例只用于确定排查顺序，不代表根因。",
            "- 尚无证据：现场状态、因果关系与措施有效性须由质量工程师复核。",
            "- 本系统不连接或写入 PLC、MES、DCS，也不下发生产参数。",
        ]
    )
    if reviewer:
        lines.extend(
            [
                "",
                "## 人工审批",
                "",
                f"- 审批人：{reviewer}",
                f"- 审批意见：{review_comment or '无'}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _render_data_owner_notification(
    report_date: date,
    validation: ValidationOutcome,
) -> str:
    lines = [
        f"# {report_date.isoformat()} 质量日报数据校验失败通知",
        "",
        "流程已显式终止，未计算指标、未生成“正常”结论、未进入发送节点。",
        "",
        f"- 必填数据缺失率：{validation.missing_rate:.2%}",
        f"- 迟到记录数：{validation.late_record_count}",
        f"- 目标日期批次数：{validation.target_batch_count}",
        "",
        "## 待数据责任人处理",
        "",
    ]
    lines.extend(f"- {issue}" for issue in validation.issues)
    lines.append("")
    return "\n".join(lines)


def _render_statistics_failure_notification(
    report_date: date,
    error: Exception,
) -> str:
    return "\n".join(
        [
            f"# {report_date.isoformat()} 质量日报统计失败通知",
            "",
            "确定性统计节点执行失败，流程已显式终止。",
            "系统未生成日报、未生成“正常”结论，也未进入审批或发布节点。",
            "",
            f"- 错误：{error}",
            "",
            "请数据责任人核对输入契约和质量标准后重新运行。",
            "",
        ]
    )


def _render_approved_notification(
    metrics: Mapping[str, object],
    exceptions: Sequence[Mapping[str, object]],
    reviewer: str,
    report_filename: str,
) -> str:
    top_defects = metrics.get("top_defects", [])
    top_defect_name = (
        top_defects[0]["defect_name"]
        if isinstance(top_defects, list) and top_defects
        else "无"
    )
    return "\n".join(
        [
            f"# {metrics['report_date']} 质量日报发布通知",
            "",
            f"- 审批人：{reviewer}",
            f"- 一次合格率：{_percentage(metrics['first_pass_yield'])}",
            f"- Top 缺陷：{top_defect_name}",
            f"- 待闭环异常单："
            f"{sum(1 for item in exceptions if item['status'] == 'open')}",
            f"- 报告文件：{report_filename}",
            f"- 指标摘要：`{metrics['metric_hash']}`",
            "",
            "该文件模拟经审批后的消息分发，不连接真实邮件或企业协作系统。",
            "",
        ]
    )


def run_quality_workflow(
    report_date: date,
    input_dir: Path,
    output_dir: Path,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE_DIR,
    workflow_file: Path = DEFAULT_WORKFLOW_FILE,
    prompt_file: Path = DEFAULT_PROMPT_FILE,
) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise WorkflowError(f"输出目录必须为空：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    definition = _load_workflow_definition(workflow_file)
    standards, cases = _load_knowledge(knowledge_dir)
    policy = definition["data_quality_policy"]
    if not isinstance(policy, dict):
        raise WorkflowError("data_quality_policy 格式损坏")

    state: dict[str, object] = {
        "workflow_id": definition["workflow_id"],
        "workflow_version": definition["workflow_version"],
        "prompt_version": definition["prompt_version"],
        "report_date": report_date.isoformat(),
        "status": "triggered",
        "current_report_version": 0,
        "approved_report_version": None,
        "exception_loop_status": "not_started",
        "state_transitions": [],
    }
    audit: dict[str, object] = {
        "workflow_id": definition["workflow_id"],
        "workflow_version": definition["workflow_version"],
        "prompt_version": definition["prompt_version"],
        "report_date": report_date.isoformat(),
        "run_id": "",
        "source_manifest": [],
        "node_trace": [],
        "events": [],
        "report_versions": [],
        "metric_hash": "",
        "review": None,
    }
    _record_event(audit, "workflow_triggered", "system", "收到日报运行请求")

    validation, bundle = validate_inputs(input_dir, report_date, policy)
    _write_json(output_dir / "data_quality.json", validation.to_dict())
    source_manifest = _build_source_manifest(
        validation.source_manifest,
        workflow_file,
        knowledge_dir,
        prompt_file,
    )
    run_id = _build_run_id(report_date, source_manifest)
    state["run_id"] = run_id
    audit["run_id"] = run_id
    audit["source_manifest"] = source_manifest

    if validation.status != "passed" or bundle is None:
        _record_node(
            audit,
            "input_validation",
            "blocked",
            "数据质量校验失败，流程停止",
        )
        _transition(state, "blocked", "输入校验失败")
        notification_path = output_dir / "data_owner_notification.md"
        notification_path.write_text(
            _render_data_owner_notification(report_date, validation),
            encoding="utf-8",
        )
        _record_node(
            audit,
            "message_distribution",
            "data_owner_only",
            notification_path.name,
        )
        _record_node(
            audit, "audit_archive", "success", "保存失败运行的审计记录"
        )
        _write_json(output_dir / "state.json", state)
        _write_json(output_dir / "audit.json", audit)
        return {
            "status": "blocked",
            "run_id": run_id,
            "output_dir": str(output_dir.resolve()),
            "issues": list(validation.issues),
        }

    _record_node(audit, "input_validation", "success", "输入校验通过")
    _transition(state, "validated", "输入字段、日期和数据量有效")

    try:
        metrics = calculate_metrics(
            bundle, report_date, standards, validation
        )
    except (KeyError, TypeError, ValueError) as error:
        _record_node(
            audit,
            "statistics",
            "error",
            f"确定性统计失败：{error}",
        )
        _transition(state, "blocked", "确定性统计失败")
        notification_path = output_dir / "statistics_failure_notification.md"
        notification_path.write_text(
            _render_statistics_failure_notification(report_date, error),
            encoding="utf-8",
        )
        _record_node(
            audit,
            "message_distribution",
            "data_owner_only",
            notification_path.name,
        )
        _record_node(
            audit, "audit_archive", "success", "保存统计失败运行审计"
        )
        _write_json(output_dir / "state.json", state)
        _write_json(output_dir / "audit.json", audit)
        return {
            "status": "blocked",
            "run_id": run_id,
            "output_dir": str(output_dir.resolve()),
            "issues": [f"确定性统计失败：{error}"],
        }
    _write_json(output_dir / "metrics.json", metrics)
    audit["metric_hash"] = metrics["metric_hash"]
    _record_node(
        audit,
        "statistics",
        "success",
        f"确定性指标摘要 {metrics['metric_hash']}",
    )
    _transition(state, "metrics_computed", "确定性统计完成")

    context, exceptions = _retrieve_context(metrics, standards, cases)
    narrative = _generate_prompt_narrative(
        context, prompt_file.read_text(encoding="utf-8")
    )
    context["narrative"] = narrative
    _write_json(output_dir / "knowledge_context.json", context)
    _write_json(output_dir / "exceptions.json", exceptions)
    _record_node(
        audit,
        "knowledge_retrieval",
        "success",
        f"匹配 {context['retrieval_count']} 条异常上下文",
    )
    _transition(state, "knowledge_retrieved", "标准与历史案例检索完成")

    report = _render_report(
        metrics,
        context,
        exceptions,
        report_status="待人工审批",
        report_version=1,
        narrative=narrative,
    )
    report_path = output_dir / "report_v1.md"
    report_path.write_text(report, encoding="utf-8")
    report_versions = audit["report_versions"]
    if not isinstance(report_versions, list):
        raise WorkflowError("report_versions 格式损坏")
    report_versions.append(
        {
            "version": 1,
            "file": report_path.name,
            "sha256": sha256_file(report_path),
            "status": "draft",
        }
    )
    state["current_report_version"] = 1
    state["exception_loop_status"] = (
        "open" if exceptions else "not_required"
    )
    _record_node(
        audit,
        "report_generation",
        "success",
        "数值锁定的日报草稿已生成",
    )
    _transition(state, "pending_approval", "等待质量工程师审批")
    _record_node(
        audit,
        "human_approval",
        "waiting",
        "消息分发节点保持阻断",
    )
    _record_node(
        audit,
        "message_distribution",
        "blocked",
        "未取得人工批准",
    )
    _record_node(audit, "audit_archive", "success", "保存草稿运行审计")
    _write_json(output_dir / "state.json", state)
    _write_json(output_dir / "audit.json", audit)
    return {
        "status": "pending_approval",
        "run_id": run_id,
        "output_dir": str(output_dir.resolve()),
        "report": str(report_path.resolve()),
        "exception_count": len(exceptions),
        "metric_hash": metrics["metric_hash"],
    }


def review_quality_workflow(
    run_dir: Path,
    decision: str,
    reviewer: str,
    comment: str = "",
    edited_summary_file: Path | None = None,
) -> dict[str, object]:
    state_payload = _read_json(run_dir / "state.json")
    audit_payload = _read_json(run_dir / "audit.json")
    metrics_payload = _read_json(run_dir / "metrics.json")
    context_payload = _read_json(run_dir / "knowledge_context.json")
    exceptions_payload = _read_json(run_dir / "exceptions.json")
    if not all(
        isinstance(item, dict)
        for item in (
            state_payload,
            audit_payload,
            metrics_payload,
            context_payload,
        )
    ) or not isinstance(exceptions_payload, list):
        raise WorkflowError("运行目录中的状态文件格式损坏")
    state = state_payload
    audit = audit_payload
    metrics = metrics_payload
    context = context_payload
    exceptions = [
        item for item in exceptions_payload if isinstance(item, dict)
    ]
    stored_metric_hash = str(metrics.get("metric_hash", ""))
    if (
        not stored_metric_hash
        or calculate_metric_hash(metrics) != stored_metric_hash
        or audit.get("metric_hash") != stored_metric_hash
    ):
        raise WorkflowError("指标文件与审计摘要不一致，禁止审批")
    if state.get("status") != "pending_approval":
        raise WorkflowError(
            f"当前状态 {state.get('status')} 不允许审批"
        )
    if decision not in {"approve", "reject"}:
        raise WorkflowError("审批决定只能是 approve 或 reject")
    if not reviewer.strip():
        raise WorkflowError("审批人不能为空")

    narrative = str(context["narrative"])
    if edited_summary_file is not None:
        narrative = _validate_narrative(
            edited_summary_file.read_text(encoding="utf-8")
        )
    else:
        narrative = _validate_narrative(narrative)

    report_status = "已批准" if decision == "approve" else "已驳回"
    report = _render_report(
        metrics,
        context,
        exceptions,
        report_status=report_status,
        report_version=2,
        narrative=narrative,
        reviewer=reviewer.strip(),
        review_comment=comment.strip(),
    )
    report_path = run_dir / "report_v2.md"
    report_path.write_text(report, encoding="utf-8")
    report_versions = audit.get("report_versions")
    if not isinstance(report_versions, list):
        raise WorkflowError("report_versions 格式损坏")
    report_versions.append(
        {
            "version": 2,
            "file": report_path.name,
            "sha256": sha256_file(report_path),
            "status": "approved" if decision == "approve" else "rejected",
        }
    )
    audit["review"] = {
        "decision": decision,
        "reviewer": reviewer.strip(),
        "comment": comment.strip(),
        "at": _now(),
    }
    state["current_report_version"] = 2

    if decision == "reject":
        _transition(state, "rejected", "质量工程师驳回日报")
        _record_node(
            audit, "human_approval", "rejected", reviewer.strip()
        )
        _record_node(
            audit,
            "message_distribution",
            "blocked",
            "审批驳回，未生成发布通知",
        )
        _record_event(
            audit, "report_rejected", reviewer.strip(), comment.strip()
        )
        _record_node(
            audit, "audit_archive", "success", "保存驳回版本与审批记录"
        )
        _write_json(run_dir / "state.json", state)
        _write_json(run_dir / "audit.json", audit)
        return {
            "status": "rejected",
            "run_id": state["run_id"],
            "report": str(report_path.resolve()),
            "notification_created": False,
        }

    _transition(state, "approved", "质量工程师批准日报")
    state["approved_report_version"] = 2
    _record_node(audit, "human_approval", "approved", reviewer.strip())
    notification_path = run_dir / "approved_notification.md"
    notification_path.write_text(
        _render_approved_notification(
            metrics, exceptions, reviewer.strip(), report_path.name
        ),
        encoding="utf-8",
    )
    _record_node(
        audit,
        "message_distribution",
        "success",
        notification_path.name,
    )
    _transition(state, "sent", "审批后生成离线发布通知")
    _record_event(
        audit, "report_approved", reviewer.strip(), comment.strip()
    )
    _record_node(
        audit, "audit_archive", "success", "保存批准版本与分发证据"
    )
    _write_json(run_dir / "state.json", state)
    _write_json(run_dir / "audit.json", audit)
    return {
        "status": "sent",
        "run_id": state["run_id"],
        "report": str(report_path.resolve()),
        "notification": str(notification_path.resolve()),
        "open_exception_count": sum(
            1 for item in exceptions if item["status"] == "open"
        ),
    }


def _render_closure_summary(
    state: Mapping[str, object],
    exceptions: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        f"# {state['report_date']} 异常闭环台账",
        "",
        f"> 闭环状态：{state['exception_loop_status']}",
        "",
        "| 异常单 | 批次 | 状态 | 责任角色 | 关闭人 | 证据引用 | 处置结论 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in exceptions:
        lines.append(
            f"| {item['exception_id']} | {item['batch_id']} | "
            f"{item['status']} | {item['owner_role']} | "
            f"{item['closed_by'] or '-'} | {item['evidence_ref'] or '-'} | "
            f"{item['resolution'] or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def close_quality_exception(
    run_dir: Path,
    exception_id: str,
    reviewer: str,
    resolution: str,
    evidence_ref: str,
) -> dict[str, object]:
    state_payload = _read_json(run_dir / "state.json")
    audit_payload = _read_json(run_dir / "audit.json")
    exceptions_payload = _read_json(run_dir / "exceptions.json")
    if (
        not isinstance(state_payload, dict)
        or not isinstance(audit_payload, dict)
        or not isinstance(exceptions_payload, list)
    ):
        raise WorkflowError("运行目录中的闭环文件格式损坏")
    state = state_payload
    audit = audit_payload
    exceptions = [
        item for item in exceptions_payload if isinstance(item, dict)
    ]
    if state.get("status") != "sent":
        raise WorkflowError("日报未批准并发送，不能关闭异常单")
    if not reviewer.strip() or not resolution.strip() or not evidence_ref.strip():
        raise WorkflowError("关闭人、处置结论和证据引用均不能为空")

    target = next(
        (
            item
            for item in exceptions
            if item.get("exception_id") == exception_id
        ),
        None,
    )
    if target is None:
        raise WorkflowError(f"未找到异常单：{exception_id}")
    if target.get("status") != "open":
        raise WorkflowError(f"异常单已关闭：{exception_id}")

    target["status"] = "closed"
    target["resolution"] = resolution.strip()
    target["evidence_ref"] = evidence_ref.strip()
    target["closed_by"] = reviewer.strip()
    target["closed_at"] = _now()
    remaining = sum(1 for item in exceptions if item["status"] == "open")
    state["exception_loop_status"] = "closed" if remaining == 0 else "in_progress"
    _record_event(
        audit,
        "exception_closed",
        reviewer.strip(),
        f"{exception_id} | {evidence_ref.strip()}",
    )
    _record_node(
        audit,
        "audit_archive",
        "success",
        f"保存异常关闭证据 {exception_id}",
    )
    _write_json(run_dir / "exceptions.json", exceptions)
    _write_json(run_dir / "state.json", state)
    _write_json(run_dir / "audit.json", audit)
    summary_path = run_dir / "exception_closure_summary.md"
    summary_path.write_text(
        _render_closure_summary(state, exceptions), encoding="utf-8"
    )
    return {
        "status": state["exception_loop_status"],
        "closed_exception": exception_id,
        "remaining_open_exceptions": remaining,
        "summary": str(summary_path.resolve()),
    }


def show_quality_workflow(run_dir: Path) -> dict[str, object]:
    state = _read_json(run_dir / "state.json")
    exceptions = (
        _read_json(run_dir / "exceptions.json")
        if (run_dir / "exceptions.json").is_file()
        else []
    )
    if not isinstance(state, dict) or not isinstance(exceptions, list):
        raise WorkflowError("运行目录中的状态文件格式损坏")
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "report_date": state.get("report_date"),
        "current_report_version": state.get("current_report_version"),
        "exception_loop_status": state.get("exception_loop_status"),
        "open_exception_count": sum(
            1
            for item in exceptions
            if isinstance(item, dict) and item.get("status") == "open"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线质量日报与异常闭环工作流"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="生成待审批质量日报")
    run_parser.add_argument("--date", required=True, help="日报日期 YYYY-MM-DD")
    run_parser.add_argument(
        "--input-dir", type=Path, default=DEFAULT_DATA_DIR
    )
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument(
        "--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR
    )
    run_parser.add_argument(
        "--workflow-file", type=Path, default=DEFAULT_WORKFLOW_FILE
    )
    run_parser.add_argument(
        "--prompt-file", type=Path, default=DEFAULT_PROMPT_FILE
    )

    review_parser = subparsers.add_parser(
        "review", help="人工批准或驳回日报"
    )
    review_parser.add_argument("--run-dir", type=Path, required=True)
    review_parser.add_argument(
        "--decision", choices=("approve", "reject"), required=True
    )
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument("--comment", default="")
    review_parser.add_argument("--edited-summary-file", type=Path)

    close_parser = subparsers.add_parser(
        "close", help="登记异常单关闭证据"
    )
    close_parser.add_argument("--run-dir", type=Path, required=True)
    close_parser.add_argument("--exception-id", required=True)
    close_parser.add_argument("--reviewer", required=True)
    close_parser.add_argument("--resolution", required=True)
    close_parser.add_argument("--evidence-ref", required=True)

    show_parser = subparsers.add_parser("show", help="查看工作流状态")
    show_parser.add_argument("--run-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_quality_workflow(
                report_date=date.fromisoformat(args.date),
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                knowledge_dir=args.knowledge_dir,
                workflow_file=args.workflow_file,
                prompt_file=args.prompt_file,
            )
            exit_code = 2 if result["status"] == "blocked" else 0
        elif args.command == "review":
            result = review_quality_workflow(
                run_dir=args.run_dir,
                decision=args.decision,
                reviewer=args.reviewer,
                comment=args.comment,
                edited_summary_file=args.edited_summary_file,
            )
            exit_code = 0
        elif args.command == "close":
            result = close_quality_exception(
                run_dir=args.run_dir,
                exception_id=args.exception_id,
                reviewer=args.reviewer,
                resolution=args.resolution,
                evidence_ref=args.evidence_ref,
            )
            exit_code = 0
        else:
            result = show_quality_workflow(args.run_dir)
            exit_code = 0
    except (
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
        WorkflowError,
    ) as error:
        print(f"工作流执行失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
