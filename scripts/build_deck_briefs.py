#!/usr/bin/env python3
"""
Dump a compact "briefing packet" per article (title/author/date + an excerpt
of the article prose + the deduplicated card names across all its decklists)
for every article that has at least one decklist. This is the raw material
an LLM pass reads to write a <=30-word description and pick archetype tags
per article -- see scripts/deck_archetypes.json (the output of that pass)
and scripts/apply_deck_archetypes.py (which folds it back into the site).

Usage:
    python3 scripts/build_deck_briefs.py                    # all articles
    python3 scripts/build_deck_briefs.py --start 0 --limit 20
    python3 scripts/build_deck_briefs.py --only 20090527_Shamans_Trounce_2009
"""
import argparse
import glob
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
MASTER_INDEX_PATH = os.path.join(REPO_ROOT, "scripts", "master_index.json")

CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+)$")


def prose_excerpt(article_text, max_words=220):
    parts = article_text.split("\n---\n", 1)
    body = parts[1] if len(parts) > 1 else article_text
    words = body.split()
    return " ".join(words[:max_words])


def deck_card_names(folder_path):
    names = set()
    for txt_path in sorted(glob.glob(os.path.join(folder_path, "decklist*.txt"))):
        with open(txt_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.lower() == "sideboard":
                    continue
                m = CARD_LINE_RE.match(line)
                if m:
                    names.add(m.group(2).strip())
    return sorted(names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None, help="write JSON to this path instead of stdout")
    args = ap.parse_args()

    with open(MASTER_INDEX_PATH, encoding="utf-8") as f:
        master = json.load(f)
    master.sort(key=lambda m: (m.get("ymd", ""), m.get("title", "")))

    if args.only:
        master = [m for m in master if m["folder"] in args.only]
    else:
        master = master[args.start:]
        if args.limit:
            master = master[: args.limit]

    briefs = []
    for m in master:
        folder = m["folder"]
        folder_path = os.path.join(ARCHIVE_DIR, folder)
        if not glob.glob(os.path.join(folder_path, "decklist*_priced.md")):
            continue  # no decklist -- out of scope for archetype tagging
        article_path = os.path.join(folder_path, "article.md")
        prose = ""
        if os.path.exists(article_path):
            with open(article_path, encoding="utf-8") as f:
                prose = prose_excerpt(f.read())
        cards = deck_card_names(folder_path)
        briefs.append({
            "folder": folder,
            "title": m.get("title", folder),
            "author": m.get("author", ""),
            "prose": prose,
            "cards": cards,
        })

    out = json.dumps(briefs, indent=1, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {len(briefs)} briefs to {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
