<#
  olivaw - one-click installer (Windows).

  Goal: the user installs NOTHING technical by hand. This script auto-installs every
  dependency, then opens the browser wizard. The only things the user provides are a paid
  account for the brain (they log in once) and their Hermes account.

  The brain is either Claude Code (default) or OpenAI Codex: -Engine claude|codex.

  What it installs automatically (each step is skipped if already present):
    1. Hermes  -> official installer (also brings uv, Python, Node.js, ripgrep, ffmpeg, Git Bash)
    2. uv      -> Astral's Python manager (from Hermes' bin, or bootstrapped)
    3. Python  -> a uv-managed Python that runs the bridge/supervisor/wizard (no system Python)
    4. the brain CLI -> Claude Code via its native installer (no Node needed), or
                        Codex via npm when -Engine codex
    5. olivaw  -> downloads + SHA-256-verifies the latest release
  Then it registers the supervisor at login and opens the setup wizard.

  Usage (non-technical, opens the wizard):
    iex (irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1)
  Advanced / headless (configure from params, no wizard):
    install-windows.ps1 -NoWizard -BotToken "123:ABC" -ChatId "8114329186"
#>
[CmdletBinding()]
param(
  [string]$Repo = "Walt9819/olivaw",
  [string]$BotToken = "",
  [string]$ChatId = "",
  [string]$MaintainerId = "",
  [string]$InstallDir = "$env:LOCALAPPDATA\Olivaw",
  [string]$Workspace = "$env:USERPROFILE\hermes-workspace",
  [string]$LocalSource = "",
  [string]$Lang = "es",
  [ValidateSet("claude","codex")]
  [string]$Engine = "claude",
  [switch]$NoWizard,
  [switch]$NoAutoInstall
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # faster Invoke-WebRequest
$UseWizard = (-not $NoWizard) -and [string]::IsNullOrWhiteSpace($BotToken)

function Info($m){ Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }
function Step($m){ Write-Host "`n> $m" -ForegroundColor White }
function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable('Path','Machine')
  $u = [Environment]::GetEnvironmentVariable('Path','User')
  $env:Path = (@($m,$u) | Where-Object { $_ } ) -join ';'
}
function Have($n){ (Get-Command $n -ErrorAction SilentlyContinue).Source }

# Run a native .exe safely under $ErrorActionPreference = "Stop".
#
# PowerShell 5.1 turns ANY line a native command writes to stderr into an ErrorRecord as soon as
# that stream is redirected - and under EAP=Stop that ErrorRecord is terminating. Tools report
# progress and warnings on stderr all the time (uv's "Downloading cpython..." is progress, npm
# warns constantly), so a perfectly successful command would kill this installer. Both `2>&1` and
# `2>$null` behave that way; only leaving stderr alone, or lowering EAP, is safe.
#
# Returns the exit code. Stdout comes back through -Capture when the caller needs it.
function Native {
  param([string]$Exe, [string[]]$Arguments = @(), [switch]$Quiet, [switch]$Capture)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $global:LASTEXITCODE = 0
    if ($Capture)   { return (& $Exe @Arguments 2>$null) }
    if ($Quiet)     { & $Exe @Arguments 2>$null | Out-Null }
    else            { & $Exe @Arguments }        # let the user watch a long download
    return $global:LASTEXITCODE
  } catch {
    Warn "${Exe}: $($_.Exception.Message)"   # ${} or PowerShell reads $Exe: as a scope
    return 1
  } finally {
    $ErrorActionPreference = $prev
  }
}

Write-Host "`n=== olivaw installer (Windows) ===" -ForegroundColor White
Write-Host "  Instalando todo automaticamente. Puede tardar varios minutos la primera vez.`n" -ForegroundColor DarkGray
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $Workspace  | Out-Null

# 1) Hermes ------------------------------------------------------------------
Step "1/5  Hermes"
Refresh-Path
if (Have hermes) {
  Ok "Hermes ya esta instalado."
} elseif ($NoAutoInstall) {
  Warn "Hermes no encontrado y auto-install desactivado."
} else {
  Info "Instalando Hermes (trae Python, Node y utilidades)... esto tarda un poco."
  # Run in a child PowerShell so an `exit` inside Hermes' installer can't abort ours.
  try { powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (irm https://hermes-agent.nousresearch.com/install.ps1)" } catch { Warn "El instalador de Hermes reporto: $($_.Exception.Message)" }
  Refresh-Path
  if (Have hermes) { Ok "Hermes instalado." } else { Warn "No pude confirmar Hermes en PATH; el asistente lo revisara." }
}
# Seed Hermes with a non-interactive baseline so the user is NEVER dropped into Hermes'
# own question wizard. Olivaw configures the model/owner-lock itself later, so defaults
# are fine here. Best-effort; failure is non-fatal (the wizard still opens).
if (Have hermes) {
  try { Start-Process -FilePath "hermes" -ArgumentList "setup","--non-interactive" -Wait -NoNewWindow -ErrorAction SilentlyContinue | Out-Null } catch {}
}

# 2) uv (Python manager) -----------------------------------------------------
Step "2/5  uv (gestor de Python)"
$uv = Have uv
if (-not $uv -and (Test-Path "$env:LOCALAPPDATA\hermes\bin\uv.exe")) { $uv = "$env:LOCALAPPDATA\hermes\bin\uv.exe" }
if (-not $uv -and -not $NoAutoInstall) {
  Info "Instalando uv..."
  try { powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null } catch { Warn "uv install: $($_.Exception.Message)" }
  Refresh-Path
  $uv = (Have uv)
  if (-not $uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
}
if ($uv) { Ok "uv: $uv" } else { throw "No se pudo obtener uv (necesario para Python). Reintenta o instala Hermes primero." }

# 3) Python via uv (runs our bridge/supervisor/wizard) -----------------------
Step "3/5  Python"
# Not silenced: this downloads ~21 MB and takes a while, and a progress bar is the difference
# between "it is working" and "it is frozen" for someone watching a fresh install.
Info "Preparando Python 3.12 (puede tardar; descarga ~21 MB la primera vez)..."
$rc = Native $uv @("python","install","3.12")
if ($rc -ne 0) { Warn "uv python install devolvio $rc; intento localizar Python igualmente." }
$py = (Native $uv @("python","find","3.12") -Capture | Select-Object -First 1)
if (-not $py -or -not (Test-Path $py)) { $py = (Native $uv @("python","find") -Capture | Select-Object -First 1) }
if (-not $py -or -not (Test-Path $py)) {
  throw "uv no pudo proveer Python 3.12. Ejecuta '$uv python install 3.12' en esta ventana para ver el motivo."
}
$pyDir = Split-Path $py
$pyw = Join-Path $pyDir "pythonw.exe"; if (-not (Test-Path $pyw)) { $pyw = $py }
Ok "Python: $py"

# 4) the brain CLI (Claude Code by default, Codex with -Engine codex) --------
$brainName = if ($Engine -eq "codex") { "Codex" } else { "Claude Code" }
Step "4/5  $brainName (el cerebro)"
Refresh-Path
$claude = ""
$codex = ""
if ($Engine -eq "codex") {
  $codex = Have codex
  if ($codex) {
    Ok "Codex ya esta instalado."
  } elseif ($NoAutoInstall) {
    Warn "Codex no encontrado y auto-install desactivado."
  } else {
    Info "Instalando Codex (npm install -g @openai/codex)..."
    if ((Native "npm" @("install","-g","@openai/codex") -Quiet) -ne 0) { Warn "npm no pudo instalar Codex." }
    Refresh-Path
    $codex = Have codex
    if ($codex) { Ok "Codex instalado." } else { Warn "No pude confirmar Codex en PATH; el asistente lo revisara." }
  }
} else {
  $claude = Have claude
  if ($claude) {
    Ok "Claude Code ya esta instalado."
  } elseif ($NoAutoInstall) {
    Warn "Claude Code no encontrado y auto-install desactivado."
  } else {
    Info "Instalando Claude Code (instalador nativo)..."
    try { powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" | Out-Null } catch { Warn "Claude install: $($_.Exception.Message)" }
    Refresh-Path
    $claude = Have claude
    if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = "$env:USERPROFILE\.local\bin\claude.exe" }
    if ($claude) { Ok "Claude Code instalado." } else { Warn "No pude confirmar Claude en PATH; el asistente lo revisara." }
  }
}

# 5) olivaw (download + verify + extract) ------------------------------------
Step "5/5  olivaw"
if ($LocalSource) {
  Info "Instalando desde copia local: $LocalSource"
  Copy-Item -Recurse -Force (Join-Path $LocalSource "src") $InstallDir
  Copy-Item -Recurse -Force (Join-Path $LocalSource "templates") $InstallDir
  Copy-Item -Force (Join-Path $LocalSource "VERSION") $InstallDir
} else {
  $tmp = Join-Path $env:TEMP ("olv_" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  Info "Descargando la ultima version de $Repo ..."
  $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent"="olivaw-installer" }
  $zipA = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
  $shaA = $rel.assets | Where-Object { $_.name -like "*.zip.sha256" } | Select-Object -First 1
  if (-not $zipA) { throw "No hay asset .zip en la ultima release de $Repo." }
  $zip = Join-Path $tmp $zipA.name
  Invoke-WebRequest -Uri $zipA.browser_download_url -OutFile $zip
  if ($shaA) {
    $sha = Join-Path $tmp $shaA.name
    Invoke-WebRequest -Uri $shaA.browser_download_url -OutFile $sha
    $expected = ((Get-Content $sha -Raw).Trim().Split()[0]).ToLower()
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "Checksum no coincide - instalacion abortada." }
    Ok "Checksum verificado."
  } else { throw "La release no incluye .sha256 - instalacion abortada (no se ejecuta codigo sin verificar)." }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  Copy-Item -Recurse -Force (Join-Path $tmp "src") $InstallDir
  Copy-Item -Recurse -Force (Join-Path $tmp "templates") $InstallDir
  Copy-Item -Force (Join-Path $tmp "VERSION") $InstallDir
}
Ok "olivaw instalado en $InstallDir (v$(Get-Content (Join-Path $InstallDir 'VERSION')))"

# headless config (only when a token was passed) -----------------------------
if (-not $UseWizard) {
  $cfg = [ordered]@{
    repo = $Repo; auto_update = $true
    bridge_cmd = @($py, "src\claude_bridge.py", "--port", "8790"); bridge_cwd = $InstallDir
    bridge_url = "http://127.0.0.1:8790"
    env = @{ OLIVAW_ENGINE = $Engine; CLAUDE_BRIDGE_CLAUDE = $claude; OLIVAW_CODEX = $codex; CLAUDE_BRIDGE_WORKSPACE = $Workspace }
    telegram_bot_token = $BotToken; telegram_chat_id = $ChatId; maintainer_chat_id = $MaintainerId
    poll_minutes = 45; idle_seconds = 300; nightly_hour = 4; lang = $Lang
  }
  ($cfg | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $InstallDir "updater.config.json")
  Ok "Escrito updater.config.json"
} else {
  Info "La configuracion final se hara en el asistente del navegador."
}

# App shortcut: "Olivaw" on the Desktop + Start Menu, reopens the setup UI ---
# Without this the user has no way back into the wizard after the first run (they would
# need a terminal), which is exactly what this kit is meant to avoid.
$openVbs = Join-Path $InstallDir "Olivaw.vbs"
$wizPy   = Join-Path $InstallDir "src\wizard\wizard_server.py"
@"
' Opens the Olivaw setup/help UI in the browser (no console window).
Set s = CreateObject("Wscript.Shell")
s.CurrentDirectory = "$InstallDir"
s.Run """$pyw"" ""$wizPy""", 0, False
"@ | Out-File -Encoding ascii $openVbs
try {
  $ws = New-Object -ComObject WScript.Shell
  foreach ($dir in @([Environment]::GetFolderPath('Desktop'),
                     (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))) {
    if (-not (Test-Path $dir)) { continue }
    $lnk = $ws.CreateShortcut((Join-Path $dir 'Olivaw.lnk'))
    $lnk.TargetPath = $openVbs
    $lnk.WorkingDirectory = $InstallDir
    # wscript.exe carries a usable icon; harmless if the index is missing.
    $lnk.IconLocation = "$env:SystemRoot\System32\wscript.exe,0"
    $lnk.Description = "Abrir la configuracion / ayuda de Olivaw"
    $lnk.Save()
  }
  Ok "Acceso directo 'Olivaw' creado (Escritorio y menu Inicio)."
} catch { Warn "No pude crear el acceso directo: $($_.Exception.Message)" }

# register supervisor at login (runs with the uv-managed Python) -------------
$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) "Olivaw.vbs"
$launch = Join-Path $InstallDir "src\launcher.py"
@"
Set s = CreateObject("Wscript.Shell")
s.Run """$pyw"" ""$launch""", 0, False
"@ | Out-File -Encoding ascii $vbs
Ok "Auto-arranque registrado."
if (-not $UseWizard) {
  Start-Process -FilePath $pyw -ArgumentList "`"$launch`"" -WindowStyle Hidden
  Ok "Supervisor iniciado."
}

# finish ---------------------------------------------------------------------
if ($UseWizard) {
  $wiz = Join-Path $InstallDir "src\wizard\wizard_server.py"
  Write-Host "`n=== Abriendo el asistente de configuracion ===" -ForegroundColor White
  Info "Sigue los pasos en el navegador: probar el cerebro, conectar Hermes, ponerle nombre"
  Info "a tu agente y vincularlo a tu Telegram. El asistente activa todo al final."
  Start-Process -FilePath $py -ArgumentList "`"$wiz`"" -WorkingDirectory $InstallDir
  Write-Host "`nSi el navegador no abre solo, ejecuta:  `"$py`" `"$wiz`"`n" -ForegroundColor Green
} else {
  Write-Host "`n=== Casi listo ===" -ForegroundColor White
  if ($Engine -eq "codex") {
    Info "1) Inicia sesion en Codex una vez:  codex login"
  } else {
    Info "1) Inicia sesion en Claude una vez:  claude"
  }
  Info "2) Asegurate de que Hermes este configurado (hermes setup) y su gateway corriendo."
  Info "3) Escribe a tu bot de Telegram para probar."
  Write-Host "`nLas actualizaciones son automaticas y silenciosas.`n" -ForegroundColor Green
}
