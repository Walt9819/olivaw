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

  Usage (non-technical, opens the wizard). It asks which brain you want:
    iex (irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1)
  Choosing the brain up front (no question asked):
    $env:OLIVAW_ENGINE='codex'; iex (irm https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1)
  Advanced / headless (configure from params, no wizard):
    install-windows.ps1 -NoWizard -BotToken "123:ABC" -ChatId "8114329186"
#>
# PositionalBinding=$false so a stray argument can never bind silently. Before this, one
# leftover fragment of a split path landed in -BotToken and, because a non-empty BotToken
# means "headless", the installer skipped the wizard and reconfigured Hermes from garbage.
# Named-only turns that into an immediate, visible error instead.
[CmdletBinding(PositionalBinding=$false)]
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
  [switch]$NoUi,
  [switch]$NoWizard,
  [switch]$NoAutoInstall
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # faster Invoke-WebRequest

# Any terminating error, said out loud on STDOUT before the script dies.
#
# Without this the installer was undiagnosable. The window runs this file again as a child
# with -RedirectStandardOutput <log> -RedirectStandardError <log>.err, and it only ever
# tailed the first of those - so a `throw` (which goes to stderr, like every terminating
# error) produced a window showing the banner, then "La instalacion fallo (codigo 1)" and
# nothing else. "Copiar detalles" copied that same nothing. A real failure on a real
# machine reached us as a photograph with no cause in it.
#
# `break` re-throws afterwards, so the exit code and stderr stay exactly as they were; this
# only adds a copy of the reason to the stream a human is actually looking at.
trap {
  try {
    Write-Host "`n=== ERROR ===" -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.PositionMessage) {
      Write-Host ("  " + ($_.InvocationInfo.PositionMessage -replace "`r?`n", "`r`n  "))
    }
    if ($_.Exception.InnerException) {
      Write-Host ("  causa: " + $_.Exception.InnerException.Message)
    }
    Write-Host "  (paso: $script:CurrentStep)"
  } catch { }
  break
}
$script:CurrentStep = "arranque" 
$UseWizard = (-not $NoWizard) -and [string]::IsNullOrWhiteSpace($BotToken)

function Info($m){ Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }
function Step($m){ $script:CurrentStep = $m; Write-Host "`n> $m" -ForegroundColor White }

function Is-Admin {
  # Whether this process is elevated. Most of the install is per-user and needs nothing,
  # but the third-party installers it calls (Hermes brings Python, Node, ripgrep, ffmpeg,
  # Git Bash) can need it - and "run it as administrator" turned out to be the fix for a
  # real failure that reported itself only as "codigo 1".
  try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
      [Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch { return $false }
}
# Where the tools this installer depends on actually land. A fresh install writes some of these
# to the USER PATH only after the shell that will need them has already started, so re-reading the
# registry is not enough on its own.
function Tool-Dirs {
  @(
    (Join-Path $env:USERPROFILE ".local\bin"),          # uv, and the Python shims it creates
    (Join-Path $env:LOCALAPPDATA "hermes\bin"),         # hermes + its bundled uv
    (Join-Path $env:APPDATA "npm")                      # npm -g (codex lives here)
  ) | Where-Object { $_ -and (Test-Path $_) }
}

function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable('Path','Machine')
  $u = [Environment]::GetEnvironmentVariable('Path','User')
  $parts = @($m,$u) | Where-Object { $_ }
  $env:Path = ($parts -join ';')
  # Add the known tool dirs to THIS session, so a tool installed a moment ago is findable even
  # though the registry has not caught up.
  foreach ($d in (Tool-Dirs)) {
    if (($env:Path -split ';') -notcontains $d) { $env:Path = "$d;$env:Path" }
  }
}

# Make it stick for every future shell, shortcut and background process. User scope only - the
# machine PATH is not ours to touch - and idempotent.
function Ensure-UserPath {
  $added = @()
  try {
    $cur = [Environment]::GetEnvironmentVariable('Path','User')
    $have = @()
    if ($cur) { $have = $cur -split ';' | Where-Object { $_ } }
    foreach ($d in (Tool-Dirs)) {
      if ($have -notcontains $d) { $have += $d; $added += $d }
    }
    if ($added.Count -gt 0) {
      [Environment]::SetEnvironmentVariable('Path', ($have -join ';'), 'User')
      Ok ("PATH del usuario actualizado (" + ($added -join '; ') + ")")
    }
  } catch {
    Warn "No pude actualizar el PATH del usuario: $($_.Exception.Message)"
  }
  return $added
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


# ── the installer window ────────────────────────────────────────────────────
# Everything before the browser wizard used to be raw console text. This is the same install,
# with a face: one question, a progress bar, and plain-language status. It runs THIS script again
# as a child (-NoUi) rather than duplicating any step, so there is only one installer to trust.
function Get-SelfCopy {
  if ($PSCommandPath -and (Test-Path $PSCommandPath)) { return $PSCommandPath }
  # Piped through iex: the script cannot see its own text, so fetch the same file it came from.
  $dst = Join-Path $env:TEMP "olivaw-install.ps1"
  try {
    $u = "https://raw.githubusercontent.com/$Repo/main/install/install-windows.ps1"
    Invoke-WebRequest -Uri $u -OutFile $dst -UseBasicParsing -ErrorAction Stop
    if ((Get-Item $dst).Length -gt 2000) { return $dst }
  } catch { }
  return ""
}

function Show-InstallUi {
  param([string]$SelfPath)
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
  } catch { return "console" }

  $haveClaude = [bool](Have claude)
  $haveCodex  = [bool](Have codex)

  $f = New-Object System.Windows.Forms.Form
  $f.Text = "Instalar Olivaw"
  $f.Size = New-Object System.Drawing.Size(660, 560)
  $f.StartPosition = "CenterScreen"
  $f.FormBorderStyle = "FixedDialog"
  $f.MaximizeBox = $false
  $f.BackColor = [System.Drawing.Color]::White
  $f.Font = New-Object System.Drawing.Font("Segoe UI", 9)

  $title = New-Object System.Windows.Forms.Label
  $title.Text = "Vamos a instalar tu agente"
  $title.Font = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)
  $title.Location = New-Object System.Drawing.Point(24, 20)
  $title.Size = New-Object System.Drawing.Size(600, 32)
  $f.Controls.Add($title)

  $sub = New-Object System.Windows.Forms.Label
  $sub.Text = "Se instala todo solo: Hermes, Python y el cerebro que elijas. Puede tardar varios minutos la primera vez. No tienes que hacer nada mas."
  $sub.ForeColor = [System.Drawing.Color]::DimGray
  $sub.Location = New-Object System.Drawing.Point(26, 52)
  $sub.Size = New-Object System.Drawing.Size(600, 40)
  $f.Controls.Add($sub)

  $grp = New-Object System.Windows.Forms.GroupBox
  $grp.Text = " El cerebro de tu agente "
  $grp.Location = New-Object System.Drawing.Point(24, 100)
  $grp.Size = New-Object System.Drawing.Size(600, 104)
  $f.Controls.Add($grp)

  $rbClaude = New-Object System.Windows.Forms.RadioButton
  $rbClaude.Text = "Claude Code  -  cuenta de pago de Claude (Pro o Max)" + $(if ($haveClaude) { "   [ya instalado]" } else { "" })
  $rbClaude.Location = New-Object System.Drawing.Point(18, 28)
  $rbClaude.Size = New-Object System.Drawing.Size(560, 22)
  $rbClaude.Checked = ($Engine -ne "codex")
  $grp.Controls.Add($rbClaude)

  $rbCodex = New-Object System.Windows.Forms.RadioButton
  $rbCodex.Text = "Codex  -  cuenta de pago de ChatGPT (Plus, Pro o Business)" + $(if ($haveCodex) { "   [ya instalado]" } else { "" })
  $rbCodex.Location = New-Object System.Drawing.Point(18, 54)
  $rbCodex.Size = New-Object System.Drawing.Size(560, 22)
  $rbCodex.Checked = ($Engine -eq "codex")
  $grp.Controls.Add($rbCodex)

  $hint = New-Object System.Windows.Forms.Label
  $hint.Text = "Solo se instala el que elijas. Puedes cambiarlo despues."
  $hint.ForeColor = [System.Drawing.Color]::DimGray
  $hint.Location = New-Object System.Drawing.Point(20, 78)
  $hint.Size = New-Object System.Drawing.Size(560, 18)
  $grp.Controls.Add($hint)

  $btn = New-Object System.Windows.Forms.Button
  $btn.Text = "Instalar"
  $btn.Location = New-Object System.Drawing.Point(24, 216)
  $btn.Size = New-Object System.Drawing.Size(140, 34)
  $btn.BackColor = [System.Drawing.Color]::FromArgb(91, 91, 214)
  $btn.ForeColor = [System.Drawing.Color]::White
  $btn.FlatStyle = "Flat"
  $f.Controls.Add($btn)
  $f.AcceptButton = $btn

  $status = New-Object System.Windows.Forms.Label
  $status.Text = ""
  $status.Location = New-Object System.Drawing.Point(178, 226)
  $status.Size = New-Object System.Drawing.Size(446, 20)
  $f.Controls.Add($status)

  $bar = New-Object System.Windows.Forms.ProgressBar
  $bar.Location = New-Object System.Drawing.Point(24, 258)
  $bar.Size = New-Object System.Drawing.Size(600, 12)
  $bar.Minimum = 0; $bar.Maximum = 100
  $f.Controls.Add($bar)

  $box = New-Object System.Windows.Forms.TextBox
  $box.Multiline = $true
  $box.ScrollBars = "Vertical"
  $box.ReadOnly = $true
  $box.BackColor = [System.Drawing.Color]::FromArgb(248, 249, 255)
  $box.Font = New-Object System.Drawing.Font("Consolas", 8.5)
  $box.Location = New-Object System.Drawing.Point(24, 282)
  $box.Size = New-Object System.Drawing.Size(600, 200)
  $f.Controls.Add($box)

  # Offered only when the run failed AND this process is not elevated - which is the single
  # most common fix, and the one that turned out to be the answer for a real reported
  # failure. Not offered up front: most of the install needs nothing, and asking a
  # non-technical owner for administrator rights they may not have is a worse first step
  # than simply trying.
  $again = New-Object System.Windows.Forms.Button
  $again.Text = "Reintentar como administrador"
  $again.Location = New-Object System.Drawing.Point(24, 492)
  $again.Size = New-Object System.Drawing.Size(230, 28)
  $again.FlatStyle = "Flat"
  $again.Visible = $false
  $f.Controls.Add($again)

  $copy = New-Object System.Windows.Forms.Button
  $copy.Text = "Copiar detalles"
  $copy.Location = New-Object System.Drawing.Point(474, 492)
  $copy.Size = New-Object System.Drawing.Size(150, 28)
  $copy.FlatStyle = "Flat"
  $copy.Visible = $false
  $f.Controls.Add($copy)

  $state = [hashtable]::Synchronized(@{ proc = $null; pos = 0; errpos = 0; log = ""; done = $false; result = "console" })
  $logFile = Join-Path $env:TEMP ("olivaw-install-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

  # Start-Process joins -ArgumentList with spaces and does NOT quote the elements, so a path
  # containing a space is split into two arguments. Measured: passing "...\Juan Perez\Olivaw"
  # installed into "...\Juan", and the leftover "Perez\Olivaw" bound POSITIONALLY to
  # -BotToken - which flipped the installer into its headless branch and sent it off to
  # reconfigure Hermes from a garbage token. Every user whose Windows name has a space in it
  # hit that. Trailing backslashes go because "C:\path\" escapes the closing quote in Windows
  # argv parsing and swallows the next argument with it.
  # One helper, used by BOTH launches below - the child run and the elevated retry.
  $qarg = { param($v) '"' + ("$v".TrimEnd('\') ) + '"' }

  $append = {
    param($text)
    if (-not $text) { return }
    $box.AppendText($text)
    $box.SelectionStart = $box.TextLength
    $box.ScrollToCaret()
  }

  $timer = New-Object System.Windows.Forms.Timer
  $timer.Interval = 400
  $timer.Add_Tick({
    # Tail the child's redirected output. A file plus a timer beats stream events here: no
    # cross-runspace handlers, and the log still exists afterwards for "copy the details".
    if (Test-Path $logFile) {
      try {
        $fs = [System.IO.File]::Open($logFile, 'Open', 'Read', 'ReadWrite')
        $fs.Seek($state.pos, 'Begin') | Out-Null
        $sr = New-Object System.IO.StreamReader($fs)
        $chunk = $sr.ReadToEnd()
        $state.pos = $fs.Position
        $sr.Close(); $fs.Close()
        if ($chunk) {
          $state.log += $chunk
          & $append $chunk
          foreach ($line in ($chunk -split "`r?`n")) {
            if ($line -match '^\s*>\s*(\d)\s*/\s*5\s+(.*)$') {
              $bar.Value = [Math]::Min(100, [int]$matches[1] * 18)
              $status.Text = $matches[2].Trim()
            }
          }
        }
      } catch { }
    }
    # stderr lives in its own file because Start-Process refuses to point both streams
    # at one path. Tailing only stdout is what made the last failure unreadable.
    $errFile = $logFile + ".err"
    if (Test-Path $errFile) {
      try {
        $efs = [System.IO.File]::Open($errFile, 'Open', 'Read', 'ReadWrite')
        $efs.Seek($state.errpos, 'Begin') | Out-Null
        $esr = New-Object System.IO.StreamReader($efs)
        $echunk = $esr.ReadToEnd()
        $state.errpos = $efs.Position
        $esr.Close(); $efs.Close()
        if ($echunk -and $echunk.Trim()) {
          $state.log += $echunk
          & $append $echunk
        }
      } catch { }
    }
    if ($state.proc -and $state.proc.HasExited -and -not $state.done) {
      $state.done = $true
      $timer.Stop()
      Start-Sleep -Milliseconds 300
      $code = $null
      try { $code = $state.proc.ExitCode } catch { }
      if ($code -eq 0) {
        $bar.Value = 100
        $status.Text = "Listo"
        & $append "`r`n=== Instalacion terminada ===`r`nSe abrio el asistente en tu navegador. Si no, usa el acceso 'Olivaw' del escritorio.`r`n"
        $state.result = "done"
        $btn.Text = "Cerrar"
        $btn.Enabled = $true
      } else {
        $status.Text = "No se pudo terminar"
        $shown = "desconocido"
        if ($null -ne $code) { $shown = "$code" }
        & $append "`r`n=== La instalacion fallo (codigo $shown) ===`r`n"
        if (-not (Is-Admin)) {
          & $append ("Esto suele pasar cuando Windows no da permiso. Pulsa " +
                     "'Reintentar como administrador' (Windows te va a pedir confirmacion).`r`n")
          $again.Visible = $true
        }
        & $append "Si sigue fallando, pulsa 'Copiar detalles' y envialos a quien te compartio Olivaw.`r`n"
        $state.result = "failed"
        $btn.Text = "Cerrar"
        $btn.Enabled = $true
        $copy.Visible = $true
      }
    }
  })

  $btn.Add_Click({
    if ($state.done) { $f.Close(); return }
    if ($state.proc) { return }
    $chosen = "claude"
    if ($rbCodex.Checked) { $chosen = "codex" }
    $rbClaude.Enabled = $false; $rbCodex.Enabled = $false
    $btn.Enabled = $false
    $btn.Text = "Instalando..."
    $status.Text = "Preparando..."
    $bar.Value = 4
    & $append ("Cerebro elegido: " + $(if ($chosen -eq "codex") { "Codex" } else { "Claude Code" }) + "`r`n")
    $argList = @("-NoProfile","-ExecutionPolicy","Bypass","-File", (& $qarg $SelfPath),
                 "-Engine", $chosen, "-NoUi", "-Repo", (& $qarg $Repo),
                 "-InstallDir", (& $qarg $InstallDir), "-Workspace", (& $qarg $Workspace),
                 "-Lang", (& $qarg $Lang))
    if ($NoAutoInstall) { $argList += "-NoAutoInstall" }
    try {
      $state.proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList `
        -RedirectStandardOutput $logFile -RedirectStandardError ($logFile + ".err") `
        -NoNewWindow -PassThru
      # Touch the handle once: a process from Start-Process -PassThru does not keep one, and
      # without it .ExitCode comes back EMPTY when the child finishes - which read as "the
      # install failed" on a perfectly good run.
      $null = $state.proc.Handle
      $timer.Start()
    } catch {
      & $append ("No pude lanzar la instalacion: " + $_.Exception.Message + "`r`n")
      $state.result = "console"
      $f.Close()
    }
  })

  $again.Add_Click({
    # Relaunch the WHOLE installer elevated (UAC prompts), then step aside. Same quoting as
    # the child launch: an unquoted path with a space in it is how one of these arguments
    # ended up in -BotToken.
    $a = @("-NoProfile","-ExecutionPolicy","Bypass","-File", (& $qarg $SelfPath),
           "-Repo", (& $qarg $Repo), "-InstallDir", (& $qarg $InstallDir),
           "-Workspace", (& $qarg $Workspace), "-Lang", (& $qarg $Lang))
    try {
      Start-Process -FilePath "powershell.exe" -ArgumentList $a -Verb RunAs | Out-Null
      $state.result = "elevated"
      $f.Close()
    } catch {
      # The commonest reason is the owner clicking "No" on the UAC prompt. Say that rather
      # than leaving a dead button.
      & $append ("No se pudo abrir como administrador: " + $_.Exception.Message + "`r`n")
    }
  })

  $copy.Add_Click({
    $facts = @(
      "",
      "--- datos del equipo ---",
      ("windows   : " + [System.Environment]::OSVersion.VersionString),
      ("powershell: " + $PSVersionTable.PSVersion.ToString()),
      ("64-bit    : " + [System.Environment]::Is64BitOperatingSystem),
      ("repo      : " + $Repo),
      ("instalar en: " + $InstallDir),
      ("workspace : " + $Workspace),
      ("registro  : " + $logFile),
      ("errores   : " + $logFile + ".err")
    ) -join "`r`n"
    try { Set-Clipboard -Value ($state.log + $facts + "`r`n") } catch { }
    $copy.Text = "Copiado"
  })

  $f.Add_FormClosing({
    if ($state.proc -and -not $state.proc.HasExited) {
      $ans = [System.Windows.Forms.MessageBox]::Show(
        "La instalacion sigue en marcha. Si cierras ahora quedara a medias. Cerrar de todas formas?",
        "Olivaw", "YesNo", "Warning")
      if ($ans -eq "No") { $_.Cancel = $true; return }
      try { $state.proc.Kill() } catch { }
    }
  })

  [void]$f.ShowDialog()
  return $state.result
}

Write-Host "`n=== olivaw installer (Windows) ===" -ForegroundColor White
Write-Host "  Instalando todo automaticamente. Puede tardar varios minutos la primera vez.`n" -ForegroundColor DarkGray
# Stated up front, not guessed at afterwards: this line is in every transcript the owner
# sends us, so "was it elevated?" is never a question we have to ask them.
if (Is-Admin) { Ok "Permisos: administrador." }
else { Info "Permisos: usuario normal (suele bastar; si algo falla, se puede reintentar como administrador)." }

# ── which brain? asked FIRST, so nobody waits ten minutes to be asked a question ──
function Can-Prompt {
  # Read-Host blocks forever when there is nobody to answer, and this script is usually run
  # piped into iex - so only ask when stdin is a real console.
  try { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) }
  catch { return $false }
}
function Ask-Engine {
  Refresh-Path
  $haveClaude = [bool](Have claude)
  $haveCodex  = [bool](Have codex)
  Write-Host "> El cerebro de tu agente" -ForegroundColor White
  Write-Host ("    1) Claude Code  - cuenta de pago de Claude (Pro o Max)" +
              $(if ($haveClaude) { "   [ya instalado]" } else { "" }) + "   [recomendado]")
  Write-Host ("    2) Codex        - cuenta de pago de ChatGPT (Plus, Pro o Business)" +
              $(if ($haveCodex) { "   [ya instalado]" } else { "" }))
  Write-Host "  Se instala y configura solo el que elijas. Puedes cambiarlo despues desde el asistente." -ForegroundColor DarkGray
  for ($i = 0; $i -lt 3; $i++) {
    $a = ""
    try { $a = (Read-Host "  Elige 1 o 2 [1]") } catch { return "claude" }
    $a = "$a".Trim().ToLower()
    if ($a -eq "" -or $a -eq "1" -or $a -eq "claude") { return "claude" }
    if ($a -eq "2" -or $a -eq "codex")  { return "codex" }
    Warn "Responde 1 o 2."
  }
  return "claude"
}

# A window, when there is somebody to look at it. Falls through to the console flow for headless
# installs, for -NoUi (the child process the window itself runs), and if WinForms is unavailable.
if ($UseWizard -and -not $NoUi -and (Can-Prompt)) {
  $self = Get-SelfCopy
  if ($self) {
    $uiResult = Show-InstallUi -SelfPath $self
    if ($uiResult -ne "console") { return }
  } else {
    Info "No pude preparar la ventana de instalacion; sigo en esta consola."
  }
}

if (-not $PSBoundParameters.ContainsKey('Engine')) {
  $envEngine = "$env:OLIVAW_ENGINE".Trim().ToLower()
  if ($envEngine -eq "claude" -or $envEngine -eq "codex") {
    $Engine = $envEngine
    Info "Cerebro elegido por OLIVAW_ENGINE: $Engine"
  } elseif (Can-Prompt) {
    $Engine = Ask-Engine
  }
}
$brainName = if ($Engine -eq "codex") { "Codex" } else { "Claude Code" }
Ok "Cerebro: $brainName"

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
  # Quiet on purpose. Hermes prints a page of "configure Hermes using environment variables or
  # config commands / run 'hermes setup' in an interactive terminal" - every one of which Olivaw
  # runs itself a few seconds later. Showing it tells the owner to go do homework that is already
  # being done for them.
  Native "hermes" @("setup","--non-interactive") -Quiet | Out-Null
  Ok "Hermes configurado por Olivaw (modelo, canal y candado de dueno). No tienes que responder nada."
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

# 4) the brain CLI — only the one that was chosen is installed --------------
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
  } elseif (-not (Have npm)) {
    Warn "Codex se instala con npm (Node.js) y no encontre npm en este equipo."
    Info "Instala Node.js desde nodejs.org, abre una ventana nueva y vuelve a ejecutar esto."
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

# Everything that had to be installed is installed: make it findable from now on, so nobody has
# to open a terminal and edit PATH the way the first tester had to.
Refresh-Path
Ensure-UserPath | Out-Null

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
    # Rest hours for the update fallback (see launcher.py rest_window).
    update_from_hour = 3; update_until_hour = 7
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
  if ($Engine -eq "codex" -and -not $codex) {
    Warn "Codex no quedo instalado. El asistente te deja instalarlo con un boton en el primer paso."
  } elseif ($Engine -eq "codex") {
    Info "Cuando el asistente te lo pida, inicia sesion en Codex (un clic, una sola vez)."
  }
  Start-Process -FilePath $py -ArgumentList "`"$wiz`"" -WorkingDirectory $InstallDir
  Write-Host "`nSi el navegador no abre solo, ejecuta:  `"$py`" `"$wiz`"`n" -ForegroundColor Green
} else {
  Write-Host "`n=== Casi listo ===" -ForegroundColor White
  if ($Engine -eq "codex") {
    Info "1) Inicia sesion en Codex una vez:  codex login"
  } else {
    Info "1) Inicia sesion en Claude una vez:  claude"
  }
  Info "2) Deja el gateway de Hermes corriendo:  hermes gateway start"
  Info "3) Escribe a tu bot de Telegram para probar."
  Write-Host "`nLas actualizaciones son automaticas y silenciosas.`n" -ForegroundColor Green
}
