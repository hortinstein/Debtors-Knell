#!/usr/bin/env python3
"""
Build the Hobbit Watch dataset: every card in the new Hobbit (HOB) set, with
its physical (USD) and digital (tix) price history and every other printing
of the same card that the price archive knows about.

Everything comes out of the price archive this repo already keeps
(see ../prices/README.md) -- no network access is needed or used:

  Card universe   ../prices/daily/<date>/goatbots/card-definitions.zip, the
                  most recent snapshot: GoatBots' MTGO card id -> name / set
                  / rarity / collector number. Cards whose `cardset` is the
                  target set (HOB) are the page's subject; every id in *any*
                  set sharing a card's name is one of its other versions.

  Digital (tix)   ../prices/goatbots_yearly_archive/<year>/<date>.txt.gz plus
                  the daily snapshots in ../prices/daily/<date>/goatbots/
                  price-history.zip (whose inner filename carries the date
                  the prices are actually for -- GoatBots publishes about two
                  days behind). One series per GoatBots id, so each printing
                  gets its own digital price history.

  Physical (USD)  ../prices/daily/<date>/mtgjson/AllPricesToday.json.bz2,
                  joined by card name through ../prices/mtgjson/
                  uuid_to_name.json.gz. MTGJSON's price snapshots are keyed
                  by uuid with no set attached, so -- exactly as
                  scripts/build_price_history.py does it -- the physical
                  series is per *card name*: the median USD retail price
                  across every matched printing on that day. That means
                  physical prices are card-level, not per-printing; the page
                  says so rather than pretending otherwise.

A caveat worth knowing about the physical side: the uuid -> name map is
refreshed monthly (scripts/build_mtgjson_uuid_map.py, REFRESH_DAYS = 30), so
a set as new as HOB is only partly in it until the next refresh lands. Cards
it doesn't cover yet simply have no USD series and the page shows a dash --
they fill themselves in on the next map refresh, no change needed here.

Output: data/hobbit_cards.json (gitignored -- derived data, rebuilt from the
archive whenever it's missing or stale; app.py builds it on startup).

Usage:
    python3 build_dataset.py                 # 180-day window, HOB
    python3 build_dataset.py --days 0        # every archived day (slow)
    python3 build_dataset.py --set LTR       # any other set in the archive
    python3 build_dataset.py --force
"""
import argparse
import datetime
import glob
import json
import os
import re
import statistics
import sys
import unicodedata
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
DATA_DIR = os.path.join(HERE, "data")
OUT_PATH = os.path.join(DATA_DIR, "hobbit_cards.json")

# The price-archive readers (GoatBots definitions, the daily/yearly price
# iterators, the MTGJSON uuid map and its USD-provider preference) already
# exist in scripts/build_price_history.py and are stdlib-only, so this script
# reuses them instead of keeping a second copy of the same parsing rules.
sys.path.insert(0, SCRIPTS_DIR)
import build_price_history as bph  # noqa: E402

SET_CODE = "HOB"

# How far back to read the digital price archive. HOB itself only entered the
# GoatBots feed in early August 2026, but its cards' *other* printings go back
# years, and reading every archived day for those costs a minute or so for
# history nobody looks at on a new-set page. Six months is plenty of context
# for "how has this printing behaved lately"; --days 0 reads everything.
WINDOW_DAYS = 180

# Widely reprinted cards (the basic lands especially) have hundreds of MTGO
# printings. Keep the most expensive ones -- those are the versions anyone
# opening the modal is actually curious about -- and report the true total.
MAX_VERSIONS = 24

# Per-version series are context for the card's own chart, not the subject of
# it; the tail is what matters and the whole dataset ships to the browser.
VERSION_SERIES_POINTS = 60

# GoatBots set codes are mostly Scryfall's too, but not always -- the two
# don't share conventions for a handful of supplemental products. Only the
# card art (a Scryfall image URL built in the browser) depends on this, and
# an unknown code falls back to a set-less lookup by exact name, so this map
# only needs the cases where a wrong-but-valid code would show the wrong art.
SCRYFALL_SET_ALIASES = {
    "PC1": "hop",
    "PC2": "pc2",
    "PRM": None,   # GoatBots' catch-all promo bucket: no single Scryfall set
    "MSC": None,   # "Masters Collection"-style bundles, no Scryfall analogue
}


def log(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


def slugify(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "card"


def scryfall_set_for(goatbots_set):
    if goatbots_set in SCRYFALL_SET_ALIASES:
        return SCRYFALL_SET_ALIASES[goatbots_set]
    return goatbots_set.lower()


# ---------------------------------------------------------------------------
# Digital (tix) price files
# ---------------------------------------------------------------------------

def iter_all_tix_days():
    """(date_str, loader) for every archived GoatBots price day, newest last.
    The yearly bulk archives and the daily snapshots overlap; where they do,
    the yearly copy wins (same data either way) -- same rule as
    build_price_history.load_relevant_prices."""
    days = {}
    for date_str, zpath, member in bph.iter_daily_goatbots_days():
        days[date_str] = ("zip", zpath, member)
    for date_str, path in bph.iter_price_days():
        days[date_str] = ("gz", path, None)
    for date_str in sorted(days):
        yield date_str, days[date_str]


def load_tix_day(source):
    kind, path, member = source
    if kind == "gz":
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with zipfile.ZipFile(path) as z:
        with z.open(member) as f:
            return json.load(f)


def load_tix_window(relevant_ids, window_days, quiet=False):
    """{date_str: {goatbots_id: tix}} for the requested trailing window,
    restricted to relevant_ids so no full day's ~100k-card map is ever held
    longer than it takes to filter it down."""
    days = list(iter_all_tix_days())
    if not days:
        raise SystemExit("No GoatBots price data found under prices/.")
    if window_days:
        latest = datetime.date.fromisoformat(days[-1][0])
        cutoff = (latest - datetime.timedelta(days=window_days)).isoformat()
        days = [d for d in days if d[0] >= cutoff]
    log(f"Reading {len(days)} archived digital price day(s)...", quiet)
    by_date = {}
    for date_str, source in days:
        day = load_tix_day(source)
        filtered = {cid: day[cid] for cid in relevant_ids if cid in day}
        if filtered:
            by_date[date_str] = filtered
    return by_date


def load_latest_tix_day(quiet=False):
    """The most recent archived day's *full* price map, used to rank a card's
    printings before deciding which ones to keep a series for."""
    days = list(iter_all_tix_days())
    if not days:
        raise SystemExit("No GoatBots price data found under prices/.")
    date_str, source = days[-1]
    log(f"Latest archived digital price day: {date_str}.", quiet)
    return date_str, load_tix_day(source)


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------

def series_for_ids(prices_by_date, ids):
    """[[date, price], ...] for one card: the median across its matched
    printings that day (build_price_history's rule -- the cheapest printing
    is usually a bulk reprint that understates the card, and a single
    printing can spike)."""
    out = []
    for date_str in sorted(prices_by_date):
        day = prices_by_date[date_str]
        vals = [day[i] for i in ids if i in day]
        if vals:
            out.append([date_str, round(statistics.median(vals), 2)])
    return out


def change_stats(series):
    """First/last value of a series plus the change since that first archived
    data point -- which is what the page ranks on. Empty series -> all None,
    so the template can show a dash without special-casing."""
    if not series:
        return {"first": None, "last": None, "first_date": None, "last_date": None,
                "change": None, "pct": None, "days": 0}
    first_date, first = series[0]
    last_date, last = series[-1]
    change = round(last - first, 2)
    pct = round((change / first) * 100, 1) if first else None
    return {"first": first, "last": last, "first_date": first_date,
            "last_date": last_date, "change": change, "pct": pct,
            "days": len(series)}


# ---------------------------------------------------------------------------
# Physical (USD) prices, joined by card name through the MTGJSON uuid map
# ---------------------------------------------------------------------------

def load_usd_by_name(names, quiet=False):
    """{card_name: [[date, usd], ...]} -- median USD retail across every
    matched printing per archived day. Returns ({}, 0) when the uuid map
    hasn't been built yet."""
    uuid_index = bph.load_uuid_name_index()
    if uuid_index is None:
        log("No prices/mtgjson/uuid_to_name.json.gz yet -- no physical prices.", quiet)
        return {}, 0

    uuids_by_name = {}
    relevant = set()
    for name in names:
        uuids = bph.resolve_ids_for_name(name, uuid_index)
        if uuids:
            uuids_by_name[name] = uuids
            relevant.update(uuids)
    log(f"{len(uuids_by_name)}/{len(names)} card name(s) matched in the MTGJSON "
        f"uuid map ({len(relevant):,} printings).", quiet)
    if not relevant:
        return {}, 0

    log("Reading archived MTGJSON snapshots (a few seconds per day)...", quiet)
    usd_by_date = bph.load_relevant_usd_prices(relevant)
    log(f"Loaded physical prices for {len(usd_by_date)} archived day(s).", quiet)

    out = {}
    for name, uuids in uuids_by_name.items():
        series = series_for_ids(usd_by_date, uuids)
        if series:
            out[name] = series
    return out, len(usd_by_date)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(set_code=SET_CODE, window_days=WINDOW_DAYS, max_versions=MAX_VERSIONS,
          out_path=OUT_PATH, quiet=False):
    definitions = bph.load_latest_card_definitions()

    set_cards = {cid: meta for cid, meta in definitions.items()
                 if (meta.get("cardset") or "").upper() == set_code.upper()}
    if not set_cards:
        raise SystemExit(
            f"No cards with cardset {set_code!r} in the latest GoatBots "
            "card-definitions snapshot.")
    log(f"{len(set_cards)} {set_code} printing(s) in the card definitions.", quiet)

    name_index = bph.build_name_index(definitions)

    # Every printing of every HOB card, anywhere in the MTGO catalogue.
    ids_by_name = {}
    for cid, meta in set_cards.items():
        name = meta["name"]
        if name in ids_by_name:
            ids_by_name[name]["set_ids"].add(cid)
            continue
        all_ids = bph.resolve_ids_for_name(name, name_index) or {cid}
        ids_by_name[name] = {"set_ids": {cid}, "all_ids": set(all_ids)}

    latest_date, latest_day = load_latest_tix_day(quiet)

    # Rank each card's other printings by their current price and keep the
    # top few, so "Island" contributes a handful of versions instead of 700.
    relevant_ids = set()
    kept_versions = {}
    for name, entry in ids_by_name.items():
        others = entry["all_ids"] - entry["set_ids"]
        ranked = sorted(others, key=lambda i: (-(latest_day.get(i) or 0), i))
        kept = ranked[:max_versions]
        kept_versions[name] = {"kept": kept, "total": len(others)}
        relevant_ids.update(entry["set_ids"])
        relevant_ids.update(kept)
    log(f"{len(relevant_ids):,} distinct printing(s) need a digital price series.", quiet)

    tix_by_date = load_tix_window(relevant_ids, window_days, quiet)
    log(f"Loaded digital prices for {len(tix_by_date)} archived day(s).", quiet)

    usd_by_name, usd_day_count = load_usd_by_name(sorted(ids_by_name), quiet)

    cards = []
    slugs = {}
    for cid, meta in sorted(set_cards.items(), key=lambda kv: _number_key(kv[1])):
        name = meta["name"]
        entry = ids_by_name[name]

        tix_series = series_for_ids(tix_by_date, entry["set_ids"])
        usd_series = usd_by_name.get(name, [])

        versions = []
        for vid in kept_versions[name]["kept"]:
            vmeta = definitions[vid]
            vseries = series_for_ids(tix_by_date, {vid})
            versions.append({
                "id": vid,
                "set": vmeta.get("cardset") or "",
                "scryfall_set": scryfall_set_for(vmeta.get("cardset") or ""),
                "rarity": vmeta.get("rarity") or "",
                "number": vmeta.get("version") or "",
                "foil": bool(vmeta.get("foil")),
                "tix": vseries[-VERSION_SERIES_POINTS:],
                "tix_stats": change_stats(vseries),
            })
        # Most expensive printing first; the ones with no archived price at
        # all sink to the bottom rather than interleaving with real data.
        versions.sort(key=lambda v: (v["tix_stats"]["last"] is None,
                                     -(v["tix_stats"]["last"] or 0)))

        slug = slugify(name)
        if slug in slugs:  # names are unique within a set, but be safe
            slug = f"{slug}-{cid}"
        slugs[slug] = name

        cards.append({
            "id": cid,
            "slug": slug,
            "name": name,
            "set": meta.get("cardset") or set_code,
            "scryfall_set": scryfall_set_for(meta.get("cardset") or set_code),
            "rarity": meta.get("rarity") or "",
            "number": meta.get("version") or "",
            "tix": tix_series,
            "tix_stats": change_stats(tix_series),
            "usd": usd_series,
            "usd_stats": change_stats(usd_series),
            "versions": versions,
            "versions_total": kept_versions[name]["total"],
        })

    payload = {
        "generated": datetime.date.today().isoformat(),
        "set_code": set_code.upper(),
        "window_days": window_days,
        "latest_tix_date": latest_date,
        "usd_day_count": usd_day_count,
        "cards": cards,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    priced = sum(1 for c in cards if c["usd"])
    log(f"Wrote {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB): "
        f"{len(cards)} cards, {priced} with a physical price series.", quiet)
    return payload


def _number_key(meta):
    """Collector-number sort that keeps '10' after '9' but tolerates the
    letter suffixes GoatBots uses for variants."""
    raw = str(meta.get("version") or "")
    m = re.match(r"(\d+)", raw)
    return (int(m.group(1)) if m else 10 ** 9, raw)


def dataset_is_stale(out_path=OUT_PATH):
    """True when data/hobbit_cards.json is missing, unreadable, or older than
    the newest archived price day -- i.e. a daily fetch has landed since it
    was built."""
    if not os.path.exists(out_path):
        return True
    try:
        with open(out_path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return True
    newest = _newest_archived_price_day()
    return bool(newest and payload.get("latest_tix_date", "") < newest)


def _newest_archived_price_day():
    dates = []
    for path in glob.glob(os.path.join(bph.DAILY_DIR, "*", "goatbots", "price-history.zip")):
        try:
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    m = bph.DAILY_GOATBOTS_INNER_RE.search(member)
                    if m:
                        dates.append(m.group(1))
        except zipfile.BadZipFile:
            continue
    for path in glob.glob(os.path.join(bph.YEARLY_DIR, "*", "*.txt.gz")):
        dates.append(os.path.basename(path)[: -len(".txt.gz")])
    return max(dates) if dates else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=SET_CODE, help=f"GoatBots set code (default {SET_CODE})")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS,
                    help=f"trailing days of digital history to read; 0 = all "
                         f"(default {WINDOW_DAYS})")
    ap.add_argument("--max-versions", type=int, default=MAX_VERSIONS,
                    help=f"other printings kept per card (default {MAX_VERSIONS})")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if the dataset is already up to date")
    args = ap.parse_args()

    if not args.force and not dataset_is_stale(args.out):
        print(f"{args.out} is already up to date with the price archive "
              "(use --force to rebuild).")
        return
    build(set_code=args.set, window_days=args.days, max_versions=args.max_versions,
          out_path=args.out)


if __name__ == "__main__":
    main()
