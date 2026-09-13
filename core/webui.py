"""The operator dashboard.

One console, rendered from an :class:`~core.idea.IdeaSpec`. It is built for the
person on shift rather than for a screenshot: the evidence trail sits next to
the decision, the cost of every check is on screen, and a check the agent chose
*not* to run is shown as prominently as one it ran, because that restraint is
the product.

Everything is inlined - no CDN, no build step - so the bundle runs from a clean
checkout with one command and cannot break because a script host is blocked.
"""

from __future__ import annotations

import json

from .idea import IdeaSpec

_CSS = """
*, *::before, *::after { box-sizing: border-box; }
:root {
  --accent: #1d6fe0;
  --accent-soft: #e8f0fd;
  --bg: #0e1117;
  --panel: #161b24;
  --panel-2: #1c222d;
  --line: #262e3a;
  --ink: #e8edf5;
  --ink-dim: #97a3b6;
  --ink-faint: #6b7686;
  --calm: #2fa36b;
  --watch: #c9992a;
  --warn: #d97430;
  --alarm: #d64550;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.55 var(--sans);
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); }
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; }

/* header */
header {
  border-bottom: 1px solid var(--line); background: var(--panel);
  padding: 18px 24px; display: flex; gap: 20px;
  align-items: flex-start; flex-wrap: wrap;
}
.brand { flex: 1 1 320px; min-width: 260px; }
.brand h1 { font-size: 21px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.brand .dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--accent); flex: none;
}
.brand p { margin: 5px 0 0; color: var(--ink-dim); font-size: 13px; max-width: 62ch; }
.badges { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.badge {
  font: 11px/1 var(--mono); text-transform: uppercase; letter-spacing: 0.06em;
  padding: 6px 9px; border-radius: 5px; border: 1px solid var(--line);
  color: var(--ink-dim); background: var(--panel-2); white-space: nowrap;
}
.badge:empty { display: none; }
.badge.on { color: var(--accent); border-color: var(--accent); background: transparent; }
.badge.sim { color: var(--watch); border-color: color-mix(in srgb, var(--watch) 45%, transparent); }
.badge.live { color: var(--calm); border-color: color-mix(in srgb, var(--calm) 45%, transparent); }

/* metric strip */
.metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1px; background: var(--line); border-bottom: 1px solid var(--line);
}
.metric { background: var(--panel); padding: 13px 24px; }
.metric .v { font: 600 22px/1.2 var(--sans); font-variant-numeric: tabular-nums; }
.metric .k {
  font: 11px/1.3 var(--mono); text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--ink-faint); margin-top: 3px;
}

/* layout */
main {
  display: grid; grid-template-columns: 320px minmax(0, 1fr) 300px;
  gap: 1px; background: var(--line); min-height: calc(100vh - 190px);
}
.col { background: var(--bg); padding: 18px; min-width: 0; }
.col.mid { background: #10141b; }
section + section { margin-top: 22px; }
.section-title {
  font: 11px/1 var(--mono); text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--ink-faint); margin-bottom: 10px; display: flex;
  justify-content: space-between; align-items: center; gap: 8px;
}

/* scenario cards */
.scenario {
  border: 1px solid var(--line); border-radius: 7px; background: var(--panel);
  padding: 12px; margin-bottom: 9px; cursor: pointer;
  transition: border-color .12s, transform .12s;
}
.scenario:hover { border-color: var(--accent); transform: translateY(-1px); }
.scenario[aria-selected="true"] { border-color: var(--accent); background: var(--panel-2); }
.scenario h3 { font-size: 13.5px; }
.scenario p { margin: 4px 0 0; color: var(--ink-dim); font-size: 12.5px; }
.scenario .expect {
  margin-top: 8px; font: 11px/1 var(--mono); color: var(--ink-faint);
  display: flex; align-items: center; gap: 6px;
}

/* buttons + inputs */
button {
  font: 500 13px var(--sans); border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel-2); color: var(--ink);
  padding: 9px 13px; transition: border-color .12s, background .12s;
}
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .5; cursor: progress; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
button.primary:hover:not(:disabled) { filter: brightness(1.08); }
button.ghost { background: transparent; }
button.wide { width: 100%; }
.row { display: flex; gap: 7px; flex-wrap: wrap; }
input {
  font: 13px var(--mono); width: 100%; padding: 9px 10px; color: var(--ink);
  background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
}
input:focus { outline: none; border-color: var(--accent); }
label {
  display: block; font: 11px/1 var(--mono); text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--ink-faint); margin: 10px 0 5px;
}
.help { color: var(--ink-faint); font-size: 11.5px; margin-top: 7px; line-height: 1.5; }

/* decision */
.verdict {
  border: 1px solid var(--line); border-left-width: 3px; border-radius: 7px;
  background: var(--panel); padding: 16px 18px;
}
.verdict.calm  { border-left-color: var(--calm); }
.verdict.watch { border-left-color: var(--watch); }
.verdict.warn  { border-left-color: var(--warn); }
.verdict.alarm { border-left-color: var(--alarm); }
.verdict .level {
  font: 600 11px/1 var(--mono); text-transform: uppercase; letter-spacing: 0.1em;
}
.verdict.calm .level  { color: var(--calm); }
.verdict.watch .level { color: var(--watch); }
.verdict.warn .level  { color: var(--warn); }
.verdict.alarm .level { color: var(--alarm); }
.verdict .action { font: 600 17px/1.35 var(--sans); margin: 7px 0 0; }
.verdict .why { color: var(--ink-dim); margin: 8px 0 0; max-width: 74ch; }
.verdict .foot {
  margin-top: 13px; padding-top: 11px; border-top: 1px solid var(--line);
  display: flex; gap: 18px; flex-wrap: wrap;
  font: 11.5px var(--mono); color: var(--ink-faint);
}
.verdict .foot b { color: var(--ink-dim); font-weight: 500; }
.guardrail {
  margin-top: 11px; padding: 10px 12px; border-radius: 6px;
  background: color-mix(in srgb, var(--alarm) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--alarm) 35%, transparent);
  font-size: 12.5px; color: var(--ink);
}

/* tables */
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th {
  text-align: left; font: 11px/1 var(--mono); text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--ink-faint); font-weight: 400;
  padding: 8px 9px; border-bottom: 1px solid var(--line);
}
td { padding: 9px; border-bottom: 1px solid var(--line); vertical-align: top; }
tbody tr:last-child td { border-bottom: none; }
td.mono, .mono { font-family: var(--mono); font-size: 12px; }
.answer { color: var(--ink); word-break: break-word; }
.tag {
  display: inline-block; font: 10px/1 var(--mono); padding: 4px 6px;
  border-radius: 4px; border: 1px solid var(--line); color: var(--ink-dim);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.tag.simulator { color: var(--watch); border-color: color-mix(in srgb, var(--watch) 40%, transparent); }
.tag.live { color: var(--calm); border-color: color-mix(in srgb, var(--calm) 40%, transparent); }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); }

/* steps */
.step {
  display: grid; grid-template-columns: 26px 1fr; gap: 11px;
  padding: 11px 0; border-bottom: 1px solid var(--line);
}
.step:last-child { border-bottom: none; }
.step .n {
  width: 26px; height: 26px; border-radius: 50%; flex: none;
  display: grid; place-items: center; font: 600 11px var(--mono);
  border: 1px solid var(--line); color: var(--ink-dim); background: var(--panel-2);
}
.step.tool .n { border-color: var(--accent); color: var(--accent); }
.step.refused .n, .step.error .n { border-color: var(--warn); color: var(--warn); }
.step h4 { margin: 0; font: 600 13px var(--sans); }
.step .meta { font: 11px var(--mono); color: var(--ink-faint); margin-top: 3px; }
.step .obs {
  margin-top: 6px; font: 11.5px var(--mono); color: var(--ink-dim);
  background: var(--panel-2); border: 1px solid var(--line);
  border-radius: 5px; padding: 7px 9px; white-space: pre-wrap; word-break: break-word;
}
.step .reason { margin-top: 6px; color: var(--ink-dim); font-size: 12.5px; font-style: italic; }

/* skipped */
.skipped { border: 1px dashed var(--line); border-radius: 7px; padding: 12px; }
.skipped li { color: var(--ink-dim); font-size: 12.5px; margin-bottom: 5px; }
.skipped li:last-child { margin-bottom: 0; }
.skipped .name { font-family: var(--mono); color: var(--ink); }

/* event feed */
.feed { display: flex; flex-direction: column; gap: 6px; max-height: 68vh; overflow-y: auto; }
.event {
  border-left: 2px solid var(--line); padding: 7px 0 7px 10px;
  font: 11.5px/1.5 var(--mono); color: var(--ink-dim);
  overflow-wrap: anywhere;
}
/* The topic is a short identifier: never break it mid-word, even in a narrow
   column, or "case.step" renders as three stacked fragments. */
.event b {
  color: var(--ink); font-weight: 500; display: block;
  white-space: nowrap; overflow-wrap: normal;
}
/* Namespaced: a bare ".step" here would collide with the agent-step timeline
   above, which is a grid with a 26px first column, and squash every event. */
.event--decided { border-left-color: var(--accent); }
.event--step { border-left-color: var(--ink-faint); }
.event--consent { border-left-color: var(--alarm); }
/* Block, or the timestamp runs straight into the end of the message. */
.event time { display: block; margin-top: 3px; color: var(--ink-faint); font-size: 10.5px; }

.empty { color: var(--ink-faint); font-size: 13px; padding: 26px 0; text-align: center; }
.limits li { color: var(--ink-dim); font-size: 12.5px; margin-bottom: 6px; }
.spin { display: inline-block; width: 11px; height: 11px; border: 2px solid var(--line);
  border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1180px) {
  main { grid-template-columns: 1fr; }
  .feed { max-height: 320px; }
}
"""

_JS = r"""
const SPEC = window.__SPEC__;
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const fmt = (v) => (v === null || v === undefined ? "-" : typeof v === "object" ? JSON.stringify(v) : String(v));

function toneFor(level) {
  const meta = (SPEC.ui.levels || {})[level];
  return (meta && meta.tone) || "calm";
}
function labelFor(level) {
  const meta = (SPEC.ui.levels || {})[level];
  return (meta && meta.label) || level;
}

/* ---------- scenarios ---------- */

function renderScenarios() {
  const host = $("#scenarios");
  host.innerHTML = "";
  (SPEC.scenarios || []).forEach((s) => {
    const card = el("article", "scenario");
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.dataset.id = s.id;
    card.appendChild(el("h3", null, s.title));
    card.appendChild(el("p", null, s.subtitle));
    const expect = el("div", "expect");
    expect.appendChild(el("span", null, "expects"));
    const dot = el("span", "tag");
    dot.textContent = labelFor(s.expect_level);
    expect.appendChild(dot);
    card.appendChild(expect);
    const run = () => runScenario(s.id, card);
    card.addEventListener("click", run);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); run(); }
    });
    host.appendChild(card);
  });
}

function markSelected(card) {
  document.querySelectorAll(".scenario").forEach((n) => n.setAttribute("aria-selected", "false"));
  if (card) card.setAttribute("aria-selected", "true");
}

/* ---------- running ---------- */

let busy = false;
function setBusy(state, label) {
  busy = state;
  document.querySelectorAll("button").forEach((b) => { b.disabled = state; });
  $("#status").innerHTML = state
    ? '<span class="spin"></span> ' + (label || "agent working")
    : "";
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error((await res.text()).slice(0, 300));
  return res.json();
}

async function runScenario(id, card) {
  if (busy) return;
  markSelected(card);
  setBusy(true, "running " + id);
  try {
    const decision = await post("/api/run", { scenario_id: id });
    renderDecision(decision);
  } catch (e) {
    $("#decision").innerHTML = '<div class="verdict alarm"><div class="level">error</div>'
      + '<p class="action">Could not run this scenario</p><p class="why">' + fmt(e.message) + "</p></div>";
  } finally {
    setBusy(false);
    refreshStats();
  }
}

async function runAll() {
  if (busy) return;
  markSelected(null);
  setBusy(true, "running every scenario");
  try {
    const out = await post("/api/run-all", {});
    renderBatch(out);
  } catch (e) {
    $("#decision").textContent = fmt(e.message);
  } finally {
    setBusy(false);
    refreshStats();
  }
}

async function runAdHoc() {
  const subject = $("#adhoc").value.trim();
  if (!subject || busy) return;
  setBusy(true, "checking " + subject);
  markSelected(null);
  try {
    renderDecision(await post("/api/case", { subject, label: "ad-hoc check" }));
  } catch (e) {
    $("#decision").textContent = fmt(e.message);
  } finally {
    setBusy(false);
    refreshStats();
  }
}

async function revokeConsent() {
  const subject = $("#adhoc").value.trim() || (SPEC.demo_lines[0] || {}).msisdn;
  if (!subject) return;
  const out = await post("/api/consent/revoke", { subject });
  $("#consent-note").textContent =
    out.grants_withdrawn + " grant(s) withdrawn for " + subject
    + ". Run a case on that line now. The agent gets refused at the transport layer.";
}

async function grantConsent() {
  const subject = $("#adhoc").value.trim() || (SPEC.demo_lines[0] || {}).msisdn;
  if (!subject) return;
  await post("/api/consent/grant", { subject });
  $("#consent-note").textContent = "Consent restored for " + subject + ".";
}

/* ---------- rendering a decision ---------- */

function renderDecision(d) {
  const host = $("#decision");
  host.innerHTML = "";

  const verdict = el("div", "verdict " + toneFor(d.level));
  verdict.appendChild(el("div", "level", labelFor(d.level)));
  verdict.appendChild(el("p", "action", d.action || "-"));
  verdict.appendChild(el("p", "why", d.rationale || ""));

  const foot = el("div", "foot");
  const bits = [
    ["subject", d.subject],
    ["spent", d.budget_spent + " / " + d.budget_limit + " units"],
    ["planner", d.planner],
    ["apis", (d.apis_used || []).length + " used"],
    ["confidence", d.confidence],
  ];
  if (d.scenario) bits.unshift(["scenario", d.scenario.title]);
  bits.forEach(([k, v]) => {
    const span = el("span");
    span.appendChild(el("b", null, k + " "));
    span.appendChild(document.createTextNode(fmt(v)));
    foot.appendChild(span);
  });
  verdict.appendChild(foot);

  if (d.guardrail_applied) {
    verdict.appendChild(el("div", "guardrail", "Guardrail held: " + d.guardrail_note));
  }
  if (d.planner_error) {
    verdict.appendChild(el("div", "guardrail", "Planner fallback: " + d.planner_error));
  }
  if (d.met_expectation === false) {
    verdict.appendChild(el("div", "guardrail",
      "This run did not reach the level the scenario expected (" + labelFor(d.scenario.expect_level) + ")."));
  }
  host.appendChild(verdict);

  if (d.scenario && d.scenario.teaches) {
    const teach = el("section");
    teach.appendChild(el("div", "section-title", "what this scenario is for"));
    teach.appendChild(el("p", "help", d.scenario.teaches));
    host.appendChild(teach);
  }

  host.appendChild(renderSteps(d));
  host.appendChild(renderEvidence(d));
  if ((d.skipped || []).length) host.appendChild(renderSkipped(d));
}

function renderSteps(d) {
  const section = el("section");
  section.appendChild(el("div", "section-title", "how the agent got there"));
  const box = el("div", "table-wrap");
  const inner = el("div");
  inner.style.padding = "4px 14px";
  (d.steps || []).forEach((s) => {
    const step = el("div", "step " + s.kind);
    step.appendChild(el("div", "n", s.kind === "decide" ? "=" : String(s.n)));
    const body = el("div");
    const title = s.kind === "decide"
      ? "Submitted a decision"
      : (s.tool || "planner") + (s.kind === "refused" ? " - refused" : s.kind === "error" ? " - failed" : "");
    body.appendChild(el("h4", null, title));
    const meta = [];
    if (s.api) meta.push("CAMARA " + s.api);
    if (s.cost_units) meta.push(s.cost_units + " units");
    if (s.latency_ms) meta.push(s.latency_ms + "ms");
    if (s.source) meta.push(s.source);
    if (meta.length) body.appendChild(el("div", "meta", meta.join("  |  ")));
    if (s.reasoning) body.appendChild(el("div", "reason", '"' + s.reasoning + '"'));
    if (s.observation && Object.keys(s.observation).length) {
      body.appendChild(el("div", "obs", JSON.stringify(s.observation, null, 2)));
    }
    if (s.note) body.appendChild(el("div", "meta", s.note));
    step.appendChild(body);
    inner.appendChild(step);
  });
  if (!(d.steps || []).length) inner.appendChild(el("div", "empty", "no steps recorded"));
  box.appendChild(inner);
  section.appendChild(box);
  return section;
}

function renderEvidence(d) {
  const section = el("section");
  section.appendChild(el("div", "section-title",
    "network evidence (" + (d.evidence || []).length + " CAMARA calls)"));
  const wrap = el("div", "table-wrap");
  const table = el("table");
  table.innerHTML =
    "<thead><tr><th>API</th><th>Operation</th><th>Answer</th><th>Source</th>"
    + "<th>Cost</th><th>Latency</th></tr></thead>";
  const tbody = el("tbody");
  (d.evidence || []).forEach((c) => {
    const tr = el("tr");
    tr.appendChild(el("td", "mono", c.api));
    tr.appendChild(el("td", "mono", c.operation));
    tr.appendChild(el("td", "mono answer", JSON.stringify(c.data)));
    const src = el("td");
    src.appendChild(el("span", "tag " + c.source, c.source));
    tr.appendChild(src);
    tr.appendChild(el("td", "mono", c.cost_units));
    tr.appendChild(el("td", "mono", c.latency_ms + "ms"));
    tbody.appendChild(tr);
  });
  if (!(d.evidence || []).length) {
    const tr = el("tr");
    const td = el("td", "empty", "no calls were needed");
    td.colSpan = 6;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  section.appendChild(wrap);
  return section;
}

function renderSkipped(d) {
  const section = el("section");
  section.appendChild(el("div", "section-title", "checks the agent chose not to buy"));
  const box = el("div", "skipped");
  const list = el("ul");
  list.style.margin = "0";
  list.style.paddingLeft = "18px";
  (d.skipped || []).forEach((s) => {
    const li = el("li");
    li.appendChild(el("span", "name", s.tool));
    li.appendChild(document.createTextNode(" - " + s.reason));
    list.appendChild(li);
  });
  box.appendChild(list);
  section.appendChild(box);
  return section;
}

function renderBatch(out) {
  const host = $("#decision");
  host.innerHTML = "";
  const head = el("div", "verdict " + (out.expectations_met === out.scenarios_run ? "calm" : "warn"));
  head.appendChild(el("div", "level", "batch run"));
  head.appendChild(el("p", "action",
    out.expectations_met + " of " + out.scenarios_run + " scenarios reached the level they expected"));
  head.appendChild(el("p", "why",
    "Spent " + out.total_cost_units + " units. Calling every available check on every case would have cost "
    + out.cost_if_everything_called + " units, so the agent's restraint saved " + out.saved_pct + "%."));
  host.appendChild(head);

  const wrap = el("div", "table-wrap");
  wrap.style.marginTop = "22px";
  const table = el("table");
  table.innerHTML = "<thead><tr><th>Scenario</th><th>Outcome</th><th>Action</th>"
    + "<th>Calls</th><th>Spent</th><th>Met</th></tr></thead>";
  const tbody = el("tbody");
  (out.results || []).forEach((r) => {
    const tr = el("tr");
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => renderDecision(r));
    tr.appendChild(el("td", null, r.scenario.title));
    const lv = el("td");
    lv.appendChild(el("span", "tag", labelFor(r.level)));
    tr.appendChild(lv);
    tr.appendChild(el("td", null, r.action));
    tr.appendChild(el("td", "mono", String((r.evidence || []).length)));
    tr.appendChild(el("td", "mono", String(r.budget_spent)));
    tr.appendChild(el("td", "mono", r.met_expectation ? "yes" : "no"));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
  host.appendChild(el("p", "help", "Click any row to open its full evidence trail."));
}

/* ---------- stats + tools ---------- */

async function refreshStats() {
  try {
    const s = await fetch("/api/stats").then((r) => r.json());
    const map = {
      decisions: s.decisions,
      calls: s.api_calls,
      percase: s.calls_per_decision,
      avgcost: s.avg_cost_per_decision,
      saved: s.saved_vs_calling_everything_pct + "%",
    };
    Object.keys(map).forEach((k) => {
      const node = document.getElementById("m-" + k);
      if (node) node.textContent = fmt(map[k]);
    });
  } catch (e) { /* metrics are decoration */ }
  refreshProvenance();
}

/* Where the answers came from, and who decided. This is the one thing on the
   page that must never flatter the deployment: a simulator run says simulator,
   and a configured model that has not actually planned a case yet says so. */
async function refreshProvenance() {
  try {
    const h = await fetch("/api/health").then((r) => r.json());
    const source = (h.network || {}).effective_source || "simulator";
    const net = $("#badge-network");
    net.textContent = source === "live" ? "live network" : source + " answers";
    net.className = "badge " + (source === "live" ? "live" : "sim");

    const a = h.agent || {};
    const planner = $("#badge-planner");
    if (a.configured_planner !== "gemini") {
      planner.textContent = "policy planner";
      planner.className = "badge";
    } else if (a.model_verified) {
      planner.textContent = "gemini verified";
      planner.className = "badge live";
    } else if (a.last_decision_planner) {
      planner.textContent = "gemini configured, last run fell back";
      planner.className = "badge sim";
    } else {
      planner.textContent = "gemini configured, unproven";
      planner.className = "badge sim";
    }
  } catch (e) { /* the badges are honest or absent, never wrong */ }
}

function renderTools() {
  const wrap = $("#tools");
  const table = el("table");
  table.innerHTML = "<thead><tr><th>Tool</th><th>CAMARA API</th><th>Cost</th><th>Reveals</th>"
    + "<th>Consent scope</th></tr></thead>";
  const tbody = el("tbody");
  (SPEC.tool_catalogue || []).forEach((t) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, t.title));
    tr.appendChild(el("td", "mono", t.api));
    tr.appendChild(el("td", "mono", t.cost_units));
    tr.appendChild(el("td", "mono", t.reveals));
    tr.appendChild(el("td", "mono", t.consent_scope));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

/* ---------- live feed ---------- */

function connectFeed() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  let socket;
  try {
    socket = new WebSocket(proto + "//" + location.host + "/ws");
  } catch (e) { return; }
  socket.onmessage = (msg) => {
    let event;
    try { event = JSON.parse(msg.data); } catch (e) { return; }
    addEvent(event);
  };
  socket.onclose = () => setTimeout(connectFeed, 2500);
}

function addEvent(event) {
  const feed = $("#feed");
  const placeholder = feed.querySelector(".empty");
  if (placeholder) placeholder.remove();
  const kind = event.topic.includes("decided") ? "event--decided"
    : event.topic.includes("consent") ? "event--consent"
    : event.topic.includes("step") ? "event--step" : "";
  const node = el("div", "event " + kind);
  node.appendChild(el("b", null, event.topic));
  const d = event.data || {};
  let line = "";
  if (event.topic === "case.step") {
    line = (d.tool || "planner") + (d.source ? " (" + d.source + ")" : "")
      + (d.cost_units ? " " + d.cost_units + "u" : "");
  } else if (event.topic === "case.decided") {
    line = labelFor(d.level) + " - " + (d.action || "");
  } else if (event.topic === "case.started") {
    line = d.subject + " " + (d.label || "");
  } else {
    line = JSON.stringify(d).slice(0, 120);
  }
  node.appendChild(document.createTextNode(line));
  const when = el("time", null, new Date(event.at).toLocaleTimeString());
  node.appendChild(when);
  feed.prepend(node);
  while (feed.children.length > 80) feed.removeChild(feed.lastChild);
}

/* ---------- boot ---------- */

renderScenarios();
renderTools();
refreshStats();
connectFeed();
$("#run-all").addEventListener("click", runAll);
$("#run-adhoc").addEventListener("click", runAdHoc);
$("#adhoc").addEventListener("keydown", (e) => { if (e.key === "Enter") runAdHoc(); });
$("#revoke").addEventListener("click", revokeConsent);
$("#grant").addEventListener("click", grantConsent);
$("#reset").addEventListener("click", async () => {
  await post("/api/reset", {});
  $("#decision").innerHTML = '<div class="empty">Ledger cleared. Pick a scenario to start again.</div>';
  refreshStats();
});
"""


def render_dashboard(spec: IdeaSpec) -> str:
    """Render the console for one product."""
    from .tools import ToolRegistry

    registry = ToolRegistry(list(spec.policy.tool_names))
    payload = {
        **spec.metadata(),
        "scenarios": [s.summary() for s in spec.scenarios],
        "tool_catalogue": registry.catalogue(),
        "demo_lines": [
            {"msisdn": p.msisdn, "label": p.label, "notes": p.notes}
            for p in spec.all_lines()
        ],
    }

    css = _CSS.replace("#1d6fe0", spec.ui.accent).replace("#e8f0fd", spec.ui.accent_soft)

    metrics = [
        ("decisions", "Decisions made"),
        ("calls", "CAMARA calls"),
        ("percase", "Calls per case"),
        ("avgcost", "Avg cost per case"),
        ("saved", "Saved vs all checks"),
    ]
    metric_html = "".join(
        '<div class="metric"><div class="v" id="m-%s">-</div><div class="k">%s</div></div>'
        % (key, label)
        for key, label in metrics
    )

    limits_html = "".join("<li>%s</li>" % _esc(item) for item in spec.honest_limits)
    consent = spec.consent

    lines_html = "".join(
        '<div class="event"><b>%s</b>%s</div>' % (_esc(p.msisdn), _esc(p.label or p.notes))
        for p in spec.all_lines()[:14]
    )

    return _PAGE \
        .replace("__CSS__", css) \
        .replace("__JS__", _JS) \
        .replace("__SPEC_JSON__", json.dumps(payload).replace("</", "<\\/")) \
        .replace("__NAME__", _esc(spec.name)) \
        .replace("__TAGLINE__", _esc(spec.tagline)) \
        .replace("__KICKER__", _esc(spec.ui.hero_kicker or spec.tagline)) \
        .replace("__THEME_N__", str(spec.theme_number)) \
        .replace("__THEME__", _esc(spec.theme_name)) \
        .replace("__METRICS__", metric_html) \
        .replace("__RUN_ALL__", _esc(spec.ui.run_all_label)) \
        .replace("__SUBJECT_LABEL__", _esc(spec.ui.subject_label)) \
        .replace("__PLACEHOLDER__", _esc(spec.ui.ad_hoc_placeholder)) \
        .replace("__ADHOC_HELP__", _esc(spec.ui.ad_hoc_help)) \
        .replace("__LIMITS__", limits_html) \
        .replace("__CONSENT_MOMENT__", _esc(consent.moment)) \
        .replace("__CONSENT_WHO__", _esc(consent.who_consents)) \
        .replace("__CONSENT_DURATION__", _esc(consent.duration_note)) \
        .replace("__CONSENT_REVOKE__", _esc(consent.revocation)) \
        .replace("__LINES__", lines_html) \
        .replace("__API_COUNT__", str(len(registry.apis())))


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ - operator console</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <div class="brand">
    <h1><span class="dot"></span>__NAME__ <span class="badge">theme __THEME_N__</span></h1>
    <p>__KICKER__</p>
  </div>
  <div class="badges">
    <span class="badge" id="status"></span>
    <span class="badge" id="badge-network" title="Where the CAMARA answers come from"></span>
    <span class="badge" id="badge-planner" title="Which planner is deciding"></span>
    <span class="badge">__API_COUNT__ CAMARA APIs</span>
    <span class="badge on">Nokia Network as Code</span>
  </div>
</header>

<div class="metrics">__METRICS__</div>

<main>
  <div class="col">
    <section>
      <div class="section-title"><span>Scenarios</span></div>
      <div id="scenarios"></div>
      <button class="primary wide" id="run-all">__RUN_ALL__</button>
    </section>

    <section>
      <div class="section-title">Ad-hoc check</div>
      <label for="adhoc">__SUBJECT_LABEL__</label>
      <input id="adhoc" placeholder="__PLACEHOLDER__" autocomplete="off">
      <div class="row" style="margin-top:8px">
        <button id="run-adhoc">Run the agent</button>
        <button class="ghost" id="reset">Clear ledger</button>
      </div>
      <p class="help">__ADHOC_HELP__</p>
    </section>

    <section>
      <div class="section-title">Consent gate</div>
      <p class="help" style="margin-top:0">
        Consent is taken __CONSENT_MOMENT__ from __CONSENT_WHO__. __CONSENT_DURATION__
        __CONSENT_REVOKE__
      </p>
      <div class="row">
        <button id="revoke">Withdraw consent</button>
        <button class="ghost" id="grant">Restore</button>
      </div>
      <p class="help" id="consent-note">
        Withdraw it, then run a case on that line: the agent is refused before a
        request is even built.
      </p>
    </section>
  </div>

  <div class="col mid">
    <div id="decision">
      <div class="empty">Pick a scenario on the left, or run all of them at once.</div>
    </div>

    <section>
      <div class="section-title">Tools this agent may use</div>
      <div class="table-wrap" id="tools"></div>
      <p class="help">
        The planner sees cost and reveal level for every tool and is instructed to
        ask the cheapest, least revealing question that could still change the
        outcome. The budget is enforced by the runtime, not by the model.
      </p>
    </section>

    <section>
      <div class="section-title">What this does not do</div>
      <ul class="limits">__LIMITS__</ul>
    </section>
  </div>

  <div class="col">
    <section>
      <div class="section-title">Live agent feed</div>
      <div class="feed" id="feed">
        <div class="empty">Streams every step the agent takes, over a WebSocket.</div>
      </div>
    </section>
    <section>
      <div class="section-title">Demo lines</div>
      <div class="feed">__LINES__</div>
    </section>
  </div>
</main>

<script>window.__SPEC__ = __SPEC_JSON__;</script>
<script>__JS__</script>
</body>
</html>
"""
