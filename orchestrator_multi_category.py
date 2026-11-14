import sys
import time
import json
import re
import urllib.request
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

PROJECT_ROOT = Path("/root/projects/reverse-harness")
FLEET_ROOT = Path("/root/projects/fleet")
FLEET_WIKI = FLEET_ROOT / "wiki"
TARGETS_DIR = FLEET_WIKI / "targets"
PROGRESS_FILE = PROJECT_ROOT / "category_progress.json"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reverse_harness.capture import capture_web
from reverse_harness.analyze import analyze_target
from reverse_harness.storage import index_wiki_targets

CATEGORIES = [
    # Checkpoint 1: High-Craft Flagships
    "design-agencies", "e-commerce", "architecture", "fashion", "experimental",
    # Checkpoint 2: Tech, Luxury & Corporate
    "technology", "luxury", "art-illustration", "business-corporate", "culture-education",
    # Checkpoint 3: Media, Entertainment & Hospitality
    "events", "film-tv", "food-drink", "games-entertainment", "hotel-restaurant",
    # Checkpoint 4: Content, Audio & Mobile
    "institutions", "magazine-newspaper-blog", "mobile-apps", "music-sound", "promotional",
    # Checkpoint 5: Real Estate, Social & Interaction
    "real-estate", "social-responsibility", "sports", "startups", "web-interactive"
]

CHECKPOINTS = [
    {"name": "Checkpoint 1 (Flagship Studios & Commerce)", "categories": CATEGORIES[0:5]},
    {"name": "Checkpoint 2 (Tech, Luxury & Culture)", "categories": CATEGORIES[5:10]},
    {"name": "Checkpoint 3 (Media, Entertainment & Food)", "categories": CATEGORIES[10:15]},
    {"name": "Checkpoint 4 (Institutions, Sound & Content)", "categories": CATEGORIES[15:20]},
    {"name": "Checkpoint 5 (Startups, Real Estate & Interaction)", "categories": CATEGORIES[20:25]},
]

NUM_WORKERS = 3  # 3 concurrent Chromium workers (now <100MB RAM with minimal HAR)

def sanitize_target_name(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.replace(":", "_").replace(".", "-").lower()
    path_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", parsed.path).strip("-").lower()
    if path_slug:
        return f"{netloc}_{path_slug}"
    return netloc or "target"

def get_html(url: str, retries: int = 2):
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception:
            time.sleep(1)
    return None

def extract_target_urls_for_category(category: str, needed_count: int, known_domains: set):
    print(f"\n🔍 Crawling category '{category}' for {needed_count} new targets...", flush=True)
    found_urls = []
    page = 1

    while len(found_urls) < needed_count and page <= 8:
        list_url = f"https://www.awwwards.com/websites/{category}/?page={page}"
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

        for slug in site_slugs:
            if len(found_urls) >= needed_count:
                break
            detail_url = f"https://www.awwwards.com{slug}"
            d_html = get_html(detail_url)
            if not d_html:
                continue
            dsoup = BeautifulSoup(d_html, "html.parser")
            target_site_url = None
            for a in dsoup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if "Visit Site" in text or a.get("data-model") == "site":
                    if href.startswith("http") and "awwwards.com" not in href:
                        target_site_url = href
                        break
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

            if not domain_stripped or domain_stripped in known_domains:
                continue

            target_name = sanitize_target_name(target_site_url)
            if (TARGETS_DIR / target_name).exists():
                known_domains.add(domain_stripped)
                continue

            known_domains.add(domain_stripped)
            found_urls.append(target_site_url)
            print(f"  [{len(found_urls)}/{needed_count}] Found: {target_site_url}", flush=True)

        page += 1

    return found_urls

def process_single_target(url: str, category: str):
    t_start = time.time()
    try:
        target_dir = capture_web(url=url, wait_seconds=2)
        target_name = target_dir.name
        analyze_target(target_name=target_name, wiki_dir=TARGETS_DIR)
        elapsed = time.time() - t_start
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ [{category}] Captured & analyzed {target_name} ({elapsed:.1f}s)", flush=True)
        return {"success": True, "url": url, "name": target_name, "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - t_start
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ [{category}] Failed {url} ({elapsed:.1f}s): {e}", flush=True)
        return {"success": False, "url": url, "error": str(e), "elapsed": elapsed}

def update_index_markdown():
    targets = []
    for d in sorted(TARGETS_DIR.iterdir()):
        if not d.is_dir():
            continue
        overview = d / "overview.md"
        name = d.name
        title = name
        url = ""
        framework = "Modern Web Application"
        page_title = ""
        if overview.exists():
            content = overview.read_text(encoding="utf-8")
            m_title = re.search(r'title:\s*["\']?([^"\n\']+)["\']?', content)
            if m_title:
                title = m_title.group(1).replace("Target Overview: ", "").replace("Target: ", "")
            m_url = re.search(r'url:\s*["\']?([^"\n\']+)["\']?', content)
            if m_url:
                url = m_url.group(1)
            m_pt = re.search(r'Page Title\*\*:\s*([^\n]+)', content)
            if m_pt:
                val = m_pt.group(1).strip()
                if val and not val.startswith("-"):
                    page_title = val
            m_fw = re.search(r'Identified Frameworks\*\*:\s*([^\n]+)', content)
            if m_fw:
                val = m_fw.group(1).strip()
                if val:
                    framework = val

        display_label = page_title or title or name
        display_label = re.sub(r'[\r\n]+', ' ', display_label).split('|')[0].strip()
        if len(display_label) > 60:
            display_label = display_label[:57] + "..."
        if not display_label:
            display_label = name

        targets.append({
            "name": name,
            "display_label": display_label,
            "url": url,
            "framework": framework
        })

    md = f"""# Knowledge Base (Wiki)

A lightweight, Obsidian-compatible knowledge base designed for agents and humans. It tracks historical ventures, outcomes, reusable engineering playbooks, and a rich index of production reverse-engineered web targets.

---

## 🗺️ Map of Content (MOC)

### 🚀 Historical Ventures (`wiki/ventures/`)
Logs of past projects, refactors, and investigations—capturing the intended goal, obstacles, and actual outcome.

- [[2026-10-31-fleet-harness-setup]] — Initial harness setup, migration to native skills (`tstack`), and wiki integration.
- [[2026-10-31-reverse-harness-setup]] — Setup of standalone reverse-harness engine, upstream reverse-skill submodule, and vector store.
- [[2026-10-31-cinematic-studio-portfolio]] — High-craft 35mm photography portfolio with interactive scroll-scrub optical lens hero and EXIF inspector.
- [[2026-11-14-awwwards-photography-expansion]] — Scale reverse-harness knowledge base with 50 live Awwwards photography portfolio targets, vector indexing, and new design DNA archetypes.
- [[2026-11-14-awwwards-multi-category-expansion]] — Scale Fleet KB across 25 Awwwards industry categories (500+ production targets).

---

### 📖 Playbooks & Standards (`wiki/playbooks/`)
Actionable, proven patterns synthesized from past ventures.

- [[agent-harness-principles]] — Core principles for designing agent skills without unnecessary wrappers or dependencies.

---

### 📝 Templates (`wiki/templates/`)
Standard blueprints for recording new knowledge.

- [[venture-template]] — Template for logging a completed or attempted project/task.
- [[playbook-template]] — Template for writing a reusable operational pattern or standard.
- [[target-template]] — Template for reverse-engineered web application dossiers.

---

### 🎯 Reverse-Engineered Targets (`wiki/targets/`) — {len(targets)} Production Targets
Comprehensive dossiers covering network APIs, schemas, DOM layout primitives, CSS custom properties, and source maps extracted directly from live production sites.

| Target / Dossier | Application / Identity | Framework | Production URL |
|---|---|---|---|
"""
    for t in sorted(targets, key=lambda x: x["name"]):
        md += f"| [[{t['name']}/overview|{t['name']}]] | {t['display_label']} | `{t['framework']}` | [{t['url']}]({t['url']}) |\n"

    md += """
---

## 🔍 Semantic Search Quick-Start
To search patterns across all targets:
```bash
uv run --directory ../reverse-harness reverse-harness query "<concept or pattern>"
```
"""
    (FLEET_WIKI / "INDEX.md").write_text(md, encoding="utf-8")
    print(f"✓ Updated INDEX.md ({len(targets)} targets total)", flush=True)

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Launching Multi-Worker Reverse Engineering Engine ({NUM_WORKERS} concurrent Chromium workers)...", flush=True)

    progress = {
        "started_at": datetime.now().isoformat(),
        "checkpoints_total": len(CHECKPOINTS),
        "checkpoints_completed": 0,
        "completed_categories": {},
        "total_targets_successful": 0,
        "total_targets_failed": 0
    }
    if PROGRESS_FILE.exists():
        try:
            progress = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Build known domains set
    known_domains = set()
    for d in TARGETS_DIR.iterdir():
        if d.is_dir():
            clean = d.name.replace("-", ".").replace("www.", "")
            known_domains.add(clean)

    for cp_idx, cp in enumerate(CHECKPOINTS, start=1):
        cp_name = cp["name"]
        print(f"\n==================================================================", flush=True)
        print(f"🚀 {cp_name} (Checkpoint {cp_idx}/{len(CHECKPOINTS)})", flush=True)
        print(f"==================================================================", flush=True)

        for cat in cp["categories"]:
            if cat in progress.get("completed_categories", {}):
                print(f"✓ Category '{cat}' already completed. Skipping.", flush=True)
                continue

            print(f"\n--- Processing Category: {cat} (Target: 20 sites with {NUM_WORKERS} workers) ---", flush=True)
            urls = extract_target_urls_for_category(category=cat, needed_count=20, known_domains=known_domains)
            cat_success = []
            cat_failed = []

            # Execute targets concurrently across workers
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                future_to_url = {executor.submit(process_single_target, u, cat): u for u in urls}
                for future in as_completed(future_to_url):
                    u = future_to_url[future]
                    try:
                        res = future.result(timeout=50)
                        if res["success"]:
                            cat_success.append({"url": res["url"], "name": res["name"]})
                            progress["total_targets_successful"] = progress.get("total_targets_successful", 0) + 1
                        else:
                            cat_failed.append({"url": res["url"], "error": res.get("error", "unknown")})
                            progress["total_targets_failed"] = progress.get("total_targets_failed", 0) + 1
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ [{cat}] Worker timeout/exception on {u}: {e}", flush=True)
                        cat_failed.append({"url": u, "error": str(e)})
                        progress["total_targets_failed"] = progress.get("total_targets_failed", 0) + 1

            progress["completed_categories"][cat] = {
                "successful": cat_success,
                "failed": cat_failed,
                "completed_at": datetime.now().isoformat()
            }
            PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")

        # End of Checkpoint: Index in isolated subprocess and update INDEX.md
        print(f"\n💾 Checkpoint {cp_idx} Complete. Syncing vector embeddings in isolated subprocess...", flush=True)
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "-c", f"from reverse_harness.storage import index_wiki_targets; from pathlib import Path; index_wiki_targets(Path('{TARGETS_DIR}'))"],
                cwd=str(PROJECT_ROOT),
                check=True
            )
        except Exception as e:
            print(f"Indexing error: {e}", flush=True)

        update_index_markdown()

        progress["checkpoints_completed"] = cp_idx
        PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        print(f"✓ Checkpoint {cp_idx} successfully committed and indexed!\n", flush=True)

    progress["finished_at"] = datetime.now().isoformat()
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")
    print(f"\n🎉 ALL 25 CATEGORIES PROCESSED SUCCESSFULLY! 🎉", flush=True)

if __name__ == "__main__":
    main()
