from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.models import GateName
from backend.services.repository import JsonValue


type JsonObject = dict[str, JsonValue]


class Command(StrEnum):
    DEMO = "demo"
    START = "start"
    STATUS = "status"
    APPROVE = "approve"
    RUN = "run"
    PAUSE = "pause"
    REVISE = "revise"
    RESUME = "resume"
    REVIEW_NOTES = "review-notes"
    REPORT = "report"
    EXPORT = "export"


class CliState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: Path
    actor_id: Annotated[str, Field(min_length=1)]
    tenant_id: Annotated[str, Field(min_length=1)]
    line_id: Annotated[str, Field(min_length=1)]
    product: Annotated[str, Field(min_length=1)]
    batch_id: Annotated[str, Field(min_length=1)]
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end_at <= self.start_at:
            raise CliError("invalid_input", "end_at must be after start_at", 4)
        return self


class WorkspaceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tenant_id: str
    line_id: str
    deidentified: Literal[True]
    read_only: Literal[True]
    production_control: Literal["prohibited"]


@dataclass(frozen=True, slots=True)
class CliError(ValueError):
    code: str
    detail: str
    exit_code: int

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("number must be finite and non-negative")
    return value


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("number must be non-negative")
    return value


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("number must be positive")
    return value


def build_parser(chapter_root: Path) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="离线质量异常深度研究智能体")
    result.add_argument("command", choices=tuple(item.value for item in Command))
    result.add_argument("--workspace", default=str(chapter_root / "sample_workspace"))
    result.add_argument("--state-dir", required=True)
    result.add_argument("--run-id", default="demo-run")
    result.add_argument("--actor", default="researcher")
    result.add_argument("--tenant", default="TENANT-DEMO")
    result.add_argument("--line", default="LINE-A")
    result.add_argument("--product", default="surface_variation")
    result.add_argument("--batch-id", default="LOT-0715-B")
    result.add_argument("--start-at", default="2026-07-10T00:00:00+08:00")
    result.add_argument("--end-at", default="2026-07-17T23:59:59+08:00")
    result.add_argument("--objective", default="定位质量异常候选因素")
    result.add_argument("--max-tool-calls", type=non_negative_int, default=20)
    result.add_argument("--max-cost", type=finite_float, default=20.0)
    result.add_argument("--max-rows-scanned", type=non_negative_int, default=10000)
    result.add_argument("--max-elapsed-ms", type=non_negative_int, default=10000)
    result.add_argument("--max-steps", type=positive_int, default=100)
    result.add_argument("--gate", choices=tuple(item.value for item in GateName))
    result.add_argument("--approver", default="QA")
    result.add_argument("--reviewer", default="QA")
    result.add_argument("--reason", default="人工复核通过")
    result.add_argument("--plan-approver", default="QA")
    result.add_argument("--conclusion-approver", default="QA")
    result.add_argument("--approve-plan", action="store_true")
    result.add_argument("--approve-conclusion", action="store_true")
    result.add_argument("--output")
    return result


__all__ = (
    "CliError", "CliState", "Command", "JsonObject", "WorkspaceIdentity",
    "build_parser",
)
