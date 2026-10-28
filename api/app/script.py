"""Inline voice tags: "{Alice} Hello there. {Bob} Hi!" becomes one segment per voice."""
import re
from dataclasses import dataclass
from typing import Optional

TAG = re.compile(r"\{([^{}\n]{1,64})\}")
CUT_TAG = re.compile(r"cut(?:\s+(\d{1,6}))?", re.IGNORECASE)
RESET = {"default", "none"}
MAX_OVERLAP_MS = 2000


@dataclass(frozen=True)
class Cut:
    """How a segment is cut off.

    keep is the fraction of the line's characters spoken before the cut. None means
    the whole line is kept and only its trailing silence goes. overlap_ms starts the
    next segment that long before this one ends.
    """

    keep: Optional[float] = None
    overlap_ms: int = 0


def _flush(pieces: list, voice: Optional[str], segments: list, cuts: dict) -> None:
    """Turn one voice's stretch of text and cut markers into a segment.

    "{cut}" after the text ends the line there. "{cut}" in the middle of a line
    keeps the whole sentence for the model but cuts the audio at the marker.
    """
    before: list[str] = []
    after: list[str] = []
    mark: Optional[int] = None  # overlap_ms of the first usable marker
    for p in pieces:
        if isinstance(p, str):
            (after if mark is not None else before).append(p)
        elif mark is None and "".join(before).strip():
            mark = p  # a marker with nothing before it is ignored
    head, tail = "".join(before).strip(), "".join(after).strip()
    if not head and not tail:
        return
    if mark is None:
        segments.append((head, voice))
    elif not tail:
        cuts[len(segments)] = Cut(None, mark)
        segments.append((head, voice))
    else:
        spoken = len("".join(head.split()))
        total = spoken + len("".join(tail.split()))
        cuts[len(segments)] = Cut(spoken / total, mark)
        segments.append((f"{head} {tail}", voice))


def parse_script_plan(
    text: str, default_voice: Optional[str]
) -> tuple[list[tuple[str, Optional[str]]], dict[int, Cut]]:
    """Split text on {voice} tags into (text, voice) segments, plus cut points.

    A tag switches the voice for everything after it, until the next tag.
    {default} returns to the voice chosen in the UI. Text before the first tag
    uses the default voice. Empty stretches between tags are dropped. Text with
    no tags comes back unchanged as a single segment.

    {cut} does not change the voice. After a line it means no pause, and the
    trailing silence is trimmed. In the middle of a line the text after it is
    generated but cut from the audio, so the speaker is chopped off mid-phrase.
    {cut 200} also starts the next segment 200 ms early, over the cut-off tail.
    """
    if not TAG.search(text):
        return [(text, default_voice)], {}
    segments: list[tuple[str, Optional[str]]] = []
    cuts: dict[int, Cut] = {}
    voice = default_voice
    pieces: list = []
    pos = 0
    for m in TAG.finditer(text):
        pieces.append(text[pos : m.start()])
        pos = m.end()
        name = m.group(1).strip()
        cut = CUT_TAG.fullmatch(name)
        if cut:
            pieces.append(min(int(cut.group(1) or 0), MAX_OVERLAP_MS))
        else:
            _flush(pieces, voice, segments, cuts)
            pieces = []
            voice = default_voice if name.lower() in RESET else name
    pieces.append(text[pos:])
    _flush(pieces, voice, segments, cuts)
    # A line-end cut after the last segment has nothing to cut into.
    last = cuts.get(len(segments) - 1)
    if last is not None and last.keep is None:
        del cuts[len(segments) - 1]
    return segments, cuts


def parse_script_cuts(
    text: str, default_voice: Optional[str]
) -> tuple[list[tuple[str, Optional[str]]], set[int]]:
    segments, cuts = parse_script_plan(text, default_voice)
    return segments, set(cuts)


def parse_script(text: str, default_voice: Optional[str]) -> list[tuple[str, Optional[str]]]:
    return parse_script_plan(text, default_voice)[0]
