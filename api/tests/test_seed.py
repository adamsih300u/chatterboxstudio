from app.generations import GenerationStore


def test_tts_uses_given_seed_and_reports_it(client, engine):
    r = client.post("/api/tts", json={"text": "hello", "seed": 1234})
    assert r.status_code == 200
    assert r.headers["X-Seed"] == "1234"
    assert engine.calls[-1]["seed"] == 1234


def test_tts_picks_a_random_seed_when_omitted(client, engine):
    r = client.post("/api/tts", json={"text": "hello"})
    seed = int(r.headers["X-Seed"])
    assert 0 <= seed <= 2**32 - 1
    assert engine.calls[-1]["seed"] == seed


def test_seed_is_saved_with_history_and_listed(client):
    r = client.post("/api/tts", json={"text": "hello", "seed": 77, "save": True})
    gen_id = r.headers["X-Generation-Id"]
    listed = client.get("/api/generations").json()["generations"]
    assert [(g["id"], g["seed"]) for g in listed] == [(gen_id, 77)]


def test_every_segment_gets_the_same_seed(client, engine):
    client.post("/api/tts", json={"segments": [{"text": "a"}, {"text": "b"}], "seed": 5})
    assert [c["seed"] for c in engine.calls] == [5, 5]


def test_seed_is_validated(client):
    assert client.post("/api/tts", json={"text": "hi", "seed": -1}).status_code == 422
    assert client.post("/api/tts", json={"text": "hi", "seed": 2**32}).status_code == 422


def test_openai_route_accepts_seed(client, engine):
    r = client.post("/v1/audio/speech", json={"input": "hi", "seed": 9})
    assert r.status_code == 200
    assert r.headers["X-Seed"] == "9"
    assert engine.calls[-1]["seed"] == 9


def test_old_generations_have_no_seed(tmp_path):
    store = GenerationStore(tmp_path / "gens")
    assert store.add("line", "turbo", "", "en", b"RIFF1").seed is None
    assert store.add("line", "turbo", "", "en", b"RIFF1", seed=3).seed == 3
