import subprocess
import sys
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.markdown import Markdown

from reverse_harness.config import UPSTREAM_DIR

console = Console()

def route_task(hint: str) -> None:
    """Invokes upstream reverse-skill master routing ladder or parses MASTER-ROUTING.md."""
    console.print(f"[bold cyan]Routing reverse-engineering task with upstream reverse-skill:[/bold cyan] [yellow]{hint}[/yellow]\n")

    master_route_sh = UPSTREAM_DIR / "skills" / "scripts" / "master-route.sh"
    master_routing_md = UPSTREAM_DIR / "skills" / "MASTER-ROUTING.md"

    if master_route_sh.exists():
        try:
            res = subprocess.run(
                ["bash", str(master_route_sh), "--hint", hint],
                cwd=str(UPSTREAM_DIR),
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and res.stdout.strip():
                console.print(res.stdout)
                return
        except Exception:
            pass

    if master_routing_md.exists():
        console.print("[dim]Reading upstream MASTER-ROUTING.md...[/dim]\n")
        content = master_routing_md.read_text(encoding="utf-8")
        
        # Display relevant sections
        console.print(Markdown(content[:2500]))
        console.print(f"\n[dim]Full routing guide available at: {master_routing_md}[/dim]")
    else:
        console.print(f"[yellow]Upstream reverse-skill not found at {UPSTREAM_DIR}. Ensure submodules are initialized.[/yellow]")
