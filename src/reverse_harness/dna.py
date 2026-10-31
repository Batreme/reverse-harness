import json
from pathlib import Path
from typing import Dict, Any, List
from rich.console import Console

from reverse_harness.config import DEFAULT_FLEET_WIKI_DIR, ROOT_DIR

console = Console()

DNA_DIR = ROOT_DIR / "dna"
ARCHETYPES_FILE = DNA_DIR / "archetypes.json"

DEFAULT_ARCHETYPES = {
    "dark-luxury": {
        "name": "Dark Luxury & High-Performance",
        "description": "High-contrast dark mode with razor-sharp borders, metallic accents, and high-velocity media framing.",
        "inspired_by": ["spykercars-com", "revelatio-studio", "www-aaronbrimhall-com"],
        "colors": {
            "background": "#09090b",
            "surface": "#121215",
            "surface_elevated": "#1c1c21",
            "border": "#27272a",
            "text_primary": "#fafafa",
            "text_muted": "#a1a1aa",
            "accent": "#f59e0b",
            "accent_glow": "#f59e0b22"
        },
        "typography": {
            "heading_font": "var(--font-heading), 'Cabinet Grotesk', sans-serif",
            "body_font": "var(--font-body), 'Inter', sans-serif",
            "mono_font": "'JetBrains Mono', monospace",
            "heading_tracking": "-0.03em",
            "label_tracking": "0.15em"
        },
        "ui_rules": {
            "border_radius": "rounded-sm",
            "navbar_style": "floating-pill-glass",
            "hero_style": "full-bleed-headline-split",
            "grid_style": "bento-sharp"
        }
    },
    "minimal-editorial": {
        "name": "Minimalist Editorial & Spatial Design",
        "description": "Generous negative space, asymmetric layouts, refined typography, and Japanese/Scandinavian minimalism.",
        "inspired_by": ["ondocollective-jp", "wndws-space", "tamasolajos-com", "christoph-nagel-dev"],
        "colors": {
            "background": "#0d0d0e",
            "surface": "#161618",
            "surface_elevated": "#1f1f22",
            "border": "#2c2c30",
            "text_primary": "#f4f4f5",
            "text_muted": "#8e8e93",
            "accent": "#e4e4e7",
            "accent_glow": "#e4e4e715"
        },
        "typography": {
            "heading_font": "var(--font-heading), 'Playfair Display', serif",
            "body_font": "var(--font-body), 'Geist', sans-serif",
            "mono_font": "'Geist Mono', monospace",
            "heading_tracking": "-0.02em",
            "label_tracking": "0.18em"
        },
        "ui_rules": {
            "border_radius": "rounded-none",
            "navbar_style": "minimal-sticky-border",
            "hero_style": "asymmetric-typography-statement",
            "grid_style": "staggered-editorial-list"
        }
    },
    "cinematic-studio": {
        "name": "Cinematic Motion Studio",
        "description": "Bold video-first presentation, glassmorphism overlays, fluid transitions, and immersive case study storytelling.",
        "inspired_by": ["untitledfilm-framer-website", "velourproductions-com", "gallery-play-be"],
        "colors": {
            "background": "#070709",
            "surface": "#0f0f13",
            "surface_elevated": "#18181f",
            "border": "#23232b",
            "text_primary": "#ffffff",
            "text_muted": "#9ca3af",
            "accent": "#6366f1",
            "accent_glow": "#6366f126"
        },
        "typography": {
            "heading_font": "var(--font-heading), 'Plus Jakarta Sans', sans-serif",
            "body_font": "var(--font-body), 'Inter', sans-serif",
            "mono_font": "'Space Mono', monospace",
            "heading_tracking": "-0.04em",
            "label_tracking": "0.12em"
        },
        "ui_rules": {
            "border_radius": "rounded-xl",
            "navbar_style": "glass-blur-top",
            "hero_style": "cinematic-video-preview",
            "grid_style": "fluid-motion-cards"
        }
    },
    "modern-boutique": {
        "name": "Modern Boutique & Curated Lifestyle",
        "description": "Warm luxury accents, elegant product cards, smooth micro-interactions, and high-craft editorial commerce.",
        "inspired_by": ["zeyraglow-com", "www-velaaprivateisland-com", "peaches-world"],
        "colors": {
            "background": "#0a0a0c",
            "surface": "#141418",
            "surface_elevated": "#1e1e24",
            "border": "#2e2e38",
            "text_primary": "#fdfdfd",
            "text_muted": "#a6a6b0",
            "accent": "#e2b887",
            "accent_glow": "#e2b88722"
        },
        "typography": {
            "heading_font": "var(--font-heading), 'Cormorant Garamond', serif",
            "body_font": "var(--font-body), 'Plus Jakarta Sans', sans-serif",
            "mono_font": "'JetBrains Mono', monospace",
            "heading_tracking": "-0.01em",
            "label_tracking": "0.2em"
        },
        "ui_rules": {
            "border_radius": "rounded-lg",
            "navbar_style": "floating-pill-glass",
            "hero_style": "luxury-editorial-banner",
            "grid_style": "masonry-curated"
        }
    }
}

def get_or_build_archetypes() -> Dict[str, Any]:
    """Returns stored archetypes or initializes them from default reverse-engineered profiles."""
    DNA_DIR.mkdir(parents=True, exist_ok=True)
    if not ARCHETYPES_FILE.exists():
        ARCHETYPES_FILE.write_text(json.dumps(DEFAULT_ARCHETYPES, indent=2), encoding="utf-8")
        return DEFAULT_ARCHETYPES
    try:
        return json.loads(ARCHETYPES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_ARCHETYPES

def list_archetypes() -> None:
    """Displays available design archetypes."""
    archetypes = get_or_build_archetypes()
    console.print("\n[bold cyan]🎨 Reverse-Engineered Design Archetypes (Anti-AI-Slop):[/bold cyan]\n")
    for key, data in archetypes.items():
        console.print(f"[bold green]• {key}[/bold green]: [bold]{data['name']}[/bold]")
        console.print(f"  [dim]{data['description']}[/dim]")
        console.print(f"  [cyan]Inspirations:[/cyan] {', '.join(data.get('inspired_by', []))}")
        console.print(f"  [yellow]Colors:[/yellow] bg: {data['colors']['background']} | accent: {data['colors']['accent']}")
        console.print()
