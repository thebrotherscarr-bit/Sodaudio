"""Optional tray icon for the PWA build (BUILD-PWA.md, phase P4): shows the queue state and offers Open, Pause queue
and Quit engine. Run with the engine's pythonw (tray.cmd, or start.cmd --tray)."""
import json
import os
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

PORT = int(os.environ.get("SODAUDIO_PORT", 8770))
URL = f"http://127.0.0.1:{PORT}"
ICON = Path(__file__).parent / "ui" / "public" / "icon-192.png"
state = {"health": None}


def call(path, body=None):
    request = urllib.request.Request(URL + "/api" + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read() or b"null")


def title():
    health = state["health"]
    if health is None:
        return "Sodaudio: engine not running"
    running = health.get("running")
    if running:
        return f"Sodaudio: #{running['id']} {running['type']} {round((running.get('progress') or 0) * 100)}%"
    return f"Sodaudio: {'paused, ' if health.get('paused') else ''}{health.get('queued', 0)} queued"


def toggle_pause(icon, item):
    try:
        call("/queue/resume" if (state["health"] or {}).get("paused") else "/queue/pause", {})
    except OSError:
        pass


def quit_engine(icon, item):
    try:
        call("/shutdown", {"mode": "finish"})
    except OSError:
        pass
    icon.stop()


def poll(icon):
    while icon.visible or True:
        try:
            state["health"] = call("/health")
        except OSError:
            state["health"] = None
        icon.title = title()[:120]
        icon.update_menu()
        time.sleep(5)


def main():
    icon = pystray.Icon("sodaudio", Image.open(ICON), "Sodaudio", menu=pystray.Menu(
        pystray.MenuItem(lambda item: title(), None, enabled=False),
        pystray.MenuItem("Open", lambda icon, item: webbrowser.open(URL + "/"), default=True),
        pystray.MenuItem(lambda item: "Resume queue" if (state["health"] or {}).get("paused") else "Pause queue", toggle_pause),
        pystray.MenuItem("Quit engine", quit_engine),
        pystray.MenuItem("Hide tray icon", lambda icon, item: icon.stop()),
    ))
    threading.Thread(target=poll, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
