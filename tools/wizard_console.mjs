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
// The html of an element that may not have been rendered. Returns "" instead of throwing:
// a panel that stopped rendering something should fail the assertion about it and let the
// rest of the suite run, rather than crashing and reporting nothing at all.
function htmlOf(id) {
  const n = getEl(id);
  return (n && n._html) || "";
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
    // A real classList, not a no-op: the boot splash covers the whole viewport and is
    // torn down by adding a class and then removing the node. Stubbing that away would
    // make "the splash goes away" untestable, which is the assertion that matters most.
    _cls: new Set(),
    classList: {
      add(c) { e._cls.add(c); },
      remove(c) { e._cls.delete(c); },
      contains: (c) => e._cls.has(c),
      toggle(c) { e._cls.has(c) ? e._cls.delete(c) : e._cls.add(c); },
    },
    _removed: false,
    dataset: {},
    parentNode: null,
    getAttribute: () => null,
    setAttribute() {},
    removeAttribute() {},
    addEventListener() {},
    appendChild() {},
    insertBefore() {},
    remove() { e._removed = true; },
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
  // Ids that exist on the page right now: the static shell, plus whatever any element has
  // been given as innerHTML. It used to look only at #panel and #navTree, which modelled a
  // page one level deep - so a panel that paints a sub-card and then addresses something
  // inside it (as the dashboard does) had that child come back null here while working
  // perfectly in a browser. Walking every node keeps el()-guards meaningful without
  // pretending the DOM is flat.
  const s = new Set(SHELL_IDS);
  for (const n of nodes.values()) {
    if (n && n._html) for (const i of idsIn(n._html)) s.add(i);
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
  // How the agent is reachable, in the shape wizard/connections.py returns.
  "dashboard": {
    ok: true, fast: false,
    session: { engine: "claude", label: "Claude Code", found: true, signed_in: false,
               account: "walt@example.com", plan: "team", state: "expired",
               needs_login: true, expires_in_days: -0.2, expiry_known: true,
               detail: "La sesion de Claude Code caduco." },
    agents: [
      { slug: "default", name: "Agente principal", is_default: true, gateway_running: true,
        bridge_up: true, port: 8790,
        channels: [{ icon: "TG", label: "Telegram", state: "ok", detail: "ok" },
                   { icon: "WA", label: "WhatsApp", state: "off", detail: "no" }] },
      { slug: "daneel", name: "Daneel", is_default: false, gateway_running: false,
        bridge_up: false, port: 8792,
        channels: [{ icon: "TG", label: "Telegram", state: "ok", detail: "ok" }] },
    ],
    update: { version: "1.0.49", available: null },
    supervisor: { running: true, known: true },
  },
  "session/status": { ok: true, engine: "claude", label: "Claude Code", state: "expired",
                      needs_login: true, signed_in: false, detail: "caduco" },
  "session/login": { ok: true, detail: "Abri la ventana de inicio de sesion." },
  "session/verify": { ok: true, session: { signed_in: true, state: "ok" }, tested: true,
                      detail: "Listo: Claude Code tiene sesion y tu agente volvio a responder." },
  "connections/status": {
    ok: true, profile: "default", fast: false,
    gateway: { running: true, detail: "corriendo" },
    brain: { engine: "codex", label: "Codex", source: "bridge", available: true,
             detail: "Codex es el cerebro de este agente" },
    telegram: { ok: true, state: "connected", bot: "mi_bot", owner_locked: true,
                detail: "Telegram conectado como @mi_bot" },
    whatsapp: {
      enabled: true, count: 2, linked_count: 1, max: 6, can_add: true, main: "principal",
      receipts: "applied", detail: "1 de 2 conectados. Falta vincular: Ventas.",
      numbers: [
        { slug: "principal", label: "Numero principal", platform: "whatsapp", port: 3000,
          builtin: true, main: true, linked: true, enabled: true },
        { slug: "ventas", label: "Ventas", platform: "whatsapp_ventas", port: 3001,
          builtin: false, main: false, linked: false, enabled: true },
      ],
    },
    talk: { ok: true, enabled: true, ready: true, port: 8642,
            detail: "Puedes escribirle desde aqui." },
    channels: [
      { icon: "TG", label: "Telegram", state: "ok", detail: "Conectado como @mi_bot" },
      { icon: "WA", label: "WhatsApp", state: "warn", detail: "1 de 2 conectados" },
      { icon: "UI", label: "Desde esta pantalla", state: "ok", detail: "Listo" },
      { icon: "BR", label: "Codex", state: "ok", detail: "Codex es el cerebro" },
    ],
  },
  "talk/status": { ok: true, enabled: true, ready: true, port: 8642,
                   detail: "Puedes escribirle desde aqui." },
  "talk/new": { ok: true, session_id: "sess-1" },
  "talk/send": { ok: true, reply: "Hola, soy tu agente.", session_id: "sess-1" },
  "numbers/add": { ok: true, number: { slug: "soporte", label: "Soporte", port: 3002 } },
  "channel/whatsapp-qr": { ok: true, connected: false,
                           qr: "████\n████\n████\n████\n████\n████\n████\n████\n████", detail: "escanea" },
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
  " targetProfile: targetProfile, render: render, enterSetup: enterSetup," +
  " SOS: SOS, openSos: openSos, sendTurn: sendTurn, paintMsgs: paintMsgs," +
  " showQr: showQr, TALK: TALK, sendTalk: sendTalk, loadConn: loadConn," +
  " startLogin: startLogin, loadDash: loadDash, DASH: DASH, BOOT: BOOT," +
  " get LIVE(){ return LIVE } };\n";
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
ok("it lands on the dashboard, not on one agent", OL.curSec().id === "panel");
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
OL.goSec("home", "default");   // the console opens on the dashboard now; this is one click in
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
// "whatsapp" rather than "canales": the assertion belongs on a panel that actually loads
// state for the selected agent, and the WhatsApp panel is now the one that does. (Before
// the redesign this lived in "canales", which carried the WhatsApp <details>; that section
// is now mail/Slack/webhooks, which render from local state and fetch nothing.)
OL.goSec("whatsapp", "daneel");
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

// ── the SOS console keeps what it was told ─────────────────────────────────────
// Reported from a real machine: "once the response arrived, the message is deleted and the
// conversation is not saved". The server half was proven healthy first - the turn lands on
// disk, the session id is recorded, the listing returns it - so the loss is here, in the
// browser. finishLive() nulls LIVE (the only copy of the answer on screen) and then waits
// for rescue/conversation to hand back the persisted transcript. Any answer that call fails
// to give back takes the conversation with it.
// -- the three things the owner asked for -------------------------------------
console.log("\n=== opening Olivaw shows real progress, not one grey line ===");
{
  // The splash is driven by calls RESOLVING, not by a timer. That is the difference
  // between a slow machine looking busy and looking broken, and it is why this asserts
  // against the recorded calls rather than against elapsed time.
  const shell = fs.readFileSync(SHELL, "utf8");
  ok("the shell carries a boot splash", shell.includes('id="boot"'));
  ok("with a place for the steps", shell.includes('id="bootSteps"'));
  ok("the old single grey line is not the whole story any more",
     shell.includes("boot-mark") && shell.includes("boot-ring"), "no animated mark");
  ok("every step is a real check, named", OL.BOOT.steps.length >= 4,
     JSON.stringify(OL.BOOT.steps.map((s) => s.id)));
  const ids = OL.BOOT.steps.map((s) => s.id);
  ok("and they are the things it actually does",
     ids.includes("state") && ids.includes("agents") && ids.includes("brain") &&
     ids.includes("conn"), JSON.stringify(ids));
  // boot() ran at load; by now every canned call has resolved.
  ok("all of them ticked off", OL.BOOT.at === OL.BOOT.steps.length,
     OL.BOOT.at + "/" + OL.BOOT.steps.length);
  const li = getEl("bootSteps")._html;
  ok("the list renders one row per check", (li.match(/<li/g) || []).length === OL.BOOT.steps.length, li);
  ok("finished rows are marked done", li.includes('class="done"'), li);

  ok("and the splash is dismissed once it is up", OL.BOOT.done === true);
  // The teardown is on a timer (a minimum on screen, then the fade), so wait for it
  // rather than assuming it already ran - and cap the wait so a regression fails the
  // assertion instead of hanging the suite.
  const b = getEl("boot");
  for (let i = 0; i < 60 && !b._removed; i++) await new Promise((r) => setTimeout(r, 50));
  ok("it fades rather than vanishing mid-frame", b._cls.has("gone"), [...b._cls]);
  // Hiding is not enough: it covers the whole viewport, so a transition that never fires
  // would leave an invisible sheet swallowing every click on the console underneath.
  ok("and it is taken OUT of the page, not just hidden", b._removed === true,
     "still in the document");
}

console.log("\n=== the splash cannot outlive the thing it is waiting for ===");
{
  const src = fs.readFileSync(APP, "utf8");
  ok("a stalled check still ends it", /BOOT_MAX_MS/.test(src) &&
     /setTimeout\(bootDone, BOOT_MAX_MS\)/.test(src), "no ceiling on the splash");
  ok("a failed call marks its step instead of hanging on it",
     /bootStep\("brain", true\)/.test(src) && /bootStep\("conn", true\)/.test(src), src.slice(0, 0));
  ok("and a server that never answers takes the splash away before the error",
     /BOOT\.done = false;\s*\n\s*bootDone\(\);/.test(src),
     "the error page would render under a cheerful animation");
  ok("a machine with no agents skips the checks that do not apply",
     /if \(!hasAnyAgent\(\)\) \{ bootStep\("brain"\); bootStep\("conn"\); bootDone\(\); return; \}/.test(src));
  const css = fs.readFileSync(path.join(ROOT, "src", "wizard", "web", "app.css"), "utf8");
  ok("someone who asked for less motion gets the state without the spinning",
     /@media \(prefers-reduced-motion: reduce\)\{[\s\S]*?\.boot-ring,\.boot-o/.test(css),
     "no reduced-motion rule for the splash");
  ok("a failed step is shown, not quietly ticked", /li\.bad \.boot-tick/.test(css));
  for (const cls of ["boot", "boot-mark", "boot-ring", "boot-o", "boot-steps", "boot-tick"]) {
    ok("." + cls + " is styled", css.includes("." + cls), "missing from app.css");
  }
}

console.log("\n=== the dashboard leads with the brain's login ===");
CALLS.length = 0;
OL.goSec("panel");
await new Promise((r) => setTimeout(r, 0));
{
  const pane = getEl("panel")._html;
  ok("it asks for the whole machine in one call",
     CALLS.some((c) => c.route === "dashboard"), CALLS.map((c) => c.route).join(","));
  const dash = CALLS.filter((c) => c.route === "dashboard");
  ok("fast first so it paints at once, then the real one",
     dash.length >= 2 && dash[0].body.fast === true && !dash[1].body.fast,
     JSON.stringify(dash.map((c) => c.body)));
  ok("it has a place for the alert above everything", pane.includes('id="dashAlert"'));
  const alert = htmlOf("dashAlert");
  // An expired brain silences every agent at once. It must be impossible to miss, and it
  // must say what the button will do - not just that something is wrong.
  ok("an expired session raises a real alert", alert.includes("callout err"), alert);
  ok("it says the agents cannot think", /no pueden pensar/i.test(alert), alert);
  ok("and it tells the owner what happens when they click",
     /navegador/i.test(alert) && /compruebo/i.test(alert), alert);
  const card = htmlOf("dashSession");
  ok("the session card names the brain", card.includes("Claude Code"), card);
  ok("and the account it is signed in as", card.includes("walt@example.com"), card);
  ok("the button is primary when a login is actually needed",
     /id="dashLogin"/.test(card) && /btn-primary[^"]*" id="dashLogin"/.test(card), card);
  const grid = htmlOf("dashAgents");
  ok("every agent is on the dashboard",
     grid.includes("Agente principal") && grid.includes("Daneel"), grid.slice(0, 300));
  ok("each one shows its channels at a glance",
     (grid.match(/class="chchip"/g) || []).length === 3, grid.slice(0, 400));
  ok("a paused agent looks different from a running one",
     grid.includes("dot-ok") && grid.includes("dot-off"), grid.slice(0, 400));
  const mach = htmlOf("dashMachine");
  ok("the machine's own state is there too",
     mach.includes("vigilante") && mach.includes("1.0.49"), mach);
}

console.log("\n=== one click: log in, then PROVE it works again ===");
{
  CALLS.length = 0;
  OL.startLogin();
  // Read the box BEFORE awaiting: startLogin sets this synchronously, and the poll then
  // correctly replaces it within a microtask. Asserting after an await was testing which
  // message won a race, not that the owner is told what to do.
  const box = htmlOf("dashLoginBox");
  ok("and says what to do while it is open", /Term[ií]nalo/.test(box), box);
  await new Promise((r) => setTimeout(r, 0));
  ok("clicking starts the brain's own sign-in",
     CALLS.some((c) => c.route === "session/login"), CALLS.map((c) => c.route).join(","));
  // The poll is shallow while the owner is still typing; the expensive end-to-end turn
  // runs once, after the session looks good. Both are asserted because doing only the
  // cheap one would report success without evidence.
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 10));
  const verifies = CALLS.filter((c) => c.route === "session/verify");
  ok("it waits for the login by asking, not by guessing", verifies.length >= 1,
     JSON.stringify(CALLS.map((c) => c.route)));
  ok("the waiting check is the cheap one", verifies[0] && verifies[0].body.deep === false,
     JSON.stringify(verifies[0] && verifies[0].body));
  ok("and once signed in it runs the real end-to-end test",
     verifies.some((c) => c.body.deep === true), JSON.stringify(verifies.map((c) => c.body)));
  const after = htmlOf("dashLoginBox");
  ok("then it says so, in the owner's words",
     /volvi[oó] a responder/.test(after), after);
}

console.log("\n=== the agent's page says how it is reachable, without being asked ===");
CALLS.length = 0;
OL.goSec("home", "default");
await new Promise((r) => setTimeout(r, 0));
{
  const home = getEl("panel")._html;
  ok("there is a 'how they talk to it' card", home.includes('id="connCard"'), home.slice(0, 200));
  const routes = CALLS.map((c) => c.route);
  ok("it asks the server for the connection state", routes.includes("connections/status"));
  // Two passes: local state paints at once, then Telegram is asked for real. A single slow
  // call would leave the card saying "Comprobando..." for as long as the network takes.
  const conn = CALLS.filter((c) => c.route === "connections/status");
  ok("first pass is the fast one, so the card paints immediately",
     conn.length >= 1 && conn[0].body.fast === true, JSON.stringify(conn.map((c) => c.body)));
  ok("and a second pass measures Telegram for real",
     conn.length >= 2 && !conn[1].body.fast, JSON.stringify(conn.map((c) => c.body)));
  const card = htmlOf("connCard");
  ok("every channel is listed with a state dot",
     (card.match(/class="cdot/g) || []).length === 4, card.slice(0, 300));
  ok("Telegram's real state is shown, not just a button", card.includes("@mi_bot"), card);
  ok("WhatsApp says how many numbers are connected", card.includes("1 de 2"), card);
  ok("the brain is named from what the server detected, not hardcoded",
     card.includes("Codex") && !card.includes("Claude"), card);
  ok("each row links to the page that fixes it", card.includes('data-goto="whatsapp"'), card);
}

console.log("\n=== WhatsApp is a page you manage, with every number's state on it ===");
CALLS.length = 0;
OL.goSec("whatsapp", "daneel");
await new Promise((r) => setTimeout(r, 0));
{
  const wa = getEl("panel")._html;
  ok("the panel exists for the selected agent", wa.includes('id="waState"'));
  ok("it asks only about THAT agent",
     CALLS.every((c) => !Object.prototype.hasOwnProperty.call(c.body, "profile") ||
                        c.body.profile === "daneel"),
     JSON.stringify(CALLS.map((c) => [c.route, c.body.profile])));
  const st = htmlOf("waState");
  ok("both numbers are listed",
     st.includes("Numero principal") && st.includes("Ventas"), st.slice(0, 400));
  ok("the connected one and the pending one look different",
     st.includes("dot-ok") && st.includes("dot-warn"), st.slice(0, 400));
  ok("the main line is marked", /principal<\/span>/.test(st), st.slice(0, 400));
  ok("each number offers its own QR",
     (st.match(/data-wa="qr"/g) || []).length === 2, st.slice(0, 400));
  ok("the built-in number cannot be removed, the extra one can",
     (st.match(/data-wa="del"/g) || []).length === 1, st.slice(0, 400));
  ok("and there is a way to add another line", wa.includes('id="nmAdd"'));
  ok("adding one explains it is the same agent",
     /misma personalidad|mismo<\/b> agente/.test(wa), wa.slice(0, 900));
}

console.log("\n=== the QR is asked for BY NUMBER, not 'whichever log is newest' ===");
CALLS.length = 0;
{
  const btn = [...htmlOf("waState").matchAll(/data-wa="qr" data-slug="([a-z]+)"/g)]
    .map((m) => m[1]);
  ok("the buttons carry the number they belong to",
     btn.includes("principal") && btn.includes("ventas"), JSON.stringify(btn));
  OL.showQr("daneel", "ventas");
  const qrCall = CALLS.find((c) => c.route === "channel/whatsapp-qr");
  ok("asking for a QR names the line", qrCall && qrCall.body.number === "ventas",
     JSON.stringify(qrCall && qrCall.body));
  ok("and the agent too", qrCall && qrCall.body.profile === "daneel",
     JSON.stringify(qrCall && qrCall.body));
}

console.log("\n=== talking to the agent from the screen ===");
CALLS.length = 0;
OL.goSec("hablar", "default");
await new Promise((r) => setTimeout(r, 0));
{
  const tk = getEl("panel")._html;
  ok("there is a chat box", tk.includes('id="tkInput"') && tk.includes('id="tkSend"'));
  ok("it asks whether it is available", CALLS.some((c) => c.route === "talk/status"));
  ok("being ready opens a conversation", CALLS.some((c) => c.route === "talk/new"));
  const st = htmlOf("tkState");
  ok("it says it listens only on this machine", st.includes("127.0.0.1"), st);
  OL.TALK.sid = "sess-1";
  OL.TALK.ready = true;
  getEl("tkInput").value = "hola";
  CALLS.length = 0;
  OL.sendTalk();
  const sent = CALLS.find((c) => c.route === "talk/send");
  ok("sending reaches the agent, with the session",
     sent && sent.body.session_id === "sess-1", JSON.stringify(sent && sent.body));
  ok("and the message is what was typed", sent && sent.body.text === "hola");
  ok("the input is cleared so the message is not sent twice", getEl("tkInput").value === "");
  await new Promise((r) => setTimeout(r, 0));
  const log = htmlOf("tkLog");
  ok("the question stays on screen", log.includes("hola"), log);
  ok("and the answer arrives next to it", log.includes("Hola, soy tu agente"), log);
}

console.log("\n=== the new panels have the styles they render against ===");
{
  // A class the CSS does not define renders as an unstyled div - which for a status dot
  // means an invisible one, i.e. exactly the "I cannot see whether it is connected" this
  // work exists to fix. So the classes the panels emit are checked against the stylesheet.
  for (const cls of ["cdot", "dot-ok", "dot-warn", "dot-off", "dot-wait", "connlist",
                     "walist", "cdet", "clink", "qrbox", "chatlog", "bub", "btn-xs",
                     "dashgrid", "dashcard", "chchips", "chchip"]) {
    ok("." + cls + " is defined", CSS.includes("." + cls), "missing from app.css");
  }
  ok("the waiting dot animates, so 'checking' cannot be mistaken for 'off'",
     /\.dot-wait\{[^}]*animation/.test(CSS),
     CSS.slice(CSS.indexOf(".dot-wait"), CSS.indexOf(".dot-wait") + 120));
  // Block-drawing QR: too much line-height and the squares stop touching, and a phone
  // cannot read the code.
  ok("the QR keeps its squares touching", /\.qr\{line-height:1/.test(CSS));
  ok("and shrinks on a narrow screen rather than overflowing",
     /@media\(max-width:520px\)\{ \.qr\{font-size:7px\} \}/.test(CSS));
  ok("the chat log scrolls instead of pushing the page down",
     /\.chatlog\{[^}]*overflow-y:auto/.test(CSS));
  ok("the two sides of the conversation are told apart",
     CSS.includes(".bub.me") && CSS.includes(".bub.them"));
  // Every colour here comes from the existing tokens, so dark mode needs no second set.
  ok("the states use the shared colour tokens, not hardcoded hex",
     !/\.dot-(ok|warn)\{[^}]*#[0-9a-f]{3,6}/i.test(CSS), "hardcoded colour in a state dot");
}

console.log("\n=== the SOS console does not lose what it just showed you ===");
let SOS_TURNS = [];
let SOS_CONV_OK = true;
CANNED["rescue/context"] = { ok: true, version: "1.0.44", brain: "Claude Code",
                             engine: "claude" };
CANNED["rescue/conversations"] = { ok: true, conversations: [] };
const realFetch = global.fetch;
global.fetch = (url, opt) => {
  const route = String(url).replace(/^\/api\//, "");
  let body = {};
  try { body = JSON.parse((opt && opt.body) || "{}"); } catch (e) { /* keep {} */ }
  if (route === "rescue/start") {
    CALLS.push({ route, body });
    return Promise.resolve({ json: () => Promise.resolve({
      ok: true, job_id: "job1", conversation_id: "aabbccddeeff0011",
      title: body.question.slice(0, 30), engine: "claude", brain: "Claude Code",
      mode: "diagnose" }) });
  }
  if (route === "rescue/poll") {
    CALLS.push({ route, body });
    // The turn is persisted by the time the job reports done - that is what the server
    // does in its finally, and it was verified against the real store.
    SOS_TURNS = [{ ts: 1, question: "por que falla mi agente", mode: "diagnose",
                   reply: "Esta es la respuesta.", events: [] }];
    return Promise.resolve({ json: () => Promise.resolve({
      ok: true, events: [{ kind: "text", text: "Esta es la respuesta." }], cursor: 1,
      done: true, reply: "Esta es la respuesta.",
      conversation_id: "aabbccddeeff0011" }) });
  }
  if (route === "rescue/conversation") {
    CALLS.push({ route, body });
    return Promise.resolve({ json: () => Promise.resolve(
      SOS_CONV_OK
        ? { ok: true, conversation: { id: "aabbccddeeff0011", title: "por que falla",
                                      turns: SOS_TURNS, resumable: true } }
        : { ok: false, detail: "no pude abrirla" }) });
  }
  return realFetch(url, opt);
};
async function settle(n) {
  for (let i = 0; i < (n || 40); i++) await new Promise((r) => setTimeout(r, 5));
}

OL.openSos();
await settle(10);
OL.sendTurn("por que falla mi agente");
ok("the question appears while it is working",
   getEl("rescueLive")._html.includes("por que falla mi agente"),
   getEl("rescueLive")._html.slice(0, 160));
await settle(60);
ok("when the answer arrives, the question is still on screen",
   getEl("rescueMsgs")._html.includes("por que falla mi agente"),
   "msgs=" + getEl("rescueMsgs")._html.slice(0, 200));
ok("and so is the answer",
   getEl("rescueMsgs")._html.includes("Esta es la respuesta"),
   "msgs=" + getEl("rescueMsgs")._html.slice(0, 200));
ok("the live block handed its content over rather than both showing it",
   getEl("rescueLive")._html === "", getEl("rescueLive")._html.slice(0, 120));
ok("the conversation is remembered, so the next question continues it",
   OL.SOS.conv && OL.SOS.conv.id === "aabbccddeeff0011", JSON.stringify(OL.SOS.conv));

// The failure that loses work: the reload call does not come back with the turn.
SOS_CONV_OK = false;
OL.SOS.conv = null; OL.SOS.turns = [];
getEl("rescueMsgs")._html = ""; getEl("rescueLive")._html = "";
OL.sendTurn("segunda pregunta");
await settle(80);
const kept = getEl("rescueMsgs")._html + getEl("rescueLive")._html;
ok("if the transcript cannot be re-read, the answer is kept anyway",
   kept.includes("segunda pregunta") && kept.includes("Esta es la respuesta"),
   "both boxes ended up empty: " + JSON.stringify(kept.slice(0, 200)));

// The exact race that caused the report: the server answers OK but with a transcript that
// does not contain the turn yet, because `done` used to be published before the write. The
// UI must not treat a shorter transcript as the truth and drop what it is holding.
SOS_CONV_OK = true;
SOS_TURNS = [];
OL.SOS.conv = null; OL.SOS.turns = [];
getEl("rescueMsgs")._html = ""; getEl("rescueLive")._html = "";
const realPoll = global.fetch;
global.fetch = (url, opt) => {
  if (String(url).includes("rescue/poll")) {
    return Promise.resolve({ json: () => Promise.resolve({
      ok: true, events: [{ kind: "text", text: "Esta es la respuesta." }], cursor: 1,
      done: true, reply: "Esta es la respuesta.",
      conversation_id: "aabbccddeeff0011" }) });   // note: SOS_TURNS stays empty
  }
  return realPoll(url, opt);
};
OL.sendTurn("tercera pregunta");
await settle(80);
const raced = getEl("rescueMsgs")._html + getEl("rescueLive")._html;
ok("a transcript that lags behind does not erase the answer",
   raced.includes("tercera pregunta") && raced.includes("Esta es la respuesta"),
   "lost it: " + JSON.stringify(raced.slice(0, 200)));
global.fetch = realFetch;

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
