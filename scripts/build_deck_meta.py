#!/usr/bin/env python3
"""
Builds scripts/deck_meta.json -- per-article metadata the webapp can't get
from the decklist files themselves:

  * the real title of each decklist ("Ninjutsu v.1.0"), recovered from the
    archived source.html. The decklist filenames were slugified at scrape
    time (scripts/scrape.py slugify() strips punctuation), so deriving a
    display label from the filename mangles ~350 of the 820 decklists:
    "Ninjutsu v.1.0" -> "Ninjutsu v10", "Wild Pair & Slivers" -> "Wild Pair
    Slivers", "(untitled)" -> "untitled".
  * each decklist's subtitle, where the original page had one -- either a
    format ("Standard", "Extended", "Pauper") or the event a referenced
    tournament list came from ("Pro Tour-Honolulu 2006").
  * a role for each decklist: whether it's one of the column's own budget
    builds, or a reference list the article merely quotes (a pro tour deck,
    a reader submission, the preconstructed deck the article starts from,
    or an explicitly non-budget "if money were no object" build). Without
    this, the card pool and card stats pages mix Black Lotus and dual lands
    from quoted vintage/pro lists in with the actual budget decks.
  * exact-duplicate decklists within one article -- a few articles print
    last week's deck a second time as a recap, and the scraper saved it
    under two filenames, which double-counted it in the article's combined
    total and listed it twice in the card pool.
  * the article's overall win/loss record, parsed from the "*Record: W-L*"
    markers the game logs carry.

Run:
    pip install -r requirements-prices.txt beautifulsoup4
    python3 scripts/build_deck_meta.py
"""
import argparse
import glob
import hashlib
import json
import os
import re

from bs4 import BeautifulSoup

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deck_meta.json")

DECK_NUM_RE = re.compile(r"decklist_(\d+)_")
CARD_LINE_RE = re.compile(r"^\d+\s+\S")

# ---------------------------------------------------------------------------
# Deck roles
# ---------------------------------------------------------------------------
# "budget" is the default: a decklist the column itself built to the ~30-ticket
# budget. Everything else is a list the article quotes for comparison, and is
# what the webapp lets you filter out of the card pool / card stats.
ROLE_BUDGET = "budget"
ROLE_PRO = "pro"
ROLE_READER = "reader"
ROLE_PRECON = "precon"
ROLE_NONBUDGET = "nonbudget"

REFERENCE_ROLES = (ROLE_PRO, ROLE_READER, ROLE_PRECON, ROLE_NONBUDGET)

ROLE_LABELS = {
    ROLE_BUDGET: "Budget build",
    ROLE_PRO: "Tournament list",
    ROLE_READER: "Reader submission",
    ROLE_PRECON: "Preconstructed deck",
    ROLE_NONBUDGET: "Non-budget build",
}

# An event/placing in the title or subtitle means the list is somebody's
# tournament deck quoted for comparison. Deliberately narrow: a bare "World"
# or "States" is far more likely to be part of a deck name ("Dead World",
# "Ipseria Controls the World") than an event reference.
EVENT_RE = re.compile(
    r"\b("
    r"pro tour|grand prix|\bgp [a-z]|nationals|regionals|invitational|ptq|"
    r"world championship|worlds \d|worlds? 20\d\d|qualifier|trial winner|"
    r"top \d+|\d+(?:st|nd|rd|th) place|\d+(?:st|nd|rd|th) -|seat [a-d]\b|"
    r"[a-z]+ states 20\d\d|standard championships?|pt [a-z]"
    r")\b",
    re.I,
)
# "Ninja Attack! by Nate Heiss", "Toshi Flames by ToshiUmezawa"
BY_RE = re.compile(r"\bby\s+[A-Z0-9_]")
# "thickasabrick's Hostility-a-Tog", "Ttek's Bridge From Below Dredge"
OWNER_RE = re.compile(r"^([A-Za-z0-9_\-]+)'s\s+\S")
PRECON_RE = re.compile(r"\b(precon(?:structed)?|theme deck)\b", re.I)
NONBUDGET_RE = re.compile(r"\b(expensive|non-?budget)\b", re.I)

# Hand-checked corrections to the title heuristics above, for deck names that
# happen to look like an event or a forum handle. Keys are "<folder>::<deck
# title>"; every one of these is one of Ben's own budget builds, named after
# the article.
ROLE_OVERRIDES = {
    "20050516_Rats_Nest_Ratimation::Rat's Nest": ROLE_BUDGET,
    "20061106_Whats_Upton::What's Upton 1": ROLE_BUDGET,
    "20061106_Whats_Upton::What's Upton 2": ROLE_BUDGET,
    "20061106_Whats_Upton::What's Upton 3": ROLE_BUDGET,
    "20071003_Tight_Sight_Wrap-Up_Wily_Arcanis::Arcanis's Guile": ROLE_BUDGET,
    "20071003_Tight_Sight_Wrap-Up_Wily_Arcanis::Arcanis's Guile, Expanded to 60 Cards": ROLE_BUDGET,
    "20051003_Interlude_My_Mirage_Precon_Part_1::Dead World": ROLE_BUDGET,
    "20060116_Interlude_BOAB_Smackdown_II::Dead World (Dead/Aflame v.1.6)": ROLE_BUDGET,
    "20060213_Interlude_Spare-Time_Decks::Dead World Redux": ROLE_BUDGET,
    "20060724_Azorius_Ascendant_One_Piece_At_A_Time::Ipseria Controls the World": ROLE_BUDGET,
    # Ben's own 1994 deck, quoted as nostalgia -- the archive's only source of
    # Black Lotus, the Moxen, Badlands and Strip Mine.
    "20070326_10_Decks_in_10_Weeks_Grim_Outlook::Ben's Old Discard Deck": ROLE_NONBUDGET,
}


def classify_role(folder, title, subtitle):
    override = ROLE_OVERRIDES.get(f"{folder}::{title}")
    if override:
        return override
    blob = f"{title} | {subtitle}"
    if NONBUDGET_RE.search(blob):
        return ROLE_NONBUDGET
    if PRECON_RE.search(blob):
        return ROLE_PRECON
    if EVENT_RE.search(blob):
        return ROLE_PRO
    if BY_RE.search(title) or OWNER_RE.match(title):
        return ROLE_READER
    return ROLE_BUDGET


# ---------------------------------------------------------------------------
# Win/loss records
# ---------------------------------------------------------------------------
# The game logs mark each game's result with a running cumulative record,
# "*Record: 4-2*". Taking the last marker in the file would be wrong often
# enough to be useless: an article that tests two builds restarts the count
# partway through, a few print their games newest-first, and one interleaves
# two decks' logs game by game. The furthest point any single run reaches --
# the marker with the most games played -- is order-independent, and on every
# article spot-checked by hand it is the record the article's own wrap-up
# quotes for the deck it settled on.
RECORD_RE = re.compile(r"Record:\s*(\d+)\s*[-–]\s*(\d+)", re.I)


def parse_record(article_text):
    """Article-level record from the game log's cumulative "Record: W-L"
    markers, or None if the article has no game log."""
    points = [(int(w), int(l)) for w, l in RECORD_RE.findall(article_text)]
    points = [p for p in points if sum(p) > 0]
    if not points:
        return None
    wins, losses = max(points, key=lambda p: (sum(p), p[0]))
    return {"wins": wins, "losses": losses, "markers": len(points)}


# ---------------------------------------------------------------------------
# Archive scanning
# ---------------------------------------------------------------------------

def decklist_sort_key(path):
    """Same ordering webapp/app.py uses: decklist.txt first, then
    decklist_1_*, decklist_2_*, ... in numeric order."""
    base = os.path.basename(path)
    m = DECK_NUM_RE.match(base)
    return (int(m.group(1)), base) if m else (0, base)


def raw_decklists_for(folder_path):
    return sorted(glob.glob(os.path.join(folder_path, "decklist*.txt")), key=decklist_sort_key)


def titles_from_source(source_path):
    """(title, subtitle) for every deck block on the archived page, in page
    order -- the same order and count scrape.py wrote decklist_N_*.txt in."""
    with open(source_path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    out = []
    for deck_div in soup.select("div.deck"):
        heading = deck_div.select_one("heading")
        sub = deck_div.select_one(".sub")
        out.append((
            heading.get_text(" ", strip=True) if heading else "",
            sub.get_text(" ", strip=True) if sub else "",
        ))
    return out


def card_fingerprint(decklist_path):
    """Hash of just the card lines, so two files holding the same deck match
    even if one carries a stray title or "Sideboard" label line."""
    with open(decklist_path, encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n").replace("\r", "\n")
    cards = [l.strip() for l in text.split("\n") if CARD_LINE_RE.match(l.strip())]
    return hashlib.md5("\n".join(cards).encode("utf-8")).hexdigest()


def build_meta(quiet=False):
    meta = {}
    warnings = []
    for folder in sorted(os.listdir(ARCHIVE_DIR)):
        folder_path = os.path.join(ARCHIVE_DIR, folder)
        if not os.path.isdir(folder_path):
            continue

        entry = {}

        article_path = os.path.join(folder_path, "article.md")
        if os.path.exists(article_path):
            with open(article_path, encoding="utf-8") as f:
                record = parse_record(f.read())
            if record:
                entry["record"] = record

        raw_files = raw_decklists_for(folder_path)
        if raw_files:
            source_path = os.path.join(folder_path, "source.html")
            titles = titles_from_source(source_path) if os.path.exists(source_path) else []
            if len(titles) != len(raw_files):
                warnings.append(
                    f"{folder}: {len(titles)} deck blocks in source.html but "
                    f"{len(raw_files)} decklist files -- falling back to filenames"
                )
                titles = []

            decks = []
            seen_fingerprints = {}
            for i, path in enumerate(raw_files):
                filename = os.path.basename(path)
                title, subtitle = titles[i] if i < len(titles) else ("", "")
                fingerprint = card_fingerprint(path)
                deck = {
                    "file": filename,
                    "title": title,
                    "subtitle": subtitle,
                    "role": classify_role(folder, title, subtitle) if title else ROLE_BUDGET,
                }
                if fingerprint in seen_fingerprints:
                    deck["duplicate_of"] = seen_fingerprints[fingerprint]
                else:
                    seen_fingerprints[fingerprint] = filename
                decks.append(deck)
            entry["decks"] = decks

        if entry:
            meta[folder] = entry

    if not quiet:
        for w in warnings:
            print("warning: " + w)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=OUT_PATH)
    args = ap.parse_args()

    meta = build_meta()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    decks = [d for e in meta.values() for d in e.get("decks", [])]
    roles = {}
    for d in decks:
        roles[d["role"]] = roles.get(d["role"], 0) + 1
    print(f"Wrote {args.out}")
    print(f"  {len(meta)} articles, {len(decks)} decklists")
    print(f"  roles: " + ", ".join(f"{k}={v}" for k, v in sorted(roles.items())))
    print(f"  duplicates: {sum(1 for d in decks if d.get('duplicate_of'))}")
    print(f"  articles with a win/loss record: {sum(1 for e in meta.values() if 'record' in e)}")


if __name__ == "__main__":
    main()
