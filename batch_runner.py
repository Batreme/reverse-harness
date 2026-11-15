import os
import sys
import time
import json
import traceback
from pathlib import Path
from datetime import datetime

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent
FLEET_WIKI = Path(os.environ.get("FLEET_ROOT", PROJECT_ROOT.parent / "fleet")) / "wiki"
TARGETS_DIR = FLEET_WIKI / "targets"
TARGETS_FILE = PROJECT_ROOT / "targets_50.txt"
PROGRESS_FILE = PROJECT_ROOT / "batch_progress.json"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reverse_harness.capture import capture_web
from reverse_harness.analyze import analyze_target
from reverse_harness.storage import index_wiki_targets

def run_batch():
    urls = [line.strip() for line in TARGETS_FILE.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]
    total = len(urls)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting autonomous batch capture for {total} targets...", flush=True)

    progress = {
        "started_at": datetime.now().isoformat(),
        "total": total,
        "completed": 0,
        "successful": [],
        "failed": [],
        "current": None
    }
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    start_time = time.time()

    for idx, url in enumerate(urls, start=1):
        progress["current"] = {"index": idx, "url": url}
        PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        print(f"\n=======================================================", flush=True)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Target {idx}/{total}: {url}", flush=True)
        print(f"=======================================================", flush=True)

        try:
            target_dir = capture_web(url=url, wait_seconds=3)
            target_name = target_dir.name
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Captured: {target_name}. Analyzing...", flush=True)

            target_wiki = analyze_target(target_name=target_name, wiki_dir=TARGETS_DIR)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Dossier written to: {target_wiki}", flush=True)

            progress["successful"].append({
                "index": idx,
                "url": url,
                "target_name": target_name,
                "wiki_dir": str(target_wiki)
            })
        except Exception as e:
            err_msg = str(e)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ ERROR on {url}: {err_msg}", flush=True)
            progress["failed"].append({
                "index": idx,
                "url": url,
                "error": err_msg
            })

        progress["completed"] = idx
        PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    elapsed = time.time() - start_time
    print(f"\n\n=======================================================", flush=True)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch capture complete in {elapsed/60:.1f} minutes!", flush=True)
    print(f"Successful: {len(progress['successful'])} / {total}", flush=True)
    print(f"Failed: {len(progress['failed'])} / {total}", flush=True)
    print(f"=======================================================", flush=True)

    # Now run indexing
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Re-indexing all dossiers into SQLite+sqlite-vec...", flush=True)
    try:
        index_wiki_targets(wiki_dir=TARGETS_DIR)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Indexing complete!", flush=True)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Indexing error: {e}", flush=True)

    progress["finished_at"] = datetime.now().isoformat()
    progress["elapsed_seconds"] = elapsed
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")

if __name__ == "__main__":
    run_batch()
