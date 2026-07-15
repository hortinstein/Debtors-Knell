#!/usr/bin/env python3
import re, json, time, os
from datetime import datetime
import requests
from bs4 import BeautifulSoup

BASE_TS = "20090601202842"
LISTING_URL_TMPL = ("https://web.archive.org/web/{ts}/https://www.wizards.com/Magic/Magazine/"
                     "Archive.aspx?page={page}&tag=Building%20on%20a%20Budget&description=Building%20on%20a%20Budget")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArchiveResearchBot/1.0; +personal-archival-project)"})

def fetch_listing_page(page_idx, retries=3):
    url = LISTING_URL_TMPL.format(ts=BASE_TS, page=page_idx)
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"  retry page {page_idx} attempt {attempt+1}: {e}")
            time.sleep(2)
    raise RuntimeError(f"failed to fetch listing page {page_idx}")

def parse_listing(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    entries = []
    for li in soup.select("li"):
        a = li.find("a", href=re.compile(r"^Article\.aspx\?x="))
        if not a:
            continue
        title_span = a.find("span", class_="title")
        date_span = a.find("span", class_="date")
        author_span = a.find("span", class_="author")
        if not title_span or not date_span:
            continue
        title = title_span.get_text(strip=True)
        if title == "Article Title":
            continue
        date_str = date_span.get_text(strip=True)
        author = author_span.get_text(strip=True) if author_span else ""
        href = a["href"]
        m = re.search(r"x=([^&]+)", href)
        slug = m.group(1)
        entries.append({"title": title, "date_str": date_str, "author": author, "slug": slug})
    return entries

def parse_date(date_str):
    date_str = date_str.strip()
    dt = datetime.strptime(date_str, "%A, %b %d, %Y")
    return dt.strftime("%Y%m%d")

def slugify(title):
    s = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s[:80] or "Untitled"

def main():
    all_entries = []
    for page in range(13):
        print(f"Fetching listing page {page+1}/13 ...")
        html_text = fetch_listing_page(page)
        entries = parse_listing(html_text)
        print(f"  found {len(entries)} entries")
        for e in entries:
            e["page"] = page
        all_entries.extend(entries)
        time.sleep(0.4)

    # dedupe by slug, preserve first occurrence order
    seen = set()
    deduped = []
    for e in all_entries:
        if e["slug"] in seen:
            continue
        seen.add(e["slug"])
        deduped.append(e)

    # compute date + folder name, with collision handling
    folder_counts = {}
    for e in deduped:
        try:
            ymd = parse_date(e["date_str"])
        except ValueError:
            ymd = "00000000"
        e["ymd"] = ymd
        base_name = f"{ymd}_{slugify(e['title'])}"
        count = folder_counts.get(base_name, 0)
        folder_counts[base_name] = count + 1
        e["folder_base"] = base_name

    # apply suffix for collisions
    seen_names = {}
    for e in deduped:
        base = e["folder_base"]
        if folder_counts[base] > 1:
            n = seen_names.get(base, 0) + 1
            seen_names[base] = n
            e["folder"] = f"{base}_{n}"
        else:
            e["folder"] = base

    print(f"\nTotal unique entries: {len(deduped)}")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "master_index.json")
    with open(out_path, "w") as f:
        json.dump(deduped, f, indent=2)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    main()
