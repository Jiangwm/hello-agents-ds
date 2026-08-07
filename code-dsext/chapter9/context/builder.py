from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from notes.store import NoteRecord


_RAW_FIELD_NAMES = frozenset(
    {"raw_data", "rows", "row", "sample", "samples", "dataframe", "records"}
)
_NOTE_SECTIONS = {
    "summary": "summaries",
    "decision": "decisions",
    "fact": "facts",
    "hypothesis": "hypotheses",
    "todo": "todos",
    "risk": "risks",
}


@dataclass(frozen=True)
class ContextBuildResult:
    context_json: str
    included_note_ids: list[str]
    excluded_note_ids: list[str]
    compressed: bool

    @property
    def text(self) -> str:
        return self.context_json

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_json": self.context_json,
            "included_note_ids": list(self.included_note_ids),
            "excluded_note_ids": list(self.excluded_note_ids),
            "compressed": self.compressed,
        }


class InvestigationContextBuilder:
    def __init__(self, *, max_notes: int = 12, max_chars: int = 6_000) -> None:
        if max_notes < 1:
            raise ValueError("max_notes 必须至少为 1")
        if max_chars < 256:
            raise ValueError("max_chars 必须至少为 256")
        self.max_notes = max_notes
        self.max_chars = max_chars

    def build(
        self,
        *,
        target: str | Mapping[str, Any],
        state: Mapping[str, Any],
        notes: Iterable[NoteRecord | Mapping[str, Any]],
        tool_summaries: Sequence[Mapping[str, Any] | str],
        current_question: str,
    ) -> ContextBuildResult:
        if not isinstance(current_question, str) or not current_question.strip():
            raise ValueError("current_question 必须是非空字符串")
        note_items = [self._normalise_note(note) for note in notes]
        candidates, always_excluded = self._candidates(
            note_items, current_question=current_question
        )
        base = self._base_context(target, state, tool_summaries, current_question)
        base_compressed = False
        if len(self._render(self._with_notes(base, []))) > self.max_chars:
            base = self._trim_base(self._with_notes(base, []))
            base_compressed = True
        included: list[dict[str, Any]] = []
        included_ids: list[str] = []
        excluded_ids = list(always_excluded)
        for note in candidates:
            note_id = note["id"]
            if len(included) >= self.max_notes:
                excluded_ids.append(note_id)
                continue
            trial = included + [note]
            if len(self._render(self._with_notes(base, trial))) <= self.max_chars:
                included.append(note)
                included_ids.append(note_id)
            else:
                excluded_ids.append(note_id)
        context = self._with_notes(base, included)
        rendered = self._render(context)
        if len(rendered) > self.max_chars:
            context = self._trim_base(context)
            rendered = self._render(context)
        if len(rendered) > self.max_chars:
            raise ValueError("max_chars 不足以容纳最小调查上下文")
        return ContextBuildResult(
            context_json=rendered,
            included_note_ids=included_ids,
            excluded_note_ids=excluded_ids,
            compressed=base_compressed or bool(excluded_ids),
        )

    def _normalise_note(self, note: NoteRecord | Mapping[str, Any]) -> dict[str, Any]:
        data = note.to_dict() if isinstance(note, NoteRecord) else dict(note)
        required = {"id", "note_type", "status", "title", "content"}
        missing = required.difference(data)
        if missing:
            raise ValueError(f"上下文笔记缺少字段: {', '.join(sorted(missing))}")
        return _strip_raw_data(data)

    def _candidates(
        self,
        notes: Sequence[dict[str, Any]],
        current_question: str = "",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        priority = {
            "decision": 0,
            "summary": 1,
            "fact": 2,
            "hypothesis": 3,
            "todo": 4,
            "risk": 5,
        }
        candidates: list[dict[str, Any]] = []
        excluded: list[str] = []
        for note in notes:
            note_type = note["note_type"]
            status = note["status"]
            note_id = note["id"]
            is_confirmed_decision = (
                note_type == "decision" and note.get("human_confirmed") is True
            )
            is_current_summary = note_type == "summary" and status == "active"
            is_current_fact = note_type == "fact" and status == "active"
            is_active_work = note_type in {"hypothesis", "todo", "risk"} and status == "active"
            if not (
                is_current_summary
                or is_current_fact
                or is_confirmed_decision
                or is_active_work
            ):
                excluded.append(note_id)
                continue
            if note_type not in priority:
                excluded.append(note_id)
                continue
            candidates.append(note)
        question_tokens = _text_tokens(current_question)
        candidates.sort(key=lambda note: note["id"])
        candidates.sort(
            key=lambda note: note.get("updated_at", ""),
            reverse=True,
        )
        candidates.sort(
            key=lambda note: _text_relevance(note, question_tokens),
            reverse=True,
        )
        candidates.sort(key=lambda note: priority[note["note_type"]])
        return candidates, excluded

    def _base_context(
        self,
        target: str | Mapping[str, Any],
        state: Mapping[str, Any],
        tool_summaries: Sequence[Mapping[str, Any] | str],
        current_question: str,
    ) -> dict[str, Any]:
        clean_state = _strip_raw_data(dict(state))
        stage = clean_state.pop("stage", clean_state.pop("current_stage", "unspecified"))
        limitations = clean_state.pop("limitations", clean_state.pop("limits", []))
        summaries = [_strip_raw_data(summary) for summary in tool_summaries[-4:]]
        return {
            "target": _strip_raw_data(target),
            "stage": stage,
            "current_question": current_question,
            "summaries": [],
            "facts": [],
            "hypotheses": [],
            "todos": [],
            "risks": [],
            "decisions": [],
            "tool_summaries": summaries,
            "limitations": limitations,
        }

    def _with_notes(
        self, base: Mapping[str, Any], notes: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        context = dict(base)
        for section in _NOTE_SECTIONS.values():
            context[section] = []
        for note in notes:
            context[_NOTE_SECTIONS[note["note_type"]]].append(note)
        return context

    def _trim_base(self, context: Mapping[str, Any]) -> dict[str, Any]:
        trimmed = _truncate_strings(dict(context), limit=96)
        summaries = trimmed.get("tool_summaries", [])
        if isinstance(summaries, list):
            trimmed["tool_summaries"] = [
                _limit_structure(summary, max_items=8)
                for summary in summaries[-2:]
            ]
        return trimmed

    @staticmethod
    def _render(context: Mapping[str, Any]) -> str:
        return json.dumps(context, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _strip_raw_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_raw_data(item)
            for key, item in value.items()
            if str(key).lower() not in _RAW_FIELD_NAMES
        }
    if isinstance(value, (list, tuple)):
        return [_strip_raw_data(item) for item in value]
    return value


def _text_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    text = value.casefold()
    for match in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        chunk = match.group(0)
        if "\u4e00" <= chunk[0] <= "\u9fff":
            tokens.update(chunk)
            tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        else:
            tokens.add(chunk)
    return tokens


def _text_relevance(note: Mapping[str, Any], question_tokens: set[str]) -> int:
    if not question_tokens:
        return 0
    note_tokens = _text_tokens(
        f"{note.get('title', '')} {note.get('content', '')}"
    )
    return len(question_tokens.intersection(note_tokens))


def _truncate_strings(value: Any, *, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit - 1]}…"
    if isinstance(value, Mapping):
        return {key: _truncate_strings(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(item, limit=limit) for item in value]
    return value


def _limit_structure(value: Any, *, max_items: int) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _limit_structure(item, max_items=max_items)
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [
            _limit_structure(item, max_items=max_items)
            for item in value[:max_items]
        ]
    return value
