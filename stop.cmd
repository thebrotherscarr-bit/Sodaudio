@echo off
rem Asks the engine to finish the current job and exit. "stop.cmd --now" interrupts the running job instead.
set "PORT=8770"
if not "%SODAUDIO_PORT%"=="" set "PORT=%SODAUDIO_PORT%"
set "MODE=finish"
if /i "%~1"=="--now" set "MODE=stop"
curl.exe -s -X POST -H "Content-Type: application/json" -d "{\"mode\": \"%MODE%\"}" "http://127.0.0.1:%PORT%/api/shutdown" || echo The engine is not running.
echo.
