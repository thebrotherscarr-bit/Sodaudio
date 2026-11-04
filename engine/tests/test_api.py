"""The HTTP API through FastAPI's test client, with the fake generator."""
import pytest
from fastapi.testclient import TestClient

from engine.app import create_app

from .conftest import wait_idle


@pytest.fixture
def client(services):
    app = create_app(services, port=8770, open_last=False)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_origin(client):
    assert client.get("/api/health").json()["ok"]
    assert client.get("/health").json()["ok"]
    assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Origin": "http://127.0.0.1:8770"}).status_code == 200
    assert client.get("/api/health", headers={"Origin": "app://sodaudio"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


def test_settings_never_return_the_key(client, monkeypatch):
    import keyring
    stored = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: stored.update({u: p}))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: stored.get(u))
    body = client.put("/api/settings", json={"api_key": "sk-test", "gpu_mode": "restricted"}).json()
    assert body["api_key_set"] is True and "sk-test" not in str(body)
    assert client.get("/api/settings").json()["gpu_mode"] == "restricted"


def test_cues_and_preview(client):
    rows = client.get("/api/cues", params={"category": "music"}).json()
    assert {r["id"] for r in rows} >= {"mus_frozen_day", "mus_title"}
    detail = client.get("/api/cues/mus_frozen_day").json()
    assert detail["prompt_final"].startswith("crystalline baroque counterpoint") and detail["shape"] == "drive"
    client.post("/api/project/decompose")
    preview = client.post("/api/preview", json={"cue": {"id": "mus_frozen_day", "voice": "whispering monks"}}).json()
    assert "whispering monks, with a low droning hum underneath" in preview["prompt_final"]
    style = client.post("/api/preview", json={"project": {"style": {**client.get("/api/project").json()["style"], "choir": "NEW CHOIR"}}}).json()
    assert len(style["samples"]) == 3 and all("NEW CHOIR" in s["prompt_final"] for s in style["samples"] if s["id"] != "mus_title")
    assert client.patch("/api/cues/mus_frozen_day", json={"notes": "colder"}).json()["notes"] == "colder"
    assert client.post("/api/project/undo").json()["undone"]["cue"] == "mus_frozen_day"


def test_generate_audition_install_flow(client, services):
    ids = client.post("/api/jobs", json={"type": "generate", "cues": ["use_broadsword"], "takes": 2}).json()["ids"]
    wait_idle(services)
    assert client.get(f"/api/jobs/{ids[0]}").json()["state"] == "done"
    takes = client.get("/api/cues/use_broadsword/takes").json()
    assert [c["name"] for c in takes["candidates"]] == ["use_broadsword__c1.wav", "use_broadsword__c2.wav"]
    groups = client.get("/api/audition").json()["groups"]
    assert groups[0]["cue"]["id"] == "use_broadsword"
    audio = client.get("/api/files/" + takes["candidates"][0]["path"], headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.content) == 100
    installed = client.post("/api/promote", json={"candidates": ["use_broadsword__c2.wav"]}).json()["installed"]
    assert installed[0]["path"] == "Assets/Sounds/use_broadsword.wav"
    assert (services.project.root / "Assets/Sounds/use_broadsword.wav").exists()
    assert client.get("/api/game/Assets/Sounds/use_broadsword.wav").status_code == 200
    assert client.get("/api/files/../audio-project.json").status_code == 404
    client.post("/api/candidates/reject", json={"name": "use_broadsword__c1.wav"})
    assert client.get("/api/audition").json()["groups"][0]["candidates"][0]["name"] == "use_broadsword__c2.wav"


def test_map_route_queues_then_serves(client, services):
    client.post("/api/jobs", json={"type": "generate", "cues": ["mus_town"], "takes": 1, "seconds": 20})
    wait_idle(services)
    first = client.get("/api/maps", params={"file": "mus_town__c1"})
    assert first.status_code == 202
    wait_idle(services)
    data = client.get("/api/maps", params={"file": "mus_town__c1"}).json()
    assert data["tempo"] == pytest.approx(100, abs=3)  # the fake take clicks at the cue's 100 BPM


def test_events_socket(client, services):
    with client.websocket_connect("/api/events") as socket:
        assert socket.receive_json()["type"] == "health"
        services.log("hello from the test")
        for _ in range(20):
            event = socket.receive_json()
            if event.get("type") == "log" and event["line"] == "hello from the test":
                break
        else:
            raise AssertionError("no log event")


def test_folder_browser_is_limited(client, services, tmp_path):
    services.settings.data["fs_roots"] = [str(services.project.root)]
    listing = client.get("/api/fs", params={"path": str(services.project.root)}).json()
    assert "Assets" in [f["name"] for f in listing["folders"]]
    assert client.get("/api/fs", params={"path": "C:/Windows"}).status_code == 403


def test_upload_to_inbox(client, services, tmp_path):
    from .conftest import click_track
    path = click_track(tmp_path / "drop.wav", 120, 4)
    with path.open("rb") as handle:
        result = client.post("/api/upload", files={"file": ("drop.wav", handle, "audio/wav")}).json()
    assert result["file"] == "inbox/drop.wav"
    assert client.get("/api/maps", params={"file": result["file"]}).status_code == 202


def test_shutdown_stops_the_running_job_and_cancels_the_queued(client, services):
    class Server:
        should_exit = False

    server = client.app.state.holder["server"] = Server()  # without one, shutdown would end this test process
    services.settings.data.update(fake_delay=5)
    ids = [services.queue.submit("generate", {"cues": ["mus_frozen_day"], "takes": 4}) for _ in range(3)]
    from .conftest import wait_for
    wait_for(lambda: services.queue.running, 10)
    assert client.post("/api/shutdown", json={}).json()["ok"] is True
    wait_for(lambda: server.should_exit, 40)
    assert {services.queue.store.get(i)["state"] for i in ids} == {"cancelled"}
