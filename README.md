<!--
  olivaw — after R. Daneel Olivaw, the robot who spends twenty thousand years quietly
  keeping humanity on course. This kit does the small, patient version: keeping a helpful
  agent alive and current on someone's machine without them ever noticing the machinery.
  "It is the chief characteristic of the religion of science that it works." — Salvor Hardin
-->
# Hermes Bridge

Run the **Hermes Agent** using your **Claude Code subscription as its model** — no API key —
reachable over Telegram (or any Hermes platform). Includes local GPU **STT** (voice notes) and
**TTS** (voice replies), an `hqctl` ops CLI, and a **silent auto-updater** so non-technical users
stay current without ever touching a terminal.

```
Telegram ⇄ Hermes gateway ⇄ bridge (localhost:8790) ⇄ claude -p  (Claude Code = the brain)
                                     ▲
                          supervisor: keeps it alive + auto-updates when idle
```

## 🚀 Empezar en ~15 minutos (guía para no-técnicos)

> Esta guía es para ti si alguien te compartió **olivaw** y quieres tu propio asistente.
> No necesitas saber programar. El instalador se encarga de **todo** solo.

**Lo único que necesitas tener tú:**
- Una **cuenta de pago de Claude** (plan Pro o Max) → [claude.com](https://claude.com). Es el “cerebro” de tu agente; iniciarás sesión una vez.
- Una **cuenta de Hermes**.

Todo lo demás (Python, Node, Claude Code y el propio Hermes) **se descarga e instala solo** — tú no instalas nada técnico a mano. La primera vez puede tardar varios minutos.

**Paso 1 — Instálalo.** En **Windows**, busca “PowerShell” en el menú de inicio, ábrelo, pega esto y pulsa Enter:
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1 -OutFile $env:TEMP\olivaw.ps1; & $env:TEMP\olivaw.ps1"
```
En **Mac**, abre la app “Terminal”, pega esto y pulsa Enter:
```bash
curl -fsSL https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-macos.command -o ~/olivaw.command && HB_REPO=Walt9819/olivaw bash ~/olivaw.command
```
Se descarga solo, verifica que todo esté bien y abre un **asistente en tu navegador**.

**Paso 2 — Sigue el asistente** (te lleva de la mano y prueba cada paso):
1. Elige el cerebro (**Claude Code**) y pulsa **“Probar el cerebro”**.
2. Conecta **Hermes**.
3. Ponle **nombre** a tu agente y dile **para qué** lo quieres.
4. Conéctalo a tu **Telegram** (te ayuda a crear el bot con BotFather y te deja como su único dueño).
5. Pulsa **“Aplicar y activar”**.

**Paso 3 — Háblale.** Abre tu bot en Telegram y salúdalo. ¡Ya piensa por ti! 🎉

**¿Y las actualizaciones?** No haces nada. Se actualiza **solo y en silencio** cuando no lo estás usando; solo te llega un aviso: *“🔄 se actualizó a la versión X”*.

**¿Algo falló?** Escríbele a quien te compartió olivaw — casi siempre es un programa que faltó instalar, y el asistente te dice cuál.

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

Prerequisites the user must have (installer guides these): Python 3, Node.js, the Claude Code CLI
(`npm i -g @anthropic-ai/claude-code`, logged into a Claude subscription), and Hermes Agent.

### The onboarding wizard (`src/wizard/`)

A tiny stdlib web app (opens in the browser, zero extra dependencies) that walks a non-technical
user through, **testing each step live so nothing is left half-connected**:

1. **The brain** — pick the provider (Claude Code today; Codex / Antigravity are pluggable stubs),
   with a download/login guide and a real *“probar el cerebro”* button that sends a live request
   through the bridge.
2. **Hermes** — install guide + a *“verificar”* button.
3. **Your agent** — name, purpose, business/website, starting approach, and use-case chips
   (scheduler, sales, research, support…). These **warm-start** a real `CLAUDE.md` so the agent
   boots already knowing roughly what it’s for. A live preview shows the generated instructions.
4. **Your channel (owner lock)** — guided BotFather flow → paste the token → the wizard validates
   it, auto-captures your Telegram id (you tap *Start*), brands the bot, and locks the agent so
   **only your account** can command it.
5. **Activate** — writes `CLAUDE.md` + `updater.config.json`, then configures Hermes **through its
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

- **WhatsApp** — opens `hermes whatsapp` (personal, QR) or `whatsapp-cloud` (Business API) in a terminal.
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

Bridge (env): `CLAUDE_BRIDGE_PRIMARY` (default `sonnet`), `_FALLBACK` (`opus`), `_AUX` (`sonnet`),
`_BIGCTX` (`fable`), `_EFFORT_HEAVY/NORMAL/LIGHT`, `_IMG_DIR`, `_CLAUDE`, `_WORKSPACE`.
Supervisor (`updater.config.json`): `repo`, `poll_minutes`, `idle_seconds`, `nightly_hour`,
`auto_update`, `lang`, Telegram token/chat ids. See `templates/`.
