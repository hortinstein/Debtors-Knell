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
                  *history* is per card name: the median USD retail price
                  across every matched printing on that day.

  Art, printings  scryfall/hob_prints.json.gz (checked in, refreshed by
  and current     fetch_scryfall.py in .github/workflows/hobbit-data.yml).
  paper prices    Scryfall supplies what the price archive structurally
                  can't: the real image for each printing, a current paper
                  price per *printing* (not just per name), and every
                  printing of a card including paper-only ones the
                  MTGO-derived GoatBots catalogue never lists. It's optional
                  -- without it the build still works, falling back to
                  GoatBots printings and browser-side name lookups for art.

So the two physical numbers on the page come from different places on
purpose: the current price is Scryfall's (per printing, always present), and
the change-since-first is MTGJSON's archived series (per card name, and only
where the uuid -> name map already covers the card). The page labels which is
which rather than blending them silently.

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
SCRYFALL_PATH = os.path.join(HERE, "scryfall", "hob_prints.json.gz")

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
# Scryfall printings: art, current per-printing paper prices, paper-only sets
# ---------------------------------------------------------------------------

def scryfall_name_index(scry_cards):
    """{normalized name: card entry}, indexed under the full name and under
    each face of a double-faced card. GoatBots names a two-faced card by its
    front face ("Bofur, Reliable Guardian") where Scryfall uses the full
    "Front // Back", so an exact-string lookup misses every one of them."""
    index = {}

    def put(key, entry):
        index.setdefault(bph.norm_key(key), entry)
        index.setdefault(bph.norm_key_noapos(key), entry)
        index.setdefault(bph.norm_key_noaccent_nopunct(key), entry)

    for name, entry in scry_cards.items():
        put(name, entry)
        if " // " in name:
            for face in name.split(" // "):
                put(face, entry)
    return index


def lookup_scryfall(name, index):
    for key in (bph.norm_key(name), bph.norm_key_noapos(name),
                bph.norm_key_noaccent_nopunct(name)):
        if key in index:
            return index[key]
    return None


def load_scryfall(path=SCRYFALL_PATH, quiet=False):
    """The checked-in Scryfall snapshot (fetch_scryfall.py), or None. Keyed by
    card name, each entry holding the set's own printing(s) plus every other
    printing of that card."""
    if not os.path.exists(path):
        log("No scryfall/hob_prints.json.gz -- building without art or current "
            "paper prices (run fetch_scryfall.py where Scryfall is reachable).", quiet)
        return None
    import gzip
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    log(f"Loaded Scryfall snapshot for {len(payload.get('cards', {}))} card(s), "
        f"fetched {payload.get('generated', '?')}.", quiet)
    return payload


def paper_price(printing):
    """One representative current USD price for a printing, and which finish
    it came from -- non-foil first, since that's what a price column means."""
    for key in ("usd", "usd_foil", "usd_etched"):
        value = printing.get(key)
        if value is not None:
            return value, key
    return None, None


def choose_primary(printings, set_code, number):
    """The printing a set's card should be *shown* as: same set, same
    collector number where GoatBots and Scryfall agree on it, else the
    lowest-numbered printing in the set that has art."""
    same_set = [p for p in printings if (p.get("set") or "").upper() == set_code.upper()]
    if not same_set:
        return None
    for p in same_set:
        if str(p.get("number")) == str(number):
            return p
    return sorted(same_set, key=lambda p: (p.get("image") is None,
                                           _collector_key(p.get("number"))))[0]


def _collector_key(number):
    m = re.match(r"(\d+)", str(number or ""))
    return (int(m.group(1)) if m else 10 ** 9, str(number or ""))


def goatbots_ids_by_set(all_ids, definitions):
    """{SET: [goatbots id, ...]} for one card's printings, so a Scryfall
    printing can pick up the tix series of the MTGO printing from the same
    set (both finishes, where GoatBots lists them separately)."""
    out = {}
    for cid in all_ids:
        code = (definitions[cid].get("cardset") or "").upper()
        out.setdefault(code, []).append(cid)
    return out


# ---------------------------------------------------------------------------
# Physical (USD) prices, joined by card name through the MTGJSON uuid map
# ---------------------------------------------------------------------------

def load_uuid_printing_index(quiet=False):
    """{(SET, number): [uuid, ...]} from the uuid map's `printings` section
    (scripts/build_mtgjson_uuid_map.py), or None for maps built before that
    section existed. This is what makes the physical series *per printing*
    instead of a median across everything sharing a name -- which matters
    enormously for a new set, where the same card exists as a $30 base
    printing and a $200 serialized variant."""
    if not os.path.exists(bph.UUID_MAP_PATH):
        return None
    import gzip
    with gzip.open(bph.UUID_MAP_PATH, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    printings = payload.get("printings")
    if not printings:
        log("The MTGJSON uuid map has no `printings` section (built before it "
            "was added) -- physical prices fall back to name-level medians.", quiet)
        return None
    index = {}
    for uuid, ident in printings.items():
        code, _, number = ident.partition("|")
        index.setdefault((code.upper(), number.lower()), []).append(uuid)
    return index


def printing_uuids(printing, printing_index):
    """The MTGJSON uuid(s) for one Scryfall printing, matched on set code and
    collector number (MTGJSON and Scryfall agree on both)."""
    if not printing or not printing_index:
        return set()
    key = ((printing.get("set") or "").upper(), str(printing.get("number") or "").lower())
    return set(printing_index.get(key, ()))


def load_usd_series(targets, quiet=False):
    """targets: {key: set-of-uuids}. Returns ({key: [[date, usd], ...]},
    day_count) -- one series per target, median across its uuids per archived
    day (a single printing usually has exactly one)."""
    relevant = set()
    for uuids in targets.values():
        relevant.update(uuids)
    if not relevant:
        return {}, 0

    log(f"Reading archived MTGJSON snapshots for {len(relevant):,} printing(s) "
        "(a few seconds per archived day)...", quiet)
    usd_by_date = bph.load_relevant_usd_prices(relevant)
    log(f"Loaded physical prices for {len(usd_by_date)} archived day(s).", quiet)

    out = {}
    for key, uuids in targets.items():
        series = series_for_ids(usd_by_date, uuids)
        if series:
            out[key] = series
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
    scryfall = load_scryfall(quiet=quiet)
    scry_cards = (scryfall or {}).get("cards", {})
    scry_index = scryfall_name_index(scry_cards)
    printing_index = load_uuid_printing_index(quiet)
    uuid_name_index = bph.load_uuid_name_index()
    if uuid_name_index is None:
        log("No prices/mtgjson/uuid_to_name.json.gz yet -- no physical prices.", quiet)

    # Each card's other versions, from both catalogues: Scryfall knows every
    # printing (paper included, with art and a current price), GoatBots knows
    # the MTGO ones (with our tix history). Rank by what a version is worth --
    # paper price first, then tix -- and keep the top few, so "Island"
    # contributes a handful of versions instead of seven hundred.
    relevant_ids = set(set_cards)
    version_plans = {}
    for name, entry in ids_by_name.items():
        gb_by_set = goatbots_ids_by_set(entry["all_ids"], definitions)
        set_gb_ids = entry["set_ids"]
        scry = lookup_scryfall(name, scry_index) or {}
        printings = scry.get("printings") or scry.get("set_printings") or []
        primary = choose_primary(printings, set_code, definitions[min(set_gb_ids)].get("version"))

        candidates = []
        covered_sets = set()
        for p in printings:
            if primary is not None and p.get("id") == primary.get("id"):
                continue
            code = (p.get("set") or "").upper()
            covered_sets.add(code)
            gb_ids = [i for i in gb_by_set.get(code, []) if i not in set_gb_ids]
            usd, finish = paper_price(p)
            candidates.append({
                "key": f"{code}-{p.get('number')}",
                "set": code,
                "set_name": p.get("set_name") or "",
                "number": p.get("number") or "",
                "rarity": p.get("rarity") or "",
                "released": p.get("released") or "",
                "digital": bool(p.get("digital")),
                "promo": bool(p.get("promo")),
                "image": p.get("image"),
                "image_small": p.get("image_small"),
                "scryfall_uri": p.get("scryfall_uri") or "",
                "scryfall_set": (p.get("set") or "").lower(),
                "usd": usd,
                "usd_finish": finish,
                "usd_nonfoil": p.get("usd"),
                "usd_foil": p.get("usd_foil"),
                "scry_tix": p.get("tix"),
                "same_set": code == set_code.upper(),
                "_gb_ids": gb_ids,
            })

        # MTGO printings Scryfall has no counterpart for (GoatBots' promo and
        # MTGO-only buckets); keep them, they still have real tix history.
        for code, gb_ids in gb_by_set.items():
            if code == set_code.upper() or code in covered_sets:
                continue
            ids = [i for i in gb_ids if i not in set_gb_ids]
            if not ids:
                continue
            vmeta = definitions[ids[0]]
            candidates.append({
                "key": f"gb-{ids[0]}",
                "set": code,
                "set_name": "",
                "number": vmeta.get("version") or "",
                "rarity": vmeta.get("rarity") or "",
                "released": "",
                "digital": True,
                "promo": False,
                "image": None,
                "image_small": None,
                "scryfall_uri": "",
                "scryfall_set": scryfall_set_for(code),
                "usd": None,
                "usd_finish": None,
                "usd_nonfoil": None,
                "usd_foil": None,
                "scry_tix": None,
                "same_set": False,
                "_gb_ids": ids,
            })

        def rank(v):
            tix_now = max((latest_day.get(i) or 0) for i in v["_gb_ids"]) if v["_gb_ids"] else 0
            # Other printings from this same set (showcase, borderless) sort
            # first: they're the closest thing to "another version of this".
            return (not v["same_set"], -(v["usd"] or 0), -tix_now, v["key"])

        candidates.sort(key=rank)
        kept = candidates[:max_versions]
        for v in kept:
            relevant_ids.update(v["_gb_ids"])
        version_plans[name] = {
            "kept": kept,
            "total": len(candidates),
            "primary": primary,
            "set_printings": scry.get("set_printings") or [],
        }
    log(f"{len(relevant_ids):,} distinct printing(s) need a digital price series.", quiet)

    tix_by_date = load_tix_window(relevant_ids, window_days, quiet)
    log(f"Loaded digital prices for {len(tix_by_date)} archived day(s).", quiet)

    # Physical targets: the exact printing where the uuid map can identify it,
    # the card name (median across its printings) where it can't. Versions get
    # their own per-printing series too, so the modal can show what each
    # printing has done in paper, not just in tix.
    usd_targets = {}
    usd_basis = {}
    for name, plan in version_plans.items():
        uuids = printing_uuids(plan["primary"], printing_index)
        basis = "printing"
        if not uuids and uuid_name_index is not None:
            uuids = bph.resolve_ids_for_name(name, uuid_name_index) or set()
            basis = "name" if uuids else None
        if uuids:
            usd_targets[("card", name)] = uuids
        usd_basis[name] = basis if uuids else None
        for v in plan["kept"]:
            vuuids = printing_uuids(v, printing_index)
            if vuuids:
                usd_targets[("ver", name, v["key"])] = vuuids

    by_printing = sum(1 for b in usd_basis.values() if b == "printing")
    by_name = sum(1 for b in usd_basis.values() if b == "name")
    log(f"Physical price basis: {by_printing} card(s) matched to their exact "
        f"printing, {by_name} to a card name, "
        f"{len(usd_basis) - by_printing - by_name} unmatched.", quiet)

    usd_series_by_target, usd_day_count = load_usd_series(usd_targets, quiet)

    cards = []
    slugs = {}
    for cid, meta in sorted(set_cards.items(), key=lambda kv: _number_key(kv[1])):
        name = meta["name"]
        entry = ids_by_name[name]
        plan = version_plans[name]
        primary = plan["primary"]

        tix_series = series_for_ids(tix_by_date, entry["set_ids"])
        usd_series = usd_series_by_target.get(("card", name), [])

        versions = []
        for v in plan["kept"]:
            vseries = series_for_ids(tix_by_date, v["_gb_ids"]) if v["_gb_ids"] else []
            vusd = usd_series_by_target.get(("ver", name, v["key"]), [])
            record = {k: val for k, val in v.items() if k != "_gb_ids"}
            record["tix"] = vseries[-VERSION_SERIES_POINTS:]
            record["tix_stats"] = change_stats(vseries)
            record["usd_series"] = vusd[-VERSION_SERIES_POINTS:]
            record["usd_stats"] = change_stats(vusd)
            versions.append(record)

        # The set's own printing, as Scryfall shows it: real art, and a
        # current paper price for cards whose MTGJSON history hasn't started.
        scry_usd, scry_usd_finish = paper_price(primary) if primary else (None, None)
        if scry_usd is None:
            for p in plan["set_printings"]:
                scry_usd, scry_usd_finish = paper_price(p)
                if scry_usd is not None:
                    break

        # One "current physical price" per card, and where it came from: the
        # archived MTGJSON series when there is one (it's what the change
        # column measures), Scryfall's live price otherwise.
        if usd_series:
            usd_current, usd_source = usd_series[-1][1], "mtgjson"
        elif scry_usd is not None:
            usd_current, usd_source = scry_usd, "scryfall"
        else:
            usd_current, usd_source = None, None

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
            "rarity": (primary or {}).get("rarity") or meta.get("rarity") or "",
            "number": (primary or {}).get("number") or meta.get("version") or "",
            "set_name": (primary or {}).get("set_name") or "",
            "released": (primary or {}).get("released") or "",
            "art": (primary or {}).get("image"),
            "art_small": (primary or {}).get("image_small"),
            "scryfall_uri": (primary or {}).get("scryfall_uri") or "",
            "tix": tix_series,
            "tix_stats": change_stats(tix_series),
            "scry_tix": (primary or {}).get("tix"),
            "usd": usd_series,
            "usd_stats": change_stats(usd_series),
            "usd_basis": usd_basis.get(name),
            "scry_usd": scry_usd,
            "scry_usd_finish": scry_usd_finish,
            "scry_usd_foil": (primary or {}).get("usd_foil"),
            "usd_current": usd_current,
            "usd_source": usd_source,
            "versions": versions,
            "versions_total": plan["total"],
        })

    payload = {
        "generated": datetime.date.today().isoformat(),
        "set_code": set_code.upper(),
        "window_days": window_days,
        "latest_tix_date": latest_date,
        "usd_day_count": usd_day_count,
        "scryfall_generated": (scryfall or {}).get("generated"),
        "cards": cards,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    with_series = sum(1 for c in cards if c["usd"])
    with_price = sum(1 for c in cards if c["usd_current"] is not None)
    log(f"Wrote {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB): "
        f"{len(cards)} cards, {with_price} with a current physical price, "
        f"{with_series} with a physical price series.", quiet)
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
