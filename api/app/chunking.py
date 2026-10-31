"""Split text into sentence-sized pieces so the first one can be generated and played early."""
import re

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
FIRST_MAX_WORDS = 14  # a longer opening sentence is cut at a comma to shorten the wait
MIN_WORDS = 4  # shorter pieces ("Yes." "Oh no!") join their neighbour


def _words(s: str) -> int:
    return len(s.split())


def split_text(text: str, cut_first: bool = True) -> list[str]:
    """Sentences in order, with tiny ones merged and, if cut_first, a long first one cut at a comma."""
    sentences = [s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()]
    if not sentences:
        return []
    if cut_first and _words(sentences[0]) > FIRST_MAX_WORDS:
        parts = [p.strip() for p in CLAUSE_END.split(sentences[0]) if p.strip()]
        # Cut at the first comma that leaves a usable opening.
        head = ""
        for i, part in enumerate(parts):
            head = f"{head} {part}".strip()
            if _words(head) >= MIN_WORDS and i < len(parts) - 1:
                sentences[0:1] = [head, " ".join(parts[i + 1 :])]
                break
    merged: list[str] = []
    carry = ""
    for s in sentences:
        s = f"{carry} {s}".strip()
        carry = ""
        if _words(s) < MIN_WORDS:
            carry = s
        else:
            merged.append(s)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged
