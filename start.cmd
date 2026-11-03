@echo off
rem Starts Sodaudio engine in its own window and opens the app in the browser.
rem   start.cmd            start (or, if it is already running, just open the page)
rem   start.cmd --no-open  start without opening the page (start with Windows)
rem   start.cmd --tray     also show the tray icon (tray.pyw)
setlocal
cd /d "%~dp0"
set "PORT=8770"
if not "%SODAUDIO_PORT%"=="" set "PORT=%SODAUDIO_PORT%"
set "OPEN=1"
set "TRAY=0"
for %%a in (%*) do (
    if /i "%%~a"=="--no-open" set "OPEN=0"
    if /i "%%~a"=="--tray" set "TRAY=1"
)
set "URL=http://127.0.0.1:%PORT%"

call :up
if not errorlevel 1 goto running

if exist ".venv\Scripts\python.exe" goto ui
echo First run: creating the engine's Python 3.12 environment in .venv ...
where uv >nul 2>nul
if errorlevel 1 goto pip
uv venv --python 3.12 .venv || goto nopython
uv pip install --python .venv\Scripts\python.exe -r engine\requirements.txt || goto nopython
goto ui
:pip
py -3.12 -m venv .venv || goto nopython
.venv\Scripts\python.exe -m pip install -r engine\requirements.txt || goto nopython

:ui
if exist "ui\dist\index.html" goto start
echo The UI is not built; building it (needs Node.js) ...
where npm >nul 2>nul || (echo Node.js is not installed and ui\dist is missing. & pause & exit /b 1)
pushd ui
call npm install --no-audit --no-fund && call npm run build
popd
if not exist "ui\dist\index.html" (echo The UI build failed. & pause & exit /b 1)

:start
start "Sodaudio engine" ".venv\Scripts\python.exe" -m engine.app --port %PORT%
echo Waiting for the engine on %URL% (the first start after setup can take a couple of minutes) ...
for /l %%i in (1,1,240) do (
    call :up && goto running
    >nul ping -n 2 127.0.0.1
)
echo The engine did not answer in time; see the "Sodaudio engine" window.
pause
exit /b 1

:running
if "%TRAY%"=="1" start "" ".venv\Scripts\pythonw.exe" tray.pyw
if "%OPEN%"=="1" start "" "%URL%/"
exit /b 0

:nopython
echo Could not set up Python 3.12 for the engine. Install Python 3.12 (python.org) or uv, then run start.cmd again.
pause
exit /b 1

:up
curl.exe -s -o nul -m 2 "%URL%/api/health"
exit /b %errorlevel%
