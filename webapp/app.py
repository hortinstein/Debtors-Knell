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
from flask import Flask, Response, abort, render_template, send_from_directory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
MASTER_INDEX_PATH = os.path.join(REPO_ROOT, "scripts", "master_index.json")

MD_EXTENSIONS = ["tables", "sane_lists", "nl2br"]

GRAND_TOTAL_RE = re.compile(r"\*\*Grand total:\s*\$([\d,]+\.\d+)\*\*")
GRAND_TOTAL_TIX_RE = re.compile(r"\*\*Grand total \(digital\):\s*([\d,]+\.\d+)\s*tix\*\*")
DECK_NUM_RE = re.compile(r"decklist_(\d+)_")

app = Flask(__name__)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
