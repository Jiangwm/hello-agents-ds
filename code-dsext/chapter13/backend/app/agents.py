from __future__ import annotations

from copy import deepcopy

from .engine import DeterministicEnergyEngine
from .models import (
    EnergyBaseline,
    EnergyDataMetadata,
    OperationConstraint,
    PlanningRequest,
    ProductionTask,
    RecommendationEvidence,
    ScheduleOption,
    TariffPeriod,
)


class EnergyDataValidationAgent:
    def __init__(self, engine: DeterministicEnergyEngine):
        self.engine = engine

    def validate(
        self,
        request: PlanningRequest,
        tasks: list[ProductionTask],
        tariffs: list[TariffPeriod],
        baselines: list[EnergyBaseline],
        constraints: list[OperationConstraint],
        metadata: EnergyDataMetadata,
    ) -> list[str]:
        self.engine.validate_tariffs(tariffs)
        task_ids = {task.task_id for task in tasks}
        if len(task_ids) != len(tasks):
            raise ValueError("production task ids must be unique")
        missing = set(request.locked_task_ids) - task_ids
        if missing:
            raise ValueError(f"locked_task_ids not found: {sorted(missing)}")
        missing_baselines = task_ids - {item.task_id for item in baselines}
        if missing_baselines:
            raise ValueError(
                f"energy baselines not found: {sorted(missing_baselines)}"
            )
        if request.output_target_units > min(task.batch_size for task in tasks):
            raise ValueError(
                "output_target_units exceeds the available production task batch size"
            )
        constraint_ids = [item.constraint_id for item in constraints]
        if len(set(constraint_ids)) != len(constraint_ids):
            raise ValueError("constraint ids must be unique")
        fixed_starts: dict[str, int] = {}
        selected_resources: dict[str, str] = {}
        task_by_id = {task.task_id: task for task in tasks}
        constrained_windows = {
            task.task_id: [
                task.earliest_start_minute,
                min(task.latest_end_minute, task.due_minute),
            ]
            for task in tasks
        }
        for constraint in constraints:
            if constraint.kind == "peak_limit":
                if constraint.task_id != "*" or float(constraint.value) <= 0:
                    raise ValueError("peak_limit must target * with a positive value")
                continue
            task = task_by_id.get(constraint.task_id)
            if task is None:
                raise ValueError(
                    f"constraint references unknown task: {constraint.task_id}"
                )
            if constraint.kind == "precedence":
                if constraint.related_task_id not in task_ids:
                    raise ValueError(
                        "precedence constraint references unknown related task: "
                        f"{constraint.related_task_id}"
                    )
            elif constraint.kind == "fixed":
                fixed_start = int(constraint.value)
                if (
                    task.task_id in fixed_starts
                    and fixed_starts[task.task_id] != fixed_start
                ):
                    raise ValueError("task has conflicting fixed constraints")
                if fixed_start % self.engine.interval_minutes:
                    raise ValueError("fixed constraint must align to 30 minutes")
                if (
                    fixed_start < task.earliest_start_minute
                    or fixed_start + task.duration_minutes
                    > min(task.latest_end_minute, task.due_minute)
                ):
                    raise ValueError("fixed constraint is outside the task window")
                if (task.locked or not task.movable) and (
                    task.start_minute != fixed_start
                ):
                    raise ValueError("fixed constraint conflicts with an immutable task")
                fixed_starts[task.task_id] = fixed_start
            elif constraint.kind == "duration":
                if task.duration_minutes != int(constraint.value):
                    raise ValueError("duration constraint conflicts with task duration")
            elif constraint.kind == "window" and isinstance(
                constraint.value,
                list,
            ):
                window = constrained_windows[task.task_id]
                window[0] = max(window[0], constraint.value[0])
                window[1] = min(window[1], constraint.value[1])
            elif constraint.kind == "resource":
                resource_id = str(constraint.value)
                if resource_id not in task.device_candidates:
                    raise ValueError(
                        "resource constraint is not in task device_candidates"
                    )
                if (
                    task.task_id in selected_resources
                    and selected_resources[task.task_id] != resource_id
                ):
                    raise ValueError("task has conflicting resource constraints")
                if (task.locked or not task.movable) and (
                    task.selected_device_id != resource_id
                ):
                    raise ValueError(
                        "resource constraint conflicts with an immutable task"
                    )
                selected_resources[task.task_id] = resource_id
        for task in tasks:
            window_start, window_end = constrained_windows[task.task_id]
            if window_start + task.duration_minutes > window_end:
                raise ValueError(
                    f"selected window is shorter than task duration: {task.task_id}"
                )
            if task.task_id in fixed_starts and not (
                window_start <= fixed_starts[task.task_id]
                and fixed_starts[task.task_id] + task.duration_minutes
                <= window_end
            ):
                raise ValueError("fixed constraint is outside the selected window")
            if task.locked or not task.movable or task.task_id in fixed_starts:
                start_minute = fixed_starts.get(task.task_id, task.start_minute)
                if not self.engine.fits_selected_shifts(
                    start_minute,
                    task.duration_minutes,
                    request.shifts,
                ):
                    raise ValueError(
                        f"task {task.task_id} is outside selected shifts"
                    )
        if metadata.observed_meter_points == 0:
            raise ValueError("historical energy meter window is empty")
        issues: list[str] = []
        if metadata.observed_meter_points < metadata.expected_meter_points:
            issues.append(
                "计量点不完整："
                f"{metadata.observed_meter_points}/"
                f"{metadata.expected_meter_points}"
            )
        if metadata.timezone != "Asia/Shanghai":
            issues.append(f"时区未对齐：{metadata.timezone}")
        if metadata.anomaly_rate > 0:
            issues.append(f"历史功率异常率：{metadata.anomaly_rate:.2%}")
        return issues


class EnergyForecastAgent:
    def apply_baselines(
        self,
        tasks: list[ProductionTask],
        baselines: list[EnergyBaseline],
        output_units: int,
    ) -> tuple[list[ProductionTask], list[EnergyBaseline]]:
        forecast = deepcopy(tasks)
        baseline_by_task = {item.task_id: item for item in baselines}
        scaled_baselines: list[EnergyBaseline] = []
        for task in forecast:
            baseline = baseline_by_task[task.task_id]
            load_factor = output_units / task.batch_size
            task.batch_size = output_units
            task.power_kw = round(baseline.expected_power_kw * load_factor, 6)
            scaled_baselines.append(
                baseline.model_copy(
                    update={
                        "expected_power_kw": round(
                            baseline.expected_power_kw * load_factor,
                            6,
                        ),
                        "lower_power_kw": round(
                            baseline.lower_power_kw * load_factor,
                            6,
                        ),
                        "upper_power_kw": round(
                            baseline.upper_power_kw * load_factor,
                            6,
                        ),
                    }
                )
            )
        return forecast, scaled_baselines


class ScheduleOptimizationAgent:
    def generate(
        self,
        tasks: list[ProductionTask],
        tariffs: list[TariffPeriod],
        constraints: list[OperationConstraint],
        shifts: list[str],
    ) -> dict[str, list[ProductionTask]]:
        return {
            "cost": self._schedule(tasks, tariffs, constraints, shifts, "cost"),
            "peak": self._schedule(tasks, tariffs, constraints, shifts, "peak"),
            "balanced": self._schedule(
                tasks,
                tariffs,
                constraints,
                shifts,
                "balanced",
            ),
        }

    def _schedule(
        self,
        tasks: list[ProductionTask],
        tariffs: list[TariffPeriod],
        constraints: list[OperationConstraint],
        shifts: list[str],
        strategy: str,
    ) -> list[ProductionTask]:
        scheduled = deepcopy(tasks)
        fixed_starts = {
            constraint.task_id: int(constraint.value)
            for constraint in constraints
            if constraint.kind == "fixed"
        }
        selected_resources = {
            constraint.task_id: str(constraint.value)
            for constraint in constraints
            if constraint.kind == "resource"
        }
        constrained_windows = {
            task.task_id: [
                task.earliest_start_minute,
                min(task.latest_end_minute, task.due_minute),
            ]
            for task in scheduled
        }
        for constraint in constraints:
            if constraint.kind == "window" and isinstance(
                constraint.value,
                list,
            ):
                window = constrained_windows[constraint.task_id]
                window[0] = max(window[0], constraint.value[0])
                window[1] = min(window[1], constraint.value[1])
        for task in scheduled:
            if task.task_id in fixed_starts:
                task.start_minute = fixed_starts[task.task_id]
                task.locked = True
            if task.task_id in selected_resources:
                task.selected_device_id = selected_resources[task.task_id]
        fixed = [
            task
            for task in scheduled
            if not task.movable or task.locked or task.task_id in fixed_starts
        ]
        movable = [task for task in scheduled if task not in fixed]
        placed = list(fixed)
        for index, task in enumerate(movable):
            window_start, window_end = constrained_windows[task.task_id]
            positions = [
                minute
                for minute in range(
                    window_start,
                    window_end - task.duration_minutes + 1,
                    30,
                )
                if DeterministicEnergyEngine.fits_selected_shifts(
                    minute,
                    task.duration_minutes,
                    shifts,
                )
                and self._resource_is_available(minute, task, placed)
                and self._respects_precedence(
                    minute,
                    task,
                    scheduled,
                    placed,
                    constraints,
                )
            ]
            if not positions:
                raise ValueError(
                    "no feasible position under selected shifts, resource and "
                    "precedence constraints: "
                    f"{task.task_id}"
                )
            task.start_minute = min(
                positions,
                key=lambda minute: self._score(
                    minute, task, placed, tariffs, strategy, index
                ),
            )
            placed.append(task)
        return scheduled

    @staticmethod
    def _respects_precedence(
        minute: int,
        task: ProductionTask,
        tasks: list[ProductionTask],
        placed: list[ProductionTask],
        constraints: list[OperationConstraint],
    ) -> bool:
        by_id = {task.task_id: task for task in tasks}
        placed_ids = {item.task_id for item in placed}
        for constraint in constraints:
            if constraint.kind != "precedence" or not constraint.related_task_id:
                continue
            successor = by_id.get(constraint.task_id)
            predecessor = by_id.get(constraint.related_task_id)
            if successor is None:
                raise ValueError(
                    f"constraint references unknown task: {constraint.task_id}"
                )
            if predecessor is None:
                raise ValueError(
                    "precedence constraint references unknown related task: "
                    f"{constraint.related_task_id}"
                )
            if task.task_id == successor.task_id:
                if (
                    predecessor.task_id in placed_ids
                    or predecessor.locked
                    or not predecessor.movable
                ) and minute < (
                    predecessor.start_minute + predecessor.duration_minutes
                ):
                    return False
            elif task.task_id == predecessor.task_id:
                if (
                    successor.task_id in placed_ids
                    or successor.locked
                    or not successor.movable
                ) and minute + task.duration_minutes > successor.start_minute:
                    return False
        return True

    @staticmethod
    def _resource_is_available(
        minute: int,
        task: ProductionTask,
        placed: list[ProductionTask],
    ) -> bool:
        end_minute = minute + task.duration_minutes
        return all(
            other.selected_device_id != task.selected_device_id
            or end_minute <= other.start_minute
            or minute >= other.start_minute + other.duration_minutes
            for other in placed
        )

    @staticmethod
    def _score(
        minute: int,
        task: ProductionTask,
        placed: list[ProductionTask],
        tariffs: list[TariffPeriod],
        strategy: str,
        index: int,
    ) -> tuple[float, int]:
        intervals = range(minute, minute + task.duration_minutes, 30)
        tariff_cost = sum(
            next(
                period.price_per_kwh
                for period in tariffs
                if period.start_minute <= point < period.end_minute
            )
            for point in intervals
        )
        overlap_peak = max(
            (
                task.power_kw
                + sum(
                    other.power_kw
                    for other in placed
                    if other.start_minute
                    <= point
                    < other.start_minute + other.duration_minutes
                )
                for point in intervals
            ),
            default=task.power_kw,
        )
        if strategy == "cost":
            return tariff_cost, minute
        if strategy == "peak":
            return overlap_peak, minute
        preferred = 420 + index * 180
        return tariff_cost + overlap_peak * 0.02 + abs(minute - preferred) / 1440, minute


class ScheduleReviewAgent:
    def __init__(self, engine: DeterministicEnergyEngine):
        self.engine = engine

    def review(
        self,
        option_id: str,
        name: str,
        strategy: str,
        tasks: list[ProductionTask],
        tariffs: list[TariffPeriod],
        constraints: list[OperationConstraint],
        shifts: list[str],
        output_units: int,
        evidence: RecommendationEvidence,
        baselines: list[EnergyBaseline] | None = None,
    ) -> ScheduleOption:
        metrics = self.engine.calculate(tasks, tariffs, output_units, baselines)
        violations = self.engine.check_constraints(tasks, constraints, shifts)
        return ScheduleOption(
            option_id=option_id,
            name=name,
            strategy=strategy,
            tasks=tasks,
            curve=metrics.curve,
            cost=metrics.cost,
            total_energy_kwh=metrics.total_energy_kwh,
            peak_kw=metrics.peak_kw,
            unit_energy_kwh=metrics.unit_energy_kwh,
            confidence_interval_kwh=metrics.confidence_interval_kwh,
            violations=violations,
            feasible=not violations,
            evidence=evidence.model_copy(update={"reviewed": True}),
        )


class EnergyReportAgent:
    def recommend(
        self, request: PlanningRequest, candidates: list[ScheduleOption]
    ) -> str:
        feasible = [candidate for candidate in candidates if candidate.feasible]
        if not feasible:
            raise ValueError("no feasible schedule option")
        if request.optimization_objective == "cost":
            selected = min(feasible, key=lambda item: item.cost.total_cost)
        elif request.optimization_objective == "peak":
            selected = min(feasible, key=lambda item: item.peak_kw)
        else:
            selected = next(
                (item for item in feasible if item.strategy == "balanced"),
                feasible[0],
            )
        return selected.option_id
