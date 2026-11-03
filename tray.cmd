@echo off
rem Shows Sodaudio tray icon (the engine must be running; start.cmd --tray does both).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tray.pyw"
