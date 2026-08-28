<!--
  olivaw — after R. Daneel Olivaw, the robot who spends twenty thousand years quietly
  keeping humanity on course. This kit does the small, patient version: keeping a helpful
  agent alive and current on someone's machine without them ever noticing the machinery.
  "It is the chief characteristic of the religion of science that it works." — Salvor Hardin
-->
# Hermes Bridge

Run the **Hermes Agent** on a **coding-CLI subscription as its model** — no API key — reachable
over Telegram (or any Hermes platform). The brain is your choice: **Claude Code** (default) or
**OpenAI Codex**. Includes local GPU **STT** (voice notes) and **TTS** (voice replies), an `hqctl`
ops CLI, and a **silent auto-updater** so non-technical users stay current without ever touching a
terminal.

```
Telegram ⇄ Hermes gateway ⇄ bridge (localhost:8790) ⇄ claude -p     (Claude Code = the brain)
                                     │                └─ or ─┐
                                     │                  codex exec  (Codex = the brain)
                                     ▲
                          supervisor: keeps it alive + auto-updates when idle
```

Everything else is engine-agnostic: the same tool loop, session resume, routing, wizard, SOS
console, nightly consolidation and weekly self-review run on either brain.

## 🚀 Empezar en ~15 minutos (guía para no-técnicos)

> Esta guía es para ti si alguien te compartió **olivaw** y quieres tu propio asistente.
> No necesitas saber programar. El instalador se encarga de **todo** solo.

**Lo único que necesitas tener tú:**
- Una **cuenta de pago de Claude** (plan Pro o Max) → [claude.com](https://claude.com). Es el “cerebro” de tu agente; iniciarás sesión una vez.
- Una **cuenta de Hermes**.

Todo lo demás (Python, Node, Claude Code y el propio Hermes) **se descarga e instala solo** — tú no instalas nada técnico a mano. La primera vez puede tardar varios minutos.

**Paso 1 — Enciéndelo.** Copia la línea de tu sistema, pégala y pulsa Enter. Con eso se instala **todo solo** y se abre un asistente en tu navegador. (La primera vez tarda unos minutos: descarga varias cosas.)

- **Windows** — abre *PowerShell* (búscalo en el menú de inicio) y pega:
  ```powershell
  irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1 | iex
  ```
- **Mac** — abre *Terminal* (en Aplicaciones → Utilidades) y pega:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-macos.command | bash
  ```

**Paso 2 — Sigue el asistente.** Te lleva de la mano y prueba cada paso; no tienes que escribir comandos:
1. **Conecta tu cuenta de Claude** — un botón abre la ventana para iniciar sesión, y otro para probar que tu agente ya piensa.
2. **Comprueba Hermes** — ya quedó listo; solo confirmas con un botón (no hay preguntas que responder).
3. **Ponle nombre y propósito** a tu agente.
4. **Conéctalo a tu Telegram** — te guía para crear el bot y te deja como su único dueño.
5. **Pulsa “Aplicar y activar”.**

**Paso 3 — Háblale.** Abre tu bot en Telegram y salúdalo. ¡Ya piensa por ti! 🎉

**¿Y las actualizaciones?** No haces nada. Se actualiza **solo y en silencio** cuando no lo estás usando; solo te llega un aviso: *“🔄 se actualizó a la versión X”*.

**¿Algo falló?** Escríbele a quien te compartió olivaw. El asistente te dirá si algo quedó pendiente.

---

## How updates work (the important part)

A tiny **supervisor** (`src/launcher.py`) is what auto-starts at login — not the bridge directly.
It keeps the bridge running and, every ~45 min, checks GitHub Releases for a newer version. When
one exists it applies it **only while the bridge is idle** (no conversation in flight, quiet for a
few minutes) or in a **nightly window** — so it never interrupts a chat. Each update is
**SHA‑256 verified**, swapped atomically, health‑checked, and **rolled back automatically** if the
new version fails to come up. The user just gets a Telegram note: *“🔄 updated to vX — what’s new: …”*.

Only **code** (`src/` + `VERSION`) is updated. The user’s Hermes config, secrets (`.env`),
customized `CLAUDE.md`, and database are never touched.

## Layout

```
src/            code that gets auto-updated
  claude_bridge.py   the bridge (function-calling shim; /status idle probe)
  codex_engine.py    the Codex brain: `codex exec` behind the same (text, usage, session) contract
  launcher.py        supervisor + auto-updater
  hqctl.py           terse HQ ops CLI
  stt/ tts/          local voice in/out
templates/      config examples the installer seeds from (never overwrites live config)
install/        one-time guided installers (Windows / macOS)
tools/release.py  maintainer: cut a new release in one command
VERSION         current version (bumped by release.py)
```

## Setup (once, with the maintainer’s help)

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File install\install-windows.ps1 -Repo "Walt9819/olivaw"
```
**macOS:**
```bash
bash install/install-macos.command       # prompts for the repo
```
The installer checks prerequisites (Python 3, Node, Claude Code CLI, Hermes), downloads + verifies
the latest release, registers the supervisor at login, and then **opens the onboarding wizard in
the browser** to finish everything else — no terminal, no config files.

Prerequisites the user must have (installer guides these): Python 3, Node.js, Hermes Agent, and
**one** brain CLI:

* **Claude Code** — `npm i -g @anthropic-ai/claude-code`, then `claude auth login`. Needs a paid
  Claude plan (Pro or Max).
* **Codex** — `npm i -g @openai/codex`, then `codex login`. Needs a paid ChatGPT plan (Plus, Pro or
  Business), or an OpenAI API key in `CODEX_API_KEY`.

**The installer opens a small window** (WinForms on Windows, a native dialog on macOS — no
dependencies either way), because the browser wizard cannot exist until Python and the release are
installed, and that first stretch used to be a wall of console text. The window asks the one
question, shows progress in plain language, and hands over to the browser at the end. It does not
reimplement anything: it runs the same script as a child with `-NoUi` and streams its output, so
there is only ever one installer. No desktop, no WinForms, or a headless run falls back to the
console flow.

Two things it now handles that a non-technical owner cannot:

* **PATH.** uv installs Python's shims into `~/.local/bin` and merely *warns* when that is not on
  PATH; the first tester had to add it by hand. The installer now adds the tool directories to the
  session and persists the missing ones to the **user** PATH (idempotent, user scope only), so the
  next terminal, the shortcut and the supervisor all find them.
* **Hermes' questions.** `hermes setup --non-interactive` prints a page of "configure Hermes using
  environment variables or config commands / run `hermes setup` in an interactive terminal" — every
  one of which Olivaw runs itself seconds later. It is captured and replaced with one line saying
  what happened, and nothing anywhere tells the owner to go run a setup wizard.

**It asks which brain you want**, before it does any of the long work, and installs only
that CLI — so nothing Claude-specific lands on the machine of someone who uses ChatGPT. The wizard
then opens already set to that brain (you can still switch there; the choice is one click).

The question is skipped where there is no console to answer it (a piped `curl | bash`, a headless
run with a bot token), which defaults to Claude Code. To answer up front instead:

```powershell
$env:OLIVAW_ENGINE='codex'; irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1 | iex
install-windows.ps1 -Engine codex        # when running the file directly
```
```bash
HB_ENGINE=codex bash ~/olivaw.command    # macOS / Linux
```

### Choosing the brain (Claude Code or Codex)

The wizard writes the choice into `updater.config.json` → `env.OLIVAW_ENGINE` (`claude` | `codex`),
and the bridge reads it at startup. To switch by hand, set that value (plus `OLIVAW_CODEX` with the
path to the CLI) and restart the bridge.

What is identical on both: the decision protocol and tool loop, per-conversation session resume
(so later turns send only the new messages), effort routing by task weight, image attachments,
the SOS console with its resumable conversations, and the nightly/weekly routines.

What genuinely differs, and why:

| | Claude Code | Codex |
|---|---|---|
| tool-less reasoning | `--tools ""` | `--disable shell_tool` (+ `apps`, `browser_use`, `computer_use`, `web_search`), with `sandbox_mode="read-only"` behind it |
| runtime contract | `--append-system-prompt` | prepended to the prompt on stdin |
| session id | we choose it (`--session-id`) | Codex mints it; we learn it from `thread.started` |
| model routing | Sonnet / Opus / Fable tiers | no `-m` unless you set `OLIVAW_CODEX_MODEL` — your configured default is used |
| the agent's persona | `<workspace>/CLAUDE.md` | `<workspace>/AGENTS.md` (the wizard writes it) |
| advertised context | 1M | 256k by default (`OLIVAW_CODEX_CONTEXT`) — Hermes compacts against this number |

Codex's tools are **features, not config fields**: `-c tools.shell=false` is rejected outright,
while `codex features list` shows `shell_tool` and `--disable shell_tool` turns it off. That is
what makes the Codex brain a pure reasoner rather than an agent with a fence around it, and it is
why the SOS console's diagnose mode can make the owner the same promise on both engines — no
tools, nothing can change. `unified_exec` refuses to be disabled in 0.150.x, so the read-only
sandbox stays as the backstop.

Because that flag cannot be tested against the live API here, it is **fail-open**: the first turn
whose failure looks like a rejected flag drops the feature flags for the life of the process, logs
it, and retries. A renamed feature costs isolation, never the brain.

One more Codex behaviour worth knowing: **`codex exec` can exit 0 having produced nothing but
errors** (an auth failure looks like a clean exit). The engine therefore treats "an `agent_message`
came back" as the definition of success, and surfaces the real cause otherwise.
`tools/test_codex_engine.py` pins all of this, using a stream captured from the real CLI.

### Telegram: verified, not assumed

The wizard used to report success once it had written the config. If Telegram had revoked the bot
token (BotFather invalidates the old one the moment you generate a new one), the gateway logged
`token ... was rejected`, exited, and the owner got a green screen and a silent bot — the cause
only visible in Hermes' own profile logs.

Now the token is re-checked against Telegram **at apply time** (a token that validated ten minutes
ago is not a token that works), the wizard **waits for the gateway to actually connect**, and the
finished screen states the verdict with the fix: token rejected, webhook set (polling can never
see a message), gateway down, or connected-but-incomplete (no `TELEGRAM_ALLOWED_USERS`, so no owner
lock; no `TELEGRAM_HOME_CHANNEL`, so scheduled messages have nowhere to go). "Could not reach
Telegram" is reported as its own state — telling someone their token is revoked when their wifi is
down sends them to BotFather to fix a router.

`hermes_ctl.gateway("restart", …)` is now stop → confirm gone → start → confirm up, because
Hermes' own restart has been seen racing itself into two gateways on Windows.

The SOS console gets the same verdict in its snapshot, plus the per-profile facts (the default
agent and each extra agent keep separate config, `.env` and logs — "the gateway is running" is
never an answer without saying *which profile*), and it is told which two alarming Windows log
lines are harmless: the Unix-only `start_unix_server` watchdog and the dispatcher-lock warning.
Neither is ever the cause, and both used to look like one.

### Switching brain on a live install

Pick the other brain in the wizard and apply. The supervisor compares `/status.engine` against
`env.OLIVAW_ENGINE` on every loop and, **when the agent is idle**, restarts the bridge onto the new
engine — no reboot, and never mid-turn. The wizard says so instead of leaving you wondering why
answers still sound like the old one. A bridge too old to report its engine is left to the updater
rather than restarted in a loop.

The SOS console follows the same config, so it diagnoses the brain you are actually running: it is
handed a per-engine runbook (which CLI, how to check its session, which env var selects it, what
restarts what) plus a flag for the one failure that is otherwise invisible — a configured engine
whose CLI is not installed.

### The onboarding wizard (`src/wizard/`)

A tiny stdlib web app (opens in the browser, zero extra dependencies) that walks a non-technical
user through, **testing each step live so nothing is left half-connected**:

1. **The brain** — pick the provider (Claude Code or Codex; Antigravity is a pluggable stub),
   with a download/login guide and a real *“probar el cerebro”* button that sends a live request
   through the bridge.
2. **Hermes** — install guide + a *“verificar”* button.
3. **Your agent** — name, purpose, business/website, starting approach, and use-case chips
   (scheduler, sales, research, support…). These **warm-start** a real `CLAUDE.md` so the agent
   boots already knowing roughly what it’s for. A live preview shows the generated instructions.
4. **Your channel (owner lock)** — guided BotFather flow → paste the token → the wizard validates
   it, auto-captures your Telegram id (you tap *Start*), brands the bot, and locks the agent so
   **only your account** can command it.
5. **Activate** — writes `CLAUDE.md` + `updater.config.json`, sets the **home channel**
   (`TELEGRAM_HOME_CHANNEL`) from the verified owner chat so cron jobs, reminders and proactive
   messages work **without the user ever running `/sethome`**, then configures Hermes **through its
   own CLI**: `hermes config set model.*` points it at the bridge, the owner lock is written as
   `TELEGRAM_ALLOWED_USERS` in the profile `.env` (the real mechanism, plus `hermes pairing`), and
   the gateway is restarted. No YAML editing, no manual paste. (If the `hermes` CLI isn't found,
   it falls back to a paste-in `hermes-config-snippet.yaml`.) Then it starts the supervisor.

### Multiple agents on one machine

The wizard opens on an **agent manager**: it lists every agent on the machine (the built-in
`default` plus any you've added) with live status, and lets you **reconfigure, pause/resume, reset,
or create a new fully-isolated agent**. Each agent is its own **Hermes profile** (own
`config.yaml`/`.env`/`SOUL.md`/memory/skills/**bot**), its own **bridge on its own port**
(8790, 8792, …), its own **workspace/`CLAUDE.md`**, and optionally its own **Claude login**
(`CLAUDE_CONFIG_DIR`) — so two agents can even use different Claude accounts.

How it works:
- Extra agents are recorded in `agents.json`; the **one supervisor** runs a bridge per agent and
  updates the shared code **once, only when every agent is idle** (then restarts them all).
- Hermes owns each agent's gateway; the wizard drives it via the per-profile wrapper
  (`~/.local/bin/<slug>`), so configuring agent B never touches agent A.
- The `default` agent keeps running exactly as before — multi-agent is layered on top, nothing
  about the single-agent path changes.
- **Isolated Claude account (optional):** an agent can use its own `CLAUDE_CONFIG_DIR`; the wizard
  shows the one-time `CLAUDE_CONFIG_DIR=… claude` login command on the finish screen.
- **Gateways survive reboot:** the supervisor also keeps each extra agent's Hermes gateway alive
  (`<slug> gateway run --external-supervisor`), so channels come back after a restart.

### Help console (works even when the bridge is down)

The **🆘 button** in the sidebar opens a full-screen console — an overlay, not a setup step, so it
is one click away at any moment — that talks to **Claude Code directly**: not through Telegram, not
through Hermes, not through the bridge. It attaches a live snapshot of the installation (bridge
ports up/down, Hermes gateway, Claude auth, `launcher.log` / `bridge.log` tails, config with
**secrets redacted**) so Claude can say what is broken and what to do, in plain language. Two
modes: **diagnose** (tools off — cannot change anything) and an explicit **"permitir que aplique
arreglos"** (tools on, scoped to the install dir). This is the escape hatch for exactly the case
where the agent can't be reached through its normal channel.

**Answers are rendered, not dumped.** Replies come back as markdown and are rendered as HTML —
bold, lists, headings, links, and commands in real code blocks instead of literal `**asterisks**`
and triple backticks. The renderer escapes the source first and passes no raw HTML through, so a
log line or config value quoted inside an answer can never inject markup, and links are limited to
`http(s)`.

**When Claude needs decisions, you click them.** A reply can end in **up to four questions** at
once (Claude is told to ask everything in one go instead of dripping one question per turn), and
the console renders them as an answer form:

- **buttons per question** — single-select, or **multi-select** where several answers apply;
- **a comment on any option you picked** — a note row appears under each pick, so "yes, but after
  6pm" travels with the choice it belongs to;
- **your own answer** — a free-text field per question when none of the options fit;
- **one general comment** covering the whole set;
- questions you skip are reported back as `(sin responder)` rather than silently dropped.

What gets sent is the exact wording of each option plus your comments, so nothing has to be
retyped or spelled right. A single pick with no comments still sends as a plain one-line answer.
Claude emits a fenced `ask` block for this (several blocks in one reply are merged); a plain
question followed by a numbered list is also recognised, and answered questions stay in the
transcript as a read-only record.

**The view follows you, not the stream.** New events only scroll the console down when you are
already parked at the bottom — scroll up to re-read something mid-answer and it stays where you
put it. Opening a conversation or sending a message jumps to the newest message on purpose.

**Conversations are saved and resumable.** Each console conversation is backed by a real Claude
Code session (olivaw mints the session id and resumes it), so reopening one from the left-hand list
continues it with the context genuinely still in place — Claude remembers what you already told it,
nothing is re-pasted. They live in `console/*.json` inside the install dir (mode 600, already
redacted), the newest 60 are kept, and you can rename or delete any of them.

### App shortcut

The installers create an **Olivaw** shortcut (Windows: Desktop + Start Menu; macOS:
`~/Applications/Olivaw.command`) that reopens this setup/help UI at any time — no terminal.

### Extras: capabilities, connectors & channels (optional, per agent)

A final optional step gives an agent more power — all Hermes-side, so it actually works through
the bridge (Claude Code MCP connectors do **not** work here — the bridge disables them):

- **Conversation memory** — lets the agent recall and resume past conversations. Hermes keeps a
  searchable session store, but `session_search` ships only on the `cli` toolset, so on Telegram the
  agent can't recall history until you enable it. The wizard shows per-platform status and opens
  `hermes setup tools` to turn on Session Search; every agent's `CLAUDE.md` is also warm-started to
  recall history before assuming ignorance and to continue vs. start-fresh sensibly.
- **Image / video generation** — opens `hermes setup tools` to enable the toolset and pick a
  provider. The wizard lists free/low-cost options (local GPU, Google Gemini free tier,
  Pollinations no-key, OpenRouter) alongside the paid ones Hermes ships
  (openai, xai, fal, deepinfra, krea).
- **Connectors (MCP)** — browse the Hermes catalog (`hermes mcp catalog`) and install with one
  click, or add a custom server by URL. This is the correct place for connectors like Pixa.

…and it connects an agent to more channels — each guided and testable:

- **WhatsApp** — one click starts pairing and the **QR appears inside the wizard** (no terminal);
  then you lock the channel to your own number (`WHATSAPP_ALLOWED_USERS`).
- **Google Workspace** — **Gmail/Workspace mail** via Hermes' native `email` platform, so the agent
  **receives and replies** to email (IMAP in + SMTP out, app password, presets for Gmail/Outlook),
  plus **Google Chat** via a service account. Both require an allow-list, so a stranger who emails
  or messages the agent cannot command it.
- **Slack** — generates the app manifest (`hermes slack manifest`) to paste at api.slack.com, then
  finishes config in a terminal.
- **Webhook / Google Chat** — creates a `hermes webhook subscribe` route for event-driven activation,
  with a one-click test.
- **Email (SMTP)** — not native to Hermes, so the kit ships `src/tools/smtp_send.py` and the wizard
  writes `SMTP_*` into the agent's profile `.env` (presets + app-password guides for Gmail, Outlook,
  Yahoo, iCloud), sends a live test email, and adds an email-capability note to the agent's `CLAUDE.md`.

Re-run anytime to reconfigure:
```bash
python src/wizard/wizard_server.py
```
Headless installs still work — pass `-BotToken`/`-ChatId` (Windows) or `HB_BOT_TOKEN` (macOS) to
skip the wizard and configure from parameters (add `-NoWizard` / `HB_NO_WIZARD=1` to force-skip).

> **Owner-lock is now automated** via Hermes' own CLI (`TELEGRAM_ALLOWED_USERS` + `hermes pairing`),
> so there's no manual paste when the `hermes` command is available. The paste-in snippet remains
> only as a fallback for machines where the CLI isn't on PATH.

## Publishing an update (maintainer)

One-time setup (so `--publish` can create the GitHub Release):
```powershell
winget install --id GitHub.cli -e   # install the GitHub CLI once
gh auth login                       # GitHub.com → HTTPS → login with a browser
```
Then, for every update:
```bash
python tools/release.py patch -m "Ahora puede ver imágenes que le mandes" --publish
# bumps VERSION, builds + checksums the release zip, creates the GitHub Release via gh.
```
Every installed supervisor picks it up and applies it when idle. Config changes ship as idempotent
`--migration` steps in the release manifest.

## Security / trust

The bridge runs with full machine access (Hermes YOLO mode), so **whatever is published to the
release repo runs automatically on every installed machine.** Keep control of the repo, only ship
tagged releases, and never commit secrets — tokens/keys live only in each user’s local `.env`
(`.env.example` documents them). Releases are SHA‑256‑verified before running.

## Configuration knobs

Bridge (env): `OLIVAW_ENGINE` (`claude` | `codex`), `CLAUDE_BRIDGE_PRIMARY` (default `sonnet`),
`_FALLBACK` (`opus`), `_AUX` (`sonnet`), `_BIGCTX` (`fable`), `_EFFORT_HEAVY/NORMAL/LIGHT`,
`_IMG_DIR`, `_CLAUDE`, `_WORKSPACE`.
Codex engine (env): `OLIVAW_CODEX` (path to the CLI), `OLIVAW_CODEX_MODEL` (empty = the model your
Codex config already uses), `OLIVAW_CODEX_MODEL_<TIER>` for a per-tier override,
`OLIVAW_CODEX_FALLBACK`, `OLIVAW_CODEX_AUX`, `OLIVAW_CODEX_BIGCTX`, `OLIVAW_CODEX_CONTEXT`.
Supervisor (`updater.config.json`): `repo`, `poll_minutes`, `idle_seconds`, `nightly_hour`,
`auto_update`, `lang`, Telegram token/chat ids. See `templates/`.
