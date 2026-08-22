from __future__ import annotations

from typing import Any

from .engine import DigitalFactorySandbox
from .models import ComparisonRequest, SimulationCreateRequest
from .scenario_loader import list_scenarios


class SandboxRegistry:
    def __init__(self) -> None:
        self.simulations: dict[str, DigitalFactorySandbox] = {}

    def scenarios(self) -> list[dict[str, str | bool]]:
        return list_scenarios()

    def create(self, request: SimulationCreateRequest) -> DigitalFactorySandbox:
        sandbox = DigitalFactorySandbox.create(request.scenario_id, request.seed, request.strategy)
        self.simulations[sandbox.simulation_id] = sandbox
        return sandbox

    def get(self, simulation_id: str) -> DigitalFactorySandbox:
        if simulation_id not in self.simulations:
            raise KeyError("simulation not found")
        return self.simulations[simulation_id]

    def compare(self, request: ComparisonRequest) -> dict[str, Any]:
        results = []
        for strategy in ("fixed_rule", "single_agent", "multi_agent"):
            sandbox = DigitalFactorySandbox.create(request.scenario_id, request.seed, strategy)
            sandbox.execute("step", request.steps)
            results.append(
                {
                    "strategy": strategy,
                    "strategy_version": sandbox.strategy_version,
                    "seed": request.seed,
                    "scenario_sha256": sandbox.scenario_sha256,
                    "trajectory_hash": sandbox.trajectory_hash,
                    "metrics": sandbox.metrics().model_dump(mode="json"),
                }
            )
        return {
            "scenario_id": request.scenario_id,
            "seed": request.seed,
            "steps": request.steps,
            "fairness": {
                "same_scenario": len({item["scenario_sha256"] for item in results}) == 1,
                "same_seed": len({item["seed"] for item in results}) == 1,
                "same_horizon": True,
            },
            "eligible_for_ranking": all(
                item["metrics"]["pending_decisions"] == 0 for item in results
            ),
            "ranking_note": (
                "仅当所有策略均无待复核决策时才允许排序；本报告不外推真实工厂收益。"
            ),
            "results": results,
            "read_only": True,
            "production_control": "prohibited",
        }
