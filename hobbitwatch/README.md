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
* **Rating** and **Pick order** columns: Draftsim's 0–5 limited rating and
  Untapped.gg's sealed pick-order tier (A+ down to F), scraped by
  `fetch_pick_order.py` — see below.
* **My pool**: paste a sealed pool or draft export (MTGO/Arena format, one
  card per line) into the panel above the table. It's matched client-side
  against the cards already on the page — no server round trip — and narrows
  the table to just what you have, sorted best pick order first, so you can
  see what to keep.

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
| `pickorder/hob_pick_order.json.gz` (checked in, from `fetch_pick_order.py`) | Draftsim's limited rating and Untapped.gg's sealed pick-order tier, matched by card name |

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

## Keeping the price archive in sync with main

This app lives on a feature branch, but `../prices/` is archived daily on
`main` (`.github/workflows/fetch-prices.yml`). Pulling that archive in is a
plain `git merge origin/main` with one recurring wrinkle: `main` rebuilds
`prices/mtgjson/uuid_to_name.json.gz` on its own (much rarer) monthly
schedule using whatever version of `scripts/build_mtgjson_uuid_map.py` is on
`main` at the time, and this branch has carried a newer version of that
script since it added per-printing pricing (a `printings` section the older
script doesn't write) -- so a plain merge can silently overwrite the richer
map with a `printings`-less one from `main` and quietly fall physical prices
back to per-name medians.

```bash
python3 hobbitwatch/update_prices.py
```

Fetches `origin/main`, merges it into the current branch, and — if that's the
*only* thing that conflicts — resolves `uuid_to_name.json.gz` by keeping
whichever side's copy has a `printings` section (falling back to the newer
`generated` date if both or neither do), commits the merge, and rebuilds
`data/hobbit_cards.json`. Any other conflict aborts the merge and leaves the
tree untouched rather than guessing. Requires a clean working tree and a
branch other than `main`.

## Refreshing the network-sourced data

Neither Scryfall nor MTGJSON is reachable from a sandboxed dev container, so
all these fetches run in CI: **`.github/workflows/hobbit-data.yml`** fetches
the Scryfall snapshot, the pick-order rankings, and forces a refresh of
`prices/mtgjson/uuid_to_name.json.gz` (the daily price workflow only refreshes
that map monthly, which would leave a new set unpriced in paper for weeks),
then commits all of it back to the branch. It runs on push to this branch,
weekly on Sundays, and on manual dispatch.

Where the sites *are* reachable, the fetch is just:

```bash
python3 hobbitwatch/fetch_scryfall.py     # → hobbitwatch/scryfall/hob_prints.json.gz
python3 hobbitwatch/fetch_pick_order.py   # → hobbitwatch/pickorder/hob_pick_order.json.gz
python3 scripts/build_mtgjson_uuid_map.py --force
python3 hobbitwatch/build_dataset.py --force
```

`fetch_pick_order.py` scrapes two pages rather than calling a documented API
(neither site publishes one for this data): Draftsim's ratings table is a
tab-separated block inlined in its React bundle's JS, found by locating the
bundle's own `"data/HOB.txt" -> <variable>` mapping rather than a hardcoded
variable name (the minifier renames it every build); Untapped.gg's tier grid
is plain server-rendered HTML, no bundle-diving needed. Both are third-party
internals outside this repo's control and can drift — the script raises a
specific error per parse step instead of silently writing a partial ranking,
and a source that fails keeps its last successful snapshot rather than
disappearing from the page.

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
* **Pick-order rankings only cover `HOB`** — Draftsim and Untapped.gg rate the
  set you draft or open in sealed, not `HOC`'s Commander-deck reprints, so
  those cards show a dash in the Rating/Pick order columns. Within `HOB`,
  Untapped.gg doesn't rate the basic lands either (188 of 193 cards ranked);
  Draftsim rates all 193.
## What this release actually contains

Scryfall knows exactly three sets for The Hobbit, and the snapshot records all
of them with an `included` flag so this isn't a matter of trust:

| Set | Name | Type | Printings | In the page |
| --- | --- | --- | --- | --- |
| `HOB` | The Hobbit | expansion | 321 | ✅ 193 cards |
| `HOC` | The Hobbit Eternal | eternal | 158 | ✅ 117 cards |
| `THOB` | The Hobbit Tokens | token | 15 | ❌ skipped (`--include-tokens`) |

There is **no separate art-series or book-cover set** for this release. The
special treatments all live inside those two sets as extra printings, and every
one of them is in the dataset — as its own row where it's a distinct card, or
in the versions grid with art and price where it's another printing of one:

| Treatment | Printings |
| --- | --- |
| inverted | 143 |
| showcase | 131 |
| surge foil | 120 |
| extended art | 73 |
| bundle / headliner / box topper | 2 / 1 / 2 |

Treatments are labelled in the versions grid, so a variant reads as "box
topper" or "extended art" rather than being an unexplained second row at ten
times the price.

Also checked and correctly excluded: the `SPG` Special Guests batch that sits
near `HOB` in GoatBots' id range — the price archive shows those 20 cards a
week before `HOB` appeared, so they belong to an earlier release.

## Routes

| Route | What it serves |
| --- | --- |
| `/` | the set table |
| `/api/cards.json` | the whole dataset |
| `/api/card/<slug>.json` | one card: series, stats, versions (what the modal fetches) |
