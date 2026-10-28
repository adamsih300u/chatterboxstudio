import pytest

from app.voices import VoiceError, VoiceStore


def test_add_list_get_delete(store):
    voice = store.add("My Voice", b"RIFFdata", "clip.wav")
    assert voice.id == "my-voice"
    assert [v.name for v in store.list()] == ["My Voice"]
    assert store.get("My Voice").path.read_bytes() == b"RIFFdata"
    assert store.delete("my-voice") is True
    assert store.list() == []
    assert store.delete("my-voice") is False


def test_persists_across_instances(tmp_path):
    VoiceStore(tmp_path).add("Alice", b"x", "a.mp3")
    assert [v.id for v in VoiceStore(tmp_path).list()] == ["alice"]


def test_rejects_duplicates_bad_ext_and_empty(store):
    store.add("a", b"x", "a.wav")
    with pytest.raises(VoiceError):
        store.add("A", b"x", "a.wav")
    with pytest.raises(VoiceError):
        store.add("b", b"x", "b.exe")
    with pytest.raises(VoiceError):
        store.add("c", b"", "c.wav")
    with pytest.raises(VoiceError):
        store.add("!!!", b"x", "c.wav")


def test_default_voice(store):
    store.add("Alice", b"x", "a.wav")
    store.add("Bob", b"x", "b.wav")
    assert store.default_id() is None
    assert store.set_default("Bob") == "bob"
    assert store.default_id() == "bob"
    assert VoiceStore(store.root).default_id() == "bob"
    assert [v.id for v in store.list()] == ["alice", "bob"]
    with pytest.raises(VoiceError):
        store.set_default("nobody")
    store.delete("bob")
    assert store.default_id() is None
    store.set_default("alice")
    assert store.set_default(None) is None
    assert store.default_id() is None


def test_default_voice_routes(client):
    client.post("/api/voices", data={"name": "Bob"}, files={"file": ("b.wav", b"abc", "audio/wav")})
    assert client.get("/api/voices").json()["default"] is None
    assert client.put("/api/voices/default", json={"id": "bob"}).json() == {"default": "bob"}
    assert client.get("/api/voices").json()["default"] == "bob"
    assert client.put("/api/voices/default", json={"id": "ghost"}).status_code == 404
    assert client.put("/api/voices/default", json={"id": None}).json() == {"default": None}


def test_voice_routes(client):
    r = client.post("/api/voices", data={"name": "Bob"}, files={"file": ("b.wav", b"abc", "audio/wav")})
    assert r.status_code == 201 and r.json()["id"] == "bob"
    assert [v["id"] for v in client.get("/api/voices").json()["voices"]] == ["bob"]
    assert client.get("/api/voices/bob/audio").content == b"abc"
    assert client.delete("/api/voices/bob").status_code == 204
    assert client.delete("/api/voices/bob").status_code == 404
    bad = client.post("/api/voices", data={"name": "x"}, files={"file": ("x.txt", b"abc")})
    assert bad.status_code == 400


def test_builtin_hidden_flag(store):
    assert store.builtin_hidden() is False
    assert store.set_builtin_hidden(True) is True
    assert VoiceStore(store.root).builtin_hidden() is True
    assert store.list() == []
    assert store.set_builtin_hidden(False) is False
    assert store.builtin_hidden() is False


def test_builtin_hidden_route(client):
    assert client.get("/api/voices").json()["builtin_hidden"] is False
    assert client.put("/api/voices/builtin", json={"hidden": True}).json() == {"builtin_hidden": True}
    assert client.get("/api/voices").json()["builtin_hidden"] is True
    assert client.put("/api/voices/builtin", json={"hidden": False}).json() == {"builtin_hidden": False}
