import re
import urllib.request
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from pathlib import Path

WIKI_TARGETS_DIR = Path("/root/projects/fleet/wiki/targets")
OUTPUT_FILE = Path("/root/projects/reverse-harness/targets_50.txt")

# Get existing domains/slugs
existing_targets = set()
if WIKI_TARGETS_DIR.exists():
    for d in WIKI_TARGETS_DIR.iterdir():
        if d.is_dir():
            existing_targets.add(d.name.lower())

print(f"Loaded {len(existing_targets)} existing targets in wiki.")

def sanitize_target_name(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.replace(":", "_").replace(".", "-").lower()
    path_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", parsed.path).strip("-").lower()
    if path_slug:
        return f"{netloc}_{path_slug}"
    return netloc or "target"

def get_html(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

found_urls = []
already_present = []
seen_domains = set()

page_num = 1
while len(found_urls) < 50 and page_num <= 10:
    list_url = f"https://www.awwwards.com/websites/photography/?page={page_num}"
    print(f"\n--- Scraping Awwwards Listing Page {page_num} ({list_url}) ---")
    html = get_html(list_url)
    if not html:
        break
    
    soup = BeautifulSoup(html, "html.parser")
    site_slugs = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/sites/" in href:
            clean_slug = href.split("?")[0].rstrip("/")
            if clean_slug not in site_slugs:
                site_slugs.append(clean_slug)
    
    print(f"Page {page_num}: Found {len(site_slugs)} site links on Awwwards.")
    
    for slug in site_slugs:
        if len(found_urls) >= 50:
            break
        
        detail_url = f"https://www.awwwards.com{slug}"
        detail_html = get_html(detail_url)
        if not detail_html:
            continue
        
        dsoup = BeautifulSoup(detail_html, "html.parser")
        target_site_url = None
        
        # Look for Visit Site link
        for a in dsoup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "Visit Site" in text or a.get("data-model") == "site":
                if href.startswith("http") and "awwwards.com" not in href:
                    target_site_url = href
                    break
        
        # Fallback: search for external links in detail page
        if not target_site_url:
            for a in dsoup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and not any(x in href for x in ["awwwards.com", "facebook.com", "twitter.com", "instagram.com", "linkedin.com", "youtube.com", "google.com", "pinterest.com"]):
                    target_site_url = href
                    break
        
        if not target_site_url:
            continue
        
        parsed = urlparse(target_site_url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain_stripped = domain[4:]
        else:
            domain_stripped = domain
        
        target_name = sanitize_target_name(target_site_url)
        
        # Check against existing targets
        matches_existing = False
        for ex in existing_targets:
            if domain_stripped in ex.replace("-", ".") or ex in target_name:
                matches_existing = True
                break
        
        if matches_existing:
            already_present.append((slug, target_site_url))
            print(f"  [ALREADY PRESENT] {slug} -> {target_site_url}")
            continue
        
        if domain_stripped in seen_domains:
            continue
        seen_domains.add(domain_stripped)
        
        found_urls.append(target_site_url)
        print(f"  [{len(found_urls)}/50 NEW] {slug} -> {target_site_url}")

    page_num += 1

print(f"\n==========================================")
print(f"Extraction complete! Found {len(found_urls)} new photography targets.")
print(f"Encountered {len(already_present)} previously existing targets.")

OUTPUT_FILE.write_text("\n".join(found_urls) + "\n", encoding="utf-8")
print(f"Saved targets to {OUTPUT_FILE}")
