#!/usr/bin/env python3
"""
Build the weekly "Price Movers" dataset: the biggest paper (USD) and MTGO
(tix) price changes over the last week of archived snapshots, among the
cards actually played in the archive's decklists (the same card pool the
rest of the site tracks -- every nonbasic card in any
archive/*/decklist*_priced.md).

Sources (both already archived daily by scripts/fetch_prices.py):

  MTGO (tix):  prices/daily/<fetch-date>/goatbots/price-history.zip (the
               inner filename carries the date the prices are actually for)
               plus prices/goatbots_yearly_archive/<year>/<date>.txt.gz,
               joined to card names via the most recent
               goatbots/card-definitions.zip. Non-card products (rarity
               "Booster") are excluded.

  Paper (USD): prices/daily/<fetch-date>/mtgjson/AllPricesToday.json.bz2
               (meta.date is the day the prices are for), joined by
               prices/mtgjson/uuid_to_name.json.gz.

As elsewhere in this repo (see build_price_history.py), a card name that maps
to several printings/ids gets the median price across matched printings per
day, avoiding both the bulk-reprint floor and single-printing spikes.

For each market the script takes the last WINDOW_DAYS days of available
snapshots, computes each card's change from the first to the last day of that
window, and keeps the top TOP_N movers (gainers and losers mixed, ranked by
magnitude) under four rankings:

  paper_pct / paper_abs   biggest % / biggest absolute USD change
  tix_pct   / tix_abs     biggest % / biggest absolute tix change

Percentage rankings require a minimum starting price (MIN_*_FOR_PCT) so
bulk-bin noise (a $0.05 card "tripling") doesn't drown out real moves. Every
selected card carries its full daily series for BOTH markets (where the name
matches), so the page can chart paper and digital side by side.

Output: prices/movers/<end-date>.json, one archived file per price day --
re-running with the same archived data rewrites the same file (idempotent),
and each daily fetch adds a new dated file, forming the browseable archive.

Usage:
    python3 scripts/build_movers.py            # build for the latest data
    python3 scripts/build_movers.py --all      # (re)build every possible day
"""
import argparse
import bz2
import datetime
import glob
import gzip
import json
import os
import re
import statistics
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_price_history as bph

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_DIR = os.path.join(REPO_ROOT, "prices")
DAILY_DIR = os.path.join(PRICES_DIR, "daily")
YEARLY_DIR = os.path.join(PRICES_DIR, "goatbots_yearly_archive")
UUID_MAP_PATH = os.path.join(PRICES_DIR, "mtgjson", "uuid_to_name.json.gz")
MOVERS_DIR = os.path.join(PRICES_DIR, "movers")

WINDOW_DAYS = 7
TOP_N = 20

# Floor on the window-start price for the *percentage* rankings; without it
# the list is all bulk commons ticking from $0.03 to $0.10. Absolute-change
# rankings need no floor (a big $ move implies a real price).
MIN_USD_FOR_PCT = 1.00
MIN_TIX_FOR_PCT = 0.50

# Mirrors build_price_history.py's provider preference for one representative
# USD retail price per printing.
PAPER_PROVIDER_PREFERENCE = ["tcgplayer", "cardkingdom", "manapool", "cardsphere"]

DAILY_GOATBOTS_INNER_RE = re.compile(r"price-history-(\d{4}-\d{2}-\d{2})\.txt$")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# The deck card pool: every nonbasic card in any priced decklist
# ---------------------------------------------------------------------------

def deck_pool_name_keys():
    """Three normalized-name sets (exact / no-apostrophe / accent-and-
    punctuation-stripped, mirroring build_price_history.py's matching
    levels) covering every nonbasic card played anywhere in the archive.
    Movers are restricted to this pool -- the point of the page is what the
    archive's decks are doing, not the whole Magic market."""
    exact, noapos, stripped = set(), set(), set()
    count = 0
    for pf in bph.collect_priced_files():
        for qty, name, is_basic in bph.parse_priced_rows(pf):
            if is_basic or bph.norm_key(name) in bph.BASIC_LANDS:
                continue
            exact.add(bph.norm_key(name))
            noapos.add(bph.norm_key_noapos(name))
            stripped.add(bph.norm_key_noaccent_nopunct(name))
            count += 1
    log(f"Deck card pool: {len(exact):,} distinct nonbasic names "
        f"across {count:,} decklist rows.")
    return exact, noapos, stripped


def in_deck_pool(name, pool_keys):
    """Whether a price-source card name matches the deck pool at any
    normalization level. Split/double-faced names ("A // B") also match on
    either face, since decklists cite the front face."""
    exact, noapos, stripped = pool_keys
    candidates = [name]
    if " // " in name:
        candidates.extend(name.split(" // "))
    for c in candidates:
        if (bph.norm_key(c) in exact
                or bph.norm_key_noapos(c) in noapos
                or bph.norm_key_noaccent_nopunct(c) in stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# Available price days per market
# ---------------------------------------------------------------------------

def goatbots_day_sources():
    """{price_date: loader} across the yearly bulk archives and the daily
    fetches (daily wins on overlap only if the yearly copy is absent -- same
    data either way)."""
    sources = {}
    for path in sorted(glob.glob(os.path.join(YEARLY_DIR, "*", "*.txt.gz"))):
        date_str = os.path.basename(path)[: -len(".txt.gz")]
        sources[date_str] = ("yearly", path, None)
    for day_dir in sorted(glob.glob(os.path.join(DAILY_DIR, "*"))):
        zpath = os.path.join(day_dir, "goatbots", "price-history.zip")
        if not os.path.exists(zpath):
            continue
        try:
            with zipfile.ZipFile(zpath) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            continue
        for member in names:
            m = DAILY_GOATBOTS_INNER_RE.search(member)
            if m and m.group(1) not in sources:
                sources[m.group(1)] = ("daily", zpath, member)
    return sources


def load_goatbots_day(source):
    kind, path, member = source
    if kind == "yearly":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with zipfile.ZipFile(path) as z:
        with z.open(member) as f:
            return json.load(f)


def mtgjson_day_sources():
    """{price_date: snapshot_path}; meta.date is the day the prices are for
    (one day behind the fetch date). A later fetch carrying the same price
    date wins."""
    sources = {}
    for day_dir in sorted(glob.glob(os.path.join(DAILY_DIR, "*"))):
        path = os.path.join(day_dir, "mtgjson", "AllPricesToday.json.bz2")
        if os.path.exists(path):
            # Peek at meta.date lazily: assume fetch-date - 1 first, correct
            # after load. To stay simple (and because we only load a handful
            # of days), record by fetch dir and resolve the real date on load.
            sources[os.path.basename(day_dir)] = path
    return sources


def load_mtgjson_day(path):
    with bz2.open(path, "rt", encoding="utf-8") as f:
        snapshot = json.load(f)
    date_str = (snapshot.get("meta") or {}).get("date")
    return date_str, snapshot.get("data", {})


# ---------------------------------------------------------------------------
# Per-day name -> median price maps
# ---------------------------------------------------------------------------

def load_goatbots_names():
    """id -> name for real cards only (excludes rarity 'Booster' products
    like prize boosters), from the most recent card-definitions.zip."""
    day_dirs = sorted(
        d for d in os.listdir(DAILY_DIR)
        if os.path.isdir(os.path.join(DAILY_DIR, d))
    )
    for day in reversed(day_dirs):
        zpath = os.path.join(DAILY_DIR, day, "goatbots", "card-definitions.zip")
        if not os.path.exists(zpath):
            continue
        with zipfile.ZipFile(zpath) as z:
            with z.open(z.namelist()[0]) as f:
                definitions = json.load(f)
        id_to_name = {
            cid: meta["name"]
            for cid, meta in definitions.items()
            if meta.get("name") and meta.get("rarity") != "Booster"
        }
        log(f"Loaded {len(id_to_name):,} GoatBots card definitions from {day}.")
        return id_to_name
    raise SystemExit("No prices/daily/*/goatbots/card-definitions.zip found.")


def median_by_name(prices_by_id, id_to_name):
    by_name = {}
    for cid, price in prices_by_id.items():
        name = id_to_name.get(cid)
        if name is not None and price is not None:
            by_name.setdefault(name, []).append(price)
    return {name: round(statistics.median(vals), 2) for name, vals in by_name.items()}


def usd_price_for_uuid(entry):
    """One representative USD retail price from an AllPricesToday per-uuid
    entry (same provider/finish preference as build_price_history.py)."""
    paper = entry.get("paper") or {}
    for provider in PAPER_PROVIDER_PREFERENCE:
        p = paper.get(provider)
        if not p or p.get("currency") != "USD":
            continue
        retail = p.get("retail") or {}
        for finish in ("normal", "foil"):
            dated = retail.get(finish) or {}
            if dated:
                return dated[max(dated)]
    return None


def load_uuid_to_name():
    if not os.path.exists(UUID_MAP_PATH):
        raise SystemExit(
            "prices/mtgjson/uuid_to_name.json.gz missing -- run "
            "scripts/build_mtgjson_uuid_map.py first."
        )
    with gzip.open(UUID_MAP_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)["names"]


# ---------------------------------------------------------------------------
# Movers computation
# ---------------------------------------------------------------------------

def window_dates(all_dates, end_date):
    """The available dates within [end_date - (WINDOW_DAYS-1), end_date]."""
    end = datetime.date.fromisoformat(end_date)
    start = end - datetime.timedelta(days=WINDOW_DAYS - 1)
    return [d for d in sorted(all_dates) if start.isoformat() <= d <= end_date]


def compute_movers(day_maps, min_start_for_pct):
    """day_maps: {date: {name: price}} over the window, chronological.
    Returns (per-name summary dict, top-by-pct list, top-by-abs list)."""
    dates = sorted(day_maps.keys())
    first, last = dates[0], dates[-1]
    summaries = {}
    for name, end_price in day_maps[last].items():
        start_price = day_maps[first].get(name)
        if start_price is None or start_price <= 0:
            continue
        change = round(end_price - start_price, 2)
        if change == 0:
            continue
        pct = round(change / start_price * 100.0, 1)
        summaries[name] = {
            "start": start_price,
            "end": end_price,
            "change": change,
            "pct": pct,
        }
    by_abs = sorted(summaries, key=lambda n: -abs(summaries[n]["change"]))[:TOP_N]
    eligible = [n for n in summaries if summaries[n]["start"] >= min_start_for_pct]
    by_pct = sorted(eligible, key=lambda n: -abs(summaries[n]["pct"]))[:TOP_N]
    return summaries, by_pct, by_abs


def series_for(name, day_maps):
    dates = sorted(day_maps.keys())
    return [[d, day_maps[d][name]] for d in dates if name in day_maps[d]]


def build_movers_for_end_date(tix_sources, id_to_name, uuid_to_name,
                              mtgjson_by_fetch, end_date=None):
    """Build one prices/movers/<date>.json. end_date=None means "latest
    available in either market". Returns the output path, or None if there
    isn't at least a 2-day window in either market."""
    tix_dates_all = sorted(tix_sources.keys())
    # A fetch on day D carries MTGJSON prices for D-1 (the snapshot's
    # meta.date, which is what actually keys the series after load).
    est_paper_dates = {
        (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat(): d
        for d in mtgjson_by_fetch
    }

    if end_date is None:
        candidates = tix_dates_all + sorted(est_paper_dates)
        if not candidates:
            raise SystemExit("No archived price data found.")
        end_date = max(candidates)

    # --- Paper (USD) day maps: only load snapshots whose estimated price
    # date falls in the window (each is a ~110k-card bz2 -- loading all of
    # them would dominate the runtime), then re-key by the real meta.date.
    paper_maps = {}
    for est_date in window_dates(est_paper_dates, end_date):
        date_str, data = load_mtgjson_day(mtgjson_by_fetch[est_paper_dates[est_date]])
        date_str = date_str or est_date
        by_name = {}
        for uuid, entry in data.items():
            name = uuid_to_name.get(uuid)
            if name is None:
                continue
            price = usd_price_for_uuid(entry)
            if price is not None:
                by_name.setdefault(name, []).append(price)
        paper_maps[date_str] = {
            n: round(statistics.median(v), 2) for n, v in by_name.items()
        }
        log(f"  paper {date_str}: {len(paper_maps[date_str]):,} priced names")
    paper_maps = {d: m for d, m in paper_maps.items()
                  if d in window_dates(paper_maps.keys(), end_date)}

    tix_window = window_dates(tix_dates_all, end_date)

    tix_maps = {}
    for d in tix_window:
        tix_maps[d] = median_by_name(load_goatbots_day(tix_sources[d]), id_to_name)
        log(f"  tix {d}: {len(tix_maps[d]):,} priced names")

    views = {}
    tix_summaries = paper_summaries = {}
    if len(tix_maps) >= 2:
        tix_summaries, tix_pct, tix_abs = compute_movers(tix_maps, MIN_TIX_FOR_PCT)
        views["tix_pct"], views["tix_abs"] = tix_pct, tix_abs
    if len(paper_maps) >= 2:
        paper_summaries, paper_pct, paper_abs = compute_movers(paper_maps, MIN_USD_FOR_PCT)
        views["paper_pct"], views["paper_abs"] = paper_pct, paper_abs
    if not views:
        log(f"Not enough archived days around {end_date} for a movers window.")
        return None

    # Every card selected by any view, with both markets' data where the name
    # matches (so each row can chart paper AND digital).
    cards = {}
    for names in views.values():
        for name in names:
            if name in cards:
                continue
            card = {"name": name, "paper": None, "tix": None}
            if name in paper_summaries or any(name in m for m in paper_maps.values()):
                s = paper_summaries.get(name)
                series = series_for(name, paper_maps)
                if series:
                    card["paper"] = dict(s or {}, series=series)
            if name in tix_summaries or any(name in m for m in tix_maps.values()):
                s = tix_summaries.get(name)
                series = series_for(name, tix_maps)
                if series:
                    card["tix"] = dict(s or {}, series=series)
            cards[name] = card

    out = {
        "generated": datetime.date.today().isoformat(),
        "date": end_date,
        "window_days": WINDOW_DAYS,
        "paper_dates": sorted(paper_maps.keys()),
        "tix_dates": sorted(tix_maps.keys()),
        "min_usd_for_pct": MIN_USD_FOR_PCT,
        "min_tix_for_pct": MIN_TIX_FOR_PCT,
        "views": views,
        "cards": cards,
    }
    os.makedirs(MOVERS_DIR, exist_ok=True)
    out_path = os.path.join(MOVERS_DIR, f"{end_date}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    log(f"Wrote {os.path.relpath(out_path, REPO_ROOT)} "
        f"({len(cards)} cards across {len(views)} views).")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="end date (YYYY-MM-DD) instead of the latest available")
    ap.add_argument("--all", action="store_true",
                    help="rebuild a movers file for every archived price day")
    args = ap.parse_args()

    pool_keys = deck_pool_name_keys()
    id_to_name = load_goatbots_names()
    id_to_name = {cid: n for cid, n in id_to_name.items()
                  if in_deck_pool(n, pool_keys)}
    log(f"  {len(id_to_name):,} GoatBots printings are in the deck pool.")
    uuid_to_name = load_uuid_to_name()
    uuid_to_name = {u: n for u, n in uuid_to_name.items()
                    if in_deck_pool(n, pool_keys)}
    log(f"  {len(uuid_to_name):,} MTGJSON printings are in the deck pool.")
    tix_sources = goatbots_day_sources()
    mtgjson_by_fetch = mtgjson_day_sources()

    if args.all:
        # Every date that has data in either market and at least one earlier
        # day inside its window.
        all_dates = sorted(set(tix_sources) | {
            (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
            for d in mtgjson_by_fetch
        })
        for end_date in all_dates:
            if window_dates(all_dates, end_date)[:-1]:
                log(f"=== {end_date} ===")
                build_movers_for_end_date(
                    tix_sources, id_to_name, uuid_to_name, mtgjson_by_fetch,
                    end_date=end_date,
                )
    else:
        build_movers_for_end_date(
            tix_sources, id_to_name, uuid_to_name, mtgjson_by_fetch,
            end_date=args.date,
        )


if __name__ == "__main__":
    main()
