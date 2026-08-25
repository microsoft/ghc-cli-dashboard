// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
//
// dom_harness.js - minimal fake-DOM runner used by the security regression
// tests to execute the inline <script> block from a generated
// usage_dashboard.html the same way a real browser would (module-load,
// then automatic render() at the bottom), without needing a browser or
// jsdom. It captures every element's resulting innerHTML/textContent plus
// the clipboard-copy TSV text so Python tests can assert hostile payloads
// never become live markup/formulas.
//
// Usage: node dom_harness.js path/to/dashboard.html ['{"localStorage json key":"value",...}']
// The optional 3rd argument pre-seeds localStorage BEFORE the inline script
// runs - e.g. to simulate a returning visitor who already excluded a
// project/model/provider via a checkbox on a previous visit (see the
// provider-filter tests, which use this to prove exclusions saved under the
// STORAGE_KEY_PROJECT/MODEL/PROVIDER keys actually change render() output).
// Prints one line of JSON: { elements: { id: {innerHTML, textContent} }, tsv }
"use strict";
const fs = require("fs");

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error("usage: node dom_harness.js <html-file> [localStorage-seed-json]");
  process.exit(1);
}
const localStorageSeedArg = process.argv[3];
const html = fs.readFileSync(htmlPath, "utf8");
const scripts = html.match(/<script>[\s\S]*?<\/script>/g) || [];
if (scripts.length < 2) {
  console.error("expected at least 2 <script> blocks (plotly.js + dashboard), found " + scripts.length);
  process.exit(2);
}
// The Plotly bundle is embedded first; the dashboard's own inline script is last.
const mainScript = scripts[scripts.length - 1].replace(/^<script>/, "").replace(/<\/script>$/, "");

const vm = require("vm");

const ELEMENTS = {};
function makeClassList() {
  const set = new Set();
  return {
    toggle(name, force) {
      if (force === undefined) {
        if (set.has(name)) set.delete(name); else set.add(name);
      } else if (force) set.add(name); else set.delete(name);
    },
    add(name) { set.add(name); },
    remove(name) { set.delete(name); },
    contains(name) { return set.has(name); },
  };
}
function makeFakeEl(id) {
  return {
    id,
    _innerHTML: "",
    _textContent: "",
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) { this._innerHTML = v; },
    get textContent() { return this._textContent; },
    set textContent(v) { this._textContent = v; },
    classList: makeClassList(),
    style: {},
    value: "",
    checked: false,
    title: "",
    addEventListener() {},
    focus() {},
    setSelectionRange() {},
    querySelectorAll() { return []; },
    appendChild() {},
    remove() {},
    select() {},
  };
}

const documentStub = {
  getElementById(id) {
    if (!ELEMENTS[id]) ELEMENTS[id] = makeFakeEl(id);
    return ELEMENTS[id];
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return makeFakeEl("__tmp__"); },
  body: { appendChild() {} },
};

const storageMap = new Map();
if (localStorageSeedArg) {
  const seed = JSON.parse(localStorageSeedArg);
  for (const [k, v] of Object.entries(seed)) storageMap.set(k, String(v));
}
const localStorageStub = {
  getItem(k) { return storageMap.has(k) ? storageMap.get(k) : null; },
  setItem(k, v) { storageMap.set(k, String(v)); },
  removeItem(k) { storageMap.delete(k); },
};

// Records the last figure passed to Plotly.react() per div id, so Python
// tests can assert on trace data and layout (legend visibility, no-data
// annotations) without a real Plotly renderer.
const PLOTLY_FIGURES = {};
const PlotlyStub = {
  react(id, data, layout) { PLOTLY_FIGURES[id] = { data, layout }; },
  Plots: { resize() {} },
};

const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  navigator: { clipboard: undefined },
  Plotly: PlotlyStub,
  console,
  setTimeout,
  clearTimeout,
  Date, Set, Map, Array, JSON, Math, String, Number, Boolean,
  parseFloat, parseInt, isNaN,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
// Append a test-only trailer (same script/lexical scope, so it can still see
// top-level `const RAW = ...`) that mirrors RAW onto `window` - top-level
// const/let bindings are NOT copied onto the contextified sandbox object by
// Node's vm module (only `var`/function declarations are), so without this
// `sandbox.RAW` would otherwise be undefined outside the script.
const mainScriptWithTestHook = mainScript + "\nwindow.__RAW_FOR_TESTS = (typeof RAW !== 'undefined') ? RAW : null;\n";
vm.runInContext(mainScriptWithTestHook, sandbox, { filename: "dashboard-inline.js" });

let tsv = "";
try {
  tsv = typeof sandbox.taskTableText === "function" ? sandbox.taskTableText() : "";
} catch (e) {
  tsv = "ERROR:" + e.message;
}

// Direct probes of the two hardening helpers, independent of the dataset used
// to drive render(), so every hostile-payload class is covered regardless of
// what happens to be the "top" project/model in a given test's fixture data.
const probeInputs = [
  "=1+1", "+1", "-1", "@SUM(1)", "plain",
  "a\tb\nc\rd",
  "<img src=x onerror=alert(1)>",
  "</script><script>alert(1)</script>",
  "He said \"hi\" & 'bye'",
];
const probes = { sanitizeForSpreadsheet: {}, escapeHtml: {} };
for (const s of probeInputs) {
  probes.sanitizeForSpreadsheet[s] = typeof sandbox.sanitizeForSpreadsheet === "function" ? sandbox.sanitizeForSpreadsheet(s) : null;
  probes.escapeHtml[s] = typeof sandbox.escapeHtml === "function" ? sandbox.escapeHtml(s) : null;
}

// Direct probes of the cost-coverage/token-composition helpers over the
// dataset's full, unfiltered RAW array - lets Python tests assert on the
// underlying aggregate math (no divide-by-zero, correct full/partial/
// unknown classification, category totals) independent of chart rendering
// (Plotly itself is stubbed out above and does not compute anything).
let coverage = null, composition = null;
try {
  const rawForTests = sandbox.__RAW_FOR_TESTS;
  if (typeof sandbox.computeCostCoverage === "function" && Array.isArray(rawForTests)) {
    coverage = sandbox.computeCostCoverage(rawForTests);
  }
  if (typeof sandbox.computeTokenComposition === "function" && Array.isArray(rawForTests)) {
    composition = sandbox.computeTokenComposition(rawForTests);
  }
} catch (e) {
  coverage = "ERROR:" + e.message;
}

const out = {
  elements: {}, tsv, probes, coverage, composition,
  valueEntries: sandbox.__debugValueEntries ?? null,
  providerEntries: sandbox.__debugProviderEntries ?? null,
  providerAllZero: sandbox.__debugProviderAllZero ?? null,
  figures: PLOTLY_FIGURES,
  filteredCount: sandbox.__debugFilteredCount ?? null,
};
for (const [id, el] of Object.entries(ELEMENTS)) {
  out.elements[id] = { innerHTML: el._innerHTML, textContent: el._textContent };
}
console.log(JSON.stringify(out));
