from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from typing import Protocol
from typing import Sequence


FIELD_NAMES = (
    "equipment",
    "phenomenon",
    "candidate_cause",
    "action_taken",
)
CERTAINTIES = {"fact", "speculation", "unknown"}
DEFAULT_MODEL_ID = "Qwen/Qwen1.5-0.5B-Chat"
EQUIPMENT_PATTERN = re.compile(
    r"[A-Za-z]{1,6}-\d{1,3}"
    r"(?:主轴|伺服|电机|液压缸|液压站|比例阀|高压软管)?",
    re.IGNORECASE,
)
PHENOMENON_PATTERN = re.compile(
    r"跟随(?:误差|偏差)"
    r"|温度(?:升高|偏高|高)"
    r"|发热"
    r"|振动(?:达到\d+(?:\.\d+)?\s*mm/s|升高|偏高|剧烈)"
    r"|I/O信号间歇丢失"
    r"|电流偏高"
    r"|母线过压"
    r"|低速动作爬行"
    r"|压力偏低"
    r"|响应迟滞"
    r"|偶发卡滞"
    r"|持续渗油"
    r"|粗糙度超差"
    r"|尺寸连续偏[大小]\d+(?:\.\d+)?\s*mm"
    r"|密集气孔"
    r"|温度波动"
    r"|偶发停机"
    r"|短时异响"
    r"|间歇报警"
    r"|节拍不稳",
    re.IGNORECASE,
)
FACT_CAUSE_PATTERN = re.compile(
    r"(?:检查记录为|原因记录为|检查发现|确认为)(?P<value>[^，；。]+)"
)
SPECULATIVE_CAUSE_PATTERN = re.compile(
    r"(?:可能因|疑似|推测为)(?P<value>[^，；。]+)"
)
ACTION_PATTERN = re.compile(
    r"(?:，|；)(?:已|现场|处理措施为)?"
    r"(?P<value>[^，；。]+?)"
    r"(?:后(?:恢复|试运行正常)|，设备恢复|。|$)"
)


@dataclass(frozen=True)
class GroundedField:
    value: str | None
    evidence: str | None
    certainty: str


@dataclass(frozen=True)
class MaintenanceEvent:
    equipment: GroundedField
    phenomenon: GroundedField
    candidate_cause: GroundedField
    action_taken: GroundedField
    uncertainties: tuple[str, ...]


@dataclass(frozen=True)
class AuditIssue:
    field: str
    severity: str
    reason: str


@dataclass(frozen=True)
class ExtractionAudit:
    passed: bool
    issues: tuple[AuditIssue, ...]


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> str:
        ...


@dataclass(frozen=True)
class ModelExtractionResult:
    raw_response: str
    extraction: dict[str, object]
    audit: ExtractionAudit


class QwenLocalGenerator:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        max_new_tokens: int = 512,
    ) -> None:
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens 必须大于 0")
        try:
            import torch
            from transformers import AutoModelForCausalLM
            from transformers import AutoTokenizer
        except ImportError as error:
            raise RuntimeError(
                "本地 Qwen 模式需要安装 torch 与 transformers"
            ) from error

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.max_new_tokens = max_new_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id).to(
            self.device
        )
        self.model.eval()

    def generate(self, prompt: str) -> str:
        messages = (
            {
                "role": "system",
                "content": "你是只做原文证据抽取的维修日志助手。",
            },
            {"role": "user", "content": prompt},
        )
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        model_inputs = self.tokenizer(
            [text],
            return_tensors="pt",
        ).to(self.device)
        generated_ids = self.model.generate(
            model_inputs.input_ids,
            max_new_tokens=self.max_new_tokens,
        )
        response_ids = [
            output_ids[len(input_ids):]
            for input_ids, output_ids in zip(
                model_inputs.input_ids,
                generated_ids,
            )
        ]
        return self.tokenizer.batch_decode(
            response_ids,
            skip_special_tokens=True,
        )[0]


def _matched_field(pattern: re.Pattern[str], text: str) -> GroundedField:
    match = pattern.search(text)
    if not match:
        return GroundedField(value=None, evidence=None, certainty="unknown")
    value = match.group(0)
    return GroundedField(value=value, evidence=value, certainty="fact")


def _cause_field(text: str) -> GroundedField:
    fact_match = FACT_CAUSE_PATTERN.search(text)
    if fact_match:
        value = fact_match.group("value")
        return GroundedField(value=value, evidence=value, certainty="fact")
    speculative_match = SPECULATIVE_CAUSE_PATTERN.search(text)
    if speculative_match:
        value = speculative_match.group("value")
        return GroundedField(
            value=value,
            evidence=speculative_match.group(0),
            certainty="speculation",
        )
    return GroundedField(value=None, evidence=None, certainty="unknown")


def _action_field(text: str) -> GroundedField:
    match = ACTION_PATTERN.search(text)
    if not match:
        return GroundedField(value=None, evidence=None, certainty="unknown")
    value = match.group("value").strip()
    return GroundedField(value=value, evidence=value, certainty="fact")


def extract_offline_event(raw_text: str) -> MaintenanceEvent:
    if not raw_text.strip():
        raise ValueError("维修日志不能为空")

    equipment = _matched_field(EQUIPMENT_PATTERN, raw_text)
    phenomenon = _matched_field(PHENOMENON_PATTERN, raw_text)
    candidate_cause = _cause_field(raw_text)
    action_taken = _action_field(raw_text)
    uncertainties = []
    if equipment.certainty == "unknown":
        uncertainties.append("原文未识别到设备型号")
    if phenomenon.certainty == "unknown":
        uncertainties.append("原文未识别到已知现象短语")
    if candidate_cause.certainty == "unknown":
        uncertainties.append("原文未说明故障原因")
    if action_taken.certainty == "unknown":
        uncertainties.append("原文未说明处理措施")
    return MaintenanceEvent(
        equipment=equipment,
        phenomenon=phenomenon,
        candidate_cause=candidate_cause,
        action_taken=action_taken,
        uncertainties=tuple(uncertainties),
    )


def _field_payload(
    extraction: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object] | None:
    field = extraction.get(field_name)
    return field if isinstance(field, Mapping) else None


def audit_extraction(
    raw_text: str,
    extraction: Mapping[str, object],
) -> ExtractionAudit:
    issues = []
    for field_name in FIELD_NAMES:
        field = _field_payload(extraction, field_name)
        if field is None:
            issues.append(AuditIssue(field_name, "error", "缺少结构化字段"))
            continue

        value = field.get("value")
        evidence = field.get("evidence")
        certainty = str(field.get("certainty", ""))
        if certainty not in CERTAINTIES:
            issues.append(AuditIssue(field_name, "error", "certainty 取值无效"))
            continue
        if certainty == "unknown" and value in {None, ""}:
            continue
        if certainty == "speculation" and field_name == "candidate_cause":
            if not value or not evidence:
                issues.append(
                    AuditIssue(
                        field_name,
                        "error",
                        "推测原因缺少值或原文证据",
                    )
                )
            elif str(evidence) not in raw_text:
                issues.append(
                    AuditIssue(
                        field_name,
                        "error",
                        "推测原因的 evidence 无法在原文中定位",
                    )
                )
            elif str(value) not in raw_text and str(value) not in str(evidence):
                issues.append(
                    AuditIssue(
                        field_name,
                        "error",
                        "推测值与原文 evidence 不一致",
                    )
                )
            continue
        if not value or not evidence:
            issues.append(AuditIssue(field_name, "error", "事实字段缺少值或原文证据"))
            continue
        if str(evidence) not in raw_text or str(value) not in raw_text:
            issues.append(
                AuditIssue(
                    field_name,
                    "error",
                    "字段值或 evidence 无法在原文中定位",
                )
            )

    uncertainties = extraction.get("uncertainties")
    if not isinstance(uncertainties, (list, tuple)):
        issues.append(
            AuditIssue(
                "uncertainties",
                "error",
                "uncertainties 必须是 JSON 数组",
            )
        )
    elif any(
        not isinstance(item, str) or not item.strip()
        for item in uncertainties
    ):
        issues.append(
            AuditIssue(
                "uncertainties",
                "error",
                "uncertainties 只能包含非空文本",
            )
        )

    return ExtractionAudit(
        passed=not any(issue.severity == "error" for issue in issues),
        issues=tuple(issues),
    )


def build_extraction_prompt(raw_text: str) -> str:
    schema = {
        "equipment": {"value": None, "evidence": None, "certainty": "unknown"},
        "phenomenon": {"value": None, "evidence": None, "certainty": "unknown"},
        "candidate_cause": {
            "value": None,
            "evidence": None,
            "certainty": "unknown",
        },
        "action_taken": {"value": None, "evidence": None, "certainty": "unknown"},
        "uncertainties": [],
    }
    return (
        "仅根据维修日志原文抽取 JSON，不补充常识。"
        "事实字段的 value 与 evidence 必须逐字出现在原文；"
        "原文没有原因时，将 candidate_cause 标为 unknown，"
        "模型提出的可能原因必须标为 speculation。"
        f"\nJSON 模板：{json.dumps(schema, ensure_ascii=False)}"
        f"\n维修日志：{raw_text}"
    )


def event_as_dict(event: MaintenanceEvent) -> dict[str, object]:
    return asdict(event)


def parse_model_response(response: str) -> dict[str, object]:
    object_start = response.find("{")
    if object_start < 0:
        raise ValueError("模型输出中未找到 JSON 对象")
    payload, _ = json.JSONDecoder().raw_decode(response[object_start:])
    if not isinstance(payload, Mapping):
        raise ValueError("模型输出必须是 JSON 对象")
    return dict(payload)


def extract_with_generator(
    raw_text: str,
    generator: TextGenerator,
) -> ModelExtractionResult:
    response = generator.generate(build_extraction_prompt(raw_text))
    extraction = parse_model_response(response)
    audit = audit_extraction(raw_text, extraction)
    return ModelExtractionResult(
        raw_response=response,
        extraction=extraction,
        audit=audit,
    )


def audit_model_json(raw_text: str, response: str) -> ExtractionAudit:
    return audit_extraction(raw_text, parse_model_response(response))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="维修日志证据约束结构化抽取")
    parser.add_argument("raw_text", help="待抽取的维修日志")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prompt", action="store_true", help="输出 LLM 提示词")
    modes.add_argument(
        "--model-response",
        type=Path,
        help="读取已有模型 JSON 响应并执行证据审计",
    )
    modes.add_argument(
        "--run-local-model",
        action="store_true",
        help="调用本地 Hugging Face Qwen 模型",
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    return parser


def _result_payload(result: ModelExtractionResult) -> dict[str, object]:
    return {
        "extraction": result.extraction,
        "audit": asdict(result.audit),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.prompt:
            print(build_extraction_prompt(args.raw_text))
            return 0
        if args.model_response:
            response_path: Path = args.model_response
            if response_path.stat().st_size > 1_000_000:
                raise ValueError("模型响应文件超过 1 MB 教学样例上限")
            response = response_path.read_text(encoding="utf-8")
            extraction = parse_model_response(response)
            result = ModelExtractionResult(
                raw_response=response,
                extraction=extraction,
                audit=audit_extraction(args.raw_text, extraction),
            )
            print(
                json.dumps(
                    _result_payload(result),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if result.audit.passed else 2
        if args.run_local_model:
            generator = QwenLocalGenerator(
                model_id=args.model_id,
                max_new_tokens=args.max_new_tokens,
            )
            result = extract_with_generator(args.raw_text, generator)
            print(
                json.dumps(
                    _result_payload(result),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if result.audit.passed else 2

        event = extract_offline_event(args.raw_text)
        print(json.dumps(event_as_dict(event), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
