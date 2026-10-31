import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from reverse_harness.config import (
    DEFAULT_STORAGE_DIR,
    DEFAULT_RAW_DIR,
    DEFAULT_SESSIONS_DIR,
    DEFAULT_FLEET_WIKI_DIR,
    DEFAULT_DB_PATH,
    UPSTREAM_DIR,
)
from reverse_harness.auth import login_interactive, list_profiles
from reverse_harness.capture import capture_web
from reverse_harness.analyze import analyze_target
from reverse_harness.storage import get_db, index_wiki_targets, search_patterns, render_search_results
from reverse_harness.route import route_task

app = typer.Typer(
    name="reverse-harness",
    help="Autonomous reverse-engineering harness: web capture, de-obfuscation, SQLite+Vector pattern index, and Obsidian dossier sync.",
    add_completion=False,
)

auth_app = typer.Typer(help="Manage authenticated browser sessions.")
app.add_typer(auth_app, name="auth")

console = Console()

@app.command()
def init():
    """Initializes harness storage directories, SQLite database, and checks submodules."""
    console.print("[bold cyan]Initializing reverse-harness environment...[/bold cyan]")
    
    DEFAULT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize SQLite + sqlite-vec
    conn = get_db(DEFAULT_DB_PATH)
    conn.close()
    console.print(f"[bold green]✓ Database initialized at:[/bold green] {DEFAULT_DB_PATH}")

    # Check upstream reverse-skill submodule
    if UPSTREAM_DIR.exists() and (UPSTREAM_DIR / "skills").exists():
        console.print(f"[bold green]✓ Upstream reverse-skill detected at:[/bold green] {UPSTREAM_DIR}")
    else:
        console.print(f"[yellow]⚠️ Upstream reverse-skill submodule not initialized. Run:[/yellow] git submodule update --init --recursive")

    console.print("[bold green]✓ reverse-harness is ready to use![/bold green]")

@auth_app.command("login")
def auth_login(
    profile: str = typer.Argument(..., help="Name of the session profile to save (e.g. 'linear', 'twitter')"),
    url: str = typer.Option(..., "--url", "-u", help="URL to navigate to for authentication"),
):
    """Launches an interactive browser to log in and save session cookies/storage."""
    login_interactive(profile_name=profile, url=url)

@auth_app.command("list")
def auth_list():
    """Lists saved authenticated session profiles."""
    profiles = list_profiles()
    if not profiles:
        console.print("[yellow]No authenticated session profiles found in .sessions/[/yellow]")
        return
    console.print("[bold cyan]Saved Session Profiles:[/bold cyan]")
    for p in profiles:
        console.print(f"  • [green]{p}[/green]")

@app.command()
def capture(
    url: str = typer.Argument(..., help="Target website or application URL"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Target name (defaults to sanitized domain)"),
    auth: Optional[str] = typer.Option(None, "--auth", "-a", help="Authentication profile name to apply"),
    wait: int = typer.Option(5, "--wait", "-w", help="Extra seconds to wait for async scripts/websockets"),
):
    """Performs headless capture of HAR network traffic, bundles, DOM, and client storage."""
    capture_web(url=url, target_name=name, auth_profile=auth, wait_seconds=wait)

@app.command()
def analyze(
    target: str = typer.Argument(..., help="Target name to analyze (must exist in raw/)"),
    wiki_dir: Optional[Path] = typer.Option(None, "--wiki-dir", help="Path to Fleet wiki targets directory"),
):
    """Parses raw capture data and generates the 5-document Obsidian dossier."""
    out_dir = analyze_target(target_name=target, wiki_dir=wiki_dir)
    console.print(f"[bold green]✓ Target '{target}' analyzed. Next, run 'reverse-harness index' to sync vectors.[/bold green]")

@app.command()
def index(
    wiki_dir: Optional[Path] = typer.Option(None, "--wiki-dir", help="Path to Fleet wiki targets directory"),
):
    """Scans all Obsidian target dossiers, extracts chunks, and syncs the SQLite+Vector index."""
    index_wiki_targets(wiki_dir=wiki_dir)

@app.command()
def query(
    search_term: str = typer.Argument(..., help="Natural language search term (e.g. 'optimistic updates', 'auth tokens')"),
    limit: int = typer.Option(5, "--limit", "-l", help="Maximum results to return"),
):
    """Semantically queries the reverse-engineered knowledge base for production patterns."""
    results = search_patterns(query_str=search_term, limit=limit)
    render_search_results(results, query=search_term)

@app.command()
def route(
    task: str = typer.Argument(..., help="Description of reverse engineering task, file, or challenge"),
):
    """Routes an arbitrary reverse engineering task using upstream reverse-skill playbooks."""
    route_task(task)

def main():
    app()

if __name__ == "__main__":
    main()
