#!/usr/bin/env python3
"""
Build per-deck historical price series for the "Building on a Budget" archive.

Digital (MTGO tix): genuine multi-year daily history, built from GoatBots'
yearly bulk archives (prices/goatbots_yearly_archive/<year>/<date>.txt.gz,
card id -> tix price) joined against the id -> card name mapping in the most
recent prices/daily/<date>/goatbots/card-definitions.zip. Where a name maps
to several GoatBots ids (different sets/foil versions -- GoatBots and
Scryfall don't share set-code conventions, e.g. Scryfall's "hop" vs
GoatBots' "PC1" for the same Planechase set, so the two can't reliably be
cross-referenced to "the same printing"), the median price across matched
printings for that day is used, to avoid both the near-zero bulk-reprint
floor and any single-printing spike.

Physical (paper USD): this project only started archiving a daily snapshot
(prices/daily/<date>/mtgjson/AllPricesToday.json.bz2) on 2026-07-16, and that
snapshot is keyed by MTGJSON uuid with no locally-available name mapping, so
it isn't joined here. Per research/PRICE_DATA_SOURCES.md, no source has real
day-by-day paper history for these 2003-2009 decks anyway. Instead, the one
genuine data point available -- the deck's current Scryfall-sourced grand
total, already computed in decklist*_priced.md -- is recorded as a single-day
series. Re-running this script on later dates (once paper-price name-mapping
is available) can extend it; the JSON shape already supports more points.

Output: one JSON sidecar per priced decklist, next to it in the same archive
folder, e.g. archive/<folder>/decklist_price_history.json (or
decklist_1_price_history.json, ... for multi-deck articles):

    {
      "generated": "2026-07-16",
      "tix": [["2023-01-01", 12.34], ["2023-01-02", 12.5], ...],
      "usd": [["2026-07-16", 111.34]],
      "unmatched_cards": ["Some Card Name", ...]
    }

Safe to re-run: skips any folder whose sidecar already exists, unless
--force is given.

Usage:
    python3 scripts/build_price_history.py
    python3 scripts/build_price_history.py --only 20090527_Shamans_Trounce_2009
    python3 scripts/build_price_history.py --force
"""
import argparse
import bz2
import glob
import gzip
import json
import os
import re
import statistics
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
PRICES_DIR = os.path.join(REPO_ROOT, "prices")
DAILY_DIR = os.path.join(PRICES_DIR, "daily")
YEARLY_DIR = os.path.join(PRICES_DIR, "goatbots_yearly_archive")

import unicodedata

# Mirrors the normalization helpers in build_markdown_and_prices.py (kept as a
# standalone copy here so this script only needs the standard library -- no
# bs4/requests/rapidfuzz install required just to build price history).
BASIC_LANDS = {"island", "plains", "swamp", "mountain", "forest", "wastes"}


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm_basic(s):
    s = s.strip()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(s):
    return _norm_basic(s).lower()


def norm_key_noapos(s):
    return norm_key(s).replace("'", "").replace(",", "")


def norm_key_noaccent_nopunct(s):
    s = _strip_accents(norm_key(s))
    return re.sub(r"[^a-z0-9]+", "", s)

GRAND_TOTAL_RE = re.compile(r"\*\*Grand total:\s*\$([\d,]+\.\d+)\*\*")
ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*"
    r"([\d.]+|N/A)\s*\|\s*([\d.]+|N/A)\s*\|\s*(.*?)\s*\|\s*$"
)
FUZZY_NOTE_RE = re.compile(r"->\s*(.+)$")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# GoatBots card-definitions (id -> name) -- most recent snapshot available
# ---------------------------------------------------------------------------

def load_latest_card_definitions():
    day_dirs = sorted(
        d for d in os.listdir(DAILY_DIR)
        if os.path.isdir(os.path.join(DAILY_DIR, d))
    )
    for day in reversed(day_dirs):
        zpath = os.path.join(DAILY_DIR, day, "goatbots", "card-definitions.zip")
        if os.path.exists(zpath):
            with zipfile.ZipFile(zpath) as z:
                with z.open(z.namelist()[0]) as f:
                    data = json.load(f)
            log(f"Loaded {len(data):,} GoatBots card definitions from {day}.")
            return data
    raise SystemExit("No prices/daily/*/goatbots/card-definitions.zip found.")


def build_name_index(definitions):
    """Three name -> set-of-ids indexes at increasing normalization strength,
    mirroring CardIndex in build_markdown_and_prices.py (exact / no-apostrophe
    / accent-and-punctuation-stripped)."""
    by_exact, by_noapos, by_stripped = {}, {}, {}
    for card_id, meta in definitions.items():
        name = meta.get("name")
        if not name:
            continue
        by_exact.setdefault(norm_key(name), set()).add(card_id)
        by_noapos.setdefault(norm_key_noapos(name), set()).add(card_id)
        by_stripped.setdefault(norm_key_noaccent_nopunct(name), set()).add(card_id)
    return by_exact, by_noapos, by_stripped


def resolve_ids_for_name(raw_name, name_index):
    by_exact, by_noapos, by_stripped = name_index
    k = norm_key(raw_name)
    if k in by_exact:
        return by_exact[k]
    ka = norm_key_noapos(raw_name)
    if ka in by_noapos:
        return by_noapos[ka]
    ks = norm_key_noaccent_nopunct(raw_name)
    if ks in by_stripped:
        return by_stripped[ks]
    return None


# ---------------------------------------------------------------------------
# Daily tix price files (id -> price), across all yearly archives
# ---------------------------------------------------------------------------

def iter_price_days():
    """Yields (date_str, path) for every archived GoatBots daily price file,
    sorted chronologically."""
    for year_dir in sorted(glob.glob(os.path.join(YEARLY_DIR, "*"))):
        for path in sorted(glob.glob(os.path.join(year_dir, "*.txt.gz"))):
            date_str = os.path.basename(path)[: -len(".txt.gz")]
            yield date_str, path


def load_relevant_prices(relevant_ids):
    """Returns {date_str: {id: price}} restricted to relevant_ids, across
    every archived day, without ever holding a full day's ~150k-card price
    map in memory longer than it takes to filter it down."""
    by_date = {}
    for date_str, path in iter_price_days():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            day_prices = json.load(f)
        filtered = {cid: price for cid, price in day_prices.items() if cid in relevant_ids}
        if filtered:
            by_date[date_str] = filtered
    return by_date


# ---------------------------------------------------------------------------
# Priced decklist parsing (reuse the already-resolved canonical card names)
# ---------------------------------------------------------------------------

def parse_priced_rows(priced_md_path):
    """Returns list of (qty, canonical_name, is_basic_land)."""
    rows = []
    with open(priced_md_path, encoding="utf-8") as f:
        for line in f:
            m = ROW_RE.match(line.rstrip("\n"))
            if not m:
                continue
            qty = int(m.group(1))
            name_cell = m.group(2)
            is_basic = "basic land" in name_cell.lower()
            # strip the trailing "<sub>(...)</sub>" annotation, but use its
            # "-> Canonical Name" fuzzy-match target if present
            sub_m = re.search(r"<sub>\((.*)\)</sub>\s*$", name_cell)
            base_name = re.sub(r"\s*<sub>.*</sub>\s*$", "", name_cell).strip()
            canonical = base_name
            if sub_m:
                fm = FUZZY_NOTE_RE.search(sub_m.group(1))
                if fm:
                    canonical = fm.group(1).strip()
            rows.append((qty, canonical, is_basic))
    return rows


def parse_grand_total_usd(priced_md_path):
    with open(priced_md_path, encoding="utf-8") as f:
        text = f.read()
    m = GRAND_TOTAL_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_priced_files(only=None):
    folders = sorted(
        d for d in os.listdir(ARCHIVE_DIR)
        if os.path.isdir(os.path.join(ARCHIVE_DIR, d))
    )
    if only:
        folders = [f for f in folders if f in only]
    priced_files = []
    for folder in folders:
        folder_path = os.path.join(ARCHIVE_DIR, folder)
        priced_files.extend(sorted(glob.glob(os.path.join(folder_path, "decklist*_priced.md"))))
    return priced_files


def build_price_histories(only=None, force=False, limit=None, quiet=False):
    """Build/refresh the decklist*_price_history.json sidecars. These are
    generated (gitignored) files, not checked in -- rebuilt here on webapp
    startup (force=False: only fill in whatever's missing) and after every
    daily price fetch (force=True: yesterday's sidecars are now missing a
    day, see scripts/fetch_prices.py). Returns the number of sidecars written.
    """
    _log = log if not quiet else (lambda msg: None)

    priced_files = collect_priced_files(only=only)
    if limit:
        priced_files = priced_files[:limit]

    pending = []
    for pf in priced_files:
        out_path = pf[: -len("_priced.md")] + "_price_history.json"
        if os.path.exists(out_path) and not force:
            continue
        pending.append((pf, out_path))

    if not pending:
        _log("Nothing to do (all sidecars already exist; use force=True to rebuild).")
        return 0

    _log(f"{len(pending)} decklists need a price-history sidecar.")

    definitions = load_latest_card_definitions()
    name_index = build_name_index(definitions)

    # First pass: parse every pending decklist's rows, resolve GoatBots ids,
    # and collect the full set of ids we'll need prices for.
    parsed = {}  # priced_md_path -> (rows_with_ids, unmatched_names)
    relevant_ids = set()
    for pf, out_path in pending:
        rows = parse_priced_rows(pf)
        resolved = []
        unmatched = []
        for qty, name, is_basic in rows:
            if is_basic or norm_key(name) in BASIC_LANDS:
                resolved.append((qty, None))
                continue
            ids = resolve_ids_for_name(name, name_index)
            if ids:
                relevant_ids.update(ids)
                resolved.append((qty, ids))
            else:
                resolved.append((qty, None))
                unmatched.append(name)
        parsed[pf] = (resolved, unmatched)

    _log(f"Resolved GoatBots ids for cards; {len(relevant_ids):,} distinct ids needed. "
         "Scanning daily price archives (this reads every archived day once)...")
    prices_by_date = load_relevant_prices(relevant_ids)
    _log(f"Loaded prices for {len(prices_by_date):,} archived days.")

    dates_sorted = sorted(prices_by_date.keys())

    written = 0
    for pf, out_path in pending:
        resolved, unmatched = parsed[pf]
        tix_series = []
        for date_str in dates_sorted:
            day_prices = prices_by_date[date_str]
            total = 0.0
            any_priced = False
            for qty, ids in resolved:
                if ids is None:
                    continue
                candidates = [day_prices[i] for i in ids if i in day_prices]
                if not candidates:
                    continue
                # A card name can map to many GoatBots ids (every set/foil
                # printing ever released digitally), and the cheapest of
                # those is usually a long-tail bulk reprint that understates
                # what the card actually costs to acquire. The median across
                # matched printings is a steadier, more representative price
                # than either extreme.
                total += qty * statistics.median(candidates)
                any_priced = True
            if any_priced:
                tix_series.append([date_str, round(total, 2)])

        usd_total = parse_grand_total_usd(pf)
        usd_series = [[_today(), usd_total]] if usd_total is not None else []

        out = {
            "generated": _today(),
            "tix": tix_series,
            "usd": usd_series,
            "unmatched_cards": sorted(set(unmatched)),
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        written += 1

    _log(f"Wrote {written} price-history sidecars.")
    return written


def _today():
    import datetime
    return datetime.date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="only process these folder names")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    build_price_histories(only=args.only, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
