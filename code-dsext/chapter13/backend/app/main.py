from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import (
    ApprovalRequest,
    EnergyCurve,
    EnergyPlan,
    PlanEditRequest,
    PlanningRequest,
)
from .service import EnergyPlanningAssistant


app = FastAPI(title="DSExt Energy Planning Assistant", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type"],
)
assistant = EnergyPlanningAssistant()


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    detail = [
        {
            "type": error["type"],
            "loc": list(error["loc"]),
            "msg": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "read_only": True,
        "production_control": "prohibited",
    }


@app.post(
    "/api/plans",
    response_model=EnergyPlan,
    status_code=status.HTTP_201_CREATED,
)
def create_plan(request: PlanningRequest) -> EnergyPlan:
    try:
        return assistant.create_plan(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plans/{plan_id}", response_model=EnergyPlan)
def get_plan(plan_id: str) -> EnergyPlan:
    try:
        return assistant.get_plan(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/plans/{plan_id}/curve", response_model=EnergyCurve)
def get_curve(plan_id: str, option_id: str | None = None) -> EnergyCurve:
    try:
        return assistant.get_curve(plan_id, option_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/plans/{plan_id}", response_model=EnergyPlan)
def edit_plan(plan_id: str, edit: PlanEditRequest) -> EnergyPlan:
    try:
        return assistant.edit_plan(plan_id, edit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plans/{plan_id}/approve", response_model=EnergyPlan)
def approve_plan(plan_id: str, approval: ApprovalRequest) -> EnergyPlan:
    try:
        return assistant.approve_plan(plan_id, approval)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plans/{plan_id}/export")
def export_plan(plan_id: str) -> dict:
    try:
        return assistant.export_plan(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
