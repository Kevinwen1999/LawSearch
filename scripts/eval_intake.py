"""Check that uploaded scenarios read the same as typed ones (Phase 6's intake DoD).

    python -m scripts.eval_intake                  # every eval scenario as DOCX and scanned PDF
    python -m scripts.eval_intake --only on-003    # just some

Each scenario in eval/scenarios.yaml is rendered two ways: a DOCX (one paragraph per line) and an
image-only PDF (the text drawn onto A4 pages, no text layer, so intake has to OCR it). Both go
through app.intake.extract_text, exactly as an upload would, and the result is compared word by
word with the original text. A scanned PDF that reads back at under 95% is flagged: fingerprinting
and search would be working from different words than the user wrote.
"""

import argparse
import difflib
import io
import re
import textwrap
import time
from pathlib import Path

import docx
import yaml
from PIL import Image, ImageDraw, ImageFont

from app import intake

SCENARIOS = Path(__file__).resolve().parent.parent / "eval" / "scenarios.yaml"
FONT_PATHS = ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
PAGE = (1240, 1754)  # A4 at 150 DPI
MARGIN, LINE_HEIGHT, FONT_SIZE, WRAP = 110, 40, 26, 80
FLAG_BELOW = 0.95


def _font() -> ImageFont.ImageFont:
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default(size=FONT_SIZE)


def to_docx(text: str) -> bytes:
    document = docx.Document()
    for line in textwrap.wrap(text, WRAP * 2):
        document.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def to_scanned_pdf(text: str) -> bytes:
    font, lines = _font(), textwrap.wrap(text, WRAP)
    per_page = (PAGE[1] - 2 * MARGIN) // LINE_HEIGHT
    pages = []
    for start in range(0, max(len(lines), 1), per_page):
        page = Image.new("RGB", PAGE, "white")
        draw = ImageDraw.Draw(page)
        for i, line in enumerate(lines[start:start + per_page]):
            draw.text((MARGIN, MARGIN + i * LINE_HEIGHT), line, fill="black", font=font)
        pages.append(page)
    out = io.BytesIO()
    pages[0].save(out, format="PDF", resolution=150, save_all=True, append_images=pages[1:])
    return out.getvalue()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%]+", text.lower())


def similarity(original: str, extracted: str) -> float:
    return difflib.SequenceMatcher(None, words(original), words(extracted), autojunk=False).ratio()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="scenario ids")
    args = parser.parse_args()

    scenarios = [
        s for s in yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]
        if not args.only or s["id"] in args.only
    ]
    rows = []
    for s in scenarios:
        text = " ".join(s["scenario"].split())
        for fmt, filename, render in (("docx", "scenario.docx", to_docx), ("scanned pdf", "scenario.pdf", to_scanned_pdf)):
            started = time.monotonic()
            extracted = intake.extract_text(filename, render(text))
            score = similarity(text, extracted)
            rows.append((s["id"], fmt, score, time.monotonic() - started))
            flag = "  <-- below 0.95" if score < FLAG_BELOW else ""
            print(f"  {s['id']:14} {fmt:12} {score:.3f}  {time.monotonic() - started:5.1f}s{flag}", flush=True)

    for fmt in ("docx", "scanned pdf"):
        scores = [r[2] for r in rows if r[1] == fmt]
        low = [r[0] for r in rows if r[1] == fmt and r[2] < FLAG_BELOW]
        print(f"\n{fmt}: n={len(scores)} mean {sum(scores) / len(scores):.3f} min {min(scores):.3f}"
              + (f"; below {FLAG_BELOW}: {', '.join(low)}" if low else ""))


if __name__ == "__main__":
    main()
