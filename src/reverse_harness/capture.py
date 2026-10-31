import json
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Dict, Any
from rich.console import Console
from playwright.sync_api import sync_playwright

from reverse_harness.config import DEFAULT_RAW_DIR, DEFAULT_SESSIONS_DIR
from reverse_harness.auth import get_session_path

console = Console()

def sanitize_target_name(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.replace(":", "_").replace(".", "-")
    path_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", parsed.path).strip("-")
    if path_slug:
        return f"{netloc}_{path_slug}"
    return netloc or "target"

def capture_web(
    url: str,
    target_name: Optional[str] = None,
    auth_profile: Optional[str] = None,
    output_dir: Optional[Path] = None,
    wait_seconds: int = 5,
) -> Path:
    """Performs comprehensive automated headless web capture."""
    name = target_name or sanitize_target_name(url)
    raw_root = output_dir or DEFAULT_RAW_DIR
    target_dir = raw_root / name
    target_dir.mkdir(parents=True, exist_ok=True)

    scripts_dir = target_dir / "scripts"
    styles_dir = target_dir / "styles"
    scripts_dir.mkdir(exist_ok=True)
    styles_dir.mkdir(exist_ok=True)

    har_path = target_dir / "traffic.har"
    screenshot_path = target_dir / "screenshot.png"
    dom_path = target_dir / "dom.html"
    storage_path = target_dir / "storage.json"
    metadata_path = target_dir / "metadata.json"

    console.print(f"[bold cyan]Initiating headless capture for:[/bold cyan] [green]{url}[/green]")
    console.print(f"[dim]Output directory:[/dim] {target_dir}")

    context_kwargs: Dict[str, Any] = {
        "record_har_path": str(har_path),
        "record_har_content": "embed",
        "viewport": {"width": 1440, "height": 900},
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    if auth_profile:
        session_file = get_session_path(auth_profile)
        if session_file.exists():
            console.print(f"[cyan]Applying auth profile:[/cyan] [yellow]{auth_profile}[/yellow]")
            context_kwargs["storage_state"] = str(session_file)
        else:
            console.print(f"[yellow]Warning: Auth profile {auth_profile} not found. Running unauthenticated.[/yellow]")

    collected_scripts = []
    collected_styles = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # Listen to response streams to save assets
        def handle_response(response):
            try:
                res_url = response.url
                c_type = response.headers.get("content-type", "").lower()
                clean_url = res_url.split("?")[0]
                
                # Check for scripts
                if "javascript" in c_type or clean_url.endswith(".js"):
                    fname = clean_url.split("/")[-1] or f"script_{len(collected_scripts)}.js"
                    if not fname.endswith(".js"):
                        fname += ".js"
                    # Prevent filename collisions
                    save_path = scripts_dir / f"{len(collected_scripts):03d}_{fname}"
                    try:
                        body = response.body()
                        save_path.write_bytes(body)
                        collected_scripts.append({"url": res_url, "file": save_path.name, "size": len(body)})
                    except Exception:
                        pass
                
                # Check for styles
                elif "css" in c_type or clean_url.endswith(".css"):
                    fname = clean_url.split("/")[-1] or f"style_{len(collected_styles)}.css"
                    if not fname.endswith(".css"):
                        fname += ".css"
                    save_path = styles_dir / f"{len(collected_styles):03d}_{fname}"
                    try:
                        body = response.body()
                        save_path.write_bytes(body)
                        collected_styles.append({"url": res_url, "file": save_path.name, "size": len(body)})
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", handle_response)

        # Navigate
        console.print("[dim]Navigating and recording network interactions...[/dim]")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        except Exception as e:
            console.print(f"[yellow]Navigation warning:[/yellow] {e}")

        # Sleep extra wait seconds to ensure lazy loaders and async sockets trigger
        if wait_seconds > 0:
            page.wait_for_timeout(wait_seconds * 1000)

        # Capture rendered DOM
        dom_content = page.content()
        dom_path.write_text(dom_content, encoding="utf-8")

        # Capture screenshot
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
            except Exception:
                pass

        # Extract Client Storage (localStorage, sessionStorage)
        storage_data = {}
        try:
            storage_data = page.evaluate("""() => {
                const ls = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    ls[k] = localStorage.getItem(k);
                }
                const ss = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    ss[k] = sessionStorage.getItem(k);
                }
                return { localStorage: ls, sessionStorage: ss };
            }""")
        except Exception:
            pass
        storage_path.write_text(json.dumps(storage_data, indent=2), encoding="utf-8")

        # Page metadata
        metadata = {
            "target_name": name,
            "url": url,
            "title": page.title(),
            "auth_profile": auth_profile,
            "scripts_count": len(collected_scripts),
            "styles_count": len(collected_styles),
            "storage_keys": list(storage_data.get("localStorage", {}).keys()),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        context.close()
        browser.close()

    console.print(f"[bold green]✓ Capture complete for [cyan]{name}[/cyan]![/bold green]")
    console.print(f"  • HAR Network Log: {har_path.stat().st_size if har_path.exists() else 0} bytes")
    console.print(f"  • Scripts Saved: {len(collected_scripts)}")
    console.print(f"  • Styles Saved: {len(collected_styles)}")
    console.print(f"  • DOM Snapshot: {dom_path}")
    console.print(f"  • Storage Dump: {storage_path}")

    return target_dir
