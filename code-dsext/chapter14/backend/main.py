from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


CHAPTER_ROOT = Path(__file__).resolve().parent.parent
if str(CHAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAPTER_ROOT))

from backend.agents.orchestrator import InvestigationContext, OrchestratorError, ResearchOrchestrator
from backend.api_contracts import ReviewInput
from backend.models import Budget, GateName, ResearchRun, ResearchScope
from backend.services.authorization import ActorContext, AuthorizationInputError, Domain
from backend.services.evidence import EvidenceLedger
from backend.services.events import EventInputError, EventService
from backend.services.export import ExportDeniedError, ExportService
from backend.services.gates import GateDecisionError, GateService
from backend.services.notes import NoteService, NoteServiceError
from backend.services.reporting import ReportingError, ReportingService, ResearchReport
from backend.services.repository import JsonValue, Repository, RepositoryError
from backend.services.synthesis import SynthesisService
from backend.settings import Settings
from backend.tools.workspace import ReadOnlyWorkspace


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ActorInput(ApiModel):
    actor_id: Annotated[str, Field(min_length=1)]
    tenant_id: Annotated[str, Field(min_length=1)]
    line_id: Annotated[str, Field(min_length=1)]
    role: Annotated[str, Field(min_length=1)]
    domains: tuple[Domain, ...] = Field(strict=False)

    def domain_model(self) -> ActorContext:
        return ActorContext(
            self.actor_id, self.tenant_id, self.line_id, self.role, frozenset(self.domains)
        )


class ContextInput(ApiModel):
    tenant_id: Annotated[str, Field(min_length=1)]
    line_id: Annotated[str, Field(min_length=1)]
    product: Annotated[str, Field(min_length=1)]
    batch_id: Annotated[str, Field(min_length=1)]
    start_at: Annotated[str, Field(min_length=1)]
    end_at: Annotated[str, Field(min_length=1)]

    def domain_model(self) -> InvestigationContext:
        return InvestigationContext(**self.model_dump())


class RunCreate(ApiModel):
    run_id: Annotated[str, Field(min_length=1)]
    actor: ActorInput
    context: ContextInput
    scope: ResearchScope
    budget: Budget

    @field_validator("scope", mode="before")
    @classmethod
    def parse_scope(cls, value: JsonValue) -> ResearchScope:
        return ResearchScope.model_validate(value, strict=False)


class RunMetadata(ApiModel):
    actor: ActorInput
    context: ContextInput


class DecisionInput(ApiModel):
    approver: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]


class RevisionInput(ApiModel):
    scope: ResearchScope

    @field_validator("scope", mode="before")
    @classmethod
    def parse_scope(cls, value: JsonValue) -> ResearchScope:
        return ResearchScope.model_validate(value, strict=False)


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    offline: Literal[True] = True
    read_only: Literal[True] = True
    production_control: Literal["prohibited"] = "prohibited"


class ApiRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings.resolve()
        self.repository = Repository(
            Path(self.settings.state_dir) / "research.sqlite3", self.settings.workspace_root
        )
        self.repository.initialize()
        self.workspace = ReadOnlyWorkspace(self.settings.workspace_root)
        self.events = EventService(self.repository)

    def create(self, payload: RunCreate) -> ResearchRun:
        orchestrator = ResearchOrchestrator(
            self.repository, self.workspace, payload.actor.domain_model(),
            payload.context.domain_model(),
        )
        run = orchestrator.start(payload.run_id, payload.scope, payload.budget)
        self.repository.replace_or_upsert(
            "audit", run.run_id, "api-runtime",
            RunMetadata(actor=payload.actor, context=payload.context),
        )
        self.events.emit(run.run_id, "status", {"status": "planned", "run_id": run.run_id})
        return run

    def bound(
        self, run_id: str, tenant_id: str
    ) -> tuple[ResearchOrchestrator, ResearchRun, ActorContext]:
        record = next(
            (item for item in self.repository.list_records("audit", run_id)
             if item.record_id == "api-runtime"), None,
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        metadata = RunMetadata.model_validate(record.payload, strict=False)
        if metadata.actor.tenant_id != tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "permission denied")
        actor = metadata.actor.domain_model()
        orchestrator = ResearchOrchestrator(
            self.repository, self.workspace, actor, metadata.context.domain_model()
        )
        return orchestrator, orchestrator.get(run_id), actor

    def report(self, run: ResearchRun) -> tuple[ResearchReport, ReportingService]:
        ledger = EvidenceLedger(self.repository, self.workspace, run.run_id)
        notes = NoteService(self.repository, ledger, run.run_id).list_approved_valid(
            run.scope_version
        )
        synthesis = SynthesisService().synthesize(
            notes, run.evidence, scope_version=run.scope_version
        )
        service = ReportingService(self.repository, ledger)
        return service.generate(run, notes, synthesis), service


def create_app(settings: Settings) -> FastAPI:
    runtime = ApiRuntime(settings)
    app = FastAPI(title="DSExt Quality Deep Research", version="1.0.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=list(runtime.settings.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"],
    )

    @app.exception_handler(OrchestratorError)
    async def orchestrator_error(_, error: OrchestratorError) -> JSONResponse:
        code = status.HTTP_404_NOT_FOUND if error.code.endswith("run_missing") else status.HTTP_409_CONFLICT
        content = {"detail": {"code": error.code, "message": error.detail}}
        return JSONResponse(status_code=code, content=content)

    @app.exception_handler(ReportingError)
    @app.exception_handler(ExportDeniedError)
    @app.exception_handler(GateDecisionError)
    @app.exception_handler(EventInputError)
    @app.exception_handler(NoteServiceError)
    @app.exception_handler(RepositoryError)
    async def conflict_error(_, error: Exception) -> JSONResponse:  # noqa: BROAD_EXCEPT_OK
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(error)})

    @app.exception_handler(AuthorizationInputError)
    async def authorization_error(_, error: AuthorizationInputError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": error.detail})

    @app.exception_handler(sqlite3.IntegrityError)
    async def duplicate_run(_, error: sqlite3.IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": "run already exists"})

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.post("/runs", status_code=status.HTTP_201_CREATED, response_model=ResearchRun)
    def create_run(payload: RunCreate, response: Response) -> ResearchRun:
        run = runtime.create(payload)
        response.headers["Location"] = f"/runs/{run.run_id}"
        return run

    @app.post("/research/stream", status_code=status.HTTP_201_CREATED)
    def research_stream(payload: RunCreate) -> StreamingResponse:
        run = runtime.create(payload)
        return StreamingResponse(
            iter(runtime.events.stream_frames(run.run_id, keepalive=True)),
            status_code=status.HTTP_201_CREATED, media_type="text/event-stream",
        )

    @app.get("/runs/{run_id}", response_model=ResearchRun)
    def get_run(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        return runtime.bound(run_id, tenant_id)[1]

    @app.get("/runs/{run_id}/todos")
    def get_todos(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]):
        return runtime.bound(run_id, tenant_id)[1].todos

    @app.get("/runs/{run_id}/notes")
    def get_notes(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]):
        return runtime.bound(run_id, tenant_id)[1].notes

    @app.post("/runs/{run_id}/step", response_model=ResearchRun)
    def step(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        return runtime.bound(run_id, tenant_id)[0].step(run_id)

    @app.post("/runs/{run_id}/run", response_model=ResearchRun)
    def run(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        return runtime.bound(run_id, tenant_id)[0].run_until_stop(run_id)

    @app.post("/runs/{run_id}/pause", response_model=ResearchRun)
    def pause(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        orchestrator, _, _ = runtime.bound(run_id, tenant_id)
        return orchestrator.pause(run_id, "api-user")

    @app.post("/runs/{run_id}/revise", response_model=ResearchRun)
    def revise(run_id: str, payload: RevisionInput, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        orchestrator, _, _ = runtime.bound(run_id, tenant_id)
        return orchestrator.revise_scope(run_id, payload.scope, "api-user")

    @app.post("/runs/{run_id}/resume", response_model=ResearchRun)
    def resume(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        orchestrator, _, _ = runtime.bound(run_id, tenant_id)
        return orchestrator.resume(run_id, "api-user")

    @app.post("/runs/{run_id}/notes/review", response_model=ResearchRun)
    def review_notes(run_id: str, payload: ReviewInput, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        orchestrator, _, _ = runtime.bound(run_id, tenant_id)
        if payload.note_id is not None and payload.approved is not None:
            return orchestrator.review_note(run_id, payload.note_id, payload.approved, payload.reviewer, payload.reason)
        return orchestrator.review_notes(run_id, payload.reviewer, payload.reason)

    @app.post("/runs/{run_id}/gates/{gate_id}/approve", response_model=ResearchRun)
    def approve_gate(run_id: str, gate_id: str, payload: DecisionInput, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> ResearchRun:
        orchestrator, run, actor = runtime.bound(run_id, tenant_id)
        approver = ActorContext(payload.approver, actor.tenant_id, actor.line_id, "reviewer", actor.allowed_domains)
        gate = next((item for item in run.gates if item.gate_id == gate_id), None)
        if gate is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "gate not found")
        if gate.name is GateName.FINAL_CONCLUSION:
            report, service = runtime.report(run)
            request = service.final_gate_request(run, report, actor.actor_id)
            bound_gate = GateService(runtime.repository).request(request)
            result = orchestrator.approve_gate(run_id, gate_id, approver, payload.reason)
            GateService(runtime.repository).approve(run_id, bound_gate.gate_id, payload.approver, payload.reason)
            return result
        return orchestrator.approve_gate(run_id, gate_id, approver, payload.reason)

    @app.get("/runs/{run_id}/report")
    def report(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> JSONResponse:
        _, run, _ = runtime.bound(run_id, tenant_id)
        generated, _ = runtime.report(run)
        return JSONResponse(content=asdict(generated))

    @app.post("/runs/{run_id}/export")
    def export(run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> JSONResponse:
        _, run, actor = runtime.bound(run_id, tenant_id)
        report, service = runtime.report(run)
        request = service.final_gate_request(run, report, actor.actor_id)
        target = Path(runtime.settings.state_dir) / "exports" / f"{run_id}.json"
        ExportService(
            runtime.repository, EvidenceLedger(runtime.repository, runtime.workspace, run_id),
            GateService(runtime.repository),
        ).export(run, report, request, target)
        return JSONResponse(content=json.loads(target.read_text(encoding="utf-8")))

    @app.get("/runs/{run_id}/events")
    def events(
        run_id: str, tenant_id: Annotated[str, Header(alias="X-Tenant-ID")],
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
    ) -> StreamingResponse:
        runtime.bound(run_id, tenant_id)
        cursor = last_event_id if last_event_id is not None else after
        return StreamingResponse(
            iter(runtime.events.stream_frames(run_id, cursor, keepalive=True)),
            media_type="text/event-stream", headers={"Cache-Control": "no-cache"},
        )

    return app


def _default_settings() -> Settings:
    workspace = Path(os.environ.get("CH14_WORKSPACE", CHAPTER_ROOT / "sample_workspace"))
    default_state = Path(tempfile.gettempdir()) / "hello-agents-chapter14-api"
    state_dir = Path(os.environ.get("CH14_STATE_DIR", default_state))
    return Settings(workspace_root=workspace, state_dir=state_dir)


app = create_app(_default_settings())
