#!/usr/bin/env python3
"""
Re-extracts decklist*.txt from each article's already-archived source.html,
repairing the decklists that scripts/scrape.py's parse_cell_cards() truncated.

The bug: a deck cell is "<qty> <a>Card</a><br>" repeated, with an <hr> and a
"22 lands" subtotal between card groups. Some pages give each group (lands /
creatures / spells) its own <td>; others run all three down a single <td>.
parse_cell_cards() used to stop at the first <hr>, so on the single-<td>
pages it kept only the first group -- "Expensive Wurms!" came out as its six
lands and nothing else, and 50-odd other decklists lost a group or two.

This needs no network: the original pages are already saved as source.html,
so re-running the (now fixed) parser over them recovers the missing cards.
Only files that would gain cards are rewritten; a decklist scrape.py
downloaded as a real .txt from wizards.com is left alone unless the archived
page holds strictly more of it.

    python3 scripts/repair_truncated_decklists.py --dry-run   # list what would change
    python3 scripts/repair_truncated_decklists.py

Rewriting a decklist*.txt does NOT reprice it -- decklist*_priced.md is built
separately and needs Scryfall bulk data. After running this, run:

    python3 scripts/build_markdown_and_prices.py --skip-md --force-price

from somewhere with network access to api.scryfall.com -- or scope it with
--only using the folder list this script prints when it finishes.
"""
import argparse
import glob
import os
import re
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")

sys.path.insert(0, SCRIPTS_DIR)
from scrape import extract_decks, mtgo_format  # noqa: E402

CARD_LINE_RE = re.compile(r"^(\d+)\s+(\S.*)$")
DECK_NUM_RE = re.compile(r"decklist_(\d+)_")


def decklist_sort_key(path):
    base = os.path.basename(path)
    m = DECK_NUM_RE.match(base)
    return (int(m.group(1)), base) if m else (0, base)


def read_decklist(path):
    """The [(qty, name), ...] a decklist*.txt currently holds."""
    with open(path, encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n").replace("\r", "\n")
    cards = []
    for line in text.split("\n"):
        m = CARD_LINE_RE.match(line.strip())
        if m:
            cards.append((int(m.group(1)), m.group(2).strip()))
    return cards


def repair_folder(folder, dry_run=False):
    """Returns [(filename, cards_before, cards_after), ...] for files changed."""
    folder_path = os.path.join(ARCHIVE_DIR, folder)
    source_path = os.path.join(folder_path, "source.html")
    files = sorted(glob.glob(os.path.join(folder_path, "decklist*.txt")), key=decklist_sort_key)
    if not files or not os.path.exists(source_path):
        return []

    with open(source_path, encoding="utf-8", errors="replace") as f:
        decks, _ = extract_decks(f.read(), None)
    # scrape.py wrote one decklist_N_*.txt per deck block in page order; if
    # the counts disagree the pairing is guesswork, so leave the folder be.
    if len(decks) != len(files):
        return []

    changed = []
    for path, deck in zip(files, decks):
        have = read_decklist(path)
        maindeck = list(deck["maindeck"])
        sideboard = list(deck["sideboard"])

        # Card *count*, not card names: several files legitimately differ
        # from the page's link text without being short of anything -- a
        # downloaded .txt spells "Kodama\x92s Reach" where the HTML link says
        # "Kodama's Reach", and "Hit/Run" where it says "Hit // Run". Only a
        # file holding strictly fewer cards than the archived page lost
        # something to the truncation bug.
        before = sum(q for q, _ in have)
        after = sum(q for q, _ in maindeck) + sum(q for q, _ in sideboard)
        if after <= before:
            continue

        text = mtgo_format(maindeck, sideboard)
        if not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        changed.append((os.path.basename(path), before, after))
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    folders = sorted(
        d for d in os.listdir(ARCHIVE_DIR)
        if os.path.isdir(os.path.join(ARCHIVE_DIR, d))
    )
    if args.only:
        folders = [f for f in folders if f in args.only]

    touched = []
    for folder in folders:
        changed = repair_folder(folder, dry_run=args.dry_run)
        for filename, before, after in changed:
            print(f"{folder}/{filename}: {before} -> {after} cards")
        if changed:
            touched.append(folder)

    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"\n{verb} decklists in {len(touched)} article(s).")
    if touched and not args.dry_run:
        print("\nNow reprice them (needs network access to api.scryfall.com):")
        print("  python3 scripts/build_markdown_and_prices.py --skip-md --force-price \\")
        print("      --only " + " ".join(touched))


if __name__ == "__main__":
    main()
