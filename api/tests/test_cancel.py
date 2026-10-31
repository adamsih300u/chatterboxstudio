import asyncio
import json
import time

TEXT = "The first sentence is right here. The second one follows it closely. And a third to finish."


async def _post_then_hang_up(app, body: dict, after: float):
    """Send a request, then close the connection `after` seconds later without reading a response."""
    payload = json.dumps(body).encode()
    sent = False
    start = time.monotonic()

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # Like a real server: report the hang-up as soon as it has happened, otherwise stay pending.
        # (Starlette polls with an already-cancelled scope, so a pending receive just reads as "still connected".)
        if time.monotonic() - start >= after:
            return {"type": "http.disconnect"}
        await asyncio.sleep(3600)

    async def send(message):
        pass

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/tts",
        "raw_path": b"/api/tts",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        "client": ("test", 1),
        "server": ("test", 80),
        "scheme": "http",
    }
    await app(scope, receive, send)


def test_hanging_up_stops_the_take_and_keeps_nothing(client, engine, gen_store):
    real = engine.synthesize

    def slow(*args, **kwargs):
        time.sleep(0.4)
        return real(*args, **kwargs)

    engine.synthesize = slow
    asyncio.run(_post_then_hang_up(client.app, {"model": "turbo", "text": TEXT, "save": True}, after=0.2))
    # The first sentence was in flight when the client left; the other two must never start.
    assert len(engine.calls) == 1
    assert gen_store.list() == []


def test_a_take_that_is_not_cancelled_is_still_saved(client, gen_store):
    r = client.post("/api/tts", json={"model": "turbo", "text": TEXT, "save": True})
    assert r.status_code == 200
    assert len(gen_store.list()) == 1
