from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from backend.agents.dispatch import InvestigationContext
from backend.models import (
    EvidenceRef,
    Gate,
    GateName,
    GateStatus,
    NoteStatus,
    ResearchNote,
    ResearchRun,
)
from backend.services.authorization import AccessRequest, ActorContext
from backend.services.evidence import EvidenceLedger
from backend.services.gates import GateRequest
from backend.services.notes import NoteService
from backend.services.repository import JsonValue, Repository, canonical_json
from backend.tools.workspace import ReadOnlyWorkspace


@dataclass(frozen=True, slots=True)
class OrchestratorError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)


@dataclass(frozen=True, slots=True)
class LoadedRun:
    version: int
    model: ResearchRun


class OrchestratorState:
    def __init__(
        self,
        repository: Repository,
        workspace: ReadOnlyWorkspace,
        actor: ActorContext,
        context: InvestigationContext,
    ) -> None:
        self.repository = repository
        self.workspace = workspace
        self.actor = actor
        self.context = context

    def load(self, run_id: str) -> LoadedRun:
        record = self.repository.get_run(run_id)
        if record is None:
            raise OrchestratorError(
                "orchestrator.run_missing", f"run not found: {run_id}"
            )
        return LoadedRun(
            record.version, ResearchRun.model_validate(record.payload, strict=False)
        )

    def save(self, version: int, run: ResearchRun) -> ResearchRun:
        self.repository.update_run(
            run.run_id, run.model_dump(mode="json"), expected_version=version
        )
        self.persist_records(run)
        return run

    def persist_records(self, run: ResearchRun) -> None:
        for todo in run.todos:
            self.repository.replace_or_upsert(
                "todos", run.run_id, todo.todo_id, todo
            )
        for table, records in (
            ("evidence", run.evidence),
            ("notes", run.notes),
            ("gates", run.gates),
        ):
            existing = {
                item.record_id for item in self.repository.list_records(table, run.run_id)
            }
            for record in records:
                identifier = self._record_id(record)
                if identifier not in existing:
                    self.repository.replace_or_upsert(
                        table, run.run_id, identifier, record
                    )

    def gate_request(self, run: ResearchRun, name: GateName) -> GateRequest:
        evidence_version = self.hash_value(
            [[item.evidence_id, item.content_hash] for item in run.evidence]
        )
        budget_payload = run.budget.model_dump(mode="json") if run.budget else {}
        return GateRequest(
            run.run_id,
            name,
            self.actor.actor_id,
            run.plan_version,
            run.scope_version,
            evidence_version,
            self.hash_value(budget_payload),
        )

    def cross_request(self, run_id: str, gate_id: str) -> AccessRequest:
        record = next(
            (
                item
                for item in self.repository.list_records("gates", run_id)
                if item.record_id == gate_id
            ),
            None,
        )
        if record is None:
            raise OrchestratorError(
                "orchestrator.gate_missing", "cross-domain gate missing"
            )
        fields = (
            "tenant_id",
            "line_id",
            "domain",
            "scope_hash",
            "plan_version",
            "scope_version",
        )
        values = tuple(record.payload.get(field) for field in fields)
        if not all(isinstance(item, str) for item in values):
            raise OrchestratorError(
                "orchestrator.gate_binding", "cross-domain gate binding invalid"
            )
        tenant, line, domain, scope_hash, plan, scope = values
        if domain not in {
            "quality",
            "process",
            "equipment",
            "material",
            "internal_docs",
            "external_knowledge",
        }:
            raise OrchestratorError(
                "orchestrator.gate_domain", "cross-domain gate domain invalid"
            )
        return AccessRequest(run_id, tenant, line, domain, scope_hash, plan, scope)

    def approval_current(self, run_id: str, gate: Gate) -> bool:
        return any(
            item.payload.get("gate_id") == gate.gate_id
            and item.payload.get("input_hash") == gate.input_hash
            and item.payload.get("plan_version") == gate.plan_version
            and item.payload.get("scope_version") == gate.scope_version
            for item in self.repository.list_records("approvals", run_id)
        )

    def notes(self, run_id: str) -> NoteService:
        return NoteService(
            self.repository,
            EvidenceLedger(self.repository, self.workspace, run_id),
            run_id,
        )

    def supersede_notes(self, run_id: str, scope_version: str, actor: str) -> None:
        self.notes(run_id).supersede_for_scope(scope_version, actor=actor)
        for record in self.repository.list_records("notes", run_id):
            if (
                record.payload.get("current") is True
                and record.payload.get("scope_version") != scope_version
            ):
                payload = dict(record.payload)
                payload.update(
                    {
                        "status": NoteStatus.SUPERSEDED.value,
                        "reviewer": None,
                        "current": False,
                        "superseded_reason": "scope_version_changed",
                    }
                )
                self.repository.replace_or_upsert(
                    "notes", run_id, record.record_id, payload
                )

    def placeholders(self, plan: str, scope: str) -> tuple[Gate, ...]:
        return tuple(
            Gate(
                gate_id=f"{plan}:{name.value}",
                name=name,
                input_hash=sha256(f"{plan}:{scope}:{name.value}".encode()).hexdigest(),
                plan_version=plan,
                scope_version=scope,
                status=GateStatus.PENDING,
            )
            for name in GateName
        )

    def scope_hash(self, run: ResearchRun) -> str:
        context = {
            "tenant_id": self.context.tenant_id,
            "line_id": self.context.line_id,
            "product": self.context.product,
            "batch_id": self.context.batch_id,
            "start_at": self.context.start_at,
            "end_at": self.context.end_at,
        }
        return self.hash_value(
            {
                "scope": run.scope.model_dump(mode="json"),
                "context": context,
                "plan_version": run.plan_version,
                "scope_version": run.scope_version,
            }
        )

    @staticmethod
    def actor_id(actor: ActorContext | str) -> str:
        value = actor.actor_id if isinstance(actor, ActorContext) else actor
        if not value.strip():
            raise OrchestratorError("orchestrator.actor", "actor must not be blank")
        return value

    @staticmethod
    def version_number(value: str) -> int:
        number = value.rsplit("v", 1)[-1]
        if not number.isdigit():
            raise OrchestratorError(
                "orchestrator.version", "version suffix must be numeric"
            )
        return int(number)

    @staticmethod
    def hash_value(value: JsonValue) -> str:
        return sha256(canonical_json({"value": value}).encode()).hexdigest()

    @staticmethod
    def _record_id(record: EvidenceRef | ResearchNote | Gate) -> str:
        if isinstance(record, EvidenceRef):
            return record.evidence_id
        if isinstance(record, ResearchNote):
            return record.note_id
        return record.gate_id
