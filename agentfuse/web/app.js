/* AgentFuse local dashboard - plain HTML/CSS/JS, no external resources. */
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const state = {
  overview: null,
  workflows: [],
  currentWorkflow: null,
  currentGraph: null,
  currentTimeline: null,
  currentExecution: null,
  benchmarkJob: null,
  timer: null,
};

function $(id) { return document.getElementById(id); }

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  return response.json();
}

function fmtTime(value) {
  if (!value) return "-";
  const date = new Date(value * 1000);
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  return Number(seconds).toFixed(3);
}

function pill(text, kind) {
  return `<span class="pill ${kind || ""}">${text}</span>`;
}

function statusPill(status) {
  const map = {
    COMPLETED: "ok", RUNNING: "info", QUEUED: "info", PENDING: "",
    SHARED_WAIT: "warn", DETACHED: "danger", FAILED: "danger", TIMEOUT: "danger",
    CANCELLED: "danger", CRASHED: "danger", WAITING: "warn", RUNNING_INDEPENDENTLY: "info",
    MATCH: "ok", MISMATCH: "danger", UNKNOWN: "warn", EXECUTING: "info",
    VALIDATING: "info", CLOSED: "", INTERRUPTED: "danger",
  };
  return pill(status || "-", map[status] || "");
}

function short(value, size) {
  if (!value) return "-";
  const text = String(value);
  return text.length > size ? text.slice(0, size) + "…" : text;
}

function el(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const key in attrs || {}) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------------ */
/* tabs                                                                */
/* ------------------------------------------------------------------ */
function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      $(`tab-${tab.dataset.tab}`).classList.add("active");
      if (location.hash !== `#${tab.dataset.tab}`) location.hash = tab.dataset.tab;
      if (tab.dataset.tab === "benchmark") loadBenchmarkHistory();
      if (tab.dataset.tab === "dependency") loadExecutions();
    });
  });
  window.addEventListener("hashchange", () => {
    const name = (location.hash || "#overview").slice(1);
    const tab = document.querySelector(`.tab[data-tab="${name}"]`);
    if (tab) tab.click();
  });
}

function applyHash() {
  const name = (location.hash || "#overview").slice(1);
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

/* ------------------------------------------------------------------ */
/* overview                                                            */
/* ------------------------------------------------------------------ */
function renderOverview(data) {
  state.overview = data;
  $("mode-badge").textContent = `模式: ${data.mode}`;
  const workers = data.workers || {};
  const cards = [
    { label: "运行中的 Workflow", value: data.running_workflows, sub: `累计 ${data.total_workflows}` },
    { label: "Agent Invocation", value: data.active_invocations, sub: "已观测调用" },
    { label: "逻辑工具调用", value: data.logical_tool_calls, sub: "LogicalToolCall" },
    { label: "物理执行", value: data.physical_executions, sub: "PhysicalExecution" },
    { label: "共享等待中", value: data.shared_waiting, sub: "SHARED_WAIT" },
    { label: "已分离", value: data.detached, sub: "依赖不一致" },
    { label: "运行 Worker", value: `${workers.running || 0}/${workers.max_workers || 0}`, sub: `等待 ${workers.waiting || 0}` },
    { label: "系统 CPU", value: `${(workers.system_cpu_percent || 0).toFixed(1)}%`, sub: "psutil 采样" },
    { label: "已用 RSS", value: `${((workers.observed_rss_mb || 0)).toFixed(1)} MiB`, sub: `预算 ${workers.max_memory_budget_mb || 0} MiB` },
    { label: "峰值 RSS", value: `${((workers.peak_rss_mb || 0)).toFixed(1)} MiB`, sub: "监控值(非硬限制)" },
  ];
  $("overview-cards").innerHTML = cards.map((card) => `
    <div class="card">
      <div class="label">${card.label}</div>
      <div class="value">${card.value}</div>
      <div class="sub">${card.sub || ""}</div>
    </div>`).join("");

  const rows = (data.workflows || []).map((workflow) => {
    const id = workflow.workflow_id;
    const metrics = state.workflowMetrics && state.workflowMetrics[id];
    const duration = workflow.end_time ? workflow.end_time - workflow.start_time : null;
    return `<tr data-workflow="${id}" style="cursor:pointer">
      <td class="mono">${short(id, 16)}</td>
      <td>${workflow.scenario}</td>
      <td>${workflow.runtime_mode}</td>
      <td>${statusPill(workflow.status)}</td>
      <td>${fmtDuration(duration)}</td>
      <td>${metrics ? metrics.logical_calls : "-"}</td>
      <td>${metrics ? metrics.physical_executions : "-"}</td>
      <td>${metrics ? metrics.shared_calls : "-"}</td>
      <td>${metrics ? metrics.detach_count : "-"}</td>
    </tr>`;
  }).join("");
  $("workflow-table").querySelector("tbody").innerHTML = rows || `<tr><td colspan="9">暂无运行记录，请使用 CLI 或基准测试页启动一次运行。</td></tr>`;
  $("workflow-table").querySelectorAll("tr[data-workflow]").forEach((row) => {
    row.addEventListener("click", () => {
      const select = $("workflow-select");
      select.value = row.dataset.workflow;
      switchToWorkflow(row.dataset.workflow);
    });
  });

  const resourceRows = [
    ["最大 Worker 数", workers.max_workers],
    ["运行中", workers.running],
    ["等待队列", workers.waiting],
    ["预估内存占用", `${workers.reserved_estimate_mb || 0} MiB`],
    ["实测 RSS 合计", `${workers.observed_rss_mb || 0} MiB`],
    ["峰值 RSS", `${workers.peak_rss_mb || 0} MiB`],
    ["内存预算", `${workers.max_memory_budget_mb || 0} MiB`],
    ["系统 CPU", `${workers.system_cpu_percent || 0}%`],
    ["累计放行执行", workers.admitted_total],
  ];
  $("resource-panel").innerHTML = resourceRows.map(([k, v]) =>
    `<div class="row"><span>${k}</span><span class="mono">${v}</span></div>`).join("") +
    `<div class="bar"><span style="width:${Math.min(100, (workers.running || 0) / Math.max(1, workers.max_workers || 1) * 100)}%"></span></div>` +
    renderPending(workers.pending);

  const eventRows = (data.recent_events || []).map((event) => `
    <tr>
      <td>${fmtTime(event.timestamp)}</td>
      <td>${event.event_type}</td>
      <td class="mono">${short(event.logical_call_id || event.physical_execution_id || event.invocation_id, 18)}</td>
      <td class="mono">${short(JSON.stringify(event.metadata || {}), 70)}</td>
    </tr>`).join("");
  $("event-table").querySelector("tbody").innerHTML = eventRows || `<tr><td colspan="4">暂无事件</td></tr>`;

  const shareRows = (data.recent_share_events || []).map((event) => `
    <tr>
      <td>${fmtTime(event.timestamp)}</td>
      <td>${event.event_type}</td>
      <td class="mono">${short(event.metadata && (event.metadata.reason || event.metadata.relative_path || ""), 70)}</td>
    </tr>`).join("");
  $("share-table").querySelector("tbody").innerHTML = shareRows || `<tr><td colspan="3">暂无共享事件</td></tr>`;
}

async function loadOverview() {
  try {
    const data = await api("/api/overview");
    const metrics = {};
    for (const workflow of (data.workflows || []).slice(0, 12)) {
      try {
        metrics[workflow.workflow_id] = await api(`/api/workflows/${workflow.workflow_id}/metrics`);
      } catch (error) { /* ignore */ }
    }
    state.workflowMetrics = metrics;
    renderOverview(data);
    const health = await api("/api/health");
    $("health-badge").textContent = `状态: ${health.status} · ${health.database.split(/[\\/]/).pop()}`;
  } catch (error) {
    $("health-badge").textContent = `状态: 错误 (${error.message})`;
  }
}

/* ------------------------------------------------------------------ */
/* workflow detail                                                     */
/* ------------------------------------------------------------------ */
async function loadWorkflowList() {
  const data = await api("/api/workflows?limit=100");
  state.workflows = data.workflows || [];
  const options = state.workflows.map((workflow) =>
    `<option value="${workflow.workflow_id}">${workflow.workflow_id} · ${workflow.scenario} · ${workflow.runtime_mode} · ${workflow.status}</option>`).join("");
  $("workflow-select").innerHTML = options;
  if (state.workflows.length) {
    const select = $("workflow-select");
    if (!select.value) select.value = state.workflows[0].workflow_id;
    await switchToWorkflow(select.value);
  }
}

async function switchToWorkflow(workflowId) {
  state.currentWorkflow = workflowId;
  try {
    const detail = await api(`/api/workflows/${workflowId}`);
    const metrics = detail.metrics || {};
    const items = [
      ["场景", detail.workflow.scenario],
      ["模式", detail.workflow.runtime_mode],
      ["状态", detail.workflow.status],
      ["耗时(s)", fmtDuration(metrics.makespan)],
      ["逻辑调用", metrics.logical_calls],
      ["物理执行", metrics.physical_executions],
      ["节省执行", metrics.saved_executions],
      ["共享成功率", `${((metrics.share_success_rate || 0) * 100).toFixed(1)}%`],
      ["分离次数", metrics.detach_count],
      ["验证次数", metrics.validation_count],
      ["总 CPU(s)", fmtDuration(metrics.total_cpu_time)],
      ["峰值 RSS(MiB)", ((metrics.peak_rss || 0) / 1048576).toFixed(2)],
      ["平均共享等待(s)", fmtDuration(metrics.avg_share_wait)],
    ];
    if (metrics.online_detach_before_finish !== undefined) {
      items.push(["在线分离早于 Leader 完成", metrics.online_detach_before_finish ? "是" : "否"]);
    }
    if (metrics.detach_lead_ms_max !== null && metrics.detach_lead_ms_max !== undefined) {
      items.push(["分离领先 Leader(ms)", metrics.detach_lead_ms_max]);
    }
    $("workflow-summary").innerHTML = items.map(([k, v]) =>
      `<div class="summary-item"><div class="k">${k}</div><div class="v">${v ?? "-"}</div></div>`).join("");

    const executionById = {};
    (detail.physical_executions || []).forEach((execution) => {
      executionById[execution.execution_id] = execution;
    });

    const callRows = (detail.logical_calls || []).map((call) => {
      const execution = executionById[call.physical_execution_id] || {};
      const note = execution.admission_reason
        ? `${execution.admission_reason}${execution.queued_time ? ` · 排队 ${execution.queued_time.toFixed(3)}s` : ""}`
        : (call.status === "PENDING" ? "等待执行槽位" : "-");
      return `
      <tr>
        <td class="mono">${short(call.logical_call_id, 16)}</td>
        <td>${call.tool_name}@${call.tool_version}</td>
        <td>${statusPill(call.status)}</td>
        <td class="mono">${short(call.physical_execution_id, 16)}</td>
        <td class="mono">${short(call.workspace_snapshot_id, 16)}</td>
        <td class="mono">${short(call.result_hash, 14)}</td>
        <td>${note}</td>
      </tr>`;
    }).join("");
    $("call-table").querySelector("tbody").innerHTML = callRows || `<tr><td colspan="7">暂无逻辑调用</td></tr>`;

    const graph = await api(`/api/workflows/${workflowId}/graph`);
    state.currentGraph = graph;
    renderGraph(graph);

    const timeline = await api(`/api/workflows/${workflowId}/timeline`);
    state.currentTimeline = timeline;
    renderTimeline(timeline);
  } catch (error) {
    $("workflow-summary").innerHTML = `<div class="summary-item"><div class="k">错误</div><div class="v">${error.message}</div></div>`;
  }
}

const GRAPH_COLUMNS = [
  ["workflow", "Workflow"], ["agent", "Agent"], ["invocation", "Invocation"],
  ["logical_call", "逻辑调用"], ["physical_execution", "物理执行"], ["tool_result", "结果"],
];

function renderGraph(graph) {
  const container = $("graph-container");
  container.innerHTML = "";
  const nodes = graph.nodes || [];
  if (!nodes.length) { container.innerHTML = "<p class='muted'>暂无图数据</p>"; return; }

  const columns = {};
  GRAPH_COLUMNS.forEach(([kind]) => { columns[kind] = []; });
  nodes.forEach((node) => {
    if (!columns[node.kind]) columns[node.kind] = [];
    columns[node.kind].push(node);
  });

  const nodeWidth = 190, nodeHeight = 40, colGap = 240, rowGap = 54, marginX = 30, marginY = 30;
  const positions = {};
  GRAPH_COLUMNS.forEach(([kind], colIndex) => {
    (columns[kind] || []).forEach((node, rowIndex) => {
      positions[node.id] = {
        x: marginX + colIndex * colGap,
        y: marginY + rowIndex * rowGap,
        w: nodeWidth,
        h: nodeHeight,
      };
    });
  });

  const maxRows = Math.max(...Object.values(columns).map((items) => items.length), 1);
  const width = marginX * 2 + GRAPH_COLUMNS.length * colGap;
  const height = marginY * 2 + maxRows * rowGap;
  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  (graph.edges || []).forEach((edge) => {
    const source = positions[edge.source];
    const target = positions[edge.target];
    if (!source || !target) return;
    const x1 = source.x + source.w, y1 = source.y + source.h / 2;
    const x2 = target.x, y2 = target.y + target.h / 2;
    const mid = (x1 + x2) / 2;
    let cls = "edge";
    if (edge.kind === "SHARES_EXECUTION_WITH") cls += " share";
    if (edge.kind === "RESULT_DELIVERED_TO") cls += " detached";
    const path = el("path", { class: cls, d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}` });
    path.appendChild(el("title", {}, `${edge.kind}`));
    svg.appendChild(path);
  });

  nodes.forEach((node) => {
    const position = positions[node.id];
    if (!position) return;
    const group = el("g", { class: `node ${node.kind}` });
    group.appendChild(el("rect", { x: position.x, y: position.y, width: position.w, height: position.h }));
    const label = node.label || node.id;
    group.appendChild(el("text", { x: position.x + 10, y: position.y + 17 }, short(label, 24)));
    const sub = node.status ? `状态: ${node.status}` : (node.kind === "workflow" ? "" : node.kind);
    group.appendChild(el("text", { x: position.x + 10, y: position.y + 32, fill: "#949cad" }, short(sub, 30)));
    group.addEventListener("click", () => showNodeDetail(node));
    svg.appendChild(group);
  });

  container.appendChild(svg);
  $("graph-legend").innerHTML = GRAPH_COLUMNS.map(([kind, label]) =>
    `<span><span class="dot" style="background:${{
      workflow: "#4c8dff", agent: "#7d6bd6", invocation: "#4c8dff",
      logical_call: "#35c2a5", physical_execution: "#f0a33a", tool_result: "#4cc38a",
    }[kind]}"></span>${label}</span>`).join("") +
    `<span>实线 = 调用/绑定/交付关系</span><span>绿色 = 共享执行</span><span>红色 = 结果交付</span>`;
}

function renderTimeline(timeline) {
  const container = $("timeline-container");
  container.innerHTML = "";
  const lanes = timeline.lanes || [];
  if (!lanes.length) { container.innerHTML = "<p class='muted'>暂无时间线数据</p>"; return; }

  const start = timeline.start_time || 0;
  const end = timeline.end_time || start + 1;
  const span = Math.max(end - start, 0.001);
  const labelWidth = 220, marginRight = 40, rowHeight = 38;
  const width = 1000, plotWidth = width - labelWidth - marginRight;
  const height = lanes.length * rowHeight + 60;

  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  for (let tick = 0; tick <= 5; tick += 1) {
    const ratio = tick / 5;
    const x = labelWidth + ratio * plotWidth;
    svg.appendChild(el("line", { x1: x, y1: 26, x2: x, y2: height - 24, stroke: "#2a2f3a" }));
    svg.appendChild(el("text", { x, y: 18, "text-anchor": "middle", fill: "#949cad" },
      `${(span * ratio * 1000).toFixed(0)}ms`));
  }

  lanes.forEach((lane, index) => {
    const y = 34 + index * rowHeight;
    svg.appendChild(el("text", { x: 8, y: y + 16, class: "lane-label" }, short(lane.label, 30)));

    (lane.segments || []).forEach((segment) => {
      const x1 = labelWidth + ((segment.start - start) / span) * plotWidth;
      const endValue = segment.end === null || segment.end === undefined ? segment.start : segment.end;
      const x2 = labelWidth + ((endValue - start) / span) * plotWidth;
      const rect = el("rect", {
        x: x1, y: y + 4, width: Math.max(2, x2 - x1), height: 18, rx: 4,
        fill: segment.type === "invocation" ? "#2b3a58" : "#4a3d1f",
        stroke: segment.type === "invocation" ? "#4c8dff" : "#f0a33a",
      });
      rect.appendChild(el("title", {}, `${segment.type}: ${fmtDuration(endValue - segment.start)}s`));
      svg.appendChild(rect);
    });

    (lane.markers || []).forEach((marker) => {
      const x = labelWidth + ((marker.timestamp - start) / span) * plotWidth;
      const colors = {
        requested: "#949cad", shared_wait: "#4c8dff", execution_started: "#f0a33a",
        dependency_read: "#35c2a5", validation_passed: "#4cc38a", validation_failed: "#f2606a",
        detached: "#f2606a", execution_finished: "#f0a33a", delivered: "#4cc38a",
        failed: "#f2606a", cancelled: "#f2606a",
      };
      const circle = el("circle", { cx: x, cy: y + 13, r: 4.5, fill: colors[marker.type] || "#949cad" });
      const text = `${marker.type}${marker.path ? " " + marker.path : ""}${marker.reason ? " " + marker.reason : ""}`;
      circle.appendChild(el("title", {}, text));
      circle.addEventListener("click", () => showDrawer("事件 " + marker.type, marker));
      svg.appendChild(circle);
    });
  });

  container.appendChild(svg);
}

function renderPending(pending) {
  if (!pending || !pending.length) {
    return `<h3 style="margin:12px 0 6px;font-size:13px;color:#949cad">等待资源的执行</h3>
            <p style="color:#949cad;font-size:12px;margin:0">无</p>`;
  }
  const rows = pending.map((item) => `
    <tr>
      <td class="mono">${short(item.execution_id, 16)}</td>
      <td>${item.waiting_seconds.toFixed(2)}s</td>
      <td>${item.estimated_memory_mb} MiB</td>
      <td>${item.reason}</td>
    </tr>`).join("");
  return `<h3 style="margin:12px 0 6px;font-size:13px;color:#949cad">等待资源的执行</h3>
    <table><thead><tr><th>执行</th><th>已等待</th><th>预估内存</th><th>等待原因</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

/* ------------------------------------------------------------------ */
/* dependency inspector                                                */
/* ------------------------------------------------------------------ */
async function loadExecutions() {
  const data = await api("/api/executions");
  const executions = data.executions || [];
  const select = $("execution-select");
  select.innerHTML = executions.map((execution) =>
    `<option value="${execution.execution_id}">${execution.execution_id} · ${execution.tool_name} · ${execution.execution_status}</option>`).join("");
  if (executions.length) {
    if (!select.value) select.value = executions[0].execution_id;
    await selectExecution(select.value);
  }
}

async function selectExecution(executionId) {
  state.currentExecution = executionId;
  try {
    const payload = await api(`/api/executions/${executionId}`);
    const execution = payload.execution || {};
    const items = [
      ["工具", `${execution.tool_name}@${execution.tool_version}`],
      ["状态", execution.execution_status],
      ["Worker PID", execution.worker_pid],
      ["CPU 时间(s)", fmtDuration(execution.cpu_time)],
      ["峰值 RSS(MiB)", ((execution.peak_rss || 0) / 1048576).toFixed(2)],
      ["退出状态", execution.exit_status],
      ["快照", execution.source_snapshot_id],
      ["共享组", execution.share_group_id || "-"],
    ];
    $("execution-summary").innerHTML = items.map(([k, v]) =>
      `<div class="summary-item"><div class="k">${k}</div><div class="v mono">${v ?? "-"}</div></div>`).join("");

    const inspection = await api(`/api/executions/${executionId}/dependencies`);
    const rows = (inspection.dependencies || []).map((dependency) => {
      const checks = dependency.validations || [];
      const cell = checks.length
        ? checks.map((check) => `${statusPill(check.validation_status)}<div class="mono">${short(check.logical_call_id, 12)}</div>`).join("<br>")
        : "-";
      const reason = checks.map((check) => check.mismatch_reason).filter(Boolean).join(" | ");
      return `<tr>
        <td>${dependency.sequence_number}</td>
        <td>${dependency.dependency_type}</td>
        <td class="mono">${dependency.relative_path}</td>
        <td class="mono">${short(dependency.leader_fingerprint, 18)}</td>
        <td>${cell}</td>
        <td class="mono">${short(reason, 60)}</td>
      </tr>`;
    }).join("");
    $("dependency-table").querySelector("tbody").innerHTML = rows || `<tr><td colspan="6">该执行没有依赖记录</td></tr>`;

    const participants = (inspection.participants || []).map((participant) => `
      <tr>
        <td class="mono">${short(participant.logical_call_id, 16)}</td>
        <td>${participant.is_leader ? "Leader" : "Follower"}</td>
        <td>${statusPill(participant.status)}</td>
        <td>${participant.validated_prefix_length}</td>
        <td>${participant.wait_duration === null ? "-" : fmtDuration(participant.wait_duration)}</td>
        <td class="mono">${short(participant.wait_reason || "-", 60)}</td>
        <td class="mono">${short(participant.detach_reason || "-", 60)}</td>
      </tr>`).join("");
    $("participant-table").querySelector("tbody").innerHTML = participants || `<tr><td colspan="7">无共享组</td></tr>`;
  } catch (error) {
    $("execution-summary").innerHTML = `<div class="summary-item"><div class="k">错误</div><div class="v">${error.message}</div></div>`;
  }
}

/* ------------------------------------------------------------------ */
/* benchmark                                                           */
/* ------------------------------------------------------------------ */
async function loadScenarios() {
  const data = await api("/api/benchmarks/scenarios");
  const catalogue = data.catalogue || [];
  state.scenarioCatalogue = catalogue;
  $("bench-scenario").innerHTML = (data.scenarios || []).map((scenario) => {
    const spec = catalogue.find((item) => item.name === scenario);
    return `<option value="${scenario}">${scenario}${spec ? " - " + spec.title : ""}</option>`;
  }).join("");
  renderScenarioHint();
}

function renderScenarioHint() {
  const name = $("bench-scenario").value;
  const spec = (state.scenarioCatalogue || []).find((item) => item.name === name);
  const hint = $("bench-hint");
  if (!hint) return;
  if (!spec) { hint.textContent = ""; return; }
  hint.innerHTML = `<strong>${spec.title}</strong>：${spec.description}<br/>预期：${spec.expectation}`;
}

async function runBenchmark() {
  const modes = Array.from($("bench-modes").selectedOptions).map((option) => option.value);
  const payload = {
    scenario: $("bench-scenario").value,
    repeat: Number($("bench-repeat").value || 3),
    modes,
    workers: Number($("bench-workers").value || 2),
    memory_budget_mb: Number($("bench-memory").value || 1024),
  };
  $("bench-status").textContent = "正在启动...";
  $("bench-result").innerHTML = "";
  try {
    const job = await api("/api/benchmarks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.benchmarkJob = job.job_id;
    pollBenchmark(job.job_id);
  } catch (error) {
    $("bench-status").textContent = `启动失败: ${error.message}`;
  }
}

async function pollBenchmark(jobId) {
  const job = await api(`/api/benchmarks/jobs/${jobId}`);
  if (job.status === "RUNNING") {
    $("bench-status").textContent = "运行中...";
    setTimeout(() => pollBenchmark(jobId), 1000);
    return;
  }
  if (job.status === "FAILED") {
    $("bench-status").textContent = `运行失败: ${job.error}`;
    return;
  }
  $("bench-status").textContent = `完成，用时 ${fmtDuration(job.finished_at - job.started_at)}s`;
  renderBenchmark(job.result);
  loadBenchmarkHistory();
}

function renderBenchmark(report) {
  if (!report) { $("bench-result").innerHTML = "<p class='muted'>无结果</p>"; return; }
  const aggregated = report.aggregated || {};
  const lead = (data) => (data.detach_lead_ms_max ? data.detach_lead_ms_max.mean : 0);
  const rows = Object.keys(aggregated).map((mode) => {
    const data = aggregated[mode];
    return `<tr>
      <td>${mode}</td>
      <td>${data.runs}</td>
      <td>${fmtDuration(data.makespan.mean)}</td>
      <td>${fmtDuration(data.makespan.stdev)}</td>
      <td>${data.physical_executions.mean.toFixed(1)}</td>
      <td>${data.logical_calls.mean.toFixed(1)}</td>
      <td>${data.saved_executions.mean.toFixed(1)}</td>
      <td>${fmtDuration(data.total_cpu_time.mean)}</td>
      <td>${(data.peak_rss.mean / 1048576).toFixed(2)}</td>
      <td>${(data.share_success_rate.mean * 100).toFixed(1)}%</td>
      <td>${data.detach_count.mean.toFixed(1)}</td>
      <td>${data.validation_count.mean.toFixed(1)}</td>
      <td>${lead(data).toFixed(1)}</td>
      <td>${((data.online_detach_before_finish_rate ? data.online_detach_before_finish_rate.mean : 0) * 100).toFixed(0)}%</td>
      <td>${report.speedup && report.speedup[mode] !== undefined ? report.speedup[mode].toFixed(3) + "x" : "-"}</td>
    </tr>`;
  }).join("");
  const correctness = Object.keys(report.correctness || {}).map((mode) => {
    const data = report.correctness[mode];
    return `<tr><td>${mode}</td><td>${data.compared}</td><td>${data.identical}</td><td>${data.mismatched}</td>
      <td>${data.mismatched === 0 ? pill("结果一致", "ok") : pill("结果不一致", "danger")}</td></tr>`;
  }).join("");
  const stability = report.resource_stability || {};
  const reproducibility = report.reproducibility || {};
  const stabilityRows = `
    <tr><td>随机种子</td><td>${report.seed ?? "-"}</td><td>${report.seed ?? "-"}</td>
      <td>${report.seed ? pill("已固定", "ok") : pill("未固定", "danger")}</td></tr>
    <tr><td>资源稳定性</td><td>${stability.stable ? "稳定" : "不稳定"}</td>
      <td>RSS 增长 ${(stability.runtime_rss_growth_mib ?? 0).toFixed(1)} MiB</td>
      <td>${stability.stable ? pill("无泄漏", "ok") : pill("存在增长", "danger")}</td></tr>
    <tr><td>可复现性</td><td>${reproducibility.reproducible ? "通过" : "未通过"}</td>
      <td>相同种子重复 ${report.repeats ?? 0} 次</td>
      <td>${reproducibility.reproducible ? pill("结果一致", "ok") : pill("结果不一致", "danger")}</td></tr>`;

  const modes = Object.keys(aggregated);
  const max = Math.max(...modes.map((mode) => aggregated[mode].makespan.mean), 0.001);
  const chart = modes.map((mode) => `
    <div class="col">
      <div class="value">${fmtDuration(aggregated[mode].makespan.mean)}s</div>
      <div class="rect" style="height:${(aggregated[mode].makespan.mean / max) * 130}px"></div>
      <div class="label">${mode}</div>
    </div>`).join("");

  $("bench-result").innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>模式</th><th>次数</th><th>耗时均值(s)</th><th>标准差</th><th>物理执行</th><th>逻辑调用</th>
      <th>节省执行</th><th>CPU(s)</th><th>峰值RSS(MiB)</th><th>共享成功率</th><th>分离</th><th>验证</th>
      <th>提前分离(ms)</th><th>在线提前分离率</th><th>加速比</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <h2 style="margin-top:18px">工作流耗时对比</h2>
    <div class="bar-chart">${chart}</div>
    <h2 style="margin-top:18px">正确性校验（与 baseline 逐调用比对）</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>模式</th><th>对比次数</th><th>一致</th><th>不一致</th><th>结论</th></tr></thead>
      <tbody>${correctness || "<tr><td colspan='5'>无</td></tr>"}</tbody></table></div>
    <h2 style="margin-top:18px">基准公平性与资源校验</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>校验项</th><th>结果</th><th>详情</th><th>结论</th></tr></thead>
      <tbody>${stabilityRows}</tbody></table></div>`;
}

async function loadBenchmarkHistory() {
  try {
    const data = await api("/api/benchmarks");
    const rows = (data.runs || []).slice().reverse().slice(0, 60).map((run) => `
      <tr>
        <td>${run.scenario}</td>
        <td>${run.mode}</td>
        <td>${run.repeat_index + 1}</td>
        <td>${fmtDuration(run.makespan)}</td>
        <td>${run.physical_executions}</td>
        <td>${fmtDuration(run.total_cpu_time)}</td>
        <td>${((run.share_success_rate || 0) * 100).toFixed(1)}%</td>
      </tr>`).join("");
    $("bench-history").querySelector("tbody").innerHTML = rows || `<tr><td colspan="7">暂无历史</td></tr>`;
  } catch (error) { /* ignore */ }
}

/* ------------------------------------------------------------------ */
/* tools                                                               */
/* ------------------------------------------------------------------ */
async function loadTools() {
  const data = await api("/api/tools");
  const rows = (data.tools || []).map((tool) => `
    <tr>
      <td>${tool.tool_name}</td>
      <td>${tool.tool_version}</td>
      <td>${tool.deterministic ? "是" : "否"}</td>
      <td>${tool.read_only ? "是" : "否"}</td>
      <td>${tool.shareable ? pill("可共享", "ok") : pill("不可共享", "warn")}</td>
      <td>${tool.resource_estimate.memory_mb} MiB</td>
      <td class="mono">${tool.implementation_reference}</td>
      <td>${tool.description || ""}</td>
    </tr>`).join("");
  $("tool-table").querySelector("tbody").innerHTML = rows;
}

/* ------------------------------------------------------------------ */
/* drawer                                                              */
/* ------------------------------------------------------------------ */
function showNodeDetail(node) {
  showDrawer(node.label || node.id, node);
}

function showDrawer(title, payload) {
  $("node-detail-title").textContent = title;
  $("node-detail-body").textContent = JSON.stringify(payload, null, 2);
  $("node-detail").classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* init                                                                */
/* ------------------------------------------------------------------ */
async function init() {
  bindTabs();
  $("refresh-btn").addEventListener("click", loadOverview);
  $("node-detail-close").addEventListener("click", () => $("node-detail").classList.add("hidden"));
  $("workflow-select").addEventListener("change", (event) => switchToWorkflow(event.target.value));
  $("execution-select").addEventListener("change", (event) => selectExecution(event.target.value));
  $("bench-run").addEventListener("click", runBenchmark);
  $("bench-scenario").addEventListener("change", renderScenarioHint);

  await Promise.all([loadOverview(), loadWorkflowList(), loadTools(), loadScenarios(), loadBenchmarkHistory()]);
  applyHash();
  state.timer = setInterval(loadOverview, 3000);
}

document.addEventListener("DOMContentLoaded", init);
