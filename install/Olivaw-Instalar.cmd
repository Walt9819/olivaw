@echo off
setlocal
rem ============================================================================
rem  Olivaw - instalador para Windows.
rem
rem  Para quien lo usa: descarga este archivo y haz doble clic. Nada mas.
rem  Windows va a preguntar una vez si confias en el archivo ("Editor
rem  desconocido"): es normal, no esta firmado. Pulsa "Ejecutar" / "Mas
rem  informacion" -> "Ejecutar de todas formas".
rem
rem  Para quien lo mantiene: esto existe para que nadie tenga que abrir
rem  PowerShell ni pegar un comando. Es un envoltorio de dos lineas alrededor
rem  del one-liner de siempre; toda la logica sigue en install-windows.ps1, que
rem  se descarga de main en cada ejecucion, asi que este archivo no caduca.
rem
rem  Deliberadamente NO pide permisos de administrador por su cuenta. En una
rem  cuenta de usuario estandar, UAC pediria la contrasena de OTRA cuenta y el
rem  instalador acabaria escribiendo en el perfil de ese administrador, no en el
rem  de quien va a usar Olivaw. Casi todo el instalador no necesita permisos; si
rem  algo falla por eso, la propia ventana ofrece "Reintentar como
rem  administrador", y ahi si conserva las rutas del usuario original.
rem ============================================================================

rem Overridable so a fork can point at its own copy - and so this file can be tested
rem without reaching GitHub. Same idea as OLIVAW_REPO in launcher.py.
if "%OLIVAW_PS1%"=="" set "OLIVAW_PS1=https://raw.githubusercontent.com/Walt9819/olivaw/main/install/install-windows.ps1"

title Instalar Olivaw
echo.
echo   Instalando Olivaw
echo   -----------------
echo   Se abrira una ventana con el progreso. La primera vez puede tardar
echo   varios minutos: descarga e instala todo lo que hace falta.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { iex (irm '%OLIVAW_PS1%') } catch { Write-Host ''; Write-Host ('  No se pudo instalar: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"

if errorlevel 1 (
  echo.
  echo   ---------------------------------------------------------------
  echo   La instalacion no pudo terminar.
  echo.
  echo   Prueba esto, en orden:
  echo     1^) Comprueba que tienes internet.
  echo     2^) Haz clic derecho en este archivo y elige
  echo        "Ejecutar como administrador".
  echo     3^) Si sigue fallando, manda esta ventana a quien te compartio
  echo        Olivaw ^(clic derecho -^> Seleccionar todo -^> Enter para copiar^).
  echo   ---------------------------------------------------------------
  echo.
  pause
  exit /b 1
)

rem En el camino normal la ventana de instalacion ya ha dicho como fue y ha
rem abierto el asistente en el navegador, asi que esta consola no tiene nada mas
rem que aportar y se cierra sola.
endlocal
exit /b 0
