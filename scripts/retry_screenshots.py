#!/usr/bin/env python3
"""Retry screenshot capture only, for folders whose status.json notes mention SCREENSHOT_MISSING."""
import os, json, glob, re
from playwright.sync_api import sync_playwright

REPO_ROOT = "/workspaces/Debtors-Knell"
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
BASE_TS = "20090601202842"
ARTICLE_URL_TMPL = "https://web.archive.org/web/{ts}/https://www.wizards.com/Magic/Magazine/Article.aspx?x={slug}"

HIDE_TOOLBAR_CSS = """
#wm-ipp-base, #wm-ipp, #donato, #wm-ipp-print { display: none !important; }
html { margin-top: 0 !important; }
body { margin-top: 0 !important; }
"""


def log(msg):
    print(msg, flush=True)


def take_screenshot(page, url, out_path):
    page.goto(url, wait_until="load", timeout=90000)
    try:
        page.add_style_tag(content=HIDE_TOOLBAR_CSS)
    except Exception:
        pass
    try:
        page.evaluate(
            "() => Promise.all(Array.from(document.images).filter(i=>!i.complete)"
            ".map(i=>new Promise(res=>{i.onload=i.onerror=res; setTimeout(res,15000);})))"
        )
    except Exception:
        pass
    page.wait_for_timeout(500)
    page.screenshot(path=out_path, full_page=True)


def recompute_status(folder_dir, prev):
    txt_files = [f for f in os.listdir(folder_dir) if f.endswith(".txt") and f.startswith("decklist")]
    notes = prev.get("notes", "")
    notes_clean = notes.replace("SCREENSHOT_MISSING;", "").strip()
    if not txt_files:
        status = "NO_DECKLIST" if "No div.deck structure" in notes else "FAILED"
    elif "no sideboard found" in notes or "could not parse maindeck" in notes:
        status = "PARTIAL"
    else:
        status = "OK"
    return status, notes_clean


def main():
    targets = []
    for sp in glob.glob(os.path.join(ARCHIVE_DIR, "*", "status.json")):
        with open(sp) as f:
            d = json.load(f)
        if "SCREENSHOT_MISSING" in d.get("notes", ""):
            targets.append((sp, d))

    log(f"Found {len(targets)} folders needing screenshot retry")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, (sp, d) in enumerate(targets):
            folder_dir = os.path.dirname(sp)
            slug = d["slug"]
            url = ARTICLE_URL_TMPL.format(ts=BASE_TS, slug=slug)
            out_path = os.path.join(folder_dir, "screenshot.png")
            log(f"[{i+1}/{len(targets)}] {d['folder']} ({slug})")
            ok = False
            last_err = None
            for attempt in range(3):
                try:
                    page = browser.new_page(viewport={"width": 1100, "height": 900})
                    take_screenshot(page, url, out_path)
                    page.close()
                    ok = True
                    break
                except Exception as e:
                    last_err = e
                    try:
                        page.close()
                    except Exception:
                        pass
                    log(f"   attempt {attempt+1} failed: {e}")
            if ok:
                status, notes_clean = recompute_status(folder_dir, d)
                d["status"] = status
                d["notes"] = notes_clean
                log(f"   -> screenshot OK, status={status}")
            else:
                d["notes"] = d.get("notes", "") + f" retry_failed: {last_err};"
                log(f"   -> still failed after 3 attempts")
            with open(sp, "w") as f:
                json.dump(d, f, indent=2)
        browser.close()

    remaining = 0
    for sp in glob.glob(os.path.join(ARCHIVE_DIR, "*", "status.json")):
        with open(sp) as f:
            d = json.load(f)
        if "SCREENSHOT_MISSING" in d.get("notes", ""):
            remaining += 1
    log(f"\nDone. {remaining} folders still missing screenshots.")


if __name__ == "__main__":
    main()
