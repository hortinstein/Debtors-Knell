#!/usr/bin/env python3
"""
Flask webapp for browsing the "Building on a Budget" archive (317 Magic:
the Gathering deckbuilding articles, 2003-2009).

Reads directly from ../archive/<folder>/ -- article.md, decklist*.txt, and
decklist*_priced.md (physical USD + MTGO tix pricing). Never writes to the
archive.

Run:
    cd webapp
    pip install -r requirements.txt
    python3 app.py
    # or: flask --app app run
"""
import functools
import glob
import io
import json
import os
import re
import sys
import unicodedata
from urllib.parse import quote

import markdown
from flask import Flask, Response, abort, render_template, url_for
from markupsafe import Markup, escape
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
MASTER_INDEX_PATH = os.path.join(REPO_ROOT, "scripts", "master_index.json")
DECK_ARCHETYPES_PATH = os.path.join(REPO_ROOT, "scripts", "deck_archetypes.json")
DECK_META_PATH = os.path.join(REPO_ROOT, "scripts", "deck_meta.json")
MOVERS_DIR = os.path.join(REPO_ROOT, "prices", "movers")


def _ensure_price_histories():
    """decklist*_price_history.json sidecars are generated, gitignored files
    (see scripts/build_price_history.py), not checked into the repo -- build
    whatever's missing so a fresh checkout/deploy has them without a manual
    step. They're rebuilt in full (force=True) whenever
    scripts/fetch_prices.py pulls a new day of price data; this startup pass
    only fills in gaps (force=False), so it's a no-op once that's happened."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        import build_price_history
        written = build_price_history.build_price_histories(force=False, quiet=True)
        if written:
            print(f"[startup] built {written} missing deck price-history sidecar(s)", flush=True)
    except Exception as e:
        print(f"[startup] price-history build skipped: {e}", flush=True)


def _ensure_card_histories():
    """prices/card_history.json.gz is the per-card equivalent of those
    sidecars (see scripts/build_card_history.py) -- what the card pages and
    the card modal chart. Also generated and gitignored, and built here when
    it's missing so a fresh checkout/deploy has it without a manual step; the
    daily price fetch rebuilds it in full."""
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        import build_card_history
        cards = build_card_history.build_card_histories(force=False, quiet=True)
        if cards:
            print(f"[startup] built per-card price history for {cards:,} cards", flush=True)
    except Exception as e:
        print(f"[startup] card price-history build skipped: {e}", flush=True)


_ensure_price_histories()
_ensure_card_histories()

# Fixed vocabulary, in the order shown in the index-page filter dropdown.
ARCHETYPE_LIST = [
    "Aggro", "Midrange", "Control", "Combo", "Tempo", "Ramp", "Burn", "Mill",
    "Reanimator", "Tribal", "Stax/Prison", "Aristocrats", "Artifact", "Toolbox",
    "Lifegain", "Discard",
]

# A decklist's role, from scripts/build_deck_meta.py: "budget" is one of the
# column's own budget builds, everything else is a list the article only
# quotes for comparison (a tournament deck, a reader submission, the
# preconstructed deck it starts from, an explicitly non-budget build). The
# card pool and card stats pages default to budget builds alone so cards that
# were never in a budget deck -- Black Lotus, the Moxen, dual lands -- don't
# turn up in a shopping list.
ROLE_BUDGET = "budget"
ROLE_LABELS = {
    "budget": "Budget build",
    "pro": "Tournament list",
    "reader": "Reader submission",
    "precon": "Preconstructed deck",
    "nonbudget": "Non-budget build",
}

# WUBRG order, used both for sorting a deck's color list and for the
# filter-bar pip order on the index page.
COLOR_ORDER = "WUBRG"
BASIC_LANDS = {"island", "plains", "swamp", "mountain", "forest", "wastes"}
BASIC_LAND_COLOR = {
    "plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G",
}

MD_EXTENSIONS = ["tables", "sane_lists", "nl2br"]

GRAND_TOTAL_RE = re.compile(r"\*\*Grand total:\s*\$([\d,]+\.\d+)\*\*")
GRAND_TOTAL_TIX_RE = re.compile(r"\*\*Grand total \(digital\):\s*([\d,]+\.\d+)\s*tix\*\*")
DECK_NUM_RE = re.compile(r"decklist_(\d+)_")

# Matches one card row in a decklist*_priced.md table:
# | Qty | Card | Unit Price | Extended | Tix | Extended (tix) | Scryfall |
PRICE_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*"
    r"([\d.]+|N/A)\s*\|\s*([\d.]+|N/A)\s*\|\s*(.*?)\s*\|\s*$"
)
FUZZY_NOTE_RE = re.compile(r"->\s*(.+)$")
LINK_URI_RE = re.compile(r"\[link\]\(([^)]+)\)")
SCRYFALL_CARD_LINK_RE = re.compile(r"scryfall\.com/card/([^/?#]+)/([^/?#]+)/")

app = Flask(__name__)
app.jinja_env.filters["money"] = lambda v: f"{v:,.2f}"

# Google Analytics 4 measurement ID ("G-XXXXXXXXXX"), read from the
# environment so nothing site-specific is checked in: the GitHub Pages build
# passes the repository's GA_MEASUREMENT_ID variable through to the freeze
# (see .github/workflows/deploy-pages.yml), which bakes the tag into every
# frozen page. Unset -- a local dev run, or a fork that hasn't configured one
# -- and base.html emits no tracking snippet at all.
#
# Anything that isn't a plain measurement ID is ignored rather than injected
# into the page's <script> tags.
GA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "").strip()
if GA_MEASUREMENT_ID and not GA_ID_RE.match(GA_MEASUREMENT_ID):
    print(f"[startup] ignoring malformed GA_MEASUREMENT_ID {GA_MEASUREMENT_ID!r}", flush=True)
    GA_MEASUREMENT_ID = ""


@app.context_processor
def inject_analytics():
    return {"ga_measurement_id": GA_MEASUREMENT_ID}


@app.context_processor
def inject_card_link():
    # Templates render card names through this so every one of them is a link
    # to the card's page (and so opens the card modal) -- see card_link_html.
    return {"card_link": card_link_html, "card_url": card_url_for_name}


def _parse_money(s):
    if s == "N/A":
        return None
    return float(s.lstrip("$").replace(",", ""))


def _canonical_card_name(name_cell):
    """A priced-table 'Card' cell may carry a trailing '<sub>(note)</sub>'
    annotation, e.g. '4 Vithian Renegade <sub>(matched via fuzzy(97) ->
    Vithian Renegades)</sub>'. Resolve it to the real (fuzzy-matched) card
    name so the same card is recognized across decks even when different
    articles spelled/typo'd it slightly differently."""
    sub_m = re.search(r"<sub>\((.*)\)</sub>\s*$", name_cell)
    base_name = re.sub(r"\s*<sub>.*</sub>\s*$", "", name_cell).strip()
    if sub_m:
        fm = FUZZY_NOTE_RE.search(sub_m.group(1))
        if fm:
            return fm.group(1).strip()
    return base_name


def parse_priced_card_rows(priced_md_path):
    """Parse every card row (Main Deck + Sideboard) out of a decklist*_priced.md
    file. Returns a list of dicts: qty, name (canonical), unit_usd, ext_usd,
    unit_tix, ext_tix (None where N/A), uri (Scryfall page link, or None)."""
    rows = []
    with open(priced_md_path, encoding="utf-8") as f:
        for line in f:
            m = PRICE_ROW_RE.match(line.rstrip("\n"))
            if not m:
                continue
            qty = int(m.group(1))
            link_m = LINK_URI_RE.search(m.group(7))
            rows.append({
                "qty": qty,
                "name": _canonical_card_name(m.group(2)),
                "unit_usd": _parse_money(m.group(3)),
                "ext_usd": _parse_money(m.group(4)),
                "unit_tix": None if m.group(5) == "N/A" else float(m.group(5)),
                "ext_tix": None if m.group(6) == "N/A" else float(m.group(6)),
                "uri": link_m.group(1) if link_m else None,
            })
    return rows


def _scryfall_image_url(page_uri, version="normal"):
    """Turn a scryfall.com/card/<set>/<number>/<slug> page link into a
    hotlinkable card-image URL via Scryfall's API image redirect -- the same
    URL pattern webapp/static/card-preview.js uses for its hover preview."""
    if not page_uri:
        return None
    m = SCRYFALL_CARD_LINK_RE.search(page_uri)
    if not m:
        return None
    set_code, number = m.group(1), m.group(2)
    return f"https://api.scryfall.com/cards/{set_code}/{number}?format=image&version={version}"


def _colors_for_priced_files(priced_files):
    """A deck's color identity, inferred from which colored basic lands it
    runs (Island -> U, Forest -> G, ...). These budget-era decklists are
    reliably basic-land-heavy -- title-checked against a sample (e.g.
    "Blue-Green Threshold" runs Island+Forest, "Red-Green Burn" runs
    Mountain+Forest) -- so this needs no external card-color data source.
    Colorless-only decks (no colored basics) come back as []. Returns a
    WUBRG-ordered list, unioned across every deck in a multi-deck article."""
    colors = set()
    for pf in priced_files:
        for r in parse_priced_card_rows(pf):
            if r["qty"] <= 0:
                continue
            c = BASIC_LAND_COLOR.get(r["name"].lower())
            if c:
                colors.add(c)
    return sorted(colors, key=COLOR_ORDER.index)


def _thumbnail_for_article(title, priced_files):
    """A representative card-image thumbnail for the index page: prefer the
    titular card -- a nonbasic card in the deck whose name is literally
    referenced in the article title (e.g. "Recycled Zombies" runs Zombie
    tribal, "ElfBall" is Elf tribal combo -- but plenty of titles, like
    "Squirrel Prison" or "You-Con-Du-It", aren't literal card names at all).
    Falls back to the single most expensive nonbasic card in the deck, a
    reasonable proxy for "the deck's signature card."

    Returns (image_url, card_name) -- the name so the index page can link the
    thumbnail to that card, which is the only card the index names at all --
    or (None, None) if the deck has no priced nonbasic cards."""
    by_name = {}
    for pf in priced_files:
        for r in parse_priced_card_rows(pf):
            key = r["name"].lower()
            if key in BASIC_LANDS:
                continue
            by_name.setdefault(key, r)
    if not by_name:
        return None, None

    title_lower = title.lower()
    best_match = None
    for name_lower, r in by_name.items():
        if len(name_lower) < 4:
            continue  # too short to safely word-match against the title
        if re.search(r"\b" + re.escape(name_lower) + r"\b", title_lower):
            if best_match is None or len(name_lower) > len(best_match[0]):
                best_match = (name_lower, r)

    if best_match is not None:
        chosen = best_match[1]
    else:
        priced = [r for r in by_name.values() if r["unit_usd"] is not None]
        chosen = max(priced, key=lambda r: r["unit_usd"]) if priced else next(iter(by_name.values()))

    return _scryfall_image_url(chosen["uri"], version="art_crop"), chosen["name"]


# ---------------------------------------------------------------------------
# Archive scanning / caching
# ---------------------------------------------------------------------------

def _decklist_sort_key(path):
    """Natural-ish sort: decklist.txt first, then decklist_1_*, decklist_2_*,
    ..., decklist_10_* in numeric (not lexicographic) order."""
    base = os.path.basename(path)
    m = DECK_NUM_RE.match(base)
    if m:
        return (int(m.group(1)), base)
    return (0, base)


def _priced_files_for(folder_path):
    return sorted(
        glob.glob(os.path.join(folder_path, "decklist*_priced.md")),
        key=_decklist_sort_key,
    )


def _deck_entries_for(folder, drop_duplicates=True):
    """Every priced decklist in an article, in page order, annotated with the
    metadata scripts/build_deck_meta.py recovered: real title, subtitle,
    role, and whether it is a byte-identical repeat of an earlier decklist in
    the same article (a couple of articles reprint last week's deck as a
    recap, and the scraper saved it twice)."""
    entries = []
    for pf in _priced_files_for(os.path.join(ARCHIVE_DIR, folder)):
        meta = _deck_meta_for(folder, pf)
        role = meta.get("role") or ROLE_BUDGET
        duplicate_of = meta.get("duplicate_of")
        if drop_duplicates and duplicate_of:
            continue
        entries.append({
            "priced_path": pf,
            "label": meta.get("title") or _label_from_filename(pf),
            "subtitle": meta.get("subtitle") or "",
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "duplicate_of": duplicate_of,
        })
    return entries


def _price_history_for(priced_md_path):
    """Load the sidecar decklist*_price_history.json for a priced decklist,
    if the build_price_history.py script has been run for it."""
    history_path = priced_md_path[: -len("_priced.md")] + "_price_history.json"
    if not os.path.exists(history_path):
        return {"tix": [], "usd": [], "unmatched_cards": []}
    with open(history_path, encoding="utf-8") as f:
        return json.load(f)


def _raw_decklist_files_for(folder_path):
    return sorted(
        (p for p in glob.glob(os.path.join(folder_path, "decklist*.txt"))),
        key=_decklist_sort_key,
    )


CARD_LINE_RE = re.compile(r"^\d+\s+\S")


def build_mtgo_import_text(raw_text):
    """Turn a scraped decklist*.txt into a file MTGO's plain-text importer
    can actually parse (qty + card name lines, at most one blank line
    separating maindeck from sideboard).

    The scraped files carry a few artifacts from the original article HTML
    that a strict "<qty> <name>" line parser chokes on: a deck-title line
    before the cards, a literal "Sideboard" label line (redundant - the
    blank line already marks the split), category sub-headers like "Land:"
    / "Creatures:" / "Spells:", and (one file) old Mac-style \\r-only line
    endings that leave the whole decklist as a single line.

    Fix, verified against every decklist*.txt in the archive: drop any
    non-blank line that isn't "<qty> <name>". If more than one blank-line
    gap remains after that, it's category dividers (verified by hand: every
    file with a real sideboard has a literal "Sideboard" line and collapses
    to exactly one gap once that label is dropped; every multi-gap file has
    no such label) - collapse those into a single contiguous maindeck.
    Exactly one remaining gap is a genuine maindeck/sideboard split and is
    preserved.
    """
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if s == "" or CARD_LINE_RE.match(s):
            kept.append(s)
    while kept and kept[0] == "":
        kept.pop(0)
    while kept and kept[-1] == "":
        kept.pop()

    gaps = 0
    prev_blank = False
    for line in kept:
        if line == "":
            if not prev_blank:
                gaps += 1
            prev_blank = True
        else:
            prev_blank = False

    if gaps <= 1:
        lines = kept
    else:
        lines = [l for l in kept if l != ""]

    return "\n".join(lines) + "\n"


def _raw_card_count(raw_txt_path):
    """Total copies in a scraped decklist*.txt, or None if it isn't there."""
    if not os.path.exists(raw_txt_path):
        return None
    total = 0
    with open(raw_txt_path, encoding="utf-8") as f:
        for line in f.read().replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            m = re.match(r"^(\d+)\s+\S", line.strip())
            if m:
                total += int(m.group(1))
    return total


def _priced_card_count(priced_md_path):
    return sum(r["qty"] for r in parse_priced_card_rows(priced_md_path))


def _parse_grand_totals(priced_md_path):
    """Pull the physical (USD) and digital (tix) grand totals out of a
    decklist*_priced.md file's trailing summary lines."""
    with open(priced_md_path, encoding="utf-8") as f:
        text = f.read()
    usd = 0.0
    tix = 0.0
    m = GRAND_TOTAL_RE.search(text)
    if m:
        usd = float(m.group(1).replace(",", ""))
    m = GRAND_TOTAL_TIX_RE.search(text)
    if m:
        tix = float(m.group(1).replace(",", ""))
    return usd, tix


_DECK_META_CACHE = None


def get_deck_meta():
    """folder -> {"record": {...}, "decks": [{"file", "title", "subtitle",
    "role", "duplicate_of"}, ...]}, built by scripts/build_deck_meta.py from
    the archived source.html and article text. Missing entries are fine --
    everything that reads this falls back to filename-derived values."""
    global _DECK_META_CACHE
    if _DECK_META_CACHE is None:
        if os.path.exists(DECK_META_PATH):
            with open(DECK_META_PATH, encoding="utf-8") as f:
                _DECK_META_CACHE = json.load(f)
        else:
            _DECK_META_CACHE = {}
    return _DECK_META_CACHE


def _raw_filename_for(priced_md_path):
    """decklist_1_Nether_Go_priced.md -> decklist_1_Nether_Go.txt, the key
    scripts/deck_meta.json uses for a decklist."""
    return os.path.basename(priced_md_path)[: -len("_priced.md")] + ".txt"


def _deck_meta_for(folder, priced_md_path):
    raw_filename = _raw_filename_for(priced_md_path)
    for d in get_deck_meta().get(folder, {}).get("decks", []):
        if d["file"] == raw_filename:
            return d
    return {}


def _label_from_filename(priced_md_path):
    """Fallback deck name derived from a priced-decklist filename, e.g.
    decklist_1_Nether_Go_priced.md -> 'Nether Go'. Lossy -- the filenames
    were slugified at scrape time, so punctuation is gone ('Ninjutsu v.1.0'
    became 'Ninjutsu_v10') -- which is why _deck_entries_for prefers the real
    title recovered into scripts/deck_meta.json."""
    base = os.path.basename(priced_md_path)
    base = base[: -len("_priced.md")]
    base = re.sub(r"^decklist(?:_\d+)?_?", "", base)
    base = base.replace("_", " ").strip()
    return base or "Decklist"


def _first_paragraph(article_text, max_len=230):
    """Pull the first substantial prose paragraph out of an article.md file
    (after the title/author/date front-matter block), stripped of markdown
    markup, truncated to ~max_len chars on a word boundary."""
    parts = article_text.split("\n---\n", 1)
    body = parts[1] if len(parts) > 1 else article_text
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", block)          # images
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # links
        text = re.sub(r"[*_`>#]", "", text)                          # md markup
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            continue
        if len(text) > max_len:
            text = text[:max_len].rsplit(" ", 1)[0].rstrip(",.;:") + "..."
        return text
    return ""


_DECK_ARCHETYPES_CACHE = None


def get_deck_archetypes():
    """folder -> {"description": <=30-word strategy summary, "archetypes":
    [tag, ...]}, curated once per article by reading the article text and
    decklist (see scripts/deck_archetypes.json). Articles with no decklist
    aren't covered."""
    global _DECK_ARCHETYPES_CACHE
    if _DECK_ARCHETYPES_CACHE is None:
        if os.path.exists(DECK_ARCHETYPES_PATH):
            with open(DECK_ARCHETYPES_PATH, encoding="utf-8") as f:
                _DECK_ARCHETYPES_CACHE = json.load(f)
        else:
            _DECK_ARCHETYPES_CACHE = {}
    return _DECK_ARCHETYPES_CACHE


def _load_article_entry(meta):
    folder = meta["folder"]
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    article_path = os.path.join(folder_path, "article.md")

    rerun = get_deck_meta().get(folder, {}).get("rerun")
    archetype_info = get_deck_archetypes().get(folder)
    archetypes = archetype_info["archetypes"] if archetype_info else []
    if archetype_info:
        description = archetype_info["description"]
        # The curated description opens with the marker the rerun flag was
        # derived from ("(Holiday rerun) ..."); the badge says that now, so
        # drop it rather than printing it twice.
        if rerun:
            description = re.sub(r"^\([^)]*\)\s*", "", description)
    else:
        description = ""
        if os.path.exists(article_path):
            with open(article_path, encoding="utf-8") as f:
                description = _first_paragraph(f.read())

    all_entries = _deck_entries_for(folder)
    # Prices, colors and the thumbnail describe the article's own budget
    # builds. Folding in a quoted Pro Tour or vintage list would put dual
    # lands in the color identity and, in one case, a $3,000 Black Lotus in
    # the "how much does this week's deck cost" column.
    budget_entries = [e for e in all_entries if e["role"] == ROLE_BUDGET]
    priced_entries = budget_entries or all_entries
    priced_files = [e["priced_path"] for e in priced_entries]

    # The index quotes the article's *first* decklist, not the sum of all of
    # them. Many articles iterate one deck over three or four versions, and
    # summing those answered a question nobody asked ("what would every
    # revision cost at once?") while making an article look several times
    # more expensive than the deck it was actually about.
    first_deck = priced_entries[0] if priced_entries else None
    usd_total, tix_total = _parse_grand_totals(first_deck["priced_path"]) if first_deck else (0.0, 0.0)
    colors = _colors_for_priced_files(priced_files)
    title = meta.get("title") or folder
    thumbnail, thumbnail_card = (
        _thumbnail_for_article(title, priced_files) if priced_files else (None, None)
    )

    return {
        "folder": folder,
        "title": title,
        "author": meta.get("author") or "",
        "date_str": meta.get("date_str") or "",
        "ymd": meta.get("ymd") or "",
        "description": description,
        "archetypes": archetypes,
        "colors": colors,
        "thumbnail": thumbnail,
        "thumbnail_card": thumbnail_card,
        "has_decklist": bool(all_entries),
        "num_decks": len(priced_entries),
        "first_deck_label": first_deck["label"] if first_deck else "",
        "num_reference_decks": len(all_entries) - len(budget_entries),
        "rerun": rerun,
        "usd_total": usd_total,
        "tix_total": tix_total,
        "record": get_deck_meta().get(folder, {}).get("record"),
    }


_ARTICLES_CACHE = None


def get_articles():
    """All 317 articles with description + summed price totals, built once
    and cached in memory (the archive is static content)."""
    global _ARTICLES_CACHE
    if _ARTICLES_CACHE is None:
        with open(MASTER_INDEX_PATH, encoding="utf-8") as f:
            master = json.load(f)
        articles = [_load_article_entry(m) for m in master]
        articles.sort(key=lambda a: (a["ymd"], a["title"]))
        _ARTICLES_CACHE = articles
    return _ARTICLES_CACHE


def get_article_by_folder(folder):
    for a in get_articles():
        if a["folder"] == folder:
            return a
    return None


_COLUMN_PREFIX_RE = re.compile(r"^buildingonabudget")


def _label_adds_nothing(article_title, label):
    """True when a decklist's own title just restates the article's, which is
    the norm for a single-deck article ("Building on a Budget - ElfBall" ->
    "Building on a Budget - Elf-Ball"). Lists it apart from the article --
    "Wild Pair 2.0", "Expensive Wurms!" -- are what a deck label is for."""
    def norm(s):
        return _COLUMN_PREFIX_RE.sub("", re.sub(r"[^a-z0-9]+", "", s.lower()))
    a, b = norm(article_title), norm(label)
    if not b:
        return True
    return a == b or a in b or b in a


def deck_id(folder, priced_md_path):
    """Stable id for one individual priced decklist, e.g.
    '20090527_Shamans_Trounce_2009::decklist'."""
    base = os.path.basename(priced_md_path)[: -len("_priced.md")]
    return f"{folder}::{base}"


_ALL_DECKS_CACHE = None


def get_all_decks():
    """One entry per individual priced decklist across the whole archive
    (a multi-deck article contributes one entry per deck), used by the card
    pool builder and the stats page."""
    global _ALL_DECKS_CACHE
    if _ALL_DECKS_CACHE is None:
        decks = []
        for article in get_articles():
            # A rerun reprints an earlier article's decks verbatim. Its own
            # page still shows them, but listing them again here would put the
            # same deck in the card pool twice and make one deck count as two
            # in the card stats.
            if article["rerun"]:
                continue
            for entry in _deck_entries_for(article["folder"]):
                pf = entry["priced_path"]
                usd, tix = _parse_grand_totals(pf)
                decks.append({
                    "id": deck_id(article["folder"], pf),
                    "folder": article["folder"],
                    "priced_path": pf,
                    "article_title": article["title"],
                    "label": entry["label"],
                    "show_label": not _label_adds_nothing(article["title"], entry["label"]),
                    "subtitle": entry["subtitle"],
                    "role": entry["role"],
                    "role_label": entry["role_label"],
                    "date_str": article["date_str"],
                    "ymd": article["ymd"],
                    "usd_total": usd,
                    "tix_total": tix,
                })
        decks.sort(key=lambda d: (d["ymd"], d["article_title"], d["label"]))
        _ALL_DECKS_CACHE = decks
    return _ALL_DECKS_CACHE


_CARD_STATS_CACHE = None


def get_card_stats():
    """Aggregate every card across every deck in the archive: which decks
    it appears in, how many copies total, and how many distinct decks
    (the "how common is this card" ranking).

    Counts are split by deck role: num_budget_decks covers the column's own
    budget builds, num_reference_decks the tournament/reader/precon/non-budget
    lists the articles only quote."""
    global _CARD_STATS_CACHE
    if _CARD_STATS_CACHE is None:
        by_name = {}
        for d in get_all_decks():
            is_budget = d["role"] == ROLE_BUDGET
            # A card can have two rows in one decklist (main deck and
            # sideboard); its copies add up but it is still one deck.
            counted_here = set()
            for r in parse_priced_card_rows(d["priced_path"]):
                name = r["name"]
                entry = by_name.setdefault(name, {
                    "name": name,
                    "is_basic_land": name.lower() in BASIC_LANDS,
                    "total_qty": 0,
                    "budget_qty": 0,
                    "num_decks": 0,
                    "num_budget_decks": 0,
                    "num_reference_decks": 0,
                    "unit_usd": None,
                    "unit_tix": None,
                    "uri": None,
                    "decks": [],
                })
                entry["total_qty"] += r["qty"]
                if is_budget:
                    entry["budget_qty"] += r["qty"]
                if entry["unit_usd"] is None and r["unit_usd"] is not None:
                    entry["unit_usd"] = r["unit_usd"]
                if entry["unit_tix"] is None and r["unit_tix"] is not None:
                    entry["unit_tix"] = r["unit_tix"]
                if entry["uri"] is None and r["uri"]:
                    entry["uri"] = r["uri"]
                if name not in counted_here:
                    counted_here.add(name)
                    entry["num_decks"] += 1
                    if is_budget:
                        entry["num_budget_decks"] += 1
                    else:
                        entry["num_reference_decks"] += 1
                    entry["decks"].append({
                        "id": d["id"],
                        "folder": d["folder"],
                        "title": d["article_title"],
                        "label": d["label"] if d["show_label"] else "",
                        "role": d["role"],
                        "role_label": d["role_label"],
                        "date_str": d["date_str"],
                        "ymd": d["ymd"],
                        "qty": r["qty"],
                    })
        stats = list(by_name.values())
        stats.sort(key=lambda c: (-c["num_budget_decks"], -c["num_decks"],
                                  -c["total_qty"], c["name"].lower()))
        # A name-derived anchor id, so the "most common cards" chart can link
        # to a card's row in the full table. (Row-number ids don't work: the
        # chart is ranked over nonbasic cards only, so its Nth bar is not the
        # table's Nth row.) Numbered on collision, which non-ASCII names can
        # cause -- "AEther Burst" and "Æther Burst" both slugify to
        # "aether-burst".
        slug_counts = {}
        for c in stats:
            slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-") or "card"
            n = slug_counts.get(slug, 0) + 1
            slug_counts[slug] = n
            c["slug"] = slug if n == 1 else f"{slug}-{n}"
        _CARD_STATS_CACHE = stats
    return _CARD_STATS_CACHE


# ---------------------------------------------------------------------------
# The card index: one entry per card, behind /card/<slug>/ and its modal
# ---------------------------------------------------------------------------

def card_slug(name):
    """A card's URL slug, derived from its name alone so the browser can work
    one out for a name it only knows as text (see webapp/static/card-modal.js,
    which mirrors this) rather than needing a lookup table shipped to it.

    Accents and the AE ligature fold to ASCII, which also merges the two
    spellings of the same card the archive's articles disagree about --
    "AEther Spellbomb" and "Aether Spellbomb" are one card and get one page."""
    folded = name.replace("\u00c6", "AE").replace("\u00e6", "ae")
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-") or "card"


_CARD_INDEX_CACHE = None


def get_card_index():
    """slug -> one card, merging any card-stats rows that share a slug (the
    alternate spellings card_slug() folds together). Deck appearances are
    pooled and re-sorted by date, so the card page reads as one history of
    where the card turns up in the column."""
    global _CARD_INDEX_CACHE
    if _CARD_INDEX_CACHE is None:
        index = {}
        for c in get_card_stats():
            slug = card_slug(c["name"])
            entry = index.get(slug)
            if entry is None:
                entry = {
                    "slug": slug,
                    "name": c["name"],
                    "names": [],
                    "is_basic_land": c["is_basic_land"],
                    "total_qty": 0,
                    "budget_qty": 0,
                    "num_decks": 0,
                    "num_budget_decks": 0,
                    "unit_usd": None,
                    "unit_tix": None,
                    "uri": None,
                    "decks": [],
                }
                index[slug] = entry
            entry["names"].append(c["name"])
            entry["total_qty"] += c["total_qty"]
            entry["budget_qty"] += c["budget_qty"]
            entry["num_decks"] += c["num_decks"]
            entry["num_budget_decks"] += c["num_budget_decks"]
            if entry["unit_usd"] is None:
                entry["unit_usd"] = c["unit_usd"]
            if entry["unit_tix"] is None:
                entry["unit_tix"] = c["unit_tix"]
            if entry["uri"] is None:
                entry["uri"] = c["uri"]
            entry["decks"].extend(c["decks"])
        for entry in index.values():
            entry["decks"].sort(key=lambda d: (d["ymd"], d["title"], d["label"]))
        _CARD_INDEX_CACHE = index
    return _CARD_INDEX_CACHE


_CARD_HISTORY_CACHE = None


def get_card_histories():
    """prices/card_history.json.gz, loaded once. An empty dict when the file
    hasn't been built (scripts/build_card_history.py) -- the card pages then
    simply say they have no price history rather than failing."""
    global _CARD_HISTORY_CACHE
    if _CARD_HISTORY_CACHE is None:
        sys.path.insert(0, SCRIPTS_DIR)
        try:
            import build_card_history
            _CARD_HISTORY_CACHE = build_card_history.load_card_histories() or {}
        except Exception as e:
            print(f"[cards] no per-card price history: {e}", flush=True)
            _CARD_HISTORY_CACHE = {}
    return _CARD_HISTORY_CACHE


def _history_for_card(entry):
    """The card's tix and USD price series as [[date, value], ...] pairs --
    the same shape the per-deck sidecars use, so the card page and the card
    modal can be drawn by the chart the deck pages already use
    (static/price-chart.js).

    prices/card_history.json.gz stores the values aligned to one shared date
    axis per market, with a null on days that card had no price; those days
    are dropped here. Merged spellings are tried in turn, since only the one
    the price sources recognize carries data."""
    data = get_card_histories()
    cards = data.get("cards") or {}

    def pairs(dates, values):
        return [[d, v] for d, v in zip(dates, values) if v is not None]

    tix, usd = [], []
    for name in entry["names"]:
        c = cards.get(name) or {}
        if not tix:
            tix = pairs(data.get("tix_dates") or [], c.get("tix") or [])
        if not usd:
            usd = pairs(data.get("usd_dates") or [], c.get("usd") or [])
    return {"tix": tix, "usd": usd}


def _card_image_url(entry, version="normal"):
    """The card image for the modal and the card page. The Scryfall page link
    the priced table already carries names an exact printing; a card the
    pricing pass never linked falls back to Scryfall's fuzzy name lookup."""
    url = _scryfall_image_url(entry["uri"], version=version)
    if url:
        return url
    return ("https://api.scryfall.com/cards/named?fuzzy=" + quote(entry["name"])
            + f"&format=image&version={version}")


def card_payload(entry):
    """Everything the card page and the card modal show, as plain JSON-able
    data.

    Each deck carries its folder and a URL written relative to
    /card/<slug>.json -- the file the modal fetches this from, which is what
    the modal resolves it against (see card-modal.js). Relative because the
    frozen site can be served from a GitHub Pages project subpath, where a
    root-relative "/deck/..." would miss; and written by hand because
    Frozen-Flask only rewrites the url_for calls made from templates, not the
    ones made here. The card page builds its own links from the folder."""
    return {
        "slug": entry["slug"],
        "name": entry["name"],
        "names": entry["names"],
        "image": _card_image_url(entry),
        "scryfall": entry["uri"],
        "is_basic_land": entry["is_basic_land"],
        "unit_usd": entry["unit_usd"],
        "unit_tix": entry["unit_tix"],
        "total_qty": entry["total_qty"],
        "budget_qty": entry["budget_qty"],
        "num_decks": entry["num_decks"],
        "num_budget_decks": entry["num_budget_decks"],
        "history": _history_for_card(entry),
        "decks": [{
            "title": d["title"],
            "label": d["label"],
            "role": d["role"],
            "role_label": d["role_label"],
            "date": d["date_str"] or d["ymd"],
            "qty": d["qty"],
            "folder": d["folder"],
            "url": f"../deck/{quote(d['folder'])}/",
        } for d in entry["decks"]],
    }


def page_url_for(endpoint, **values):
    """url_for as the build being served sees it.

    Frozen-Flask turns URLs relative by swapping the url_for that *templates*
    call (its patch_url_for), which does nothing for a URL built in Python
    like the card links below -- those came out as "/card/<slug>/" and missed
    every time the frozen site was served from a GitHub Pages project
    subpath. Going through the Jinja global picks that swap up during a
    freeze and is plain flask.url_for the rest of the time."""
    return app.jinja_env.globals.get("url_for", url_for)(endpoint, **values)


def card_url_for_name(name):
    """The card page for a card name, or None when the archive has no page
    for it. Templates use this where the thing being linked is not the name
    itself -- the index page's card-art thumbnail."""
    entry = get_card_index().get(card_slug(name))
    return page_url_for("card_detail", slug=entry["slug"]) if entry else None


def card_link_html(name, display=None):
    """The site-wide card link: an <a> to the card's page that
    static/card-modal.js opens as a modal instead of navigating, and that
    static/card-preview.js still shows the Scryfall hover image for."""
    text = escape(display if display is not None else name)
    entry = get_card_index().get(card_slug(name))
    if entry is None:
        return Markup(text)
    attrs = [
        'class="card-link"',
        f'href="{escape(page_url_for("card_detail", slug=entry["slug"]))}"',
        f'data-card="{escape(entry["name"])}"',
    ]
    if entry["uri"]:
        # The printing the priced table linked, so card-preview.js can still
        # show the Scryfall hover image over a name that now links here.
        attrs.append(f'data-scryfall="{escape(entry["uri"])}"')
    return Markup(f'<a {" ".join(attrs)}>{text}</a>')


def link_priced_card_names(priced_text):
    """Turn the Card column of a decklist*_priced.md table into card links,
    before the markdown is rendered.

    The cell keeps whatever the article spelled the card (and the
    "<sub>(matched via fuzzy...)</sub>" note explaining a correction), but the
    link points at the corrected card's page -- one page per card, however
    many ways the column typed its name over six years. The Scryfall column
    is left alone: it is what the hover preview reads."""
    out = []
    for line in priced_text.split("\n"):
        m = PRICE_ROW_RE.match(line)
        if not m:
            out.append(line)
            continue
        name_cell = m.group(2)
        sub_m = re.search(r"\s*<sub>.*</sub>\s*$", name_cell)
        display = name_cell[: sub_m.start()] if sub_m else name_cell
        annotation = name_cell[sub_m.start():] if sub_m else ""
        # str(): card_link_html hands back Markup, which would escape the
        # <sub> annotation being concatenated onto it -- but this is markdown
        # source being assembled, not a template's output.
        linked = str(card_link_html(_canonical_card_name(name_cell), display=display.strip()))
        out.append(line[: m.start(2)] + linked + annotation + line[m.end(2):])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    articles = get_articles()
    with_decklist = sum(1 for a in articles if a["has_decklist"])
    rerun_count = sum(1 for a in articles if a["rerun"])
    return render_template(
        "index.html",
        articles=articles,
        total_count=len(articles),
        with_decklist_count=with_decklist,
        with_record_count=sum(1 for a in articles if a["record"]),
        rerun_count=rerun_count,
        original_count=len(articles) - rerun_count,
        titles_by_folder={a["folder"]: a["title"] for a in articles},
        archetype_list=ARCHETYPE_LIST,
        color_list=list(COLOR_ORDER),
    )


@functools.lru_cache(maxsize=None)
def _screenshot_jpeg_bytes(folder_path):
    """The archived screenshots are full-page lossless PNGs (~1-2MB each,
    ~600MB total across the archive) of a "click to view the original page"
    thumbnail -- nowhere near needing lossless fidelity. Re-encoding to JPEG
    here (rather than serving the PNG as-is) keeps the frozen static site
    (staticsite/freeze.py) well under GitHub Pages' ~1GB site-size budget."""
    with Image.open(os.path.join(folder_path, "screenshot.png")) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()


@app.route("/screenshot/<folder>/screenshot.jpg")
def screenshot(folder):
    # Validate against the known folder list (not just os.path checks) so a
    # crafted folder value can't be used to walk outside archive/.
    if get_article_by_folder(folder) is None:
        abort(404)
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    if not os.path.exists(os.path.join(folder_path, "screenshot.png")):
        abort(404)
    return Response(_screenshot_jpeg_bytes(folder_path), mimetype="image/jpeg")


@app.route("/download/<folder>/<filename>")
def download_decklist(folder, filename):
    # Validate against the known folder list and that filename names an
    # actual decklist*.txt in that folder, so this can't be used to read
    # arbitrary files.
    article = get_article_by_folder(folder)
    if article is None:
        abort(404)
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    valid_names = {os.path.basename(p) for p in _raw_decklist_files_for(folder_path)}
    if filename not in valid_names:
        abort(404)

    with open(os.path.join(folder_path, filename), encoding="utf-8") as f:
        raw_text = f.read()
    mtgo_text = build_mtgo_import_text(raw_text)

    download_name = f"{folder}__{filename}"
    return Response(
        mtgo_text,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@app.route("/deck/<folder>/")
def deck_detail(folder):
    article = get_article_by_folder(folder)
    if article is None:
        abort(404)

    folder_path = os.path.join(ARCHIVE_DIR, folder)
    has_screenshot = os.path.exists(os.path.join(folder_path, "screenshot.png"))
    article_md_path = os.path.join(folder_path, "article.md")
    article_html = ""
    if os.path.exists(article_md_path):
        with open(article_md_path, encoding="utf-8") as f:
            article_html = markdown.markdown(f.read(), extensions=MD_EXTENSIONS)

    decks = []
    grand_usd = 0.0
    grand_tix = 0.0
    for entry in _deck_entries_for(folder):
        pf = entry["priced_path"]
        with open(pf, encoding="utf-8") as f:
            priced_text = f.read()
        # Drop the file's own "# Priced Decklist: <raw_filename>" heading and
        # source line -- the template already shows a nicer deck label and
        # the filename, and repeating the underscored raw title looks noisy.
        priced_text = re.sub(r"^# Priced Decklist:.*\n\n?", "", priced_text)
        priced_text = re.sub(r"^\*Source:.*\*\n\n?", "", priced_text)
        priced_text = link_priced_card_names(priced_text)
        usd, tix = _parse_grand_totals(pf)
        # The combined total is what it costs to build this week's decks, so
        # it counts the budget builds only -- see _load_article_entry.
        if entry["role"] == ROLE_BUDGET:
            grand_usd += usd
            grand_tix += tix
        # decklist*.txt and decklist*_priced.md are built by separate scripts,
        # and repricing needs a Scryfall bulk download -- so after
        # scripts/repair_truncated_decklists.py recovers cards the scraper
        # had dropped, the priced table lags the decklist until
        # scripts/build_markdown_and_prices.py --force-price is re-run. Say
        # so rather than quietly showing a total for a partial deck.
        raw_count = _raw_card_count(os.path.join(folder_path, _raw_filename_for(pf)))
        priced_count = _priced_card_count(pf)
        decks.append({
            "label": entry["label"],
            "subtitle": entry["subtitle"],
            "role": entry["role"],
            "role_label": entry["role_label"],
            "unpriced_cards": (raw_count - priced_count) if raw_count and raw_count > priced_count else 0,
            "filename": os.path.basename(pf),
            "raw_filename": _raw_filename_for(pf),
            "html": markdown.markdown(priced_text, extensions=MD_EXTENSIONS),
            "usd_total": usd,
            "tix_total": tix,
            "history": _price_history_for(pf),
        })

    return render_template(
        "deck.html",
        article=article,
        article_html=article_html,
        decks=decks,
        num_budget_decks=sum(1 for d in decks if d["role"] == ROLE_BUDGET),
        grand_usd=grand_usd,
        grand_tix=grand_tix,
        has_screenshot=has_screenshot,
    )


def _pool_card_rows_for_deck(priced_path):
    """Per-deck combined (Main Deck + Sideboard) qty for each card, with the
    first non-null unit price found -- the per-deck "how many of this card
    does this deck need" figure that the card-pool builder's client-side JS
    (static/pool.js) sums or maxes across whichever decks the user selects."""
    by_name = {}
    order = []
    for r in parse_priced_card_rows(priced_path):
        entry = by_name.get(r["name"])
        if entry is None:
            entry = {"name": r["name"], "qty": 0, "unit_usd": None, "unit_tix": None}
            by_name[r["name"]] = entry
            order.append(r["name"])
        entry["qty"] += r["qty"]
        if entry["unit_usd"] is None and r["unit_usd"] is not None:
            entry["unit_usd"] = r["unit_usd"]
        if entry["unit_tix"] is None and r["unit_tix"] is not None:
            entry["unit_tix"] = r["unit_tix"]
    return [by_name[name] for name in order]


@app.route("/pool/")
def card_pool():
    all_decks = get_all_decks()
    return render_template(
        "pool.html",
        all_decks=all_decks,
        budget_count=sum(1 for d in all_decks if d["role"] == ROLE_BUDGET),
        reference_count=sum(1 for d in all_decks if d["role"] != ROLE_BUDGET),
    )


@app.route("/pool-data.json")
def pool_data():
    """Every deck's per-card pool data as JSON, for static/pool.js to
    aggregate entirely client-side (arbitrary deck-selection combinations
    aren't something a static site build can pre-render one page per)."""
    decks = [{
        "id": d["id"],
        "title": d["article_title"],
        "label": d["label"],
        "role": d["role"],
        "cards": _pool_card_rows_for_deck(d["priced_path"]),
    } for d in get_all_decks()]
    return Response(json.dumps(decks), mimetype="application/json")


MOVER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_mover_dates():
    """Every archived weekly-movers day (prices/movers/<date>.json, one per
    price day, written by scripts/build_movers.py in the daily fetch
    workflow), sorted chronologically. Not cached: the daily fetch adds a
    new file while a long-running dev server keeps serving."""
    if not os.path.isdir(MOVERS_DIR):
        return []
    return sorted(
        f[: -len(".json")] for f in os.listdir(MOVERS_DIR)
        if f.endswith(".json") and MOVER_DATE_RE.match(f[: -len(".json")])
    )


def _render_movers(date, dates):
    with open(os.path.join(MOVERS_DIR, f"{date}.json"), encoding="utf-8") as f:
        data = json.load(f)
    idx = dates.index(date)
    return render_template(
        "movers.html",
        data=data,
        date=date,
        dates=dates,
        prev_date=dates[idx - 1] if idx > 0 else None,
        next_date=dates[idx + 1] if idx < len(dates) - 1 else None,
        is_latest=(idx == len(dates) - 1),
    )


@app.route("/movers/")
def movers():
    """Latest weekly price movers (also the archive's entry point)."""
    dates = get_mover_dates()
    if not dates:
        # No prices/movers/*.json yet (fresh fork, or build_movers.py hasn't
        # run) -- serve a stub instead of a 404 so the nav link and the
        # static freeze both stay intact.
        return render_template("base.html") + (
            "<!-- no movers data: run scripts/build_movers.py -->"
        )
    return _render_movers(dates[-1], dates)


@app.route("/movers/<date>/")
def movers_detail(date):
    """One archived day of the weekly price movers."""
    dates = get_mover_dates()
    if date not in dates:
        abort(404)
    return _render_movers(date, dates)


@app.route("/card/<slug>/")
def card_detail(slug):
    """One card: its picture, its price history, and every decklist in the
    archive that runs it. Card names across the site link here, and
    static/card-modal.js opens this same content as a modal without leaving
    the page -- this page is what that link falls back to (and what a shared
    link opens)."""
    entry = get_card_index().get(slug)
    if entry is None:
        abort(404)
    return render_template("card.html", card=card_payload(entry))


@app.route("/card/<slug>.json")
def card_data(slug):
    """The same card as JSON, for the modal to fetch."""
    entry = get_card_index().get(slug)
    if entry is None:
        abort(404)
    return Response(json.dumps(card_payload(entry)), mimetype="application/json")


@app.route("/themes/")
def themes():
    """Theme picker and per-variable editor. Entirely client-side -- the
    choice and any tweaks live in localStorage (see webapp/static/theme.js),
    which is what lets it work in the frozen static build too."""
    return render_template("themes.html")


@app.route("/stats/")
def stats():
    card_stats = get_card_stats()
    # Basic lands are in nearly every deck by definition and would otherwise
    # dominate a "most common cards" ranking without telling you anything
    # interesting; the full sortable table below still includes them.
    nonbasic = [c for c in card_stats if not c["is_basic_land"]]
    top_cards = nonbasic[:20]
    max_decks = top_cards[0]["num_budget_decks"] if top_cards else 1
    all_decks = get_all_decks()
    return render_template(
        "stats.html",
        card_stats=card_stats,
        top_cards=top_cards,
        max_decks=max_decks or 1,
        total_unique_cards=len(card_stats),
        reference_only_cards=sum(1 for c in card_stats if c["num_budget_decks"] == 0),
        total_decks=len(all_decks),
        budget_decks=sum(1 for d in all_decks if d["role"] == ROLE_BUDGET),
        deck_link_limit=25,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
