import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.db import MIGRATIONS, Database
from app.voices import VoiceError, VoiceNotFound, VoiceStore

REGIONS = [{"start": 0.5, "end": 2.0}, {"start": 3.0, "end": 4.5}]


def test_add_keeps_original_and_regions(store):
    v = store.add("Ann", b"trimmed", "clip.wav", original=b"whole-upload", original_filename="up.mp3", regions=REGIONS)
    assert v.path.read_bytes() == b"trimmed"
    assert v.source_path.read_bytes() == b"whole-upload" and v.source_path.suffix == ".mp3"
    assert store.get("ann").regions == REGIONS
    assert v.public()["has_original"] is True


def test_voice_without_original_edits_from_its_clip(store):
    v = store.add("Old", b"clip", "a.wav")
    assert v.public()["has_original"] is False
    assert v.source_path == v.path
    v2 = store.update("old", b"shorter", "b.wav", regions=REGIONS)
    # The previous clip is now the original, and the new one replaces it.
    assert v2.path.read_bytes() == b"shorter"
    assert v2.source_path.read_bytes() == b"clip"
    assert v2.regions == REGIONS and v2.updated >= v.updated


def test_update_keeps_original_unless_replaced(store):
    store.add("Ann", b"t1", "c.wav", original=b"whole", original_filename="u.wav")
    v = store.update("ann", b"t2", "c.wav", regions=REGIONS)
    assert v.path.read_bytes() == b"t2" and v.source_path.read_bytes() == b"whole"
    v = store.update("ann", b"t3", "c.flac", original=b"new-whole", original_filename="n.mp3")
    assert v.path.name == "ref.flac" and v.source_path.name == "orig.mp3"
    folder = store.root / "ann"
    assert sorted(p.name for p in folder.iterdir()) == ["orig.mp3", "ref.flac"]


def test_update_errors(store):
    with pytest.raises(VoiceNotFound):
        store.update("ghost", b"x", "a.wav")
    store.add("Ann", b"x", "a.wav")
    with pytest.raises(VoiceError):
        store.update("ann", b"", "a.wav")
    with pytest.raises(VoiceError):
        store.update("ann", b"x", "a.exe")
    assert store.get("ann").path.read_bytes() == b"x"


def test_update_discards_previews(store):
    v = store.add("Ann", b"x", "a.wav")
    store.save_preview(v, "k1", b"RIFF")
    assert store.preview_path(v, "k1").is_file()
    store.update("ann", b"y", "a.wav")
    assert not store.preview_path(v, "k1").exists()


def test_preview_cache_is_bounded(store):
    v = store.add("Ann", b"x", "a.wav")
    for i in range(10):
        store.save_preview(v, f"k{i}", b"RIFF")
    assert len(list((store.root / "ann").glob("preview-*.wav"))) == 6


def _add(client, name="Bob", **extra):
    files = {"file": ("b.wav", b"abc", "audio/wav")}
    files.update(extra.pop("files", {}))
    return client.post("/api/voices", data={"name": name, **extra}, files=files)


def test_routes_add_with_original_then_edit_and_export(client):
    r = _add(
        client,
        files={"original": ("whole.mp3", b"whole", "audio/mpeg")},
        regions=json.dumps(REGIONS),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["has_original"] is True and body["regions"] == REGIONS

    assert client.get("/api/voices/bob/source").content == b"whole"
    assert client.get("/api/voices/bob/audio").content == b"abc"

    export = client.get("/api/voices/bob/export")
    assert export.content == b"abc"
    assert 'filename="bob.wav"' in export.headers["content-disposition"]
    assert export.headers["content-disposition"].startswith("attachment")

    r = client.put(
        "/api/voices/bob",
        files={"file": ("n.wav", b"newclip", "audio/wav")},
        data={"regions": json.dumps([{"start": 1, "end": 2}])},
    )
    assert r.status_code == 200 and r.json()["regions"] == [{"start": 1.0, "end": 2.0}]
    assert client.get("/api/voices/bob/audio").content == b"newclip"
    assert client.get("/api/voices/bob/source").content == b"whole"
    assert [v["id"] for v in client.get("/api/voices").json()["voices"]] == ["bob"]


def test_routes_reject_bad_regions_and_missing_voices(client):
    for bad in ["not json", json.dumps([{"start": 2, "end": 1}]), json.dumps([{"start": -1, "end": 1}]),
                json.dumps([{"start": i, "end": i + 0.5} for i in range(101)])]:
        assert _add(client, regions=bad).status_code == 400
    assert client.get("/api/voices").json()["voices"] == []
    put = client.put("/api/voices/ghost", files={"file": ("n.wav", b"x", "audio/wav")})
    assert put.status_code == 404
    for path in ("source", "export"):
        assert client.get(f"/api/voices/ghost/{path}").status_code == 404
    assert client.post("/api/voices/ghost/preview", json={}).status_code == 404


def test_preview_renders_once_then_caches(client, engine):
    _add(client)
    first = client.post("/api/voices/bob/preview", json={})
    assert first.status_code == 200 and first.headers["x-preview-cached"] == "false"
    assert first.content[:4] == b"RIFF"
    assert len(engine.calls) == 1 and engine.calls[0]["ref"].name == "ref.wav"
    assert engine.calls[0]["text"].startswith("Hello")

    again = client.post("/api/voices/bob/preview", json={})
    assert again.headers["x-preview-cached"] == "true" and again.content == first.content
    assert len(engine.calls) == 1


def test_preview_cache_invalidates_on_clip_model_and_params(client, engine):
    _add(client)
    client.post("/api/voices/bob/preview", json={})
    assert len(engine.calls) == 1

    client.post("/api/voices/bob/preview", json={"model": "standard"})
    assert len(engine.calls) == 2  # other model
    client.post("/api/voices/bob/preview", json={"model": "standard"})
    assert len(engine.calls) == 2

    client.put("/api/voices/bob", files={"file": ("n.wav", b"different", "audio/wav")})
    r = client.post("/api/voices/bob/preview", json={})
    assert r.headers["x-preview-cached"] == "false" and len(engine.calls) == 3  # edited clip


def test_preview_language_only_matters_for_multilingual(client, engine):
    _add(client)
    client.post("/api/voices/bob/preview", json={"model": "turbo", "language": "en"})
    client.post("/api/voices/bob/preview", json={"model": "turbo", "language": "fr"})
    assert len(engine.calls) == 1
    client.post("/api/voices/bob/preview", json={"model": "multilingual", "language": "en"})
    client.post("/api/voices/bob/preview", json={"model": "multilingual", "language": "fr"})
    assert len(engine.calls) == 3


def test_preview_rejects_unknown_model(client):
    _add(client)
    assert client.post("/api/voices/bob/preview", json={"model": "nope"}).status_code == 400


def test_migration_0002_keeps_existing_voices(tmp_path):
    path = tmp_path / "studio.db"
    engine = create_engine(f"sqlite:///{path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["engine"] = engine
    command.upgrade(cfg, "0001")
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO voices VALUES ('bob', 'Bob', 1.0, 'ref.wav')")
    engine.dispose()

    voice = VoiceStore(tmp_path / "voices", Database(path)).get("bob")
    assert voice.name == "Bob" and voice.original == "" and voice.regions == []
    assert voice.source_path == voice.path and voice.public()["has_original"] is False
