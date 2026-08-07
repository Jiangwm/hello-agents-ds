from __future__ import annotations

from .models import (
    CostBreakdown,
    ConstraintViolation,
    EnergyCurve,
    EnergyBaseline,
    EnergyMetrics,
    EnergyPoint,
    OperationConstraint,
    ProductionTask,
    TariffPeriod,
)


class DeterministicEnergyEngine:
    interval_minutes = 30
    shift_windows = {
        "day": ((360, 1080),),
        "night": ((0, 360), (1080, 1440)),
    }

    def calculate(
        self,
        tasks: list[ProductionTask],
        tariffs: list[TariffPeriod],
        output_units: int,
        baselines: list[EnergyBaseline] | None = None,
    ) -> EnergyMetrics:
        if output_units <= 0:
            raise ValueError("output_units must be positive")
        points: list[EnergyPoint] = []
        costs: dict[str, float] = {}
        total_energy = 0.0
        peak_kw = 0.0
        for minute in range(0, 1440, self.interval_minutes):
            power = sum(
                task.power_kw
                for task in tasks
                if task.start_minute <= minute < task.start_minute + task.duration_minutes
            )
            tariff = self._tariff_at(minute, tariffs)
            energy = power * self.interval_minutes / 60
            cost = energy * tariff.price_per_kwh
            if cost:
                costs[tariff.name] = costs.get(tariff.name, 0.0) + cost
            total_energy += energy
            peak_kw = max(peak_kw, power)
            points.append(
                EnergyPoint(
                    start_minute=minute,
                    power_kw=round(power, 6),
                    energy_kwh=round(energy, 6),
                    tariff_period=tariff.name,
                    price_per_kwh=tariff.price_per_kwh,
                )
            )
        rounded_energy = round(total_energy, 6)
        if baselines:
            task_hours = {
                task.task_id: task.duration_minutes / 60 for task in tasks
            }
            lower = sum(
                item.lower_power_kw * task_hours.get(item.task_id, 0)
                for item in baselines
            )
            upper = sum(
                item.upper_power_kw * task_hours.get(item.task_id, 0)
                for item in baselines
            )
        else:
            lower = rounded_energy * 0.9
            upper = rounded_energy * 1.1
        return EnergyMetrics(
            curve=EnergyCurve(points=points),
            cost=CostBreakdown(
                by_period={key: round(value, 6) for key, value in costs.items()},
                total_cost=round(sum(costs.values()), 6),
            ),
            total_energy_kwh=rounded_energy,
            peak_kw=round(peak_kw, 6),
            unit_energy_kwh=round(rounded_energy / output_units, 6),
            confidence_interval_kwh=(
                round(lower, 6),
                round(upper, 6),
            ),
        )

    @staticmethod
    def validate_tariffs(tariffs: list[TariffPeriod]) -> None:
        ordered = sorted(tariffs, key=lambda item: item.start_minute)
        if not ordered or ordered[0].start_minute != 0:
            raise ValueError("tariff periods contain a gap before minute 0")
        previous_end = 0
        for tariff in ordered:
            if tariff.start_minute < previous_end:
                raise ValueError("tariff periods overlap")
            if tariff.start_minute > previous_end:
                raise ValueError("tariff periods contain a gap")
            previous_end = tariff.end_minute
        if previous_end != 1440:
            raise ValueError("tariff periods contain a gap before minute 1440")

    @staticmethod
    def check_constraints(
        tasks: list[ProductionTask],
        constraints: list[OperationConstraint],
        shifts: list[str] | None = None,
    ) -> list[ConstraintViolation]:
        by_id = {task.task_id: task for task in tasks}
        violations: list[ConstraintViolation] = []
        if shifts is not None:
            for task in tasks:
                if not DeterministicEnergyEngine.fits_selected_shifts(
                    task.start_minute,
                    task.duration_minutes,
                    shifts,
                ):
                    violations.append(
                        ConstraintViolation(
                            constraint_id=f"C-SHIFT-{task.task_id}",
                            task_id=task.task_id,
                            message=(
                                "task is outside selected shifts: "
                                f"{','.join(shifts)}"
                            ),
                        )
                    )
        for task in tasks:
            if task.start_minute + task.duration_minutes > task.due_minute:
                violations.append(
                    ConstraintViolation(
                        constraint_id=f"C-DUE-{task.task_id}",
                        task_id=task.task_id,
                        message=f"task misses delivery minute {task.due_minute}",
                    )
                )
        for index, task in enumerate(tasks):
            for other in tasks[index + 1 :]:
                if task.selected_device_id != other.selected_device_id:
                    continue
                if (
                    task.start_minute < other.start_minute + other.duration_minutes
                    and other.start_minute
                    < task.start_minute + task.duration_minutes
                ):
                    violations.append(
                        ConstraintViolation(
                            constraint_id=(
                                "C-RESOURCE-OVERLAP-"
                                f"{task.task_id}-{other.task_id}"
                            ),
                            task_id=other.task_id,
                            message=(
                                f"tasks share resource {task.selected_device_id} "
                                "at overlapping times"
                            ),
                        )
                    )
        for constraint in constraints:
            if constraint.kind == "peak_limit":
                peak = max(
                    (
                        sum(
                            task.power_kw
                            for task in tasks
                            if task.start_minute
                            <= minute
                            < task.start_minute + task.duration_minutes
                        )
                        for minute in range(0, 1440, 30)
                    ),
                    default=0,
                )
                if peak > float(constraint.value):
                    violations.append(
                        ConstraintViolation(
                            constraint_id=constraint.constraint_id,
                            message=f"peak {peak:g} kW exceeds limit {constraint.value}",
                        )
                    )
                continue
            task = by_id.get(constraint.task_id)
            if task is None:
                violations.append(
                    ConstraintViolation(
                        constraint_id=constraint.constraint_id,
                        task_id=constraint.task_id,
                        message="constraint references an unknown task",
                    )
                )
                continue
            if constraint.kind == "fixed" and task.start_minute != int(constraint.value):
                violations.append(
                    ConstraintViolation(
                        constraint_id=constraint.constraint_id,
                        task_id=task.task_id,
                        message=f"fixed task must start at {constraint.value}",
                    )
                )
            elif constraint.kind == "window":
                if isinstance(constraint.value, list):
                    window_start, window_end = constraint.value
                else:
                    window_start = task.earliest_start_minute
                    window_end = task.latest_end_minute
                if (
                    task.start_minute < window_start
                    or task.start_minute + task.duration_minutes > window_end
                ):
                    violations.append(
                        ConstraintViolation(
                            constraint_id=constraint.constraint_id,
                            task_id=task.task_id,
                            message=(
                                "task is outside its movable window: "
                                f"[{window_start}, {window_end}]"
                            ),
                        )
                    )
            elif constraint.kind == "precedence":
                previous = by_id.get(constraint.related_task_id or "")
                if previous is None:
                    violations.append(
                        ConstraintViolation(
                            constraint_id=constraint.constraint_id,
                            task_id=task.task_id,
                            message=(
                                "precedence constraint references an unknown "
                                f"related task: {constraint.related_task_id}"
                            ),
                        )
                    )
                elif task.start_minute < (
                    previous.start_minute + previous.duration_minutes
                ):
                    violations.append(
                        ConstraintViolation(
                            constraint_id=constraint.constraint_id,
                            task_id=task.task_id,
                            message=f"task must follow {previous.task_id}",
                        )
                    )
            elif (
                constraint.kind == "duration"
                and task.duration_minutes != int(constraint.value)
            ):
                violations.append(
                    ConstraintViolation(
                        constraint_id=constraint.constraint_id,
                        task_id=task.task_id,
                        message=f"task duration must be {constraint.value} minutes",
                    )
                )
            elif (
                constraint.kind == "resource"
                and task.selected_device_id != str(constraint.value)
            ):
                violations.append(
                    ConstraintViolation(
                        constraint_id=constraint.constraint_id,
                        task_id=task.task_id,
                        message=f"task must use resource {constraint.value}",
                    )
                )
        return violations

    @classmethod
    def fits_selected_shifts(
        cls,
        start_minute: int,
        duration_minutes: int,
        shifts: list[str],
    ) -> bool:
        end_minute = start_minute + duration_minutes
        return any(
            start_minute >= window_start and end_minute <= window_end
            for shift in shifts
            for window_start, window_end in cls.shift_windows[shift]
        )

    @staticmethod
    def _tariff_at(minute: int, tariffs: list[TariffPeriod]) -> TariffPeriod:
        matches = [
            tariff
            for tariff in tariffs
            if tariff.start_minute <= minute < tariff.end_minute
        ]
        if len(matches) != 1:
            raise ValueError(
                f"minute {minute} must belong to exactly one tariff period"
            )
        return matches[0]
