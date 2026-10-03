# בינה בקטנה — Instagram

Hebrew AI content on Instagram, published automatically. Built 29.9.2026 on the
same architecture as `stocklearneasy-instagram`, which has run unattended since
September 2026.

## How it works

The Mac writes, the cloud publishes. They are deliberately separate:

1. `ai-he-ig-queue.sh` runs on the Mac daily at 08:15. It calls `generate.py`,
   which pulls AI headlines from English feeds, has the model write the post in
   Hebrew, renders three right-to-left cards with headless Chrome, and commits
   them to `scheduled/<YYYY-MM-DDTHHMM>/` (the slot name is **UTC**).
2. `.github/workflows/publish.yml` runs in GitHub Actions every 10 minutes. It
   publishes any slot whose time has passed, moves it to `published/`, and
   appends to `published-log.jsonl`.

A sleeping Mac delays the queue but never misses a slot, because nothing about
publishing depends on this machine being awake.

**Slots:** 10:00 and 17:00 UTC, which is 13:00 and 20:00 Israel time.
**Queue target:** 6 future posts (three days of cover).

## Connecting the Instagram account

This is the only part that cannot be automated, because it needs a human in a
browser. Nothing publishes until it is done; until then the pipeline just keeps
a full queue warm.

1. The account must be an Instagram **professional** account (Business or
   Creator), which is a free switch in the app under Settings → Account type.
2. Get a long-lived access token and the numeric user id for that account.
3. Store the token in the Mac's Keychain so the daily refresh can keep it alive:

   ```
   security add-generic-password -a aihebrew -s ai-hebrew-ig-token -w '<token>' -U
   ```

4. Put both values in this repo's GitHub secrets:

   ```
   gh secret set IG_ACCESS_TOKEN --repo ofirozon/ai-hebrew-instagram --body '<token>'
   gh secret set IG_USER_ID      --repo ofirozon/ai-hebrew-instagram --body '<user id>'
   ```

The repo must stay **public**. Instagram fetches each card from
`raw.githubusercontent.com`, so making it private breaks publishing. There are
no secrets in the tree, only in GitHub's secret store.

## Content

Four categories cycle evenly, so the feed never becomes only news:

| Category | What it is | Source |
|---|---|---|
| `news`    | Something that happened, explained for a non-specialist | RSS |
| `tool`    | One thing you can open and use today | RSS |
| `concept` | An AI term, explained through a concrete example | `CONCEPTS` in `generate.py` |
| `prompt`  | A prompting habit worth copying | `PROMPT_TIPS` in `generate.py` |

The feeds are English because Hebrew AI reporting is too thin to fill a daily
queue; the model writes the Hebrew. `OFF_BRAND_PATTERN` drops the finance and
industry stories (funding, earnings, lawsuits) that belong on a different
account, and `_clean_copy` rejects any post that comes back without Hebrew in
it, which is the failure mode you would otherwise only notice once it is live.

If the feeds have nothing usable, a `news` or `tool` slot silently becomes a
`concept` post rather than going empty. Losing the variety beats losing the day.

## Safety rails

These exist because each one has already failed somewhere:

- **`card_check.py`** runs twice, once at render and once in the cloud right
  before publishing. It rejects anything that is not 1080x1080 with a dark
  card background. On 18.9.2026 the stock account published a screenshot of a
  browser error page; Chrome exits 0 on its own error screen, so the exit code
  is not trusted and the pixels are checked instead.
- **`--virtual-time-budget`** in `render_png` gives the Hebrew webfont time to
  load. Without it Chrome can screenshot first and fall back to a system font.
- **Length ceilings** (`_MAX_LEN`) plus length-aware font sizing. The card is a
  fixed box, so copy that runs long does not wrap to a second page, it slides
  under the footer and gets cropped.
- **The queue-fill reports its own failure** by Telegram, the same morning. The
  stock pipeline crashed silently for three days in September and drained to
  zero, and a slot that has passed cannot be backfilled.

## Local commands

```
python3 generate.py            # top the queue back up
python3 generate.py --dry-run  # render one post to a temp dir, queue nothing
DRY_RUN=1 python3 publish.py   # show what would publish, send nothing
```

Scheduled on the Mac through `~/.local/bin/agent-schedule.txt`:
`ai-he-ig-queue` (08:15), `ai-he-ig-watchdog` (21:55), and two publish kicks a
few minutes after each slot.
