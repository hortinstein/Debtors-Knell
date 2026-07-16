#!/usr/bin/env python3
"""
Build article.md (HTML -> Markdown) and priced decklist companions for the
"Building on a Budget" archive.

Safe to re-run: existing article.md / *_priced.md files are skipped unless
--force is given. Never touches source.html, screenshot.png, or status.json.

Usage:
    python3 scripts/build_markdown_and_prices.py                 # full run
    python3 scripts/build_markdown_and_prices.py --only 20030414_Building_On_A_Budget_-_Blue-Green_Threshold
    python3 scripts/build_markdown_and_prices.py --force-md --force-price
    python3 scripts/build_markdown_and_prices.py --refresh-bulk   # re-download Scryfall bulk data

Dependencies: beautifulsoup4, requests, rapidfuzz (pip install rapidfuzz),
              ijson (pip install ijson; optional -- enables the low-memory
              default_cards streaming pass used to fill in prices for cards
              whose single oracle_cards "representative" printing has no USD
              price, e.g. an MTGO-only reprint of a card sold for cents in
              paper. Without ijson those cards are reported as N/A instead.)
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:
    HAVE_RAPIDFUZZ = False

try:
    import ijson
    HAVE_IJSON = True
except ImportError:
    HAVE_IJSON = False

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
CACHE_DIR = os.path.join(SCRIPTS_DIR, "cache")
BULK_PATH = os.path.join(CACHE_DIR, "oracle-cards.json")
DEFAULT_BULK_PATH = os.path.join(CACHE_DIR, "default-cards.json")
UNMATCHED_PATH = os.path.join(SCRIPTS_DIR, "unmatched_cards.txt")

BASIC_LANDS = {"island", "plains", "swamp", "mountain", "forest", "wastes"}

# Non-playable/collectible Scryfall layouts that must never be allowed to
# shadow a real spell/permanent in the name index. Scryfall's oracle_cards
# "one representative printing per name" selection is not layout-aware, so
# e.g. an art_series double-sided "Lightning Bolt // Lightning Bolt" print
# (no rules text, no price) can otherwise win the "lightning bolt" key over
# the real, priced "Lightning Bolt" spell depending on file order.
NON_CARD_LAYOUTS = {
    "art_series", "token", "double_faced_token", "emblem",
    "vanguard", "scheme", "planar",
}

# ---------------------------------------------------------------------------
# Scryfall bulk data
# ---------------------------------------------------------------------------

SCRYFALL_HEADERS = {
    # Scryfall rejects requests carrying an HTTP library's default User-Agent
    # (e.g. bare "python-requests/x.y") with a 400 generic_user_agent error;
    # identify this tool explicitly per Scryfall API etiquette.
    "User-Agent": "DebtorsKnellArchiveBot/1.0 (+https://github.com/; building-on-a-budget archive pricing script)",
    "Accept": "application/json",
}


def _download_bulk(bulk_type, dest_path, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(dest_path) and not refresh:
        return dest_path
    print("Fetching Scryfall bulk-data manifest...", flush=True)
    r = requests.get("https://api.scryfall.com/bulk-data", timeout=30, headers=SCRYFALL_HEADERS)
    r.raise_for_status()
    manifest = r.json()
    entry = next(item for item in manifest["data"] if item["type"] == bulk_type)
    url = entry["download_uri"]
    print(f"Downloading {bulk_type} bulk file ({entry['size']/1e6:.1f} MB) from {url}", flush=True)
    with requests.get(url, stream=True, timeout=180, headers=SCRYFALL_HEADERS) as resp:
        resp.raise_for_status()
        tmp_path = dest_path + ".tmp"
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(tmp_path, dest_path)
    print("Bulk data cached at", dest_path, flush=True)
    return dest_path


def ensure_bulk_data(refresh=False):
    return _download_bulk("oracle_cards", BULK_PATH, refresh=refresh)


def ensure_default_bulk_data(refresh=False):
    return _download_bulk("default_cards", DEFAULT_BULK_PATH, refresh=refresh)


def build_min_price_index(default_bulk_path):
    """Stream default_cards (every printing of every card) without holding
    the whole ~550MB / 500k-object file in memory, and keep only the cheapest
    USD price seen per card name (and per split-card face name), plus the
    cheapest MTGO tix price seen per card name (Scryfall's `prices.tix`,
    sourced from Cardhoarder). This lets a budget-deck card that happens to
    be priceless on its oracle_cards "representative" printing (e.g. an
    MTGO-only Masters Edition reprint) still get a real, usable market price
    -- physical or digital -- from some other printing."""
    best = {}
    best_tix = {}
    with open(default_bulk_path, "rb") as f:
        for card in ijson.items(f, "item"):
            name = card.get("name")
            if not name or card.get("layout") in NON_CARD_LAYOUTS:
                continue
            prices = card.get("prices") or {}
            uri = (card.get("scryfall_uri") or "").split("?")[0]
            names = [name]
            if " // " in name:
                names.extend(p.strip() for p in name.split(" // ") if p.strip())

            usd = prices.get("usd")
            if usd is not None:
                try:
                    usd = float(usd)
                except (TypeError, ValueError):
                    usd = None
            if usd is not None:
                for n in names:
                    k = norm_key(n)
                    cur = best.get(k)
                    if cur is None or usd < cur[0]:
                        best[k] = (usd, uri)

            tix = prices.get("tix")
            if tix is not None:
                try:
                    tix = float(tix)
                except (TypeError, ValueError):
                    tix = None
            if tix is not None:
                for n in names:
                    k = norm_key(n)
                    cur = best_tix.get(k)
                    if cur is None or tix < cur[0]:
                        best_tix[k] = (tix, uri)
    return best, best_tix


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_basic(s):
    s = s.strip()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s


def norm_key(s):
    return norm_basic(s).lower()


def norm_key_noapos(s):
    return norm_key(s).replace("'", "").replace(",", "")


def norm_key_noaccent_nopunct(s):
    s = strip_accents(norm_key(s))
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


class CardIndex:
    def __init__(self, bulk_path, min_price_index=None, min_tix_index=None):
        with open(bulk_path, encoding="utf-8") as f:
            data = json.load(f)
        self.min_price_index = min_price_index or {}
        self.min_tix_index = min_tix_index or {}
        self.by_exact = {}
        self.by_noapos = {}
        self.by_stripped = {}
        self.all_names = []
        self.name_to_card = {}

        def register(name, card):
            self.name_to_card.setdefault(name, card)
            k = norm_key(name)
            self.by_exact.setdefault(k, card)
            self.by_noapos.setdefault(norm_key_noapos(name), card)
            self.by_stripped.setdefault(norm_key_noaccent_nopunct(name), card)

        for card in data:
            name = card.get("name", "")
            if not name or card.get("layout") in NON_CARD_LAYOUTS:
                continue
            self.all_names.append(name)
            register(name, card)
            if " // " in name:
                for face in name.split(" // "):
                    face = face.strip()
                    if face:
                        register(face, card)

    def price_and_uri(self, card):
        prices = card.get("prices", {}) or {}
        usd = prices.get("usd") or prices.get("usd_foil") or prices.get("usd_etched")
        uri = card.get("scryfall_uri", "")
        if uri:
            uri = uri.split("?")[0]
        if usd is None and self.min_price_index:
            cheapest = self.min_price_index.get(norm_key(card.get("name", "")))
            if cheapest:
                usd, uri = cheapest[0], cheapest[1]
        return usd, uri

    def tix_price(self, card):
        """Return the MTGO tix price for a card, or None (never printed on
        Magic Online, or otherwise unpriced -- common for cards from before
        MTGO existed, or casual-only cards)."""
        prices = card.get("prices", {}) or {}
        tix = prices.get("tix")
        if tix is None and self.min_tix_index:
            cheapest = self.min_tix_index.get(norm_key(card.get("name", "")))
            if cheapest:
                tix = cheapest[0]
        if tix is None:
            return None
        try:
            return float(tix)
        except (TypeError, ValueError):
            return None

    def lookup(self, raw_name):
        """Return (card_or_None, method_str)."""
        name = norm_basic(raw_name).strip().strip(".")
        if not name:
            return None, "empty"

        k = norm_key(name)
        if k in self.by_exact:
            return self.by_exact[k], "exact"

        ka = norm_key_noapos(name)
        if ka in self.by_noapos:
            return self.by_noapos[ka], "no-apostrophe"

        ks = norm_key_noaccent_nopunct(name)
        if ks in self.by_stripped:
            return self.by_stripped[ks], "stripped"

        # split-card slash normalization: "Hit/Run" -> "Hit // Run"
        if "/" in name and " // " not in name:
            alt = re.sub(r"\s*/\s*", " // ", name)
            k2 = norm_key(alt)
            if k2 in self.by_exact:
                return self.by_exact[k2], "slash-normalized"

        # fuzzy fallback
        if HAVE_RAPIDFUZZ:
            result = rf_process.extractOne(
                name, self.all_names, scorer=rf_fuzz.WRatio, score_cutoff=90
            )
            if result:
                match_name, score, _ = result
                return self.name_to_card[match_name], f"fuzzy({score:.0f})"

        return None, "unmatched"


# ---------------------------------------------------------------------------
# Decklist parsing / pricing
# ---------------------------------------------------------------------------

def parse_decklist_txt(path):
    """Returns (maindeck [(qty,name)], sideboard [(qty,name)])."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    lines = [ln.strip() for ln in text.splitlines()]
    maindeck, sideboard = [], []
    in_sb = False
    for ln in lines:
        if not ln:
            continue
        if ln.lower() == "sideboard":
            in_sb = True
            continue
        m = re.match(r"^(\d+)\s+(.+)$", ln)
        if not m:
            continue
        qty, name = int(m.group(1)), m.group(2).strip()
        (sideboard if in_sb else maindeck).append((qty, name))
    return maindeck, sideboard


def price_cards(cards, index, unmatched_log, folder, source_file):
    """cards: list of (qty, name). Returns list of dicts + (usd_subtotal, tix_subtotal)."""
    rows = []
    subtotal = 0.0
    tix_subtotal = 0.0
    for qty, name in cards:
        key = norm_key(name)
        if key in BASIC_LANDS:
            card, method = index.lookup(name)
            uri = index.price_and_uri(card)[1] if card else f"https://scryfall.com/search?q={requests.utils.quote(name)}"
            unit = 0.0
            ext = 0.0
            rows.append({
                "qty": qty, "name": name, "unit": unit, "ext": ext,
                "unit_tix": 0.0, "ext_tix": 0.0,
                "uri": uri, "note": "basic land (nominal $0)",
            })
            continue

        card, method = index.lookup(name)
        if card is None:
            uri = f"https://scryfall.com/search?q={requests.utils.quote('!\"' + name + '\"')}"
            rows.append({
                "qty": qty, "name": name, "unit": None, "ext": None,
                "unit_tix": None, "ext_tix": None,
                "uri": uri, "note": "N/A (unmatched)",
            })
            unmatched_log.append(f"{folder}\t{source_file}\t{qty}\t{name}")
            continue

        usd, uri = index.price_and_uri(card)
        tix = index.tix_price(card)
        unit_tix = ext_tix = None
        if tix is not None:
            unit_tix = tix
            ext_tix = tix * qty
            tix_subtotal += ext_tix

        if usd is None:
            rows.append({
                "qty": qty, "name": name, "unit": None, "ext": None,
                "unit_tix": unit_tix, "ext_tix": ext_tix,
                "uri": uri, "note": f"N/A (no USD price on Scryfall, matched via {method})",
            })
            unmatched_log.append(f"{folder}\t{source_file}\t{qty}\t{name}\t(matched={card.get('name')} but no price)")
            continue

        unit = float(usd)
        ext = unit * qty
        subtotal += ext
        note = "" if method == "exact" else f"matched via {method} -> {card.get('name')}"
        rows.append({
            "qty": qty, "name": name, "unit": unit, "ext": ext,
            "unit_tix": unit_tix, "ext_tix": ext_tix,
            "uri": uri, "note": note,
        })
    return rows, subtotal, tix_subtotal


def render_priced_md(deck_title, maindeck_rows, main_subtotal, main_tix_subtotal,
                      sb_rows, sb_subtotal, sb_tix_subtotal, source_txt):
    lines = []
    lines.append(f"# Priced Decklist: {deck_title}\n")
    lines.append(
        f"*Source: `{source_txt}` | Prices: Scryfall bulk data (oracle_cards, "
        f"USD market price + MTGO tix price)*\n"
    )

    def render_section(title, rows, subtotal, tix_subtotal):
        out = [f"## {title}\n"]
        out.append("| Qty | Card | Unit Price | Extended | Tix | Extended (tix) | Scryfall |")
        out.append("|---:|---|---:|---:|---:|---:|---|")
        for r in rows:
            unit_s = f"${r['unit']:.2f}" if r["unit"] is not None else "N/A"
            ext_s = f"${r['ext']:.2f}" if r["ext"] is not None else "N/A"
            unit_tix_s = f"{r['unit_tix']:.2f}" if r.get("unit_tix") is not None else "N/A"
            ext_tix_s = f"{r['ext_tix']:.2f}" if r.get("ext_tix") is not None else "N/A"
            link = f"[link]({r['uri']})" if r["uri"] else ""
            name_cell = r["name"]
            if r["note"]:
                name_cell += f" <sub>({r['note']})</sub>"
            out.append(
                f"| {r['qty']} | {name_cell} | {unit_s} | {ext_s} | "
                f"{unit_tix_s} | {ext_tix_s} | {link} |"
            )
        out.append(f"\n**{title} total: ${subtotal:.2f}** | **{tix_subtotal:.2f} tix**\n")
        return "\n".join(out)

    if maindeck_rows:
        lines.append(render_section("Main Deck", maindeck_rows, main_subtotal, main_tix_subtotal))
    if sb_rows:
        lines.append(render_section("Sideboard", sb_rows, sb_subtotal, sb_tix_subtotal))

    grand = main_subtotal + sb_subtotal
    grand_tix = main_tix_subtotal + sb_tix_subtotal
    lines.append(f"\n---\n\n**Grand total: ${grand:.2f}**\n")
    lines.append(f"\n**Grand total (digital): {grand_tix:.2f} tix**\n")
    return "\n".join(lines) + "\n"


def build_priced_decklists(folder_path, folder_name, index, unmatched_log, force=False):
    count = 0
    for txt_path in sorted(glob.glob(os.path.join(folder_path, "decklist*.txt"))):
        base = os.path.basename(txt_path)
        out_path = txt_path[:-4] + "_priced.md"
        if os.path.exists(out_path) and not force:
            continue
        maindeck, sideboard = parse_decklist_txt(txt_path)
        main_rows, main_sub, main_tix_sub = price_cards(maindeck, index, unmatched_log, folder_name, base)
        sb_rows, sb_sub, sb_tix_sub = price_cards(sideboard, index, unmatched_log, folder_name, base)
        title = base[:-4]
        content = render_priced_md(
            title, main_rows, main_sub, main_tix_sub,
            sb_rows, sb_sub, sb_tix_sub, base,
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        count += 1
    return count


# ---------------------------------------------------------------------------
# mtgdecklist -> decklist.txt extraction (for the 2 mis-classified NO_DECKLIST
# "Common Combo" articles whose deck used a different markup than div.deck)
# ---------------------------------------------------------------------------

def extract_mtgdecklist_files(soup, folder_path):
    """If the article uses the legacy <mtgdecklist> markup and no decklist*.txt
    files exist yet, synthesize decklist.txt (MTGO format) from it."""
    existing = glob.glob(os.path.join(folder_path, "decklist*.txt"))
    if existing:
        return 0
    tags = soup.select("mtgdecklist")
    if not tags:
        return 0
    written = 0
    for i, dl in enumerate(tags, start=1):
        maindeck = []
        for section in ("land", "creatures", "spells"):
            for card in dl.select(f"{section} card"):
                name = card.get("cardname", "").strip()
                qty = card.get("quantity", "").strip()
                if name and qty.isdigit():
                    maindeck.append((int(qty), name))
        sideboard = []
        for card in dl.select("sideboard card"):
            name = card.get("cardname", "").strip()
            qty = card.get("quantity", "").strip()
            if name and qty.isdigit():
                sideboard.append((int(qty), name))
        if not maindeck:
            continue
        lines = [f"{qty} {name}" for qty, name in maindeck]
        if sideboard:
            lines.append("")
            lines.append("Sideboard")
            lines.extend(f"{qty} {name}" for qty, name in sideboard)
        fname = "decklist.txt" if len(tags) == 1 else f"decklist_{i}.txt"
        with open(os.path.join(folder_path, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        written += 1
    return written


# ---------------------------------------------------------------------------
# HTML -> Markdown conversion
# ---------------------------------------------------------------------------

def parse_cell_cards(td):
    cards = []
    pending_qty = None
    for node in td.children:
        if isinstance(node, NavigableString):
            m = re.search(r"(\d+)", str(node))
            if m:
                pending_qty = int(m.group(1))
        elif isinstance(node, Tag) and node.name == "a":
            name = node.get_text(strip=True)
            if pending_qty is not None and name:
                cards.append((pending_qty, name))
                pending_qty = None
        elif isinstance(node, Tag) and node.name == "hr":
            break
    return cards


def deck_div_to_markdown(deck_div, decklist_files, deck_counter):
    heading = deck_div.select_one("heading")
    title = heading.get_text(strip=True) if heading else f"Deck {deck_counter[0]}"
    parts = [f"**{title}**\n"]
    for td in deck_div.select("table.cardgroup td"):
        cards = parse_cell_cards(td)
        if not cards:
            continue
        for qty, name in cards:
            parts.append(f"- {qty} {name}")
        total_span = td.select_one("span.decktotals")
        if total_span:
            parts.append(f"  *({total_span.get_text(strip=True)})*")
        parts.append("")
    idx = deck_counter[0] - 1
    if 0 <= idx < len(decklist_files):
        fname = os.path.basename(decklist_files[idx])
        priced = fname[:-4] + "_priced.md"
        parts.append(f"*(Full decklist with sideboard: `{fname}`; priced breakdown: `{priced}`)*\n")
    deck_counter[0] += 1
    return "\n".join(parts)


def mtgdecklist_to_markdown(dl_tag, decklist_files, deck_counter):
    heading = dl_tag.select_one("heading")
    title = heading.get_text(strip=True) if heading else f"Deck {deck_counter[0]}"
    sub = dl_tag.select_one("h3")
    parts = [f"**{title}**"]
    if sub:
        parts.append(f"*{sub.get_text(strip=True)}*")
    parts.append("")
    for section in ("land", "creatures", "spells", "sideboard"):
        sec_tag = dl_tag.find(section, recursive=False)
        if sec_tag is None:
            continue
        cards = sec_tag.find_all("card")
        if not cards:
            continue
        if section == "sideboard":
            parts.append("*Sideboard:*")
        for card in cards:
            name = card.get("cardname", "").strip()
            qty = card.get("quantity", "").strip()
            if name and qty:
                parts.append(f"- {qty} {name}")
        parts.append("")
    idx = deck_counter[0] - 1
    if 0 <= idx < len(decklist_files):
        fname = os.path.basename(decklist_files[idx])
        priced = fname[:-4] + "_priced.md"
        parts.append(f"*(Full decklist: `{fname}`; priced breakdown: `{priced}`)*\n")
    deck_counter[0] += 1
    return "\n".join(parts)


SKIP_TAGS = {"script", "style", "form", "input", "select", "option", "noscript"}
DROPCAP_RE = re.compile(r"^The letter (\w)!?$", re.IGNORECASE)
MANA_COLOR_RE = re.compile(r"^(Red|Black|Green|Blue|White|Colorless)\s+Mana$", re.IGNORECASE)
MANA_NUM_RE = re.compile(r"^(\d+|X)\s+Mana$", re.IGNORECASE)
MANA_COLOR_LETTER = {"red": "R", "black": "B", "green": "G", "blue": "U", "white": "W", "colorless": "C"}


def mana_symbol(alt):
    """Inline mana-cost images (alt="Red Mana", alt="1 Mana", ...) render
    side by side in the original page to spell out a mana cost; render them
    as {R}{1} style shorthand instead of losing them or gluing the alt text
    together illegibly."""
    m = MANA_COLOR_RE.match(alt)
    if m:
        return "{" + MANA_COLOR_LETTER[m.group(1).lower()] + "}"
    m = MANA_NUM_RE.match(alt)
    if m:
        return "{" + m.group(1).upper() + "}"
    return None
INLINE_WRAP = {
    "b": "**", "strong": "**",
    "i": "*", "em": "*",
}


def render_inline(node):
    """Render an inline node (and descendants) to markdown text, no block breaks."""
    if isinstance(node, Comment):
        return ""  # HTML comments (some articles have stray commented-out drafts) are not content
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if name in SKIP_TAGS:
        return ""
    if name == "br":
        return "\n"
    if name == "hr":
        return "\n\n---\n\n"
    if name == "img":
        # decorative "dropcap" first-letter images (e.g. alt="The letter H!")
        # are actual article content -- the page renders them inline as the
        # first letter of the paragraph. Other images (icons, card art
        # thumbnails) degrade gracefully to their alt text, or vanish if none.
        alt = (node.get("alt") or "").strip()
        m = DROPCAP_RE.match(alt)
        if m:
            return m.group(1)
        sym = mana_symbol(alt)
        if sym:
            return sym
        if "_" in alt and " " not in alt:
            alt = alt.replace("_", " ")  # filename-derived alt text, e.g. "Patron_of_the_Nezumi"
        return alt
    if name == "a":
        raw = "".join(render_inline(c) for c in node.children)
        if not raw.strip():
            return ""
        lead = " " if raw[0].isspace() else ""
        trail = " " if raw[-1].isspace() else ""
        text = raw.strip()
        href = node.get("href", "") or ""
        if href.startswith("javascript:") or not href:
            return f"{lead}{text}{trail}"
        # resolve wayback-relative links to absolute wayback urls; keep as-is otherwise
        if href.startswith("/web/"):
            href = "https://web.archive.org" + href
        return f"{lead}[{text}]({href}){trail}"
    if name in INLINE_WRAP:
        raw = "".join(render_inline(c) for c in node.children)
        if not raw.strip():
            return ""
        # preserve boundary whitespace *outside* the markers -- markdown
        # emphasis doesn't render if the delimiter hugs whitespace, and the
        # source HTML often has "<i>can </i>help" where the trailing space
        # is the only thing separating the word from its neighbor.
        lead = " " if raw[0].isspace() else ""
        trail = " " if raw[-1].isspace() else ""
        inner = raw.strip()
        wrap = INLINE_WRAP[name]
        return f"{lead}{wrap}{inner}{wrap}{trail}"
    if name == "card" and node.get("cardname"):
        return f"{node.get('quantity','').strip()} {node.get('cardname','').strip()}".strip()
    # generic passthrough (span, nobr, font, big, autocard, sup, strike, u, small, etc.)
    return "".join(render_inline(c) for c in node.children)


BLOCK_LEVEL_NAMES = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "table", "blockquote", "hr", "mtgdecklist",
}


def is_block_child(c):
    """True if c is a tag that block_to_markdown() handles specially. The
    archived HTML frequently nests genuine block elements (h3, ul, div.deck,
    table...) inside <p> tags -- lenient parsers/browsers would have closed
    the <p> early, but BeautifulSoup's html.parser keeps the tree as-is, so
    callers must be able to "break out" of an inline run when they hit one
    of these instead of flattening it to text."""
    if not isinstance(c, Tag):
        return False
    if c.name == "div" and "deck" in (c.get("class") or []):
        return True
    return c.name in BLOCK_LEVEL_NAMES


def render_mixed(children, decklist_files, deck_counter):
    """Walk a sequence of sibling nodes, buffering inline content into a
    paragraph block and flushing whenever a nested block-level element is
    encountered, so that e.g. <p>text<div class="deck">...</div>more</p>
    or <p><h3>Heading</h3><ul>...</ul></p> render as separate blocks
    instead of one another and losing structure."""
    blocks = []
    buffer = []

    def flush():
        text = "".join(buffer).strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text:
            blocks.append(text)
        buffer.clear()

    for c in children:
        if isinstance(c, Comment):
            continue
        if is_block_child(c):
            flush()
            blocks.extend(block_to_markdown(c, decklist_files, deck_counter))
        elif isinstance(c, NavigableString):
            buffer.append(str(c))
        elif isinstance(c, Tag):
            buffer.append(render_inline(c))
    flush()
    return blocks


def block_to_markdown(node, decklist_files, deck_counter, depth=0):
    """Render a block-level node to a list of markdown block strings."""
    blocks = []
    if isinstance(node, Comment):
        return blocks
    if isinstance(node, NavigableString):
        text = str(node).strip()
        if text:
            blocks.append(text)
        return blocks
    if not isinstance(node, Tag):
        return blocks
    name = node.name
    if name in SKIP_TAGS:
        return blocks

    classes = node.get("class") or []

    if name == "div" and "deck" in classes:
        blocks.append(deck_div_to_markdown(node, decklist_files, deck_counter))
        return blocks

    if name == "mtgdecklist":
        blocks.append(mtgdecklist_to_markdown(node, decklist_files, deck_counter))
        return blocks

    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = "".join(render_inline(c) for c in node.children).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            level = min(int(name[1]) + 2, 6)  # article body headers start at ###
            blocks.append(f"{'#' * level} {text}")
        return blocks

    if name == "p":
        return render_mixed(node.children, decklist_files, deck_counter)

    if name == "blockquote":
        sub_blocks = render_mixed(node.children, decklist_files, deck_counter)
        inner = "\n\n".join(sub_blocks)
        if inner:
            quoted = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in inner.splitlines())
            blocks.append(quoted)
        return blocks

    if name in ("ul", "ol"):
        items = []
        for i, li in enumerate(node.find_all("li", recursive=False), start=1):
            text = "".join(render_inline(c) for c in li.children).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                prefix = f"{i}." if name == "ol" else "-"
                items.append(f"{prefix} {text}")
        if items:
            blocks.append("\n".join(items))
        return blocks

    if name == "table":
        # generic fallback table (non-deck): render rows as text lines
        rows = []
        for tr in node.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            blocks.append("\n".join(rows))
        return blocks

    if name == "hr":
        blocks.append("---")
        return blocks

    # generic container (div, span-as-block, etc.): buffer+flush its children
    # too, since plain text and block tags can be siblings here as well.
    blocks.extend(render_mixed(node.children, decklist_files, deck_counter))
    return blocks


def html_body_to_markdown(article_content, decklist_files):
    deck_counter = [1]
    blocks = render_mixed(article_content.children, decklist_files, deck_counter)
    # collapse excess blank lines
    text = "\n\n".join(b for b in blocks if b.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def pretty_date(ymd):
    try:
        from datetime import datetime
        return datetime.strptime(ymd, "%Y%m%d").strftime("%B %-d, %Y")
    except Exception:
        return ymd


def build_article_md(folder_path, folder_name, force=False):
    out_path = os.path.join(folder_path, "article.md")
    if os.path.exists(out_path) and not force:
        return False

    status_path = os.path.join(folder_path, "status.json")
    with open(status_path, encoding="utf-8") as f:
        status = json.load(f)

    html_path = os.path.join(folder_path, "source.html")
    with open(html_path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    article_content = soup.select_one("div.article-content")

    decklist_files = sorted(
        glob.glob(os.path.join(folder_path, "decklist*.txt")),
        key=lambda p: (0, "") if os.path.basename(p) == "decklist.txt"
        else (1, os.path.basename(p)),
    )

    title = status.get("title", folder_name)
    author = status.get("author", "")
    date_str = pretty_date(status.get("date", ""))
    url = status.get("url", "")

    header_lines = [f"# {title}\n"]
    meta = []
    if author:
        meta.append(f"**Author:** {author}")
    if date_str:
        meta.append(f"**Date:** {date_str}")
    if url:
        meta.append(f"**Original URL (Wayback Machine):** <{url}>")
    header_lines.append("  \n".join(meta) + "\n")

    # Scoped to the article body itself, not the whole page -- wizards.com's
    # site-wide left nav links to Gatherer on every single page, which would
    # otherwise make this note fire (uselessly) on all 317 articles.
    _link_scope = article_content if article_content is not None else BeautifulSoup("", "html.parser")
    outbound_scryfall = bool(_link_scope.find("a", href=re.compile(r"scryfall\.com", re.I)))
    outbound_gatherer = bool(_link_scope.find("a", href=re.compile(r"gatherer\.wizards\.com", re.I)))
    if outbound_scryfall or outbound_gatherer:
        notes = []
        if outbound_scryfall:
            notes.append("Scryfall")
        if outbound_gatherer:
            notes.append("Gatherer")
        header_lines.append(f"*(Original article contained outbound links to: {', '.join(notes)}.)*\n")

    header_lines.append("---\n")

    if article_content is None:
        body_md = "*(Could not locate article body in source.html.)*\n"
    else:
        body_md = html_body_to_markdown(article_content, decklist_files)
        if not body_md.strip():
            body_md = (
                "*(The archived page for this article has no body content -- "
                "the Wayback Machine snapshot captured only a client-side "
                "redirect stub, not the rendered article.)*\n"
            )

    content = "\n".join(header_lines) + "\n" + body_md

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="only process these folder names")
    ap.add_argument("--force-md", action="store_true")
    ap.add_argument("--force-price", action="store_true")
    ap.add_argument("--skip-md", action="store_true")
    ap.add_argument("--skip-price", action="store_true")
    ap.add_argument("--refresh-bulk", action="store_true")
    ap.add_argument("--skip-min-price", action="store_true",
                     help="skip the default_cards streaming pass that fills in prices "
                          "for cards whose oracle_cards representative has no USD price")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    folders = sorted(
        d for d in os.listdir(ARCHIVE_DIR)
        if os.path.isdir(os.path.join(ARCHIVE_DIR, d))
    )
    if args.only:
        folders = [f for f in folders if f in args.only]
    if args.limit:
        folders = folders[: args.limit]

    index = None
    if not args.skip_price:
        bulk_path = ensure_bulk_data(refresh=args.refresh_bulk)
        min_price_index = None
        min_tix_index = None
        if not args.skip_min_price:
            if HAVE_IJSON:
                default_bulk_path = ensure_default_bulk_data(refresh=args.refresh_bulk)
                print("Streaming default_cards for cheapest-printing prices...", flush=True)
                min_price_index, min_tix_index = build_min_price_index(default_bulk_path)
                print(f"Indexed cheapest USD price for {len(min_price_index)} card names.", flush=True)
                print(f"Indexed cheapest tix price for {len(min_tix_index)} card names.", flush=True)
            else:
                print("ijson not installed; skipping cheapest-printing price fill "
                      "(pip install ijson to enable). Some cards may show N/A "
                      "even though cheaper paper printings exist.", flush=True)
        print("Loading Scryfall card index...", flush=True)
        index = CardIndex(bulk_path, min_price_index=min_price_index, min_tix_index=min_tix_index)
        print(f"Loaded {len(index.all_names)} card names.", flush=True)

    md_created = 0
    priced_created = 0
    mtgdecklist_extracted = 0
    unmatched_log = []

    for i, folder in enumerate(folders):
        folder_path = os.path.join(ARCHIVE_DIR, folder)

        # Handle the rare mtgdecklist-format articles: synthesize decklist.txt
        # if missing, before doing anything else, so pricing/article rendering
        # can reference it.
        html_path = os.path.join(folder_path, "source.html")
        if os.path.exists(html_path) and not glob.glob(os.path.join(folder_path, "decklist*.txt")):
            with open(html_path, encoding="utf-8", errors="replace") as f:
                soup_for_extract = BeautifulSoup(f.read(), "html.parser")
            n = extract_mtgdecklist_files(soup_for_extract, folder_path)
            mtgdecklist_extracted += n

        if not args.skip_md:
            try:
                if build_article_md(folder_path, folder, force=args.force_md):
                    md_created += 1
            except Exception as e:
                print(f"[article.md ERROR] {folder}: {e}", file=sys.stderr)

        if not args.skip_price and index is not None:
            try:
                priced_created += build_priced_decklists(
                    folder_path, folder, index, unmatched_log, force=args.force_price
                )
            except Exception as e:
                print(f"[priced ERROR] {folder}: {e}", file=sys.stderr)

        if (i + 1) % 25 == 0:
            print(f"[{i+1}/{len(folders)}] processed...", flush=True)

    if unmatched_log:
        with open(UNMATCHED_PATH, "w", encoding="utf-8") as f:
            f.write("# Unmatched / no-price cards from Scryfall lookup\n")
            f.write("# folder\tsource_file\tqty\tname\textra\n")
            f.write("\n".join(unmatched_log) + "\n")
    elif not args.skip_price and not args.only:
        # full run with nothing unmatched: still write an (empty-body) report
        with open(UNMATCHED_PATH, "w", encoding="utf-8") as f:
            f.write("# Unmatched / no-price cards from Scryfall lookup\n# (none)\n")

    print(f"\narticle.md created: {md_created}")
    print(f"priced decklists created: {priced_created}")
    print(f"mtgdecklist-format decklist.txt files synthesized: {mtgdecklist_extracted}")
    print(f"unmatched card lines: {len(unmatched_log)}")


if __name__ == "__main__":
    main()
