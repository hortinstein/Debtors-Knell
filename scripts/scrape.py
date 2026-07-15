#!/usr/bin/env python3
"""
Scrape "Building on a Budget" articles from the Wayback Machine:
 - full-page screenshot with images rendered
 - MTGO-format decklist extraction (direct .txt when available, else HTML parse)
 - resumable, writes archive/<folder>/status.json and top-level log.md
"""
import re, os, sys, json, time, argparse
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_TS = "20090601202842"
ARTICLE_URL_TMPL = "https://web.archive.org/web/{ts}/https://www.wizards.com/Magic/Magazine/Article.aspx?x={slug}"

REPO_ROOT = "/workspaces/Debtors-Knell"
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
INDEX_PATH = os.path.join(REPO_ROOT, "scripts", "master_index.json")
LOG_PATH = os.path.join(REPO_ROOT, "log.md")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArchiveResearchBot/1.0; +personal-archival-project)"})

HIDE_TOOLBAR_CSS = """
#wm-ipp-base, #wm-ipp, #donato, #wm-ipp-print { display: none !important; }
html { margin-top: 0 !important; }
body { margin-top: 0 !important; }
"""


def log(msg):
    print(msg, flush=True)


def fetch(url, retries=4, timeout=60):
    last_exc = None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            last_exc = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last_exc = e
        time.sleep(2 * (attempt + 1))
    raise last_exc


def parse_cell_cards(td):
    """Parse a <td> of the maindeck cardgroup table into [(qty, name), ...]."""
    cards = []
    pending_qty = None
    for node in td.children:
        if isinstance(node, str):
            m = re.search(r"(\d+)", node)
            if m:
                pending_qty = int(m.group(1))
        elif getattr(node, "name", None) == "a":
            name = node.get_text(strip=True)
            if pending_qty is not None and name:
                cards.append((pending_qty, name))
                pending_qty = None
        elif getattr(node, "name", None) == "hr":
            break
    return cards


def find_sideboard_block(deck_div, soup):
    """Best-effort: find the <p> holding sideboard card lines following this deck div."""
    all_nodes = list(soup.find_all(["div", "p"]))
    try:
        start_idx = all_nodes.index(deck_div)
    except ValueError:
        return None
    for node in all_nodes[start_idx + 1: start_idx + 40]:
        if node.name == "div" and "deck" in (node.get("class") or []):
            break
        if node.name == "p":
            b = node.find(["b", "strong"])
            if b and "sideboard" in b.get_text(strip=True).lower():
                return node
            txt = node.get_text(strip=True).lower()
            if "sideboard" in txt and len(txt) < 60:
                nxt = node.find_next("p")
                if nxt is not None:
                    return nxt
    return None


def parse_sideboard_lines(p_tag):
    if p_tag is None:
        return []
    inner = p_tag.decode_contents()
    inner = re.sub(r"^\s*<(b|strong)>.*?</\1>\s*", "", inner, flags=re.IGNORECASE | re.DOTALL)
    parts = re.split(r"<br\s*/?>", inner, flags=re.IGNORECASE)
    cards = []
    for part in parts:
        frag = BeautifulSoup(part, "html.parser")
        text = re.sub(r"\s+", " ", frag.get_text(" ", strip=True)).strip(" .")
        if not text:
            continue
        m = re.match(r"^(\d+)\s+(.+)$", text)
        if m:
            cards.append((int(m.group(1)), m.group(2)))
    return cards


def mtgo_format(maindeck, sideboard):
    lines = [f"{qty} {name}" for qty, name in maindeck]
    if sideboard:
        lines.append("")
        lines.append("Sideboard")
        lines.extend(f"{qty} {name}" for qty, name in sideboard)
    return "\n".join(lines) + "\n"


def extract_decks(html_text, base_wayback_url):
    soup = BeautifulSoup(html_text, "html.parser")
    decks = []
    for deck_div in soup.select("div.deck"):
        heading = deck_div.select_one("heading")
        title = heading.get_text(strip=True) if heading else "Deck"
        txt_link = None
        for a in deck_div.select(".deckoptions a"):
            href = a.get("href", "")
            if href.lower().endswith(".txt"):
                txt_link = href
                break
        maindeck = []
        for td in deck_div.select("table.cardgroup td"):
            maindeck.extend(parse_cell_cards(td))
        sb_p = find_sideboard_block(deck_div, soup)
        sideboard = parse_sideboard_lines(sb_p)
        decks.append({
            "title": title,
            "txt_link": txt_link,
            "maindeck": maindeck,
            "sideboard": sideboard,
        })
    return decks, soup


def resolve_wayback_url(href, base_url):
    if href.startswith("http"):
        return href
    if href.startswith("/web/"):
        return "https://web.archive.org" + href
    # relative path fallback
    return requests.compat.urljoin(base_url, href)


def slugify(s):
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:60] or "Deck"


def process_entry(entry, browser, force=False):
    folder = entry["folder"]
    out_dir = os.path.join(ARCHIVE_DIR, folder)
    status_path = os.path.join(out_dir, "status.json")

    if not force and os.path.exists(status_path):
        try:
            with open(status_path) as f:
                prev = json.load(f)
            if prev.get("status") in ("OK", "PARTIAL", "NO_DECKLIST"):
                return prev
        except Exception:
            pass

    os.makedirs(out_dir, exist_ok=True)
    slug = entry["slug"]
    url = ARTICLE_URL_TMPL.format(ts=BASE_TS, slug=slug)
    result = {
        "title": entry["title"], "folder": folder, "slug": slug,
        "date": entry["ymd"], "author": entry.get("author", ""),
        "url": url, "status": "FAILED", "notes": "",
    }

    # 1. fetch HTML for parsing
    try:
        r = fetch(url)
        final_url = r.url
        html_text = r.text
    except Exception as e:
        result["notes"] = f"HTML fetch failed: {e}"
        with open(status_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    with open(os.path.join(out_dir, "source.html"), "w", encoding="utf-8") as f:
        f.write(html_text)

    decks, soup = extract_decks(html_text, final_url)

    # 2. screenshot via playwright
    screenshot_ok = False
    try:
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(url, wait_until="networkidle", timeout=60000)
        try:
            page.add_style_tag(content=HIDE_TOOLBAR_CSS)
        except Exception:
            pass
        try:
            page.evaluate(
                "() => Promise.all(Array.from(document.images).filter(i=>!i.complete)"
                ".map(i=>new Promise(res=>{i.onload=i.onerror=res; setTimeout(res,8000);})))"
            )
        except Exception:
            pass
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(out_dir, "screenshot.png"), full_page=True)
        screenshot_ok = True
        page.close()
    except Exception as e:
        result["notes"] += f" Screenshot failed: {e};"

    # 3. decklists
    saved_any = False
    partial = False
    if not decks:
        result["notes"] += " No div.deck structure found on page;"
    else:
        multi = len(decks) > 1
        for i, d in enumerate(decks, start=1):
            fname = "decklist.txt" if not multi else f"decklist_{i}_{slugify(d['title'])}.txt"
            fpath = os.path.join(out_dir, fname)
            if d["txt_link"]:
                try:
                    txt_url = resolve_wayback_url(d["txt_link"], final_url)
                    tr = fetch(txt_url)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(tr.text)
                    saved_any = True
                    continue
                except Exception as e:
                    result["notes"] += f" txt download failed for deck {i}: {e};"
            # fallback to HTML parse
            if d["maindeck"]:
                content = mtgo_format(d["maindeck"], d["sideboard"])
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                saved_any = True
                if not d["sideboard"]:
                    partial = True
                    result["notes"] += f" deck {i}: no sideboard found;"
            else:
                partial = True
                result["notes"] += f" deck {i}: could not parse maindeck cards;"

    if saved_any and not partial:
        result["status"] = "OK"
    elif saved_any and partial:
        result["status"] = "PARTIAL"
    elif not decks:
        result["status"] = "NO_DECKLIST"
    else:
        result["status"] = "FAILED"

    if not screenshot_ok:
        result["status"] = "FAILED" if result["status"] == "NO_DECKLIST" else result["status"]
        result["notes"] += " SCREENSHOT_MISSING;"

    with open(status_path, "w") as f:
        json.dump(result, f, indent=2)
    return result


def write_log(all_results):
    counts = {}
    for r in all_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines = []
    lines.append("# Building on a Budget — Scrape Log\n")
    lines.append(f"Total articles: {len(all_results)}\n")
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    lines.append(f"Status summary: {summary}\n")
    lines.append("\n| Date | Deck | Status | Notes |")
    lines.append("|---|---|---|---|")
    for r in sorted(all_results, key=lambda x: x["date"]):
        notes = r.get("notes", "").strip().replace("|", "/")
        lines.append(f"| {r['date']} | [{r['title']}](archive/{r['folder']}/) | {r['status']} | {notes} |")
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--slugs", nargs="*", default=None, help="only process these slugs (for testing)")
    args = ap.parse_args()

    with open(INDEX_PATH) as f:
        entries = json.load(f)

    if args.slugs:
        entries = [e for e in entries if e["slug"] in args.slugs]
    else:
        entries = entries[args.start:]
        if args.limit:
            entries = entries[: args.limit]

    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    all_results = []
    # load any prior results for entries not in this run, to keep log.md complete
    with open(INDEX_PATH) as f:
        full_entries = json.load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, entry in enumerate(entries):
            log(f"[{i+1}/{len(entries)}] {entry['ymd']} {entry['title']} ({entry['slug']})")
            try:
                result = process_entry(entry, browser, force=args.force)
            except Exception as e:
                result = {"title": entry["title"], "folder": entry["folder"], "slug": entry["slug"],
                          "date": entry["ymd"], "url": "", "status": "FAILED", "notes": f"unhandled exception: {e}"}
                status_path = os.path.join(ARCHIVE_DIR, entry["folder"], "status.json")
                os.makedirs(os.path.dirname(status_path), exist_ok=True)
                with open(status_path, "w") as sf:
                    json.dump(result, sf, indent=2)
            log(f"   -> {result['status']} {result.get('notes','')}")
            all_results.append(result)
            if (i + 1) % 10 == 0:
                interim = []
                for e in full_entries:
                    sp = os.path.join(ARCHIVE_DIR, e["folder"], "status.json")
                    if os.path.exists(sp):
                        with open(sp) as sf:
                            interim.append(json.load(sf))
                write_log(interim)
            time.sleep(0.6)
        browser.close()

    # rebuild full log.md from all status.json files on disk (covers prior runs too)
    all_status = []
    for entry in full_entries:
        sp = os.path.join(ARCHIVE_DIR, entry["folder"], "status.json")
        if os.path.exists(sp):
            with open(sp) as f:
                all_status.append(json.load(f))
    write_log(all_status)
    log(f"\nDone. Wrote log.md with {len(all_status)} entries.")


if __name__ == "__main__":
    main()
