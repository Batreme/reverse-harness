import json
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from playwright.sync_api import sync_playwright

from reverse_harness.config import DEFAULT_SESSIONS_DIR

console = Console()

def get_session_path(profile_name: str, sessions_dir: Optional[Path] = None) -> Path:
    s_dir = sessions_dir or DEFAULT_SESSIONS_DIR
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir / f"{profile_name}.json"

def login_interactive(profile_name: str, url: str, sessions_dir: Optional[Path] = None) -> Path:
    """Launches an interactive browser window allowing the user to authenticate manually."""
    s_dir = sessions_dir or DEFAULT_SESSIONS_DIR
    s_dir.mkdir(parents=True, exist_ok=True)
    session_file = get_session_path(profile_name, s_dir)

    console.print(f"[bold cyan]Launching interactive browser for profile:[/bold cyan] [green]{profile_name}[/green]")
    console.print(f"[dim]Navigate and log in at:[/dim] {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        # Restore prior state if it exists
        context_kwargs = {}
        if session_file.exists():
            console.print(f"[yellow]Loaded existing session from {session_file}[/yellow]")
            context_kwargs["storage_state"] = str(session_file)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(url)

        console.print("\n[bold yellow]➡️ Log in to the application in the opened browser window.[/bold yellow]")
        console.print("[bold yellow]➡️ Once logged in, press [ENTER] here in the terminal to save your session state...[/bold yellow]")
        
        try:
            input()
        except EOFError:
            pass

        context.storage_state(path=str(session_file))
        browser.close()

    console.print(f"[bold green]✓ Session successfully saved to:[/bold green] {session_file}")
    return session_file

def list_profiles(sessions_dir: Optional[Path] = None) -> List[str]:
    s_dir = sessions_dir or DEFAULT_SESSIONS_DIR
    if not s_dir.exists():
        return []
    return [p.stem for p in s_dir.glob("*.json")]
