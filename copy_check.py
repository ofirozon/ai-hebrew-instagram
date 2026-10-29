#!/usr/bin/env python3
"""The copy rules that are cheaper to enforce than to ask for.

The same idea as the stock account's copy_check.py, and deliberately a separate
file rather than a shared one: these are two repos with two remotes, and the
rules only look identical. Hebrew needs different filler phrases, a different
readability proxy (Flesch-Kincaid is fitted to English syllables and means
nothing here), and one rule the English account does not have at all, the
em-dash ban.

Two kinds of problem:

* **hard** - unambiguous. Filler phrase, an exclamation mark, an em-dash, an
  explain with no number in it, two slides opening on the same word. Never
  accepted.
* **soft** - a judgement call measured by a crude proxy. Sentence length is
  the only one, and it is accepted on the last attempt rather than dropping
  the post back to the template copy, which is worse on every axis.

Items 26, 33, 34, 35, 37 and 39 of instagram-backlog.md.
"""
import datetime
import re

# Item 35, in Hebrew. Every one of these is a phrase a model reaches for when
# it has nothing specific to say, and a reader skips. On a three-slide card,
# one skipped sentence is a third of the post.
BANNED_PHRASES = [
    "בעולם של היום", "בעידן שבו", "בעידן הדיגיטלי", "בעולם המודרני",
    "חשוב לציין", "חשוב להבין", "יש לציין", "ראוי לציין",
    "בשורה התחתונה", "בסופו של דבר", "אין ספק", "למעשה ניתן לומר",
    "בואו נצלול", "בואו נדבר", "נתחיל מההתחלה",
    "מהפכני", "מהפכה של ממש", "פורץ דרך", "משנה את כללי המשחק",
    "ישנה את העולם", "העתיד כבר כאן", "הדבר הגדול הבא",
    "בקצב מהיר", "בקצב מסחרר", "ללא ספק",
    "כלי עוצמתי", "פתרון מושלם", "בלחיצת כפתור",
    # Hedges. The page explains; a sentence that hedges explains nothing.
    "כל אחד יחליט", "תלוי בכם", "זה תלוי", "ייתכן שכן וייתכן שלא",
]

# Item 39. Ofir's standing no-em-dash rule, which until now only covered his
# Telegram posts. The same rule belongs here: these captions are read by the
# same Hebrew audience, and the dash renders as a stray line in RTL text.
_DASHES = re.compile(r"[—–]")

# The prompts have asked for no exclamation marks since the account started,
# and the model has complied most of the time, which is the problem.
_EXCLAIM = re.compile(r"!")

_HAS_DIGIT = re.compile(r"\d")

# Item 26. A number is what makes an explanation picturable. Hebrew writes
# small numbers out as often as it writes them in digits, so both count.
_SPELLED_NUMBERS = re.compile(
    r"(?:^|[\s\"'(])(שני|שתי|שניים|שתיים|שלוש|שלושה|ארבע|ארבעה|חמש|חמישה|"
    r"שש|שישה|שבע|שבעה|שמונה|תשע|תשעה|עשר|עשרה|עשרים|שלושים|ארבעים|חמישים|"
    r"מאה|מאות|אלף|אלפים|מיליון|מיליארד|חצי|שליש|רבע|כפול|פי שניים|פי שלושה)"
    r"(?:$|[\s,.;:)\"'])"
)

_WORD = re.compile(r"[֐-׿A-Za-z][֐-׿A-Za-z'’\"-]*")
_SENTENCE_SPLIT = re.compile(r"[.!?]+\s+|[.!?]+$|\n+")

# Item 37. Words too common at the start of a Hebrew sentence to count as a
# distinctive opening, so the variety check looks past them.
_STOPWORDS_AT_START = {
    "ה", "זה", "זו", "את", "אם", "גם", "כל", "יש", "אין", "לא", "כי", "אבל",
    "עם", "של", "על", "אז", "מה", "הוא", "היא", "הם", "אתה", "אתם", "אני",
    "בכל", "כשאתה", "עכשיו", "היום",
}


def banned_phrases(text):
    return [p for p in BANNED_PHRASES if p in text]


def has_quantity(text):
    return bool(_HAS_DIGIT.search(text) or _SPELLED_NUMBERS.search(text))


def words(text):
    return _WORD.findall(text or "")


def opening_word(text):
    """The first word that carries meaning, for the variety check."""
    found = words(text)
    for w in found:
        if w not in _STOPWORDS_AT_START:
            return w
    return found[0] if found else ""


def sentence_lengths(text):
    return [len(words(s)) for s in _SENTENCE_SPLIT.split(text or "") if words(s)]


# Item 33, the Hebrew equivalent of a reading-level ceiling. Hebrew has no
# usable syllable heuristic and no validated readability formula, so the proxy
# is sentence length, which is what actually makes a card need a second read.
MAX_WORDS_PER_SENTENCE = 16
MAX_SENTENCE_WORDS = 24


def repeated_shape(slides):
    """Item 34: two slides in one carousel built the same way."""
    problems, opens = [], {}
    for name, text in slides.items():
        w = opening_word(text)
        if not w:
            continue
        if w in opens:
            problems.append(f"{name} ו{opens[w]} שניהם נפתחים ב\"{w}\"")
        else:
            opens[w] = name
    return problems


# Item 99, and the reason it is a hard gate rather than a prompt line. On
# 4.10.2026 a test generation wrote "according to a New York Times
# investigation from September 2026" about a story that is an EFF article. The
# month was right, the newspaper was invented, and nothing in the pipeline
# could have caught it: the feed item had no body text, so the model filled the
# gap with the most plausible-sounding outlet.
#
# A named outlet is the one kind of invented detail that is both common and
# checkable, so it is checked. Each entry is (what it may be called in the
# Hebrew copy, the spellings that prove it in the English source).
_OUTLETS = [
    (["ניו יורק טיימס", "הניו יורק טיימס"], ["new york times", "nytimes", "nyt"]),
    (["וושינגטון פוסט"], ["washington post", "wapo"]),
    (["וול סטריט ג'ורנל", "וול סטריט"], ["wall street journal", "wsj"]),
    (["רויטרס"], ["reuters"]),
    (["בלומברג"], ["bloomberg"]),
    (["הגרדיאן", "גרדיאן"], ["guardian"]),
    (["פייננשל טיימס"], ["financial times", "ft.com"]),
    (["פורבס"], ["forbes"]),
    (["וויירד", "ווירד"], ["wired"]),
    (["טקקראנץ", "טק קראנץ"], ["techcrunch"]),
    (["אסושייטד פרס", "אסוסייטד פרס"], ["associated press", "ap news"]),
    (["בי בי סי"], ["bbc"]),
    (["סי אן אן"], ["cnn"]),
    (["הארץ"], ["haaretz"]),
    (["כלכליסט"], ["calcalist"]),
    (["גלובס"], ["globes"]),
    (["אקונומיסט", "האקונומיסט"], ["economist"]),
    (["ארס טכניקה"], ["ars technica", "arstechnica"]),
    (["אטלנטיק", "האטלנטיק"], ["the atlantic"]),
    (["בוויסינס אינסיידר", "ביזנס אינסיידר"], ["business insider"]),
]


def unsourced_outlets(text, source_text):
    """Outlets the copy names that do not appear in the source material.

    With no source material at all this returns nothing: a concept or prompt
    post has no article behind it, and there is nothing to check against.
    """
    if not source_text:
        return []
    low_source = source_text.lower()
    found = []
    for hebrew_names, proofs in _OUTLETS:
        if any(name in text for name in hebrew_names):
            if not any(proof in low_source for proof in proofs):
                found.append(hebrew_names[0])
    return found


# Item 98, ported from the stock account 29.10.2026. A five-digit number with
# no thousands separator ("12000000") has to be counted digit by digit, and on
# a card the reader gives it one glance. Hebrew uses the same comma.
_UNSEPARATED = re.compile(r"(?<![\d.,])([$₪]\d{4,}|\d{5,})(?![\d,]|\.\d)")


def unseparated_numbers(text):
    return _UNSEPARATED.findall(text or "")


# Item 99, the figure half, ported 29.10.2026. The outlet check above catches
# an invented source; this catches an invented number. Every figure on the
# three slides has to be in the English headline or summary the model was
# given, magnitudes applied either way ("$40B" == "40 מיליארד"), unless the
# sentence is plainly a hypothetical or dated two or more years back. The stock
# account only checks sentences that name the story's subject; here the copy is
# Hebrew and the headline English, so that test is unavailable, and the check
# covers every sentence. Run over all 41 news posts published to 28.10.2026,
# the only false alarms were "three" spelled out in the source, hence the words.
_MAGNITUDE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6,
              "billion": 1e9, "b": 1e9, "bn": 1e9, "trillion": 1e12, "t": 1e12,
              "אלף": 1e3, "אלפים": 1e3, "אלפי": 1e3, "מיליון": 1e6, "מיליוני": 1e6,
              "מיליארד": 1e9, "מיליארדי": 1e9, "טריליון": 1e12}
_FIGURE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|אחוזים|אחוז|thousand\b|million\b|"
    r"billion\b|trillion\b|bn\b|mn\b|[kmbt]\b|אלפים|אלפי|אלף|מיליוני|מיליון|"
    r"מיליארדי|מיליארד|טריליון)?", re.IGNORECASE)
_ENGLISH_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100,
}
_HYPOTHETICAL_HE = re.compile(r"(?:^|\s)(אם|נניח|לדוגמה|למשל|דמיינו|תדמיינו|כאילו)\s")
_YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def _figures(text):
    out = set()
    for num, unit in _FIGURE.findall(text or ""):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        if unit in ("%", "percent", "אחוז", "אחוזים"):
            out.add(("pct", value))
        else:
            out.add(("n", value * _MAGNITUDE.get(unit, 1)))
    return out


def unsourced_figures(copy, source_text, year=None):
    """[(field, sentence, figure)] for numbers on the slides the source never had."""
    if not source_text:
        return []
    known = _figures(source_text)
    low = source_text.lower()
    known |= {("n", float(v)) for w, v in _ENGLISH_NUMBERS.items()
              if re.search(rf"\b{w}\b", low)}
    found = []
    for key in ("hook", "explain", "takeaway"):
        for sentence in re.split(r"(?<=[.?!])\s+", copy.get(key, "") or ""):
            if _HYPOTHETICAL_HE.search(f" {sentence}"):
                continue
            years = [int(y) for y in _YEAR.findall(sentence)]
            if year and any(y <= year - 2 for y in years):
                continue
            for kind, value in _figures(sentence):
                if kind == "n" and 1900 <= value <= 2100 and value == int(value):
                    continue  # a year, not a figure
                if (kind, value) not in known:
                    shown = f"{value:g}%" if kind == "pct" else (
                        f"{value:,.0f}" if value == int(value) else f"{value:g}")
                    found.append((key, sentence.strip(), shown))
    return found


def figure_problems(copy, source_text, year=None):
    return [
        f"בשדה {key} מופיע המספר {fig} (\"{sentence[:70]}\"), והוא לא מופיע "
        f"בכותרת או בתקציר. אסור להמציא מספר על הסיפור. להוריד אותו או להחליף "
        f"במספר שכתוב במקור"
        for key, sentence, fig in unsourced_figures(copy, source_text, year)
    ]


def _this_year():
    return datetime.date.today().year


def check(copy, recent_openings=(), source_text=None):
    """Every rule, over a finished copy dict.

    Returns (hard, soft): two lists of problems, in Hebrew, written to be
    pasted straight into the retry prompt.
    """
    hard, soft = [], []
    prose_keys = ("hook", "headline", "explain", "takeaway", "question")

    for key in prose_keys:
        text = copy.get(key, "")
        found = banned_phrases(text)
        if found:
            hard.append(
                f"בשדה {key} יש את הביטוי השחוק \"{found[0]}\", צריך למחוק אותו "
                f"ולא לנסח סביבו"
            )
        if _DASHES.search(text):
            hard.append(
                f"בשדה {key} יש מקף ארוך, אסור בעברית. במקומו פסיק, נקודתיים "
                f"או שני משפטים"
            )
        if _EXCLAIM.search(text):
            hard.append(f"בשדה {key} יש סימן קריאה, אסור")
        for outlet in unsourced_outlets(text, source_text):
            hard.append(
                f"בשדה {key} מיוחס המידע ל\"{outlet}\", והשם הזה לא מופיע "
                f"בחומר המקור. אסור לייחס לגוף תקשורת שלא כתוב במקור. אם לא "
                f"ברור מי פרסם, לא מזכירים אף גוף"
            )

        bad = unseparated_numbers(text)
        if bad:
            hard.append(
                f"בשדה {key} כתוב {bad[0]} בלי פסיק של אלפים. לכתוב עם פסיקים "
                f"(12,000,000) או במילים (12 מיליון)"
            )

    hard += figure_problems(copy, source_text, year=_this_year())

    # Item 26, and the one place this file deliberately differs from the stock
    # account's. There, a number is a hard requirement: a market story always
    # has a price, a percentage or a count, and an explain without one is a
    # paraphrase of the headline. Here it is SOFT on purpose. Plenty of real AI
    # stories have no number in them at all ("someone built a project that
    # displays language models as prisoners"), and a hard gate would make the
    # model invent a statistic to get past it. Invented precision in a Hebrew
    # news caption is a far worse failure than a missing number, so this costs
    # one retry and then ships.
    if not has_quantity(copy.get("explain", "")):
        soft.append(
            "בשדה explain אין שום מספר. אם לסיפור יש מספר אמיתי, מספר משתמשים, "
            "אחוז, תאריך או מחיר, לשים אותו שם. אם באמת אין בסיפור מספר, להשאיר "
            "כמו שזה. בשום מקרה לא להמציא מספר"
        )

    # Item 34 / 37, over the three fields that are actually slides.
    hard += repeated_shape({k: copy.get(k, "") for k in ("hook", "explain", "takeaway")})
    opening = opening_word(copy.get("hook", ""))
    if opening and opening in {o for o in recent_openings if o}:
        hard.append(
            f"ה-hook נפתח ב\"{opening}\", מילה שהפוסטים האחרונים כבר פתחו בה. "
            f"לפתוח במילה אחרת"
        )

    # Item 33, soft.
    lengths = sentence_lengths(copy.get("explain", ""))
    if lengths:
        longest = max(lengths)
        average = sum(lengths) / len(lengths)
        if longest > MAX_SENTENCE_WORDS:
            soft.append(
                f"המשפט הארוך ב-explain הוא {longest} מילים, המקסימום "
                f"{MAX_SENTENCE_WORDS}. לפצל אותו לשניים"
            )
        elif average > MAX_WORDS_PER_SENTENCE:
            soft.append(
                f"אורך המשפט הממוצע ב-explain הוא {average:.0f} מילים, היעד עד "
                f"{MAX_WORDS_PER_SENTENCE}. משפטים קצרים יותר"
            )

    return hard, soft
