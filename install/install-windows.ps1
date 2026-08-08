<#
  Hermes Bridge - Windows installer (run WITH the user, once).

  What it does:
    1. checks prerequisites (Python 3, Node, Claude Code CLI, Hermes) and installs the
       Claude Code CLI if Node is present;
    2. downloads the latest release from GitHub (or -LocalSource for first deploys),
       verifies its SHA-256, and extracts it to the install dir;
    3. registers the SUPERVISOR to auto-start at login (hidden);
    4. finishes in the browser onboarding WIZARD (default), or, if -BotToken is given,
       writes updater.config.json from parameters and starts the supervisor (headless).

  After this, updates are fully automatic - nothing else to run, ever.

  Examples:
    # Non-technical path (opens the wizard):
    powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Repo "walt/hermes-bridge"

    # Headless path (no wizard):
    powershell -ExecutionPolicy Bypass -File install-windows.ps1 -NoWizard `
      -Repo "walt/hermes-bridge" -BotToken "123:ABC" -ChatId "8114329186" -MaintainerId "8114329186"
#>
[CmdletBinding()]
param(
  [string]$Repo = "Walt9819/olivaw",
  [string]$BotToken = "",
  [string]$ChatId = "",
  [string]$MaintainerId = "",
  [string]$InstallDir = "$env:LOCALAPPDATA\HermesBridge",
  [string]$Workspace = "$env:USERPROFILE\hermes-workspace",
  [string]$LocalSource = "",          # install from a local repo copy instead of GitHub
  [string]$Lang = "es",
  [switch]$NoWizard                   # skip the browser wizard (headless/param install)
)
$ErrorActionPreference = "Stop"
# Wizard mode: when no bot token is passed, finish setup in the friendly browser
# wizard instead of writing config from parameters. This is the non-technical path.
$UseWizard = (-not $NoWizard) -and [string]::IsNullOrWhiteSpace($BotToken)
function Info($m){ Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

Write-Host "`n=== Hermes Bridge installer (Windows) ===`n" -ForegroundColor White

# 1) Prerequisites -----------------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Python 3 not found. Install from https://python.org (check 'Add to PATH'), then re-run." }
Ok "Python: $py"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Warn "Node.js not found - install from https://nodejs.org, then re-run." }
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
  if (Get-Command npm -ErrorAction SilentlyContinue) { Info "Installing Claude Code CLI..."; npm install -g @anthropic-ai/claude-code | Out-Null; $claude = (Get-Command claude -ErrorAction SilentlyContinue).Source }
}
if ($claude) { Ok "Claude Code: $claude" } else { Warn "Claude Code CLI not found - install with: npm install -g @anthropic-ai/claude-code" }
if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { Warn "Hermes not found - install it per its docs before first use (hermes.nousresearch.com)." }

# 2) Get the code ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$tmp = Join-Path $env:TEMP ("hb_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
if ($LocalSource) {
  Info "Installing from local source: $LocalSource"
  Copy-Item -Recurse -Force (Join-Path $LocalSource "src") $InstallDir
  Copy-Item -Recurse -Force (Join-Path $LocalSource "templates") $InstallDir
  Copy-Item -Force (Join-Path $LocalSource "VERSION") $InstallDir
} else {
  Info "Fetching latest release from $Repo ..."
  $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent"="hb-installer" }
  $zipA = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
  $shaA = $rel.assets | Where-Object { $_.name -like "*.zip.sha256" } | Select-Object -First 1
  if (-not $zipA) { throw "No .zip asset on the latest release of $Repo." }
  $zip = Join-Path $tmp $zipA.name
  Invoke-WebRequest -Uri $zipA.browser_download_url -OutFile $zip
  if ($shaA) {
    $sha = Join-Path $tmp $shaA.name
    Invoke-WebRequest -Uri $shaA.browser_download_url -OutFile $sha
    $expected = ((Get-Content $sha -Raw).Trim().Split()[0]).ToLower()
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "Checksum mismatch - refusing to install." }
    Ok "Checksum verified"
  } else { Warn "No .sha256 on release - installing unverified (add checksums to releases!)." }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  Copy-Item -Recurse -Force (Join-Path $tmp "src") $InstallDir
  Copy-Item -Recurse -Force (Join-Path $tmp "templates") $InstallDir
  Copy-Item -Force (Join-Path $tmp "VERSION") $InstallDir
}
Ok "Code installed to $InstallDir  (v$(Get-Content (Join-Path $InstallDir 'VERSION')))"

# 3) updater.config.json -----------------------------------------------------
New-Item -ItemType Directory -Force -Path $Workspace | Out-Null
if ($UseWizard) {
  Info "Final setup (token, owner, agent personality) happens in the browser wizard."
} else {
  $cfg = [ordered]@{
    repo = $Repo; auto_update = $true
    bridge_cmd = @($py, "src\claude_bridge.py"); bridge_cwd = $InstallDir
    bridge_url = "http://127.0.0.1:8790"
    env = @{ CLAUDE_BRIDGE_CLAUDE = $claude; CLAUDE_BRIDGE_WORKSPACE = $Workspace }
    telegram_bot_token = $BotToken; telegram_chat_id = $ChatId; maintainer_chat_id = $MaintainerId
    poll_minutes = 45; idle_seconds = 300; nightly_hour = 4; lang = $Lang
  }
  ($cfg | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $InstallDir "updater.config.json")
  Ok "Wrote updater.config.json"
}

# 4) Point Hermes at the bridge (best-effort) --------------------------------
$hcfg = Get-ChildItem -Path @("$env:LOCALAPPDATA\hermes","$env:APPDATA\hermes","$env:USERPROFILE\.config\hermes") -Filter config.yaml -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($hcfg) {
  Copy-Item $hcfg.FullName "$($hcfg.FullName).bak" -Force
  Warn "Found Hermes config at $($hcfg.FullName) - backup saved. The wizard writes a personalized snippet to paste in."
} else { Warn "Hermes config.yaml not found - the wizard will generate the model block to apply after installing Hermes." }

# 5) Auto-start the SUPERVISOR at login + (headless) start now ----------------
$pyw = $py -replace "python\.exe$","pythonw.exe"; if (-not (Test-Path $pyw)) { $pyw = $py }
$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) "HermesBridge.vbs"
$launch = Join-Path $InstallDir "src\launcher.py"
@"
Set s = CreateObject("Wscript.Shell")
s.Run """$pyw"" ""$launch""", 0, False
"@ | Out-File -Encoding ascii $vbs
Ok "Registered auto-start: $vbs"
if (-not $UseWizard) {
  Start-Process -FilePath $pyw -ArgumentList "`"$launch`"" -WindowStyle Hidden
  Ok "Supervisor started (it launches the bridge and handles all future updates)."
}

# 6) Finish - launch the wizard (non-technical path) or print guidance -------
if ($UseWizard) {
  $wiz = Join-Path $InstallDir "src\wizard\wizard_server.py"
  Write-Host "`n=== Opening the setup wizard ===" -ForegroundColor White
  Info "It opens in your browser. Pick the brain, connect Hermes, give your agent a"
  Info "personality, and link it to your Telegram. The wizard activates everything at the end."
  Start-Process -FilePath $py -ArgumentList "`"$wiz`"" -WorkingDirectory $InstallDir
  Write-Host "`nIf the browser does not open, run:  python `"$wiz`"`n" -ForegroundColor Green
} else {
  Write-Host "`n=== Almost done ===" -ForegroundColor White
  Info "1) Log into Claude Code once (subscription):  claude"
  Info "2) Make sure Hermes is installed and its gateway is running (hermes gateway start)."
  Info "3) Message the Telegram bot to test."
  Write-Host "`nUpdates from now on are automatic and silent - the bot will just say 'updated to vX'.`n" -ForegroundColor Green
}
