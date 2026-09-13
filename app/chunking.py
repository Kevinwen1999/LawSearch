"""Split judgment text into retrieval chunks anchored to the decision's own paragraphs."""

import re
from dataclasses import dataclass

MIN_CHARS = 400
MAX_CHARS = 1500
WINDOW_OVERLAP = 150
MIN_NUMBERED_PARAS = 3

# "[12]" preceded by whitespace/start and followed by whitespace. Some sources (SST)
# put the marker mid-line after a heading, so line-start anchoring is not enough.
_PARA_MARKER = re.compile(r"(?<!\S)\[(\d{1,4})\](?=\s)")
_SPACES = re.compile(r"[ \t\u00a0]+")


@dataclass(frozen=True)
class Chunk:
    text: str
    para_no: int | None = None
    para_end: int | None = None


def clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def chunk_judgment(text: str) -> list[Chunk]:
    markers = _sequential_markers(text)
    if len(markers) < MIN_NUMBERED_PARAS:
        return [Chunk(w) for w in _windows(text)]

    chunks = [Chunk(w) for w in _windows(text[: markers[0][1]])]
    ends = [start for _, start in markers[1:]] + [len(text)]

    buf: list[str] = []
    buf_len = 0
    buf_start = buf_end = 0
    for (para_no, start), end in zip(markers, ends):
        body = text[start:end].strip()

        if len(body) > MAX_CHARS:
            if buf:
                chunks.append(Chunk("\n".join(buf), buf_start, buf_end))
                buf, buf_len = [], 0
            chunks.extend(Chunk(w, para_no, para_no) for w in _windows(body))
            continue

        if buf and buf_len + len(body) > MAX_CHARS:
            chunks.append(Chunk("\n".join(buf), buf_start, buf_end))
            buf, buf_len = [], 0

        if not buf:
            buf_start = para_no
        buf.append(body)
        buf_len += len(body)
        buf_end = para_no

        if buf_len >= MIN_CHARS:
            chunks.append(Chunk("\n".join(buf), buf_start, buf_end))
            buf, buf_len = [], 0

    if buf:
        chunks.append(Chunk("\n".join(buf), buf_start, buf_end))
    return chunks


def _sequential_markers(text: str) -> list[tuple[int, int]]:
    """(paragraph number, offset) for markers that continue the 1, 2, 3... sequence.

    Out-of-sequence markers are quoted paragraphs from other decisions or footnotes,
    and stay part of the surrounding paragraph.
    """
    found = []
    expected = 1
    for match in _PARA_MARKER.finditer(text):
        if int(match.group(1)) == expected:
            found.append((expected, match.start()))
            expected += 1
    return found


def _windows(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return [text] if text else []

    windows = []
    start = 0
    while True:
        end = start + MAX_CHARS
        if end >= len(text):
            windows.append(text[start:].strip())
            break
        end = _break_point(text, start + MAX_CHARS // 2, end)
        windows.append(text[start:end].strip())
        overlap_from = text.find(" ", end - WINDOW_OVERLAP, end)
        start = overlap_from + 1 if overlap_from != -1 else end
    return [w for w in windows if w]


def _break_point(text: str, lo: int, hi: int) -> int:
    for sep in ("\n", ". ", " "):
        idx = text.rfind(sep, lo, hi)
        if idx != -1:
            return idx + len(sep)
    return hi
