import threading

from app.models import ModelManager


class FakeModel:
    sr = 24000

    def generate(self, text, **kwargs):
        return [0.0] * 10


def wait_for(mm, variant, state, timeout=5):
    done = threading.Event()

    def poll():
        while mm.status()[variant]["state"] != state:
            if done.wait(0.01):
                return

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    t.join(timeout)
    done.set()
    return mm.status()[variant]["state"] == state


def test_status_idle_then_ready_after_start_load():
    mm = ModelManager(loader=lambda v, d: FakeModel())
    assert mm.status()["turbo"]["state"] == "idle"
    mm.start_load("turbo")
    assert wait_for(mm, "turbo", "ready")
    assert mm.start_load("turbo") == "ready"


def test_preload_marks_queued_variants_loading_then_ready():
    gate = threading.Event()

    def loader(v, d):
        gate.wait(5)
        return FakeModel()

    mm = ModelManager(loader=loader)
    mm.preload(["turbo", "standard"])
    assert mm.status()["standard"]["state"] == "loading"
    gate.set()
    assert wait_for(mm, "standard", "ready")
    assert mm.status()["turbo"]["state"] == "ready"
    assert mm.status()["multilingual"]["state"] == "idle"


def test_failed_load_reports_error_and_can_retry():
    attempts = []

    def loader(v, d):
        attempts.append(v)
        if len(attempts) == 1:
            raise RuntimeError("download failed")
        return FakeModel()

    mm = ModelManager(loader=loader)
    mm.start_load("turbo")
    assert wait_for(mm, "turbo", "error")
    assert "download failed" in mm.status()["turbo"]["error"]
    mm.start_load("turbo")
    assert wait_for(mm, "turbo", "ready")


def test_models_endpoint_and_load_route(client):
    data = client.get("/api/models").json()
    assert {m["id"]: m["state"] for m in data["models"]}["standard"] == "idle"
    assert client.post("/api/models/standard/load").status_code == 202
    data = client.get("/api/models").json()
    assert {m["id"]: m["loaded"] for m in data["models"]}["standard"] is True
    assert client.post("/api/models/nope/load").status_code == 404
