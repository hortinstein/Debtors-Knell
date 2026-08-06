#!/usr/bin/env python3
"""
Finds decklists whose decklist*_priced.md is out of date with the
decklist*.txt beside it -- the priced table accounts for fewer cards than the
decklist actually holds, so its subtotals and grand total are low.

This is how .github/workflows/reprice-decklists.yml decides what to reprice,
rather than a hardcoded folder list, so the workflow is a no-op whenever
nothing is stale (including when it's dispatched against a commit that never
had the problem, or a second time after a reprice has already fixed it).

The comparison is total copies, not card names: build_markdown_and_prices.py
emits a row for every line of the decklist -- an unmatched card gets an
"N/A (unmatched)" row rather than being dropped -- so a correctly priced
decklist always accounts for exactly as many cards as its .txt. Names, by
contrast, legitimately differ (a decklist downloaded from wizards.com spells
"Hit/Run" where Scryfall calls it "Hit // Run").

    python3 scripts/find_stale_priced_decklists.py            # human-readable report
    python3 scripts/find_stale_priced_decklists.py --paths    # priced .md paths, one per line
    python3 scripts/find_stale_priced_decklists.py --folders  # article folders, one per line

Exit status is 0 whether or not anything is stale; callers should branch on
whether the output is empty.
"""
import argparse
import glob
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")

CARD_LINE_RE = re.compile(r"^(\d+)\s+\S")
# One card row of a decklist*_priced.md table; mirrors webapp/app.py's
# PRICE_ROW_RE.
PRICED_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*(\$[\d,.]+|N/A)\s*\|\s*"
    r"([\d.]+|N/A)\s*\|\s*([\d.]+|N/A)\s*\|\s*(.*?)\s*\|\s*$"
)


def decklist_card_count(txt_path):
    with open(txt_path, encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n").replace("\r", "\n")
    return sum(int(m.group(1)) for m in
               (CARD_LINE_RE.match(line.strip()) for line in text.split("\n")) if m)


def priced_card_count(priced_path):
    with open(priced_path, encoding="utf-8") as f:
        return sum(int(m.group(1)) for m in
                   (PRICED_ROW_RE.match(line.rstrip("\n")) for line in f) if m)


def find_stale():
    """[(folder, txt_path, priced_path_or_None, in_decklist, in_priced), ...]
    for every decklist that needs repricing -- missing a priced file entirely,
    or holding more cards than its priced file accounts for."""
    stale = []
    for folder in sorted(os.listdir(ARCHIVE_DIR)):
        folder_path = os.path.join(ARCHIVE_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        for txt_path in sorted(glob.glob(os.path.join(folder_path, "decklist*.txt"))):
            priced_path = txt_path[:-4] + "_priced.md"
            in_txt = decklist_card_count(txt_path)
            if not os.path.exists(priced_path):
                stale.append((folder, txt_path, None, in_txt, 0))
                continue
            in_priced = priced_card_count(priced_path)
            if in_priced < in_txt:
                stale.append((folder, txt_path, priced_path, in_txt, in_priced))
    return stale


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paths", action="store_true",
                   help="print the stale priced .md paths (skipping decklists that have none yet)")
    g.add_argument("--folders", action="store_true",
                   help="print the affected article folder names, deduplicated")
    args = ap.parse_args()

    stale = find_stale()

    if args.paths:
        for _, _, priced_path, _, _ in stale:
            if priced_path:
                print(os.path.relpath(priced_path, REPO_ROOT))
        return
    if args.folders:
        seen = set()
        for folder, _, _, _, _ in stale:
            if folder not in seen:
                seen.add(folder)
                print(folder)
        return

    if not stale:
        print("Every priced decklist is up to date with its decklist.txt.")
        return
    for folder, txt_path, priced_path, in_txt, in_priced in stale:
        name = os.path.basename(txt_path)
        if priced_path is None:
            print(f"{folder}/{name}: no priced file yet ({in_txt} cards)")
        else:
            print(f"{folder}/{name}: priced table has {in_priced} of {in_txt} cards "
                  f"({in_txt - in_priced} missing)")
    folders = len({f for f, _, _, _, _ in stale})
    print(f"\n{len(stale)} decklist(s) across {folders} article(s) need repricing.")


if __name__ == "__main__":
    main()
