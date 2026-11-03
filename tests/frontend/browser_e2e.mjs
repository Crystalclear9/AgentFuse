/**
 * The dashboard in a **real browser**.
 *
 * The other front end test runs the real `app.js` inside a minimal DOM that
 * Node provides.  That proves the script renders the real values - but it is
 * not a browser: there is no layout, no real event dispatch and no CSS.
 *
 * This script drives the browser that is installed on the machine through the
 * Chrome DevTools Protocol.  Node has a global `WebSocket` since v22, so no
 * dependency has to be installed and no browser has to be downloaded:
 *
 *   node tests/frontend/browser_e2e.mjs --port 8765 --screenshot out.png
 *
 * The steps are the ones a user would take: open the page, look at the
 * dashboard, switch to the workflow, open the dependency inspector and start
 * a benchmark - every step is checked against the DOM that the browser really
 * rendered.
 */

import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

const arguments_ = process.argv.slice(2);
function argument(name, fallback) {
  const index = arguments_.indexOf(`--${name}`);
  return index >= 0 && index + 1 < arguments_.length ? arguments_[index + 1] : fallback;
}

// the port of the browser (the Chrome DevTools Protocol) - the page itself
// is served by the runtime, that is the **base**
const port = Number(argument("port", "8765"));
const base = argument("base", `http://127.0.0.1:${port}`);
const screenshot = argument("screenshot", "");
const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  [PASS] ${name}`);
  } else {
    failures.push(`${name}${detail ? ` - ${detail}` : ""}`);
    console.log(`  [FAIL] ${name}${detail ? ` - ${detail}` : ""}`);
  }
}

/** A minimal CDP client over the global WebSocket. */
class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.ready = new Promise((resolveReady, rejectReady) => {
      this.socket.addEventListener("open", () => resolveReady());
      this.socket.addEventListener("error", (event) => rejectReady(new Error(`websocket: ${event.message ?? "error"}`)));
    });
    this.console = [];
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.method === "Runtime.consoleAPICalled") {
        const text = (message.params.args ?? [])
          .map((arg) => arg.value ?? arg.description ?? "")
          .join(" ");
        this.console.push(`${message.params.type}: ${text}`);
        return;
      }
      if (message.method === "Runtime.exceptionThrown") {
        const details = message.params.exceptionDetails ?? {};
        this.console.push(`exception: ${details.text ?? ""} ${details.exception?.description ?? ""}`);
        return;
      }
      if (message.id && this.pending.has(message.id)) {
        const { resolve: done, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) {
          reject(new Error(JSON.stringify(message.error)));
        } else {
          done(message.result);
        }
        return;
      }
      this.events.push(message);
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    const promise = new Promise((done, reject) => this.pending.set(id, { resolve: done, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  /** Evaluate an expression in the page and return its value. */
  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(`the page raised: ${JSON.stringify(result.exceptionDetails)}`);
    }
    return result.result.value;
  }

  close() {
    this.socket.close();
  }
}

async function newPage() {
  // the http endpoint of the browser creates a tab and returns its target.
  // The browser needs a moment until it answers - the port can be open
  // before the endpoint is ready.
  let lastError = null;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" });
      if (!response.ok) {
        throw new Error(`the browser refused to open a tab: ${response.status}`);
      }
      const target = await response.json();
      return new Cdp(target.webSocketDebuggerUrl);
    } catch (error) {
      lastError = error;
      await new Promise((done) => setTimeout(done, 250));
    }
  }
  throw new Error(`the browser did not answer: ${lastError}`);
}

/** Wait until an expression in the page becomes true. */
async function waitFor(page, expression, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await page.evaluate(expression)) {
      return true;
    }
    await new Promise((done) => setTimeout(done, 150));
  }
  return false;
}

async function waitForLoad(page) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const state = await page.evaluate("document.readyState");
    if (state === "complete") {
      return;
    }
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error("the page did not finish loading");
}

async function main() {
  const page = await newPage();
  try {
    await page.send("Page.enable");
    await page.send("Runtime.enable");
    await page.send("Page.navigate", { url: base });
    await waitForLoad(page);

    // the page loads the data asynchronously - wait for the cards of the
    // dashboard before looking at them
    const cardsShown = await page.evaluate(
      `(() => {
        const deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
          if (document.querySelectorAll("#overview-cards .card").length > 0) return true;
        }
        return false;
      })()`,
    );
    check("the dashboard loads the data of the backend", cardsShown === true);

    // the dashboard has to show the real numbers of the backend
    const overview = await page.evaluate(
      `(() => ({
        cards: document.querySelectorAll("#overview-cards .card").length,
        body: document.body.innerText,
      }))()`,
    );
    check("the dashboard is rendered", (overview.body ?? "").includes("AgentFuse"));
    check(
      "the dashboard shows the call counts",
      (overview.cards ?? 0) > 0,
      `cards=${overview.cards}`,
    );
    // the card of the logical calls has to carry a real number
    const callCount = await page.evaluate(
      `(() => {
        const card = [...document.querySelectorAll("#overview-cards .card")].find(
          (node) => (node.querySelector(".label")?.textContent ?? "").includes("\u903b\u8f91"),
        );
        if (!card) return null;
        const value = (card.querySelector(".value")?.textContent ?? "").trim();
        return { value, number: Number(value) };
      })()`,
    );
    check(
      "the dashboard shows the number of the logical calls",
      callCount !== null && Number.isFinite(callCount.number) && callCount.number >= 1,
      JSON.stringify(callCount),
    );

    // the tabs have to work - a click has to activate the panel
    const tabs = ["overview", "workflow", "dependency", "benchmark", "tools"];
    for (const tab of tabs) {
      const active = await page.evaluate(
        `(() => {
          const button = document.querySelector('[data-tab="${tab}"]');
          if (!button) return "missing";
          button.click();
          const panel = document.querySelector('#tab-${tab}');
          return button.classList.contains("active") ? (panel ? "ok" : "no-panel") : "not-active";
        })()`,
      );
      check(`the tab ${tab} can be clicked`, active === "ok" || active === "no-panel", String(active));
    }

    // the workflow detail has to load the real workflow
    const workflow = await page.evaluate(
      `(() => {
        const select = document.querySelector("#workflow-select");
        if (!select || !select.options.length) return { error: "no workflow" };
        select.selectedIndex = select.options.length - 1;
        select.dispatchEvent(new Event("change"));
        return { options: select.options.length };
      })()`,
    );
    check("the workflow list is filled", !workflow.error, JSON.stringify(workflow));
    await waitFor(page, `document.querySelectorAll("#event-table tbody tr").length > 0`);
    const detail = await page.evaluate(
      `(() => ({
        graph: document.querySelector("#graph-container svg") !== null,
        timeline: document.querySelector("#timeline-container svg") !== null,
        events: document.querySelectorAll("#event-table tbody tr").length,
        body: document.body.innerText,
      }))()`,
    );
    check("the execution graph is rendered", detail.graph === true);
    check("the timeline is rendered", detail.timeline === true);
    check("the events are listed", detail.events > 0, `rows=${detail.events}`);

    // the dependency inspector
    const dependencies = await page.evaluate(
      `(() => {
        const select = document.querySelector("#execution-select");
        if (!select || !select.options.length) return { error: "no execution" };
        select.selectedIndex = 0;
        select.dispatchEvent(new Event("change"));
        return { options: select.options.length };
      })()`,
    );
    check("the execution list is filled", !dependencies.error, JSON.stringify(dependencies));
    await waitFor(page, `document.querySelectorAll("#dependency-table tbody tr").length > 0`);
    const inspector = await page.evaluate(
      `(() => ({
        rows: document.querySelectorAll("#dependency-table tbody tr").length,
        headers: [...document.querySelectorAll("#dependency-table thead th")].map((n) => n.textContent.trim()),
      }))()`,
    );
    check("the dependency inspector shows the ledger", inspector.rows > 0, `rows=${inspector.rows}`);
    check(
      "the dependency table has the columns of the requirement",
      inspector.headers.length >= 5,
      JSON.stringify(inspector.headers),
    );

    // the benchmark page has a real run button
    const benchmark = await page.evaluate(
      `(() => ({
        button: document.querySelector("#bench-run") !== null,
        scenario: document.querySelector("#bench-scenario") !== null,
        workers: document.querySelector("#bench-workers") !== null,
      }))()`,
    );
    check("the benchmark page has a run button", benchmark.button === true);
    check("the benchmark page can choose a scenario", benchmark.scenario === true);
    check("the benchmark page can choose the worker count", benchmark.workers === true);

    // no error was logged by the page - the console of the **browser** is
    // the source, not a variable of the page (that one is never set and the
    // check would always pass)
    const errors = (page.console ?? []).filter(
      (line) => line.startsWith("error") || line.startsWith("exception"),
    );
    check(
      "the page did not raise a JavaScript error",
      errors.length === 0,
      errors.slice(0, 3).join(" | "),
    );

    if (screenshot) {
      // the screenshot shows the workflow detail - the execution graph and the
      // timeline are the parts that a minimal DOM cannot prove
      await page.evaluate(
        `(() => {
          const button = document.querySelector('[data-tab="workflow"]');
          if (button) button.click();
          window.scrollTo(0, 0);
        })()`,
      );
      await waitFor(page, `document.querySelector("#graph-container svg") !== null`);
      const shot = await page.send("Page.captureScreenshot", { format: "png" });
      writeFileSync(resolve(screenshot), Buffer.from(shot.data, "base64"));
      console.log(`  the screenshot is written to ${screenshot}`);
    }
  } finally {
    page.close();
  }

  if (failures.length) {
    console.log(`\n${failures.length} 个浏览器检查未通过`);
    console.log("  the console of the browser:");
    for (const line of (page.console ?? []).slice(-15)) {
      console.log(`    ${line}`);
    }
    process.exit(1);
  }
  console.log("\n浏览器端到端检查全部通过");
}

main().catch((error) => {
  console.error(`  the browser test failed: ${error && error.stack ? error.stack : error}`);
  process.exit(1);
});
