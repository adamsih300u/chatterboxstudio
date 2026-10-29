"""The committed web/openapi.json (source of web/src/api-types.ts) must match the live API.

If this fails, regenerate and commit:
    PYTHONPATH=api python3 api/scripts/export_openapi.py
    (cd web && npm run gen:types)
"""
import json
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


def test_committed_openapi_matches_app(client):
    live = json.loads(json.dumps(client.app.openapi(), sort_keys=True))
    assert json.loads(SCHEMA.read_text()) == live
