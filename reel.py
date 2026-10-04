#!/usr/bin/env python3
"""Turn a בינה בקטנה post into a vertical Reel. Silent, text on screen.

Why this exists (4.10.2026). Ofir said both Instagram accounts look bad and
asked for options backed by research rather than another tweak. The research
answer that outranks every design question: a carousel is distributed mainly
to people who already follow the account, and a Reel is the one format
Instagram actively pushes to people who do not. At two followers that is the
difference between a reach of 2 and a reach that can grow. The stock account
has had Reels since 28.9. This one had none, so its ceiling was its follower
count.

Two deliberate differences from the stock account's reel.py:

* **No voice.** macOS `say` in Hebrew is bad enough that a narrated Hebrew reel
  would cost more than it earns, and most Reels are watched on mute anyway.
  The words are on screen, which is where a muted viewer reads them. Music can
  be added later in the app without rebuilding anything.
* **Short scenes.** Scene length here is read time plus a beat, capped at 4
  seconds, because the guidance for retention is a cut every 1.5 to 3 seconds
  and the stock account's reel allows a single still to hold for 11.

Everything else is shared on purpose: the scenes are HTML rendered by the same
headless Chrome that renders the square cards, using the same CSS, so the reel
and the carousel cannot drift apart visually.

Degrades safely. If ffmpeg or Chrome is missing, or any step fails, this
raises ReelError and the caller publishes the carousel instead.
"""
import json
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

import generate

CHROME = generate.CHROME
WIDTH, HEIGHT = 1080, 1920
FPS = 30
PADDING = 120

# Read time, not a fixed beat. A seven-word hook and a thirty-word paragraph
# do not deserve the same second count, and the research threshold that
# matters is the upper one: past about 3 seconds on a still frame, attention
# starts leaving.
HEBREW_WPM = 180.0
SCENE_MIN_SECONDS = 1.9
SCENE_MAX_SECONDS = 4.2
REEL_MAX_SECONDS = 32.0


class ReelError(RuntimeError):
    pass


def read_seconds(text):
    words = max(len((text or "").split()), 1)
    return words / HEBREW_WPM * 60.0 + 0.9


# --- the script --------------------------------------------------------------

def build_scenes(category, copy):
    """What is on screen, scene by scene.

    The explain is split on sentence boundaries rather than shown whole: one
    idea per frame is the same rule the carousel follows, and a paragraph held
    on screen for nine seconds is the static title card the guidance warns
    about, just with more words on it.
    """
    meta = generate.CATEGORY_META[category]
    scenes = [{
        "kind": "hook",
        "eyebrow": meta["tag"],
        "title": copy["hook"],
        "sub": copy.get("headline", ""),
    }]

    for part in split_sentences(copy["explain"]):
        scenes.append({"kind": "beat", "eyebrow": meta["eyebrow"], "title": part})

    scenes.append({
        "kind": "takeaway",
        "eyebrow": "שורה תחתונה",
        "title": copy["takeaway"],
        "sub": copy.get("question", ""),
    })
    scenes.append({"kind": "cta", "title": "בינה בקטנה", "sub": "פוסט חדש כאן כל יום"})
    return scenes


def split_sentences(text, max_parts=4):
    """Split on sentence ends, then merge the short fragments back.

    A naive split leaves three-word slivers that flash past before they can be
    read, so anything under about fifty characters is glued to its neighbour.
    Quoted example prompts are common in this account's copy and they contain
    their own full stops, which is the other reason the raw split is unusable.
    """
    parts, current, depth = [], "", 0
    for ch in text:
        current += ch
        if ch == '"':
            depth ^= 1
        if ch in ".!?" and not depth:
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())

    merged = []
    for part in parts:
        if merged and len(merged[-1]) < 50:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)

    while len(merged) > max_parts:
        shortest = min(range(len(merged) - 1), key=lambda i: len(merged[i]) + len(merged[i + 1]))
        merged[shortest] = f"{merged[shortest]} {merged[shortest + 1]}"
        del merged[shortest + 1]
    return merged or [text]


# --- the frames --------------------------------------------------------------

_REEL_CSS = """
  /* Instagram's own chrome sits on top of the bottom of a Reel: the caption,
     the handle, the like and share column, the audio strip. Roughly the
     bottom 380px of a 1920 frame is covered or crowded on a normal phone. The
     first build centred the text in the geometric middle of the frame, which
     put it behind that furniture. The padding below is therefore not
     symmetric on purpose, and the result is text that sits in the upper
     middle, where it is actually read. */
  body {
    width:1080px; height:1920px;
    padding:150px 100px 400px;
    justify-content:space-between;
  }
  .reel-main { flex:1; display:flex; flex-direction:column; justify-content:center; gap:44px; }
  .reel-hook { font-weight:900; line-height:1.2; }
  .reel-beat { font-weight:400; line-height:1.55; }
  .reel-sub { font-size:44px; font-weight:400; line-height:1.5; opacity:0.72; }
  .reel-eyebrow { font-size:38px; font-weight:900; letter-spacing:3px; color:#c4b5fd; }
  .reel-foot { font-size:36px; opacity:0.55; }
  .cta-brand { font-size:132px; font-weight:900; line-height:1.1; text-align:center; }
  .cta-sub { font-size:48px; opacity:0.75; text-align:center; }
"""

_HOOK_STEPS = [(38, 108), (58, 94), (80, 82), (110, 70), (150, 60)]
_BEAT_STEPS = [(60, 76), (95, 66), (140, 58), (200, 50), (280, 44)]
_TAKE_STEPS = [(45, 92), (70, 80), (110, 68), (160, 58)]


def scene_html(scene):
    if scene["kind"] == "cta":
        main = (
            '<div class="reel-main" style="align-items:center;">'
            f'<div class="cta-brand">בינה <span class="accent">בקטנה</span></div>'
            f'<div class="cta-sub">{escape(scene["sub"])}</div>'
            f'<div class="cta-sub">{generate.HANDLE}</div>'
            '</div>'
        )
    elif scene["kind"] == "hook":
        px = generate._fit(scene["title"], _HOOK_STEPS)
        main = (
            '<div class="reel-main">'
            f'<div class="reel-eyebrow">{escape(scene["eyebrow"])}</div>'
            f'<div class="reel-hook" style="font-size:{px}px;">{escape(scene["title"])}</div>'
            + (f'<div class="reel-sub">{escape(scene["sub"])}</div>' if scene.get("sub") else "")
            + '</div>'
        )
    elif scene["kind"] == "takeaway":
        px = generate._fit(scene["title"], _TAKE_STEPS)
        main = (
            '<div class="reel-main">'
            f'<div class="reel-eyebrow">{escape(scene["eyebrow"])}</div>'
            f'<div class="reel-hook" style="font-size:{px}px;">{escape(scene["title"])}</div>'
            + (f'<div class="reel-sub">{escape(scene["sub"])}</div>' if scene.get("sub") else "")
            + '</div>'
        )
    else:
        px = generate._fit(scene["title"], _BEAT_STEPS)
        main = (
            '<div class="reel-main">'
            f'<div class="reel-eyebrow">{escape(scene["eyebrow"])}</div>'
            f'<div class="reel-beat" style="font-size:{px}px;">{escape(scene["title"])}</div>'
            '</div>'
        )

    foot = f'<div class="reel-foot ltr">{generate.HANDLE}</div>'
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="UTF-8">
<style>{generate._CARD_CSS}{_REEL_CSS}</style>
</head>
<body><div></div>{main}{foot}</body>
</html>"""


def render_frame(html, png_path: Path):
    html_path = png_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={png_path.resolve()}",
         f"--window-size={WIDTH},{HEIGHT}",
         "--virtual-time-budget=4000", f"file://{html_path.resolve()}"],
        check=True, capture_output=True, timeout=90,
    )
    if not png_path.is_file() or png_path.stat().st_size < 10_000:
        raise ReelError(f"frame did not render: {png_path}")


# --- the video ---------------------------------------------------------------

def build_clip(png: Path, seconds: float, out: Path, push_in: bool,
               fade_in=False, fade_out=False):
    """One scene as a clip: still frame, slow push, a silent audio track.

    The silent track is not decoration. concat refuses to join clips whose
    stream layouts differ, so a reel where one scene has no audio stream fails
    at the last step.
    """
    frames = max(int(seconds * FPS), 1)
    zexpr = (f"'min(1+0.05*on/{frames},1.05)'" if push_in
             else f"'max(1.05-0.05*on/{frames},1)'")
    fades = ""
    if fade_in:
        fades += ",fade=t=in:st=0:d=0.35"
    if fade_out:
        fades += f",fade=t=out:st={max(seconds - 0.45, 0):.3f}:d=0.45"

    vf = (f"scale={WIDTH * 2}:{HEIGHT * 2},"
          f"zoompan=z={zexpr}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS}{fades},format=yuv420p")

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", vf, "-af", "aformat=sample_rates=44100:channel_layouts=stereo",
         "-t", f"{seconds:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)],
        check=True, capture_output=True, timeout=240,
    )


def concat(clips, out: Path, workdir: Path):
    listing = workdir / "clips.txt"
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(out)],
        check=True, capture_output=True, timeout=420,
    )


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return float(out.stdout.strip())


def build_reel(out_dir: Path, category, copy):
    """Write out_dir/reel.mp4 and out_dir/reel.json. Raises ReelError."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise ReelError(f"{tool} is not installed")
    if not Path(CHROME).exists():
        raise ReelError("Chrome is not where this expects it")

    scenes = build_scenes(category, copy)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "reel.mp4"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib_path(tmp)
        clips, total = [], 0.0
        for i, scene in enumerate(scenes):
            png = tmp / f"scene_{i}.png"
            render_frame(scene_html(scene), png)

            text = " ".join(x for x in (scene.get("title"), scene.get("sub")) if x)
            seconds = min(max(read_seconds(text), SCENE_MIN_SECONDS), SCENE_MAX_SECONDS)
            if total + seconds > REEL_MAX_SECONDS:
                seconds = max(REEL_MAX_SECONDS - total, SCENE_MIN_SECONDS)

            clip = tmp / f"clip_{i}.mp4"
            build_clip(png, seconds, clip, push_in=(i % 2 == 0),
                       fade_in=(i == 0), fade_out=(i == len(scenes) - 1))
            clips.append(clip)
            total += seconds
            if total >= REEL_MAX_SECONDS:
                break

        # The grid shows the cover forever, so it is the hook frame and never
        # a frame ffmpeg happened to pick.
        cover = out_dir / "cover.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp / "scene_0.png"),
             "-vf", f"scale={WIDTH}:{HEIGHT}", "-q:v", "3", str(cover)],
            check=True, capture_output=True, timeout=120,
        )
        concat(clips, out_path, tmp)

    duration = duration_of(out_path)
    (out_dir / "reel.json").write_text(json.dumps({
        "scenes": len(clips),
        "duration_seconds": round(duration, 2),
        "size_bytes": out_path.stat().st_size,
        "voice": False,
        "cover": "cover.jpg",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_path


def pathlib_path(p):
    return Path(p)


if __name__ == "__main__":
    import sys
    src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    path = build_reel(out, src["category"], src["copy"])
    print(f"built {path} ({duration_of(path):.1f}s)")
