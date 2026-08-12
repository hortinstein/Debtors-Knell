# Hobbit Watch

A small, standalone Flask app for tracking prices of the **new Hobbit cards** —
both products of the release: `HOB` (The Hobbit, 193 cards) and `HOC` (The
Hobbit Eternal, 117 cards). It's built on the price archive this repo already
collects daily (`../prices/`, see `../prices/README.md`), plus a checked-in
Scryfall snapshot for art and per-printing paper prices.

The card list comes from **paper**, not from MTGO. That distinction matters:
`HOC` has no MTGO printings at all, so a GoatBots-driven card list misses all
117 of its cards — including the most valuable ones in the release. GoatBots is
a source of tix prices here, not a census.

It is deliberately separate from `../webapp` — different folder, its own
templates, static files and routes. The main site is about the 2003–2009
"Building on a Budget" decklists; this one is about one brand-new set.

## What it shows

* Every card in the set, in one searchable, sortable table.
* **Physical** price (USD, current per-printing price from Scryfall, with
  day-by-day history from MTGJSON's archived snapshots) and **digital** price
  (MTGO tix, from GoatBots), plus the **change since that card's first archived
  data point** in each market — the set is new, so the clock started when the
  archive first saw it.
* Filters: search by title, rarity chips, gainers/losers in either market, and
  "only cards with a physical price". Every column sorts.
* Click (or press Enter on) a row for a modal with the card's **art**, a
  **price chart** with a digital/physical toggle, and **every other MTGO
  printing of the same card** — each with its own art, current tix price,
  change, and a spark line of its recent history.

Card art is the real per-printing image from the checked-in Scryfall snapshot;
where that's missing it falls back to a Scryfall image URL built in the browser
by exact card name (the same no-API-call trick
`../webapp/static/card-preview.js` uses).

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
| `../prices/daily/<date>/goatbots/card-definitions.zip` | the set's card list (name, rarity, collector number) and every MTGO printing sharing a card's name |
| `../prices/goatbots_yearly_archive/<year>/<date>.txt.gz` and `../prices/daily/<date>/goatbots/price-history.zip` | digital (tix) history, **per printing** |
| `../prices/daily/<date>/mtgjson/AllPricesToday.json.bz2` joined via `../prices/mtgjson/uuid_to_name.json.gz` | physical (USD) history, **per card name** |
| `scryfall/hob_prints.json.gz` (checked in, from `fetch_scryfall.py`) | current physical price **per printing**, card art, and every printing of a card including paper-only ones |

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

## Refreshing the network-sourced data

Neither Scryfall nor MTGJSON is reachable from a sandboxed dev container, so
both fetches run in CI: **`.github/workflows/hobbit-data.yml`** fetches the
Scryfall snapshot and forces a refresh of `prices/mtgjson/uuid_to_name.json.gz`
(the daily price workflow only refreshes that map monthly, which would leave a
new set unpriced in paper for weeks), then commits both back to the branch. It
runs on push to this branch, weekly on Sundays, and on manual dispatch.

Where Scryfall *is* reachable, the fetch is just:

```bash
python3 hobbitwatch/fetch_scryfall.py     # → hobbitwatch/scryfall/hob_prints.json.gz
python3 scripts/build_mtgjson_uuid_map.py --force
python3 hobbitwatch/build_dataset.py --force
```

## Known coverage limits

* **Digital history starts 2026-08-04** — that's the first day GoatBots listed
  the set. Nothing earlier exists to chart.
* **Prices are per printing on both sides.** MTGJSON's price snapshots carry
  only a uuid, so `scripts/build_mtgjson_uuid_map.py` records each uuid's set
  code and collector number alongside its name, and the dataset joins a card to
  its *own* printing. That matters here: every mythic in this set ships as a
  base printing plus extended-art and serialized variants, and a median across
  all of them priced Smaug's $31 base printing at $61. Cards the map can't
  place fall back to the name-level median, and each card records which basis
  it used (`usd_basis`).
* **The early data is preorder pricing, not market noise.** The set released
  2026-08-14 and paper archiving began 2026-07-15, so the first weeks of the
  physical series are preorder speculation collapsing — several mythics show
  -60% to -90% since their first archived day. That's what the data says; the
  page doesn't smooth it.
* **40 of the 117 `HOC` cards have no paper price.** They're the deck-filler
  reprints (collector numbers 161+); neither MTGJSON nor Scryfall lists a USD
  retail price for those specific printings yet. All 40 *are* in the uuid map,
  so they'll price themselves as soon as a vendor lists them. They show a dash
  meanwhile.
* **`HOC` has no archived tix history** — it isn't in the GoatBots MTGO
  catalogue. Where Scryfall reports a current tix price for one of its cards,
  the modal shows it and labels it as Scryfall's.
* **Not included:** `THOB`, the 15-printing token set (`--include-tokens` to
  fetch it). Also checked and correctly excluded: the `SPG` Special Guests
  batch that sits near `HOB` in GoatBots' id range — the price archive shows
  those 20 cards a week before `HOB` appeared, so they belong to an earlier
  release.

## Routes

| Route | What it serves |
| --- | --- |
| `/` | the set table |
| `/api/cards.json` | the whole dataset |
| `/api/card/<slug>.json` | one card: series, stats, versions (what the modal fetches) |
