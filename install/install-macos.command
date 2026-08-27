#!/bin/bash
# olivaw - one-click installer (macOS / Linux).
#
# The user installs NOTHING technical by hand. This auto-installs every dependency,
# then opens the browser wizard. The brain is Claude Code (default) or OpenAI Codex
# (HB_ENGINE=codex). The only user-provided things are the brain's paid account
# (log in once) and the Hermes account.
#
# Auto-installed (each step skipped if already present):
#   1. Hermes      -> official installer (also brings uv, Python, Node, ripgrep, ffmpeg)
#   2. uv          -> Astral Python manager
#   3. Python      -> a uv-managed Python that runs bridge/supervisor/wizard
#   4. the brain CLI -> Claude Code (native installer), or Codex via npm when HB_ENGINE=codex
#   5. olivaw      -> downloads + SHA-256-verifies the latest release
#
# It asks which brain you want; set HB_ENGINE=claude|codex to answer up front.
# Non-technical (opens wizard):
#   curl -fsSL https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-macos.command -o ~/olivaw.command && bash ~/olivaw.command
# Advanced/headless: set HB_BOT_TOKEN + HB_CHAT_ID (and optionally HB_NO_WIZARD=1).
set -euo pipefail
say(){ printf "  %s\n" "$*"; }
ok(){ printf "  \033[32m[ok]\033[0m %s\n" "$*"; }
warn(){ printf "  \033[33m[!]\033[0m %s\n" "$*"; }
step(){ printf "\n\033[1m> %s\033[0m\n" "$*"; }
have(){ command -v "$1" 2>/dev/null; }

echo; echo "=== olivaw installer (macOS/Linux) ==="
echo "  Instalando todo automaticamente. La primera vez puede tardar varios minutos."; echo

REPO="${HB_REPO:-Walt9819/olivaw}"
INSTALL_DIR="${HB_INSTALL_DIR:-$HOME/.olivaw}"
WORKSPACE="${HB_WORKSPACE:-$HOME/hermes-workspace}"
BOT="${HB_BOT_TOKEN:-}"; CHAT="${HB_CHAT_ID:-}"; MAINT="${HB_MAINTAINER:-$CHAT}"
LOCAL_SRC="${HB_LOCAL_SOURCE:-}"; LANG_="${HB_LANG:-es}"
NO_WIZARD="${HB_NO_WIZARD:-}"; NO_AUTO="${HB_NO_AUTOINSTALL:-}"
ENGINE="${HB_ENGINE:-}"                # which brain: claude | codex
case "$ENGINE" in claude|codex) ;; *) ENGINE="" ;; esac
USE_WIZARD=1; { [ -n "$BOT" ] || [ -n "$NO_WIZARD" ]; } && USE_WIZARD=0
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p "$INSTALL_DIR" "$WORKSPACE"

# ── which brain? asked FIRST, so nobody waits ten minutes to be asked a question ──
ask_engine() {
  # Only ask when stdin is a real terminal: piped into bash there is nobody to answer, and the
  # install would hang forever.
  [ -t 0 ] || { ENGINE="claude"; return; }
  printf "\n"
  say "El cerebro de tu agente:"
  say "    1) Claude Code  - cuenta de pago de Claude (Pro o Max)   [recomendado]"
  say "    2) Codex        - cuenta de pago de ChatGPT (Plus, Pro o Business)"
  say "  Se instala y configura solo el que elijas. Puedes cambiarlo despues desde el asistente."
  for _ in 1 2 3; do
    printf "  Elige 1 o 2 [1]: "
    read -r a || { ENGINE="claude"; return; }
    case "$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
      ""|1|claude) ENGINE="claude"; return ;;
      2|codex)     ENGINE="codex";  return ;;
      *) warn "Responde 1 o 2." ;;
    esac
  done
  ENGINE="claude"
}
if [ -z "$ENGINE" ]; then ask_engine; else say "Cerebro elegido por HB_ENGINE: $ENGINE"; fi
ok "Cerebro: $ENGINE"

# 1) Hermes
step "1/5  Hermes"
if have hermes >/dev/null; then ok "Hermes ya esta instalado."
elif [ -n "$NO_AUTO" ]; then warn "Hermes no encontrado y auto-install desactivado."
else
  say "Instalando Hermes (trae Python, Node y utilidades)..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash || warn "El instalador de Hermes reporto un problema."
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
  have hermes >/dev/null && ok "Hermes instalado." || warn "No pude confirmar 'hermes' en PATH; el asistente lo revisara."
fi
# Seed a non-interactive baseline so the user is NEVER dropped into Hermes' question wizard.
# Olivaw configures model/owner-lock itself later; defaults are fine. Best-effort.
if have hermes >/dev/null; then hermes setup --non-interactive >/dev/null 2>&1 || true; fi

# 2) uv
step "2/5  uv (gestor de Python)"
UV="$(have uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.hermes/bin/uv" ] && UV="$HOME/.hermes/bin/uv"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ] && [ -z "$NO_AUTO" ]; then
  say "Instalando uv..."; curl -LsSf https://astral.sh/uv/install.sh | sh || true
  export PATH="$HOME/.local/bin:$PATH"; UV="$(have uv || true)"
  [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
fi
[ -n "$UV" ] || { echo "No se pudo obtener uv (necesario para Python)."; exit 1; }
ok "uv: $UV"

# 3) Python via uv
step "3/5  Python"
"$UV" python install 3.12 >/dev/null 2>&1 || true
PY="$("$UV" python find 3.12 2>/dev/null | head -1 || true)"
[ -z "$PY" ] && PY="$("$UV" python find 2>/dev/null | head -1 || true)"
[ -n "$PY" ] && [ -x "$PY" ] || { echo "uv no pudo proveer Python."; exit 1; }
ok "Python: $PY"

# 4) the brain CLI (Claude Code by default, Codex when HB_ENGINE=codex)
if [ "$ENGINE" = "codex" ]; then
  step "4/5  Codex (el cerebro)"
  if have codex >/dev/null; then ok "Codex ya esta instalado."
  elif [ -n "$NO_AUTO" ]; then warn "Codex no encontrado y auto-install desactivado."
  elif ! have npm >/dev/null; then
    warn "Codex se instala con npm (Node.js) y no encontre npm en este equipo."
    say "Instala Node.js (nodejs.org), abre una terminal nueva y vuelve a ejecutar esto."
  else
    say "Instalando Codex (npm install -g @openai/codex)..."
    npm install -g @openai/codex >/dev/null 2>&1 || warn "El instalador de Codex reporto un problema."
    have codex >/dev/null && ok "Codex instalado." || warn "No pude confirmar 'codex' en PATH; el asistente lo revisara."
  fi
else
  step "4/5  Claude Code (el cerebro)"
  if have claude >/dev/null; then ok "Claude Code ya esta instalado."
  elif [ -n "$NO_AUTO" ]; then warn "Claude Code no encontrado y auto-install desactivado."
  else
    say "Instalando Claude Code (instalador nativo)..."
    curl -fsSL https://claude.ai/install.sh | bash || warn "El instalador de Claude reporto un problema."
    export PATH="$HOME/.local/bin:$PATH"
    have claude >/dev/null && ok "Claude Code instalado." || warn "No pude confirmar 'claude' en PATH; el asistente lo revisara."
  fi
fi
CLAUDE="$(have claude || true)"
CODEX="$(have codex || true)"

# 5) olivaw
step "5/5  olivaw"
TMP="$(mktemp -d)"
if [ -n "$LOCAL_SRC" ]; then
  say "Instalando desde copia local: $LOCAL_SRC"
  cp -R "$LOCAL_SRC/src" "$LOCAL_SRC/templates" "$LOCAL_SRC/VERSION" "$INSTALL_DIR/"
else
  say "Descargando la ultima version de $REPO ..."
  META="$(curl -fsSL -H 'User-Agent: olivaw-installer' "https://api.github.com/repos/$REPO/releases/latest")"
  ZIP_URL="$(printf '%s' "$META" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print(next((a["browser_download_url"] for a in d.get("assets",[]) if a["name"].endswith(".zip")),""))')"
  SHA_URL="$(printf '%s' "$META" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print(next((a["browser_download_url"] for a in d.get("assets",[]) if a["name"].endswith(".zip.sha256")),""))')"
  [ -n "$ZIP_URL" ] || { echo "No hay asset .zip en la ultima release de $REPO"; exit 1; }
  curl -fsSL "$ZIP_URL" -o "$TMP/rel.zip"
  if [ -n "$SHA_URL" ]; then
    curl -fsSL "$SHA_URL" -o "$TMP/rel.sha"
    EXP="$(awk '{print tolower($1)}' "$TMP/rel.sha")"; ACT="$(shasum -a 256 "$TMP/rel.zip" | awk '{print tolower($1)}')"
    [ "$EXP" = "$ACT" ] || { echo "Checksum no coincide - abortado."; exit 1; }
    ok "Checksum verificado."
  else echo "La release no incluye .sha256 - abortado (no se ejecuta codigo sin verificar)."; exit 1; fi
  unzip -qo "$TMP/rel.zip" -d "$TMP"
  cp -R "$TMP/src" "$TMP/templates" "$TMP/VERSION" "$INSTALL_DIR/"
fi
ok "olivaw instalado en $INSTALL_DIR (v$(cat "$INSTALL_DIR/VERSION"))"

# headless config (only when a token was passed)
if [ "$USE_WIZARD" = "0" ]; then
"$PY" - "$INSTALL_DIR" "$REPO" "$PY" "$WORKSPACE" "${CLAUDE:-}" "$BOT" "$CHAT" "$MAINT" "$LANG_" "$ENGINE" "${CODEX:-}" <<'PYEOF'
import json,sys
inst,repo,py,ws,claude,bot,chat,maint,lang,engine,codex=sys.argv[1:12]
cfg={"repo":repo,"auto_update":True,"bridge_cmd":[py,"src/claude_bridge.py","--port","8790"],
 "bridge_cwd":inst,"bridge_url":"http://127.0.0.1:8790",
 "env":{"OLIVAW_ENGINE":engine,"CLAUDE_BRIDGE_CLAUDE":claude,"OLIVAW_CODEX":codex,
        "CLAUDE_BRIDGE_WORKSPACE":ws},
 "telegram_bot_token":bot,"telegram_chat_id":chat,"maintainer_chat_id":maint,
 "poll_minutes":45,"idle_seconds":300,"nightly_hour":4,"lang":lang}
open(inst+"/updater.config.json","w").write(json.dumps(cfg,indent=2,ensure_ascii=False))
PYEOF
ok "Escrito updater.config.json"
else
  say "La configuracion final se hara en el asistente del navegador."
fi

# App shortcut: a double-clickable "Olivaw" that reopens the setup/help UI.
# Without it the user would need a terminal to get back into the wizard.
APPDIR="$HOME/Applications"; mkdir -p "$APPDIR"
OPENER="$APPDIR/Olivaw.command"
cat > "$OPENER" <<OPENEOF
#!/bin/bash
# Abre la configuracion / ayuda de Olivaw en el navegador.
cd "$INSTALL_DIR" && exec "$PY" "$INSTALL_DIR/src/wizard/wizard_server.py"
OPENEOF
chmod +x "$OPENER"
ok "Acceso directo creado: $OPENER (doble clic para volver a la configuracion)"

# supervisor at login (launchd), runs with the uv-managed Python
PLIST="$HOME/Library/LaunchAgents/com.olivaw.supervisor.plist"
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLEOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.olivaw.supervisor</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$INSTALL_DIR/src/launcher.py</string></array>
  <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$INSTALL_DIR/supervisor.out.log</string>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/supervisor.err.log</string>
</dict></plist>
PLEOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST" 2>/dev/null || true
  ok "Auto-arranque registrado (launchd)."
else
  warn "Sistema no-macOS: inicia el supervisor con:  \"$PY\" \"$INSTALL_DIR/src/launcher.py\" &"
fi

# finish
if [ "$USE_WIZARD" = "1" ]; then
  echo; echo "=== Abriendo el asistente de configuracion ==="
  say "Sigue los pasos en el navegador: probar el cerebro, conectar Hermes, ponerle nombre"
  say "a tu agente y vincularlo a tu Telegram. El asistente activa todo al final."
  ( cd "$INSTALL_DIR" && "$PY" "$INSTALL_DIR/src/wizard/wizard_server.py" >/dev/null 2>&1 & )
  echo; printf "\033[32mSi el navegador no abre solo, ejecuta:  \"%s\" \"%s\"\033[0m\n\n" "$PY" "$INSTALL_DIR/src/wizard/wizard_server.py"
else
  echo; echo "=== Casi listo ==="
  if [ "$ENGINE" = "codex" ]; then say "1) Inicia sesion en Codex una vez:  codex login"; else say "1) Inicia sesion en Claude una vez:  claude"; fi
  say "2) Configura Hermes (hermes setup) y deja su gateway corriendo."
  say "3) Escribe a tu bot de Telegram para probar."
  echo; printf "\033[32mLas actualizaciones son automaticas y silenciosas.\033[0m\n\n"
fi
