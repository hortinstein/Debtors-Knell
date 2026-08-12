#!/usr/bin/env python3
"""Build/refresh prices/mtgjson/uuid_to_name.json.gz: MTGJSON uuid -> card
name, plus uuid -> printing (set code and collector number).

The daily-archived MTGJSON price snapshots (prices/daily/<date>/mtgjson/
AllPricesToday.json.bz2) are keyed by MTGJSON uuid with no name attached, so
they can't be joined to decklists without a mapping. This script downloads
MTGJSON's AllIdentifiers file once and distills it down to {uuid: name} and
{uuid: "SET|number"} (a few MB gzipped), which scripts/build_price_history.py
then uses to turn those archived snapshots into per-deck physical (USD)
price series.

The `printings` section is what lets a caller price one *specific* printing
instead of taking the median across every printing that shares a name --
hobbitwatch/build_dataset.py uses it so a new set's base printing isn't
priced as an average of itself and its $200 serialized variant. Treat it as
optional: maps built before it existed carry only `names`, and readers fall
back to name-level matching.

Run from .github/workflows/fetch-prices.yml after each fetch; it exits
immediately unless the map is missing or older than REFRESH_DAYS (new sets
add new uuids, but the cards in this 2003-2009 archive barely change, so a
monthly refresh is plenty). The resulting file is committed with the rest of
prices/ by the workflow's auto-commit step.

Usage:
    python3 scripts/build_mtgjson_uuid_map.py [--force]
"""
import argparse
import bz2
import datetime
import gzip
import json
import os
import sys
import tempfile

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO_ROOT, "prices", "mtgjson", "uuid_to_name.json.gz")

ALL_IDENTIFIERS_URL = "https://mtgjson.com/api/v5/AllIdentifiers.json.bz2"
REFRESH_DAYS = 30


def log(msg):
    print(msg, flush=True)


def existing_map_age_days():
    """Age of the current map in days, or None if it's missing, unreadable, or
    predates the `printings` section (a map without it can't price a single
    printing, so it's due for a rebuild whatever its age)."""
    if not os.path.exists(MAP_PATH):
        return None
    try:
        with gzip.open(MAP_PATH, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        if not payload.get("printings"):
            return None
        return (datetime.date.today()
                - datetime.date.fromisoformat(payload.get("generated"))).days
    except Exception:
        return None  # unreadable/legacy file: rebuild


def build(force=False):
    age = existing_map_age_days()
    if not force and age is not None and age < REFRESH_DAYS:
        log(f"uuid_to_name map is {age} day(s) old (< {REFRESH_DAYS}); nothing to do.")
        return

    log(f"Downloading {ALL_IDENTIFIERS_URL} ...")
    with tempfile.NamedTemporaryFile(suffix=".json.bz2", delete=False) as tmp:
        tmp_path = tmp.name
        with requests.get(ALL_IDENTIFIERS_URL, stream=True, timeout=600) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)

    try:
        log("Parsing (AllIdentifiers decompresses to several hundred MB; "
            "fine on a CI runner, just not instant)...")
        with bz2.open(tmp_path, "rt", encoding="utf-8") as f:
            data = json.load(f)["data"]
    finally:
        os.unlink(tmp_path)

    names = {}
    printings = {}
    for uuid, card in data.items():
        name = card.get("name")
        if name:
            names[uuid] = name
        set_code = card.get("setCode")
        number = card.get("number")
        if set_code and number is not None:
            printings[uuid] = f"{set_code}|{number}"
    log(f"Extracted {len(names):,} uuid -> name and {len(printings):,} "
        "uuid -> printing entries.")

    os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)
    payload = {
        "generated": datetime.date.today().isoformat(),
        "names": names,
        "printings": printings,
    }
    with gzip.open(MAP_PATH, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    log(f"Wrote {MAP_PATH} ({os.path.getsize(MAP_PATH) / 1e6:.1f} MB).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    build(force=args.force)


if __name__ == "__main__":
    main()
