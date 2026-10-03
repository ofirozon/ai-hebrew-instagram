#!/usr/bin/env python3
"""Queue-fill for "בינה בקטנה" — the Hebrew AI Instagram account.

Same architecture as the stocklearneasy-instagram pipeline, which has been
running unattended since 9.2026: this script runs on Ofir's Mac, renders the
cards with headless Chrome and pushes them into scheduled/. It never posts.
A GitHub Action (publish.yml) is the only thing that talks to Instagram, so a
Mac that is asleep delays the queue but never misses a slot.

What is different here, and why:

* The content is Hebrew and the cards are right-to-left. Instagram's crop is
  identical, but every text block has to be direction-aware or the punctuation
  lands on the wrong side.
* The sources are English (Hebrew AI reporting is too thin to fill a daily
  queue), and the model writes the post in Hebrew from them. So the headline
  shown on the card is the model's Hebrew rendering, never the raw English.
* There is no chart and no ticker. The equivalent "reason to stop scrolling"
  here is a concrete, usable thing: a tool with a name, a prompt you can copy.

Usage:
    python3 generate.py            # top the queue back up to TARGET_QUEUE_DEPTH
    python3 generate.py --dry-run  # render into a temp dir, queue nothing
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape, unescape

from card_check import is_valid_card

ROOT = pathlib.Path(__file__).resolve().parent
SCHEDULED = ROOT / "scheduled"
PUBLISHED = ROOT / "published"
SEEN_FILE = ROOT / "seen-headlines.json"
SEEN_TERMS_FILE = ROOT / "seen-terms.json"

BRAND = "בינה בקטנה"
# The connected account. This said @binabiktana for days while the account
# that actually exists and publishes is @ai_il_core, so every card carried
# a handle nobody could follow. Keep it equal to the real account.
HANDLE = "@ai_il_core"

# 10:00 and 17:00 UTC are 13:00 and 20:00 Israel time: lunch break and the
# evening scroll. Deliberately not the morning, which is his work block and
# the worst-performing window on the Telegram channel.
DAILY_SLOTS_UTC_HOUR = [10, 17]
TARGET_QUEUE_DEPTH = 6  # three days of cover at 2/day

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COPY_TIMEOUT = 180

# Hebrew AI coverage is thin and slow, so the feeds are English and the model
# writes the Hebrew. Keep these general-audience: a paper-of-the-day feed
# produces posts nobody outside the field can use.
# Ofir, 3.10.2026: "הפוסטים האלה לא אמורים להיות גנריים בכלל... חדשות
# שאנשים יעבירו אחד לשני". The trade press was the root cause. TechCrunch,
# The Verge and VentureBeat mostly publish funding rounds, product launches
# and enterprise announcements, and no one forwards a funding round.
#
# These feeds carry the stories that actually travel. Hacker News with a
# points floor is the strongest of them, because the score is other people
# having already found the story interesting, which is the exact signal we
# want and the only one here that is measured rather than guessed.
RSS_FEEDS = [
    # crowd-validated: only stories that already cleared 150 points
    "https://hnrss.org/newest?q=AI+OR+LLM+OR+OpenAI+OR+Anthropic+OR+Gemini&points=150",
    # the weird, the investigative, the uncomfortable
    "https://www.404media.co/rss/",
    # depth, and the best writing on models behaving unexpectedly
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://simonwillison.net/atom/everything/",
    # kept, but now a minority of the pool rather than all of it
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]

# Four categories in equal rotation meant every fourth post was "here is what
# a chatbot is", which is the definition of the generic content Ofir objected
# to on 3.10.2026. Stories are what get forwarded, so stories dominate. The
# prompt slot stays because practical value is itself a sharing driver
# (Berger's STEPPS): a prompt someone can copy makes the sharer look useful.
# `concept` is gone entirely; explaining a term is the least forwardable
# thing this page can do.
CATEGORY_CYCLE = ["news", "news", "prompt", "news", "news", "tool"]

CATEGORY_META = {
    "news":    {"tag": "חדשות AI",   "eyebrow": "מה קרה"},
    "tool":    {"tag": "כלי חדש",     "eyebrow": "הכלי"},
    "concept": {"tag": "מושג",        "eyebrow": "ההסבר"},
    "prompt":  {"tag": "טיפ לפרומפט", "eyebrow": "הטיפ"},
}

# A story only earns a slot if a person who does not work in tech could use or
# repeat it. "Nvidia earnings beat" is not that; "Gemini now reads your PDFs"
# is. This is the same on-brand gate the stock account uses, inverted for AI.
OFF_BRAND_PATTERN = re.compile(
    r"\b(earnings|quarterly|shares?|stock|ipo|prospectus|s-1|files? to go public"
    r"|goes public|valuation|funding round|raises \$|series [a-e]\b|investors?"
    r"|lawsuit|sues?|acquires?|acquisition|layoffs?|hiring|benchmark suite|arxiv"
    r"|chip fab|semiconductor supply)\b",
    re.IGNORECASE,
)

# Local material for the two categories that do not come from the news. Kept
# in the repo rather than generated from nothing, so a day with no usable
# headline still produces a post worth posting.
CONCEPTS = [
    ("הזיה (Hallucination)", "כשהמודל ממציא עובדה שנשמעת נכונה לגמרי אבל פשוט לא קיימת"),
    ("חלון הקשר (Context Window)", "כמה טקסט המודל יכול להחזיק בראש בבת אחת בשיחה אחת"),
    ("פרומפט מערכת (System Prompt)", "ההוראות הקבועות שהמודל מקבל לפני שאתה בכלל כותב מילה"),
    ("טוקן (Token)", "היחידה שהמודל באמת קורא, בערך שלושה רבעי מילה באנגלית ופחות מזה בעברית"),
    ("RAG", "לתת למודל לחפש במסמכים שלך לפני שהוא עונה, במקום לסמוך על הזיכרון שלו"),
    ("כוונון עדין (Fine-tuning)", "לאמן מודל קיים על הדוגמאות שלך כדי שיענה בסגנון שלך"),
    ("סוכן (Agent)", "מודל שלא רק עונה אלא מפעיל כלים ומבצע פעולות עד שהמשימה נגמרת"),
    ("טמפרטורה (Temperature)", "כמה המודל מרשה לעצמו להפתיע, נמוך לעובדות וגבוה לרעיונות"),
    ("מודל מולטימודלי", "מודל שמבין גם תמונה, קול או וידאו ולא רק טקסט"),
    ("מסנן תוכן (Guardrails)", "השכבה שעוצרת את המודל לפני שהוא עונה תשובה שאסור לו לענות"),
    ("חיתוך ידע (Knowledge Cutoff)", "התאריך שאחריו המודל פשוט לא יודע מה קרה בעולם"),
    ("אימבדינג (Embedding)", "הדרך להפוך טקסט למספרים כדי שמחשב יוכל להשוות משמעות ולא מילים"),
]

PROMPT_TIPS = [
    ("תן לו תפקיד", "פתיחה עם 'אתה עורך לשוני' משנה את התשובה יותר מכל ניסוח אחר במשפט"),
    ("תגיד באיזה פורמט", "בקש טבלה, רשימה או JSON מראש, אחרת תקבל פסקה שתצטרך לפרק לבד"),
    ("תן דוגמה אחת", "דוגמה אחת לתשובה טובה שווה שלוש פסקאות של הסברים על מה אתה רוצה"),
    ("תגיד מה לא לעשות", "'בלי הקדמות ובלי סיכום' חוסך לך חצי מהתשובה שלא התכוונת לקרוא"),
    ("בקש ממנו לשאול אותך", "'שאל אותי שלוש שאלות לפני שתענה' מונע תשובה כללית שלא מתאימה לך"),
    ("תן לו את החומר", "הדבק את הטקסט האמיתי במקום לתאר אותו, המודל לא מנחש טוב כמו שנדמה"),
    ("תבקש גרסה שנייה", "'עכשיו כתוב את זה חצי מהאורך' כמעט תמיד מוציא את הגרסה הטובה"),
    ("הגדר את הקהל", "'תסביר למישהו בן 60 שלא מבין במחשבים' משנה את כל רמת השפה"),
    ("תן לו קריטריון", "'תשובה טובה היא כזו שאפשר לשלוח כמו שהיא ללקוח' נותן לו במה למדוד"),
    ("בקש את ההנמקה בסוף", "כשהנימוק בא אחרי המסקנה אתה קורא פחות ועדיין יכול לבדוק אותו"),
]


# --- small helpers -----------------------------------------------------------

def load_seen(path):
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(path, data):
    path.write_text(json.dumps(sorted(data), ensure_ascii=False, indent=1), encoding="utf-8")


def slot_time_from_name(name):
    try:
        return datetime.strptime(name, "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def next_free_slots(target_depth):
    """UTC datetimes for enough future slots to reach target_depth queued.

    Counts only slots still in the future, and skips names already used by
    either queue, so a slot that published this morning is never refilled.
    """
    existing = set()
    now = datetime.now(timezone.utc)
    future_count = 0
    if SCHEDULED.is_dir():
        for p in SCHEDULED.iterdir():
            if not p.is_dir():
                continue
            existing.add(p.name)
            when = slot_time_from_name(p.name)
            if when and when > now:
                future_count += 1
    if PUBLISHED.is_dir():
        existing.update(p.name for p in PUBLISHED.iterdir() if p.is_dir())

    count = max(0, target_depth - future_count)
    slots = []
    day = now.date()
    while len(slots) < count:
        for hour in DAILY_SLOTS_UTC_HOUR:
            candidate = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if candidate > now:
                name = candidate.strftime("%Y-%m-%dT%H%M")
                if name not in existing:
                    slots.append(candidate)
                    if len(slots) >= count:
                        break
        day += timedelta(days=1)
    return slots


def next_category_index():
    """Cycle position from how many posts exist, so the mix stays even."""
    n = 0
    for d in (SCHEDULED, PUBLISHED):
        if d.is_dir():
            n += sum(1 for p in d.iterdir() if p.is_dir())
    return n % len(CATEGORY_CYCLE)


# --- sources -----------------------------------------------------------------

def _text(node):
    return unescape((node.text or "").strip()) if node is not None else ""


def fetch_headlines():
    """Every usable item across the feeds, newest first, lightly de-duplicated."""
    items = []
    for url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                tree = ET.fromstring(resp.read())
        except Exception as e:
            print(f"WARNING: feed failed ({url}): {e}", file=sys.stderr)
            continue

        # RSS puts items at channel/item, Atom uses a flat entry list.
        nodes = tree.findall(".//item") or tree.findall("{http://www.w3.org/2005/Atom}entry")
        for node in nodes:
            title = _text(node.find("title")) or _text(node.find("{http://www.w3.org/2005/Atom}title"))
            if not title:
                continue
            desc = (
                _text(node.find("description"))
                or _text(node.find("{http://www.w3.org/2005/Atom}summary"))
            )
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            items.append({"title": title, "description": desc[:600]})

    seen_titles = set()
    unique = []
    for item in items:
        key = item["title"].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(item)
    # 404 Media and Ars Technica are general technology feeds, so most of what
    # they carry is not about AI at all. Filter on the text rather than trust
    # the source.
    return [i for i in unique if _mentions_ai(i)]


_AI_WORDS = re.compile(
    r"\b(ai|a\.i\.|llm|llms|chatbot|chatgpt|openai|anthropic|claude|gemini|"
    r"copilot|midjourney|deepfake|neural|machine learning|language model|"
    r"agentic|ai agent|gpt|grok|nvidia|hallucinat|prompt injection)\b",
    re.IGNORECASE)


def _mentions_ai(item):
    return bool(_AI_WORDS.search(item["title"] + " " + item.get("description", "")))


def is_on_brand(headline):
    return not OFF_BRAND_PATTERN.search(headline)


# How many fresh candidates to put in front of the model before it chooses.
# Small enough to stay one cheap call, large enough that a genuinely strange
# story somewhere down the feed can still win.
SHORTLIST = 25

_PICK_PROMPT = """להלן {n} כותרות מעולם ה-AI. בחר את **אחת** שהכי תעבוד כפוסט
באינסטגרם בעברית לקהל רחב שלא עובד בהייטק.

הקריטריון היחיד: **האם מישהו ישלח את זה לחבר?**

מה שעובד, לפי סדר יורד:
1. משהו ש-AI עשה שאף אחד לא התכוון שיעשה. בריחה ממגבלות, עקיפת כללים,
   התנהגות מפתיעה של מודל, פריצה.
2. סיפור עם טוויסט, או משהו מצחיק, מוזר או מביך.
3. משהו שנוגע לחיים של אנשים רגילים: עבודה, כסף, פרטיות, ילדים, רמאות.
4. מספר או עובדה שגורמת ל"רגע מה?!"

מה שלא עובד ואין לבחור בו:
- גיוסי הון, שווי חברות, מינויים, שותפויות עסקיות
- השקת מוצר או גרסה חדשה בלי סיפור מאחוריה
- מאמרים אקדמיים, benchmarks, ביקורות כלי
- כל דבר שמעניין רק מי שעובד בתעשייה

הכותרות:
{items}

החזר JSON בלבד: {{"index": <מספר הכותרת שבחרת>, "why": "<משפט אחד בעברית,
למה דווקא היא תעבור הלאה>"}}"""


def _llm_json(prompt, timeout=COPY_TIMEOUT):
    """One model call that is expected to answer with a JSON object."""
    out = subprocess.run(
        ["with-claude-token", "claude", "-p", prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=timeout, check=True,
    ).stdout
    match = re.search(r"\{.*\}", out, re.DOTALL)
    return json.loads(match.group(0)) if match else None


def pick_headline(candidates, seen):
    """Choose the most forwardable story, not merely the newest one.

    Taking the top of the feed is what produced a run of posts explaining what
    a chatbot is. Feed order is recency, and recency has nothing to do with
    whether a story is worth passing on. The model reads a shortlist and picks
    on one criterion: would somebody send this to a friend.

    Falls back to feed order if the call fails, so a model hiccup costs a
    duller post rather than an empty slot.
    """
    fresh = [i for i in candidates
             if i["title"] not in seen and is_on_brand(i["title"])]
    if not fresh:
        return None
    if len(fresh) == 1:
        return fresh[0]

    shortlist = fresh[:SHORTLIST]
    listing = "\n".join(f"{n}. {i['title']}" for n, i in enumerate(shortlist))
    try:
        got = _llm_json(_PICK_PROMPT.format(n=len(shortlist), items=listing))
        idx = int(got["index"])
        if 0 <= idx < len(shortlist):
            why = str(got.get("why", "")).strip()
            print(f"  picked: {shortlist[idx]['title'][:70]}")
            if why:
                print(f"  because: {why[:110]}")
            return shortlist[idx]
        print(f"WARNING: pick index {idx} out of range, using feed order", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: story pick failed ({e}), using feed order", file=sys.stderr)
    return fresh[0]


def pick_from_list(pool, seen):
    """First unused entry, or the oldest one if the whole pool has been used."""
    for name, definition in pool:
        if name not in seen:
            return name, definition
    seen.clear()
    return pool[0]


# --- copy --------------------------------------------------------------------

_NEWS_PROMPT = """אתה כותב עבור "בינה בקטנה", עמוד אינסטגרם בעברית שמסביר בינה מלאכותית לאנשים שלא עובדים בתחום.

הסיפור של היום (באנגלית, מתוך פיד חדשות):
כותרת: {title}
תקציר: {description}

כתוב את הפוסט בעברית. החזר אך ורק אובייקט JSON, בלי שום טקסט סביבו, עם המפתחות האלה:

"hook": שורה אחת, עד 55 תווים, לשקופית הראשונה. אסור לחזור על הכותרת. פתח בחלק הכי מפתיע או הכי מביך בסיפור, זה שגורם למישהו לעצור ולהגיד "רגע, מה?". מספר ספציפי עדיף על תיאור כללי.
"headline": הכותרת עצמה בעברית, עד 90 תווים, ניסוח עיתונאי נקי.
"concept": הרעיון שאפשר לקחת מזה הלאה, 2 עד 5 מילים.
"explain": 2 עד 3 משפטים, עד 300 תווים סך הכל, שמסבירים מה בעצם קרה ולמה זה משנה למישהו רגיל. מספרים ושמות אמיתיים, בלי מילות מילוי.
"takeaway": משפט אחד, עד 80 תווים. מה לקחת מזה, או מה זה אומר על הכיוון שאליו הדברים הולכים. לא עצה גנרית.
"question": שאלה אחת ספציפית לתגובות, עד 80 תווים. לא שאלה גנרית, היא חייבת להתאים רק לפוסט הזה.
"tags": בדיוק 4 האשטגים עם הסולמית, בעברית או באנגלית, ספציפיים לנושא. בלי האשטג של שם העמוד.

כללים: עברית תקנית וזורמת, לא תרגומית. בלי סלנג של מתכנתים. מונח באנגלית מותר רק אם אין לו שם עברי מקובל, ואז מסבירים אותו בשלוש מילים במקום. בלי הבטחות, בלי "ישנה את העולם", בלי סימני קריאה."""

_TOOL_PROMPT = """אתה כותב עבור "בינה בקטנה", עמוד אינסטגרם בעברית שמסביר בינה מלאכותית לאנשים שלא עובדים בתחום.

הסיפור של היום (באנגלית, מתוך פיד חדשות):
כותרת: {title}
תקציר: {description}

זה פוסט מסוג "כלי חדש". התמקד בדבר אחד שאפשר לפתוח ולהשתמש בו, לא בחברה שמאחוריו.
החזר אך ורק אובייקט JSON, בלי טקסט סביבו, עם המפתחות:

"hook": שורה אחת, עד 55 תווים, מה הכלי הזה חוסך לך. לא שם הכלי לבד.
"headline": שם הכלי ומה הוא עושה, עד 90 תווים, בעברית.
"concept": סוג המשימה שהוא פותר, 2 עד 5 מילים.
"explain": 2 עד 3 משפטים, עד 300 תווים, מה הוא עושה בפועל ומתי שווה לפתוח אותו. אם ידוע אם הוא חינמי, אמור את זה.
"takeaway": משפט אחד, עד 80 תווים, מתי להשתמש בו ומתי לא.
"question": שאלה ספציפית לתגובות, עד 80 תווים.
"tags": בדיוק 4 האשטגים עם הסולמית.

כללים: עברית זורמת, בלי שפה שיווקית, בלי "מהפכני". אם אתה לא בטוח בעובדה, אל תכתוב אותה."""

_CONCEPT_PROMPT = """אתה כותב עבור "בינה בקטנה", עמוד אינסטגרם בעברית שמסביר בינה מלאכותית לאנשים שלא עובדים בתחום.

המושג של היום: {name}
הגדרת עבודה: {definition}

הגדרת מילון לא שווה עוקב. הפוך את זה למשהו שמישהו ישמור. החזר אך ורק אובייקט JSON עם המפתחות:

"hook": שורה אחת, עד 55 תווים. לא המושג כתווית, אלא הבלבול שהוא פותר.
"headline": המושג עצמו, בדיוק כפי שנמסר.
"concept": המושג עצמו, בדיוק כפי שנמסר.
"explain": 2 עד 3 משפטים, עד 300 תווים, שמסבירים אותו דרך דוגמה קונקרטית אחת עם מספרים או מקרה אמיתי. עדיף "שיחה של 200 עמודים נכנסת בחלון של מודל היום" על פני הגדרה מופשטת.
"takeaway": משפט אחד, עד 80 תווים, מה זה אומר לך בפועל כשאתה נתקל בזה.
"question": שאלה שמכריחה מישהו ליישם את המושג, עד 80 תווים.
"tags": בדיוק 4 האשטגים עם הסולמית.

כללים: עברית זורמת, בלי ז'רגון, בלי סימני קריאה."""

_PROMPT_PROMPT = """אתה כותב עבור "בינה בקטנה", עמוד אינסטגרם בעברית שמסביר בינה מלאכותית לאנשים שלא עובדים בתחום.

הטיפ של היום: {name}
במה מדובר: {definition}

זה פוסט שנועד להישמר ולהיות מיושם עוד היום. החזר אך ורק אובייקט JSON עם המפתחות:

"hook": שורה אחת, עד 55 תווים, מה משתנה בתשובה כשעושים את זה.
"headline": הטיפ בניסוח של הוראה, עד 90 תווים.
"concept": שם הטיפ, 2 עד 5 מילים.
"explain": 2 עד 3 משפטים, עד 300 תווים, שכוללים ניסוח לדוגמה שאפשר להעתיק מילה במילה.
"takeaway": משפט אחד, עד 80 תווים, מתי זה הכי עוזר.
"question": שאלה ספציפית לתגובות, עד 80 תווים.
"tags": בדיוק 4 האשטגים עם הסולמית.

כללים: עברית זורמת. הדוגמה חייבת להיות משהו שאפשר להדביק כמו שהוא, לא תיאור של דוגמה."""

_COPY_KEYS = ("hook", "headline", "concept", "explain", "takeaway", "question", "tags")

# A card whose text is not Hebrew is a failed post even if every field is
# present: it means the model answered in English and nobody would notice
# until it was live.
_HEBREW = re.compile(r"[֐-׿]")


def _fallback_copy(category, news=None, item=None):
    """Used when the model is unreachable or answers with something unusable."""
    if category in ("concept", "prompt"):
        name, definition = item
        return {
            "hook": name,
            "headline": name,
            "concept": name,
            "explain": definition,
            "takeaway": "מושג אחד ביום מספיק כדי להפסיק להרגיש מחוץ לשיחה.",
            "question": "איזה מושג הכי מבלבל אותך? כתוב בתגובות.",
            "tags": ["#בינהמלאכותית", "#AI", "#טכנולוגיה", "#ללמודAI"],
        }
    return {
        "hook": "משהו זז היום בעולם ה-AI",
        "headline": news["title"][:90],
        "concept": "מה חדש",
        # The feed summary can run to 600 characters, which is past anything
        # the card can hold, so it is cut to the same ceiling the model gets.
        "explain": (news.get("description") or "")[:_MAX_LEN["explain"]]
        or "עוד צעד קטן בכלים שכולנו נשתמש בהם בשנה הקרובה.",
        "takeaway": "לא צריך לעקוב אחרי הכל, מספיק להבין מה זז.",
        "question": "כבר ניסית את זה? ספר בתגובות.",
        "tags": ["#בינהמלאכותית", "#AI", "#טכנולוגיה", "#חדשות"],
    }


# Hard ceilings, past which even the smallest font would run under the footer.
# These sit well above what the prompts ask for: they are the "the model
# ignored the brief entirely" tripwire, not the style guide.
_MAX_LEN = {"hook": 130, "headline": 150, "concept": 60,
            "explain": 430, "takeaway": 130, "question": 120}


def _clean_copy(raw):
    """Accept the model's JSON only if every field is usable AND in Hebrew."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in _COPY_KEYS:
        value = raw.get(key)
        if key == "tags":
            if not isinstance(value, list):
                return None
            tags = [str(t).strip() for t in value if str(t).strip()]
            tags = [t if t.startswith("#") else f"#{t}" for t in tags]
            # A tag with a space in it is not a tag; Instagram cuts it at the
            # space and posts the remainder as plain text.
            tags = [t for t in tags if " " not in t][:4]
            if not tags:
                return None
            out[key] = tags
        else:
            if not isinstance(value, str) or not value.strip():
                return None
            value = value.strip()
            if len(value) > _MAX_LEN[key]:
                print(f"WARNING: '{key}' is {len(value)} chars, over the {_MAX_LEN[key]} ceiling",
                      file=sys.stderr)
                return None
            out[key] = value

    if not _HEBREW.search(" ".join(out[k] for k in _COPY_KEYS if k != "tags")):
        return None
    return out


def write_post_copy(category, news=None, item=None):
    if category == "concept":
        name, definition = item
        prompt = _CONCEPT_PROMPT.format(name=name, definition=definition)
    elif category == "prompt":
        name, definition = item
        prompt = _PROMPT_PROMPT.format(name=name, definition=definition)
    else:
        template = _TOOL_PROMPT if category == "tool" else _NEWS_PROMPT
        prompt = template.format(
            title=news["title"],
            description=news.get("description") or "(אין תקציר בפיד)",
        )

    try:
        result = subprocess.run(
            ["with-claude-token", "claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=COPY_TIMEOUT, check=True,
        )
        text = result.stdout.strip()
        # The model is asked for bare JSON but sometimes fences it.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = _clean_copy(json.loads(match.group(0))) if match else None
        if parsed:
            return parsed
        print(f"WARNING: unusable copy JSON for '{category}', using the template", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: copy generation failed for '{category}' ({e}), using the template",
              file=sys.stderr)

    return _fallback_copy(category, news=news, item=item)


# Every caption names the destination. Until 3.10.2026 no post mentioned the
# Telegram channel at all, so a scroller who liked a card had nowhere to go:
# the account is the funnel, the channel is the product. No raw t.me URL, since
# a link in an Instagram caption is not clickable anyway and the bio link is
# the habit worth training.
CHANNEL_CTA = "📲 עוד כלים וחדשות AI בעברית, כל יום בערוץ בינה בקטנה. הלינק בביו."


def make_caption(category, copy):
    tag = CATEGORY_META[category]["tag"]
    # The model sometimes returns the brand tag itself, spelled with
    # underscores (#בינה_בקטנה), which shipped next to our own #בינהבקטנה and
    # read as a typo. Compare tags ignoring underscores and keep the first.
    tags, seen_tags = [], set()
    for t in ["#בינהבקטנה", *copy["tags"]]:
        key = t.replace("_", "").lower()
        if key in seen_tags:
            continue
        seen_tags.add(key)
        tags.append(t)
    tags = " ".join(tags)
    return (
        f"{tag}\n\n"
        f"{copy['hook']}\n\n"
        f"{copy['headline']}\n\n"
        f"{copy['concept']}\n"
        f"{copy['explain']}\n\n"
        f"{copy['takeaway']}\n\n"
        f"{copy['question']}\n\n"
        f"{CHANNEL_CTA}\n\n"
        f"{tags}"
    )


# --- cards -------------------------------------------------------------------
#
# Right-to-left is the whole difference from the stock account's cards. The
# document direction is set once on <html>, so punctuation, parentheses and
# any embedded Latin word (GPT, RAG) get placed by the browser's bidi
# algorithm instead of by hand. Hebrew also sits lower in its line box than
# Latin does, which is why the line-heights here are looser than they look
# like they need to be.

_CARD_CSS = """
  @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;700;900&display=swap');
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    width:1080px; height:1080px;
    font-family:'Heebo', 'Arial Hebrew', sans-serif;
    background: linear-gradient(160deg, #12091f 0%, #241040 55%, #35186b 100%);
    color:#f6f4fb;
    display:flex; flex-direction:column; justify-content:space-between;
    padding:110px;
  }
  .top-row { display:flex; justify-content:space-between; align-items:center; }
  .tag {
    display:inline-block;
    background:#a78bfa; color:#1b0b33;
    font-weight:900; font-size:30px;
    padding:14px 34px; border-radius:999px;
  }
  .slide-num { font-size:28px; font-weight:700; opacity:0.5; }
  .headline { font-size:62px; font-weight:900; line-height:1.45; margin-top:56px; }
  .sub { font-size:32px; font-weight:400; line-height:1.7; opacity:0.85; margin-top:28px; }
  .eyebrow {
    font-size:26px; font-weight:900; letter-spacing:2px;
    color:#c4b5fd; margin-top:56px;
  }
  .source {
    font-size:26px; font-weight:400; line-height:1.6; opacity:0.6;
    margin-top:34px; border-right:4px solid rgba(255,255,255,0.25);
    padding-right:20px;
  }
  .swipe { font-size:28px; font-weight:700; opacity:0.6; margin-top:40px; }
  .accent { color:#c4b5fd; }
  .footer { border-top:2px solid rgba(255,255,255,0.15); padding-top:26px; }
  .footer-row { display:flex; justify-content:space-between; align-items:center; }
  .brand { font-size:36px; font-weight:900; }
  .handle { font-size:26px; opacity:0.6; }
  /* A Latin fragment inside a Hebrew sentence keeps its own direction. */
  .ltr { direction:ltr; unicode-bidi:isolate; display:inline-block; }
"""


def _card_shell(top_row_html, body_html):
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<style>{_CARD_CSS}</style>
</head>
<body>
  {top_row_html}
  {body_html}
  <div class="footer">
    <div class="footer-row">
      <div class="brand">בינה <span class="accent">בקטנה</span></div>
      <div class="handle ltr">{HANDLE}</div>
    </div>
  </div>
</body>
</html>
"""


# The card is a fixed 1080x1080 box, so long copy does not wrap onto a second
# page, it silently runs under the footer and gets cropped. Capping the text in
# the prompt is not enough: the model overshoots its own character budget
# regularly, and a post that is 15% too long still has to look deliberate. So
# the font size is chosen from the actual length at render time.
def _fit(text, steps):
    """steps is [(max_chars, px), ...] ascending; the last entry is the floor."""
    n = len(text)
    for limit, px in steps:
        if n <= limit:
            return px
    return steps[-1][1]


_HOOK_STEPS = [(45, 62), (70, 54), (95, 46), (130, 40)]
_EXPLAIN_STEPS = [(180, 52), (260, 46), (340, 40), (430, 34)]
_TAKEAWAY_STEPS = [(60, 52), (90, 46), (130, 40)]


def render_slide_html(kind, position, total, category, copy):
    """One carousel slide. Three slides, each carrying something the others don't:
    the hook, the actual explanation, and the thing to do with it."""
    meta = CATEGORY_META[category]
    top_row = (
        f'<div class="top-row"><div class="tag">{escape(meta["tag"])}</div>'
        f'<div class="slide-num ltr">{position}/{total}</div></div>'
    )

    if kind == "hook":
        # "Swipe" without a direction word: Instagram does not mirror its
        # carousel for Hebrew, so naming a side would be wrong half the time.
        body = (
            f'<div class="headline" style="font-size:{_fit(copy["hook"], _HOOK_STEPS)}px;">'
            f'{escape(copy["hook"])}</div>'
            f'<div class="source">{escape(copy["headline"])}</div>'
            f'<div class="swipe">החלק להמשך</div>'
        )
    elif kind == "explain":
        body = (
            f'<div class="eyebrow">{escape(meta["eyebrow"])}</div>'
            f'<div class="headline" style="font-size:{_fit(copy["explain"], _EXPLAIN_STEPS)}px;">'
            f'{escape(copy["explain"])}</div>'
        )
    else:
        body = (
            '<div class="eyebrow">שורה תחתונה</div>'
            f'<div class="headline" style="font-size:{_fit(copy["takeaway"], _TAKEAWAY_STEPS)}px;">'
            f'{escape(copy["takeaway"])}</div>'
            f'<div class="sub">{escape(copy["question"])}</div>'
            # The handle is already in the footer of every slide, so this line
            # carries the frequency promise instead of repeating it.
            '<div class="swipe">פוסט חדש כאן כל יום</div>'
        )
    return _card_shell(top_row, body)


def render_png(html_path: pathlib.Path, png_path: pathlib.Path):
    """Render to PNG via headless Chrome, then prove it is really our card.

    Paths are resolved to absolute first, always: a relative path produces an
    INVALID file:// URL (the first segment is parsed as a host), Chrome
    screenshots its own error page and still exits 0. That exact failure put a
    browser error screen on the stock account on 18.9.2026, which is why the
    exit code is not trusted here and the pixels are checked instead.

    --virtual-time-budget is what makes the Hebrew webfont safe: without it
    Chrome can screenshot before Heebo has loaded, and the card silently falls
    back to a system font mid-queue.
    """
    html_path = html_path.resolve()
    png_path = png_path.resolve()
    if not html_path.is_file():
        raise RuntimeError(f"render_png: source HTML does not exist: {html_path}")

    subprocess.run(
        [CHROME, "--headless", "--disable-gpu",
         f"--screenshot={png_path}", "--window-size=1080,1080",
         "--virtual-time-budget=4000", "--hide-scrollbars",
         f"file://{html_path}"],
        check=True, capture_output=True, timeout=60,
    )

    ok, reason = is_valid_card(png_path)
    if not ok:
        raise RuntimeError(f"render_png: rendered image failed validation ({reason}): {png_path}")


SLIDE_PLAN = ["hook", "explain", "takeaway"]


def build_slot(slot_dir: pathlib.Path, category, copy):
    slot_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for i, kind in enumerate(SLIDE_PLAN, start=1):
            html = render_slide_html(kind, i, len(SLIDE_PLAN), category, copy)
            html_path = tmp / f"slide_{i}.html"
            html_path.write_text(html, encoding="utf-8")
            render_png(html_path, slot_dir / f"post_{i}.png")

    (slot_dir / "caption.txt").write_text(make_caption(category, copy), encoding="utf-8")
    (slot_dir / "source.json").write_text(
        json.dumps({"category": category, "copy": copy}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="render one post into a temp dir and queue nothing")
    args = parser.parse_args()

    slots = next_free_slots(1 if args.dry_run else TARGET_QUEUE_DEPTH)
    if not slots:
        print("Queue is already full, nothing to do.")
        return 0

    seen_headlines = load_seen(SEEN_FILE)
    seen_terms = load_seen(SEEN_TERMS_FILE)
    headlines = fetch_headlines()
    print(f"{len(headlines)} headlines from {len(RSS_FEEDS)} feeds, {len(slots)} slot(s) to fill")

    index = next_category_index()
    made, failed = 0, 0

    for slot in slots:
        category = CATEGORY_CYCLE[index % len(CATEGORY_CYCLE)]
        index += 1

        news, item = None, None
        if category in ("news", "tool"):
            news = pick_headline(headlines, seen_headlines)
            if news is None:
                # No usable story left in the feeds. Rather than lose the slot,
                # fall back to material that is always available.
                category = "concept"
                item = pick_from_list(CONCEPTS, seen_terms)
            else:
                seen_headlines.add(news["title"])
        elif category == "concept":
            item = pick_from_list(CONCEPTS, seen_terms)
        else:
            item = pick_from_list(PROMPT_TIPS, seen_terms)

        if item is not None:
            seen_terms.add(item[0])

        copy = write_post_copy(category, news=news, item=item)
        name = slot.strftime("%Y-%m-%dT%H%M")

        if args.dry_run:
            preview = pathlib.Path(tempfile.mkdtemp(prefix="ai-he-preview-")) / name
            try:
                build_slot(preview, category, copy)
            except Exception as e:
                print(f"FAILED dry run ({category}): {e}", file=sys.stderr)
                return 1
            print(f"[dry run] {category} rendered to {preview}")
            return 0

        try:
            build_slot(SCHEDULED / name, category, copy)
        except Exception as e:
            print(f"FAILED {name} ({category}): {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"queued {name} ({category}): {copy['hook']}")
        made += 1

    save_seen(SEEN_FILE, seen_headlines)
    save_seen(SEEN_TERMS_FILE, seen_terms)
    print(f"Made {made} new post(s), {failed} failed.")
    return 1 if failed and not made else 0


if __name__ == "__main__":
    sys.exit(main())
