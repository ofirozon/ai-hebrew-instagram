#!/usr/bin/env python3
"""Backlog item 97 on @ai_il_core (4.11.2026): a monthly "best of" carousel.

Ported from stocklearneasy-instagram/best_of.py. Once a month one evening
carousel collects the takeaways (the "שורה תחתונה" card) from last month's
posts that reached the most people, one per card, and ends on the channel.

Nothing here is written by the model. Every takeaway already passed the copy
gates when it first shipped, and the hook and caption are fixed text, so the
post cannot say anything the account did not already say.

The one real difference from the stock account: this account keeps no metrics
snapshot, so reach is read live from the Graph API at queue time, with the
token the queue script already has in Keychain. If the API cannot be read the
slot stays a normal post; a best-of ranked by nothing would be a lie.

What qualifies, checked here:
- published in the calendar month before the slot, with a source.json, a
  concept and a takeaway; earlier best-ofs never count;
- ranked by reach, then likes + saves + shares, then newest first; one card
  per concept and per takeaway;
- each reached at least MIN_REACH accounts;
- at least MIN_PICKS of them, or the slot stays a normal post.

The slot: the first Tuesday, Thursday or Sunday 17:00 UTC carousel of the
month, never the Wednesday question test. Template B (item 54) loses that one
evening a month, which only shifts its alternation by a post.

    python best_of.py 2026-11-01T1700     print what that slot would collect
"""
import json
import pathlib
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
PUBLISHED = ROOT / "published"
LOG = ROOT / "published-log.jsonl"
GRAPH = "https://graph.instagram.com/v21.0"
PICKS = 4
MIN_PICKS = 3
# A card that says "reached the most people" over posts that reached one
# person each is a lie told with true numbers. Checked 4.11.2026: of 47
# October posts, 4 reached 1 account and 43 reached 0, so this gate holds the
# first best-of until the account has an audience to rank by.
MIN_REACH = 5
FREE_WEEKDAYS = (1, 3, 6)  # Tuesday, Thursday, Sunday
TAGS = ["#בינהמלאכותית", "#AI", "#טכנולוגיה", "#טיפים"]
MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
          "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
NUMBER_WORDS = {3: "שלוש", 4: "ארבע"}


def wants_best_of(slot):
    if slot is None or slot.hour < 15 or slot.weekday() not in FREE_WEEKDAYS:
        return False
    first = next(d for d in range(1, 8)
                 if slot.replace(day=d).weekday() in FREE_WEEKDAYS)
    return slot.day == first


def previous_month(slot):
    return (slot.year - 1, 12) if slot.month == 1 else (slot.year, slot.month - 1)


def month_name(slot):
    return MONTHS[previous_month(slot)[1] - 1]


def _token():
    out = subprocess.run(
        ["security", "find-generic-password", "-a", "binah", "-s", "binah-ig-token", "-w"],
        capture_output=True, text=True)
    return out.stdout.strip()


def _media_ids():
    ids = {}
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("slot") and row.get("media_id"):
                ids[row["slot"]] = row["media_id"]
    return ids


def _insights(media_id, token):
    url = f"{GRAPH}/{media_id}/insights?metric=reach,likes,saved,shares&access_token={token}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return {m["name"]: (m.get("values") or [{}])[0].get("value", 0) or 0
            for m in data.get("data", [])}


def pick(slot):
    """Last month's top takeaways for this slot, best first, or [] if too few
    or if reach cannot be read."""
    year, month = previous_month(slot)
    prefix = f"{year:04d}-{month:02d}-"
    token = _token()
    if not token:
        print("best_of: no Instagram token, slot stays normal", file=sys.stderr)
        return []
    ids = _media_ids()
    rows, read = [], 0
    for d in sorted(PUBLISHED.glob(prefix + "*")):
        p = d / "source.json"
        if not p.is_file() or d.name not in ids:
            continue
        try:
            src = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if src.get("category") == "bestof":
            continue
        copy = src.get("copy") or {}
        takeaway = (copy.get("takeaway") or "").strip()
        concept = (copy.get("concept") or "").strip()
        if not takeaway or not concept:
            continue
        try:
            m = _insights(ids[d.name], token)
            read += 1
        except Exception as e:
            print(f"best_of: no insights for {d.name} ({e})", file=sys.stderr)
            continue
        rows.append({
            "slot": d.name, "concept": concept, "takeaway": takeaway,
            "reach": m.get("reach", 0),
            "engagement": sum(m.get(k, 0) for k in ("likes", "saved", "shares")),
        })
    if not read:
        return []
    rows = [r for r in rows if r["reach"] >= MIN_REACH]
    rows.sort(key=lambda r: (r["reach"], r["engagement"], r["slot"]), reverse=True)
    out, seen = [], set()
    for r in rows:
        if r["concept"] in seen or r["takeaway"] in seen:
            continue
        seen |= {r["concept"], r["takeaway"]}
        out.append(r)
        if len(out) == PICKS:
            break
    return out if len(out) >= MIN_PICKS else []


def hook(slot, picks):
    return f"{month_name(slot)} ב{NUMBER_WORDS.get(len(picks), len(picks))} שורות תחתונות"


def caption(slot, picks, channel_line):
    lines = "\n".join(f"{i}. {p['concept']}: {p['takeaway']}" for i, p in enumerate(picks, 1))
    return (
        f"{hook(slot, picks)}\n\n"
        f"השורות מהפוסטים של {month_name(slot)} שהגיעו להכי הרבה אנשים, אחת בכל שקופית. "
        "שמרו את זה, או שלחו למי ששואל איפה מתחילים עם AI.\n\n"
        f"{lines}\n\n"
        f"{channel_line}\n\n"
        f"#בינהבקטנה {' '.join(TAGS)}"
    )


def slide_html(kind, position, total, slot, picks, card_shell, dots, fit, escape):
    """One card. card_shell, dots, fit and escape come from generate.py, so the
    best-of wears the same frame as every other carousel."""
    if kind == "hook":
        text = hook(slot, picks)
        top = ('<div class="top-row"><div class="tag">הכי נקראו</div>'
               f'<div class="slide-num ltr">{position}/{total}</div></div>')
        body = (
            f'<div class="eyebrow">הטובים של {escape(month_name(slot))}</div>'
            f'<div class="headline" style="font-size:{fit(text, [(30, 100), (40, 92), (999, 84)])}px;">'
            f'{escape(text)}</div>'
            '<div class="sub">השורות מהפוסטים של החודש שעבר שהגיעו להכי הרבה אנשים. '
            'אחת בכל שקופית.</div>'
            '<div class="swipe">החלק להמשך</div>'
        )
    else:
        p = picks[position - 2]
        last = position == total
        top = ('<div class="top-row">'
               f'<div class="tag">שורה תחתונה {position - 1} מתוך {total - 1}</div>'
               f'<div class="slide-num ltr">{position}/{total}</div></div>')
        tail = ('<div class="swipe">עוד אחד כזה כל יום בערוץ בינה בקטנה, הלינק בביו</div>'
                if last else '<div class="swipe">החלק להמשך</div>')
        body = (
            f'<div class="eyebrow">{escape(p["concept"])}</div>'
            f'<div class="headline" style="font-size:{fit(p["takeaway"], [(50, 64), (70, 58), (999, 52)])}px;">'
            f'{escape(p["takeaway"])}</div>'
            f'{tail}'
        )
    return card_shell(top, f'<div class="body">{body}</div>', dots(position, total))


def record(slot, picks):
    return {
        "category": "bestof",
        "copy": {"hook": hook(slot, picks), "headline": "", "tags": TAGS},
        "news": None,
        "picks": picks,
        "slides": ["hook"] + ["rule"] * len(picks),
        "backlog_items": [97],
        "variant": "best-of",
    }


if __name__ == "__main__":
    when = datetime.strptime(sys.argv[1], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
    print("best-of slot:", wants_best_of(when))
    for p in pick(when):
        print(f"  {p['slot']} reach {p['reach']}: {p['concept']}: {p['takeaway']}")
