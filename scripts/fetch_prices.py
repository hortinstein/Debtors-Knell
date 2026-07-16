#!/usr/bin/env python3
"""Daily price archiver: GoatBots (MTGO) + MTGJSON (paper).

Run once a day (see .github/workflows/fetch-prices.yml). Writes into ./prices
without ever overwriting a previous day's archive:

  prices/
    daily/<YYYY-MM-DD>/
      goatbots/card-definitions.zip     card id -> name/set/rarity/foil
      goatbots/price-history.zip        that day's per-card tix price snapshot
      mtgjson/AllPricesToday.json.bz2   that day's per-card paper+MTGO snapshot
    goatbots_yearly_archive/<YEAR>/<YEAR-MM-DD>.txt.gz
                                        GoatBots' own yearly bulk history files,
                                        backfilled once each (before GoatBots
                                        rolls them off its site - see
                                        research/PRICE_DATA_SOURCES.md),
                                        unpacked to one gzipped file per day
                                        rather than kept as a single >100MB
                                        zip, since GitHub hard-rejects any
                                        file over 100MB.

GoatBots fronts its site with a Cloudflare managed challenge that blocks bare
HTTP clients (see research/PRICE_DATA_SOURCES.md). The fix used here: load the
page once with a real (headless) browser to clear the challenge and mint a
session cookie, then reuse that cookie with plain `requests` for the actual
zip downloads, which is far lighter than driving a browser for every file.
"""
import argparse
import datetime
import gzip
import io
import os
import sys
import zipfile

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_DIR = os.path.join(REPO_ROOT, "prices")
DAILY_DIR = os.path.join(PRICES_DIR, "daily")
YEARLY_DIR = os.path.join(PRICES_DIR, "goatbots_yearly_archive")

GOATBOTS_BASE = "https://www.goatbots.com"
GOATBOTS_DOWNLOAD_PAGE = f"{GOATBOTS_BASE}/download-prices"
GOATBOTS_FILES = {
    "card-definitions.zip": f"{GOATBOTS_BASE}/download/prices/card-definitions.zip",
    "price-history.zip": f"{GOATBOTS_BASE}/download/prices/price-history.zip",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MTGJSON_URL = "https://mtgjson.com/api/v5/AllPricesToday.json.bz2"


def log(msg):
    print(msg, flush=True)


def goatbots_session():
    """Load the GoatBots download page in a headless browser to clear
    Cloudflare's challenge, then hand its cookies to a plain requests.Session
    for the actual file downloads."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(GOATBOTS_DOWNLOAD_PAGE, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        cookies = context.cookies()
        browser.close()

    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c["domain"])
    return s


def download(session, url, out_path, min_bytes=1000):
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    if len(resp.content) < min_bytes:
        raise RuntimeError(f"suspiciously small response ({len(resp.content)} bytes) for {url}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    log(f"  wrote {out_path} ({len(resp.content):,} bytes)")


def fetch_goatbots_daily(day_dir, force):
    out_dir = os.path.join(day_dir, "goatbots")
    if not force and os.path.isdir(out_dir) and os.listdir(out_dir):
        log("GoatBots: already archived for this run, skipping (use --force to redo)")
        return
    log("GoatBots: clearing Cloudflare challenge...")
    session = goatbots_session()
    for fname, url in GOATBOTS_FILES.items():
        log(f"GoatBots: downloading {fname}")
        download(session, url, os.path.join(out_dir, fname))
    return session


def fetch_goatbots_yearly_archive(session):
    """Backfill any yearly bulk-history zip GoatBots currently lists, one
    entry at a time, unpacked to per-day gzipped files under
    goatbots_yearly_archive/<year>/ (never the raw zip - some years exceed
    GitHub's 100MB single-file limit). Safe/cheap to call every run: days
    already on disk are skipped, so a finished past year costs one HTTP
    request and no writes, while the current (still-accumulating) year only
    writes the handful of days it's missing."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(GOATBOTS_DOWNLOAD_PAGE, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        links = page.eval_on_selector_all("a", "els => els.map(e => e.href)")
        browser.close()

    year_urls = sorted(set(l for l in links if "/download/prices/price-history-20" in l))
    if not year_urls:
        log("GoatBots yearly archive: no year links found on page (layout change?)")
        return

    for url in year_urls:
        fname = os.path.basename(url)  # e.g. price-history-2023.zip
        year = fname.removeprefix("price-history-").removesuffix(".zip")
        year_dir = os.path.join(YEARLY_DIR, year)
        resp = session.get(url, timeout=180)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        new_days = 0
        for name in names:
            # name like price-history-2023-01-01.txt -> store as 2023-01-01.txt.gz
            day = name.removeprefix("price-history-").removesuffix(".txt")
            out_path = os.path.join(year_dir, f"{day}.txt.gz")
            if os.path.exists(out_path):
                continue
            os.makedirs(year_dir, exist_ok=True)
            with gzip.open(out_path, "wb") as f:
                f.write(zf.read(name))
            new_days += 1
        log(f"GoatBots yearly archive: {year} -> {new_days} new day(s) written "
            f"({len(names) - new_days} already had local copies)")


def fetch_mtgjson_daily(day_dir, force):
    out_dir = os.path.join(day_dir, "mtgjson")
    out_path = os.path.join(out_dir, "AllPricesToday.json.bz2")
    if not force and os.path.exists(out_path):
        log("MTGJSON: already archived for this run, skipping (use --force to redo)")
        return
    log("MTGJSON: downloading AllPricesToday.json.bz2")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    download(session, MTGJSON_URL, out_path, min_bytes=100_000)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="UTC date to archive under (YYYY-MM-DD), default = today")
    ap.add_argument("--force", action="store_true", help="Redownload even if today's files exist")
    ap.add_argument("--skip-yearly-check", action="store_true",
                     help="Skip the (cheap, skip-if-present) GoatBots yearly-archive backfill check")
    args = ap.parse_args()

    day = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    day_dir = os.path.join(DAILY_DIR, day)
    os.makedirs(day_dir, exist_ok=True)
    log(f"Archiving prices for {day} into {os.path.relpath(day_dir, REPO_ROOT)}/")

    fetch_mtgjson_daily(day_dir, args.force)

    goatbots_ok = True
    session = None
    try:
        session = fetch_goatbots_daily(day_dir, args.force)
    except Exception as e:
        goatbots_ok = False
        log(f"GoatBots daily fetch FAILED: {e}")

    if not args.skip_yearly_check:
        try:
            if session is None:
                session = goatbots_session()
            fetch_goatbots_yearly_archive(session)
        except Exception as e:
            log(f"GoatBots yearly-archive backfill check FAILED (non-fatal): {e}")

    if not goatbots_ok:
        sys.exit(1)
    log("Done.")


if __name__ == "__main__":
    main()
