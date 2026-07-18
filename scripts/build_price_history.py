#!/usr/bin/env python3
"""
Build per-deck historical price series for the "Building on a Budget" archive.

Digital (MTGO tix): genuine multi-year daily history, built from GoatBots'
yearly bulk archives (prices/goatbots_yearly_archive/<year>/<date>.txt.gz,
card id -> tix price) extended day-by-day with the snapshots the daily fetch
archives (prices/daily/<fetch-date>/goatbots/price-history.zip, whose inner
filename carries the date the prices are actually for -- GoatBots publishes
about two days behind). Both are joined against the id -> card name mapping
in the most recent prices/daily/<date>/goatbots/card-definitions.zip. Where
a name maps to several GoatBots ids (different sets/foil versions -- GoatBots
and Scryfall don't share set-code conventions, e.g. Scryfall's "hop" vs
GoatBots' "PC1" for the same Planechase set, so the two can't reliably be
cross-referenced to "the same printing"), the median price across matched
printings for that day is used, to avoid both the near-zero bulk-reprint
floor and any single-printing spike.

Physical (paper USD): per research/PRICE_DATA_SOURCES.md no source has real
day-by-day paper history for these 2003-2009 decks, so this is a
"start the clock now" series: one point per archived daily MTGJSON snapshot
(prices/daily/<fetch-date>/mtgjson/AllPricesToday.json.bz2, archiving began
2026-07-16), joined by card name via prices/mtgjson/uuid_to_name.json.gz
(built by scripts/build_mtgjson_uuid_map.py in the fetch workflow). Median
across matched printings per day, USD retail, providers preferred in
PAPER_PROVIDER_PREFERENCE order; basic lands excluded (as in the tix
series). If the uuid map isn't available yet, falls back to the deck's
current Scryfall-sourced grand total from decklist*_priced.md as a
single-day series.

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

DAILY_GOATBOTS_INNER_RE = re.compile(r"price-history-(\d{4}-\d{2}-\d{2})\.txt$")


def iter_price_days():
    """Yields (date_str, path) for every archived GoatBots daily price file,
    sorted chronologically."""
    for year_dir in sorted(glob.glob(os.path.join(YEARLY_DIR, "*"))):
        for path in sorted(glob.glob(os.path.join(year_dir, "*.txt.gz"))):
            date_str = os.path.basename(path)[: -len(".txt.gz")]
            yield date_str, path


def iter_daily_goatbots_days():
    """Yields (price_date_str, zip_path, member) for each daily-fetched
    GoatBots snapshot (prices/daily/<fetch-date>/goatbots/price-history.zip).
    The zip's inner filename carries the date the prices are actually for
    (GoatBots publishes ~2 days behind the fetch date), so that -- not the
    fetch-directory date -- is the series date."""
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
            if m:
                yield m.group(1), zpath, member


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
    # The yearly bulk archives end whenever GoatBots last published one; the
    # daily fetches carry the series forward from there (overlapping dates
    # keep the yearly-archive copy -- same data either way).
    for date_str, zpath, member in iter_daily_goatbots_days():
        if date_str in by_date:
            continue
        with zipfile.ZipFile(zpath) as z:
            with z.open(member) as f:
                day_prices = json.load(f)
        filtered = {cid: price for cid, price in day_prices.items() if cid in relevant_ids}
        if filtered:
            by_date[date_str] = filtered
    return by_date


# ---------------------------------------------------------------------------
# Daily paper (USD) snapshots: MTGJSON AllPricesToday, joined by uuid -> name
# ---------------------------------------------------------------------------

UUID_MAP_PATH = os.path.join(PRICES_DIR, "mtgjson", "uuid_to_name.json.gz")

# USD retail providers, most representative first (mirrors the reasoning for
# the tix median: prefer the market this archive's grand totals came from).
PAPER_PROVIDER_PREFERENCE = ["tcgplayer", "cardkingdom", "manapool", "cardsphere"]


def load_uuid_name_index():
    """name -> set-of-uuids indexes (same three-level normalization as the
    GoatBots index) from prices/mtgjson/uuid_to_name.json.gz, or None if the
    map hasn't been built yet (see scripts/build_mtgjson_uuid_map.py).
    Split/double-faced cards are indexed under the full "A // B" name and
    each face name, since decklists cite the front face."""
    if not os.path.exists(UUID_MAP_PATH):
        return None
    with gzip.open(UUID_MAP_PATH, "rt", encoding="utf-8") as f:
        names = json.load(f)["names"]
    definitions = {}
    for uuid, name in names.items():
        definitions[uuid] = {"name": name}
    by_exact, by_noapos, by_stripped = build_name_index(definitions)
    for uuid, name in names.items():
        if " // " in name:
            for face in name.split(" // "):
                by_exact.setdefault(norm_key(face), set()).add(uuid)
                by_noapos.setdefault(norm_key_noapos(face), set()).add(uuid)
                by_stripped.setdefault(norm_key_noaccent_nopunct(face), set()).add(uuid)
    return by_exact, by_noapos, by_stripped


def _usd_price_for_uuid(entry):
    """One representative USD retail price from an AllPricesToday per-uuid
    entry (single-day snapshot: each provider's retail dict holds one dated
    value). Non-foil preferred; foil-only printings still count."""
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


def load_relevant_usd_prices(relevant_uuids):
    """Returns {price_date_str: {uuid: usd}} across every archived MTGJSON
    daily snapshot. The snapshot's meta.date (the day the prices are for,
    one day behind the fetch date) is the series date; if two fetches carry
    the same price date, the later fetch wins."""
    by_date = {}
    for day_dir in sorted(glob.glob(os.path.join(DAILY_DIR, "*"))):
        path = os.path.join(day_dir, "mtgjson", "AllPricesToday.json.bz2")
        if not os.path.exists(path):
            continue
        with bz2.open(path, "rt", encoding="utf-8") as f:
            snapshot = json.load(f)
        date_str = (snapshot.get("meta") or {}).get("date") or os.path.basename(day_dir)
        day = {}
        for uuid in relevant_uuids:
            entry = snapshot.get("data", {}).get(uuid)
            if not entry:
                continue
            price = _usd_price_for_uuid(entry)
            if price is not None:
                day[uuid] = price
        if day:
            by_date[date_str] = day
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
    uuid_index = load_uuid_name_index()
    if uuid_index is None:
        _log("No prices/mtgjson/uuid_to_name.json.gz yet -- physical (USD) "
             "series will fall back to the single current grand-total point.")

    # First pass: parse every pending decklist's rows, resolve GoatBots ids
    # (tix) and MTGJSON uuids (USD), and collect the full sets of ids we'll
    # need prices for.
    parsed = {}  # priced_md_path -> (rows_with_ids, unmatched_names)
    relevant_ids = set()
    relevant_uuids = set()
    for pf, out_path in pending:
        rows = parse_priced_rows(pf)
        resolved = []
        unmatched = []
        for qty, name, is_basic in rows:
            if is_basic or norm_key(name) in BASIC_LANDS:
                resolved.append((qty, None, None))
                continue
            ids = resolve_ids_for_name(name, name_index)
            uuids = resolve_ids_for_name(name, uuid_index) if uuid_index else None
            if ids:
                relevant_ids.update(ids)
            else:
                unmatched.append(name)
            if uuids:
                relevant_uuids.update(uuids)
            resolved.append((qty, ids, uuids))
        parsed[pf] = (resolved, unmatched)

    _log(f"Resolved GoatBots ids for cards; {len(relevant_ids):,} distinct ids needed. "
         "Scanning daily price archives (this reads every archived day once)...")
    prices_by_date = load_relevant_prices(relevant_ids)
    _log(f"Loaded tix prices for {len(prices_by_date):,} archived days.")

    usd_by_date = {}
    if relevant_uuids:
        usd_by_date = load_relevant_usd_prices(relevant_uuids)
        _log(f"Loaded USD prices for {len(usd_by_date):,} archived days.")

    dates_sorted = sorted(prices_by_date.keys())
    usd_dates_sorted = sorted(usd_by_date.keys())

    def daily_total(day_prices, resolved, id_slot):
        """Sum of qty x median-matched-printing price for one archived day;
        None when nothing in the deck matched that day. The median across a
        name's printings is used because the cheapest is usually a long-tail
        bulk reprint that understates what the card actually costs to
        acquire, while a single printing can spike."""
        total = 0.0
        any_priced = False
        for row in resolved:
            qty, ids = row[0], row[id_slot]
            if ids is None:
                continue
            candidates = [day_prices[i] for i in ids if i in day_prices]
            if not candidates:
                continue
            total += qty * statistics.median(candidates)
            any_priced = True
        return round(total, 2) if any_priced else None

    written = 0
    for pf, out_path in pending:
        resolved, unmatched = parsed[pf]
        tix_series = []
        for date_str in dates_sorted:
            total = daily_total(prices_by_date[date_str], resolved, 1)
            if total is not None:
                tix_series.append([date_str, total])

        usd_series = []
        for date_str in usd_dates_sorted:
            total = daily_total(usd_by_date[date_str], resolved, 2)
            if total is not None:
                usd_series.append([date_str, total])
        if not usd_series:
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
