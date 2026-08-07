from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from typing import Mapping
from typing import Sequence

from tokenizer_inspection import industrial_tokenize


CHAPTER_DIR = Path(__file__).resolve().parent
LOG_DATA_PATH = CHAPTER_DIR / "data" / "maintenance_logs.jsonl"
FAULT_CATEGORIES = {"机械", "电气", "液压", "工艺", "未知"}
REQUIRED_FIELDS = {
    "log_id",
    "equipment_type",
    "event_time",
    "raw_text",
    "fault_category",
    "action_taken",
    "downtime_minutes",
}
SEMANTIC_REPLACEMENTS = (
    ("跟随偏差", "跟随误差"),
    ("位置偏差", "跟随误差"),
    ("重新校准", "重新标定"),
    ("校准", "标定"),
    ("过温", "温度高"),
    ("发热", "温度高"),
    ("温升", "温度高"),
    ("抖动", "振动"),
    ("漏油", "渗油"),
    ("泄漏", "渗油"),
    ("卡涩", "卡滞"),
    ("响应慢", "响应迟滞"),
    ("忽高忽低", "波动"),
    ("不稳定", "波动"),
    ("丢信号", "信号丢失"),
    ("松脱", "松动"),
)
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class MaintenanceLog:
    log_id: str
    equipment_type: str
    event_time: datetime
    raw_text: str
    fault_category: str
    action_taken: str
    downtime_minutes: int
    keywords: tuple[str, ...]
    fault_code: str | None
    source_event_id: str


@dataclass(frozen=True)
class SearchResult:
    score: float
    log_id: str
    equipment_type: str
    raw_text: str
    fault_category: str
    action_taken: str
    event_time: str


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    confidence: float
    votes: tuple[tuple[str, float], ...]
    evidence_log_ids: tuple[str, ...]


def _maintenance_log(item: Mapping[str, object], line_number: int) -> MaintenanceLog:
    missing = REQUIRED_FIELDS - item.keys()
    if missing:
        names = "、".join(sorted(missing))
        raise ValueError(f"第 {line_number} 行缺少字段：{names}")

    log_id = str(item["log_id"]).strip()
    equipment_type = str(item["equipment_type"]).strip()
    raw_text = str(item["raw_text"]).strip()
    action_taken = str(item["action_taken"]).strip()
    fault_category = str(item["fault_category"]).strip()
    if not all((log_id, equipment_type, raw_text, action_taken)):
        raise ValueError(f"第 {line_number} 行存在空白必填文本")
    if fault_category not in FAULT_CATEGORIES:
        raise ValueError(f"第 {line_number} 行故障类别无效：{fault_category}")

    try:
        event_time = datetime.fromisoformat(str(item["event_time"]))
        downtime_minutes = int(item["downtime_minutes"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"第 {line_number} 行时间或停机分钟数无效") from error
    if downtime_minutes < 0:
        raise ValueError(f"第 {line_number} 行停机分钟数不能为负数")

    raw_keywords = item.get("keywords", ())
    if not isinstance(raw_keywords, list):
        raise ValueError(f"第 {line_number} 行 keywords 必须是数组")
    fault_code = str(item["fault_code"]).strip() if item.get("fault_code") else None
    source_event_id = str(item.get("source_event_id", log_id)).strip()
    return MaintenanceLog(
        log_id=log_id,
        equipment_type=equipment_type,
        event_time=event_time,
        raw_text=raw_text,
        fault_category=fault_category,
        action_taken=action_taken,
        downtime_minutes=downtime_minutes,
        keywords=tuple(
            str(value).strip()
            for value in raw_keywords
            if str(value).strip()
        ),
        fault_code=fault_code,
        source_event_id=source_event_id,
    )


def load_logs(path: Path = LOG_DATA_PATH) -> tuple[MaintenanceLog, ...]:
    if path.stat().st_size > 2_000_000:
        raise ValueError(f"{path.name} 超过 2 MB 教学样例上限")

    logs = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if line.strip():
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象")
            logs.append(_maintenance_log(payload, line_number))

    log_ids = [log.log_id for log in logs]
    if len(log_ids) != len(set(log_ids)):
        raise ValueError("log_id 必须唯一")
    if not 100 <= len(logs) <= 300:
        raise ValueError("教学数据应包含 100～300 条维修日志")
    return tuple(logs)


def normalize_semantic_text(text: str) -> str:
    normalized = text.lower().strip()
    for source, target in SEMANTIC_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    return normalized


def _character_ngrams(text: str, sizes: Sequence[int]) -> Iterable[str]:
    for sequence in CHINESE_PATTERN.findall(text):
        for size in sizes:
            for index in range(len(sequence) - size + 1):
                yield f"c{size}:{sequence[index:index + size]}"


def text_features(text: str) -> Counter[str]:
    normalized = normalize_semantic_text(text)
    features = Counter(_character_ngrams(normalized, (2, 3)))
    for token in industrial_tokenize(normalized):
        if not CHINESE_PATTERN.fullmatch(token) or len(token) <= 4:
            features[f"t:{token.lower()}"] += 2
    return features


class TfidfEncoder:
    def __init__(self) -> None:
        self._idf: dict[str, float] = {}

    def fit(self, texts: Sequence[str]) -> TfidfEncoder:
        if not texts:
            raise ValueError("至少需要一条训练日志")
        document_frequency = Counter(
            feature
            for text in texts
            for feature in text_features(text)
        )
        total = len(texts)
        self._idf = {
            feature: math.log((total + 1) / (frequency + 1)) + 1
            for feature, frequency in document_frequency.items()
        }
        return self

    def transform(self, text: str) -> dict[str, float]:
        weighted = {
            feature: (1 + math.log(frequency)) * self._idf[feature]
            for feature, frequency in text_features(text).items()
            if feature in self._idf
        }
        norm = math.sqrt(sum(value * value for value in weighted.values()))
        if norm == 0:
            return {}
        return {feature: value / norm for feature, value in weighted.items()}


def cosine_similarity(
    first: Mapping[str, float],
    second: Mapping[str, float],
) -> float:
    if len(first) > len(second):
        first, second = second, first
    return sum(value * second.get(feature, 0.0) for feature, value in first.items())


def split_logs(
    logs: Sequence[MaintenanceLog],
    strategy: str = "time",
    validation_ratio: float = 0.2,
) -> tuple[tuple[MaintenanceLog, ...], tuple[MaintenanceLog, ...]]:
    if strategy not in {"time", "equipment"}:
        raise ValueError("strategy 仅支持 time 或 equipment")
    if not 0 < validation_ratio < 0.5:
        raise ValueError("validation_ratio 必须大于 0 且小于 0.5")

    group_key = (
        (lambda log: log.source_event_id)
        if strategy == "time"
        else (lambda log: log.equipment_type)
    )
    groups: dict[str, list[MaintenanceLog]] = defaultdict(list)
    for log in logs:
        groups[group_key(log)].append(log)
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            max(log.event_time for log in group),
            group[0].source_event_id,
        ),
    )
    validation_group_count = max(
        1,
        round(len(ordered_groups) * validation_ratio),
    )
    training_groups = ordered_groups[:-validation_group_count]
    validation_groups = ordered_groups[-validation_group_count:]
    if not training_groups:
        raise ValueError("切分后训练集为空")

    training = tuple(
        sorted(
            (log for group in training_groups for log in group),
            key=lambda log: (log.event_time, log.log_id),
        )
    )
    validation = tuple(
        sorted(
            (log for group in validation_groups for log in group),
            key=lambda log: (log.event_time, log.log_id),
        )
    )
    training_events = {log.source_event_id for log in training}
    validation_events = {log.source_event_id for log in validation}
    if not training_events.isdisjoint(validation_events):
        raise ValueError("同一 source_event_id 不能同时出现在训练集和验证集")
    return training, validation


class MaintenanceLogIndex:
    def __init__(self, logs: Sequence[MaintenanceLog]) -> None:
        if not logs:
            raise ValueError("检索库不能为空")
        self.logs = tuple(logs)
        self.encoder = TfidfEncoder().fit(
            [f"{log.equipment_type} {log.raw_text}" for log in self.logs]
        )
        self._vectors = tuple(
            self.encoder.transform(f"{log.equipment_type} {log.raw_text}")
            for log in self.logs
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        exclude_log_id: str | None = None,
    ) -> tuple[SearchResult, ...]:
        if not query.strip():
            raise ValueError("查询文本不能为空")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k 必须在 1～20 之间")

        query_vector = self.encoder.transform(query)
        scored = []
        for log, vector in zip(self.logs, self._vectors):
            if log.log_id == exclude_log_id:
                continue
            scored.append((cosine_similarity(query_vector, vector), log))
        scored.sort(key=lambda item: (-item[0], item[1].log_id))
        return tuple(
            SearchResult(
                score=round(score, 6),
                log_id=log.log_id,
                equipment_type=log.equipment_type,
                raw_text=log.raw_text,
                fault_category=log.fault_category,
                action_taken=log.action_taken,
                event_time=log.event_time.isoformat(timespec="minutes"),
            )
            for score, log in scored[:top_k]
        )

    def classify(
        self,
        query: str,
        neighbors: int = 5,
        minimum_similarity: float = 0.08,
    ) -> ClassificationResult:
        results = self.search(query, top_k=neighbors)
        eligible_results = [
            result
            for result in results
            if result.score >= minimum_similarity
        ]
        if not eligible_results:
            return ClassificationResult(
                category="未知",
                confidence=0.0,
                votes=(),
                evidence_log_ids=tuple(result.log_id for result in results),
            )

        votes = defaultdict(float)
        for result in eligible_results:
            votes[result.fault_category] += result.score
        ordered_votes = tuple(
            sorted(
                ((category, round(score, 6)) for category, score in votes.items()),
                key=lambda item: (-item[1], item[0]),
            )
        )
        winner, winner_score = ordered_votes[0]
        total_score = sum(score for _, score in ordered_votes)
        evidence_log_ids = tuple(
            result.log_id
            for result in eligible_results
            if result.fault_category == winner
        )
        return ClassificationResult(
            category=winner,
            confidence=round(winner_score / total_score, 6),
            votes=ordered_votes,
            evidence_log_ids=evidence_log_ids,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="维修日志 TF-IDF 向量检索与归类")
    parser.add_argument("query", help="待检索的维修日志")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--split",
        choices=("time", "equipment"),
        default="time",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        training, validation = split_logs(load_logs(), strategy=args.split)
        index = MaintenanceLogIndex(training)
        results = index.search(args.query, top_k=args.top_k)
        classification = index.classify(args.query, neighbors=args.top_k)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    if args.format == "json":
        payload = {
            "split": {
                "strategy": args.split,
                "training_count": len(training),
                "validation_count": len(validation),
            },
            "classification": asdict(classification),
            "results": [asdict(result) for result in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"候选类别：{classification.category} "
        f"（近邻投票置信度 {classification.confidence:.3f}）"
    )
    print("排名\t分数\t日志 ID\t类别\t原文")
    for rank, result in enumerate(results, start=1):
        print(
            f"{rank}\t{result.score:.4f}\t{result.log_id}\t"
            f"{result.fault_category}\t{result.raw_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
