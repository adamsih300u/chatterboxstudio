import json

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.db import Database
from app.generations import GenerationStore
from app.legacy import import_legacy
from app.main import create_app
from app.voices import VoiceStore


def test_migrations_create_schema(tmp_path):
    db = Database(tmp_path / "studio.db")
    assert {"voices", "generations", "settings"} <= set(inspect(db.engine).get_table_names())
    Database(tmp_path / "studio.db")  # reopening is a no-op upgrade


def test_wal_mode(tmp_path):
    db = Database(tmp_path / "studio.db")
    with db.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_stores_share_one_database(tmp_path):
    db = Database(tmp_path / "studio.db")
    voices = VoiceStore(tmp_path / "voices", db)
    gens = GenerationStore(tmp_path / "gens", db)
    voices.add("Alice", b"x", "a.wav")
    gens.add("hello", "turbo", "Alice", "en", b"RIFF", segments=[{"text": "hello", "voice": "Alice"}])
    again = Database(tmp_path / "studio.db")
    assert [v.id for v in VoiceStore(tmp_path / "voices", again).list()] == ["alice"]
    assert GenerationStore(tmp_path / "gens", again).list()[0].segments == [{"text": "hello", "voice": "Alice"}]


def test_failed_insert_leaves_no_files(tmp_path):
    store = VoiceStore(tmp_path / "voices", Database(tmp_path / "studio.db"))
    store.add("Alice", b"x", "a.wav")
    try:
        store.add("alice", b"y", "b.wav")
    except ValueError:
        pass
    assert sorted(p.name for p in (tmp_path / "voices").iterdir()) == ["alice"]
    assert (tmp_path / "voices" / "alice" / "ref.wav").read_bytes() == b"x"


def _legacy_tree(tmp_path):
    voices, gens = tmp_path / "voices", tmp_path / "gens"
    (voices / "bob").mkdir(parents=True)
    (voices / "bob" / "ref.wav").write_bytes(b"x")
    (voices / "bob" / "meta.json").write_text(json.dumps({"name": "Bob", "created": 1.0, "filename": "ref.wav"}))
    (voices / "broken").mkdir()
    (voices / "broken" / "meta.json").write_text("{not json")
    (voices / ".default").write_text("bob")
    (voices / ".builtin_hidden").write_text("1")
    gid = "20260101000000-abcdef12"
    (gens / gid).mkdir(parents=True)
    (gens / gid / "audio.wav").write_bytes(b"RIFF")
    (gens / gid / "meta.json").write_text(json.dumps({"text": "hi", "created": 5.0}))  # old record, no model/voice
    return voices, gens, gid


def test_legacy_import(tmp_path):
    voices, gens, gid = _legacy_tree(tmp_path)
    db = Database(tmp_path / "studio.db")
    assert import_legacy(db, voices, gens) == {"voices": 1, "generations": 1}

    vs, gs = VoiceStore(voices, db), GenerationStore(gens, db)
    assert [v.name for v in vs.list()] == ["Bob"]
    assert vs.default_id() == "bob" and vs.builtin_hidden() is True
    g = gs.get(gid)
    assert g.text == "hi" and g.model == "" and g.language == "en" and g.path.read_bytes() == b"RIFF"

    # Files are kept (renamed), the unreadable record is left alone, and a rerun adds nothing.
    assert (voices / "bob" / "meta.json.migrated").is_file()
    assert (voices / "broken" / "meta.json").is_file()
    assert import_legacy(db, voices, gens) == {"voices": 0, "generations": 0}


def test_deleted_voice_is_not_resurrected(tmp_path):
    voices, gens, _ = _legacy_tree(tmp_path)
    db = Database(tmp_path / "studio.db")
    import_legacy(db, voices, gens)
    VoiceStore(voices, db).delete("bob")
    import_legacy(db, voices, gens)
    assert VoiceStore(voices, db).list() == []


def test_app_imports_legacy_on_start(tmp_path, engine, monkeypatch):
    voices, gens, _ = _legacy_tree(tmp_path)
    monkeypatch.setenv("VOICES_DIR", str(voices))
    monkeypatch.setenv("GENERATIONS_DIR", str(gens))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "studio.db"))
    c = TestClient(create_app(engine=engine, static_dir=tmp_path / "no-ui"))
    body = c.get("/api/voices").json()
    assert [v["id"] for v in body["voices"]] == ["bob"] and body["default"] == "bob"
    assert len(c.get("/api/generations").json()["generations"]) == 1
