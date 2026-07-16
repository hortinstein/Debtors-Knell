# MTG Price History Data Sources — Research Findings

**Purpose:** Identify the most complete source(s) of *historical* (not just spot) pricing
data for Magic: the Gathering cards, to eventually power a price-history chart for each
"Building on a Budget" deck (317 decks, 2003-2009, decklists in `archive/<date_name>/decklist*.txt`).

**Scope:** Research only. No code was written or decklists modified.

**Headline finding, read this first:** No source — free or paid — has genuine day-by-day
historical pricing data for paper Magic cards from 2003-2009. Real-time price tracking as an
industry didn't really start until ~2010-2013 (MTGGoldfish's own FAQ states their pre-2013
numbers are backfilled from a defunct MTGO bot, "supernovabots"; MTGJSON, GoatBots' archives,
and TCGplayer's own historical API all effectively start in the 2013-2019 range). So a "price
history chart back to the deck's publish date" is not achievable for the early decks in this
archive; the practical ceiling is a chart that starts around 2013-2019 (depending on source)
and runs to present, with the pre-history period necessarily unillustrated or represented by a
single current/backfilled estimate.

---

## 1. Physical / paper card price sources

### Scryfall (current metadata source for this project)
- **Data:** Current-day market prices only (`usd`, `usd_foil`, `usd_etched`, `eur`, `tix`)
  attached to each card object, sourced from TCGplayer (USD), Cardmarket (EUR), and
  Cardhoarder (tix). No date dimension, no series.
- **History depth: NONE.** Scryfall explicitly does not retain price history. Prices sync from
  affiliates roughly every 24 hours and the docs warn bulk-data prices should be "considered
  dangerously stale after 24 hours" — i.e., it's a rolling spot price, not a time series.
- **Access:** Free REST API + daily "bulk data" JSON dumps (`default_cards`, `all_cards`,
  etc.), no auth required, generous rate limits (~10 req/s), attribution requested.
- **Format:** JSON.
- **Verdict:** Great for what this project already uses it for (card metadata, set/printing
  IDs, images), but **contributes nothing to a historical price chart** unless this project
  itself starts archiving Scryfall's daily snapshot going forward (a "start the clock now"
  option, not a backfill option).

### MTGJSON — `AllPrices` / `AllPricesToday`
- **Data:** Aggregates paper prices from **TCGplayer, Cardmarket, CardKingdom, Cardsphere,
  and Manapool**, plus MTGO prices from **Cardhoarder**, keyed by MTGJSON card `uuid`
  (foil/nonfoil, retail/buylist split where available).
- **History depth: 90 days rolling.** `AllPrices` explicitly retains only the trailing 90
  days; `AllPricesToday` is a same-day-only snapshot. MTGJSON does **not** publish a long-run
  historical archive — you'd have to download and archive `AllPrices` yourself, daily, from
  now on, to accumulate history.
- **Access:** Free, no auth, static file downloads at `mtgjson.com/api/v5/` (JSON, with
  .bz2/.gz/.xz/.zip compressed variants). No rate limit issues since it's static files, not a
  live API.
- **Format:** JSON.
- **Verdict:** Best free *aggregator of multiple paper sources in one schema*, but only useful
  as a forward-looking daily-archive project, not a backfill source.

### TCGplayer (official API / TCG API)
- **Data:** Market/low/mid/high prices per SKU (condition, printing) across the full paper
  catalog.
- **History depth:** The official TCGplayer API's price-history endpoint only goes back to
  **March 2025** (weekly points, cards ≥$1, requires Pro/Business paid plan). TCGplayer's
  historical-data help article ("Understanding Historical Data in MTG Finance") itself points
  people to third parties for anything older.
- **Third-party TCGplayer mirrors:**
  - **TCGCSV** (tcgcsv.com) — free, community-run cache of TCGplayer's public pricing API,
    daily 7z-compressed CSV/JSON archives, but only from **Feb 8, 2024 onward**. No auth;
    scraping allowed with an identifiable User-Agent. Some categories excluded; "market high"
    values noted as unreliable due to price-parking.
  - **TCGAPIs / TCGAPI.dev / JustTCG** — paid third-party services claiming multi-year sales
    history and cross-marketplace data (Cardmarket, Cardtrader, Cardsphere, Card Kingdom) on
    higher-tier plans; pricing/depth not independently verified beyond marketing pages.
- **Verdict:** Not useful for this project's timeframe (2003-2009) and the free tiers don't
  reach back far enough (2024+) to be meaningfully "historical" for anything except very
  recent trend lines.

### MTGGoldfish
- **Data:** Per-card and per-set price-history charts and CSV export; sources disclosed in
  their FAQ as a blend of **TCGplayer Mid + Card Kingdom + eBay** for paper, **Cardhoarder**
  for MTGO.
- **History depth:** Effectively the deepest *browsable* history of any consumer site — data
  before **Jan 19, 2013** is explicitly backfilled from a defunct MTGO bot ("supernovabots"),
  implying real day-by-day tracking starts around 2013 for MTGO and a similar era for paper.
  Full per-card history download is a **Premium feature ($6/mo)**; no public bulk API.
- **Access:** No official API; premium CSV download is manual/per-card, not a bulk historical
  dump. Scraping is against typical ToS for a paid feature like this.
- **Verdict:** Good for spot-checking / validating other sources' numbers, and possibly the
  single longest continuous paper-price series available anywhere, but it's a paid,
  per-card-manual-export product, not something to build an automated pipeline on top of.

### MTGStocks / MTGPrice.com
- **MTGStocks:** No official public API; only unofficial scrapers exist (e.g.
  `jmizzoni/mtg-price-fetcher` on GitHub) built by reverse-engineering their internal
  endpoints — fragile, unsupported, likely against ToS for bulk/redistribution use.
- **MTGPrice.com:** Once had a documented price API; the page now states **"all API access to
  MTGPrice is closed. We may re-visit this decision in the future."** Dead end today.
- **Verdict:** Not viable as a primary or even secondary source right now.

### Card Kingdom
- **Data:** Public pricelist endpoints (`api.cardkingdom.com/api/v2/pricelist` for singles,
  `/api/sealed_pricelist` for sealed) return retail + buylist prices for the current catalog.
- **History depth: NONE.** These are current-snapshot pricelists, not a time series. (They're
  one of MTGJSON's aggregated paper sources, so their numbers do flow into MTGJSON's 90-day
  rolling window.)
- **Verdict:** Useful only as one more "start archiving today" input, same caveat as Scryfall.

---

## 2. MTGO (digital) price sources

### GoatBots — https://www.goatbots.com/download-prices (the lead the user flagged)
Confirmed directly from the live page (fetched via Wayback Machine snapshots, since the live
site 403s non-browser requests — see Access notes below):

> "Here you can download our daily average sell prices for your own project. We use the exact
> same prices for all graphs and trending pages. We publish these prices here once a day at
> 5:30 AM Central European Time... Please include a link to our homepage if you decide to use
> our data on your website."

- **Files offered (all JSON, in a `.zip`):**
  - `card-definitions.zip` — maps GoatBots' internal Magic Online card ID → card name, set,
    rarity, foil flag. This is the join key for everything else.
  - `price-history.zip` — **current day's** average sell price per card ID (despite the name,
    this single file is effectively a daily snapshot, not a multi-day series — confirmed via a
    third-party Go client, `CramBL/mtgo-collection-manager`, which documents it as "unique card
    ID → associated tix price").
  - `price-history-<YEAR>.zip` — **this is the actual historical archive**, one zip per
    calendar year, presumably containing the daily time series for that year (exact intra-file
    schema wasn't independently verified since the live download 403'd our fetches — see
    below).
- **History depth:** GoatBots keeps a **rolling window of yearly archives**, not the full
  history since GoatBots started operating (2012). Wayback Machine snapshots show:
  - Snapshot from **2021-06-19**: years offered = 2019, 2020, 2021 (i.e., only 2019+ was ever
    available even then).
  - Snapshot from **2024-07-17**: years offered = 2019, 2020, 2021, 2022, 2023, 2024.
  - Snapshot from **2025-11-03**: years offered = **2022, 2023, 2024, 2025 only** — 2019-2021
    have since been dropped from the page.
  - **Implication: the yearly archive is a rolling ~4-year window, and GoatBots does not
    guarantee old years stay downloadable.** If GoatBots is used, the years 2019-2021 (URLs
    like `goatbots.com/download/price-history-2019.zip`) should be fetched and archived
    **now**, before they age out further, or that slice of history may become unrecoverable
    from GoatBots directly. Individual card price-history *charts* on the site
    (`goatbots.com/card/<slug>`, e.g. `/card/track-down`) may display a longer visual history
    than the bulk downloads offer, but that's browse-only, not bulk-downloadable.
- **Access method:** Direct HTTPS zip download, no auth, no documented rate limit — but the
  live site returned **HTTP 403 to both `curl` and the WebFetch tool** during this research
  (looks like bot/Cloudflare-style protection keyed on browser-like requests/TLS
  fingerprinting, not on User-Agent string alone, since setting a realistic UA still 403'd).
  A real browser or a scraping setup with proper session/cookie handling will likely be needed
  for automated downloads; this should be validated hands-on before building a pipeline.
- **ToS / redistribution:** No formal ToS found; the only stated condition is the
  attribution request quoted above ("include a link to our homepage"). No prohibition on bulk
  download or reuse was found.
- **Format:** JSON inside zip archives.
- **Verdict: best available MTGO history source**, materially deeper (2019+) than MTGJSON's
  90-day window, free, no auth — but the rolling-archive risk means **acting soon (downloading
  and locally archiving all available `price-history-<YEAR>.zip` files) is important**, and the
  exact schema of the yearly files should be verified with a real download before relying on it.

### Cardhoarder
- **Data:** Cardhoarder is MTGO's other major secondary-market vendor (operating since 2005)
  and is the price source MTGJSON uses for its `cardhoarder` price node.
- **History depth:** Via MTGJSON, subject to the same 90-day rolling window described above.
  Cardhoarder's own site (cardhoarder.com/faq, help.cardhoarder.com) was not reachable via
  automated fetch during this research (403s) — no evidence found of Cardhoarder offering its
  own bulk historical-price download or public API independent of MTGJSON.
- **Verdict:** Useful as a second MTGO price signal (cross-validation) via MTGJSON's rolling
  window, but not a standalone historical-depth source.

### MTGGoldfish (MTGO index/price pages)
- Same product as the paper section above; MTGGoldfish explicitly publishes side-by-side
  "MTG / MTGO Price History" charts per set/index, sourced from Cardhoarder, with the same
  Jan-19-2013 backfill boundary noted in their FAQ. Same caveats: no free bulk API, Premium
  needed for full per-card CSV export.

### Other trackers checked
- **MTGO Traders / DojoTrade** (`dojotradebots.com/mtgoPrices`) — a bot-chain price page,
  same genre as GoatBots but no evidence of a structured bulk-download equivalent.
- No other MTGO-specific historical price API of note was found beyond GoatBots, Cardhoarder
  (via MTGJSON), and MTGGoldfish.

---

## 3. Comparison table

| Source | Paper/MTGO | Granularity | History depth | Access | Format | Cost / ToS |
|---|---|---|---|---|---|---|
| Scryfall | Both (spot) | Card/printing | **None** (24h-stale spot only) | Free API/bulk | JSON | Free, attribution appreciated |
| MTGJSON `AllPrices` | Both | Card uuid, foil/nonfoil, retail/buylist | **90 days rolling** | Free static files | JSON | Free, open |
| TCGplayer official API | Paper | SKU/condition | From **Mar 2025** (weekly) | Paid (Pro/Business) | JSON | Paid, ToS-gated |
| TCGCSV | Paper (TCGplayer mirror) | SKU | From **Feb 2024** | Free download/scrape | CSV/JSON (7z) | Free, UA identification requested |
| MTGGoldfish | Both | Card, per-set index | ~**2013+** (best public depth), pre-2013 backfilled estimate | Manual/Premium CSV, no API | CSV (manual export) | $6/mo Premium, no bulk API |
| MTGStocks | Paper | Card | Unknown (site-only) | Unofficial scraping only | N/A | No public API |
| MTGPrice.com | Paper | Card | Unknown | **API access closed** | N/A | Dead end |
| Card Kingdom pricelist | Paper | SKU | **None** (current only) | Free API | JSON | Free |
| **GoatBots** | **MTGO** | Card ID (per printing/foil) | **2019+** via yearly archives (rolling ~4yr window on the live page; earlier years may still be fetchable via Wayback Machine while cached) | Free zip download (site 403s naive `curl`/automated fetch — needs browser-like session) | JSON | Free, attribution requested, no explicit redistribution ban |
| Cardhoarder | MTGO | Card | Same as MTGJSON (90 days) via aggregation; no independent bulk API found | Via MTGJSON only | JSON | Free (via MTGJSON) |

---

## 4. Recommendation

**For MTGO price history:** Use **GoatBots' yearly `price-history-<YEAR>.zip` archives** as
the primary source. It's free, requires no auth, has no found redistribution prohibition
(just an attribution ask), and — critically — is the only MTGO source found with more than a
90-day depth (back to 2019). Because GoatBots appears to prune the oldest year off its public
list every so often (2019-2021 had already disappeared from the live page by Nov 2025), the
practical next step (for whoever implements the charting feature) should be to **download and
locally archive every currently-available `price-history-<YEAR>.zip` and `card-definitions.zip`
soon**, rather than treating GoatBots as an on-demand API to hit whenever needed. Since the live
site blocked both `curl` and the WebFetch tool with HTTP 403 during this research, plan on
using a real browser / headless-browser session (or a scraping library that handles
TLS/cookie fingerprinting) rather than a bare HTTP client, and validate the actual internal
schema of one yearly zip by hand before building a parser. Use MTGJSON's Cardhoarder-sourced
`AllPrices` as a secondary cross-check for the last 90 days only.

**For physical/paper price history:** There is no free source with real depth before ~2013,
so pick between two honest options:
1. **MTGJSON `AllPrices`, re-fetched and archived daily going forward** — free, multi-source
   (TCGplayer/Cardmarket/CardKingdom/Cardsphere), consistent schema, but you only start
   building real history from whenever you start pulling it (i.e., "now," not backfilled).
2. **MTGGoldfish Premium ($6/mo)** for a one-time manual bulk export per card/set, if genuine
   2013-present history is wanted immediately rather than waiting to accumulate it — but this
   is a manual, per-card CSV export product, not an automatable bulk API, and scraping it
   would violate the terms of a paid feature.

**Practical approach sketch:**
- Cross-reference each deck's cards (parsed from `archive/<date_name>/decklist*.txt`, which
  are plain MTGO-format `<qty> <name>` lines) against **Scryfall** (already used for metadata)
  to resolve canonical card name → set/printing/UUID, since none of the price sources key on
  raw card name alone.
- Use the resolved **MTGJSON `uuid`** as the join key into MTGJSON's `AllPrices` for paper, and
  a resolved **GoatBots card ID** (via `card-definitions.zip`, joined by name+set) for MTGO.
- Start a lightweight daily cron/archive job now (storing MTGJSON `AllPrices` and GoatBots'
  daily/yearly files verbatim) so that from this point forward the project has genuine
  day-by-day history it fully owns, rather than depending on any vendor's retention window.
- Be explicit in the eventual chart UI that pre-2013 (paper) / pre-2019 (MTGO) history isn't
  real market data and either start the chart at the earliest available data point or clearly
  flag any backfilled/estimated segment.
