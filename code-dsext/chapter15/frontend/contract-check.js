"use strict";

const assert = require("node:assert/strict");
require("./app.js");

const contract = globalThis.__factorySandboxTest;
assert.ok(contract, "应暴露纯归一化辅助函数");

const fixture = {
  simulation_id: "SIM 15/演示",
  status: "paused",
  read_only: true,
  production_control: "prohibited",
  pending_decisions: [{ decision_id: "DEC-1" }],
  state: {
    simulation_time: 12,
    equipment: [{ equipment_id: "EQ-01", status: "running", speed: 1, health: 0.9, power_kw: 12, downtime_minutes: 0 }],
    buffers: [{ buffer_id: "BUF-01", wip: 3, capacity: 10, wait_minutes: 2, blocked_reason: null }],
    batches: [{ batch_id: "B-01", quantity: 20, urgent: false, due_tick: 18, isolated: false }],
    quality_gates: [{ gate_id: "QG-01", status: "inspection", defects: 1, isolated_batch_ids: ["B-02"] }],
    energy: { tariff: "peak", price_per_kwh: 1.2, current_kw: 12, cumulative_kwh: 36, cumulative_cost: 43.2 }
  },
  agents: { scheduler: [{ message_id: "MSG-1", simulation_time: 12, role: "scheduler", kind: "proposal", content: { action: "reschedule" } }] }
};

const view = contract.normalizeSimulation(fixture);
assert.equal(view.simulationId, "SIM 15/演示");
assert.equal(view.simulationTime, 12);
assert.equal(view.equipment[0].equipment_id, "EQ-01");
assert.equal(view.buffers[0].buffer_id, "BUF-01");
assert.equal(view.qualityGates[0].gate_id, "QG-01");
assert.equal(view.energy.cumulative_kwh, 36);
assert.equal(view.agentMessages[0].role, "scheduler");
assert.equal(view.pendingDecisions.length, 1);

const paths = contract.simulationApiPaths(fixture.simulation_id);
assert.equal(paths.events, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/events");
assert.equal(paths.metrics, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/metrics");
assert.equal(paths.pause, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/pause");
assert.equal(paths.resume, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/resume");
assert.equal(paths.snapshot, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/snapshot");
assert.equal(paths.replay, "/api/simulations/SIM%2015%2F%E6%BC%94%E7%A4%BA/replay");
assert.deepEqual(contract.lifecycleRequestOptions("pause"), { method: "POST" });
assert.deepEqual(contract.lifecycleRequestOptions("snapshot"), { method: "POST" });
assert.deepEqual(contract.lifecycleRequestOptions("replay"), { method: "POST", body: "{\"snapshot_id\":null}" });
assert.equal(contract.lifecycleStatusDetail("snapshot", "已创建快照", { snapshot_id: "SNP-demo" }), "已创建快照：SNP-demo");
assert.equal(contract.lifecycleStatusDetail("replay", "已请求回放", { valid: true }), "已请求回放：校验通过");
assert.equal(contract.lifecycleStatusDetail("replay", "已请求回放", { valid: false }), "已请求回放：校验失败");

console.log("FACTORY_SANDBOX_CONTRACT_OK");
