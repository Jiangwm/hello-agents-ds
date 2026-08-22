from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .models import (
    ActionProposal,
    AgentMessage,
    ExportApprovalRequest,
    GateDecision,
    Metrics,
    ReplayRequest,
    ReviewRequest,
    Role,
    SimulationView,
    StateEvent,
    SuggestionRequest,
    WorldState,
)
from .scenario_loader import load_scenario


STRATEGY_VERSIONS = {
    "fixed_rule": "fixed-rule-v1",
    "single_agent": "single-agent-v1",
    "multi_agent": "multi-agent-v1",
}
ROLES: tuple[Role, ...] = (
    "equipment",
    "scheduler",
    "maintenance",
    "quality",
    "energy",
    "observer",
)
POLICY = {
    "version": "factory-safety-policy-v1",
    "hard_deny": ["production_write", "bypass_quality"],
    "human_review": [
        "adjust_speed",
        "schedule_maintenance",
        "isolate_batch",
        "reschedule_batch",
        "shift_energy_load",
    ],
    "safe": ["inspect_quality"],
    "observer": "read_only",
}
ROLE_ACTIONS = {
    "equipment": {"observe", "adjust_speed"},
    "scheduler": {"observe", "reschedule_batch"},
    "maintenance": {"observe", "schedule_maintenance"},
    "quality": {"observe", "inspect_quality", "isolate_batch"},
    "energy": {"observe", "shift_energy_load"},
    "observer": {"observe"},
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class StrategyAdapter:
    version: str

    def proposals(self, sandbox: "DigitalFactorySandbox") -> list[tuple[Role, str, dict[str, Any], list[str]]]:
        return []


class FixedRuleAdapter(StrategyAdapter):
    version = STRATEGY_VERSIONS["fixed_rule"]


class SingleAgentAdapter(StrategyAdapter):
    version = STRATEGY_VERSIONS["single_agent"]

    def proposals(self, sandbox: "DigitalFactorySandbox") -> list[tuple[Role, str, dict[str, Any], list[str]]]:
        return [("scheduler", "reschedule_batch", {"batch_id": sandbox.state.batches[0].batch_id}, ["queue_pressure"])]


class MultiAgentAdapter(StrategyAdapter):
    version = STRATEGY_VERSIONS["multi_agent"]

    def proposals(self, sandbox: "DigitalFactorySandbox") -> list[tuple[Role, str, dict[str, Any], list[str]]]:
        result: list[tuple[Role, str, dict[str, Any], list[str]]] = []
        gate = sandbox.state.quality_gates[0]
        if gate.status == "blocked":
            result.append(("quality", "inspect_quality", {"gate_id": gate.gate_id}, ["quality_signal"]))
        if sandbox.state.energy.tariff == "peak":
            result.append(("energy", "shift_energy_load", {"equipment_id": sandbox.state.equipment[-1].equipment_id}, ["tariff_signal"]))
        return result


ADAPTERS = {
    "fixed_rule": FixedRuleAdapter,
    "single_agent": SingleAgentAdapter,
    "multi_agent": MultiAgentAdapter,
}


class DigitalFactorySandbox:
    @classmethod
    def create(cls, scenario_id: str, seed: int, strategy: str) -> "DigitalFactorySandbox":
        return cls(scenario_id, seed, strategy)

    def __init__(self, scenario_id: str, seed: int, strategy: str):
        if strategy not in ADAPTERS:
            raise ValueError(f"unsupported strategy: {strategy}")
        scenario, digest, tool_call_id = load_scenario(scenario_id)
        self.scenario = scenario
        self.scenario_sha256 = digest
        self.scenario_tool_call_id = tool_call_id
        self.seed = seed
        self.strategy = strategy
        self.strategy_version = STRATEGY_VERSIONS[strategy]
        self.simulation_id = f"SIM-{content_hash([scenario_id, seed, self.strategy_version])[:16]}"
        self.state = scenario.world.model_copy(deep=True)
        self.status = "running"
        self.events: list[StateEvent] = []
        self.messages: dict[Role, list[AgentMessage]] = {role: [] for role in ROLES}
        self.proposals: dict[str, ActionProposal] = {}
        self.decisions: dict[str, GateDecision] = {}
        self.proposal_event_ids: dict[str, str] = {}
        self.decision_event_ids: dict[str, str] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.export_approval: dict[str, str] | None = None
        self.tool_calls = 1
        self._adapter = ADAPTERS[strategy]()
        initial = self.state.model_dump(mode="json")
        self._record_event(
            source="system",
            reason="scenario_initialized",
            event="initialize",
            state_diff={"world": {"before": None, "after": initial}},
            caused_by=[tool_call_id],
            decision_evidence=[digest],
        )

    @property
    def state_hash(self) -> str:
        return content_hash(self.state.model_dump(mode="json"))

    @property
    def trajectory_hash(self) -> str:
        return self.events[-1].event_hash if self.events else "0" * 64

    @property
    def policy_hash(self) -> str:
        return content_hash(POLICY)

    def _record_event(
        self,
        *,
        source: str,
        reason: str,
        event: str,
        state_diff: dict[str, dict[str, Any]],
        caused_by: list[str],
        decision_evidence: list[str],
    ) -> StateEvent:
        self.export_approval = None
        prev = self.events[-1].event_hash if self.events else "0" * 64
        number = len(self.events) + 1
        payload = {
            "event_id": f"EVT-{number:06d}",
            "simulation_id": self.simulation_id,
            "seed": self.seed,
            "strategy_version": self.strategy_version,
            "simulation_time": self.state.simulation_time,
            "source": source,
            "reason": reason,
            "event": event,
            "state_diff": state_diff,
            "decision_evidence": decision_evidence,
            "caused_by": caused_by,
            "prev_event_hash": prev,
            "state_hash": self.state_hash,
        }
        item = StateEvent(**payload, event_hash=content_hash(payload))
        self.events.append(item)
        return item

    def _message(self, role: Role, kind: str, content: dict[str, Any]) -> AgentMessage:
        message = AgentMessage(
            message_id=f"MSG-{role}-{len(self.messages[role]) + 1:06d}",
            simulation_time=self.state.simulation_time,
            role=role,
            kind=kind,
            content=copy.deepcopy(content),
        )
        self.messages[role].append(message)
        return message

    def _make_proposal(
        self,
        role: Role,
        action_type: str,
        parameters: dict[str, Any],
        reason_codes: list[str],
    ) -> ActionProposal:
        number = len(self.proposals) + 1
        evidence_event = next(item for item in reversed(self.events) if item.state_diff)
        base = {
            "proposal_id": f"PRP-{number:06d}",
            "role": role,
            "action_type": action_type,
            "parameters": copy.deepcopy(parameters),
            "reason_codes": reason_codes,
            "evidence_event_ids": [evidence_event.event_id],
        }
        proposal = ActionProposal(**base, proposal_hash=content_hash(base))
        self.proposals[proposal.proposal_id] = proposal
        self._message(role, "proposal", proposal.model_dump(mode="json"))
        audit_event = self._record_event(
            source=f"agent:{role}",
            reason="structured_action_proposal",
            event="action_proposal",
            state_diff={},
            caused_by=proposal.evidence_event_ids,
            decision_evidence=[proposal.proposal_hash],
        )
        self.proposal_event_ids[proposal.proposal_id] = audit_event.event_id
        return proposal

    def _proposal_error(self, proposal: ActionProposal) -> str | None:
        if proposal.action_type in POLICY["hard_deny"]:
            return "production_control_prohibited"
        if proposal.action_type not in ROLE_ACTIONS[proposal.role]:
            return "role_action_forbidden"
        if not proposal.evidence_event_ids or any(
            event_id not in {item.event_id for item in self.events}
            for event_id in proposal.evidence_event_ids
        ):
            return "evidence_event_invalid"
        parameters = proposal.parameters
        expected_keys = {
            "observe": set(),
            "adjust_speed": {"equipment_id", "speed"},
            "schedule_maintenance": {"equipment_id"},
            "isolate_batch": {"batch_id"},
            "inspect_quality": {"gate_id"},
            "reschedule_batch": {"batch_id"},
            "shift_energy_load": {"equipment_id"},
        }[proposal.action_type]
        if set(parameters) != expected_keys:
            return "parameters_schema_invalid"
        for key in expected_keys - {"speed"}:
            if not isinstance(parameters[key], str) or not parameters[key]:
                return "parameters_schema_invalid"
        if proposal.action_type == "adjust_speed":
            speed = parameters["speed"]
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0 <= speed <= 1.2:
                return "parameters_schema_invalid"
        if "equipment_id" in parameters and parameters["equipment_id"] not in {
            item.equipment_id for item in self.state.equipment
        }:
            return "target_not_found"
        if "batch_id" in parameters and parameters["batch_id"] not in {
            item.batch_id for item in self.state.batches
        }:
            return "target_not_found"
        if "gate_id" in parameters and parameters["gate_id"] not in {
            item.gate_id for item in self.state.quality_gates
        }:
            return "target_not_found"
        if proposal.action_type == "adjust_speed":
            equipment = next(
                item
                for item in self.state.equipment
                if item.equipment_id == parameters["equipment_id"]
            )
            if equipment.status in {"failed", "maintenance"}:
                return "equipment_unavailable"
        if proposal.action_type == "schedule_maintenance" and not any(
            team.maintenance_capacity > 0 for team in self.state.shift_teams
        ):
            return "maintenance_capacity_unavailable"
        if proposal.action_type == "isolate_batch":
            batch = next(
                item
                for item in self.state.batches
                if item.batch_id == parameters["batch_id"]
            )
            if batch.isolated:
                return "batch_already_isolated"
        if proposal.action_type == "reschedule_batch":
            batch = next(
                item
                for item in self.state.batches
                if item.batch_id == parameters["batch_id"]
            )
            if batch.isolated:
                return "isolated_batch_cannot_be_rescheduled"
        if (
            proposal.action_type == "shift_energy_load"
            and self.state.energy.tariff != "peak"
        ):
            return "peak_tariff_not_active"
        return None

    def _gate(self, proposal: ActionProposal) -> GateDecision:
        validation_error = self._proposal_error(proposal)
        if validation_error:
            verdict, reasons, hard = "deny", [validation_error], True
        elif proposal.action_type in POLICY["human_review"]:
            verdict, reasons, hard = "hold", ["human_approval_required"], False
        elif proposal.action_type in POLICY["safe"] or proposal.action_type == "observe":
            verdict, reasons, hard = "allow", ["deterministic_safe_action"], False
        else:
            verdict, reasons, hard = "deny", ["action_not_whitelisted"], True
        decision = GateDecision(
            decision_id=f"DEC-{len(self.decisions) + 1:06d}",
            proposal_id=proposal.proposal_id,
            verdict=verdict,
            reason_codes=reasons,
            hard_rejection=hard,
            proposal_hash=proposal.proposal_hash,
            state_hash=self.state_hash,
            policy_hash=self.policy_hash,
        )
        self.decisions[decision.decision_id] = decision
        self._message(proposal.role, "decision", decision.model_dump(mode="json"))
        audit_event = self._record_event(
            source="deterministic_gate",
            reason=reasons[0],
            event="gate_decision",
            state_diff={},
            caused_by=[self.proposal_event_ids[proposal.proposal_id]],
            decision_evidence=[proposal.proposal_hash, decision.decision_id, self.policy_hash],
        )
        self.decision_event_ids[decision.decision_id] = audit_event.event_id
        return decision

    def _apply(self, proposal: ActionProposal, decision: GateDecision) -> StateEvent | None:
        if decision.verdict != "allow":
            return None
        before = self.state.model_dump(mode="json")
        if proposal.action_type == "inspect_quality":
            gate_id = str(proposal.parameters.get("gate_id", ""))
            gate = next((item for item in self.state.quality_gates if item.gate_id == gate_id), None)
            if gate is None:
                return None
            gate.status = "inspection"
        elif proposal.action_type == "isolate_batch":
            batch_id = str(proposal.parameters.get("batch_id", ""))
            batch = next((item for item in self.state.batches if item.batch_id == batch_id), None)
            if batch is None:
                return None
            batch.isolated = True
            for gate in self.state.quality_gates:
                if batch_id not in gate.isolated_batch_ids:
                    gate.isolated_batch_ids.append(batch_id)
                    gate.status = "blocked"
            for item in self.state.wip_items:
                if item.batch_id == batch_id:
                    item.status = "isolated"
        elif proposal.action_type == "adjust_speed":
            equipment_id = str(proposal.parameters.get("equipment_id", ""))
            equipment = next((item for item in self.state.equipment if item.equipment_id == equipment_id), None)
            if equipment is None:
                return None
            speed = float(proposal.parameters.get("speed", equipment.speed))
            if not 0 <= speed <= 1.2:
                return None
            equipment.speed = speed
            equipment.status = "slowed" if speed < 1 else "running"
        elif proposal.action_type == "schedule_maintenance":
            equipment_id = str(proposal.parameters.get("equipment_id", ""))
            equipment = next((item for item in self.state.equipment if item.equipment_id == equipment_id), None)
            if equipment is None:
                return None
            equipment.status = "maintenance"
        elif proposal.action_type == "reschedule_batch":
            batch_id = str(proposal.parameters.get("batch_id", ""))
            batch = next((item for item in self.state.batches if item.batch_id == batch_id), None)
            if batch is None:
                return None
            batch.urgent = True
            batch.due_tick = max(self.state.simulation_time + 1, batch.due_tick - 1)
        elif proposal.action_type == "shift_energy_load":
            equipment_id = str(proposal.parameters.get("equipment_id", ""))
            equipment = next((item for item in self.state.equipment if item.equipment_id == equipment_id), None)
            if equipment is None:
                return None
            equipment.speed = min(equipment.speed, 0.8)
            equipment.status = "slowed"
        else:
            return None
        after = self.state.model_dump(mode="json")
        if before == after:
            return None
        return self._record_event(
            source=f"agent:{proposal.role}",
            reason="gate_allowed",
            event=proposal.action_type,
            state_diff={"world": {"before": before, "after": after}},
            caused_by=[self.decision_event_ids[decision.decision_id]],
            decision_evidence=[
                proposal.proposal_hash,
                decision.decision_id,
                self.policy_hash,
                *([decision.review_hash] if decision.review_hash else []),
            ],
        )

    def _step_once(self) -> StateEvent:
        if self.status != "running":
            raise ValueError("simulation is paused")
        before = self.state.model_dump(mode="json")
        self.state.simulation_time += 1
        tick = self.state.simulation_time
        equipment = self.state.equipment[0]
        buffer = self.state.buffers[0]
        quality = self.state.quality_gates[0]
        energy = self.state.energy
        seed_variation = int(content_hash([self.seed, tick, self.scenario.scenario_id])[:8], 16) % 2

        reason = "normal_flow"
        if self.scenario.scenario_id == "upstream_slowdown":
            reason = "upstream_speed_loss"
            equipment.speed = 0.55
            equipment.status = "slowed"
        elif self.scenario.scenario_id == "quality_isolation":
            reason = "quality_signal"
            if tick == 1:
                quality.defects += 2
                quality.status = "blocked"
                buffer.blocked_reason = "quality_isolation"
                for batch in self.state.batches:
                    batch.isolated = True
                    if batch.batch_id not in quality.isolated_batch_ids:
                        quality.isolated_batch_ids.append(batch.batch_id)
                for item in self.state.wip_items:
                    item.status = "isolated"
            elif quality.status == "inspection":
                reason = "deterministic_reinspection_release"
                quality.status = "open"
                quality.isolated_batch_ids.clear()
                buffer.blocked_reason = None
                for batch in self.state.batches:
                    batch.isolated = False
                for item in self.state.wip_items:
                    item.status = "queued"
            elif quality.status == "blocked":
                reason = "quality_delivery_pressure"
        else:
            reason = "peak_tariff_urgent_order"
            energy.tariff = "peak"

        upstream_output = max(0, int(self.state.equipment[0].speed * 2))
        buffer.wip = min(buffer.capacity, buffer.wip + upstream_output)
        quality_blocked = any(batch.isolated for batch in self.state.batches)
        downstream_capacity = max(
            0,
            int(self.state.equipment[-1].speed * 2) + seed_variation,
        )
        produced = 0 if quality_blocked else min(buffer.wip, downstream_capacity)
        buffer.wip -= produced
        if self.scenario.scenario_id == "upstream_slowdown":
            buffer.blocked_reason = "upstream_starvation" if buffer.wip == 0 else None
        if buffer.wip > 0 or quality_blocked:
            buffer.wait_minutes += 1
        self.state.throughput += produced
        energy.current_kw = round(
            sum(
                item.power_kw * item.speed
                for item in self.state.equipment
                if item.status not in {"failed", "maintenance"}
            ),
            3,
        )
        added_kwh = energy.current_kw / 60.0
        energy.cumulative_kwh = round(energy.cumulative_kwh + added_kwh, 6)
        energy.cumulative_cost = round(energy.cumulative_cost + added_kwh * energy.price_per_kwh, 6)
        for item in self.state.equipment:
            if item.status in {"failed", "maintenance"}:
                item.downtime_minutes += 1
        after = self.state.model_dump(mode="json")
        previous_state_event = next(
            item for item in reversed(self.events) if item.state_diff
        )
        event = self._record_event(
            source="discrete_event_engine",
            reason=reason,
            event="simulation_tick",
            state_diff={"world": {"before": before, "after": after}},
            caused_by=[previous_state_event.event_id],
            decision_evidence=[self.scenario_sha256, self.strategy_version],
        )
        observation = {"event_id": event.event_id, "state_hash": event.state_hash, "tick": tick}
        for role in ROLES:
            self._message(role, "observation", observation)
        for role, action, parameters, reasons in self._adapter.proposals(self):
            proposal = self._make_proposal(role, action, parameters, reasons)
            decision = self._gate(proposal)
            self.tool_calls += 1
            self._apply(proposal, decision)
        return event

    def execute(self, command: str, payload: Any | None = None) -> Any:
        if command == "step":
            steps = int(payload or 1)
            return [self._step_once() for _ in range(steps)]
        if command == "pause":
            if self.status != "paused":
                before = self.status
                self.status = "paused"
                self._record_event(source="operator", reason="explicit_pause", event="pause", state_diff={"status": {"before": before, "after": self.status}}, caused_by=[self.events[-1].event_id], decision_evidence=[])
            return self.inspect()
        if command == "resume":
            if self.status != "running":
                before = self.status
                self.status = "running"
                self._record_event(source="operator", reason="explicit_resume", event="resume", state_diff={"status": {"before": before, "after": self.status}}, caused_by=[self.events[-1].event_id], decision_evidence=[])
            return self.inspect()
        if command == "snapshot":
            body = {
                "simulation_id": self.simulation_id,
                "tick": self.state.simulation_time,
                "state": self.state.model_dump(mode="json"),
                "state_hash": self.state_hash,
                "trajectory_hash": self.trajectory_hash,
                "event_count": len(self.events),
            }
            snapshot_id = f"SNP-{content_hash(body)[:16]}"
            self.snapshots[snapshot_id] = copy.deepcopy(body)
            return {"snapshot_id": snapshot_id, "snapshot_hash": content_hash(body), **body}
        raise ValueError(f"unsupported command: {command}")

    def inspect(self) -> SimulationView:
        return SimulationView(
            simulation_id=self.simulation_id,
            scenario_id=self.scenario.scenario_id,
            scenario_sha256=self.scenario_sha256,
            scenario_tool_call_id=self.scenario_tool_call_id,
            data_version=self.scenario.data_version,
            seed=self.seed,
            strategy=self.strategy,
            strategy_version=self.strategy_version,
            status=self.status,
            state=self.state,
            state_hash=self.state_hash,
            trajectory_hash=self.trajectory_hash,
            agents=self.messages,
            pending_decisions=[item for item in self.decisions.values() if item.verdict == "hold" and not item.reviewed],
            export_approval=self.export_approval,
        )

    def metrics(self) -> Metrics:
        proposal_count = len(self.proposals)
        explained = sum(
            1
            for proposal in self.proposals.values()
            if proposal.reason_codes and proposal.evidence_event_ids
        )
        return Metrics(
            throughput=self.state.throughput,
            wip=sum(item.wip for item in self.state.buffers),
            downtime=sum(item.downtime_minutes for item in self.state.equipment),
            defects=sum(item.defects for item in self.state.quality_gates),
            energy_kwh=self.state.energy.cumulative_kwh,
            energy_cost=self.state.energy.cumulative_cost,
            tool_calls=self.tool_calls,
            explanation_quality=round(explained / proposal_count, 4) if proposal_count else 0.0,
            explanation_cost=round(proposal_count * 0.01, 4),
            pending_decisions=sum(
                1
                for decision in self.decisions.values()
                if decision.verdict == "hold" and not decision.reviewed
            ),
        )

    def suggest(self, request: SuggestionRequest) -> dict[str, Any]:
        proposal = self._make_proposal(request.role, request.action_type, request.parameters, request.reason_codes)
        decision = self._gate(proposal)
        self.tool_calls += 1
        event = self._apply(proposal, decision)
        return {"proposal": proposal, "decision": decision, "event": event, "narrative_ignored": request.narrative is not None}

    def review(self, decision_id: str, request: ReviewRequest) -> GateDecision:
        decision = self.decisions.get(decision_id)
        if decision is None:
            raise KeyError("decision not found")
        if decision.hard_rejection or decision.verdict == "deny":
            raise PermissionError("hard rejection cannot be overridden")
        if decision.verdict != "hold" or decision.reviewed:
            raise ValueError("decision is not pending review")
        if (
            request.proposal_hash != decision.proposal_hash
            or request.state_hash != decision.state_hash
            or request.policy_hash != decision.policy_hash
            or self.state_hash != decision.state_hash
        ):
            raise PermissionError("review binding mismatch or stale state")
        decision.reviewed = True
        decision.reviewer = request.reviewer
        decision.review_reason = request.reason
        decision.review_hash = content_hash(request.model_dump(mode="json"))
        if request.approve:
            decision.verdict = "allow"
        else:
            decision.verdict = "deny"
        review_event = self._record_event(
            source=f"human:{request.reviewer}",
            reason=request.reason,
            event="human_review_approved" if request.approve else "human_review_rejected",
            state_diff={},
            caused_by=[self.decision_event_ids[decision.decision_id]],
            decision_evidence=[
                decision.proposal_hash,
                decision.decision_id,
                decision.policy_hash,
                decision.review_hash,
            ],
        )
        self.decision_event_ids[decision.decision_id] = review_event.event_id
        if request.approve:
            self._apply(self.proposals[decision.proposal_id], decision)
        return decision

    def approve_export(self, request: ExportApprovalRequest) -> dict[str, str]:
        evidence_manifest_hash = content_hash(
            {
                "scenario_sha256": self.scenario_sha256,
                "data_version": self.scenario.data_version,
                "event_hashes": [item.event_hash for item in self.events],
                "state_hash": self.state_hash,
            }
        )
        body = {
            "simulation_id": self.simulation_id,
            "approver": request.approver,
            "reason": request.reason,
            "state_hash": self.state_hash,
            "trajectory_hash": self.trajectory_hash,
            "policy_hash": self.policy_hash,
            "evidence_manifest_hash": evidence_manifest_hash,
        }
        self.export_approval = {**body, "approval_hash": content_hash(body)}
        return self.export_approval

    def export(self) -> dict[str, Any]:
        approval = self.export_approval
        current_manifest_hash = content_hash(
            {
                "scenario_sha256": self.scenario_sha256,
                "data_version": self.scenario.data_version,
                "event_hashes": [item.event_hash for item in self.events],
                "state_hash": self.state_hash,
            }
        )
        if (
            approval is None
            or approval["state_hash"] != self.state_hash
            or approval["trajectory_hash"] != self.trajectory_hash
            or approval["policy_hash"] != self.policy_hash
            or approval["evidence_manifest_hash"] != current_manifest_hash
        ):
            raise PermissionError("explicit current-state export approval is required")
        return {
            "simulation": self.inspect().model_dump(mode="json"),
            "events": [item.model_dump(mode="json") for item in self.events],
            "metrics": self.metrics().model_dump(mode="json"),
            "approval": approval,
            "audit": {
                "simulation_id": self.simulation_id,
                "seed": self.seed,
                "strategy_version": self.strategy_version,
                "event": self.trajectory_hash,
                "state_diff": self.events[-1].state_diff,
                "decision_evidence": self.events[-1].decision_evidence,
            },
            "read_only": True,
            "production_control": "prohibited",
        }

    def replay(self, request: ReplayRequest | None = None) -> dict[str, Any]:
        snapshot = None
        if request and request.snapshot_id:
            snapshot = self.snapshots.get(request.snapshot_id)
            if snapshot is None:
                raise KeyError("snapshot not found")
        event_count = int(snapshot["event_count"] if snapshot else len(self.events))
        expected_state = snapshot["state_hash"] if snapshot else self.state_hash
        expected_trajectory = snapshot["trajectory_hash"] if snapshot else self.trajectory_hash
        reconstructed: dict[str, Any] | None = None
        reconstructed_status = "running"
        previous_hash = "0" * 64
        actual_trajectory = previous_hash
        valid = True
        for item in self.events[:event_count]:
            payload = item.model_dump(mode="json")
            claimed_hash = payload.pop("event_hash")
            if item.prev_event_hash != previous_hash or content_hash(payload) != claimed_hash:
                valid = False
                break
            if "world" in item.state_diff:
                world_diff = item.state_diff["world"]
                if world_diff.get("before") != reconstructed:
                    valid = False
                    break
                try:
                    reconstructed = WorldState.model_validate(world_diff.get("after")).model_dump(mode="json")
                except Exception:
                    valid = False
                    break
            if "status" in item.state_diff:
                status_diff = item.state_diff["status"]
                if status_diff.get("before") != reconstructed_status:
                    valid = False
                    break
                reconstructed_status = str(status_diff.get("after"))
            if reconstructed is None or content_hash(reconstructed) != item.state_hash:
                valid = False
                break
            previous_hash = claimed_hash
            actual_trajectory = claimed_hash
        actual_state_hash = content_hash(reconstructed) if reconstructed is not None else ""
        valid = (
            valid
            and event_count > 0
            and actual_state_hash == expected_state
            and actual_trajectory == expected_trajectory
        )
        return {
            "isolated": True,
            "valid": valid,
            "expected_state_hash": expected_state,
            "actual_state_hash": actual_state_hash,
            "expected_trajectory_hash": expected_trajectory,
            "actual_trajectory_hash": actual_trajectory,
            "actual_status": reconstructed_status,
            "read_only": True,
            "production_control": "prohibited",
        }

    def event_chain(self, event_id: str) -> list[StateEvent]:
        by_id = {item.event_id: item for item in self.events}
        if event_id not in by_id:
            raise KeyError("event not found")
        required: set[str] = set()

        def collect(current_id: str) -> None:
            if current_id in required:
                return
            required.add(current_id)
            for parent_id in by_id[current_id].caused_by:
                if parent_id.startswith("EVT-") and parent_id in by_id:
                    collect(parent_id)

        collect(event_id)
        return [item for item in self.events if item.event_id in required]
