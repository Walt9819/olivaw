/* Runs the wizard's front-end for real, in Node, against a DOM just real enough.
 *
 * `node --check` only proves app.js parses. It cannot catch the mistakes that actually
 * happen in this file: a section renderer that reaches for a variable which used to be a
 * local of rChannels(), a handler bound to an element this page did not render, or - the
 * expensive one - a panel that saves its settings to the WRONG AGENT because the profile
 * it sends comes from somewhere other than the sidebar selection.
 *
 * So: stub the DOM, stub fetch, load app.js, then drive it section by section and agent
 * by agent and look at what it renders and what it asks the server for.
 *
 * getElementById deliberately answers only for ids that are on the page right now (the
 * static shell in index.html, plus whatever the last render wrote into #panel/#navTree).
 * Anything else is null - which is what makes app.js's el()-guards meaningful here
 * instead of decorative.
 *
 * Run: node tools/wizard_console.mjs <repo-root>
 */
import fs from "fs";
import path from "path";

const ROOT = process.argv[2] || process.cwd();
const APP = path.join(ROOT, "src", "wizard", "web", "app.js");
const SHELL = path.join(ROOT, "src", "wizard", "web", "index.html");

let PASS = 0;
const FAILED = [];
function ok(name, cond, extra) {
  if (cond) { PASS++; console.log("  ok   " + name); }
  else { FAILED.push(name); console.log("  FAIL " + name + (extra ? "\n       " + String(extra).slice(0, 400) : "")); }
}
function idsIn(html) {
  return [...String(html).matchAll(/id="([A-Za-z0-9_]+)"/g)].map((m) => m[1]);
}

// ── the DOM ────────────────────────────────────────────────────────────────────
const SHELL_IDS = new Set(idsIn(fs.readFileSync(SHELL, "utf8")));
const nodes = new Map();

function mkEl(id) {
  const e = {
    id,
    _html: "",
    value: "",
    textContent: "",
    className: "",
    hidden: false,
    disabled: false,
    checked: false,
    style: new Proxy({}, { get: () => "", set: () => true }),
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    dataset: {},
    parentNode: null,
    getAttribute: () => null,
    setAttribute() {},
    removeAttribute() {},
    addEventListener() {},
    appendChild() {},
    insertBefore() {},
    remove() {},
    focus() {},
    scrollIntoView() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    get innerHTML() { return e._html; },
    set innerHTML(v) { e._html = String(v == null ? "" : v); },
  };
  return e;
}
function live() {
  const s = new Set(SHELL_IDS);
  for (const key of ["panel", "navTree"]) {
    const n = nodes.get(key);
    if (n) for (const i of idsIn(n._html)) s.add(i);
  }
  return s;
}
function getEl(id) {
  if (!live().has(id)) return null;
  if (!nodes.has(id)) nodes.set(id, mkEl(id));
  return nodes.get(id);
}
const body = mkEl("body");
global.document = {
  body,
  documentElement: mkEl("html"),
  getElementById: getEl,
  querySelector: (sel) => (sel === ".panel-wrap" ? mkEl("panel-wrap") : null),
  querySelectorAll: () => [],
  createElement: (t) => mkEl("new-" + t),
  addEventListener() {},
};
global.window = global;
global.location = { search: "", hash: "", href: "http://127.0.0.1:1/" };
global.localStorage = {
  _d: {},
  getItem(k) { return this._d[k] == null ? null : this._d[k]; },
  setItem(k, v) { this._d[k] = String(v); },
  removeItem(k) { delete this._d[k]; },
};
global.confirm = () => true;
global.alert = () => {};

// ── the server ─────────────────────────────────────────────────────────────────
const DEFAULT_AGENT = {
  slug: "default", name: "Agente principal", profile: "default", port: 8790,
  workspace: "C:/ws/main", is_default: true, gateway_running: true, bridge_up: true,
  owners: [{ user_id: "111", name: "Walt" }],
};
const EXTRA_AGENT = {
  slug: "daneel", name: "Daneel", profile: "daneel", port: 8791,
  workspace: "C:/ws/daneel", is_default: false, gateway_running: true, bridge_up: true,
  owners: [{ user_id: "222", name: "Walt" }],
};
const AGENTS = { default: DEFAULT_AGENT, extra: [EXTRA_AGENT] };

const CALLS = [];
const CANNED = {
  state: {
    ok: true,
    providers: [{ id: "claude-code", label: "Claude Code", status: "ready", steps: [],
                  cli_key: "claude", cli_label: "Claude Code", engine: "claude" }],
    usecases: [{ id: "u1", label: "Uno", blurb: "b", icon: "1" }],
    hermes: {}, agents: AGENTS,
    smtp_providers: [{ id: "gmail", label: "Gmail", host: "smtp.gmail.com", port: 587,
                       secure: "starttls", note: "n", link: "https://x" },
                     { id: "other", label: "Otro", host: "", port: 587, secure: "starttls", note: "" }],
    image_options: [{ label: "Gratis", note: "n", free: true, link: "" }],
    google_presets: { gmail: { label: "Gmail", smtp_host: "smtp.gmail.com",
                               imap_host: "imap.gmail.com", note: "n", link: "https://x" } },
    default_provider: "claude-code",
    defaults: { install_dir: "C:/inst", workspace: "C:/ws/main", python: "py",
                claude: "claude", node: "node", hermes: "hermes", hermes_config: "cfg",
                repo: "o/r" },
  },
  "agents/list": { ok: true, ...AGENTS },
  "policy/get": {
    ok: true, policy: { mode: "both", idle_minutes: 150, at_hour: 4, compact: true, compact_at: 0.1 },
    presets: [{ id: "recomendado", label: "Recomendado", note: "n", values: {} }],
    defaults: { mode: "both", idle_minutes: 150, at_hour: 4, compact: true, compact_at: 0.1 },
    preset: "recomendado", context_length: 200000, configured: true,
  },
  "images/status": { ok: true, engine: "claude", recommended: "r1",
    routes: [{ id: "r1", label: "Gratis", cost: "0", note: "n", ready: true, available: true }] },
  "browser/status": { ok: true, mode: "headless", browser_found: true },
  "browser/delegation": { ok: true, ready: true, detail: "listo" },
  "intercom/status": { ok: true, enabled: true, max_turns: 8, hourly_limit: 30,
    agents: [{ name: "Agente principal", reachable: true }, { name: "Daneel", reachable: true }],
    quota: { used: 1, limit: 30 }, threads: [{ id: "abc", from: "a", to: "b", turns: 2 }] },
  "selfcare/status": { ok: true, jobs: [], installed: false },
  "obsidian/status": { ok: true, installed: false, steps: [] },
  "proposals/list": { ok: true, items: [], learning: {} },
  "channel/escalation-get": { ok: true, catalog: [{ key: "k1", label: "Uno", description: "d", priority: "alta" }],
    prefs: { enabled: false, reasons: [], custom: [] }, telegram_ready: true, telegram_detail: "" },
  "provider/check": { ok: true, path: "C:/claude.exe", detail: "listo" },
  check: { ok: true, path: "C:/hermes.exe", detail: "listo" },
};
global.fetch = (url, opt) => {
  const route = String(url).replace(/^\/api\//, "");
  let bodyObj = {};
  try { bodyObj = JSON.parse((opt && opt.body) || "{}"); } catch (e) { /* keep {} */ }
  CALLS.push({ route, body: bodyObj });
  const data = CANNED[route] || { ok: true, detail: "stub" };
  return Promise.resolve({ json: () => Promise.resolve(data) });
};

// ── load app.js with a hook onto its internals ─────────────────────────────────
const src = fs.readFileSync(APP, "utf8");
const cut = src.lastIndexOf("})();");
if (cut < 0) { console.log("  FAIL app.js is not the expected IIFE"); process.exit(1); }
const HOOK = "\n  globalThis.__ol = { CONSOLE: CONSOLE, S: S, META: META, STEPS: STEPS," +
  " curSec: curSec, curAgent: curAgent, allAgents: allAgents, goSec: goSec," +
  " hasAnyAgent: hasAnyAgent, unfold: unfold, secPolicy: secPolicy, rChannels: rChannels," +
  " targetProfile: targetProfile, render: render, enterSetup: enterSetup };\n";
const hooked = src.slice(0, cut) + HOOK + src.slice(cut);

let loadErr = null;
try {
  new Function(hooked)();
} catch (e) {
  loadErr = e;
}
ok("app.js loads and runs (no reference errors at load)", !loadErr, loadErr && loadErr.stack);
if (loadErr) { report(); }

await new Promise((r) => setTimeout(r, 0));   // let boot()'s fetch resolve
const OL = globalThis.__ol;

// ── what opens ─────────────────────────────────────────────────────────────────
console.log("\n=== with agents on the machine, the console opens, not the stepper ===");
ok("view is the console", OL.S.view === "console", OL.S.view);
ok("it lands on the selected agent's own page", OL.curSec().id === "home");
ok("the stepper is hidden and the tree is shown",
   getEl("stepper").hidden === true && getEl("navTree").hidden === false);
ok("the sidebar lists every agent on the machine",
   getEl("navTree")._html.includes("Agente principal") && getEl("navTree")._html.includes("Daneel"));
ok("and offers creating another one", getEl("navTree")._html.includes('id="treeNew"'));
const CSS = fs.readFileSync(path.join(ROOT, "src", "wizard", "web", "app.css"), "utf8");
ok("the footer's Back/Continue is out of the way (console mode)",
   /body\.console \.nav\{display:none\}/.test(CSS));
// Narrow windows hide the wizard's sidebar, which is only decoration there. In the
// console that sidebar IS the navigation: hiding it leaves no way to reach any agent.
const narrow = CSS.slice(CSS.indexOf("@media(max-width:860px)"));
ok("a narrow window keeps the console's sidebar reachable",
   /body\.console \.sidebar\{display:block/.test(narrow) && /max-height:42vh/.test(narrow),
   narrow.slice(0, 400));

console.log("\n=== the agent's page says how it is and what else you can change ===");
const home = getEl("panel")._html;
ok("it names the agent", home.includes("Agente principal"));
ok("it says whether it is running", /Encendido|Pausado|No pude comprobarlo/.test(home));
ok("it says who may command it", home.includes("Walt"));
ok("it shows its workspace and profile", home.includes("C:/ws/main") && home.includes("default"));
ok("it offers the other sections as cards", (home.match(/class="seccard"/g) || []).length >= 4);
ok("nothing rendered as the string 'undefined'", !home.includes("undefined"), home.slice(0, 300));

// ── every section, for every agent ─────────────────────────────────────────────
console.log("\n=== every console section renders for every agent ===");
const SECS = OL.CONSOLE.map((x) => x.id);
for (const slug of ["default", "daneel"]) {
  for (const id of SECS) {
    CALLS.length = 0;
    let err = null;
    try { OL.goSec(id, slug); } catch (e) { err = e; }
    const html = getEl("panel")._html;
    ok(slug + " / " + id + " renders without throwing", !err, err && err.stack);
    ok(slug + " / " + id + " produced a page", !err && html.length > 80, html.length);
    ok(slug + " / " + id + " has no stray 'undefined'", !html.includes("undefined"));
  }
}

// ── the point of the whole redesign ────────────────────────────────────────────
console.log("\n=== a panel configures the agent selected in the sidebar, and no other ===");
const AGENT_SECS = OL.CONSOLE.filter((x) => x.scope === "agent").map((x) => x.id);
for (const id of AGENT_SECS) {
  CALLS.length = 0;
  OL.goSec(id, "daneel");
  const withProf = CALLS.filter((c) => Object.prototype.hasOwnProperty.call(c.body, "profile"));
  const wrong = withProf.filter((c) => c.body.profile !== "daneel");
  ok("daneel / " + id + ": every per-agent request carries profile=daneel",
     !wrong.length, wrong.map((c) => c.route + " -> " + JSON.stringify(c.body.profile)).join(", "));
}
for (const id of AGENT_SECS) {
  CALLS.length = 0;
  OL.goSec(id, "default");
  const wrong = CALLS.filter((c) => Object.prototype.hasOwnProperty.call(c.body, "profile") &&
                                    c.body.profile !== null);
  ok("default / " + id + ": the main agent is addressed as bare hermes (profile=null)",
     !wrong.length, wrong.map((c) => c.route + " -> " + JSON.stringify(c.body.profile)).join(", "));
}
CALLS.length = 0;
OL.goSec("canales", "daneel");
ok("selecting an agent actually reaches the server for that agent",
   CALLS.some((c) => c.body.profile === "daneel"), JSON.stringify(CALLS.map((c) => c.route)));

console.log("\n=== switching agents keeps you on the same panel ===");
OL.goSec("gasto", "default");
const before = OL.curSec().id;
OL.goSec(OL.curSec().id, "daneel");
ok("the panel does not jump back to the summary", OL.curSec().id === before);
ok("but the agent did change", OL.curAgent().slug === "daneel");

// ── the shared things are not pretending to be per-agent ───────────────────────
console.log("\n=== team sections do not quietly touch one agent's settings ===");
for (const id of OL.CONSOLE.filter((x) => x.scope === "team").map((x) => x.id)) {
  CALLS.length = 0;
  OL.goSec(id, "daneel");
  const perAgent = CALLS.filter((c) => /^(channel|policy)\//.test(c.route));
  ok(id + ": asks for nothing per-agent", !perAgent.length,
     perAgent.map((c) => c.route).join(", "));
}

console.log("\n=== a section only wires what its own page rendered ===");
OL.goSec("rutinas", "default");
ok("no WhatsApp element exists on the routines page", getEl("waPair") === null);
ok("no policy element exists on the routines page", getEl("polSave") === null);
ok("the routines box does exist there", getEl("selfcareBox") !== null);
CALLS.length = 0;
OL.goSec("rutinas", "default");
ok("and it does not fetch the panels it is not showing",
   !CALLS.some((c) => /escalation|whatsapp|policy|images|browser|intercom/.test(c.route)),
   CALLS.map((c) => c.route).join(", "));

// ── the wizard's long page still contains everything ───────────────────────────
console.log("\n=== the wizard's own last step still shows every section ===");
OL.S.applied = true;
const all = OL.rChannels();
for (const pill of ["polPill", "brwPill", "histPill", "capPill", "icPill", "mcpPill",
                    "waPill", "escPill", "gwPill", "slackPill", "whPill", "smtpPill"]) {
  ok("the setup page still renders " + pill, all.includes('id="' + pill + '"'));
}
for (const box of ["obsBox", "selfcareBox", "propBox"]) {
  ok("the setup page still renders " + box, all.includes('id="' + box + '"'));
}
ok("and each section appears exactly once",
   (all.match(/id="waPill"/g) || []).length === 1 && (all.match(/id="polPill"/g) || []).length === 1);

console.log("\n=== unfolding a single-section page keeps the fine print folded ===");
const folded = OL.secPolicy();
const opened = OL.unfold(folded);
ok("the outer accordion is gone",
   (folded.match(/<details/g) || []).length - (opened.match(/<details/g) || []).length === 1);
ok("the inner fold survives", opened.includes("Ajustes finos"));
ok("and the closing tags stay balanced",
   (opened.match(/<details/g) || []).length === (opened.match(/<\/details>/g) || []).length);
ok("its own explanation is still on the page", opened.includes("empieza una conversación nueva"));

// ── the first run must still be the stepper ────────────────────────────────────
console.log("\n=== a machine with no agents still gets the guided install ===");
OL.META.agents = { default: null, extra: [] };
ok("no agents means no console", OL.hasAnyAgent() === false);
OL.S.view = "setup"; OL.S.step = 0;
let err2 = null;
try { OL.render(); } catch (e) { err2 = e; }
ok("the wizard's welcome step still renders", !err2, err2 && err2.stack);
ok("the stepper is back", getEl("stepper").hidden === false && getEl("navTree").hidden === true);
ok("and it does not offer 'go to my agents' when there are none",
   getEl("navNext").textContent !== "Ir a mis agentes \u2192", getEl("navNext").textContent);

report();

function report() {
  console.log("\n%d passed, %d failed", PASS, FAILED.length);
  for (const f of FAILED) console.log("  - " + f);
  process.exit(FAILED.length ? 1 : 0);
}
