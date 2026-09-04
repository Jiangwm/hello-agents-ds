from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..models.approval import Gate
from ..models.research import Budget
from .gates import GateRequest, GateService


@dataclass(frozen=True, slots=True)
class BudgetValidationError(ValueError):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class BudgetCharge:
    tool_calls: int
    rows_scanned: int
    cost_units: float
    elapsed_ms: int

    def __post_init__(self) -> None:
        if (
            self.tool_calls < 0
            or self.rows_scanned < 0
            or self.cost_units < 0
            or not isfinite(self.cost_units)
            or self.elapsed_ms < 0
        ):
            raise BudgetValidationError(
                "budget.invalid_charge",
                "budget charge values must be finite and non-negative",
            )


@dataclass(frozen=True, slots=True)
class UpdatedBudget:
    budget: Budget
    charge: BudgetCharge


@dataclass(frozen=True, slots=True)
class BudgetExhausted:
    budget: Budget
    charge: BudgetCharge
    exhausted_dimensions: tuple[str, ...]
    gate: Gate | None


class BudgetService:
    def __init__(
        self,
        gate_service: GateService | None = None,
        budget_extension_request: GateRequest | None = None,
    ) -> None:
        if (gate_service is None) != (budget_extension_request is None):
            raise BudgetValidationError(
                "budget.invalid_gate_configuration",
                "gate service and budget extension request must be configured together",
            )
        self._gate_service = gate_service
        self._budget_extension_request = budget_extension_request

    def reserve(
        self, budget: Budget, charge: BudgetCharge
    ) -> UpdatedBudget | BudgetExhausted:
        dimensions = self._exhausted_dimensions(budget, charge)
        if dimensions:
            gate = None
            if self._gate_service is not None and self._budget_extension_request is not None:
                gate = self._gate_service.request(self._budget_extension_request)
            return BudgetExhausted(budget, charge, dimensions, gate)
        updated = Budget(
            max_tool_calls=budget.max_tool_calls,
            max_rows_scanned=budget.max_rows_scanned,
            max_cost=budget.max_cost,
            max_elapsed_ms=budget.max_elapsed_ms,
            consumed_tool_calls=budget.consumed_tool_calls + charge.tool_calls,
            consumed_rows_scanned=budget.consumed_rows_scanned + charge.rows_scanned,
            consumed_cost=budget.consumed_cost + charge.cost_units,
            consumed_elapsed_ms=budget.consumed_elapsed_ms + charge.elapsed_ms,
        )
        return UpdatedBudget(updated, charge)

    @staticmethod
    def _exhausted_dimensions(
        budget: Budget, charge: BudgetCharge
    ) -> tuple[str, ...]:
        exhausted: list[str] = []
        if budget.consumed_tool_calls + charge.tool_calls > budget.max_tool_calls:
            exhausted.append("tool_calls")
        if budget.consumed_rows_scanned + charge.rows_scanned > budget.max_rows_scanned:
            exhausted.append("rows_scanned")
        if budget.consumed_cost + charge.cost_units > budget.max_cost:
            exhausted.append("cost_units")
        if budget.consumed_elapsed_ms + charge.elapsed_ms > budget.max_elapsed_ms:
            exhausted.append("elapsed_ms")
        return tuple(exhausted)
