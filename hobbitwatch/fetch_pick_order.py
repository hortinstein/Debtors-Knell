#!/usr/bin/env python3
"""
Fetch external "how good is this card" rankings for the Hobbit (HOB) set and
cache them alongside the Scryfall snapshot (pickorder/hob_pick_order.json.gz,
checked in -- same rationale as fetch_scryfall.py: small, changes rarely, and
committing it means the pool feature works with no network access).

Two sources, both scraped rather than called through a documented API
(neither publishes one for this data):

  Draftsim   https://draftsim.com/HOB-pick-order/ renders client-side from a
             React bundle, but that bundle inlines the entire ratings table
             as a literal template string (originally meant for the site's
             own bundler, not for us) -- a tab-separated block assigned to a
             minifier-chosen variable, found by first locating the "data/
             HOB.txt" -> <var> mapping the bundle itself uses, then pulling
             that variable's literal value. Ratings are Draftsim's usual 0-5
             limited scale.

  Untapped.gg  https://mtga.untapped.gg/limited/sealed/the-hobbit/pick-order
             is a Next.js page that *is* server-rendered -- the tier badges
             ("A+" down to "F") and card names are plain text in the fetched
             HTML, in on-page rank order, no JSON API needed.

Both are third-party pages outside this repo's control: selectors and bundle
internals can and will drift. Every parse step raises with a specific message
on the shape it expected rather than silently writing an empty/partial
ranking, and the two sources are fetched and written independently so one
breaking doesn't take out the other (main() below always writes whatever it
has, on the CI job's `always()` step, same pattern as hobbit-data.yml).

Run from CI (.github/workflows/hobbit-data.yml) or by hand:
    python3 hobbitwatch/fetch_pick_order.py
"""
import datetime
import gzip
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "pickorder", "hob_pick_order.json.gz")

SET_CODE = "HOB"
DRAFTSIM_PAGE = "https://draftsim.com/HOB-pick-order/"
UNTAPPED_PAGE = "https://mtga.untapped.gg/limited/sealed/the-hobbit/pick-order?eventType=SEALED"

HEADERS = {
    "User-Agent": "DebtorsKnellHobbitWatch/1.0 (+https://github.com/hortinstein/Debtors-Knell; "
                  "fetches public pick-order rankings for a personal price tracker)",
    "Accept": "text/html,application/xhtml+xml",
}


def log(msg):
    print(msg, flush=True)


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# Draftsim: ratings table inlined in the draft-app JS bundle
# ---------------------------------------------------------------------------

def fetch_draftsim(set_code=SET_CODE):
    html = _get(DRAFTSIM_PAGE)
    m = re.search(r'<script[^>]+type="module"[^>]+src="(/draft-app/dist/assets/index-[^"]+\.js)"', html)
    if not m:
        raise RuntimeError("draftsim: couldn't find the draft-app bundle <script> tag -- "
                            "page layout may have changed.")
    bundle_url = "https://draftsim.com" + m.group(1)
    js = _get(bundle_url)

    # The bundle maps every set's ratings file to a minifier-chosen variable:
    # `{"../../../data/HOB.txt": L0, ...}`. Find HOB's variable name rather
    # than assuming it (it changes every time the bundle is rebuilt).
    var_m = re.search(r'"\.\./\.\./\.\./data/' + re.escape(set_code) + r'\.txt"\s*:\s*([A-Za-z_$][\w$]*)', js)
    if not var_m:
        raise RuntimeError(f"draftsim: no data/{set_code}.txt entry in the bundle's asset map -- "
                            "set code may not be live there yet, or the bundle's format changed.")
    var_name = var_m.group(1)

    # That variable is assigned a backtick template literal earlier in the
    # same bundle: `<var>=\`Name\tName_2\t...\n...\``. The ratings data has
    # no backticks in it, so the next backtick after the opening one closes it.
    assign_m = re.search(re.escape(var_name) + r"=`", js)
    if not assign_m:
        raise RuntimeError(f"draftsim: found variable {var_name!r} in the asset map but not its "
                            "template-literal assignment -- bundle format may have changed.")
    start = assign_m.end()
    end = js.find("`", start)
    if end == -1:
        raise RuntimeError("draftsim: ratings template literal never closes -- bundle format may "
                            "have changed.")
    tsv = js[start:end]

    lines = tsv.strip("\n").split("\n")
    if len(lines) < 2:
        raise RuntimeError("draftsim: ratings block is empty.")
    header = lines[0].split("\t")
    try:
        name_i = header.index("Name")
        rating_i = header.index("Rating")
    except ValueError:
        raise RuntimeError(f"draftsim: expected Name/Rating columns, got {header!r}.")
    rarity_i = header.index("Rarity") if "Rarity" in header else None
    list_i = header.index("List") if "List" in header else None
    archetype_i = header.index("Archetype") if "Archetype" in header else None

    cards = {}
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(name_i, rating_i):
            continue
        name = cols[name_i].replace("_", " ").strip()
        if not name:
            continue
        raw_rating = cols[rating_i].strip()
        rating = float(raw_rating) if raw_rating else None
        tags = []
        for i in (list_i, archetype_i):
            if i is not None and i < len(cols) and cols[i].strip():
                tags.append(cols[i].strip())
        cards[name] = {
            "rating": rating,
            "rarity": cols[rarity_i].strip() if rarity_i is not None and rarity_i < len(cols) else None,
            "tags": tags,
        }

    # Rank by rating desc, original (roughly set/color) order as a tiebreak --
    # Draftsim doesn't publish an explicit rank, only the rating.
    ordered = sorted(
        (n for n in cards if cards[n]["rating"] is not None),
        key=lambda n: -cards[n]["rating"],
    )
    for i, name in enumerate(ordered, 1):
        cards[name]["rank"] = i
    for name in cards:
        cards[name].setdefault("rank", None)

    log(f"draftsim: {len(cards)} card ratings ({len(ordered)} ranked).")
    return {"url": DRAFTSIM_PAGE, "bundle": bundle_url, "cards": cards}


# ---------------------------------------------------------------------------
# Untapped.gg: server-rendered tier grid, plain text in the HTML
# ---------------------------------------------------------------------------

_UNTAPPED_TIER_RE = re.compile(
    r'<span class="sc-64e5e909-0 lilJvV">Tier</span>'
    r'<span class="sc-779b2592-5 biVkxt">([A-F])(?:<span class="sc-779b2592-6 bnNPGq">([+-])</span>)?</span>'
)
_UNTAPPED_CARD_RE = re.compile(r'includingCards=(\d+)[^>]*>.*?alt="([^"]+)"', re.S)


def fetch_untapped():
    import html as htmlmod

    html = _get(UNTAPPED_PAGE)
    events = []
    for m in _UNTAPPED_TIER_RE.finditer(html):
        events.append((m.start(), "tier", m.group(1) + (m.group(2) or "")))
    for m in _UNTAPPED_CARD_RE.finditer(html):
        events.append((m.start(), "card", (m.group(1), htmlmod.unescape(m.group(2)))))
    if not events:
        raise RuntimeError("untapped.gg: no tier badges or card entries found in the page -- "
                            "layout may have changed.")
    events.sort(key=lambda e: e[0])

    cur_tier = None
    cards = {}
    rank = 0
    for _, kind, val in events:
        if kind == "tier":
            cur_tier = val
        else:
            _title_id, name = val
            if name in cards:
                continue  # the page repeats a card if it appears in more than one gallery
            rank += 1
            cards[name] = {"tier": cur_tier, "rank": rank}

    if not cards:
        raise RuntimeError("untapped.gg: tier badges found but no card entries followed them.")
    log(f"untapped.gg: {len(cards)} ranked cards across "
        f"{len(set(c['tier'] for c in cards.values()))} tiers.")
    return {"url": UNTAPPED_PAGE, "cards": cards}


# ---------------------------------------------------------------------------

def main():
    payload = {"generated": datetime.date.today().isoformat()}

    try:
        payload["draftsim"] = fetch_draftsim()
    except Exception as e:
        log(f"[warn] draftsim fetch failed, leaving it out: {e}")

    try:
        payload["untapped"] = fetch_untapped()
    except Exception as e:
        log(f"[warn] untapped.gg fetch failed, leaving it out: {e}")

    if "draftsim" not in payload and "untapped" not in payload:
        log("Both sources failed -- not overwriting the existing snapshot.")
        return 1

    # A source that failed this run keeps its last successful snapshot rather
    # than disappearing from the page until the next run succeeds.
    if os.path.exists(OUT_PATH):
        with gzip.open(OUT_PATH, "rt", encoding="utf-8") as f:
            previous = json.load(f)
        for key in ("draftsim", "untapped"):
            payload.setdefault(key, previous.get(key))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    log(f"Wrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e3:.1f} KB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
