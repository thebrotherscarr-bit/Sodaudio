@echo off
rem Stops the engine: the running job and the queued ones are stopped, and the ComfyUI it started is turned off.
rem "stop.cmd --finish" lets the running job finish first and keeps the queued ones for the next start.
set "PORT=8770"
if not "%SODAUDIO_PORT%"=="" set "PORT=%SODAUDIO_PORT%"
set "MODE=stop"
if /i "%~1"=="--finish" set "MODE=finish"
curl.exe -s -X POST -H "Content-Type: application/json" -d "{\"mode\": \"%MODE%\"}" "http://127.0.0.1:%PORT%/api/shutdown" || echo The engine is not running.
echo.
