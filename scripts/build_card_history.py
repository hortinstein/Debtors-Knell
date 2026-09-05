#!/usr/bin/env python3
"""
Build the per-card price history that the site's card modal and card pages
chart (webapp/static/card-modal.js, webapp/templates/card.html).

The per-deck sidecars scripts/build_price_history.py writes are totals for a
whole decklist; this is the same idea one level down -- one tix and one USD
series per individual card played anywhere in the archive, so clicking a card
name shows what that card has done over the years the archive has been
tracking prices.

Sources are exactly the ones build_price_history.py uses (and this script
reuses its loaders and its three-level name matching):

  MTGO (tix):  prices/goatbots_yearly_archive/<year>/<date>.txt.gz plus
               prices/daily/<fetch-date>/goatbots/price-history.zip, joined to
               card names via the most recent goatbots/card-definitions.zip.

  Paper (USD): prices/daily/<fetch-date>/mtgjson/AllPricesToday.json.bz2,
               joined by prices/mtgjson/uuid_to_name.json.gz.

A card name that maps to several printings gets the median price across
matched printings per day -- the same choice the deck sidecars and the movers
dataset make, avoiding both the bulk-reprint floor and single-printing spikes.

Output: prices/card_history.json.gz, a generated (gitignored) file rebuilt on
webapp startup when it's missing and after every daily price fetch. One shared
date axis plus one aligned array of values per card, which is what keeps a
few thousand cards' worth of history to a couple of megabytes:

    {"generated": "2026-09-05",
     "tix_dates": [...], "usd_dates": [...],
     "cards": {"Wild Mongrel": {"tix": [0.04, null, ...], "usd": [...]}}}

The tix axis is thinned: every archived day for the last DAILY_TAIL_DAYS,
one day a week before that. Three-plus years of daily points is far more
resolution than a modal-sized chart can draw, and thinning the axis also
means the scan only has to read the archived days it actually keeps.

Usage:
    python3 scripts/build_card_history.py           # build if missing/stale
    python3 scripts/build_card_history.py --force   # always rebuild
"""
import argparse
import bz2
import datetime
import glob
import gzip
import json
import os
import statistics
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_price_history as bph

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_DIR = os.path.join(REPO_ROOT, "prices")
OUT_PATH = os.path.join(PRICES_DIR, "card_history.json.gz")

# Recent days are kept in full; older ones are sampled one day a week.
DAILY_TAIL_DAYS = 120
WEEKLY_STEP = 7


def log(msg):
    print(msg, flush=True)


def _today():
    return datetime.date.today().isoformat()


def collect_card_names():
    """Every distinct nonbasic card name across every priced decklist in the
    archive, canonical (fuzzy-match target) spelling.

    Unlike the movers dataset -- which ranks only the cards the column
    actually built with -- this covers the quoted reference lists too, so a
    card name anywhere on the site, Black Lotus in a quoted vintage deck
    included, has a history to show when it's clicked."""
    names = {}
    for pf in bph.collect_priced_files():
        for qty, name, is_basic in bph.parse_priced_rows(pf):
            if is_basic or bph.norm_key(name) in bph.BASIC_LANDS:
                continue
            names.setdefault(bph.norm_key(name), name)
    return sorted(names.values())


def choose_axis(dates):
    """Thin a sorted list of price days down to the axis described in the
    module docstring: every day for the last DAILY_TAIL_DAYS, one day a week
    before that (always keeping the oldest day, so the chart still starts
    where the data does)."""
    dates = sorted(dates)
    if len(dates) <= DAILY_TAIL_DAYS:
        return dates
    head, tail = dates[:-DAILY_TAIL_DAYS], dates[-DAILY_TAIL_DAYS:]
    return head[::WEEKLY_STEP] + tail


def tix_day_sources():
    """{price_date: (kind, path[, member])} for every archived GoatBots day.
    Overlapping dates keep the yearly-archive copy, matching
    build_price_history.load_relevant_prices -- same data either way."""
    sources = {}
    for date_str, path in bph.iter_price_days():
        sources[date_str] = ("gz", path, None)
    for date_str, zpath, member in bph.iter_daily_goatbots_days():
        sources.setdefault(date_str, ("zip", zpath, member))
    return sources


def load_tix_day(source):
    kind, path, member = source
    if kind == "gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with zipfile.ZipFile(path) as z:
        with z.open(member) as f:
            return json.load(f)


def median_price(day_prices, ids):
    """Median price across the printings of one card that are priced on this
    day, or None when none of them are."""
    if not ids:
        return None
    values = [day_prices[i] for i in ids if i in day_prices]
    if not values:
        return None
    return round(statistics.median(values), 2)


def build_tix_series(names, resolved_ids):
    """{name: [price-or-None per axis day]} plus the axis itself."""
    sources = tix_day_sources()
    axis = choose_axis(sources.keys())
    log(f"MTGO (tix): {len(sources):,} archived days available, charting {len(axis):,} of them.")

    wanted_ids = set()
    for ids in resolved_ids.values():
        if ids:
            wanted_ids.update(ids)

    series = {name: [] for name in names}
    for date_str in axis:
        day_prices = load_tix_day(sources[date_str])
        day = {cid: p for cid, p in day_prices.items() if cid in wanted_ids}
        for name in names:
            series[name].append(median_price(day, resolved_ids.get(name)))
    return axis, series


def build_usd_series(names, resolved_uuids):
    """Same shape as build_tix_series, off the archived MTGJSON daily
    snapshots. There are only ever a couple of months of those (one per
    daily fetch), so every day is charted."""
    wanted_uuids = set()
    for uuids in resolved_uuids.values():
        if uuids:
            wanted_uuids.update(uuids)
    if not wanted_uuids:
        return [], {name: [] for name in names}

    by_date = {}
    for day_dir in sorted(glob.glob(os.path.join(bph.DAILY_DIR, "*"))):
        path = os.path.join(day_dir, "mtgjson", "AllPricesToday.json.bz2")
        if not os.path.exists(path):
            continue
        with bz2.open(path, "rt", encoding="utf-8") as f:
            snapshot = json.load(f)
        date_str = (snapshot.get("meta") or {}).get("date") or os.path.basename(day_dir)
        data = snapshot.get("data") or {}
        day = {}
        for uuid in wanted_uuids:
            entry = data.get(uuid)
            if not entry:
                continue
            price = bph._usd_price_for_uuid(entry)
            if price is not None:
                day[uuid] = price
        if day:
            by_date[date_str] = day

    axis = choose_axis(by_date.keys())
    log(f"Paper (USD): {len(by_date):,} archived days available, charting {len(axis):,} of them.")
    series = {}
    for name in names:
        uuids = resolved_uuids.get(name)
        series[name] = [median_price(by_date[d], uuids) for d in axis]
    return axis, series


def build_card_histories(force=False, quiet=False):
    """Write prices/card_history.json.gz. Returns the number of cards in it,
    or 0 when an up-to-date file was left alone."""
    _log = log if not quiet else (lambda msg: None)

    if os.path.exists(OUT_PATH) and not force:
        _log("prices/card_history.json.gz already exists (use --force to rebuild).")
        return 0

    names = collect_card_names()
    _log(f"{len(names):,} distinct nonbasic cards across the archive's decklists.")

    definitions = bph.load_latest_card_definitions()
    name_index = bph.build_name_index(definitions)
    uuid_index = bph.load_uuid_name_index()

    resolved_ids = {n: bph.resolve_ids_for_name(n, name_index) for n in names}
    resolved_uuids = (
        {n: bph.resolve_ids_for_name(n, uuid_index) for n in names} if uuid_index else {}
    )
    unmatched = sorted(n for n in names if not resolved_ids.get(n) and not resolved_uuids.get(n))
    if unmatched:
        _log(f"{len(unmatched)} card names matched neither price source (they get an empty chart).")

    tix_dates, tix_series = build_tix_series(names, resolved_ids)
    usd_dates, usd_series = build_usd_series(names, resolved_uuids)

    cards = {}
    for name in names:
        tix = tix_series.get(name) or []
        usd = usd_series.get(name) or []
        # A card no price source ever matched would otherwise cost a few
        # hundred bytes of nulls apiece; store nothing and let the page say
        # it has no price history.
        cards[name] = {
            "tix": tix if any(v is not None for v in tix) else [],
            "usd": usd if any(v is not None for v in usd) else [],
        }

    out = {
        "generated": _today(),
        "tix_dates": tix_dates,
        "usd_dates": usd_dates,
        "cards": cards,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp_path = OUT_PATH + ".tmp"
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp_path, OUT_PATH)
    size_mb = os.path.getsize(OUT_PATH) / (1024 * 1024)
    _log(f"Wrote {OUT_PATH} ({len(cards):,} cards, {size_mb:.1f} MB gzipped).")
    return len(cards)


def load_card_histories():
    """Read the generated file back, or None when it hasn't been built."""
    if not os.path.exists(OUT_PATH):
        return None
    with gzip.open(OUT_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if prices/card_history.json.gz exists")
    args = ap.parse_args()
    build_card_histories(force=args.force)


if __name__ == "__main__":
    main()
