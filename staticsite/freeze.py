#!/usr/bin/env python3
"""
Freezes the "Building on a Budget" Flask webapp (../webapp/app.py) into a
static site deployable to GitHub Pages -- see
../.github/workflows/deploy-pages.yml, which runs this on every push.

Every route in the live app is either:
  - fully static (/, /stats, /pool, /pool-data.json) -- frozen with no
    variables, exactly as Frozen-Flask discovers it, or
  - one static file per (folder[, filename]) combination (/deck/<folder>,
    /screenshot/<folder>, /download/<folder>/<filename>) -- the generators
    below enumerate every real combination so Frozen-Flask doesn't have to
    guess them from crawled links.

The one route Frozen-Flask genuinely can't pre-render is the card-pool
builder's deck-selection logic: /pool takes an arbitrary combination of
`deck` query-string values, which can't be enumerated ahead of time.
webapp/static/pool.js does that aggregation client-side instead, off the
/pool-data.json file this script freezes like any other page.

Usage:
    cd staticsite
    pip install -r requirements.txt -r ../webapp/requirements.txt
    python3 freeze.py [destination-dir]   # default: staticsite/build
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(REPO_ROOT, "webapp")
DEFAULT_DESTINATION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")

sys.path.insert(0, WEBAPP_DIR)
import app as webapp_app  # noqa: E402 -- needs the sys.path.insert above

from flask_frozen import Freezer  # noqa: E402


def build(destination):
    a = webapp_app.app
    a.config["FREEZER_DESTINATION"] = os.path.abspath(destination)
    # Relative links (not "http://localhost/...") so the frozen output works
    # both at a domain root and at a GitHub Pages project subpath.
    a.config["FREEZER_RELATIVE_URLS"] = True
    a.config["FREEZER_IGNORE_MIMETYPE_WARNINGS"] = True
    freezer = Freezer(a)

    @freezer.register_generator
    def deck_detail():
        for article in webapp_app.get_articles():
            yield {"folder": article["folder"]}

    @freezer.register_generator
    def screenshot():
        for article in webapp_app.get_articles():
            yield {"folder": article["folder"]}

    @freezer.register_generator
    def download_decklist():
        for article in webapp_app.get_articles():
            folder_path = os.path.join(webapp_app.ARCHIVE_DIR, article["folder"])
            for path in webapp_app._raw_decklist_files_for(folder_path):
                yield {"folder": article["folder"], "filename": os.path.basename(path)}

    urls = freezer.freeze()

    # GitHub Pages runs everything through Jekyll by default, which ignores
    # files/folders starting with "_" -- harmless here, but the standard fix
    # is a .nojekyll marker so Pages serves the frozen output byte-for-byte.
    open(os.path.join(a.config["FREEZER_DESTINATION"], ".nojekyll"), "w").close()

    print(f"Froze {len(urls)} URLs to {a.config['FREEZER_DESTINATION']}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DESTINATION)
