"""Write the API's OpenAPI schema to a file (default: web/openapi.json).

Runs against a throwaway data directory and never loads a model, so it is safe
to run anywhere (dev box, CI). Usage, from the repo root:

    PYTHONPATH=api python3 api/scripts/export_openapi.py [output.json]
"""
import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("web/openapi.json")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["VOICES_DIR"] = f"{tmp}/voices"
        os.environ["GENERATIONS_DIR"] = f"{tmp}/generations"
        os.environ["DATABASE_PATH"] = f"{tmp}/studio.db"
        os.environ["STATIC_DIR"] = f"{tmp}/static"
        from app.main import create_app

        schema = create_app().openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
