# Resuming the "Building on a Budget" scrape

## What this is
Scraping all 317 "Building on a Budget" articles (2003-2009) from the Wayback
Machine snapshot of wizards.com. Each article gets a full-page screenshot
(images rendered) and its decklist(s) extracted in MTGO format.

## One-time environment setup (needed in a fresh container/session)
```bash
pip install playwright beautifulsoup4 requests
python3 -m playwright install chromium --with-deps
```

## Layout
- `archive/<YYYYMMDD_DeckName>/` — one folder per article
  - `screenshot.png` — full-page screenshot, images loaded, wayback toolbar hidden
  - `decklist.txt` (or `decklist_1_<Title>.txt`, `decklist_2_...` for multi-deck articles) — MTGO format
  - `source.html` — raw archived HTML, kept for provenance/debugging
  - `status.json` — `{status, notes, url, slug, date, ...}`
- `log.md` (repo root) — table of every article with status + notes
- `scripts/master_index.json` — the 317 articles (title/date/slug/folder), scraped from the 13 listing pages
- `scripts/build_index.py` — rebuilds `master_index.json` from the Wayback listing pages. Only rerun if the index needs regenerating.
- `scripts/scrape.py` — main pipeline (screenshot + decklist extraction). **Resumable**: skips any folder whose `status.json` already has status OK/PARTIAL/NO_DECKLIST. Use `--force` to redo, `--slugs <slug1> <slug2>` to target specific articles, `--limit N --start N` to slice.
- `scripts/retry_screenshots.py` — fixes a bug from the first full run: `wait_until="networkidle"` timed out on image-heavy pages, silently leaving ~78/317 articles without a screenshot even though status wasn't always flagged FAILED. This script finds every `status.json` with `SCREENSHOT_MISSING` in its notes and retries with `wait_until="load"` + an explicit bounded wait for all `<img>` elements. Safe to rerun — only touches folders still flagged.

## Check where things stand
```bash
cd /workspaces/Debtors-Knell
for f in archive/*/status.json; do python3 -c "import json;print(json.load(open('$f'))['status'])"; done | sort | uniq -c
grep -l SCREENSHOT_MISSING archive/*/status.json   # anything still missing a screenshot
```

## If the screenshot retry pass got interrupted
```bash
cd /workspaces/Debtors-Knell/scripts
python3 retry_screenshots.py
```

## Regenerate log.md after any changes
```bash
cd /workspaces/Debtors-Knell/scripts
python3 -c "
import scrape, json, os
full_entries = json.load(open(scrape.INDEX_PATH))
all_status = []
for e in full_entries:
    sp = os.path.join(scrape.ARCHIVE_DIR, e['folder'], 'status.json')
    if os.path.exists(sp):
        all_status.append(json.load(open(sp)))
scrape.write_log(all_status)
print('wrote', scrape.LOG_PATH)
"
```

## Known, mostly-expected non-OK statuses (as of the first full run)
- **7 FAILED** — Wayback network timeouts on very image-heavy pages. Worth a manual retry (`--slugs`) or just accept — these are edge cases.
- **16 NO_DECKLIST** — genuinely no `<div class="deck">` block in the source (trading/strategy articles, "unplugged" retrospectives, etc.) — verified not a parser bug, spot-checked several.
- **~235 PARTIAL** — mostly genuine: many older/round-robin-era articles simply never published a sideboard list in the source HTML (verified by grepping source.html for "sideboard" and finding zero hits). A smaller number may be real parser misses worth spot-checking if you want higher accuracy.

## Outstanding: reprice the repaired decklists
`scripts/scrape.py`'s `parse_cell_cards()` used to stop at the first `<hr>` in a
deck cell. On pages that run every card group (lands / creatures / spells) down
a single `<td>` separated by those subtotal rules, that kept only the first
group — 50 decklists across 35 articles were saved short, from 3 cards missing
up to "Expensive Wurms!" being saved as its six lands and nothing else.

The parser is fixed, and `scripts/repair_truncated_decklists.py` has re-extracted
those `decklist*.txt` files from the already-archived `source.html` (no network
needed). What has **not** run is the reprice — `decklist*_priced.md` is built by
a separate script that needs a Scryfall bulk download, so those 50 priced tables
are still missing the recovered cards and their totals are low. The deck pages
say so inline until it's done.

The fix is the **Reprice stale decklists** workflow
(`.github/workflows/reprice-decklists.yml`) — dispatch it manually from the
Actions tab once this is on `main`. It works out which decklists are stale via
`scripts/find_stale_priced_decklists.py`, regenerates only those, and is a
green no-op if there's nothing to do.

To do it by hand instead, from somewhere with access to `api.scryfall.com`:

```bash
pip install -r scripts/requirements-prices.txt beautifulsoup4 rapidfuzz ijson
python3 scripts/find_stale_priced_decklists.py --paths | xargs -r -d '\n' rm --
python3 scripts/build_markdown_and_prices.py --skip-md \
    --only $(python3 scripts/find_stale_priced_decklists.py --folders | tr '\n' ' ')
python3 scripts/find_stale_priced_decklists.py   # should report nothing stale
```

Deleting the stale files and running *without* `--force-price` matters:
`--force-price` is folder-wide, so it would also reprice the 55 healthy
decklists that share those 35 folders. Two knock-on effects clear themselves up
once this has run — the per-deck price-history charts
(`build_price_history.py`, rebuilt with `force=True` by the next
`fetch_prices.py` run) and the /movers/ card pool (`build_movers.py`), both of
which read the priced files.

## Not yet done
- Final tally/spot-check of the screenshot retry pass results
- Optional: re-attempt the 7 FAILED articles
- Optional: merge duplicate card lines within a single decklist.txt (e.g. "4 Island" + "10 Island" as two lines instead of one "14 Island" line) — cosmetically imperfect but functionally valid MTGO format, so low priority
