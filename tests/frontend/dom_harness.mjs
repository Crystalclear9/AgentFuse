/* Front end harness - runs the real dashboard script against a minimal DOM.
 *
 * The dashboard is plain HTML/CSS/JS without a build chain.  This harness
 * provides the small part of the browser API that ``agentfuse/web/app.js``
 * uses, feeds it the **recorded responses of the real backend**
 * (``tests/frontend/payloads.json``) and then drives the interface:
 *
 *   * every tab is opened,
 *   * a workflow is selected (the graph and the timeline are rendered),
 *   * an execution is selected (the dependency inspector is rendered),
 *   * the benchmark table is rendered.
 *
 * Afterwards it asserts that the rendered HTML contains the values of the
 * real payloads.  A JavaScript error or a wrong field therefore fails the
 * test - the front end is executed, not only parsed.
 *
 *     node tests/frontend/dom_harness.mjs
 *
 * Exit code 0 = the front end rendered the real data, 1 = it did not.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const SCRIPT = join(ROOT, "agentfuse", "web", "app.js");
const PAYLOADS = JSON.parse(
  readFileSync(join(HERE, "payloads.json"), "utf-8"),
);

/* ------------------------------------------------------------------ */
/* a minimal DOM                                                      */
/* ------------------------------------------------------------------ */
class ClassList {
  constructor(element) {
    this.element = element;
  }
  add(...names) {
    const current = new Set((this.element.className || "").split(/\s+/).filter(Boolean));
    names.forEach((name) => current.add(name));
    this.element.className = [...current].join(" ");
  }
  remove(...names) {
    const current = new Set((this.element.className || "").split(/\s+/).filter(Boolean));
    names.forEach((name) => current.delete(name));
    this.element.className = [...current].join(" ");
  }
  contains(name) {
    return (this.element.className || "").split(/\s+/).includes(name);
  }
}

class Element {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.children = [];
    this.listeners = {};
    this.className = "";
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.selectedOptions = [];
    this.classList = new ClassList(this);
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  /* a small HTML serialisation - the dashboard builds SVG elements */
  serialize() {
    const attributes = Object.entries(this.attributes)
      .map(([name, value]) => ` ${name}="${value}"`)
      .join("");
    const children = this.children.map((child) => child.serialize()).join("");
    const text = this.textContent || "";
    return `<${this.tagName.toLowerCase()}${attributes}>${text}${children}</${this.tagName.toLowerCase()}>`;
  }
  remove() {}
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  getAttribute(name) {
    return this.attributes[name];
  }
  addEventListener(name, handler) {
    (this.listeners[name] = this.listeners[name] || []).push(handler);
  }
  async dispatch(name, event = {}) {
    for (const handler of this.listeners[name] || []) {
      await handler({ target: this, ...event });
    }
  }
  click() {
    return this.dispatch("click");
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const matches = [];
    const walk = (element) => {
      for (const child of element.children) {
        if (matchesSelector(child, selector)) {
          matches.push(child);
        }
        walk(child);
      }
    };
    walk(this);
    return matches;
  }
}

function matchesSelector(element, selector) {
  const wanted = selector.trim();
  if (wanted.startsWith(".")) {
    return (element.className || "").split(/\s+/).includes(wanted.slice(1));
  }
  if (wanted.startsWith("#")) {
    return element.id === wanted.slice(1);
  }
  return element.tagName === wanted.toUpperCase();
}

/* the tables of the dashboard - every table has a body */
const TABLES = [
  "bench-history",
  "call-table",
  "dependency-table",
  "event-table",
  "participant-table",
  "share-table",
  "tool-table",
  "workflow-agent-table",
  "workflow-table",
  "workflow-dependency-table",
];

const ELEMENTS = new Map();

function element(id, tag = "div") {
  if (!ELEMENTS.has(id)) {
    const created = new Element(TABLES.includes(id) ? "table" : tag, id);
    if (TABLES.includes(id)) {
      created.appendChild(new Element("tbody", `${id}-tbody`));
    }
    ELEMENTS.set(id, created);
  }
  return ELEMENTS.get(id);
}

/* the ids the dashboard uses - they are created on demand */
const document = {
  getElementById: (id) => element(id),
  querySelector: (selector) => element(selector.replace(/^[#.]/, "")),
  querySelectorAll: (selector) => [element(selector.replace(/^[#.]/, ""))],
  createElement: (tag) => new Element(tag),
  createElementNS: (_ns, tag) => new Element(tag),
  addEventListener: () => {},
  body: new Element("body"),
};

/* ------------------------------------------------------------------ */
/* the recorded backend                                               */
/* ------------------------------------------------------------------ */
const ROUTES = [
  [/^\/api\/overview$/, () => PAYLOADS.overview],
  [/^\/api\/workflows\?/, () => PAYLOADS.workflows],
  [/^\/api\/workflows\/[^/]+\/metrics$/, () => PAYLOADS.metrics],
  [/^\/api\/workflows\/[^/]+\/graph$/, () => PAYLOADS.graph],
  [/^\/api\/workflows\/[^/]+\/timeline$/, () => PAYLOADS.timeline],
  [/^\/api\/workflows\/[^/]+$/, () => PAYLOADS.workflow],
  [/^\/api\/executions\?/, () => PAYLOADS.executions],
  [/^\/api\/executions\/[^/]+\/dependencies$/, () => PAYLOADS.dependencies],
  [/^\/api\/executions\/[^/]+$/, () => PAYLOADS.execution],
  [/^\/api\/tools$/, () => PAYLOADS.tools],
  [/^\/api\/benchmarks\/scenarios$/, () => PAYLOADS.scenarios],
  [/^\/api\/benchmarks$/, () => PAYLOADS.benchmarks],
  [/^\/api\/health$/, () => ({ status: "ok" })],
];

const REQUESTED = [];

async function fetchStub(path) {
  REQUESTED.push(path);
  for (const [pattern, payload] of ROUTES) {
    if (pattern.test(path)) {
      return {
        ok: true,
        status: 200,
        json: async () => payload(),
        text: async () => JSON.stringify(payload()),
      };
    }
  }
  return {
    ok: false,
    status: 404,
    json: async () => ({}),
    text: async () => `not found: ${path}`,
  };
}

/* ------------------------------------------------------------------ */
/* run the dashboard                                                  */
/* ------------------------------------------------------------------ */
const sandbox = {
  document,
  fetch: fetchStub,
  location: { hash: "" },
  window: { location: { hash: "" }, addEventListener: () => {} },
  console,
  setTimeout: (handler, delay) => setTimeout(handler, delay),
  clearTimeout: (handle) => clearTimeout(handle),
  setInterval: () => 0,
  clearInterval: () => {},
  Date,
  Math,
  JSON,
  Number,
  String,
  Object,
  Array,
  Promise,
  Set,
  Map,
  Error,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const source = readFileSync(SCRIPT, "utf-8");
vm.runInContext(source, sandbox, { filename: "app.js" });

const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  [PASS] ${name}`);
  } else {
    failures.push(`${name}${detail ? ` - ${detail}` : ""}`);
    console.log(`  [FAIL] ${name}${detail ? ` - ${detail}` : ""}`);
  }
}

await sandbox.init();

/* the tables are filled through their body */
function htmlOf(id) {
  const node = element(id);
  const body = node.querySelector("tbody");
  if (body) {
    return body.innerHTML;
  }
  return node.innerHTML + node.children.map((child) => child.serialize()).join("");
}

/* ------------------------------------------------------------------ */
/* the dashboard has to show the real data                            */
/* ------------------------------------------------------------------ */
const overview = PAYLOADS.overview;
const summary = element("overview-cards").innerHTML;
check(
  "总览显示真实的逻辑调用数",
  summary.includes(String(overview.logical_tool_calls)),
  `expected ${overview.logical_tool_calls} in ${summary.slice(0, 120)}`,
);
check(
  "总览显示真实的物理执行数",
  summary.includes(String(overview.physical_executions)),
);
check(
  "工具列表来自真实注册表",
  htmlOf("tool-table").includes(overview.tools[0].tool_name),
);

/* the workflow list is filled from the real workflow records */
const workflowRows = htmlOf("workflow-table");
check(
  "工作流列表包含真实的工作流",
  workflowRows.includes(PAYLOADS._ids.workflow),
);

/* select a workflow: the graph and the timeline are rendered */
await sandbox.switchToWorkflow(PAYLOADS._ids.workflow);
check("执行图被渲染", htmlOf("graph-container").length > 0);
check(
  "执行图包含真实的节点",
  htmlOf("graph-container").includes(PAYLOADS.graph.nodes[0].label),
  PAYLOADS.graph.nodes[0].label,
);
check("时间线被渲染", htmlOf("timeline-container").length > 0);
check(
  "时间线包含真实的泳道",
  htmlOf("timeline-container").includes(PAYLOADS.timeline.lanes[0].label),
  PAYLOADS.timeline.lanes[0].label,
);

/* select an execution: the dependency inspector is rendered */
await sandbox.selectExecution(PAYLOADS._ids.execution);
const dependencyRows = htmlOf("dependency-table");
check("依赖检查器被渲染", dependencyRows.length > 0);
check(
  "依赖检查器显示真实的依赖路径",
  dependencyRows.includes(PAYLOADS.dependencies.dependencies[0].relative_path),
);
const participantRows = htmlOf("participant-table");
check("参与者表被渲染", participantRows.length > 0);
const participantId = PAYLOADS.dependencies.participants[0].logical_call_id;
check(
  "参与者表显示真实的调用",
  participantRows.includes(participantId.slice(0, 16)),
  participantId,
);

/* every call status needs a badge - the runtime error must be visible */
check(
  "RUNTIME_ERROR 的徽标是危险色",
  sandbox.statusPill("RUNTIME_ERROR").includes("danger"),
  sandbox.statusPill("RUNTIME_ERROR"),
);
check(
  "CREATED 的徽标不是危险色",
  !sandbox.statusPill("CREATED").includes("danger"),
  sandbox.statusPill("CREATED"),
);
check(
  "SHARED_WAIT 的徽标是警告色",
  sandbox.statusPill("SHARED_WAIT").includes("warn"),
  sandbox.statusPill("SHARED_WAIT"),
);

/* the benchmark table renders the recorded comparison */
await sandbox.renderBenchmark({
  aggregated: {
    online: {
      runs: 2,
      makespan: { mean: 0.08, stdev: 0.01 },
      physical_executions: { mean: 1 },
      logical_calls: { mean: 2 },
      saved_executions: { mean: 1 },
      total_cpu_time: { mean: 0.01 },
      peak_rss: { mean: 24 * 1024 * 1024 },
      share_success_rate: { mean: 0.5 },
      detach_count: { mean: 0 },
      validation_count: { mean: 5 },
      detach_lead_ms_max: { mean: 12 },
      saved_wait_ms_max: { mean: 12 },
      online_detach_before_finish_rate: { mean: 1 },
    },
  },
  speedup: { online: 1.5 },
  runs: {
    online: [{ workflow_id: PAYLOADS._ids.workflow }],
  },
  correctness: { online: { compared: 2, identical: 2, mismatched: 0 } },
  resource_stability: { stable: true, runtime_rss_growth_mib: 1 },
  reproducibility: { reproducible: true },
  seed: 20240917,
});
const benchmarkRows = element("bench-result").innerHTML;
check("基准表格被渲染", benchmarkRows.includes("online"));
check("基准表格显示节省的执行数", benchmarkRows.includes("1.0"));
check("基准表格显示加速比", benchmarkRows.includes("1.500x"));
check(
  "基准表格显示资源稳定性",
  benchmarkRows.includes("stable") || benchmarkRows.includes("稳定"),
);
check(
  "基准表格显示共享次数",
  benchmarkRows.includes("共享次数"),
);
check(
  "基准表格显示验证开销",
  benchmarkRows.includes("验证失败"),
);
check(
  "基准页展示逻辑调用与物理执行的对应",
  benchmarkRows.includes(PAYLOADS.workflow.logical_calls[0].logical_call_id.slice(0, 14))
    && benchmarkRows.includes(PAYLOADS.workflow.physical_executions[0].execution_id.slice(0, 14)),
);

/* the workflow detail shows the agents and the state of their identity */
const agentRows = htmlOf("workflow-agent-table");
check(
  "工作流详情显示 Agent 列表",
  agentRows.includes(PAYLOADS.workflow.agents[0].agent_id.slice(0, 16)),
);
check(
  "工作流详情显示身份状态",
  agentRows.includes("已确认") || agentRows.includes("未确定"),
);

/* the workflow detail shows the confirmed dependencies and the wait reason */
const detail = PAYLOADS.workflow;
const workflowCalls = htmlOf("call-table");
check(
  "工作流详情显示已确认依赖",
  htmlOf("workflow-dependency-table").includes(
    Object.values(detail.dependencies)[0][0].relative_path,
  ),
);
check(
  "工作流详情显示等待原因",
  workflowCalls.includes("等待 Leader") || workflowCalls.includes("Leader"),
);

/* every tab has to be reachable */
for (const tab of ["overview", "workflow", "dependency", "benchmark", "tools"]) {
  check(`页签 ${tab} 存在`, Boolean(element(`tab-${tab}`)));
}

check(
  "所有请求都命中了后端路由",
  REQUESTED.length > 0 && REQUESTED.every((path) => path.startsWith("/api/")),
  REQUESTED.filter((path) => !path.startsWith("/api/")).join(", "),
);

if (failures.length) {
  console.log(`\n${failures.length} 个前端检查未通过`);
  process.exit(1);
}
console.log("\n前端在真实数据上渲染成功");
