/* Front end harness - runs the real dashboard script against a minimal DOM.
 *
 * The dashboard is plain HTML/CSS/JS without a build chain.  This harness
 * provides the small part of the browser API that ``agentfuse/web/app.js``
 * uses, feeds it the **recorded responses of the real backend**
 * (``tests/frontend/payloads.json``) and then drives the interface:
 *
 *   * every tab is opened (the tabs are the real buttons of index.html),
 *   * a workflow is selected by **clicking its row** (the graph and the
 *     timeline are rendered),
 *   * an execution is selected (the dependency inspector is rendered),
 *   * the benchmark table is rendered.
 *
 * The DOM is built by parsing the **real** ``agentfuse/web/index.html``:
 * an element that the page does not have does not exist in the harness, and
 * the rows that the script writes are parsed back into elements - therefore
 * the click handlers of the rows and the tabs really run.
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
const PAGE = join(ROOT, "agentfuse", "web", "index.html");
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

const VOID_TAGS = new Set(["input", "br", "hr", "img", "meta", "link", "source"]);

class Element {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.children = [];
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.value = "";
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.selectedOptions = [];
    this.classList = new ClassList(this);
  }
  /* the text of an element replaces its children (like in a browser) */
  get textContent() {
    return this._text;
  }
  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }
  /* the parser appends the text of a node - it must not clear the children */
  appendText(text) {
    this._text += text;
  }
  /* setting the HTML **parses** it - the rows become elements and can be
   * clicked; reading it serialises them again */
  get innerHTML() {
    return this.children.map((child) => child.serialize()).join("");
  }
  set innerHTML(html) {
    this.children = parseHtml(String(html ?? ""));
    this._text = "";
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
    const text = this._text || "";
    const tag = this.tagName.toLowerCase();
    return `<${tag}${attributes}>${text}${children}</${tag}>`;
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

/* a very small HTML parser - enough for the rows the dashboard writes */
function parseHtml(html) {
  const root = new Element("div", "");
  const stack = [root];
  const tokens = html.match(/<[^>]*>|[^<]+/g) || [];
  for (const token of tokens) {
    if (!token.startsWith("<")) {
      stack[stack.length - 1].appendText(token);
      continue;
    }
    if (token.startsWith("</")) {
      const name = token.slice(2, -1).trim().toLowerCase();
      for (let index = stack.length - 1; index > 0; index -= 1) {
        if (stack[index].tagName.toLowerCase() === name) {
          stack.length = index;
          break;
        }
      }
      continue;
    }
    const match = token.match(/^<([a-zA-Z][\w-]*)([^>]*?)(\/?)>$/);
    if (!match) {
      continue;
    }
    const element = new Element(match[1], "");
    const attributePattern = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"/g;
    let attribute;
    while ((attribute = attributePattern.exec(match[2] || "")) !== null) {
      element.setAttribute(attribute[1], attribute[2]);
      if (attribute[1] === "id") {
        element.id = attribute[2];
      }
      if (attribute[1] === "class") {
        element.className = attribute[2];
      }
      if (attribute[1].startsWith("data-")) {
        element.dataset[attribute[1].slice(5)] = attribute[2];
      }
    }
    stack[stack.length - 1].appendChild(element);
    const tag = match[1].toLowerCase();
    if (!match[3] && !VOID_TAGS.has(tag)) {
      stack.push(element);
    }
  }
  return root.children;
}

/* the selectors of the dashboard: a tag, classes, an id and attributes */
function matchesSelector(element, selector) {
  const wanted = selector.trim();
  const match = wanted.match(/^([a-zA-Z][\w-]*)?((?:[.#][\w-]+|\[[^\]]*\])*)$/);
  if (!match) {
    return false;
  }
  if (match[1] && element.tagName !== match[1].toUpperCase()) {
    return false;
  }
  const tokens = (match[2] || "").match(/[.#][\w-]+|\[[^\]]*\]/g) || [];
  for (const token of tokens) {
    if (token.startsWith(".")) {
      if (!(element.className || "").split(/\s+/).includes(token.slice(1))) {
        return false;
      }
    } else if (token.startsWith("#")) {
      if (element.id !== token.slice(1)) {
        return false;
      }
    } else {
      const attribute = token.slice(1, -1).match(/^([\w-]+)(?:=["']?([^"']*)["']?)?$/);
      if (!attribute) {
        return false;
      }
      const actual = element.getAttribute(attribute[1]);
      if (actual === undefined || actual === null) {
        return false;
      }
      if (attribute[2] !== undefined && actual !== attribute[2]) {
        return false;
      }
    }
  }
  return true;
}

/* ------------------------------------------------------------------ */
/* the document: the real index.html                                  */
/* ------------------------------------------------------------------ */
const BODY = new Element("body", "");
const DOCUMENT = new Element("html", "");
DOCUMENT.appendChild(BODY);

function collect(element, found = []) {
  for (const child of element.children) {
    found.push(child);
    collect(child, found);
  }
  return found;
}

/* the page provides the structure - an element that is not in the page does
 * not exist in the harness */
const page = parseHtml(readFileSync(PAGE, "utf-8"));
for (const element of page) {
  BODY.appendChild(element);
}

const document = {
  getElementById: (id) => collect(BODY).find((item) => item.id === id) || null,
  querySelector: (selector) => collect(BODY).find((item) => matchesSelector(item, selector)) || null,
  querySelectorAll: (selector) => collect(BODY).filter((item) => matchesSelector(item, selector)),
  createElement: (tag) => new Element(tag, ""),
  createElementNS: (_ns, tag) => new Element(tag, ""),
  addEventListener: () => {},
  body: BODY,
};

/* the harness helpers - an element that the page does not have is a failure,
 * it must not be fabricated */
function element(id) {
  const found = document.getElementById(id);
  if (!found) {
    throw new Error(`the page has no element with the id ${id}`);
  }
  return found;
}

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
  [/^\/api\/executions(\?|$)/, () => PAYLOADS.executions],
  [/^\/api\/executions\/[^/]+\/dependencies$/, () => PAYLOADS.dependencies],
  [/^\/api\/executions\/[^/]+$/, () => PAYLOADS.execution],
  [/^\/api\/tools$/, () => PAYLOADS.tools],
  [/^\/api\/benchmarks\/scenarios$/, () => PAYLOADS.scenarios],
  [/^\/api\/benchmarks$/, () => PAYLOADS.benchmarks],
  // the health endpoint reports the path of the database - the dashboard
  // shows it, so the stub has to know it
  [/^\/api\/health$/, () => ({ status: "ok", database: "C:/agentfuse/agentfuse.db" })],
];

const REQUESTED = [];
const UNMATCHED = [];

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
  UNMATCHED.push(path);
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
  location: {
    hash: "",
  },
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

/* the browser normalises the hash to "#name" */
Object.defineProperty(sandbox.location, "hash", {
  get() {
    return this._hash || "";
  },
  set(value) {
    const text = String(value || "");
    this._hash = text.startsWith("#") ? text : `#${text}`;
  },
});

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
  return node.innerHTML;
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
  "总览显示加入被拒的计数",
  summary.includes("加入被拒") && summary.includes(String(overview.rejected_candidates)),
  `expected ${overview.rejected_candidates}`,
);
check(
  "总览显示离组重试的计数",
  summary.includes("离组重试") && summary.includes(String(overview.retried_participants)),
  `expected ${overview.retried_participants}`,
);
/* the share events show the reason of the event - the waiting call carries
 * the wait reason, not a plain reason */
const waitingEvent = (overview.recent_share_events || []).find(
  (event) => event.metadata && event.metadata.wait_reason,
);
check(
  "分享事件显示等待原因",
  waitingEvent
    ? htmlOf("share-table").includes(waitingEvent.metadata.wait_reason)
    : false,
  waitingEvent ? waitingEvent.metadata.wait_reason : "no waiting event",
);
check(
  "工具列表来自真实注册表",
  htmlOf("tool-table").includes(PAYLOADS.tools.tools[0].tool_name),
  PAYLOADS.tools.tools[0].tool_name,
);

/* the workflow list is filled from the real workflow records */
const workflowRows = htmlOf("workflow-table");
check(
  "工作流列表包含真实的工作流",
  workflowRows.includes(PAYLOADS._ids.workflow),
);

/* the rows are elements - the click handler of a row has to run */
const workflowRow = element("workflow-table").querySelectorAll("tr[data-workflow]")[0];
check("工作流列表的行是可点击的元素", Boolean(workflowRow), "no tr[data-workflow]");
if (workflowRow) {
  await workflowRow.click();
}

/* select a workflow: the graph and the timeline are rendered */
const graph = htmlOf("graph-container");
check(
  "执行图被渲染",
  graph.includes(PAYLOADS.graph.nodes[0].label),
  PAYLOADS.graph.nodes[0].label,
);
const timeline = htmlOf("timeline-container");
check(
  "时间线被渲染",
  timeline.includes(PAYLOADS.timeline.lanes[0].label),
  PAYLOADS.timeline.lanes[0].label,
);

/* select an execution: the dependency inspector is rendered */
await sandbox.selectExecution(PAYLOADS._ids.execution);
const dependencyRows = htmlOf("dependency-table");
check(
  "依赖检查器显示真实的依赖路径",
  dependencyRows.includes(PAYLOADS.dependencies.dependencies[0].relative_path),
  PAYLOADS.dependencies.dependencies[0].relative_path,
);
const participantRows = htmlOf("participant-table");
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
  benchmarkRows.includes("稳定"),
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
const identity = PAYLOADS.workflow.agents[0].identity_status;
check(
  "工作流详情显示身份状态",
  identity === "CONFIRMED"
    ? agentRows.includes("已确认")
    : agentRows.includes("未确定"),
  `identity_status=${identity}`,
);

/* the workflow detail shows the confirmed dependencies and the wait reason */
const detail = PAYLOADS.workflow;
const firstDependency = Object.values(detail.dependencies)[0][0];
check(
  "工作流详情显示已确认依赖",
  htmlOf("workflow-dependency-table").includes(firstDependency.relative_path),
  firstDependency.relative_path,
);
/* the wait reason is shown in the participant table of the inspector */
const waiting = (PAYLOADS.dependencies.participants || []).find(
  (participant) => participant.wait_reason,
);
check(
  "依赖检查器显示真实的等待原因",
  waiting ? participantRows.includes(waiting.wait_reason) : false,
  waiting ? waiting.wait_reason : "the execution has no waiting participant",
);

/* every tab has to be reachable - and the click really switches the panel */
const TABS = ["overview", "workflow", "dependency", "benchmark", "tools"];
for (const tab of TABS) {
  const button = document.querySelector(`.tab[data-tab="${tab}"]`);
  check(`页签 ${tab} 存在`, Boolean(button));
  if (!button) {
    continue;
  }
  await button.click();
  const panel = element(`tab-${tab}`);
  check(
    `页签 ${tab} 的面板被激活`,
    panel.classList.contains("active") && button.classList.contains("active"),
    `panel=${panel.className} button=${button.className}`,
  );
}

/* the health badge shows the state of the backend */
const healthBadge = element("health-badge").textContent;
check(
  "状态徽标显示后端的真实状态",
  healthBadge.includes("ok"),
  healthBadge,
);

check(
  "所有请求都命中了后端路由",
  REQUESTED.length > 0 && UNMATCHED.length === 0,
  UNMATCHED.join(", "),
);

if (failures.length) {
  console.log(`\n${failures.length} 个前端检查未通过`);
  process.exit(1);
}
console.log("\n前端在真实数据上渲染成功");
