import pytest

from app.generations import GenerationStore


def test_store_roundtrip_and_order(tmp_path):
    store = GenerationStore(tmp_path / "gens")
    a = store.add("first line", "turbo", "bob", "en", b"RIFF1")
    b = store.add("second line", "standard", "", "en", b"RIFF2")
    ids = [g.id for g in GenerationStore(tmp_path / "gens").list()]
    assert set(ids) == {a.id, b.id}
    assert store.get(a.id).text == "first line"
    assert store.get(a.id).path.read_bytes() == b"RIFF1"
    assert store.delete(a.id) is True
    assert store.delete(a.id) is False


def test_store_rejects_bad_input_and_traversal(tmp_path):
    store = GenerationStore(tmp_path)
    with pytest.raises(ValueError):
        store.add("hi", "turbo", "", "en", b"")
    with pytest.raises(ValueError):
        store.add("  ", "turbo", "", "en", b"x")
    assert store.get("../etc") is None
    assert store.delete("..") is False


def test_generation_routes(client):
    r = client.post(
        "/api/generations",
        data={"text": "Hello there", "model": "turbo", "voice": "", "language": "en"},
        files={"file": ("speech.wav", b"RIFFabc", "audio/wav")},
    )
    assert r.status_code == 201
    gen_id = r.json()["id"]
    listed = client.get("/api/generations").json()["generations"]
    assert [g["text"] for g in listed] == ["Hello there"]
    assert client.get(f"/api/generations/{gen_id}/audio").content == b"RIFFabc"
    assert client.delete(f"/api/generations/{gen_id}").status_code == 204
    assert client.get(f"/api/generations/{gen_id}/audio").status_code == 404
    bad = client.post("/api/generations", data={"text": " "}, files={"file": ("a.wav", b"x")})
    assert bad.status_code == 400
