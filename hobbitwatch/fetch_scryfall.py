#!/usr/bin/env python3
"""
Fetch the Hobbit set from Scryfall: every card, every printing, with current
prices and the art URLs the page shows.

Why this exists alongside the price archive: the archive is a *history*
(GoatBots tix per day, MTGJSON USD per day) but it carries no set information
for paper cards -- MTGJSON's price snapshots are keyed by uuid, and this repo
only keeps a uuid -> name map for them. Scryfall fills in exactly the gaps
that leaves:

  * a current paper price (USD, and USD foil) per *printing*, for cards whose
    MTGJSON history hasn't started yet
  * the real image for each printing, instead of a URL guessed from a
    GoatBots set code Scryfall may not use
  * every printing of a card, including paper-only ones the MTGO-derived
    GoatBots catalogue never lists

Output: scryfall/hob_prints.json.gz, which *is* checked in (unlike the
derived dataset) -- it's small, it changes only when Scryfall's prices do,
and committing it means the app has real art and paper prices even where
there's no network access to Scryfall.

Scryfall's API guidelines ask for a descriptive User-Agent, an explicit
Accept header, and 50-100ms between requests; this waits 120ms and retries
politely on 429.

Run from CI (.github/workflows/hobbit-data.yml) or by hand:
    python3 hobbitwatch/fetch_scryfall.py
    python3 hobbitwatch/fetch_scryfall.py --set ltr --out /tmp/ltr.json.gz
"""
import argparse
import datetime
import gzip
import json
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "scryfall", "hob_prints.json.gz")

SEARCH_URL = "https://api.scryfall.com/cards/search"
SET_CODE = "hob"

REQUEST_DELAY = 0.12  # Scryfall asks for 50-100ms between calls; be generous.
HEADERS = {
    "User-Agent": "DebtorsKnell-HobbitWatch/1.0 (github.com/hortinstein/Debtors-Knell)",
    "Accept": "application/json",
}


def log(msg):
    print(msg, flush=True)


def get(url, params=None, tries=4):
    """One rate-limited GET, retrying on 429 and transient 5xx."""
    for attempt in range(tries):
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            wait = 2 ** attempt
            log(f"  {r.status_code} from Scryfall, retrying in {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise SystemExit(f"Scryfall kept failing for {url}")


def paginate(url, params=None):
    """Every card across a paginated Scryfall search."""
    page = get(url, params)
    while True:
        for card in page.get("data", []):
            yield card
        if not page.get("has_more"):
            return
        page = get(page["next_page"])


def image_urls(card):
    """(normal, small) image URLs. Double-faced cards keep their images on
    each face rather than the card, so fall back to the front face."""
    imgs = card.get("image_uris")
    if not imgs:
        faces = card.get("card_faces") or []
        for face in faces:
            if face.get("image_uris"):
                imgs = face["image_uris"]
                break
    if not imgs:
        return None, None
    return imgs.get("normal") or imgs.get("large"), imgs.get("small") or imgs.get("normal")


def printing_record(card):
    normal, small = image_urls(card)
    prices = card.get("prices") or {}

    def num(key):
        v = prices.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "id": card.get("id"),
        "set": (card.get("set") or "").upper(),
        "set_name": card.get("set_name") or "",
        "number": card.get("collector_number") or "",
        "rarity": (card.get("rarity") or "").capitalize(),
        "released": card.get("released_at") or "",
        "digital": bool(card.get("digital")),
        "finishes": card.get("finishes") or [],
        "promo": bool(card.get("promo")),
        "image": normal,
        "image_small": small,
        "scryfall_uri": card.get("scryfall_uri") or "",
        "usd": num("usd"),
        "usd_foil": num("usd_foil"),
        "usd_etched": num("usd_etched"),
        "eur": num("eur"),
        "tix": num("tix"),
    }


def build(set_code=SET_CODE, out_path=OUT_PATH):
    log(f"Fetching set:{set_code} from Scryfall...")
    set_cards = list(paginate(SEARCH_URL, {"q": f"set:{set_code}", "unique": "prints",
                                           "order": "set"}))
    log(f"{len(set_cards)} printing(s) in {set_code.upper()}.")

    cards = {}
    seen_oracle = {}
    for card in set_cards:
        name = card.get("name") or ""
        oracle_id = card.get("oracle_id") or ""
        entry = cards.setdefault(name, {
            "name": name,
            "oracle_id": oracle_id,
            "set_printings": [],
            "printings": [],
        })
        entry["set_printings"].append(printing_record(card))
        if oracle_id:
            seen_oracle[oracle_id] = (name, card.get("prints_search_uri"))

    log(f"{len(cards)} distinct card(s); fetching every printing of each "
        f"(~{len(seen_oracle) * REQUEST_DELAY:.0f}s of rate-limited calls)...")
    for i, (oracle_id, (name, prints_uri)) in enumerate(sorted(seen_oracle.items()), 1):
        if not prints_uri:
            continue
        try:
            prints = list(paginate(prints_uri))
        except requests.HTTPError as e:
            log(f"  [{i}/{len(seen_oracle)}] {name}: {e} -- skipping")
            continue
        cards[name]["printings"] = [printing_record(p) for p in prints]
        if i % 25 == 0:
            log(f"  [{i}/{len(seen_oracle)}] {name}")

    payload = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "set_code": set_code.upper(),
        "cards": cards,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    total_prints = sum(len(c["printings"]) or len(c["set_printings"]) for c in cards.values())
    priced = sum(1 for c in cards.values()
                 if any(p["usd"] is not None or p["usd_foil"] is not None
                        for p in c["set_printings"]))
    log(f"Wrote {out_path} ({os.path.getsize(out_path) / 1e6:.2f} MB): "
        f"{len(cards)} cards, {total_prints} printings, {priced} with a paper price.")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=SET_CODE, help=f"Scryfall set code (default {SET_CODE})")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()
    build(set_code=args.set, out_path=args.out)


if __name__ == "__main__":
    main()
