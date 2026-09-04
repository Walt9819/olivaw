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
ok("and still shows the version there, which is the one line that has to be legible",
   /body\.console #verFoot\{display:flex\}/.test(narrow), narrow.slice(0, 600));
// .app is min-height:100vh, so the grid ROW grows to its tallest child: without an
// explicit height the sidebar simply gets taller than the window and its foot goes
// off-screen. Measured at 855px of viewport, the version line landed at y=911 - invisible,
// with no way to scroll to it that did not also drag the panel away.
ok("the sidebar is pinned to the viewport, so its foot cannot fall off the bottom",
   /\.sidebar\{height:100vh; position:sticky; top:0; overflow:hidden\}/.test(CSS), "rule missing");
ok("and the agent list is what scrolls when it does not fit",
   /\.stepper,\.tree\{min-height:0; overflow-y:auto/.test(CSS), "rule missing");

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

// ── the browser panel, painted from real replies ───────────────────────────────
// Each agent drives its own Chrome window now. The state worth pinning is the one where
// two agents are still parked on one endpoint: `connected` is true, so the old panel
// showed a green tick, while every task they browse at the same time ruins the other's
// tab. A healthy-looking panel over a broken feature is the failure mode to avoid.
console.log("\n=== the browser panel says whose window it is ===");
async function paintBrowser(reply) {
  CANNED["browser/status"] = reply;
  CALLS.length = 0;
  OL.goSec("habilidades", "daneel");
  await new Promise((r) => setTimeout(r, 0));
  return getEl("brwBox")._html;
}
let brw = await paintBrowser({
  ok: true, mode: "cdp", connected: true, browser: "Chrome/152", port: 9223,
  data_dir: "C:/h/profiles/daneel/chrome-debug", browser_found: true, shared_with: [] });
ok("a private window reads as this agent's own", /suya y de nadie/.test(brw), brw);
ok("and it names the port, so two windows can be told apart", brw.includes("9223"), brw);

brw = await paintBrowser({
  ok: true, mode: "cdp", connected: true, browser: "Chrome/152", port: 9222,
  data_dir: "C:/h/chrome-debug", browser_found: true, shared_with: ["default"] });
ok("a shared window is called out instead of reported healthy",
   brw.includes("Comparte ventana") && !/suya y de nadie/.test(brw), brw);
ok("it names who it is sharing with", brw.includes("default"), brw);
ok("and does not show the green tick over a broken feature", !brw.includes("\u2705"), brw);
ok("it says which button fixes it", brw.includes("Usar un navegador real"), brw);

brw = await paintBrowser({ ok: true, mode: "headless", browser_found: true, shared_with: [] });
ok("headless still reads as headless", brw.includes("invisible"), brw);

// The window is labelled from whatever the panel sends, so if this stops being sent the
// owner gets two identical blank Chromes and no way to tell which agent owns which.
CANNED["browser/status"] = { ok: true, mode: "headless", browser_found: true };
OL.goSec("habilidades", "daneel");
await new Promise((r) => setTimeout(r, 0));
CALLS.length = 0;
const btn = getEl("brwOn");
let clickErr = null;
try { btn.onclick.call(btn); } catch (e) { clickErr = e; }
await new Promise((r) => setTimeout(r, 0));
const enable = CALLS.filter((c) => c.route === "browser/enable");
ok("pressing 'use a real browser' asks the server to open one", !clickErr && enable.length === 1,
   clickErr ? clickErr.stack : JSON.stringify(CALLS.map((c) => c.route)));
ok("for the agent selected in the sidebar, and no other",
   enable.length === 1 && enable[0].body.profile === "daneel", JSON.stringify(enable));
ok("and tells it whose window it is, so the tab can say so",
   enable.length === 1 && enable[0].body.name === "Daneel", JSON.stringify(enable));

// ── version + updates ──────────────────────────────────────────────────────────
// The owner's complaint was "it does not update itself", and the machinery was fine —
// what was missing was any way to SEE it. So the states pinned here are the ones that
// answer that question on screen: what version am I on, is there a newer one, and is the
// thing that installs it even running.
console.log("\n=== the panel can answer 'am I up to date?' ===");
async function paintUpdate(reply) {
  CANNED["update/status"] = reply;
  CANNED["update/check"] = reply;
  CALLS.length = 0;
  OL.goSec("version", "default");
  await new Promise((r) => setTimeout(r, 0));
  return { box: getEl("updBox")._html, log: getEl("updLog")._html };
}
let upd = await paintUpdate({
  ok: true, current: "1.0.42", latest: "1.0.42", available: false,
  auto_update: true, supervisor_running: true, supervisor_known: true,
  rest_from: 18, rest_until: 24, rest_text: "entre las 18:00 y las 00:00" });
ok("it names the installed version", upd.box.includes("1.0.42"), upd.box);
ok("up to date reads as up to date", /Es la última/.test(upd.box), upd.box);
ok("and says when it would install one on its own",
   upd.box.includes("18:00") && /cuando nadie lo usa/.test(upd.box), upd.box);
ok("the sidebar shows the version too", getEl("verNum").textContent === "Olivaw v1.0.42",
   getEl("verNum").textContent);
ok("and no badge when there is nothing to install", getEl("verBadge").hidden === true);

upd = await paintUpdate({
  ok: true, current: "1.0.30", latest: "1.0.42", available: true,
  changelog: "Cada agente abre su propia ventana.", auto_update: true,
  supervisor_running: true, supervisor_known: true, rest_from: 18, rest_until: 24,
  rest_text: "entre las 18:00 y las 00:00" });
ok("a newer version is offered", /Hay una más nueva/.test(upd.box), upd.box);
ok("with what it actually contains", upd.box.includes("propia ventana"), upd.box);
ok("the sidebar badge appears", getEl("verBadge").hidden === false);
ok("and says which version it would install",
   getEl("verBadge").textContent === "Actualizar a 1.0.42", getEl("verBadge").textContent);

// The state that made the whole feature necessary: nothing is running, so nothing checks
// and nothing installs, however many times the owner presses.
upd = await paintUpdate({
  ok: true, current: "1.0.30", latest: "1.0.42", available: true, auto_update: true,
  supervisor_running: false, supervisor_known: true, rest_from: 4, rest_until: 7 });
ok("a stopped background service is named as the reason",
   /no se actualiza solo/.test(upd.box), upd.box);
ok("and it is not dressed up as healthy", !/✅ <b>Servicio/.test(upd.box), upd.box);

upd = await paintUpdate({
  ok: true, current: "1.0.30", latest: "", available: false, auto_update: true,
  error: "getaddrinfo failed", supervisor_running: true, supervisor_known: true });
ok("an offline machine says so instead of claiming to be current",
   /No pude preguntarle a GitHub/.test(upd.box), upd.box);
ok("and still shows the version it is on", upd.box.includes("1.0.30"), upd.box);

CANNED["update/status"] = { ok: true, current: "1.0.30", latest: "1.0.42", available: true,
                            auto_update: true, supervisor_running: true, supervisor_known: true };
OL.goSec("version", "default");
await new Promise((r) => setTimeout(r, 0));
CALLS.length = 0;
const updBtn = getEl("updNow");
let updErr = null;
try { updBtn.onclick.call(updBtn); } catch (e) { updErr = e; }
await new Promise((r) => setTimeout(r, 0));
ok("pressing 'update now' asks the server, once",
   !updErr && CALLS.filter((c) => c.route === "update/apply").length === 1,
   updErr ? updErr.stack : JSON.stringify(CALLS.map((c) => c.route)));
ok("and it is a machine-wide action, not one agent's",
   !CALLS.filter((c) => c.route === "update/apply")
      .some((c) => Object.prototype.hasOwnProperty.call(c.body, "profile")),
   JSON.stringify(CALLS));

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
