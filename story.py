#!/usr/bin/env python3
"""Write story.png: a 9:16 Story pointing at the slot's feed post.

Backlog item 80, ported from stocklearneasy-instagram on 2.11.2026. Stories sit
at the top of a follower's app, above the feed, and reach followers whose feed
never shows the post. publish.py posts this as a Story right after the feed
post goes live.

The Story is the slot's first card (already validated by card_check), centred
on the card's own purple ground under a short "new post" line. Rendered with
the same headless Chrome as the cards rather than PIL, because PIL without
libraqm lays Hebrew out left to right and would print the line backwards.

The API cannot attach a link or a "new post" sticker, so the line says where
to go instead: the profile, where the post is the top square.

    python story.py <slot_dir>
"""
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

from PIL import Image

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H = 1080, 1920
STORY_SIZE = (W, H)
TITLE = "פוסט חדש"
HINT = "הקישו על השם שלנו למעלה כדי לראות אותו"

_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;700;900&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width:1080px; height:1920px; overflow:hidden;
  font-family:'Heebo', 'Arial Hebrew', sans-serif;
  background: linear-gradient(160deg, #12091f 0%, #241040 55%, #35186b 100%);
  color:#f6f4fb;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
}}
h1 {{ font-size:84px; font-weight:900; }}
p {{ font-size:36px; font-weight:700; color:#c4b5fd; margin-top:18px; }}
img {{ width:960px; height:960px; margin-top:64px; border-radius:24px;
       border:2px solid rgba(255,255,255,0.15); }}
</style></head><body>
<h1>{title}</h1><p>{hint}</p><img src="{src}">
</body></html>
"""


def source_card(slot_dir: Path):
    for name in ("post_1.png", "post.png"):
        if (slot_dir / name).is_file():
            return slot_dir / name
    return None


def write_story(slot_dir: Path) -> bool:
    """Never raises: the Story is a bonus, and a build that throws must not
    cost the slot its feed post."""
    try:
        return _write_story(Path(slot_dir))
    except Exception as e:
        (Path(slot_dir) / "story.png").unlink(missing_ok=True)
        print(f"{Path(slot_dir).name}: story build failed ({e})", file=sys.stderr)
        return False


def _write_story(slot_dir: Path) -> bool:
    slot_dir = slot_dir.resolve()
    out = slot_dir / "story.png"
    card = source_card(slot_dir)
    if card is None:
        out.unlink(missing_ok=True)
        print(f"{slot_dir.name}: no card, no story", file=sys.stderr)
        return False

    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "story.html"
        html.write_text(_HTML.format(title=escape(TITLE), hint=escape(HINT),
                                     src=card.as_uri()), encoding="utf-8")
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--allow-file-access-from-files",
             f"--screenshot={out}", f"--window-size={W},{H}",
             "--virtual-time-budget=4000", "--hide-scrollbars", html.as_uri()],
            check=True, capture_output=True, timeout=60,
        )

    ok, reason = is_valid_story(out, card)
    if not ok:
        out.unlink(missing_ok=True)
        print(f"{slot_dir.name}: story failed its check ({reason})", file=sys.stderr)
        return False
    return True


def is_valid_story(path: Path, card: Path = None):
    """Right size, dark corners (not a browser error page), and the card
    actually loaded: a broken <img> leaves the middle a flat gradient."""
    try:
        im = Image.open(path).convert("RGB")
    except Exception as e:
        return False, f"unreadable ({e})"
    if im.size != STORY_SIZE:
        return False, f"size {im.size}, want {STORY_SIZE}"
    for pt in [(5, 5), (W - 5, 5), (5, H - 5), (W - 5, H - 5)]:
        if sum(im.getpixel(pt)) / 3 > 200:
            return False, f"corner {pt} too bright, not our ground"
    middle = im.crop((60, 700, W - 60, 1500)).convert("L")
    lo, hi = middle.getextrema()
    if hi - lo < 120:
        return False, "card area has almost no contrast, the image did not load"
    return True, "ok"


if __name__ == "__main__":
    sys.exit(0 if write_story(Path(sys.argv[1])) else 1)
