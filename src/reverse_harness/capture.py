import asyncio
import gc
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console

from reverse_harness.auth import get_session_path
from reverse_harness.config import DEFAULT_RAW_DIR
from reverse_harness.live_capture import capture_single_url_standalone

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
    wiki_targets_dir: Optional[Path] = None,
    retry_extended: bool = False,
) -> Path:
    """Performs 3-tier adaptive headless live capture (1440px desktop + 390px mobile + computed styles)."""
    name = target_name or sanitize_target_name(url)
    raw_root = output_dir or DEFAULT_RAW_DIR
    target_dir = raw_root / name
    target_dir.mkdir(parents=True, exist_ok=True)

    storage_state: Optional[str] = None
    if auth_profile:
        session_file = get_session_path(auth_profile)
        if session_file.exists():
            console.print(f"[cyan]Applying auth profile:[/cyan] [yellow]{auth_profile}[/yellow]")
            storage_state = str(session_file)
        else:
            console.print(f"[yellow]Warning: Auth profile {auth_profile} not found. Running unauthenticated.[/yellow]")

    console.print(f"[bold cyan]Initiating 3-tier live capture for:[/bold cyan] [green]{url}[/green]")
    console.print(f"[dim]Output directory:[/dim] {target_dir}")

    meta = asyncio.run(
        capture_single_url_standalone(
            url=url,
            target_name=name,
            raw_dir=raw_root,
            wiki_targets_dir=wiki_targets_dir,
            auth_storage_state=storage_state,
            retry_extended=retry_extended or (wait_seconds >= 8),
        )
    )
    gc.collect()

    status = meta.get("status", "unknown")
    tier = meta.get("capture_tier", "none")
    d_dims = meta.get("desktop_dims", [0, 0])
    m_dims = meta.get("mobile_dims", [0, 0])
    console.print(
        f"[bold green]✓ Capture complete for [cyan]{name}[/cyan][/bold green] "
        f"(status={status}, tier={tier}, http={meta.get('http_status')}, "
        f"desktop={d_dims[0]}x{d_dims[1]}, mobile={m_dims[0]}x{m_dims[1]})"
    )
    if meta.get("block_reason"):
        console.print(f"  [yellow]• Block reason:[/yellow] {meta['block_reason']}")

    return target_dir
