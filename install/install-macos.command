#!/bin/bash
# Hermes Bridge — macOS installer (run WITH the user, once). Double-click or:
#   bash install-macos.command
# It checks prerequisites, downloads+verifies the latest release, writes config,
# registers the SUPERVISOR as a launchd agent (auto-start + keep-alive), and starts it.
# After this, updates are automatic & silent. Provide values via env or the prompts:
#   HB_REPO, HB_BOT_TOKEN, HB_CHAT_ID, HB_MAINTAINER, HB_LOCAL_SOURCE (optional)
set -euo pipefail
say(){ printf "  %s\n" "$*"; }
ok(){ printf "  \033[32m[ok]\033[0m %s\n" "$*"; }
warn(){ printf "  \033[33m[!]\033[0m %s\n" "$*"; }

echo; echo "=== Hermes Bridge installer (macOS) ==="; echo
INSTALL_DIR="${HB_INSTALL_DIR:-$HOME/HermesBridge}"
WORKSPACE="${HB_WORKSPACE:-$HOME/hermes-workspace}"
REPO="${HB_REPO:-}"; BOT="${HB_BOT_TOKEN:-}"; CHAT="${HB_CHAT_ID:-}"; MAINT="${HB_MAINTAINER:-}"
LOCAL_SRC="${HB_LOCAL_SOURCE:-}"; LANG_="${HB_LANG:-es}"; NO_WIZARD="${HB_NO_WIZARD:-}"
[ -z "$REPO" ] && read -rp "  GitHub repo (owner/name): " REPO
# Wizard mode (default): no token passed -> finish setup in the browser wizard.
USE_WIZARD=1; { [ -n "$BOT" ] || [ -n "$NO_WIZARD" ]; } && USE_WIZARD=0
if [ "$USE_WIZARD" = "0" ]; then
  [ -z "$BOT" ]  && read -rp "  Telegram bot token: " BOT
  [ -z "$CHAT" ] && read -rp "  Your Telegram chat id: " CHAT
  [ -z "$MAINT" ] && MAINT="$CHAT"
fi

# 1) prerequisites
PY="$(command -v python3 || true)"; [ -z "$PY" ] && { echo "python3 not found. Install: brew install python"; exit 1; }
ok "python3: $PY"
command -v node >/dev/null || warn "Node.js not found — install: brew install node"
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ] && command -v npm >/dev/null; then say "Installing Claude Code CLI..."; npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || true; CLAUDE="$(command -v claude || true)"; fi
[ -n "$CLAUDE" ] && ok "Claude Code: $CLAUDE" || warn "Claude Code CLI missing — npm install -g @anthropic-ai/claude-code"
command -v hermes >/dev/null || warn "Hermes not found — install per its docs before first use."

# 2) get the code
mkdir -p "$INSTALL_DIR" "$WORKSPACE"
TMP="$(mktemp -d)"
if [ -n "$LOCAL_SRC" ]; then
  say "Installing from local source: $LOCAL_SRC"
  cp -R "$LOCAL_SRC/src" "$LOCAL_SRC/templates" "$LOCAL_SRC/VERSION" "$INSTALL_DIR/"
else
  say "Fetching latest release from $REPO ..."
  META="$(curl -fsSL -H 'User-Agent: hb-installer' "https://api.github.com/repos/$REPO/releases/latest")"
  ZIP_URL="$(printf '%s' "$META" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print(next((a["browser_download_url"] for a in d.get("assets",[]) if a["name"].endswith(".zip")),""))')"
  SHA_URL="$(printf '%s' "$META" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print(next((a["browser_download_url"] for a in d.get("assets",[]) if a["name"].endswith(".zip.sha256")),""))')"
  [ -z "$ZIP_URL" ] && { echo "No .zip asset on latest release of $REPO"; exit 1; }
  curl -fsSL "$ZIP_URL" -o "$TMP/rel.zip"
  if [ -n "$SHA_URL" ]; then
    curl -fsSL "$SHA_URL" -o "$TMP/rel.sha"
    EXP="$(awk '{print tolower($1)}' "$TMP/rel.sha")"; ACT="$(shasum -a 256 "$TMP/rel.zip" | awk '{print tolower($1)}')"
    [ "$EXP" = "$ACT" ] || { echo "Checksum mismatch — refusing to install."; exit 1; }
    ok "Checksum verified"
  else warn "No .sha256 on release — installing unverified."; fi
  unzip -qo "$TMP/rel.zip" -d "$TMP"
  cp -R "$TMP/src" "$TMP/templates" "$TMP/VERSION" "$INSTALL_DIR/"
fi
ok "Code installed to $INSTALL_DIR (v$(cat "$INSTALL_DIR/VERSION"))"

# 3) updater.config.json
if [ "$USE_WIZARD" = "0" ]; then
"$PY" - "$INSTALL_DIR" "$REPO" "$PY" "$WORKSPACE" "$CLAUDE" "$BOT" "$CHAT" "$MAINT" "$LANG_" <<'PYEOF'
import json,sys
inst,repo,py,ws,claude,bot,chat,maint,lang=sys.argv[1:10]
cfg={"repo":repo,"auto_update":True,"bridge_cmd":[py,"src/claude_bridge.py"],
 "bridge_cwd":inst,"bridge_url":"http://127.0.0.1:8790",
 "env":{"CLAUDE_BRIDGE_CLAUDE":claude,"CLAUDE_BRIDGE_WORKSPACE":ws},
 "telegram_bot_token":bot,"telegram_chat_id":chat,"maintainer_chat_id":maint,
 "poll_minutes":45,"idle_seconds":300,"nightly_hour":4,"lang":lang}
open(inst+"/updater.config.json","w").write(json.dumps(cfg,indent=2,ensure_ascii=False))
PYEOF
ok "Wrote updater.config.json"
else
  say "La configuración final (token, dueño, agente) se hará en el asistente del navegador."
fi

# 4) point Hermes at the bridge (best-effort)
HCFG="$(find "$HOME/Library/Application Support/hermes" "$HOME/.config/hermes" "$HOME/.local/share/hermes" -name config.yaml 2>/dev/null | head -1 || true)"
if [ -n "$HCFG" ]; then cp "$HCFG" "$HCFG.bak"; warn "Hermes config at $HCFG (backup saved). Ensure model.provider=custom, base_url=http://127.0.0.1:8790/v1 (see templates/config.snippet.yaml)."; else warn "Hermes config.yaml not found — apply templates/config.snippet.yaml after installing Hermes."; fi

# 5) launchd auto-start (supervisor) + start now
PLIST="$HOME/Library/LaunchAgents/com.hermesbridge.supervisor.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLEOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.hermesbridge.supervisor</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$INSTALL_DIR/src/launcher.py</string></array>
  <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$INSTALL_DIR/supervisor.out.log</string>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/supervisor.err.log</string>
</dict></plist>
PLEOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
ok "Registered + started supervisor (launchd: com.hermesbridge.supervisor)"

if [ "$USE_WIZARD" = "1" ]; then
  echo; echo "=== Abriendo el asistente de configuración ==="
  say "Se abrirá en tu navegador. Elige el cerebro, conecta Hermes, dale personalidad"
  say "a tu agente y vincúlalo a tu Telegram. El asistente activa todo al final."
  ( cd "$INSTALL_DIR" && "$PY" "$INSTALL_DIR/src/wizard/wizard_server.py" >/dev/null 2>&1 & )
  echo; printf "\033[32mSi el navegador no se abre solo, ejecuta:  %s %s\033[0m\n\n" \
    "$PY" "$INSTALL_DIR/src/wizard/wizard_server.py"
else
  echo; echo "=== Almost done ==="
  say "1) Log into Claude Code once:  claude   (complete sign-in)"
  say "2) Ensure Hermes is installed and its gateway is running."
  say "3) Message the Telegram bot to test."
  echo; printf "\033[32mUpdates from now on are automatic & silent.\033[0m\n\n"
fi
