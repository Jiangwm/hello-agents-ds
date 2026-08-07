from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import replace
from statistics import fmean
from typing import Mapping, Sequence

from schemas.models import (
    AgentMessage,
    AgentReport,
    AnalysisTask,
    Evidence,
    ExperimentPlan,
    HumanDecision,
    Hypothesis,
    MissingDataRequest,
    ReviewScore,
    WorkflowConfig,
)
from tools.industrial_tools import ReadOnlyQualityTools


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _evidence_metrics(evidence: Evidence) -> Mapping[str, object]:
    data = getattr(evidence, "data", None)
    return data if isinstance(data, MappingABC) else evidence.metrics


def _is_success(evidence: Evidence | None) -> bool:
    return evidence is not None and evidence.status == "success"


def _falsifiability_score(test: str) -> float:
    if len(test.strip()) < 16:
        return 0.0
    has_observable = any(
        marker in test
        for marker in ("尺寸", "温度", "振动", "进给", "压力", "量具")
    )
    has_comparison = any(
        marker in test
        for marker in ("若", "比较", "对照", "固定", "仅改变", "盲态", "重复")
    )
    if has_observable and has_comparison:
        return 1.0
    if has_observable or has_comparison:
        return 0.5
    return 0.2


_PLAN_SUCCESS_EVIDENCE: dict[str, tuple[str, ...]] = {}


class CoordinatorAgent:
    name = "coordinator"

    def dispatch(self, task: AnalysisTask) -> tuple[AgentMessage, ...]:
        assignments = (
            (
                "data_analyst",
                "构造正常组与异常组，量化尺寸与一次合格率差异。",
                ("质量差异是否跨批次稳定？",),
            ),
            (
                "process_engineer",
                "筛查工艺时序漂移，并核对适用工艺规范。",
                ("哪一个参数与尺寸漂移时间一致？",),
            ),
            (
                "equipment_engineer",
                "核对设备告警、维护记录和部件状态。",
                ("异常窗口是否存在设备侧共因？",),
            ),
        )
        return tuple(
            AgentMessage(
                task_id=task.task_id,
                sender=self.name,
                recipient=recipient,
                evidence_ids=(),
                claim=claim,
                confidence=1.0,
                open_questions=open_questions,
                message_type="assignment",
            )
            for recipient, claim, open_questions in assignments
        )

    def merge(
        self,
        task: AnalysisTask,
        reports: Sequence[AgentReport],
    ) -> tuple[tuple[Hypothesis, ...], AgentMessage]:
        evidence = tuple(item for report in reports for item in report.evidence)
        evidence_by_id = {item.evidence_id: item for item in evidence}
        self._evidence_by_id = evidence_by_id
        quality_evidence_ids = tuple(
            item.evidence_id
            for item in evidence
            if item.tool_name == "analyze_quality_shift"
        )
        evidence_versions: dict[str, set[tuple[object, ...]]] = {}
        for item in evidence:
            evidence_versions.setdefault(item.evidence_id, set()).add(
                (
                    item.status,
                    item.claim,
                    repr(_evidence_metrics(item)),
                    getattr(item, "error", None),
                )
            )
        conflicting_evidence_ids = {
            evidence_id
            for evidence_id, versions in evidence_versions.items()
            if len(versions) > 1
        }
        grouped: dict[str, list[Hypothesis]] = {}
        for report in reports:
            for hypothesis in report.hypotheses:
                grouped.setdefault(hypothesis.hypothesis_id, []).append(hypothesis)

        merged: list[Hypothesis] = []
        conflict_questions: list[str] = []
        for hypothesis_id, items in grouped.items():
            claims = _unique(tuple(item.claim for item in items))
            own_evidence_ids = _unique(
                tuple(
                    evidence_id
                    for item in items
                    for evidence_id in item.evidence_ids
                )
            )
            evidence_ids = _unique(
                own_evidence_ids
                + (
                    quality_evidence_ids
                    if hypothesis_id in {"H-PROCESS-SHIFT", "H-MEASUREMENT"}
                    else ()
                )
            )
            counter_evidence_ids = _unique(
                tuple(
                    evidence_id
                    for item in items
                    for evidence_id in item.counter_evidence_ids
                )
            )
            overlap = set(evidence_ids) & set(counter_evidence_ids)
            status_conflict = len({item.status for item in items}) > 1
            if overlap:
                conflicting_evidence_ids.update(overlap)
                conflict_questions.append(
                    f"{hypothesis_id} 将 {', '.join(sorted(overlap))} 同时作为支持和反证，"
                    "需人工判定证据方向。"
                )
            if status_conflict:
                conflict_questions.append(
                    f"{hypothesis_id} 同时出现候选与排除状态，需补充可复现对照证据。"
                )
            merged.append(
                Hypothesis(
                    hypothesis_id=hypothesis_id,
                    title=items[0].title,
                    claim="；".join(claims),
                    owners=_unique(
                        tuple(
                            owner
                            for item in items
                            for owner in item.owners
                        )
                    ),
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=counter_evidence_ids,
                    confidence=round(
                        fmean(item.confidence for item in items)
                        * (0.6 if overlap or status_conflict else 1.0),
                        4,
                    ),
                    open_questions=_unique(
                        tuple(
                            question
                            for item in items
                            for question in item.open_questions
                        )
                    ),
                    falsification_test=items[0].falsification_test,
                    status=(
                        "candidate"
                        if any(item.status == "candidate" for item in items)
                        and not (
                            counter_evidence_ids
                            and all(
                                _is_success(evidence_by_id.get(evidence_id))
                                for evidence_id in counter_evidence_ids
                            )
                        )
                        else "excluded"
                    ),
                )
            )
        if conflicting_evidence_ids:
            conflict_questions.insert(
                0,
                "以下同 ID 证据存在版本或方向冲突："
                + ", ".join(sorted(conflicting_evidence_ids))
                + "。",
            )
        merged.sort(
            key=lambda item: (
                item.status != "candidate",
                -item.confidence,
                item.hypothesis_id,
            )
        )
        evidence_ids = _unique(
            tuple(
                evidence_id
                for item in merged
                for evidence_id in item.evidence_ids
            )
        )
        message = AgentMessage(
            task_id=task.task_id,
            sender=self.name,
            recipient="quality_reviewer",
            evidence_ids=(
                tuple(sorted(conflicting_evidence_ids))
                if conflicting_evidence_ids
                else evidence_ids
            ),
            claim=(
                "conflict_evidence_request："
                + "；".join(conflict_questions)
                if conflict_questions
                else (
                    f"已合并 {sum(len(items) for items in grouped.values())} 条原始假设为 "
                    f"{len(merged)} 条候选/排除项，等待证据审核。"
                )
            ),
            confidence=max((item.confidence for item in merged), default=0.0),
            open_questions=_unique(
                tuple(conflict_questions)
                + tuple(
                    question
                    for item in merged
                    for question in item.open_questions
                )
            ),
            message_type=(
                "conflict_evidence_request"
                if conflict_questions
                else "merged_hypotheses"
            ),
        )
        return tuple(merged), message

    def build_experiment_plan(
        self,
        top_hypothesis: Hypothesis,
    ) -> ExperimentPlan:
        successful_evidence_ids = tuple(
            evidence_id
            for evidence_id in top_hypothesis.evidence_ids
            if _is_success(
                getattr(self, "_evidence_by_id", {}).get(evidence_id)
            )
        )
        plan_templates = {
            "H-THERMAL": (
                "在隔离试验环境中验证主轴温度窗口与尺寸均值变化是否可重复对应。",
                (
                    "由工艺、质量和设备负责人确认试验设备、样件与停机条件。",
                    "固定物料、程序版本、进给速度和夹紧压力，仅设置基线与偏高两个温度窗口。",
                    "每个窗口加工不少于 30 件脱敏试验件，并由盲态量具重复测量尺寸。",
                    "比较两组尺寸均值、离散度和一次合格率，同时记录温度与振动。",
                ),
                (
                    "偏高温度窗口尺寸均值相对基线上移，且重复试验方向一致。",
                    "润滑与温度恢复后尺寸均值回到教学规范范围。",
                ),
            ),
            "H-FEED": (
                "在隔离试验环境中验证进给速度窗口是否会引起可重复的尺寸变化。",
                (
                    "确认离线设备、样件和程序版本，并由人工锁定其他工艺条件。",
                    "仅设置基线与受控进给速度窗口，保持温度、夹紧压力和物料一致。",
                    "每个进给窗口加工不少于 30 件脱敏试验件并盲态测量尺寸。",
                    "比较尺寸均值、离散度与一次合格率，并记录实际进给速度。",
                ),
                (
                    "两档进给速度的尺寸变化在重复试验中方向一致。",
                    "恢复基线进给速度后尺寸分布回归基线范围。",
                ),
            ),
            "H-FIXTURE": (
                "在隔离试验环境中验证夹紧压力和重复定位是否解释尺寸偏移。",
                (
                    "由设备和质量负责人确认离线夹具、压力边界与试验件。",
                    "固定物料、程序、温度和进给，仅设置受控夹紧压力窗口。",
                    "执行夹具重复定位精度检查并盲态测量每组试验件尺寸。",
                    "比较压力、定位偏差与尺寸分布的重复关系。",
                ),
                (
                    "夹紧压力或定位偏差变化与尺寸变化在重复试验中一致。",
                    "恢复基线压力后定位精度和尺寸分布回归基线范围。",
                ),
            ),
            "H-MEASUREMENT": (
                "验证量具校准状态与重复性是否足以解释当前尺寸差异。",
                (
                    "由质量负责人确认已校准的独立量具和盲态复测样件。",
                    "对同一样件执行交叉量具与重复测量，不改变任何生产参数。",
                    "计算量具重复性、再现性与测量偏差。",
                    "比较复测结果与原始质量结果的差异。",
                ),
                (
                    "量具偏差或重复性足以解释原始尺寸差异，或被独立复测否定。",
                    "复测过程满足预先定义的量具 R&R 接受准则。",
                ),
            ),
            "H-PROCESS-SHIFT": (
                "在隔离试验环境中验证工艺参数窗口与尺寸分布漂移是否存在可重复关系。",
                (
                    "由工艺和质量负责人确认离线设备、样件、对照批次与参数边界。",
                    "固定物料、程序、设备状态和测量方法，仅设置受控工艺参数窗口。",
                    "每个参数窗口加工不少于 30 件脱敏试验件并盲态测量尺寸。",
                    "比较参数、尺寸均值、离散度和一次合格率的重复变化。",
                ),
                (
                    "参数窗口变化与尺寸分布变化在重复试验中方向一致。",
                    "恢复基线参数后尺寸分布回归基线范围。",
                ),
            ),
        }
        objective, steps, success_criteria = plan_templates.get(
            top_hypothesis.hypothesis_id,
            (
                "在隔离试验环境中验证该候选假设是否能被可重复的对照数据支持或反驳。",
                (
                    "由质量、工艺和设备负责人确认离线试验条件与对照组。",
                    "固定无关变量，仅改变与候选假设直接相关的一个因素。",
                    "采集可追溯的重复测量并比较对照组与试验组。",
                ),
                (
                    "预定义指标在重复试验中呈现一致差异，或该假设被明确反驳。",
                ),
            ),
        )
        plan_id = f"PLAN-{top_hypothesis.hypothesis_id}"
        _PLAN_SUCCESS_EVIDENCE[plan_id] = successful_evidence_ids
        return ExperimentPlan(
            plan_id=plan_id,
            hypothesis_id=top_hypothesis.hypothesis_id,
            evidence_ids=successful_evidence_ids,
            objective=objective,
            steps=steps,
            success_criteria=success_criteria,
            safety_constraints=(
                "仅在离线或经批准的试验设备执行。",
                "不由智能体写入 PLC、MES、DCS、配方或工艺参数。",
                "任何越过设备、质量或 EHS 边界的步骤立即人工中止。",
            ),
        )


class DataAnalysisAgent:
    name = "data_analyst"

    def __init__(self, tools: ReadOnlyQualityTools) -> None:
        self.tools = tools

    def investigate(self, task: AnalysisTask) -> AgentReport:
        execution = self.tools.execute(
            "analyze_quality_shift",
            source_agent=self.name,
            normal_batch_ids=task.normal_batch_ids,
            abnormal_batch_ids=task.abnormal_batch_ids,
        )
        evidence = execution.evidence
        confidence = 0.68 if evidence.status == "success" else 0.35
        hypotheses = (
            Hypothesis(
                hypothesis_id="H-PROCESS-SHIFT",
                title="制程分布发生系统性漂移",
                claim=(
                    "尺寸均值与一次合格率在异常组同步变化，优先检查同窗口工艺参数。"
                ),
                owners=(self.name,),
                evidence_ids=(evidence.evidence_id,),
                counter_evidence_ids=(),
                confidence=confidence,
                open_questions=("需要工艺时序确定具体漂移参数。",),
                falsification_test=(
                    "在同产品同产线的匹配批次中，若工艺参数无同步变化则降低该假设优先级。"
                ),
            ),
            Hypothesis(
                hypothesis_id="H-MEASUREMENT",
                title="测量系统偏移",
                claim="当前质量数据也可能由量具偏移或重复性不足解释。",
                owners=(self.name,),
                evidence_ids=(evidence.evidence_id,),
                counter_evidence_ids=(),
                confidence=0.30,
                open_questions=("缺少量具 R&R、校准和盲态复测数据。",),
                falsification_test=(
                    "使用已校准的独立量具盲态复测同一样件；若差异仍存在则反驳该假设。"
                ),
            ),
        )
        return AgentReport(
            message=AgentMessage(
                task_id=task.task_id,
                sender=self.name,
                recipient="coordinator",
                evidence_ids=(evidence.evidence_id,),
                claim=evidence.claim,
                confidence=confidence,
                open_questions=("质量变化是结果证据，仍需工艺与设备证据解释。",),
            ),
            evidence=(evidence,),
            hypotheses=hypotheses,
            new_evidence_count=int(execution.is_new),
        )


class ProcessAgent:
    name = "process_engineer"

    def __init__(self, tools: ReadOnlyQualityTools) -> None:
        self.tools = tools

    def investigate(self, task: AnalysisTask) -> AgentReport:
        shift_execution = self.tools.execute(
            "analyze_process_shift",
            source_agent=self.name,
            normal_batch_ids=task.normal_batch_ids,
            abnormal_batch_ids=task.abnormal_batch_ids,
        )
        shift_evidence = shift_execution.evidence
        shift_metrics = _evidence_metrics(shift_evidence)
        product_id = shift_metrics.get("product_id")
        top_parameter = shift_metrics.get("top_parameter")
        has_top_parameter = (
            isinstance(top_parameter, str)
            and bool(top_parameter.strip())
        )
        spec_execution = None
        spec_evidence = None
        if shift_evidence.status == "success" and has_top_parameter:
            spec_execution = self.tools.execute(
                "lookup_process_spec",
                source_agent=self.name,
                product_id=str(product_id or ""),
                parameter=top_parameter,
            )
            candidate_spec_evidence = spec_execution.evidence
            if isinstance(candidate_spec_evidence, Evidence):
                spec_evidence = candidate_spec_evidence
        complete = (
            shift_evidence.status == "success"
            and _is_success(spec_evidence)
            and top_parameter == "spindle_temperature_c"
        )
        shifts = shift_metrics.get("shifts", ())
        feed_shift = next(
            (
                item
                for item in shifts
                if isinstance(item, MappingABC)
                and item.get("parameter") == "feed_rate_mm_s"
            ),
            {},
        )
        feed_stability_metric = (
            feed_shift.get("normalized_shift")
            if isinstance(feed_shift, MappingABC)
            else None
        )
        feed_stable = (
            shift_evidence.status == "success"
            and isinstance(feed_stability_metric, (int, float))
            and float(feed_stability_metric) < 0.1
        )
        thermal_evidence_ids = _unique(
            (shift_evidence.evidence_id,)
            + ((spec_evidence.evidence_id,) if spec_evidence else ())
        )
        hypotheses = (
            Hypothesis(
                hypothesis_id="H-THERMAL",
                title="主轴温升引发热尺寸漂移",
                claim=(
                    f"{top_parameter} 是异常窗口内最显著漂移参数，"
                    "其方向与尺寸均值上移一致。"
                    if complete
                    else "当前工艺数据或适用规范不足，主轴温升仍是待验证未知项。"
                ),
                owners=(self.name,),
                evidence_ids=thermal_evidence_ids,
                counter_evidence_ids=(),
                confidence=0.84 if complete else 0.15,
                open_questions=(
                    "需要有效工艺时序、适用规范和单变量温度窗口试验区分温升与其他共变因素。",
                ),
                falsification_test=(
                    "固定物料、程序、进给和夹紧条件，仅改变主轴温度窗口并盲态测量尺寸。"
                ),
            ),
            Hypothesis(
                hypothesis_id="H-FEED",
                title="进给速度漂移",
                claim=(
                    "成功的工艺时序显示进给速度稳定，可作为排除该假设的反证。"
                    if feed_stable
                    else "进给速度影响仍未判定，需补充有效工艺时序。"
                ),
                owners=(self.name,),
                evidence_ids=(shift_evidence.evidence_id,),
                counter_evidence_ids=(
                    (shift_evidence.evidence_id,) if feed_stable else ()
                ),
                confidence=0.10 if feed_stable else 0.15,
                open_questions=(
                    ()
                    if feed_stable
                    else ("缺少可用于判断进给稳定性的成功工艺证据。",)
                ),
                falsification_test=(
                    "比较匹配批次进给速度分布；若均值和范围稳定则排除该假设。"
                ),
                status="excluded" if feed_stable else "candidate",
            ),
        )
        return AgentReport(
            message=AgentMessage(
                task_id=task.task_id,
                sender=self.name,
                recipient="coordinator",
                evidence_ids=(
                    shift_evidence.evidence_id,
                    *((spec_evidence.evidence_id,) if spec_evidence else ()),
                ),
                claim=(
                    f"{shift_evidence.claim} {spec_evidence.claim}"
                    if spec_evidence
                    else "工艺时序不足，未形成参数或规范结论。"
                ),
                confidence=0.84 if complete else 0.42,
                open_questions=(
                    "参数共变与因果关系仍需受控试验验证。",
                ),
            ),
            evidence=(shift_evidence,) + (
                (spec_evidence,) if spec_evidence else ()
            ),
            hypotheses=hypotheses,
            new_evidence_count=(
                int(shift_execution.is_new)
                + int(spec_execution.is_new if spec_execution else False)
            ),
        )


class EquipmentAgent:
    name = "equipment_engineer"

    def __init__(self, tools: ReadOnlyQualityTools) -> None:
        self.tools = tools

    def investigate(self, task: AnalysisTask) -> AgentReport:
        execution = self.tools.execute(
            "inspect_equipment_context",
            source_agent=self.name,
            abnormal_batch_ids=task.abnormal_batch_ids,
        )
        evidence = execution.evidence
        metrics = _evidence_metrics(evidence)
        alarm_codes = tuple(metrics.get("alarm_codes", ()))
        thermal_alarm = any(
            marker in str(code).upper()
            for code in alarm_codes
            for marker in ("TEMP", "THERM", "VIB", "LUBE", "SPINDLE")
        )
        equipment_signal = evidence.status == "success" and thermal_alarm
        fixture_stable = (
            evidence.status == "success"
            and metrics.get("fixture_stable") is True
        )
        hypotheses = (
            Hypothesis(
                hypothesis_id="H-THERMAL",
                title="主轴温升引发热尺寸漂移",
                claim=(
                    "主轴温度/振动告警与润滑维护上下文支持设备热状态作为优先排查方向。"
                    if equipment_signal
                    else "设备告警或维护上下文不足，设备热状态仍是待验证未知项。"
                ),
                owners=(self.name,),
                evidence_ids=(evidence.evidence_id,),
                counter_evidence_ids=(),
                confidence=0.76 if equipment_signal else 0.15,
                open_questions=(
                    "缺少异常前后的轴承温度、振动原始趋势和复验质量数据。",
                ),
                falsification_test=(
                    "完成润滑和状态恢复后，在相同工艺条件下复验尺寸与振动趋势。"
                ),
            ),
            Hypothesis(
                hypothesis_id="H-FIXTURE",
                title="夹紧机构异常",
                claim=(
                    "成功的夹具稳定性指标支持排除夹紧机构异常。"
                    if fixture_stable
                    else "夹紧机构影响仍未判定，缺少成功的压力或重复定位稳定性指标。"
                ),
                owners=(self.name,),
                evidence_ids=(evidence.evidence_id,),
                counter_evidence_ids=(
                    (evidence.evidence_id,) if fixture_stable else ()
                ),
                confidence=0.12 if fixture_stable else 0.15,
                open_questions=(
                    ()
                    if fixture_stable
                    else ("缺少夹具压力趋势或重复定位稳定性记录。",)
                ),
                falsification_test=(
                    "核对夹紧压力趋势并进行夹具重复定位精度检查。"
                ),
                status="excluded" if fixture_stable else "candidate",
            ),
        )
        return AgentReport(
            message=AgentMessage(
                task_id=task.task_id,
                sender=self.name,
                recipient="coordinator",
                evidence_ids=(evidence.evidence_id,),
                claim=evidence.claim,
                confidence=0.76 if equipment_signal else 0.15,
                open_questions=(
                    "维护后恢复只提供时间顺序证据，不能单独确认因果。",
                ),
            ),
            evidence=(evidence,),
            hypotheses=hypotheses,
            new_evidence_count=int(execution.is_new),
        )


class QualityReviewAgent:
    name = "quality_reviewer"

    def __init__(self, review_threshold: float | None = None) -> None:
        if review_threshold is None:
            review_threshold = WorkflowConfig().review_threshold
        if not 0.0 <= review_threshold <= 1.0:
            raise ValueError("review_threshold 必须在 0 到 1 之间")
        self.review_threshold = review_threshold

    def review(
        self,
        task: AnalysisTask,
        hypotheses: Sequence[Hypothesis],
        evidence: Sequence[Evidence],
    ) -> tuple[
        tuple[ReviewScore, ...],
        tuple[MissingDataRequest, ...],
        AgentMessage,
    ]:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        scores: list[ReviewScore] = []
        for hypothesis in hypotheses:
            support = [
                evidence_by_id[evidence_id]
                for evidence_id in hypothesis.evidence_ids
                if evidence_id in evidence_by_id
            ]
            counter = [
                evidence_by_id[evidence_id]
                for evidence_id in hypothesis.counter_evidence_ids
                if evidence_id in evidence_by_id
            ]
            valid_counter = [
                item for item in counter if item.status == "success"
            ]
            status_weights = {
                "success": 1.0,
                "insufficient": 0.25,
                "blocked": 0.0,
                "error": 0.0,
            }
            completeness = min(
                1.0,
                sum(status_weights[item.status] for item in support) / 2.0,
            )
            completeness *= max(0.0, 1.0 - 0.45 * len(valid_counter))
            temporal_values = [
                bool(_evidence_metrics(item).get("temporal_alignment"))
                for item in support
                if item.status == "success"
                and "temporal_alignment" in _evidence_metrics(item)
            ]
            temporal = (
                sum(temporal_values) / len(temporal_values)
                if temporal_values
                else 0.0
            )
            falsifiability = _falsifiability_score(
                hypothesis.falsification_test
            )
            total = (
                0.50 * completeness
                + 0.25 * temporal
                + 0.25 * falsifiability
                - 0.20 * len(valid_counter)
            )
            cited_evidence = _unique(
                tuple(item.evidence_id for item in support)
                + tuple(item.evidence_id for item in counter)
            )
            scores.append(
                ReviewScore(
                    hypothesis_id=hypothesis.hypothesis_id,
                    evidence_completeness=round(completeness, 4),
                    temporal_consistency=round(temporal, 4),
                    falsifiability=round(falsifiability, 4),
                    total_score=round(max(0.0, min(1.0, total)), 4),
                    evidence_ids=cited_evidence,
                    rationale=(
                        f"成功/不足支持证据={sum(item.status == 'success' for item in support)}/"
                        f"{sum(item.status == 'insufficient' for item in support)}；"
                        f"有效反证={len(valid_counter)}，不足/失败/阻塞反证不用于排除；"
                        "评分只反映证据完整性、时间一致性和可反证性，不采纳智能体置信度投票。"
                    ),
                )
            )
        score_by_id = {item.hypothesis_id: item for item in scores}
        ordered_scores = tuple(
            sorted(
                scores,
                key=lambda item: (
                    next(
                        hypothesis.status == "excluded"
                        for hypothesis in hypotheses
                        if hypothesis.hypothesis_id == item.hypothesis_id
                    ),
                    -item.total_score,
                    item.hypothesis_id,
                ),
            )
        )
        candidate_scores = [
            score_by_id[item.hypothesis_id]
            for item in hypotheses
            if item.status == "candidate"
        ]
        top_score = max(
            (item.total_score for item in candidate_scores),
            default=0.0,
        )
        missing: list[MissingDataRequest] = [
            MissingDataRequest(
                request_id="REQ-MSA-001",
                description="获取量具校准、量具 R&R 与同一样件盲态复测结果。",
                reason="区分制程漂移与测量系统偏移。",
                related_hypothesis_ids=("H-MEASUREMENT",),
                required_approval="质量负责人",
            ),
            MissingDataRequest(
                request_id="REQ-POST-MAINT-001",
                description="补充维护前后主轴温度、振动与尺寸复验数据。",
                reason="验证设备状态恢复与质量恢复是否时间一致。",
                related_hypothesis_ids=("H-THERMAL",),
                required_approval="设备与质量负责人",
            ),
        ]
        insufficient_tools = {
            item.tool_name
            for item in evidence
            if item.status in {"insufficient", "error", "blocked"}
        }
        if top_score < self.review_threshold or insufficient_tools:
            missing.append(
                MissingDataRequest(
                    request_id="REQ-SAMPLE-001",
                    description="每个对照组补充不少于 6 条工艺采样和 2 个可比批次。",
                    reason="当前证据不足以越过报告阈值。",
                    related_hypothesis_ids=tuple(
                        item.hypothesis_id
                        for item in hypotheses
                        if item.status == "candidate"
                    ),
                    required_approval="数据域负责人",
                )
            )
        cited_ids = _unique(
            tuple(
                evidence_id
                for score in ordered_scores
                for evidence_id in score.evidence_ids
            )
        )
        message = AgentMessage(
            task_id=task.task_id,
            sender=self.name,
            recipient="coordinator",
            evidence_ids=cited_ids,
            claim=(
                f"已完成 {len(ordered_scores)} 条假设审核，"
                f"候选最高证据评分为 {top_score:.3f}。"
            ),
            confidence=top_score,
            open_questions=tuple(item.description for item in missing),
            message_type="quality_review",
        )
        return ordered_scores, tuple(missing), message


class HumanSupervisorAgent:
    name = "human_supervisor"

    def decide(
        self,
        task: AnalysisTask,
        plan: ExperimentPlan,
        requested_decision: str,
    ) -> tuple[ExperimentPlan, HumanDecision, AgentMessage]:
        if requested_decision not in {"approve", "defer", "reject"}:
            raise ValueError("human_decision 必须为 approve、defer 或 reject")
        validated_evidence_ids = _PLAN_SUCCESS_EVIDENCE.get(plan.plan_id, ())
        has_valid_evidence = (
            bool(plan.evidence_ids)
            and plan.evidence_ids == validated_evidence_ids
        )
        safety_text = " ".join(plan.safety_constraints)
        has_safety_constraints = (
            ("离线" in safety_text or "批准的试验设备" in safety_text)
            and ("不由智能体写入" in safety_text or "不写入" in safety_text)
            and "人工" in safety_text
        )
        if requested_decision == "approve" and has_valid_evidence and has_safety_constraints:
            approved_plan = replace(plan, approved=True)
            decision = HumanDecision(
                decision="approve",
                approver_role="人工质量负责人",
                scope="仅批准离线/试验设备验证计划",
                note="候选根因仍待试验验证，不批准任何生产参数写入或最终因果结论。",
            )
        elif requested_decision == "approve":
            approved_plan = plan
            decision = HumanDecision(
                decision="defer",
                approver_role="人工质量负责人",
                scope="退回补充验证计划",
                note=(
                    "未批准："
                    + (
                        "缺少经协调器校验的成功证据。"
                        if not has_valid_evidence
                        else "安全约束未覆盖离线、禁止写入和人工中止边界。"
                    )
                ),
            )
        elif requested_decision == "reject":
            approved_plan = plan
            decision = HumanDecision(
                decision="reject",
                approver_role="人工质量负责人",
                scope="拒绝当前验证计划",
                note="流程人工中断；需修改风险控制或试验设计后重新提交。",
            )
        else:
            approved_plan = plan
            decision = HumanDecision(
                decision="defer",
                approver_role="人工质量负责人",
                scope="暂缓验证计划",
                note="等待补充资源、数据与跨专业会签。",
            )
        message = AgentMessage(
            task_id=task.task_id,
            sender=self.name,
            recipient="coordinator",
            evidence_ids=plan.evidence_ids,
            claim=f"人工决策={decision.decision}；{decision.note}",
            confidence=1.0,
            open_questions=(
                ()
                if decision.decision == "approve"
                else ("何时补充材料并重新提交人工审批？",)
            ),
            message_type="human_decision",
        )
        return approved_plan, decision, message
