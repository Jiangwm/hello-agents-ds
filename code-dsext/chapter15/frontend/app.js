(() => {
  "use strict";

  const API = "";
  const KPIDefinitions = [
    ["throughput", "吞吐"], ["wip", "WIP"], ["downtime", "停机"],
    ["defects", "缺陷"], ["energy_kwh", "能耗"], ["energy_cost", "电费"],
    ["tool_calls", "工具调用"], ["explanation_quality", "解释质量"]
  ];
  const ComparisonMetricDefinitions = [
    ["throughput", "吞吐"], ["wip", "WIP"], ["downtime", "停机"],
    ["defects", "缺陷"], ["energy_kwh", "能耗（kWh）"], ["energy_cost", "电费"],
    ["tool_calls", "工具调用"], ["explanation_quality", "解释质量"],
    ["explanation_cost", "解释成本"], ["pending_decisions", "待审批决策"]
  ];
  const state = { simulationId: null, scenario: null, simulation: null, events: [], metrics: null };
  if (typeof document === "undefined") {
    globalThis.__factorySandboxTest = { normalizeSimulation, flattenAgents, simulationApiPaths, lifecycleRequestOptions, lifecycleStatusDetail };
    return;
  }
  const element = (id) => document.getElementById(id);
  const ui = {
    scenario: element("scenario-select"), seed: element("seed-input"), status: element("simulation-status"),
    error: element("error-message"), dot: element("connection-dot"), connection: element("connection-text"),
    topology: element("topology-view"), buffers: element("buffers-view"), summary: element("summary-view"),
    kpis: element("kpi-view"), messages: element("messages-view"), events: element("events-view"),
    tick: element("current-tick").querySelector("strong"), comparisonHead: element("comparison-head"),
    comparisonBody: element("comparison-body")
  };
  const actionButtons = ["step-button", "five-steps-button", "pause-button", "resume-button", "snapshot-button", "replay-button"]
    .map(element);

  function asObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function asArray(value) { return Array.isArray(value) ? value : []; }
  function firstDefined(...values) { return values.find((value) => value !== undefined && value !== null); }
  function text(value) {
    if (value === undefined || value === null || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  function keyLabel(key) { return String(key || "未命名").replace(/_/g, " "); }
  function clear(node) { node.replaceChildren(); }
  function make(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }
  function readPath(source, names) {
    for (const name of names) {
      if (source && source[name] !== undefined && source[name] !== null) return source[name];
    }
    return undefined;
  }
  function normalized(payload) {
    const root = asObject(payload);
    const simulation = asObject(firstDefined(root.simulation, root.result, root));
    const world = asObject(firstDefined(simulation.world, simulation.state, root.world, root.state, simulation));
    return { root, simulation, world };
  }
  function flattenAgents(agents) {
    if (Array.isArray(agents)) return agents.map(asObject);
    return Object.entries(asObject(agents)).flatMap(([role, messages]) => asArray(messages).map((message) => ({ ...asObject(message), role: firstDefined(asObject(message).role, role) })));
  }
  function normalizeSimulation(payload) {
    const data = normalized(payload);
    const view = data.simulation;
    return {
      data,
      simulationId: firstDefined(view.simulation_id, view.id, data.root.simulation_id, data.root.id),
      simulationTime: firstDefined(data.world.simulation_time, data.world.tick, data.world.time),
      status: firstDefined(view.status, data.root.status),
      readOnly: firstDefined(view.read_only, data.root.read_only),
      productionControl: firstDefined(view.production_control, data.root.production_control),
      pendingDecisions: asArray(firstDefined(view.pending_decisions, data.root.pending_decisions)),
      equipment: asArray(firstDefined(data.world.equipment, data.world.machines, data.world.devices)),
      buffers: asArray(firstDefined(data.world.buffers, data.world.buffer_wip)),
      batches: asArray(data.world.batches),
      qualityGates: asArray(firstDefined(data.world.quality_gates, data.world.quality)),
      energy: asObject(data.world.energy),
      agentMessages: flattenAgents(firstDefined(view.agents, data.root.agents, view.messages, data.root.messages)),
      metrics: asObject(firstDefined(view.metrics, data.root.metrics, data.world.metrics))
    };
  }
  function simulationApiPaths(simulationId) {
    const id = encodeURIComponent(simulationId);
    return {
      events: `/api/simulations/${id}/events`,
      metrics: `/api/simulations/${id}/metrics`,
      pause: `/api/simulations/${id}/pause`,
      resume: `/api/simulations/${id}/resume`,
      snapshot: `/api/simulations/${id}/snapshot`,
      replay: `/api/simulations/${id}/replay`
    };
  }
  function lifecycleRequestOptions(action) {
    return action === "replay"
      ? { method: "POST", body: JSON.stringify({ snapshot_id: null }) }
      : { method: "POST" };
  }
  function lifecycleStatusDetail(action, label, payload) {
    const result = asObject(payload);
    if (action === "snapshot" && result.snapshot_id) return `${label}：${result.snapshot_id}`;
    if (action === "replay" && typeof result.valid === "boolean") return `${label}：${result.valid ? "校验通过" : "校验失败"}`;
    return label;
  }
  globalThis.__factorySandboxTest = { normalizeSimulation, flattenAgents, simulationApiPaths, lifecycleRequestOptions, lifecycleStatusDetail };
  function responseLooksLikeSimulation(payload) {
    const data = normalized(payload);
    return Boolean(readPath(data.simulation, ["id", "simulation_id"]) || readPath(data.world, ["machines", "equipment", "devices", "topology", "metrics"]));
  }
  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
      ...options
    });
    const raw = await response.text();
    let body = null;
    try { body = raw ? JSON.parse(raw) : {}; } catch { body = { message: raw }; }
    if (!response.ok) throw new Error(readPath(asObject(body), ["detail", "message", "error"]) || `请求失败（HTTP ${response.status}）`);
    return body;
  }
  function showError(error) {
    ui.error.hidden = false;
    ui.error.textContent = `操作未完成：${error instanceof Error ? error.message : text(error)}`;
  }
  function clearError() { ui.error.hidden = true; ui.error.textContent = ""; }
  function setBusy(busy) { document.querySelectorAll("button").forEach((button) => { button.disabled = busy || (actionButtons.includes(button) && !state.simulationId); }); }
  function currentStrategy() { return document.querySelector('input[name="strategy"]:checked').value; }
  function currentSeed() { return Number(ui.seed.value); }
  function selectedScenarioId() { return ui.scenario.value; }
  function dataCollection(data, names) { return asArray(firstDefined(...names.map((name) => data.world[name]), ...names.map((name) => data.simulation[name]), ...names.map((name) => data.root[name]))); }
  function dataObject(data, names) { return asObject(firstDefined(...names.map((name) => data.world[name]), ...names.map((name) => data.simulation[name]), ...names.map((name) => data.root[name]))); }

  function renderSimulation(payload) {
    if (!payload) return;
    state.simulation = payload;
    const view = normalizeSimulation(payload);
    const data = view.data;
    state.simulationId = firstDefined(view.simulationId, state.simulationId);
    ui.tick.textContent = text(view.simulationTime);
    ui.status.textContent = state.simulationId
      ? `仿真 ${state.simulationId}：${text(view.status)} · 只读：${text(view.readOnly)} · 生产控制：${text(view.productionControl)} · 待审批：${view.pendingDecisions.length}`
      : "后端已返回仿真状态";
    renderTopology(view);
    renderBuffers(view);
    renderSummary(view);
    renderKpis(view.metrics);
    renderMessages(view.agentMessages);
    const inlineEvents = dataCollection(data, ["events", "timeline", "event_log"]);
    if (inlineEvents.length) renderEvents(inlineEvents);
    setBusy(false);
  }
  function renderTopology(view) {
    const entries = view.equipment;
    clear(ui.topology);
    if (!entries.length) { ui.topology.classList.add("empty-state"); ui.topology.textContent = "后端尚未返回产线拓扑或设备状态。"; return; }
    ui.topology.classList.remove("empty-state");
    entries.forEach((item, index) => {
      const machine = asObject(item);
      const card = make("article", "machine-card");
      const status = firstDefined(readPath(machine, ["status", "state", "condition"]), "unknown");
      card.dataset.status = String(status).toLowerCase();
      card.append(make("h3", "", text(readPath(machine, ["equipment_id", "name", "machine_name", "id", "station_id"]) || `设备 ${index + 1}`)));
      card.append(make("p", "", `状态：${text(status)}`));
      const detail = readPath(machine, ["detail", "message", "type"]);
      if (detail !== undefined) card.append(make("p", "", text(detail)));
      ui.topology.append(card);
    });
  }
  function renderBuffers(view) {
    const buffers = view.buffers;
    clear(ui.buffers);
    if (!buffers.length) { ui.buffers.classList.add("empty-state"); ui.buffers.textContent = "后端尚未返回缓冲区 WIP 数据。"; return; }
    ui.buffers.classList.remove("empty-state");
    buffers.forEach((item, index) => {
      const buffer = asObject(item); const card = make("div", "data-card");
      card.append(make("span", "", text(readPath(buffer, ["buffer_id", "name", "buffer_name", "id"]) || `缓冲区 ${index + 1}`)));
      card.append(make("strong", "", `WIP：${text(readPath(buffer, ["wip", "count", "level", "value"]))}`));
      ui.buffers.append(card);
    });
  }
  function renderSummary(view) {
    const summaries = [
      ...view.batches.map((batch) => [
        `批次 ${text(batch.batch_id)}`,
        `数量：${text(batch.quantity)} · 紧急：${text(batch.urgent)} · 隔离：${text(batch.isolated)}`
      ]),
      ...view.qualityGates.map((gate) => [
        `质量关卡 ${text(gate.gate_id)}`,
        `状态：${text(gate.status)} · 缺陷：${text(gate.defects)} · 隔离批次：${text(gate.isolated_batch_ids)}`
      ]),
      ["能源", `电价时段：${text(view.energy.tariff)} · 当前功率：${text(view.energy.current_kw)} · 累计能耗：${text(view.energy.cumulative_kwh)} · 累计电费：${text(view.energy.cumulative_cost)}`]
    ].filter(([, value]) => value !== undefined && value !== null);
    clear(ui.summary);
    if (!view.batches.length && !view.qualityGates.length && !Object.keys(view.energy).length) { ui.summary.classList.add("empty-state"); ui.summary.textContent = "后端尚未返回批次、质量或能源摘要。"; return; }
    ui.summary.classList.remove("empty-state");
    summaries.forEach(([label, value]) => { const card = make("div", "data-card"); card.append(make("span", "", label)); card.append(make("strong", "", text(value))); ui.summary.append(card); });
  }
  function renderKpis(metrics) {
    clear(ui.kpis);
    KPIDefinitions.forEach(([key, label]) => {
      const metric = firstDefined(metrics[key], metrics[`${key}_value`]);
      const box = make("div"); box.append(make("dt", "", label)); box.append(make("dd", "", text(metric))); ui.kpis.append(box);
    });
  }
  function renderMessages(messages) {
    clear(ui.messages);
    if (!messages.length) { ui.messages.classList.add("empty-state"); ui.messages.append(make("li", "", "暂无角色消息。")); return; }
    ui.messages.classList.remove("empty-state");
    messages.forEach((item) => {
      const message = asObject(item); const li = make("li", "message-item");
      const meta = make("div", "message-meta", `${text(readPath(message, ["role", "agent", "sender", "name"]))} · ${text(readPath(message, ["simulation_time", "timestamp", "time", "tick"]))}`);
      li.append(meta); li.append(make("p", "", text(readPath(message, ["content", "message", "text", "detail"])))); ui.messages.append(li);
    });
  }
  function renderEvents(events) {
    state.events = events; clear(ui.events);
    if (!events.length) { ui.events.classList.add("empty-state"); ui.events.append(make("li", "", "暂无事件记录。")); return; }
    ui.events.classList.remove("empty-state");
    events.forEach((item) => {
      const event = asObject(item); const li = make("li", "timeline-item");
      li.append(make("div", "event-meta", `${text(readPath(event, ["simulation_time", "timestamp", "time", "tick", "sequence"]))} · ${text(readPath(event, ["source", "type", "event_type", "actor"]))}`));
      li.append(make("p", "", `事件：${text(event.event)} · 原因：${text(event.reason)}`));
      if (event.state_diff !== undefined) li.append(make("p", "", `状态变更：${text(event.state_diff)}`));
      ui.events.append(li);
    });
  }
  function comparisonRows(payload) {
    const root = asObject(payload);
    return asArray(firstDefined(root.comparisons, root.results, root.rows, root.data));
  }
  function renderComparison(payload) {
    const rows = comparisonRows(payload); clear(ui.comparisonHead); clear(ui.comparisonBody);
    if (!rows.length) { ui.comparisonHead.append(make("th", "", "策略")); const row = make("tr"); row.append(make("td", "", "后端未返回比较结果。")); ui.comparisonBody.append(row); return; }
    const visibleMetrics = ComparisonMetricDefinitions.filter(([key]) => rows.some((row) => {
      const item = asObject(row); const metrics = asObject(item.metrics);
      return firstDefined(metrics[key], item[key]) !== undefined;
    }));
    ui.comparisonHead.append(make("th", "", "策略"));
    visibleMetrics.forEach(([, label]) => ui.comparisonHead.append(make("th", "", label)));
    rows.forEach((raw) => {
      const row = asObject(raw); const metrics = asObject(row.metrics); const tr = make("tr");
      const strategy = make("td", "", text(firstDefined(row.strategy, row.strategy_id, row.name))); strategy.scope = "row"; tr.append(strategy);
      visibleMetrics.forEach(([key]) => tr.append(make("td", "", text(firstDefined(metrics[key], row[key])))));
      ui.comparisonBody.append(tr);
    });
  }
  async function refreshSupplementary() {
    if (!state.simulationId) return;
    const paths = simulationApiPaths(state.simulationId);
    const [events, metrics] = await Promise.allSettled([request(paths.events), request(paths.metrics)]);
    if (events.status === "fulfilled") renderEvents(asArray(firstDefined(events.value.events, events.value.timeline, events.value)));
    if (metrics.status === "fulfilled") { state.metrics = metrics.value; renderKpis(asObject(firstDefined(metrics.value.metrics, metrics.value.kpis, metrics.value))); }
  }
  async function refreshSimulation() {
    if (!state.simulationId) return;
    const payload = await request(`/api/simulations/${encodeURIComponent(state.simulationId)}`);
    renderSimulation(payload);
  }
  async function runAction(action) {
    clearError(); setBusy(true);
    try { await action(); } catch (error) { showError(error); setBusy(false); }
  }
  async function initialize() {
    await runAction(async () => {
      const scenarioId = selectedScenarioId();
      if (!scenarioId) throw new Error("请先选择教学场景。");
      if (!Number.isFinite(currentSeed())) throw new Error("随机种子必须是有效数字。");
      const payload = await request("/api/simulations", { method: "POST", body: JSON.stringify({ scenario_id: scenarioId, seed: currentSeed(), strategy: currentStrategy() }) });
      const data = normalized(payload);
      state.simulationId = firstDefined(readPath(data.simulation, ["id", "simulation_id"]), readPath(data.root, ["id", "simulation_id"]));
      if (!state.simulationId) throw new Error("后端未返回仿真标识，无法继续执行。");
      renderSimulation(payload); await refreshSimulation(); await refreshSupplementary();
    });
  }
  async function step(steps) {
    await runAction(async () => {
      if (!state.simulationId) throw new Error("请先初始化仿真。");
      const payload = await request(`/api/simulations/${encodeURIComponent(state.simulationId)}/step`, { method: "POST", body: JSON.stringify({ steps }) });
      if (responseLooksLikeSimulation(payload)) renderSimulation(payload);
      await refreshSimulation(); await refreshSupplementary();
    });
  }
  async function lifecycle(action, label) {
    await runAction(async () => {
      if (!state.simulationId) throw new Error("请先初始化仿真。");
      const path = simulationApiPaths(state.simulationId)[action];
      const options = lifecycleRequestOptions(action);
      const payload = await request(path, options);
      if (responseLooksLikeSimulation(payload)) renderSimulation(payload);
      await refreshSimulation(); await refreshSupplementary();
      ui.status.textContent = `${ui.status.textContent} · ${lifecycleStatusDetail(action, label, payload)}`;
    });
  }
  async function compare() {
    await runAction(async () => {
      const scenarioId = selectedScenarioId();
      if (!scenarioId) throw new Error("请先选择教学场景。");
      const payload = await request("/api/comparisons", { method: "POST", body: JSON.stringify({ scenario_id: scenarioId, seed: currentSeed(), steps: 5 }) });
      renderComparison(payload); ui.status.textContent = "三策略比较结果已更新";
    });
  }
  async function loadScenarios() {
    const payload = await request("/api/scenarios");
    const scenarios = asArray(firstDefined(payload.scenarios, payload.items, payload.data, payload));
    clear(ui.scenario);
    if (!scenarios.length) { ui.scenario.append(new Option("后端未提供可用场景", "")); ui.scenario.disabled = true; return; }
    ui.scenario.append(new Option("请选择教学场景", ""));
    scenarios.forEach((raw) => { const scenario = asObject(raw); const id = firstDefined(scenario.id, scenario.scenario_id, scenario.slug); ui.scenario.append(new Option(text(firstDefined(scenario.name, scenario.title, id)), text(id))); });
    ui.scenario.disabled = false;
  }
  async function boot() {
    try {
      await request("/health"); ui.dot.className = "status-dot status-ok"; ui.connection.textContent = "本地服务已连接";
      await loadScenarios();
    } catch (error) {
      ui.dot.className = "status-dot status-error"; ui.connection.textContent = "本地服务不可用"; ui.scenario.disabled = true; showError(new Error(`无法连接本地教学服务。${error instanceof Error ? error.message : text(error)}`));
    }
  }
  element("initialize-button").addEventListener("click", initialize);
  element("step-button").addEventListener("click", () => step(1));
  element("five-steps-button").addEventListener("click", () => step(5));
  element("pause-button").addEventListener("click", () => lifecycle("pause", "已暂停"));
  element("resume-button").addEventListener("click", () => lifecycle("resume", "已恢复"));
  element("snapshot-button").addEventListener("click", () => lifecycle("snapshot", "已创建快照"));
  element("replay-button").addEventListener("click", () => lifecycle("replay", "已请求回放"));
  element("compare-button").addEventListener("click", compare);
  renderKpis({});
  boot();
})();
