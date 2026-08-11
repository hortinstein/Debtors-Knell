# Hobbit Watch

A small, standalone Flask app for tracking prices of the **new Hobbit cards**
(GoatBots set code `HOB`), built entirely on the price archive this repo
already collects daily (`../prices/`, see `../prices/README.md`).

It is deliberately separate from `../webapp` — different folder, its own
templates, static files and routes. The main site is about the 2003–2009
"Building on a Budget" decklists; this one is about one brand-new set.

## What it shows

* Every card in the set, in one searchable, sortable table.
* **Physical** price (USD, from MTGJSON's daily snapshots) and **digital**
  price (MTGO tix, from GoatBots), plus the **change since that card's first
  archived data point** in each market — the set is new, so the clock started
  when the archive first saw it.
* Filters: search by title, rarity chips, gainers/losers in either market, and
  "only cards with a physical price". Every column sorts.
* Click (or press Enter on) a row for a modal with the card's **art**, a
  **price chart** with a digital/physical toggle, and **every other MTGO
  printing of the same card** — each with its own art, current tix price,
  change, and a spark line of its recent history.

Card art comes from Scryfall image URLs built in the browser (the same
no-API-call trick `../webapp/static/card-preview.js` uses); a set code Scryfall
doesn't recognize falls back to a lookup by exact card name.

## Run it

```bash
cd hobbitwatch
pip install -r requirements.txt
python3 build_dataset.py      # first build: ~2 minutes
python3 app.py                # http://127.0.0.1:5001
```

`app.py` builds the dataset itself if it's missing or older than the newest
archived price day, so the explicit `build_dataset.py` step is optional — it
just moves the wait out of the first page load.

## How the data is put together

`build_dataset.py` writes `data/hobbit_cards.json` (gitignored — derived data,
rebuilt from the archive). It reads:

| Source | Used for |
| --- | --- |
| `../prices/daily/<date>/goatbots/card-definitions.zip` | the set's card list (name, rarity, collector number) and every other printing sharing a card's name |
| `../prices/goatbots_yearly_archive/<year>/<date>.txt.gz` and `../prices/daily/<date>/goatbots/price-history.zip` | digital (tix) history, **per printing** |
| `../prices/daily/<date>/mtgjson/AllPricesToday.json.bz2` joined via `../prices/mtgjson/uuid_to_name.json.gz` | physical (USD) history, **per card name** |

It reuses the archive readers in `../scripts/build_price_history.py` rather
than re-implementing them, and follows the same conventions as the rest of the
repo: median across matched printings per day, and the MTGJSON snapshot's own
`meta.date` (not the fetch date) as the series date.

Useful flags:

```bash
python3 build_dataset.py --days 0          # read every archived day, not the last 180
python3 build_dataset.py --set LTR         # point it at a different set
python3 build_dataset.py --max-versions 8  # fewer "other versions" per card
python3 build_dataset.py --force           # rebuild even if up to date
```

## Known coverage limits

* **Digital history starts 2026-08-04** — that's the first day GoatBots listed
  the set. Nothing earlier exists to chart.
* **Physical prices are thin for now.** MTGJSON's price snapshots are keyed by
  uuid with no name attached, and the uuid → name map this repo keeps
  (`scripts/build_mtgjson_uuid_map.py`) refreshes monthly, so only the part of
  the set already in that map (16 of 193 cards at the time of writing) has a
  USD series. The rest fill in automatically at the next refresh — no change to
  this app needed. Cards with no paper data show a dash and say so in the modal.
* **Physical prices are per card name, not per printing**, for the same reason:
  there is no set information in the price snapshots. Digital prices *are* per
  printing, which is why the "other versions" grid prices are tix.

## Routes

| Route | What it serves |
| --- | --- |
| `/` | the set table |
| `/api/cards.json` | the whole dataset |
| `/api/card/<slug>.json` | one card: series, stats, versions (what the modal fetches) |
