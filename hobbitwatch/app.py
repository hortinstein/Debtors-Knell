#!/usr/bin/env python3
"""
Hobbit Watch -- a small Flask app for tracking prices of the new Hobbit (HOB)
cards, built on the same daily price archive as the main "Building on a
Budget" webapp (../prices, see ../prices/README.md).

Deliberately a separate app in its own folder: it shares no routes, templates
or static files with ../webapp, and reads only the price archive plus
scripts/build_price_history.py's archive readers (via build_dataset.py). The
main site is about 2003-2009 decklists; this one is about one brand-new set.

Pages:
    /                       searchable, sortable table of every HOB card with
                            its physical (USD) and digital (tix) price and the
                            change since the first archived data point; click
                            a row for a chart, art, and the card's other
                            printings
    /api/cards.json         the whole dataset
    /api/card/<slug>.json   one card (what the modal fetches)

Run:
    cd hobbitwatch
    pip install -r requirements.txt
    python3 app.py            # http://127.0.0.1:5001

The first start builds data/hobbit_cards.json from the price archive, which
takes a couple of minutes (most of it decompressing the archived MTGJSON
snapshots); after that it's instant, and it rebuilds itself whenever a newer
day of prices lands in ../prices.
"""
import json
import os

from flask import Flask, abort, jsonify, render_template

import build_dataset

DATA_PATH = build_dataset.OUT_PATH

RARITY_ORDER = ["Mythic", "Rare", "Uncommon", "Common"]

app = Flask(__name__)


def _signed(value, unit):
    """'+$1.20' / '-$1.20' / '+0.35 tix' -- the sign belongs in front of the
    currency, not between it and the digits."""
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    amount = f"{abs(value):,.2f}"
    return f"{sign}${amount}" if unit == "usd" else f"{sign}{amount}"


app.jinja_env.filters["money"] = lambda v: f"{v:,.2f}"
app.jinja_env.filters["signed_usd"] = lambda v: _signed(v, "usd")
app.jinja_env.filters["signed_tix"] = lambda v: _signed(v, "tix")

_dataset = None


def _load_dataset(rebuild_if_stale=True):
    """The dataset is derived, gitignored data (build_dataset.py). Build it on
    demand so a fresh checkout just works, and rebuild when a newer price day
    has been fetched since it was last built."""
    global _dataset
    if _dataset is not None:
        return _dataset
    if rebuild_if_stale and build_dataset.dataset_is_stale(DATA_PATH):
        print("[startup] building data/hobbit_cards.json from the price archive "
              "(a couple of minutes on a cold start)...", flush=True)
        _dataset = build_dataset.build(out_path=DATA_PATH)
        return _dataset
    with open(DATA_PATH, encoding="utf-8") as f:
        _dataset = json.load(f)
    return _dataset


def get_cards():
    return _load_dataset()["cards"]


def card_by_slug(slug):
    for card in get_cards():
        if card["slug"] == slug:
            return card
    return None


def _summary(card):
    """The index table's view of a card: current prices, the change since the
    card's first archived data point in each market, and how many other
    printings the modal has to show. No series -- those are fetched per card."""
    tix, usd = card["tix_stats"], card["usd_stats"]
    return {
        "slug": card["slug"],
        "name": card["name"],
        "rarity": card["rarity"],
        "number": card["number"],
        "set": card["set"],
        "scryfall_set": card["scryfall_set"],
        "usd": usd["last"],
        "usd_change": usd["change"],
        "usd_pct": usd["pct"],
        "usd_first": usd["first"],
        "usd_first_date": usd["first_date"],
        "usd_days": usd["days"],
        "tix": tix["last"],
        "tix_change": tix["change"],
        "tix_pct": tix["pct"],
        "tix_first": tix["first"],
        "tix_first_date": tix["first_date"],
        "tix_days": tix["days"],
        "versions_total": card["versions_total"],
        "versions_shown": len(card["versions"]),
    }


@app.route("/")
def index():
    data = _load_dataset()
    cards = [_summary(c) for c in data["cards"]]
    rarities = [r for r in RARITY_ORDER if any(c["rarity"] == r for c in cards)]
    return render_template(
        "index.html",
        cards=cards,
        rarities=rarities,
        set_code=data["set_code"],
        generated=data["generated"],
        latest_tix_date=data["latest_tix_date"],
        usd_day_count=data["usd_day_count"],
        paper_count=sum(1 for c in cards if c["usd"] is not None),
        digital_count=sum(1 for c in cards if c["tix"] is not None),
    )


@app.route("/api/cards.json")
def api_cards():
    return jsonify(_load_dataset())


@app.route("/api/card/<slug>.json")
def api_card(slug):
    card = card_by_slug(slug)
    if card is None:
        abort(404)
    return jsonify(card)


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    _load_dataset()
    port = int(os.environ.get("PORT", "5001"))
    app.run(debug=bool(os.environ.get("FLASK_DEBUG")), port=port)
