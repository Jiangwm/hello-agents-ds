from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    ComparisonRequest,
    ExportApprovalRequest,
    ReplayRequest,
    ReviewRequest,
    SimulationCreateRequest,
    StepRequest,
    SuggestionRequest,
)
from .service import SandboxRegistry


app = FastAPI(title="DSExt Digital Factory Sandbox", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
registry = SandboxRegistry()


def domain_call(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"status": "ok", "read_only": True, "production_control": "prohibited"}


@app.get("/api/scenarios")
def scenarios() -> list[dict[str, str | bool]]:
    return registry.scenarios()


@app.post("/api/simulations", status_code=status.HTTP_201_CREATED)
def create_simulation(request: SimulationCreateRequest) -> Any:
    return domain_call(lambda: registry.create(request).inspect())


@app.get("/api/simulations/{simulation_id}")
def get_simulation(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).inspect())


@app.post("/api/simulations/{simulation_id}/step")
def step_simulation(simulation_id: str, request: StepRequest) -> Any:
    def operation() -> Any:
        sandbox = registry.get(simulation_id)
        sandbox.execute("step", request.steps)
        return sandbox.inspect()
    return domain_call(operation)


@app.post("/api/simulations/{simulation_id}/pause")
def pause_simulation(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).execute("pause"))


@app.post("/api/simulations/{simulation_id}/resume")
def resume_simulation(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).execute("resume"))


@app.post("/api/simulations/{simulation_id}/snapshot")
def snapshot_simulation(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).execute("snapshot"))


@app.post("/api/simulations/{simulation_id}/replay")
def replay_simulation(simulation_id: str, request: ReplayRequest) -> Any:
    return domain_call(lambda: registry.get(simulation_id).replay(request))


@app.get("/api/simulations/{simulation_id}/events")
def simulation_events(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).events)


@app.get("/api/simulations/{simulation_id}/events/{event_id}/chain")
def simulation_event_chain(simulation_id: str, event_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).event_chain(event_id))


@app.get("/api/simulations/{simulation_id}/metrics")
def simulation_metrics(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).metrics())


@app.post("/api/simulations/{simulation_id}/suggestions")
def simulation_suggestion(simulation_id: str, request: SuggestionRequest) -> Any:
    return domain_call(lambda: registry.get(simulation_id).suggest(request))


@app.post("/api/simulations/{simulation_id}/decisions/{decision_id}/review")
def review_decision(simulation_id: str, decision_id: str, request: ReviewRequest) -> Any:
    return domain_call(lambda: registry.get(simulation_id).review(decision_id, request))


@app.post("/api/simulations/{simulation_id}/approve-export")
def approve_export(simulation_id: str, request: ExportApprovalRequest) -> Any:
    return domain_call(lambda: registry.get(simulation_id).approve_export(request))


@app.get("/api/simulations/{simulation_id}/export")
def export_simulation(simulation_id: str) -> Any:
    return domain_call(lambda: registry.get(simulation_id).export())


@app.post("/api/comparisons")
def compare_simulations(request: ComparisonRequest) -> Any:
    return domain_call(lambda: registry.compare(request))


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
if (FRONTEND_ROOT / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ROOT / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def frontend_root() -> Any:
    index = FRONTEND_ROOT / "index.html"
    if index.is_file():
        return FileResponse(index)
    return health()


@app.get("/styles.css", include_in_schema=False)
def frontend_styles() -> Any:
    path = FRONTEND_ROOT / "styles.css"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend stylesheet not found")
    return FileResponse(path, media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def frontend_script() -> Any:
    path = FRONTEND_ROOT / "app.js"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend script not found")
    return FileResponse(path, media_type="text/javascript")
