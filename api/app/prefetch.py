"""Download model weights into the Hugging Face cache without starting the API.

    python -m app.prefetch turbo standard multilingual

With no arguments, every variant is fetched. Each variant is loaded once
(which is what populates the cache) and then released before the next one.
"""
import gc
import sys

from .models import VARIANTS, _load_variant


def main(argv: list[str]) -> int:
    variants = argv or list(VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)}. choose from: {', '.join(VARIANTS)}", file=sys.stderr)
        return 2
    failed = []
    for variant in variants:
        print(f"[prefetch] {variant}: downloading...", flush=True)
        try:
            model = _load_variant(variant, "cpu")
            del model
            gc.collect()
            print(f"[prefetch] {variant}: done", flush=True)
        except Exception as e:
            failed.append(variant)
            print(f"[prefetch] {variant}: FAILED: {e}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
