# Stands in for a game's start script (like Rimtera's tools/comfy/start_comfy.ps1): starts the fake ComfyUI hidden,
# sets its priority, records the arguments it got, and returns.
param([double]$ReserveVramGB = 4, [string]$Priority = "BelowNormal")
$port = $env:FAKE_COMFY_PORT
$process = Start-Process -FilePath $env:FAKE_COMFY_PYTHON -ArgumentList "`"$PSScriptRoot\ComfyUI\main.py`" --port $port" -WindowStyle Hidden -PassThru
$process.PriorityClass = $Priority
"$ReserveVramGB $Priority" | Set-Content $env:FAKE_COMFY_RECORD
"fake ComfyUI starting (pid $($process.Id), priority $Priority, reserved VRAM $ReserveVramGB GB)"
