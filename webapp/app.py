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
import glob
import json
import os
import re

import markdown
from flask import Flask, Response, abort, render_template, request, send_from_directory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
MASTER_INDEX_PATH = os.path.join(REPO_ROOT, "scripts", "master_index.json")

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

app = Flask(__name__)


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
    unit_tix, ext_tix (None where N/A)."""
    rows = []
    with open(priced_md_path, encoding="utf-8") as f:
        for line in f:
            m = PRICE_ROW_RE.match(line.rstrip("\n"))
            if not m:
                continue
            qty = int(m.group(1))
            rows.append({
                "qty": qty,
                "name": _canonical_card_name(m.group(2)),
                "unit_usd": _parse_money(m.group(3)),
                "ext_usd": _parse_money(m.group(4)),
                "unit_tix": None if m.group(5) == "N/A" else float(m.group(5)),
                "ext_tix": None if m.group(6) == "N/A" else float(m.group(6)),
            })
    return rows


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


def _deck_label(priced_md_path):
    """Derive a human-friendly deck name from a priced-decklist filename,
    e.g. decklist_1_Nether_Go_priced.md -> 'Nether Go'."""
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


def _load_article_entry(meta):
    folder = meta["folder"]
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    article_path = os.path.join(folder_path, "article.md")
    description = ""
    if os.path.exists(article_path):
        with open(article_path, encoding="utf-8") as f:
            description = _first_paragraph(f.read())

    priced_files = _priced_files_for(folder_path)
    has_decklist = bool(priced_files)
    usd_total = 0.0
    tix_total = 0.0
    for pf in priced_files:
        usd, tix = _parse_grand_totals(pf)
        usd_total += usd
        tix_total += tix

    return {
        "folder": folder,
        "title": meta.get("title") or folder,
        "author": meta.get("author") or "",
        "date_str": meta.get("date_str") or "",
        "ymd": meta.get("ymd") or "",
        "description": description,
        "has_decklist": has_decklist,
        "num_decks": len(priced_files),
        "usd_total": usd_total,
        "tix_total": tix_total,
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
            folder_path = os.path.join(ARCHIVE_DIR, article["folder"])
            for pf in _priced_files_for(folder_path):
                decks.append({
                    "id": deck_id(article["folder"], pf),
                    "folder": article["folder"],
                    "priced_path": pf,
                    "article_title": article["title"],
                    "label": _deck_label(pf),
                    "date_str": article["date_str"],
                    "ymd": article["ymd"],
                    "usd_total": _parse_grand_totals(pf)[0],
                    "tix_total": _parse_grand_totals(pf)[1],
                })
        decks.sort(key=lambda d: (d["ymd"], d["article_title"], d["label"]))
        _ALL_DECKS_CACHE = decks
    return _ALL_DECKS_CACHE


def get_deck_by_id(did):
    for d in get_all_decks():
        if d["id"] == did:
            return d
    return None


BASIC_LANDS = {"island", "plains", "swamp", "mountain", "forest", "wastes"}

_CARD_STATS_CACHE = None


def get_card_stats():
    """Aggregate every card across every deck in the archive: which decks
    it appears in, how many copies total, and how many distinct decks
    (the "how common is this card" ranking)."""
    global _CARD_STATS_CACHE
    if _CARD_STATS_CACHE is None:
        by_name = {}
        for d in get_all_decks():
            rows = parse_priced_card_rows(d["priced_path"])
            seen_in_this_deck = set()
            for r in rows:
                name = r["name"]
                entry = by_name.setdefault(name, {
                    "name": name,
                    "is_basic_land": name.lower() in BASIC_LANDS,
                    "total_qty": 0,
                    "num_decks": 0,
                    "unit_usd": None,
                    "unit_tix": None,
                    "decks": [],
                })
                entry["total_qty"] += r["qty"]
                if entry["unit_usd"] is None and r["unit_usd"] is not None:
                    entry["unit_usd"] = r["unit_usd"]
                if entry["unit_tix"] is None and r["unit_tix"] is not None:
                    entry["unit_tix"] = r["unit_tix"]
                if d["id"] not in seen_in_this_deck:
                    seen_in_this_deck.add(d["id"])
                    entry["num_decks"] += 1
                    entry["decks"].append({
                        "id": d["id"],
                        "folder": d["folder"],
                        "title": d["article_title"],
                        "label": d["label"],
                        "qty": r["qty"],
                    })
        stats = list(by_name.values())
        stats.sort(key=lambda c: (-c["num_decks"], -c["total_qty"], c["name"].lower()))
        _CARD_STATS_CACHE = stats
    return _CARD_STATS_CACHE


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    articles = get_articles()
    with_decklist = sum(1 for a in articles if a["has_decklist"])
    return render_template(
        "index.html",
        articles=articles,
        total_count=len(articles),
        with_decklist_count=with_decklist,
    )


@app.route("/screenshot/<folder>")
def screenshot(folder):
    # Validate against the known folder list (not just os.path checks) so a
    # crafted folder value can't be used to walk outside archive/.
    if get_article_by_folder(folder) is None:
        abort(404)
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    if not os.path.exists(os.path.join(folder_path, "screenshot.png")):
        abort(404)
    return send_from_directory(folder_path, "screenshot.png")


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


@app.route("/deck/<folder>")
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
    for pf in _priced_files_for(folder_path):
        with open(pf, encoding="utf-8") as f:
            priced_text = f.read()
        # Drop the file's own "# Priced Decklist: <raw_filename>" heading and
        # source line -- the template already shows a nicer deck label and
        # the filename, and repeating the underscored raw title looks noisy.
        priced_text = re.sub(r"^# Priced Decklist:.*\n\n?", "", priced_text)
        priced_text = re.sub(r"^\*Source:.*\*\n\n?", "", priced_text)
        usd, tix = _parse_grand_totals(pf)
        grand_usd += usd
        grand_tix += tix
        raw_filename = os.path.basename(pf)[: -len("_priced.md")] + ".txt"
        decks.append({
            "label": _deck_label(pf),
            "filename": os.path.basename(pf),
            "raw_filename": raw_filename,
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
        grand_usd=grand_usd,
        grand_tix=grand_tix,
        has_screenshot=has_screenshot,
    )


def _aggregate_card_pool(selected_decks):
    """Combine the card rows of several priced decklists into one shopping
    list: total copies needed to build ALL of the selected decks at the same
    time (no assumption that cards can be shared/reused between decks)."""
    by_name = {}
    for d in selected_decks:
        for r in parse_priced_card_rows(d["priced_path"]):
            entry = by_name.setdefault(r["name"], {
                "name": r["name"], "qty": 0,
                "unit_usd": None, "unit_tix": None,
                "ext_usd": 0.0, "ext_tix": 0.0,
                "has_usd": False, "has_tix": False,
                "decks": [],
            })
            entry["qty"] += r["qty"]
            if entry["unit_usd"] is None and r["unit_usd"] is not None:
                entry["unit_usd"] = r["unit_usd"]
            if entry["unit_tix"] is None and r["unit_tix"] is not None:
                entry["unit_tix"] = r["unit_tix"]
            if r["ext_usd"] is not None:
                entry["ext_usd"] += r["ext_usd"]
                entry["has_usd"] = True
            if r["ext_tix"] is not None:
                entry["ext_tix"] += r["ext_tix"]
                entry["has_tix"] = True
            entry["decks"].append({
                "id": d["id"], "title": d["article_title"], "label": d["label"], "qty": r["qty"],
            })
    rows = list(by_name.values())
    rows.sort(key=lambda c: c["name"].lower())
    return rows


@app.route("/pool")
def card_pool():
    all_decks = get_all_decks()
    requested_ids = request.args.getlist("deck")
    selected_decks = [d for did in requested_ids if (d := get_deck_by_id(did)) is not None]

    pool_rows = None
    pool_usd_total = pool_tix_total = 0.0
    if selected_decks:
        pool_rows = _aggregate_card_pool(selected_decks)
        pool_usd_total = sum(r["ext_usd"] for r in pool_rows if r["has_usd"])
        pool_tix_total = sum(r["ext_tix"] for r in pool_rows if r["has_tix"])

    return render_template(
        "pool.html",
        all_decks=all_decks,
        selected_ids={d["id"] for d in selected_decks},
        selected_decks=selected_decks,
        pool_rows=pool_rows,
        pool_usd_total=pool_usd_total,
        pool_tix_total=pool_tix_total,
    )


@app.route("/pool/download")
def pool_download():
    requested_ids = request.args.getlist("deck")
    selected_decks = [d for did in requested_ids if (d := get_deck_by_id(did)) is not None]
    if not selected_decks:
        abort(404)

    pool_rows = _aggregate_card_pool(selected_decks)
    lines = [f"{r['qty']} {r['name']}" for r in pool_rows]
    text = "\n".join(lines) + "\n"
    return Response(
        text,
        mimetype="text/plain",
        headers={"Content-Disposition": 'attachment; filename="card_pool.txt"'},
    )


@app.route("/stats")
def stats():
    card_stats = get_card_stats()
    # Basic lands are in nearly every deck by definition and would otherwise
    # dominate a "most common cards" ranking without telling you anything
    # interesting; the full sortable table below still includes them.
    nonbasic = [c for c in card_stats if not c["is_basic_land"]]
    top_cards = nonbasic[:20]
    max_decks = top_cards[0]["num_decks"] if top_cards else 1
    return render_template(
        "stats.html",
        card_stats=card_stats,
        top_cards=top_cards,
        max_decks=max_decks,
        total_unique_cards=len(card_stats),
        total_decks=len(get_all_decks()),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
