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

Politeness matters here and the first version of this script got it wrong: it
asked Scryfall for each card's printings separately (193 searches) and was
rate-limited a quarter of the way through. It now does the whole job in two
paginated searches -- `set:hob` for the set itself, and `in:hob unique:prints`
for every printing of every card that appears in the set -- which is a few
dozen requests instead of a few hundred. On top of that: a descriptive
User-Agent, an explicit Accept header, 250ms between requests (Scryfall asks
for 50-100ms), Retry-After honoured, and a long backoff on 429.

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

REQUEST_DELAY = 0.25  # Scryfall asks for 50-100ms between calls; be generous.
HEADERS = {
    "User-Agent": "DebtorsKnell-HobbitWatch/1.0 (github.com/hortinstein/Debtors-Knell)",
    "Accept": "application/json",
}

# A 429 means we've already been impolite, so back off in seconds, not
# milliseconds, and give it several chances before giving up.
RATE_LIMIT_BACKOFF = [5, 10, 20, 40, 60]

# Every printing of a card, for a set containing basic lands, runs to several
# hundred entries (there are ~800 Islands). Keep the most valuable ones -- the
# page only ever shows a couple of dozen -- so the committed snapshot stays
# small. The set's own printings are always kept.
MAX_PRINTINGS_PER_CARD = 40


def log(msg):
    print(msg, flush=True)


def get(url, params=None):
    """One rate-limited GET, backing off properly on 429 and transient 5xx."""
    for attempt in range(len(RATE_LIMIT_BACKOFF) + 1):
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt >= len(RATE_LIMIT_BACKOFF):
                break
            wait = RATE_LIMIT_BACKOFF[attempt]
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = max(wait, int(retry_after))
                except ValueError:
                    pass
            log(f"  {r.status_code} from Scryfall, waiting {wait}s before retrying...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise SystemExit(f"Scryfall kept rate-limiting {url}; giving up.")


def paginate(url, params=None, label=""):
    """Every card across a paginated Scryfall search."""
    page = get(url, params)
    fetched = 0
    while True:
        for card in page.get("data", []):
            fetched += 1
            yield card
        if not page.get("has_more"):
            if label:
                log(f"  {label}: {fetched} card(s) over "
                    f"{-(-fetched // 175)} page(s).")
            return
        page = get(page["next_page"])


def search(query, unique="prints", label=""):
    """A whole paginated search, tolerating Scryfall's 404 for 'no cards
    matched' (an empty result is data, not an error)."""
    try:
        return list(paginate(SEARCH_URL, {"q": query, "unique": unique,
                                          "order": "released"}, label=label))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            log(f"  no cards matched {query!r}")
            return []
        raise


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


def _print_sort_key(p, set_code):
    """Which printings to keep when a card has more than we need: the set's
    own first, then the most valuable, then the most recent."""
    usd, _ = paper_price(p)
    return (p["set"] != set_code.upper(), -(usd or 0), p.get("released") or "")


def paper_price(printing):
    for key in ("usd", "usd_foil", "usd_etched"):
        value = printing.get(key)
        if value is not None:
            return value, key
    return None, None


def build(set_code=SET_CODE, out_path=OUT_PATH):
    log(f"Fetching set:{set_code} from Scryfall...")
    set_cards = search(f"set:{set_code}", label=f"set:{set_code}")
    if not set_cards:
        raise SystemExit(f"Scryfall has no cards in set {set_code!r}.")
    log(f"{len(set_cards)} printing(s) in {set_code.upper()}.")

    cards = {}
    oracle_to_name = {}
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
            oracle_to_name[oracle_id] = name

    # One search for every printing of every card that appears in the set,
    # rather than one search per card. `in:<set>` selects cards *printed in*
    # that set; `unique=prints` then returns each of their printings.
    log(f"{len(cards)} distinct card(s); fetching every printing of each in one "
        f"search (in:{set_code} unique:prints)...")
    all_prints = search(f"in:{set_code}", label=f"in:{set_code}")
    if not all_prints:
        log("  in: search returned nothing -- keeping the set's own printings only.")

    grouped = {}
    for card in all_prints:
        name = oracle_to_name.get(card.get("oracle_id") or "") or card.get("name") or ""
        if name not in cards:
            continue
        grouped.setdefault(name, []).append(printing_record(card))

    trimmed = 0
    for name, entry in cards.items():
        prints = grouped.get(name) or list(entry["set_printings"])
        if len(prints) > MAX_PRINTINGS_PER_CARD:
            trimmed += 1
            prints = sorted(prints, key=lambda p: _print_sort_key(p, set_code))
            prints = prints[:MAX_PRINTINGS_PER_CARD]
        entry["printings"] = prints
    if trimmed:
        log(f"  {trimmed} card(s) had more than {MAX_PRINTINGS_PER_CARD} printings; "
            "kept the set's own plus the most valuable.")

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
