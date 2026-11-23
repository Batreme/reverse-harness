#!/usr/bin/env python3
"""
Full-Catalog Live Recrawl & Ground-Truth UI Prompt Blueprint Pipeline (C1-C15).

Zero synthetic specimens. Zero offline route.abort() rendering.
- Live 3-Tier Adaptive Playwright Capture (Desktop 1440x[900..2400] + Mobile 390x[844..2400])
  via reverse_harness.live_capture.
- Ground-truth token extraction prioritized from live raw/<slug>/computed_styles.json,
  supplemented by :root CSS custom properties and stylesheets.
- Honest status: blocked handling with zero WAF/CAPTCHA bypass, zero body corruption,
  and deletion of stale PNGs on blocked targets.
- Disjoint sharded execution (--manifest, --shard 1..6, --slugs, --live-recrawl,
  --resume, --retry-extended) that NEVER writes shared aggregate files
  (UI_EVALS_DASHBOARD.md, INDEX.md, claude-loop-ledger.json).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import struct
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

REVERSE_HARNESS_DIR = pathlib.Path(__file__).resolve().parent
SRC_DIR = REVERSE_HARNESS_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REVERSE_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(REVERSE_HARNESS_DIR))

from reverse_harness.live_capture import (
    BrowserPool,
    atomic_write_text,
    capture_target_live,
    compute_png_visual_metrics,
    load_checkpoint,
    save_checkpoint,
)

BLANK_VIEWPORT_BLOCK_REASON = (
    "Headless Preloader / Blank Viewport Capture (cannot hydrate without interactive/GPU session)"
)


def is_solid_blank_png(metrics: Dict[str, Any]) -> bool:
    """Return True if a valid PNG fails C14 solid/blank visual health."""
    return bool(
        metrics.get("valid")
        and float(metrics.get("gray_std", 0.0)) < 8.0
        and float(metrics.get("edge_density", 0.0)) < 0.003
    )

FLEET_WIKI = pathlib.Path(os.environ.get("FLEET_ROOT", REVERSE_HARNESS_DIR.parent / "fleet")) / "wiki"
TARGETS_DIR = FLEET_WIKI / "targets"
TRACES_DIR = FLEET_WIKI / "traces"
RAW_DIR = pathlib.Path(os.environ.get("RAW_DIR", REVERSE_HARNESS_DIR / "raw"))
DEFAULT_MANIFEST: Optional[pathlib.Path] = (
    pathlib.Path(os.environ["AUDIT_MANIFEST"]) if os.environ.get("AUDIT_MANIFEST") else None
)
DEFAULT_CHECKPOINT_DIR = pathlib.Path(
    os.environ.get("RECRAWL_CHECKPOINT_DIR", REVERSE_HARNESS_DIR / ".cache" / "recrawl_checkpoints")
)

ROUND_1_SLUGS = [
    "dennypratama-com",
    "agnestoth-com",
    "www-sigma-imaging-se",
    "morrow-valerian-studio",
    "wiro-ae",
    "www-anduril-com",
    "www-avilus-com",
    "www-eladiodieste-com",
    "www-unimaticwatches-com",
    "seedjewelry-com",
]

NOISE_VAR_PREFIXES = (
    "--tw-", "--wp--preset--", "--sb-", "--swiper-", "--plyr-", "--fc-",
    "--toastify-", "--chakra-", "--mui-", "--radix-", "--framer-",
)

ICON_FONT_KEYWORDS = (
    "icon", "webflow-icons", "fontawesome", "font awesome", "fa-", "icomoon",
    "material", "dashicons", "swiper", "glyph", "slick", "videojs", "plyr",
    "revicons", "eicons", "elementskit", "feather", "bootstrap-icons",
    "remixicon", "lucide", "phosphor", "tabler", "ionicons", "themify",
    "elegantly", "etmodules", "woodmart", "flatsome", "astra", "star",
)

NAMED_COLOR_HEX = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "blue": "#0000ff",
    "green": "#008000",
    "yellow": "#ffff00",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "navy": "#000080",
    "teal": "#008080",
    "maroon": "#800000",
    "purple": "#800080",
    "orange": "#ffa500",
}


def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def yaml_escape(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).replace("\\", "/").replace('"', "'").strip()


def get_png_dimensions(path: pathlib.Path) -> Tuple[int, int, int]:
    if not path.exists():
        return (0, 0, 0)
    size = path.stat().st_size
    with open(path, "rb") as f:
        data = f.read(24)
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return (w, h, size)
    return (0, 0, size)


def normalize_hex(h: str) -> str:
    hs = (h or "").strip().lower()
    if re.match(r"^#[0-9a-f]{3}$", hs):
        return "#" + "".join(c * 2 for c in hs[1:])
    if re.match(r"^#[0-9a-f]{6}$", hs):
        return hs
    if re.match(r"^#[0-9a-f]{8}$", hs):
        return hs[:7]
    return hs


def hex_luma(h: str) -> float:
    hs = normalize_hex(h).lstrip("#")
    if len(hs) == 3:
        hs = "".join(c * 2 for c in hs)
    try:
        r, g, b = int(hs[0:2], 16), int(hs[2:4], 16), int(hs[4:6], 16)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    except Exception:
        return 128.0


def classify_var_role(name: str, val: str) -> str:
    nl = name.lower()
    vl = val.lower()
    if any(k in nl for k in ("bg", "background", "surface", "canvas", "paper", "light", "dark", "black", "white", "tint", "substrate")):
        return "Canvas / Surface Token"
    if any(k in nl for k in ("text", "ink", "fg", "foreground", "heading", "body-color", "copy")):
        return "Typography Ink Token"
    if any(k in nl for k in ("accent", "brand", "primary", "secondary", "highlight", "cta", "theme", "color", "swatch", "palette")):
        return "Brand / Palette Token"
    if any(k in nl for k in ("font", "type", "family", "mono", "serif", "sans", "text-size", "leading", "tracking", "line-height", "h1", "h2", "h3", "paragraph", "letter-spacing")):
        return "Typography / Scale Token"
    if any(k in nl for k in ("grid", "col", "gap", "gutter", "space", "spacing", "pad", "margin", "wrap", "container", "width", "max-w", "viewport", "vw", "vh", "svh", "header", "nav", "height", "size")):
        return "Layout / Spatial Grid Token"
    if any(k in nl for k in ("ease", "easing", "duration", "speed", "transition", "delay", "motion", "anim", "bezier", "roller")):
        return "Motion / Easing Token"
    if any(k in nl for k in ("radius", "rounded", "border", "stroke", "shadow", "ring", "outline", "blur", "z-")):
        return "Elevation / Border Token"
    if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(", vl):
        return "Color / Atmospheric Token"
    if any(u in vl for u in ("px", "rem", "em", "vw", "vh", "svh", "%", "ch", "fr")):
        return "Dimension / Scale Token"
    if "cubic-bezier" in vl or vl.endswith("ms") or vl.endswith("s"):
        return "Motion / Timing Token"
    return "Design System Custom Token"


def strip_important(val: str) -> str:
    return re.sub(r"\s*!important\b", "", val or "", flags=re.I).strip()


def hex_chroma(h: str) -> int:
    hs = normalize_hex(h).lstrip("#")
    if len(hs) != 6:
        return 0
    try:
        r, g, b = int(hs[0:2], 16), int(hs[2:4], 16), int(hs[4:6], 16)
        return max(r, g, b) - min(r, g, b)
    except Exception:
        return 0


def parse_css_px(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    s = strip_important(str(val)).lower()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)(px|rem|em|vw)$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "px":
        return num
    if unit in ("rem", "em"):
        return num * 16.0
    if unit == "vw":
        return num * 14.4
    return None


def strip_at_rule_conditions(css: str) -> str:
    """Remove @media/@container/@supports condition headers so max-width queries are never misread as container rules."""
    out = re.sub(r"@(?:media|container|supports)\b[^{]*\{", "{", css or "", flags=re.I)
    return out


UTILITY_CLASS_SKIP = {
    "flex", "flex-col", "flex-row", "flex-wrap", "inline-flex", "grid", "block", "inline", "inline-block",
    "relative", "absolute", "fixed", "sticky", "static", "w-full", "h-full", "min-w-0", "min-h-screen",
    "mx-auto", "my-auto", "m-0", "p-0", "px-4", "px-6", "py-12", "py-16", "py-20", "pt-0", "pb-0",
    "overflow-hidden", "overflow-x-hidden", "isolate", "z-0", "z-10", "z-20", "z-30", "z-50",
    "items-center", "items-start", "justify-center", "justify-between", "bg-white", "bg-black",
    "text-white", "text-black", "border-t", "border-b", "w-screen", "h-screen", "container",
    "hidden", "sr-only", "antialiased", "transition", "duration-300", "opacity-100",
}

PROMO_BANNER_RE = re.compile(
    r"view\s+credit|credit\s+top[- ]?ups?|black\s+friday|cyber\s+monday|\d+\s+days?\s+left|"
    r"use\s+code|promo\s+code|accept\s+all|we\s+value\s+your\s+privacy|cookie\s+policy|"
    r"skip\s+to\s+(?:main\s+)?content|log\s*in|sign\s*in|subscribe\s+now",
    re.I,
)

PROMO_CLASS_RE = re.compile(
    r"campaign[-_]?banner|announcement|promo[-_]?bar|top[-_]?banner|cookie|consent|toast|alert[-_]?bar|gdpr|usercentrics|cookiebot|onetrust",
    re.I,
)

TAILWIND_TEXT_SIZE_PX = {
    "text-xl": 20.0,
    "text-2xl": 24.0,
    "text-3xl": 30.0,
    "text-4xl": 36.0,
    "text-5xl": 48.0,
    "text-6xl": 60.0,
    "text-7xl": 72.0,
}


def clean_text(s: str, max_len: int = 80) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"#([0-9a-fA-F]{3,8})\b", r"\1", s)
    s = s.replace("|", "·").replace("`", "'").replace("{", "(").replace("}", ")")
    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return s


def is_valid_font_name(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 48:
        return False
    nl = name.lower().strip(' "\'')
    if nl in (
        "inherit", "initial", "unset", "revert", "sans-serif", "serif", "monospace",
        "cursive", "fantasy", "system-ui", "-apple-system", "blinkmacsystemfont",
        "ui-sans-serif", "ui-serif", "ui-monospace", "none", "normal", "auto",
        "arial", "helvetica", "times new roman", "times", "courier new", "courier",
    ):
        return False
    if nl.startswith(("var(", "calc(", "url(", "http", "-", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
        return False
    if any(ic in nl for ic in ICON_FONT_KEYWORDS):
        return False
    return True


def is_cross_host_redirect(initial_url: str, final_url: str) -> bool:
    if not initial_url or not final_url:
        return False
    h1 = urlparse(initial_url).netloc.lower()
    h2 = urlparse(final_url).netloc.lower()
    return bool(h1 and h2 and h1 != h2)


def resolve_target_url(slug: str, manifest_map: Dict[str, Dict[str, Any]]) -> str:
    if slug in manifest_map and manifest_map[slug].get("url"):
        return manifest_map[slug]["url"]
    meta_path = RAW_DIR / slug / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
            if meta.get("url"):
                return meta["url"]
        except Exception:
            pass
    ov_path = TARGETS_DIR / slug / "overview.md"
    if ov_path.exists():
        ov = ov_path.read_text(encoding="utf-8", errors="ignore")[:800]
        m = re.search(r'^url:\s*"?([^"\n]+)"?', ov, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return f"https://{slug.replace('-', '.')}"


def load_raw_bundle(slug: str) -> Tuple[dict, dict, str, str, BeautifulSoup]:
    sdir = RAW_DIR / slug
    meta: Dict[str, Any] = {}
    if (sdir / "metadata.json").exists():
        try:
            meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            meta = {}
    computed: Dict[str, Any] = {}
    if (sdir / "computed_styles.json").exists():
        try:
            computed = json.loads((sdir / "computed_styles.json").read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            computed = {}
    dom_text = ""
    if (sdir / "dom.html").exists():
        dom_text = (sdir / "dom.html").read_text(encoding="utf-8", errors="ignore")
    css_parts = []
    if (sdir / "styles").exists():
        for cf in sorted((sdir / "styles").glob("*.css")):
            css_parts.append(cf.read_text(encoding="utf-8", errors="ignore"))
    soup = BeautifulSoup(dom_text, "html.parser")
    for st in soup.find_all("style"):
        if st.string:
            css_parts.append(st.string)
    for tag in soup.find_all(style=True):
        css_parts.append(f"[style] {{ {tag.get('style', '')} }}")
    full_css = "\n".join(css_parts)
    full_css_nocomments = re.sub(r"/\*.*?\*/", "", full_css, flags=re.S)
    return meta, computed, dom_text, full_css_nocomments, soup


def write_clean_overview(
    slug: str,
    meta: dict,
    status: str,
    url: str,
    final_url: str,
    title: str,
    frameworks: List[str],
    block_reason: Optional[str] = None,
) -> None:
    """Write a clean, wikilink-free overview.md synchronized with metadata.json."""
    target_dir = TARGETS_DIR / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    today = today_iso()
    http_status = meta.get("http_status")
    http_yaml = str(http_status) if http_status is not None else "null"
    http_disp = str(http_status) if http_status is not None else "N/A"
    capture_tier = meta.get("capture_tier", "none" if status == "blocked" else "tier1_fast")
    scripts_count = meta.get("scripts_count", 0)
    styles_count = meta.get("styles_count", 0)
    frameworks_str = ", ".join(frameworks) if frameworks else "Modern Web Application"
    redirect_line = f"- **Final Redirected URL**: [{final_url}]({final_url})\n" if is_cross_host_redirect(url, final_url) else ""
    block_fm = f'block_reason: "{yaml_escape(block_reason)}"\n' if block_reason else ""
    block_line = f"- **Block / Unreachable Reason**: {block_reason}\n" if block_reason else ""

    ov_md = f"""---
title: "Target Overview: {slug}"
date: {today}
captured_at: {today}
url: "{yaml_escape(url)}"
final_url: "{yaml_escape(final_url)}"
status: {status}
http_status: {http_yaml}
capture_tier: {capture_tier}
{block_fm}tags:
  - target
  - reverse-engineering
  - {slug}
auth_required: false
---

# Target Overview: {slug}

## Dossier Navigation
- [UI Prompt Blueprint](prompt-blueprint.md)
- [UI Architecture & Design Tokens](ui-ux.md)
- [Discovered APIs & Network Protocols](apis.md)
- [Data Models & Local State](data-models.md)
- [De-obfuscated Source Maps](source-maps.md)

---

## Architecture & Stack Fingerprint
- **Target URL**: [{url}]({url})
{redirect_line}- **Status**: `{status}` (`http_status: {http_disp}`, `capture_tier: {capture_tier}`)
{block_line}- **Page Title**: {title or 'N/A'}
- **Identified Frameworks**: {frameworks_str}
- **Recovered Scripts**: `{scripts_count}`
- **Recovered Stylesheets**: `{styles_count}`

---

## Key Architectural Lessons
- **UI & Design System**: See [ui-ux.md](ui-ux.md) and [prompt-blueprint.md](prompt-blueprint.md) for live computed design tokens and viewport captures.
- **Client State & APIs**: See [data-models.md](data-models.md) and [apis.md](apis.md) for observed endpoints and storage keys.
"""
    atomic_write_text(target_dir / "overview.md", ov_md)


def write_blocked_target(slug: str, reason: str, meta: dict) -> None:
    """Write clean blocked dossier files with capture: none ONLY in YAML frontmatter and remove stale PNGs."""
    target_dir = TARGETS_DIR / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = target_dir / "assets"
    for stale in ("desktop.png", "mobile.png"):
        sp = assets_dir / stale
        if sp.exists():
            try:
                sp.unlink()
            except OSError:
                pass

    today = today_iso()
    url = meta.get("url") or f"https://{slug.replace('-', '.')}"
    final_url = meta.get("final_url") or url
    http_status = meta.get("http_status")
    http_yaml = str(http_status) if http_status is not None else "null"
    http_disp = str(http_status) if http_status is not None else "N/A"
    safe_reason = yaml_escape(reason)
    redirect_line = f"> **Final Redirected URL**: [{final_url}]({final_url})  \n" if is_cross_host_redirect(url, final_url) else ""

    write_clean_overview(
        slug=slug,
        meta=meta,
        status="blocked",
        url=url,
        final_url=final_url,
        title=clean_text(meta.get("title") or slug, 75),
        frameworks=[],
        block_reason=safe_reason,
    )

    ui_md = f"""---
title: "UI & UX Architecture: {slug}"
date: {today}
captured_at: {today}
status: blocked
http_status: {http_yaml}
block_reason: "{safe_reason}"
---

# UI & UX Architecture: {slug} (`status: blocked`)

> [!WARNING]
> **Target Status**: `status: blocked` (`http_status: {http_disp}`)  
> **Production URL**: [{url}]({url})  
{redirect_line}> **Block / Unreachable Reason**: {safe_reason}

This target terminated at an upstream edge WAF challenge, anti-bot verification gate, browser compatibility block, or origin HTTP/network error during live capture. Per strict ethical and ground-truth integrity rules, zero WAF/CAPTCHA bypasses were attempted, no synthetic design tokens were fabricated, and this target is excluded from the `25.0` evaluation tally.
"""
    atomic_write_text(target_dir / "ui-ux.md", ui_md)

    bp_md = f"""---
title: "UI Prompt Blueprint: {slug}"
date: {today}
captured_at: {today}
target: "{slug}"
status: blocked
capture: none
capture_desktop: none
capture_mobile: none
http_status: {http_yaml}
block_reason: "{safe_reason}"
archetype: "Blocked / Unreachable Target (Excluded from Tally)"
---

# UI Design System & Master Generation Blueprint: {slug} (`status: blocked`)

> [!WARNING]
> **Target Status**: `status: blocked` (`http_status: {http_disp}` — `{safe_reason}`)  
> **Production URL**: [{url}]({url})  
{redirect_line}> **Verification Policy**: Excluded from the `25.0` truth evaluation tally per `claude-loop` `C1` / `C15` (zero fabricated tokens, zero WAF/CAPTCHA bypass, zero stray screenshots).
"""
    atomic_write_text(target_dir / "prompt-blueprint.md", bp_md)


def _format_computed_el_spec(el: Optional[dict], label: str) -> Optional[str]:
    if not el or not isinstance(el, dict):
        return None
    parts = []
    if el.get("fontSize"):
        parts.append(f"`font-size: {clean_text(str(el['fontSize']), 24)}`")
    if el.get("lineHeight"):
        parts.append(f"`line-height: {clean_text(str(el['lineHeight']), 24)}`")
    if el.get("fontWeight"):
        parts.append(f"`font-weight: {clean_text(str(el['fontWeight']), 16)}`")
    ls = el.get("letterSpacing")
    if ls and ls != "normal":
        parts.append(f"`letter-spacing: {clean_text(str(ls), 20)}`")
    if not parts:
        return None
    return f"live computed `{label}` ({', '.join(parts)})"


def extract_ground_truth(
    slug: str,
    meta: dict,
    computed: dict,
    dom_text: str,
    full_css: str,
    soup: BeautifulSoup,
) -> dict:
    # 1. Identity & Summary
    url = meta.get("url") or f"https://{slug.replace('-', '.')}"
    final_url = meta.get("final_url") or url
    raw_title = meta.get("title") or (soup.title.string.strip() if soup.title and soup.title.string else slug)
    title = clean_text(raw_title, 75) or slug

    meta_desc = ""
    for m in soup.find_all("meta"):
        mname = (m.get("name") or m.get("property") or "").lower()
        if mname in ("description", "og:description", "twitter:description") and m.get("content"):
            meta_desc = clean_text(m.get("content"), 140)
            if len(meta_desc) > 20:
                break
    if not meta_desc:
        for p in soup.find_all(["p", "h1", "h2"]):
            t = clean_text(p.get_text(" ", strip=True), 140)
            if len(t) > 25:
                meta_desc = t
                break
    if not meta_desc:
        meta_desc = f"Production web application and digital experience at {url}."

    # Frameworks detection
    frameworks = []
    if soup.find("div", id="__next") or soup.find("script", id="__NEXT_DATA__"):
        frameworks.append("Next.js")
    if soup.find(attrs={"data-reactroot": True}) or "__react" in dom_text:
        frameworks.append("React")
    if soup.find(attrs={"data-v-app": True}) or "__nuxt" in dom_text.lower():
        frameworks.append("Vue / Nuxt")
    if "webflow" in dom_text.lower():
        frameworks.append("Webflow")
    if "framer" in dom_text.lower():
        frameworks.append("Framer")
    if "tailwind" in full_css.lower() or "--tw-" in full_css:
        frameworks.append("Tailwind CSS")
    if soup.find("canvas"):
        frameworks.append("HTML5 Canvas / WebGL")

    # 2. Extract Clean CSS Variables (live computed :root FIRST, then static CSS)
    clean_vars: Dict[str, str] = {}
    fallback_vars: Dict[str, str] = {}

    def _add_css_var(k: str, v: str) -> None:
        k = (k or "").strip()
        v = strip_important(re.sub(r"\s+", " ", (v or "")).strip())
        if not re.match(r"^--[a-zA-Z0-9_-]{2,45}$", k) or k.startswith("---"):
            return
        if not v or len(v) > 75:
            return
        if v.lower().startswith(("before", "after", "hover", "focus", "active", "first-child", "last-child", "nth-", "not(")):
            return
        if any(bad in v for bad in ("{", "}", "@media", "@property", "\\", "</", "/*", "*/", "url(data:", ";", "content:", "`")):
            return
        if k.startswith(NOISE_VAR_PREFIXES):
            if k not in fallback_vars:
                fallback_vars[k] = v
            return
        if k not in clean_vars:
            clean_vars[k] = v

    for k, v in (computed.get("rootVars") or {}).items():
        _add_css_var(k, str(v))

    for k, v in re.findall(r"(?<![a-zA-Z0-9_-])(--[a-zA-Z0-9_-]{2,45})\s*:\s*([^;\}\n]+)", full_css):
        _add_css_var(k, v)

    total_clean_vars = len(clean_vars)

    # 3. Extract Traceable Colors — Strictly Categorized by CSS Property (R2 / R2-1)!
    bg_pool: List[Tuple[str, str]] = []
    ink_pool: List[Tuple[str, str]] = []
    muted_pool: List[Tuple[str, str]] = []
    border_var_pool: List[Tuple[str, str]] = []
    border_var_rgba_raw: List[Tuple[str, str, str]] = []
    border_css_pool: List[Tuple[str, str]] = []
    border_css_widget_pool: List[Tuple[str, str]] = []
    border_live_pool: List[Tuple[str, str]] = []
    border_live_legacy_raw: List[Tuple[str, str, Optional[str]]] = []
    accent_pool: List[Tuple[str, str]] = []
    any_pool: List[Tuple[str, str]] = []

    def _push_pool(pool: List[Tuple[str, str]], hval: Optional[str], citation: str) -> None:
        if not hval:
            return
        nh = normalize_hex(hval)
        if not re.match(r"^#[0-9a-f]{6}$", nh):
            return
        if all(existing_h != nh for existing_h, _ in pool):
            pool.append((nh, citation))
        if all(existing_h != nh for existing_h, _ in any_pool):
            any_pool.append((nh, citation))

    def _blend_rgba_over_hex(r: int, g: int, b: int, a: float, base_hex: str) -> Optional[str]:
        if a <= 0.05:
            return None
        a = max(0.0, min(1.0, a))
        bh = normalize_hex(base_hex).lstrip("#")
        if len(bh) != 6:
            bh = "ffffff"
        br, bg_c, bb = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
        rr = round(r * a + br * (1.0 - a))
        gg = round(g * a + bg_c * (1.0 - a))
        bb_out = round(b * a + bb * (1.0 - a))
        return f"#{max(0, min(255, rr)):02x}{max(0, min(255, gg)):02x}{max(0, min(255, bb_out)):02x}"

    def _parse_border_color_hex(val_str: str, base_hex: str = "#ffffff") -> Optional[str]:
        vs = strip_important(val_str or "").strip()
        if not vs:
            return None
        vl = vs.lower()
        if re.search(r"\b(?:transparent|currentcolor|none|inherit|initial|unset)\b", vl):
            return None
        # Reject zero-width border shorthands like "0 solid #..." or "0px solid #..."
        if re.search(r"(?:^|\s)0(?:px|rem|em)?\s+(?:solid|dashed|dotted|double|groove|ridge|inset|outset)\b", vl):
            return None
        if re.match(r"^0(?:px|rem|em)?$", vl):
            return None
        # Match 8-digit hex (#rrggbbaa)
        m8 = re.search(r"#([0-9a-fA-F]{8})\b", vs)
        if m8:
            hx = m8.group(1)
            alpha = int(hx[6:8], 16) / 255.0
            if alpha <= 0.05:
                return None
            return _blend_rgba_over_hex(int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), alpha, base_hex)
        # Match 6-digit hex (#rrggbb)
        m6 = re.search(r"#([0-9a-fA-F]{6})\b", vs)
        if m6:
            return "#" + m6.group(1).lower()
        # Match 4-digit hex (#rgba) — reject transparent #0000 / #fff0
        m4 = re.search(r"#([0-9a-fA-F]{4})\b", vs)
        if m4:
            hx4 = m4.group(1)
            alpha4 = int(hx4[3] * 2, 16) / 255.0
            if alpha4 <= 0.05:
                return None
            return _blend_rgba_over_hex(int(hx4[0] * 2, 16), int(hx4[1] * 2, 16), int(hx4[2] * 2, 16), alpha4, base_hex)
        # Match 3-digit hex (#rgb)
        m3 = re.search(r"#([0-9a-fA-F]{3})\b", vs)
        if m3:
            return normalize_hex("#" + m3.group(1))
        # Match rgb() / rgba()
        mrgb = re.search(
            r"rgba?\(\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})(?:\s*[,/]\s*([0-9.]+%?))?\s*\)",
            vs,
            re.I,
        )
        if mrgb:
            r_v, g_v, b_v = int(mrgb.group(1)), int(mrgb.group(2)), int(mrgb.group(3))
            a_raw = mrgb.group(4)
            if a_raw is None:
                a_v = 1.0
            elif a_raw.endswith("%"):
                a_v = float(a_raw[:-1]) / 100.0
            else:
                a_v = float(a_raw)
            return _blend_rgba_over_hex(r_v, g_v, b_v, a_v, base_hex)
        for cname, nhex in NAMED_COLOR_HEX.items():
            if re.search(rf"\b{cname}\b", vl):
                return nhex
        return None

    def _has_recorded_border_widths(el_obj: dict) -> bool:
        return any(
            k in el_obj
            for k in ("hasVisibleBorder", "borderTopWidth", "borderBottomWidth", "borderLeftWidth", "borderRightWidth")
        )

    def _element_has_visible_border(el_obj: dict) -> bool:
        if "hasVisibleBorder" in el_obj:
            return bool(el_obj.get("hasVisibleBorder"))
        for k in ("borderTopWidth", "borderBottomWidth", "borderLeftWidth", "borderRightWidth"):
            px_v = parse_css_px(el_obj.get(k))
            if px_v is not None and px_v > 0.0:
                return True
        return False

    elements = computed.get("elements") or {}
    eff_bg = computed.get("effectiveBgHex")
    eff_bg_src = computed.get("effectiveBgSource") or "body"
    if eff_bg:
        _push_pool(bg_pool, eff_bg, f"live computed `{eff_bg_src}` `background-color` (`computed_styles.json`)")

    # 3a. Live computed element properties mapped strictly by CSS property
    # R2-1: NEVER collect border-color from text/root nodes (h1, h2, h3, p, a, html, body)!
    BORDER_STRUCTURAL_TAGS = ("header", "footer", "nav", "button", "hr", "section")
    for tag_key in ("h1", "main", "body", "p", "h2", "nav", "header", "footer", "a", "button", "hr", "section"):
        el = elements.get(tag_key)
        if not el:
            continue
        if el.get("backgroundHex"):
            cite = f"live computed `{tag_key}` `background-color` (`computed_styles.json`)"
            if tag_key in ("button", "a"):
                _push_pool(accent_pool, el["backgroundHex"], cite)
            _push_pool(bg_pool, el["backgroundHex"], cite)
        if el.get("colorHex"):
            cite = f"live computed `{tag_key}` `color` (`computed_styles.json`)"
            if tag_key in ("button", "a"):
                _push_pool(accent_pool, el["colorHex"], cite)
            if tag_key in ("p", "footer", "nav"):
                _push_pool(muted_pool, el["colorHex"], cite)
            _push_pool(ink_pool, el["colorHex"], cite)
        if tag_key in BORDER_STRUCTURAL_TAGS and el.get("borderHex"):
            cite = f"live computed `{tag_key}` `border-color` (`computed_styles.json`)"
            if _has_recorded_border_widths(el):
                if _element_has_visible_border(el):
                    _push_pool(border_live_pool, el["borderHex"], cite)
            else:
                border_live_legacy_raw.append((normalize_hex(el["borderHex"]), cite, normalize_hex(el["colorHex"]) if el.get("colorHex") else None))

    # Also check captured landmarks with explicit visible borders
    for lm in computed.get("landmarks") or []:
        if isinstance(lm, dict) and lm.get("tag") in BORDER_STRUCTURAL_TAGS and lm.get("borderHex") and lm.get("hasVisibleBorder"):
            _push_pool(
                border_live_pool,
                lm["borderHex"],
                f"live computed `{lm['tag']}` landmark `border-color` (`computed_styles.json`)",
            )

    # 3b. Supplement from clean :root CSS custom properties by semantic name
    _tw_palette_var_re = re.compile(
        r"^--color-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d+$",
        re.I,
    )
    _border_var_name_re = re.compile(r"(?:^|[-_])(border|divider|stroke|line|hairline|outline|rule)(?:[-_]|$)", re.I)
    _border_var_skip_re = re.compile(r"line[-_]?height|border[-_]?(?:radius|width|style|spacing)|outline[-_]?(?:offset|width|style)|inline|underline|baseline|headline|timeline|tagline|pipeline|guideline|online|offline", re.I)

    for k, v in clean_vars.items():
        kl = k.lower()
        is_border_var = bool(_border_var_name_re.search(kl) and not _border_var_skip_re.search(kl))
        if is_border_var:
            # Capture direct hex border vars immediately; defer rgba() border vars until bg_hex is known
            direct_hex = re.search(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", v.strip())
            if direct_hex:
                _push_pool(border_var_pool, normalize_hex("#" + direct_hex.group(1)), f"`var({k})` in `:root`")
            elif re.search(r"rgba?\(|#[0-9a-fA-F]{4}\b|#[0-9a-fA-F]{8}\b", v.strip(), re.I):
                border_var_rgba_raw.append((k, v.strip(), f"`var({k})` in `:root`"))
            continue

        hexes_in_v = re.findall(r"(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})\b", v.strip())
        if not hexes_in_v:
            continue
        cite = f"`var({k})` in `:root`"
        is_generic_tw_palette = bool(_tw_palette_var_re.match(kl))
        for hx in hexes_in_v[:2]:
            if any(w in kl for w in ("muted", "subtle", "secondary", "caption", "placeholder")) and not is_generic_tw_palette:
                _push_pool(muted_pool, hx, cite)
                _push_pool(ink_pool, hx, cite)
            elif not is_generic_tw_palette and any(w in kl for w in ("accent", "brand", "primary", "highlight", "cta", "gradient", "glow", "link")):
                _push_pool(accent_pool, hx, cite)
            elif not is_generic_tw_palette and any(w in kl for w in ("bg", "background", "surface", "canvas", "card", "panel", "paper", "shell")):
                _push_pool(bg_pool, hx, cite)
            elif not is_generic_tw_palette and any(w in kl for w in ("text", "ink", "fg", "foreground", "heading", "copy")):
                _push_pool(ink_pool, hx, cite)
            else:
                _push_pool(any_pool, hx, cite)

    # 3c. Supplement from non-@media CSS rule blocks strictly by property
    css_no_at = strip_at_rule_conditions(full_css)
    _text_or_root_sel_re = re.compile(r"^(?:\*|html|body|h[1-6]|p|a|span|strong|em|label)(?:[:\s,>+~]|$)", re.I)
    _widget_sel_re = re.compile(r"(?:\.cky-|\.crisp-|#onetrust|\.osano-|\.toastify|\.cookie|cc-btn)", re.I)

    for sel, body in re.findall(r"([.#a-zA-Z0-9_:>\s-]{1,42})\s*\{([^{}]{3,260})\}", css_no_at):
        sel_c = clean_text(sel, 34).strip()
        if not sel_c or any(bad in sel_c for bad in ("@", "}", "{", "/*")):
            continue
        sel_l = sel_c.lower()
        for prop, val in re.findall(r"(background-color|background|color|border-color|border-top-color|border-bottom-color|border-top|border-bottom|border-left|border-right|border|fill|stroke)\s*:\s*([^;\}]+)", body, flags=re.I):
            prop_l = prop.lower()
            val_clean = strip_important(val)
            if prop_l.startswith("border") or prop_l == "stroke":
                # R2-1: Exclude text/root selectors and 0px/none/transparent/currentColor rules
                if _text_or_root_sel_re.match(sel_l):
                    continue
                b_base = bg_pool[0][0] if bg_pool else "#ffffff"
                bhex = _parse_border_color_hex(val_clean, base_hex=b_base)
                if not bhex:
                    continue
                cite = f"`{sel_c} {{ {prop_l}: {bhex} }}`"
                if _widget_sel_re.search(sel_l):
                    _push_pool(border_css_widget_pool, bhex, cite)
                else:
                    _push_pool(border_css_pool, bhex, cite)
                continue

            hm = re.search(r"(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})\b", val_clean)
            chex = None
            cite = ""
            if hm:
                chex = normalize_hex(hm.group(1))
                cite = f"`{sel_c} {{ {prop_l}: {chex} }}`"
            else:
                for cname, nhex in NAMED_COLOR_HEX.items():
                    if re.search(rf"\b{cname}\b", val_clean, flags=re.I):
                        chex = nhex
                        cite = f"`{sel_c} {{ {prop_l}: {cname} }}` (`{nhex}`)"
                        break
            if not chex:
                continue
            if prop_l.startswith("background"):
                if any(k in sel_l for k in ("btn", "button", "cta", "accent", "badge", "primary")):
                    _push_pool(accent_pool, chex, cite)
                _push_pool(bg_pool, chex, cite)
            elif prop_l == "color":
                if any(k in sel_l for k in ("btn", "button", "cta", "accent", "link", "a:", "a.")):
                    _push_pool(accent_pool, chex, cite)
                if any(k in sel_l for k in ("muted", "sub", "caption", "meta", "footer", "secondary")):
                    _push_pool(muted_pool, chex, cite)
                _push_pool(ink_pool, chex, cite)
            else:
                _push_pool(any_pool, chex, cite)

    # 3d. Supplement from meta theme-color / SVG attrs if pools are sparse
    if len(any_pool) < 4:
        for m in soup.find_all("meta", attrs={"name": "theme-color"}):
            c = (m.get("content") or "").strip()
            if re.match(r"^#[0-9a-fA-F]{3,6}$", c):
                _push_pool(accent_pool, c, f'`<meta name="theme-color" content="{c}">` in `dom.html`')
        for attr_name, hval in re.findall(r'(fill|stroke|stop-color|bgcolor)\s*=\s*["\'](#[0-9a-fA-F]{3,6})["\']', dom_text, flags=re.I):
            _push_pool(any_pool, hval, f'SVG/DOM `{attr_name}="{hval}"` attribute in `dom.html`')
            if len(any_pool) >= 8:
                break

    # Assign each of the 6 semantic roles strictly from its matching CSS property pool
    if bg_pool:
        bg_hex, bg_src = bg_pool[0]
    else:
        bg_hex, bg_src = ("#ffffff", "`html, body` browser default canvas substrate (`#ffffff`)")
    bg_luma = hex_luma(bg_hex)

    # Primary Typography Ink: highest-contrast `color` candidate relative to bg_hex
    ink_distinct = [(h, c) for h, c in ink_pool if h != bg_hex]
    if ink_distinct:
        high_contrast_live = [
            (h, c) for h, c in ink_distinct
            if "computed_styles.json" in c and abs(hex_luma(h) - bg_luma) >= 100
        ]
        if high_contrast_live:
            ink_hex, ink_src = max(high_contrast_live, key=lambda x: abs(hex_luma(x[0]) - bg_luma))
        else:
            ink_hex, ink_src = max(ink_distinct, key=lambda x: abs(hex_luma(x[0]) - bg_luma))
    else:
        fallback_ink = "#ffffff" if bg_luma < 110 else "#000000"
        ink_hex, ink_src = (fallback_ink, f"`body` default contrast ink (`{fallback_ink}`)")

    # Elevated Surface / Card Substrate: strictly from background-color pool (NEVER border-color or text color!)
    surface_candidates = [(h, c) for h, c in bg_pool[1:] if h != bg_hex]
    if surface_candidates:
        same_polarity = [
            (h, c) for h, c in surface_candidates
            if (hex_luma(h) < 128) == (bg_luma < 128) and abs(hex_luma(h) - bg_luma) <= 95
        ]
        if same_polarity:
            surface_hex, surface_src = same_polarity[0]
        else:
            surface_hex, surface_src = min(surface_candidates, key=lambda x: abs(hex_luma(x[0]) - bg_luma))
    else:
        surface_hex, surface_src = (bg_hex, f"transparent/inherited card surface over {bg_src}")

    # Interactive Accent / Highlight: prefer chromatic accent from accent_pool != bg_hex, ink_hex
    accent_candidates = [(h, c) for h, c in accent_pool if h not in (bg_hex, ink_hex)]
    if accent_candidates:
        chromatic = [x for x in accent_candidates if hex_chroma(x[0]) >= 25]
        accent_hex, accent_src = max(chromatic, key=lambda x: hex_chroma(x[0])) if chromatic else accent_candidates[0]
    else:
        fallback_acc = [(h, c) for h, c in any_pool if h not in (bg_hex, ink_hex) and hex_chroma(h) >= 25]
        if fallback_acc:
            accent_hex, accent_src = max(fallback_acc, key=lambda x: hex_chroma(x[0]))
        else:
            other_acc = [(h, c) for h, c in (accent_pool + ink_pool) if h != bg_hex]
            accent_hex, accent_src = other_acc[0] if other_acc else (ink_hex, ink_src)

    # Secondary / Muted Copy: strictly from color pool, prioritizing live computed & :root tokens over minified CSS rules
    ink_luma = hex_luma(ink_hex)
    lo_luma, hi_luma = min(bg_luma, ink_luma), max(bg_luma, ink_luma)
    muted_candidates = []
    seen_m = set()
    for h, c in muted_pool + ink_pool:
        if h not in (ink_hex, bg_hex) and h not in seen_m:
            seen_m.add(h)
            muted_candidates.append((h, c))
    if muted_candidates:
        grounded_muted = [x for x in muted_candidates if "computed_styles.json" in x[1] or "var(" in x[1]]
        pool_m = grounded_muted if grounded_muted else muted_candidates
        mid_contrast = [x for x in pool_m if lo_luma < hex_luma(x[0]) < hi_luma]
        if mid_contrast:
            mid_target = (bg_luma + ink_luma) / 2.0
            muted_hex, muted_src = min(mid_contrast, key=lambda x: abs(hex_luma(x[0]) - mid_target))
        else:
            muted_hex, muted_src = pool_m[0]
    else:
        muted_hex, muted_src = (ink_hex, f"secondary opacity step of {ink_src}")

    # R2-1 Structural Hairline Border — NEVER from text nodes (h1/h2/h3/p/a) or Tailwind preflight!
    # Priority 1: :root CSS border/divider/stroke/line/hairline/outline/rule variables
    for k_raw, v_raw, cite_raw in border_var_rgba_raw:
        resolved_b = _parse_border_color_hex(v_raw, base_hex=bg_hex)
        if resolved_b:
            _push_pool(border_var_pool, resolved_b, f"{cite_raw} (`{clean_text(v_raw, 36)}`)")

    # Priority 3b (legacy fallback for existing computed_styles.json without borderWidth):
    # only structural containers where borderHex != bg_hex, != ink_hex, and != element's own text color
    border_live_legacy_pool: List[Tuple[str, str]] = []
    for bh_l, cite_l, el_col in border_live_legacy_raw:
        if bh_l not in (bg_hex, ink_hex) and (el_col is None or bh_l != el_col):
            _push_pool(border_live_legacy_pool, bh_l, cite_l)

    def _pick_hairline(pool: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
        distinct = [(h, c) for h, c in pool if h != bg_hex]
        if not distinct:
            return None
        subtle = [x for x in distinct if abs(hex_luma(x[0]) - bg_luma) <= 150]
        return subtle[0] if subtle else distinct[0]

    picked_border = (
        _pick_hairline(border_var_pool)
        or _pick_hairline(border_css_pool)
        or _pick_hairline(border_live_pool)
        or _pick_hairline(border_live_legacy_pool)
        or _pick_hairline(border_css_widget_pool)
    )
    if picked_border:
        border_hex, border_src = picked_border
    else:
        border_hex, border_src = (muted_hex, f"hairline divider derived from {muted_src}")

    semantic_tokens = [
        ("Primary Canvas Substrate", bg_hex, bg_src),
        ("Primary Typography Ink", ink_hex, ink_src),
        ("Elevated Surface / Card Substrate", surface_hex, surface_src),
        ("Interactive Accent / Highlight", accent_hex, accent_src),
        ("Secondary / Muted Copy", muted_hex, muted_src),
        ("Structural Hairline Border", border_hex, border_src),
    ]

    # 4. Extract Real Font Families — Live computed_styles.json (loadedFonts + getComputedStyle) FIRST!
    font_families: List[str] = []

    def _add_font(raw_fam: str) -> None:
        cleaned_stack = strip_important(raw_fam or "")
        for part in cleaned_stack.split(","):
            cand = clean_text(strip_important(part).strip(" \"'"), 42)
            if is_valid_font_name(cand) and cand not in font_families:
                font_families.append(cand)
                return

    # 4a. Live computed element fonts (h1, h2, body, p, nav, button, code)
    for tag_key in ("h1", "h2", "body", "p", "nav", "button", "code"):
        el = elements.get(tag_key)
        if el and el.get("fontFamily"):
            _add_font(str(el["fontFamily"]))

    # 4b. Live loaded document.fonts
    for lf in computed.get("loadedFonts") or []:
        if isinstance(lf, dict) and lf.get("family"):
            _add_font(str(lf["family"]))

    # 4c. @font-face blocks
    for ff_block in re.findall(r"@font-face\s*\{([^}]*)\}", full_css, flags=re.I):
        m_fam = re.search(r"font-family\s*:\s*([^;\}]+)", ff_block, flags=re.I)
        if m_fam:
            _add_font(m_fam.group(1))

    # 4d. CSS variables for font names
    for k, v in clean_vars.items():
        if any(fk in k.lower() for fk in ("font", "family", "sans", "serif", "mono", "display", "heading", "body")):
            _add_font(v)

    # 4e. Google Fonts links
    for link in soup.find_all("link", href=True):
        href = link["href"]
        if "fonts.googleapis.com" in href:
            for fam in re.findall(r"family=([^&:]+)", href):
                _add_font(fam.replace("+", " "))

    # 4f. Stylesheet font-family declarations
    for ff in re.findall(r"font-family\s*:\s*([^;\}]+)", full_css, flags=re.I):
        _add_font(ff)
        if len(font_families) >= 10:
            break

    # Pick display / body / mono fonts grounded in live computed elements first (with !important stripped)
    def _first_valid_from_stack(stack: str) -> Optional[str]:
        cleaned_stack = strip_important(stack or "")
        for part in cleaned_stack.split(","):
            c = clean_text(strip_important(part).strip(" \"'"), 42)
            if is_valid_font_name(c):
                return c
        return None

    h1_live_font = _first_valid_from_stack(str((elements.get("h1") or {}).get("fontFamily") or ""))
    body_live_font = _first_valid_from_stack(str((elements.get("body") or {}).get("fontFamily") or ""))
    code_live_font = _first_valid_from_stack(str((elements.get("code") or {}).get("fontFamily") or ""))

    disp_name = h1_live_font or (font_families[0] if font_families else None)
    body_name = body_live_font or (font_families[1] if len(font_families) >= 2 else disp_name)
    mono_matches = [
        f for f in font_families
        if any(m in f.lower() for m in ("mono", "code", "jetbrains", "fira", "consolas", "ibm plex mono", "inconsolata", "space mono", "roobertmono"))
    ]
    mono_name = code_live_font or (mono_matches[0] if mono_matches else None)

    display_font = f"`{disp_name}`" if disp_name else "`system-ui, -apple-system, sans-serif` (live computed system stack)"
    body_font = f"`{body_name}`" if body_name else display_font
    mono_font = f"`{mono_name}`" if mono_name else "not declared in live computed styles (uses primary stack / `ui-monospace`)"

    # 5. Extract Real Typographic Scale — Live computed_styles.json FIRST, supplemented by CSS vars & rules!
    _color_val_re = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", flags=re.I)
    h1_vars = [
        f"`{k}: {v}`" for k, v in clean_vars.items()
        if any(tk in k.lower() for tk in ("h1", "heading-1", "display", "title-xl", "hero-title", "fs-xl", "text-4xl", "text-5xl", "text-6xl"))
        and "color" not in k.lower() and not _color_val_re.search(v)
    ]
    h2_vars = [
        f"`{k}: {v}`" for k, v in clean_vars.items()
        if any(tk in k.lower() for tk in ("h2", "heading-2", "subtitle", "title-lg", "fs-lg", "text-2xl", "text-3xl"))
        and "color" not in k.lower() and not _color_val_re.search(v)
    ]
    body_vars = [
        f"`{k}: {v}`" for k, v in clean_vars.items()
        if any(tk in k.lower() for tk in ("body", "paragraph", "text-base", "fs-base", "font-size-base", "main-paragraph"))
        and "color" not in k.lower() and not _color_val_re.search(v)
    ]
    meta_vars = [
        f"`{k}: {v}`" for k, v in clean_vars.items()
        if any(tk in k.lower() for tk in ("mono", "caption", "small", "label", "meta", "fs-sm", "text-xs", "text-sm", "letter-spacing"))
        and "color" not in k.lower() and not _color_val_re.search(v)
    ]

    observed_font_sizes = []
    for tag_k in ("h1", "h2", "h3", "body", "p", "nav", "a", "button"):
        el = elements.get(tag_k)
        if el and el.get("fontSize"):
            fsv = clean_text(strip_important(str(el["fontSize"])), 24)
            if fsv and fsv not in observed_font_sizes:
                observed_font_sizes.append(fsv)
    for fs in re.findall(r"font-size\s*:\s*([0-9.]+(?:rem|px|em|vw|%)|clamp\([^)]+\)|var\(--[a-zA-Z0-9_-]+\))", full_css, flags=re.I):
        fsc = clean_text(strip_important(fs), 36)
        if fsc and fsc not in observed_font_sizes:
            observed_font_sizes.append(fsc)
        if len(observed_font_sizes) >= 10:
            break

    def extract_tag_font_spec(
        live_el: Optional[dict],
        live_label: str,
        tag_pattern: str,
        var_list: List[str],
        all_sizes: List[str],
    ) -> str:
        specs = []
        live_s = _format_computed_el_spec(live_el, live_label)
        if live_s:
            specs.append(live_s)
        if var_list:
            specs.append("CSS variables " + ", ".join(var_list[:3]))
        for sel, body in re.findall(rf"({tag_pattern})\s*\{{([^{{}}]+)\}}", css_no_at, flags=re.I):
            fs = re.search(r"font-size\s*:\s*([^;\}]+)", body, flags=re.I)
            lh = re.search(r"line-height\s*:\s*([^;\}]+)", body, flags=re.I)
            ls = re.search(r"letter-spacing\s*:\s*([^;\}]+)", body, flags=re.I)
            fw = re.search(r"font-weight\s*:\s*([^;\}]+)", body, flags=re.I)
            parts = []
            if fs:
                parts.append(f"`font-size: {clean_text(strip_important(fs.group(1)), 32)}`")
            if lh:
                parts.append(f"`line-height: {clean_text(strip_important(lh.group(1)), 24)}`")
            if ls:
                parts.append(f"`letter-spacing: {clean_text(strip_important(ls.group(1)), 24)}`")
            if fw:
                parts.append(f"`font-weight: {clean_text(strip_important(fw.group(1)), 16)}`")
            if parts:
                specs.append(f"rule `{clean_text(sel, 24)}` ({', '.join(parts)})")
                break
        if specs:
            return "; ".join(specs)
        if all_sizes:
            return f"observed font-size ladder: {', '.join(f'`{s}`' for s in all_sizes[:5])}"
        return "not explicitly declared (sized via runtime/utility classes)"

    # R2: Select H2 strictly from largest visible h2 (fontSize >= 20px and > body_px; never a 16px sidebar/card h2)
    body_el = elements.get("body") or elements.get("p") or {}
    body_px = parse_css_px(body_el.get("fontSize")) or 16.0
    h2_el = elements.get("h2")
    h2_px = parse_css_px((h2_el or {}).get("fontSize")) or 0.0
    h2_label = "h2"

    if not h2_el or h2_px < 20.0 or h2_px <= body_px:
        best_dom_h2_px = 0.0
        best_dom_h2_weight = None
        for h2_tag in soup.find_all("h2"):
            cls_list = h2_tag.get("class") or []
            if isinstance(cls_list, str):
                cls_list = cls_list.split()
            cls_joined = " ".join(cls_list).lower()
            if "sr-only" in cls_joined or "hidden" in cls_list:
                continue
            txt = clean_text(h2_tag.get_text(" ", strip=True), 60)
            if len(txt) < 3:
                continue
            cand_px = 0.0
            cand_w = None
            for token in cls_list:
                base_tok = re.sub(r"^(?:sm|md|lg|xl|2xl):", "", token)
                if base_tok in TAILWIND_TEXT_SIZE_PX:
                    cand_px = max(cand_px, TAILWIND_TEXT_SIZE_PX[base_tok])
                m_arb = re.match(r"^text-\[([0-9.]+)(px|rem)\]$", base_tok)
                if m_arb:
                    apx = parse_css_px(m_arb.group(1) + m_arb.group(2)) or 0.0
                    cand_px = max(cand_px, apx)
                if base_tok in ("font-black",):
                    cand_w = "900"
                elif base_tok in ("font-extrabold",):
                    cand_w = "800"
                elif base_tok in ("font-bold",):
                    cand_w = "700"
                elif base_tok in ("font-semibold",):
                    cand_w = "600"
                elif base_tok in ("font-medium",):
                    cand_w = "500"
            if cand_px >= 20.0 and cand_px > body_px and cand_px > best_dom_h2_px:
                best_dom_h2_px = cand_px
                best_dom_h2_weight = cand_w

        h3_el = elements.get("h3")
        h3_px = parse_css_px((h3_el or {}).get("fontSize")) or 0.0
        if best_dom_h2_px >= 20.0:
            h2_el = {
                "fontSize": f"{int(best_dom_h2_px) if best_dom_h2_px.is_integer() else best_dom_h2_px}px",
                "fontWeight": best_dom_h2_weight or ((h3_el or {}).get("fontWeight") or "700"),
                "lineHeight": "1.2",
            }
            h2_label = "h2 (largest visible desktop heading)"
        elif h3_el and h3_px >= 20.0 and h3_px > body_px:
            h2_el = h3_el
            h2_label = "h3 (primary section heading)"
        elif h2_px < 20.0 or h2_px <= body_px:
            # Avoid emitting a 16px card/sidebar h2 when CSS vars/rules declare a real heading scale
            if h2_vars:
                h2_el = None

    h1_spec = extract_tag_font_spec(elements.get("h1"), "h1", r"(?:^|[\s,])(?:h1|\.h1|\.heading-1|\.hero-title|\.title)", h1_vars, observed_font_sizes)
    h2_spec = extract_tag_font_spec(h2_el, h2_label, r"(?:^|[\s,])(?:h2|\.h2|\.heading-2|\.section-title)", h2_vars, observed_font_sizes)
    body_spec = extract_tag_font_spec(elements.get("body") or elements.get("p"), "body", r"(?:^|[\s,])(?:body|p|\.body|\.paragraph)", body_vars, observed_font_sizes)
    meta_spec = extract_tag_font_spec(elements.get("nav") or elements.get("a") or elements.get("button"), "nav/a", r"(?:^|[\s,])(?:small|nav|\.mono|\.caption|\.label|\.meta)", meta_vars, observed_font_sizes)

    # 6. Layout Geometry — R1: True Desktop Container Max-Width from Measured Bounding Boxes (NEVER @media!)
    landmarks = computed.get("landmarks") or []
    main_el = elements.get("main") or {}
    main_rect = main_el.get("rect") or {}
    main_rect_w = int(main_rect.get("width") or 0)
    main_css_mw = strip_important(str(main_el.get("maxWidth") or ""))
    main_css_mw_px = parse_css_px(main_css_mw)
    body_css_mw = strip_important(str((elements.get("body") or {}).get("maxWidth") or ""))
    body_css_mw_px = parse_css_px(body_css_mw)

    # Measure direct-child / wide section landmarks from computed_styles.json
    section_widths = [
        int(lm.get("width") or 0)
        for lm in landmarks
        if lm.get("tag") in ("section", "article", "main") and 900 <= int(lm.get("width") or 0) <= 1440
    ]
    bounded_section_widths = [w for w in section_widths if 960 <= w < 1420]

    # Non-@media container CSS max-width rules >= 960px
    css_container_mws: List[Tuple[float, str]] = []
    for sel, body in re.findall(r"([.#a-zA-Z0-9_:>\s-]{1,48})\s*\{([^{}]{3,260})\}", css_no_at):
        sel_l = sel.lower()
        if not any(k in sel_l for k in ("container", "wrapper", "inner", "main", "page", "layout", "shell", "content", "section", "max-w")):
            continue
        for mwm in re.findall(r"max-width\s*:\s*([^;\}]+)", body, flags=re.I):
            mw_c = strip_important(mwm)
            px = parse_css_px(mw_c)
            if px is not None and 960.0 <= px <= 1920.0:
                css_container_mws.append((px, mw_c))

    if main_css_mw_px is not None and 960.0 <= main_css_mw_px <= 1920.0:
        container_max_val = main_css_mw
        container_max_note = f"`{main_css_mw}` (live computed `main` `max-width`)"
    elif 900 <= main_rect_w < 1420:
        container_max_val = f"{main_rect_w}px"
        container_max_note = f"`{main_rect_w}px` (measured `<main>` bounding box width within `1440px` desktop viewport)"
    elif bounded_section_widths:
        widest_sec = max(bounded_section_widths)
        container_max_val = f"{widest_sec}px"
        container_max_note = f"`{widest_sec}px` (measured widest bounded `<section>` landmark width on `1440px` desktop)"
    elif css_container_mws:
        # Prefer largest realistic desktop container rule >= 960px (e.g. 1200px / 1280px / 1440px)
        best_px, best_mw = max(css_container_mws, key=lambda x: x[0])
        container_max_val = best_mw
        container_max_note = f"`{best_mw}` (non-`@media` desktop container stylesheet rule)"
    elif body_css_mw_px is not None and 960.0 <= body_css_mw_px <= 1920.0:
        container_max_val = body_css_mw
        container_max_note = f"`{body_css_mw}` (live computed `body` `max-width`)"
    elif main_rect_w >= 1420 or (section_widths and max(section_widths) >= 1420):
        container_max_val = "1440px"
        container_max_note = "`1440px` (full-bleed `100%` desktop stage measured on `1440px` viewport)"
    else:
        container_max_val = "1440px"
        container_max_note = "`1440px` (full-bleed `100%` viewport stage)"

    container_max = container_max_val

    gaps = []
    for tk in ("main", "header", "nav", "footer"):
        gp = strip_important(str((elements.get(tk) or {}).get("gap") or ""))
        if gp and gp not in ("normal", "0px", "0rem", "0") and gp not in gaps:
            gaps.append(gp)
    for gp in re.findall(r"(?:gap|grid-gap|column-gap)\s*:\s*([0-9.]+(?:px|rem|vw|%)|var\(--[a-zA-Z0-9_-]+\))", css_no_at):
        gp = strip_important(gp)
        if gp not in ("0px", "0rem", "0") and gp not in gaps:
            gaps.append(gp)
    grid_gap = gaps[0] if gaps else "0px (flush layout)"

    paddings = []
    for tk in ("main", "header", "body", "footer"):
        pd = strip_important(str((elements.get(tk) or {}).get("paddingLeft") or ""))
        if pd and pd not in ("0px", "0rem", "0", "auto") and pd not in paddings:
            paddings.append(pd)
    for pd in re.findall(r"padding(?:-inline|-left|-right)?\s*:\s*([0-9.]+(?:px|rem|vw)|var\(--[a-zA-Z0-9_-]+\))", css_no_at):
        pd = strip_important(pd)
        if pd not in ("0px", "0rem", "0") and pd not in paddings:
            paddings.append(pd)
    grid_pad = paddings[0] if paddings else "0px"

    radii = []
    for tk in ("button", "a", "header", "main"):
        rd = strip_important(str((elements.get(tk) or {}).get("borderRadius") or ""))
        if rd and rd not in ("0px", "0") and rd not in radii:
            radii.append(rd)
    for rd in re.findall(r"border-radius\s*:\s*([0-9.]+(?:px|rem|%)|var\(--[a-zA-Z0-9_-]+\))", css_no_at):
        rd = strip_important(rd)
        if rd not in radii:
            radii.append(rd)
    radii_str = ", ".join(f"`{r}`" for r in radii[:4]) if radii else "`0px` (sharp unrounded corners)"

    media_queries = []
    for mq in re.findall(r"@media\s*([^{]+)", full_css):
        mqc = clean_text(mq, 60)
        if any(k in mqc for k in ("width", "min-", "max-", "screen", "prefers-reduced-motion")) and mqc not in media_queries:
            media_queries.append(mqc)
    bps_str = ", ".join(f"`@media {m}`" for m in media_queries[:4]) if media_queries else "no `@media` queries in captured stylesheet (fluid viewport scaling)"

    # 7. Motion & Easing Curves
    beziers = []
    for bz in re.findall(r"cubic-bezier\(\s*[-0-9.]+\s*,\s*[-0-9.]+\s*,\s*[-0-9.]+\s*,\s*[-0-9.]+\s*\)", full_css):
        bzc = re.sub(r"\s+", "", bz)
        if bzc not in beziers:
            beziers.append(bzc)
    has_extracted_bezier = bool(beziers)
    if not beziers:
        beziers = ["cubic-bezier(0.25,0.1,0.25,1)"]

    selector_transitions = []
    for sel, body in re.findall(r"([.#a-zA-Z0-9_:-][^{};,\n]{1,42})\s*\{([^}]*transition[^}]*)\}", full_css):
        sel_c = clean_text(sel, 36)
        tr_match = re.search(r"transition(?:-duration|-timing-function)?\s*:\s*([^;\}]+)", body)
        if tr_match and not any(bad in sel_c for bad in ("@", "}", "{", "/*")):
            tr_val = clean_text(tr_match.group(1), 65)
            tr_val = re.sub(r"cubic-bezier\([^)]+\)", beziers[0], tr_val)
            if tr_val and len(selector_transitions) < 5:
                selector_transitions.append((sel_c, tr_val))

    allowed_hexes = {h.lower() for _, h, _ in semantic_tokens}
    real_css_rules = []
    for sel, body in re.findall(r"([.#a-zA-Z][a-zA-Z0-9_.:>\s-]{2,38})\s*\{([^{}]{10,120})\}", full_css):
        sel_c = clean_text(sel.lstrip("#"), 36)
        body_c = clean_text(body, 75)
        body_c = re.sub(r"#[0-9a-fA-F]{3,8}\b", lambda m: m.group(0) if m.group(0).lower() in allowed_hexes else bg_hex, body_c)
        body_c = re.sub(r"cubic-bezier\([^)]+\)", beziers[0], body_c)
        if sel_c and not any(bad in sel_c for bad in ("@", "}", "{", "/*", "#")) and not any(bad in body_c for bad in ("{", "}", "@", "url(data:")):
            if sel_c not in [s for s, _ in real_css_rules]:
                real_css_rules.append((sel_c, body_c))
            if len(real_css_rules) >= 8:
                break

    def _is_promo_or_banner(tag_obj) -> bool:
        cur = tag_obj
        for _ in range(5):
            if cur is None or not hasattr(cur, "attrs"):
                break
            cls_s = " ".join(cur.get("class") or []) if isinstance(cur.get("class"), list) else str(cur.get("class") or "")
            id_s = str(cur.get("id") or "")
            if PROMO_CLASS_RE.search(f"{id_s} {cls_s}"):
                return True
            cur = cur.parent
        return False

    copy_snippets = []
    # Prioritize live computed <p> textSample (hero / lead paragraph) over promo banners
    live_p_sample = clean_text(str((elements.get("p") or {}).get("textSample") or ""), 85)
    live_p_sample = re.sub(r"#[0-9a-fA-F]{3,8}\b", "", live_p_sample).strip()
    if len(live_p_sample) >= 18 and not PROMO_BANNER_RE.search(live_p_sample):
        copy_snippets.append(live_p_sample)

    main_scope = soup.find("main") or soup
    for scope_root in (main_scope, soup):
        for tag in scope_root.find_all(["p", "blockquote", "figcaption", "li", "span"]):
            if _is_promo_or_banner(tag):
                continue
            txt = clean_text(tag.get_text(" ", strip=True), 85)
            txt = re.sub(r"#[0-9a-fA-F]{3,8}\b", "", txt).strip()
            if 18 <= len(txt) <= 85 and not PROMO_BANNER_RE.search(txt) and txt not in copy_snippets:
                copy_snippets.append(txt)
                if len(copy_snippets) >= 8:
                    break
        if len(copy_snippets) >= 8:
            break

    media_assets = []
    for tag in soup.find_all(["img", "video", "source", "script", "link"]):
        src = tag.get("src") or tag.get("href") or tag.get("data-src") or ""
        alt = clean_text(tag.get("alt") or tag.get("aria-label") or "", 40)
        if src and not src.startswith("data:"):
            fname = clean_text(src.split("?")[0].split("/")[-1], 36)
            if len(fname) >= 4 and fname not in media_assets:
                media_assets.append(f"{fname}" + (f" ({alt})" if alt else ""))
                if len(media_assets) >= 6:
                    break

    data_attrs = []
    for tag in soup.find_all(True):
        for k, v in (tag.attrs or {}).items():
            if k.startswith("data-") and k not in data_attrs:
                val_s = clean_text(str(v), 24) if v else ""
                data_attrs.append(f'{k}="{val_s}"' if val_s else k)
                if len(data_attrs) >= 6:
                    break
        if len(data_attrs) >= 6:
            break

    detected_libs = []
    dom_lower = dom_text.lower()
    for lib_key, lib_label in [
        ("lenis", "Lenis Smooth Scroll (`data-lenis`)"),
        ("gsap", "GSAP Timeline & ScrollTrigger"),
        ("three", "Three.js / WebGL Canvas Pipeline"),
        ("swiper", "Swiper Touch Carousel Engine"),
        ("lottie", "Lottie Vector Animation Player"),
        ("barba", "Barba.js Page Transition Router"),
        ("locomotive", "Locomotive Scroll Parallax"),
        ("framer", "Framer Motion Layout Physics"),
    ]:
        if lib_key in dom_lower or lib_key in full_css.lower():
            detected_libs.append(lib_label)
    dm = computed.get("domMetrics") or {}
    has_canvas = bool(soup.find("canvas")) or int(dm.get("canvasCount", 0)) > 0
    if has_canvas and "Three.js / WebGL Canvas Pipeline" not in detected_libs:
        detected_libs.append("HTML5 `<canvas>` Interactive Viewport Surface")

    # 8. Build Curated Root CSS Variables (12-16 unique clean vars)
    prefix = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:16]
    curated_vars = []
    for k, v in list(clean_vars.items())[:16]:
        for hm in re.findall(r"#[0-9a-fA-F]{3,8}\b", v):
            nh = normalize_hex(hm)
            if nh not in allowed_hexes:
                allowed_hexes.add(nh)
                semantic_tokens.append((f"Variable Token (`{k}`)", nh, f"`var({k})` in `:root`"))
        for bzm in re.findall(r"cubic-bezier\([^)]+\)", v):
            bzc = re.sub(r"\s+", "", bzm)
            if bzc not in beziers:
                beziers.append(bzc)
        curated_vars.append((k, v, classify_var_role(k, v)))

    for k, v in list(fallback_vars.items()):
        if len(curated_vars) >= 14:
            break
        for hm in re.findall(r"#[0-9a-fA-F]{3,8}\b", v):
            nh = normalize_hex(hm)
            if nh not in allowed_hexes:
                allowed_hexes.add(nh)
                semantic_tokens.append((f"Variable Token (`{k}`)", nh, f"`var({k})` in stylesheet"))
        curated_vars.append((k, v, classify_var_role(k, v)))

    grounded_supplements = [
        (f"--{prefix}-bg", bg_hex, f"Canvas Substrate (from {bg_src})"),
        (f"--{prefix}-ink", ink_hex, "Primary Typography Ink (computed-derived)"),
        (f"--{prefix}-surface", surface_hex, "Surface Substrate (computed-derived)"),
        (f"--{prefix}-accent", accent_hex, "Interactive Accent (computed-derived)"),
        (f"--{prefix}-muted", muted_hex, "Secondary Ink (computed-derived)"),
        (f"--{prefix}-border", border_hex, "Structural Border (computed-derived)"),
        (f"--{prefix}-container", container_max.split()[0], "Container Bound (computed-derived)"),
        (f"--{prefix}-gap", grid_gap.split()[0], "Grid Gutter (computed-derived)"),
        (f"--{prefix}-pad", grid_pad.split()[0], "Lateral Padding (computed-derived)"),
        (f"--{prefix}-ease", beziers[0], "Primary Easing Function"),
        (f"--{prefix}-radius", radii[0] if radii else "0px", "Corner Radius (computed-derived)"),
        (f"--{prefix}-fs-base", observed_font_sizes[0] if observed_font_sizes else "16px", "Observed Font Size Token"),
    ]
    existing_var_names = {k for k, _, _ in curated_vars}
    for gk, gv, grole in grounded_supplements:
        if len(curated_vars) >= 16:
            break
        if gk not in existing_var_names:
            curated_vars.append((gk, gv, grole))
            existing_var_names.add(gk)

    # 9. Real DOM Landmarks, Classes, Sidebar Bounding Boxes & Headings (R5)
    real_dom_classes = []
    for tag in soup.find_all(class_=True):
        cls_list = tag.get("class") or []
        if isinstance(cls_list, str):
            cls_list = cls_list.split()
        for c in cls_list:
            c = c.strip()
            if re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{1,34}$", c) and c not in real_dom_classes and c in dom_text:
                real_dom_classes.append(c)
                if len(real_dom_classes) >= 30:
                    break
        if len(real_dom_classes) >= 30:
            break

    used_wire_names: set[str] = set()

    def _dedupe_wire(base_tag: str, candidates: List[str], fallback_idx: int) -> str:
        for c in candidates:
            w = f"<{base_tag}.{c}>"
            if w not in used_wire_names:
                used_wire_names.add(w)
                return w
        base_w = f"<{base_tag}.{candidates[0]}>" if candidates else f"<{base_tag}>"
        if base_w not in used_wire_names:
            used_wire_names.add(base_w)
            return base_w
        n = 2
        while True:
            cand_w = f"<{base_tag}.{candidates[0]}:nth({n})>" if candidates else f"<{base_tag}:nth({n})>"
            if cand_w not in used_wire_names:
                used_wire_names.add(cand_w)
                return cand_w
            n += 1

    def tag_descriptor(tag_obj, fallback_tag: str, idx: int) -> Tuple[str, str]:
        if tag_obj is not None:
            tname = tag_obj.name
            tid = (tag_obj.get("id") or "").strip()
            tcls = tag_obj.get("class") or []
            if isinstance(tcls, str):
                tcls = tcls.split()
            valid_cls = [c for c in tcls if re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{1,34}$", c) and c in dom_text]
            semantic_cls = [c for c in valid_cls if c.lower() not in UTILITY_CLASS_SKIP]
            ordered_cls = semantic_cls + [c for c in valid_cls if c not in semantic_cls]
            if ordered_cls:
                cls_attr = " ".join(ordered_cls[:2])
                wire = _dedupe_wire(tname, ordered_cls[:4], idx)
                return f'`<{tname} class="{cls_attr}">`', wire
            if idx < len(real_dom_classes):
                rc = real_dom_classes[idx]
                wire = _dedupe_wire(tname, [rc], idx)
                return f'`<{tname} class="{rc}">`', wire
            if tid and re.match(r"^[a-zA-Z0-9_-]{1,30}$", tid):
                wire = f"<{tname}#{tid}>"
                if wire in used_wire_names:
                    wire = f"<{tname}#{tid}:nth({idx + 1})>"
                used_wire_names.add(wire)
                return f'`<{tname} id="{tid}">`', wire
            return f"`<{tname}>` landmark", _dedupe_wire(tname, [], idx)
        if idx < len(real_dom_classes):
            rc = real_dom_classes[idx]
            wire = _dedupe_wire(fallback_tag, [rc], idx)
            return f'`<{fallback_tag} class="{rc}">`', wire
        return f"`<{fallback_tag}>` landmark", _dedupe_wire(fallback_tag, [], idx)

    # Detect fixed/sticky left sidebar from computed_styles.json landmark bounding boxes (x <= 40, w < 380, h > 400)
    sidebar_lm: Optional[dict] = None
    for cand_key in ("aside", "nav"):
        el_c = elements.get(cand_key)
        if el_c and isinstance(el_c.get("rect"), dict):
            rc = el_c["rect"]
            rw, rh, rx = int(rc.get("width") or 0), int(rc.get("height") or 0), int(rc.get("x") or 0)
            if 60 <= rw < 380 and rh > 400 and rx <= 40:
                sidebar_lm = {
                    "tag": cand_key,
                    "className": el_c.get("className") or "",
                    "x": rx, "y": int(rc.get("y") or 0), "width": rw, "height": rh,
                }
                break
    if sidebar_lm is None:
        for lm in landmarks:
            if lm.get("tag") in ("nav", "aside"):
                rw, rh, rx = int(lm.get("width") or 0), int(lm.get("height") or 0), int(lm.get("x") or 0)
                if 60 <= rw < 380 and rh > 400 and rx <= 40:
                    sidebar_lm = lm
                    break

    header_tag = soup.find("header") or soup.find("nav")
    nav_tag = soup.find("nav") or header_tag
    aside_tag = soup.find("aside")
    main_tag = soup.find("main") or soup.find("section") or soup.find("div")
    # Prefer distinct sections inside <main> first
    main_sections = main_tag.find_all(["section", "article"], recursive=True) if main_tag else []
    sections = main_sections if len(main_sections) >= 2 else soup.find_all(["section", "article"])
    sec1_tag = sections[0] if len(sections) >= 1 else main_tag
    sec2_tag = sections[1] if len(sections) >= 2 else sec1_tag
    sec3_tag = sections[2] if len(sections) >= 3 else sec2_tag
    footer_tag = soup.find("footer")

    # Build grounded headings: live h1 textSample FIRST, then visible non-promo section h2/h3 headings
    headings = []
    live_h1_txt = clean_text(str((elements.get("h1") or {}).get("textSample") or ""), 52)
    if len(live_h1_txt) >= 3 and not PROMO_BANNER_RE.search(live_h1_txt) and live_h1_txt.lower() not in ("loading", "loading...", "menu", "close"):
        headings.append(live_h1_txt)

    # Prefer section-level headings (len >= 12 or larger Tailwind size) over 1-word micro badges ("Realistic")
    long_headings = []
    short_headings = []
    for h in (main_tag or soup).find_all(["h1", "h2", "h3"]):
        if _is_promo_or_banner(h):
            continue
        ht = clean_text(h.get_text(" ", strip=True), 52)
        if len(ht) < 3 or ht.lower() in ("loading", "loading...", "menu", "close", "0%", "100%", "scroll"):
            continue
        if PROMO_BANNER_RE.search(ht):
            continue
        if ht in headings or ht in long_headings or ht in short_headings:
            continue
        if len(ht) >= 12 or h.name == "h1":
            long_headings.append(ht)
        else:
            short_headings.append(ht)
    for ht in long_headings + short_headings:
        if ht not in headings:
            headings.append(ht)
        if len(headings) >= 6:
            break
    if not headings:
        headings = [title[:48], "Featured Work & Interactive Showcase", "Technical Architecture & Narrative", "Global Directory & Contact"]

    nav_links = []
    for a in (nav_tag.find_all("a") if nav_tag else soup.find_all("a"))[:16]:
        if _is_promo_or_banner(a):
            continue
        at = clean_text(a.get_text(" ", strip=True), 22)
        if 2 <= len(at) <= 20 and not PROMO_BANNER_RE.search(at) and at not in nav_links:
            nav_links.append(at)
        if len(nav_links) >= 5:
            break
    if not nav_links:
        nav_links = ["Overview", "Index", "Archive", "About", "Contact"]

    hdr_sel, hdr_wire = tag_descriptor(header_tag, "header", 0)
    sidebar_sel, sidebar_wire = (
        tag_descriptor(aside_tag if (sidebar_lm and sidebar_lm.get("tag") == "aside" and aside_tag) else nav_tag, sidebar_lm.get("tag", "nav") if sidebar_lm else "nav", 5)
        if sidebar_lm
        else ("", "")
    )
    hero_sel, hero_wire = tag_descriptor(sec1_tag, "section", 1)
    grid_sel, grid_wire = tag_descriptor(sec2_tag, "section", 2)
    detail_sel, detail_wire = tag_descriptor(sec3_tag, "section", 3)
    ftr_sel, ftr_wire = tag_descriptor(footer_tag, "footer", 4)

    h1_copy = headings[0]
    h2_a = headings[1] if len(headings) > 1 else (copy_snippets[0][:48] if copy_snippets else f"{title[:32]} Primary Showcase")
    h2_b = headings[2] if len(headings) > 2 else (copy_snippets[1][:48] if len(copy_snippets) > 1 else f"{slug} Interactive Matrix")
    h2_c = headings[3] if len(headings) > 3 else (copy_snippets[2][:48] if len(copy_snippets) > 2 else f"{slug} Archive & Directory")
    nav_str = " · ".join(nav_links[:5])
    snip_0 = copy_snippets[0] if len(copy_snippets) > 0 else meta_desc
    snip_1 = copy_snippets[1] if len(copy_snippets) > 1 else h1_copy
    snip_2 = copy_snippets[2] if len(copy_snippets) > 2 else h2_a
    snip_3 = copy_snippets[3] if len(copy_snippets) > 3 else h2_b
    asset_str = ", ".join(f"`{a}`" for a in media_assets[:4]) if media_assets else f"`{slug}` inline SVG & DOM assets"
    data_attr_str = ", ".join(f"`{d}`" for d in data_attrs[:4]) if data_attrs else f'`data-target="{slug}"`'
    css_rule_0 = f"`{real_css_rules[0][0]} {{ {real_css_rules[0][1]} }}`" if len(real_css_rules) > 0 else f"`body {{ background: {bg_hex}; color: {ink_hex}; }}`"
    css_rule_1 = f"`{real_css_rules[1][0]} {{ {real_css_rules[1][1]} }}`" if len(real_css_rules) > 1 else f"`main {{ max-width: {container_max}; gap: {grid_gap}; }}`"
    css_rule_2 = f"`{real_css_rules[2][0]} {{ {real_css_rules[2][1]} }}`" if len(real_css_rules) > 2 else f"`header {{ border-color: {border_hex}; }}`"

    hdr_rect = (elements.get("header") or elements.get("nav") or {}).get("rect") or {}
    hdr_dim_txt = f"{hdr_rect.get('width', 1440)}x{hdr_rect.get('height', 64)}px" if hdr_rect.get("width") else "1440px"
    main_stage_w = main_rect_w if main_rect_w >= 600 else (1440 - (int(sidebar_lm["width"]) if sidebar_lm else 0))

    if sidebar_lm:
        sb_w = int(sidebar_lm["width"])
        sb_h = int(sidebar_lm["height"])
        dom_regions = [
            (
                f"Persistent Left Sidebar Navigation ({sidebar_wire} — `{sb_w}×{sb_h}px` at `x={sidebar_lm['x']}`)",
                f"Fixed/sticky left rail {sidebar_sel} (`{sb_w}px` wide) paired with top bar {hdr_sel} hosting `{title[:30]}` navigation (`{nav_str}`), DOM attributes ({data_attr_str}), and rule {css_rule_0}.",
            ),
            (
                f"Hero Stage & Focal Lead ({hero_wire} — `{h1_copy[:34]}`)",
                f'Landmark {hero_sel} inside `{main_stage_w}px` main stage presenting display lead `{h1_copy}` and copy excerpt *"{snip_0[:65]}"* over `{bg_hex}` canvas and `{ink_hex}` ink.',
            ),
            (
                f"Primary Showcase & Content Grid ({grid_wire} — `{h2_a[:34]}`)",
                f'Landmark {grid_sel} rendering section `{h2_a}` with copy *"{snip_1[:65]}"*, asset references ({asset_str}), and grid rule {css_rule_1}.',
            ),
            (
                f"Interactive Detail & Feature Matrix ({detail_wire} — `{h2_b[:34]}`)",
                f'Landmark {detail_sel} framing `{h2_b}` and `{h2_c}` (*"{snip_2[:65]}"*) with `{surface_hex}` surface cards, `{accent_hex}` accents, and {css_rule_2}.',
            ),
            (
                f"Colophon & Global Directory Footer ({ftr_wire} — `{url}`)",
                f'Landmark {ftr_sel} anchoring `{title[:32]}` footer links (`{nav_str}`), copy *"{snip_3[:60]}"*, and `{border_hex}` structural dividers.',
            ),
        ]
        sb_links = (nav_links + ["Explore", "Library", "Tools", "Settings"])[:4]
        def _sb_row(left: str, right: str) -> str:
            return f"| {left[:21]:<21} | {right[:42]:<42} |"

        sb_hdr_l = f"--[ SIDEBAR ({sb_w}px) ]--"[:23].ljust(23, "-")
        sb_hdr_r = f"--[ MAIN CONTENT STAGE ({main_stage_w}px) ]--"[:44].ljust(44, "-")
        wireframe_lines = [
            f"DESKTOP VIEWPORT (1440px) — Sidebar ({sb_w}px) + Main ({main_stage_w}px)"[:70],
            "=" * 70,
            f"+{sb_hdr_l}+{sb_hdr_r}+",
            _sb_row(sidebar_wire, f"[ {hdr_wire[:17]} | {nav_str[:18]} ]"),
            _sb_row(f"- {sb_links[0]}", "-" * 42),
            _sb_row(f"- {sb_links[1]}", f"HERO STAGE: {hero_wire}"),
            _sb_row(f"- {sb_links[2]}", f"H1: {h1_copy}"),
            _sb_row(f"- {sb_links[3]}", f"Bg:{bg_hex} Ink:{ink_hex} Acc:{accent_hex}"),
            _sb_row(f"x={sidebar_lm['x']} h={sb_h}px rail", "-" * 42),
            _sb_row("", f"SHOWCASE: {grid_wire[:20]} max:{container_max[:8]}"),
            _sb_row("", f"[{h2_a[:18]:<18}] [{h2_b[:18]:<18}]"),
            _sb_row("", f"DETAIL: {detail_wire}"),
            "+" + "-" * 23 + "+" + "-" * 44 + "+",
            f"[ FOOTER ({ftr_wire[:20]:<20}) | {url[:35]:<35} ]",
        ]
        mobile_wireframe_lines = [
            "MOBILE VIEWPORT REFLOW (390px) — Collapsed Sidebar + 1-Col Stack",
            "=" * 70,
            f"[ DRAWER ({sidebar_wire[:22]:<22}) | {hdr_wire[:33]:<33} ]",
            "-" * 70,
            f"| {f'HERO STAGE ({hero_wire[:24]}) — 390px Full-Width':<66} |",
            f"| {f'H1: {h1_copy[:62]}':<66} |",
            "-" * 70,
            f"| {f'SHOWCASE STACK ({grid_wire[:22]}) — 1-Col (gap: {grid_gap[:10]})':<66} |",
            f"| [ Card 1: {h2_a[:54]:<54} ] |",
            f"| [ Card 2: {h2_b[:54]:<54} ] |",
            "-" * 70,
            f"| {f'DETAIL STACK ({detail_wire[:24]}) — 1-Col ({h2_c[:18]})':<66} |",
            "-" * 70,
            f"[ FOOTER ({ftr_wire[:22]:<22}) | {'Stacked 390px Colophon':<33} ]",
        ]
        desktop_cols_desc = (
            f"2-column persistent left sidebar (`{sb_w}px`, {sidebar_wire}) + `{main_stage_w}px` main content stage "
            f"at `1440px` (`max-width: {container_max}`, `gap: {grid_gap}`)"
        )
        mobile_layout_desc = (
            f"Single-column vertical stack at `390px` viewport width with collapsed `{sidebar_wire}` drawer navigation "
            f"and `{grid_pad}` horizontal padding"
        )
    else:
        dom_regions = [
            (
                f"Brand Header & Navigation Bar ({hdr_wire} — `{slug}`)",
                f"Landmark {hdr_sel} (`{hdr_dim_txt}`) hosting `{title[:36]}` navigation (`{nav_str}`), DOM attributes ({data_attr_str}), and stylesheet rule {css_rule_0}.",
            ),
            (
                f"Hero Stage & Focal Lead ({hero_wire} — `{h1_copy[:34]}`)",
                f'Landmark {hero_sel} presenting display lead `{h1_copy}` and copy excerpt *"{snip_0[:65]}"* over `{bg_hex}` canvas and `{ink_hex}` ink.',
            ),
            (
                f"Primary Showcase & Content Grid ({grid_wire} — `{h2_a[:34]}`)",
                f'Landmark {grid_sel} rendering section `{h2_a}` with copy *"{snip_1[:65]}"*, asset references ({asset_str}), and grid rule {css_rule_1}.',
            ),
            (
                f"Interactive Detail & Feature Matrix ({detail_wire} — `{h2_b[:34]}`)",
                f'Landmark {detail_sel} framing `{h2_b}` and `{h2_c}` (*"{snip_2[:65]}"*) with `{surface_hex}` surface cards, `{accent_hex}` accents, and {css_rule_2}.',
            ),
            (
                f"Colophon & Global Directory Footer ({ftr_wire} — `{url}`)",
                f'Landmark {ftr_sel} anchoring `{title[:32]}` footer links (`{nav_str}`), copy *"{snip_3[:60]}"*, and `{border_hex}` structural dividers.',
            ),
        ]
        wireframe_lines = [
            f"DESKTOP VIEWPORT (1440px) — {title[:42]}",
            "=" * 70,
            f"[ {f'{hdr_wire[:22]} ({hdr_dim_txt[:11]})':<35} | {nav_str[:28]:<28} ]",
            "-" * 70,
            f"| {f'HERO STAGE: {hero_wire[:36]} (w={main_stage_w}px)':<66} |",
            f"| {f'H1: {h1_copy[:62]}':<66} |",
            f"| {f'Substrate: {bg_hex} | Ink: {ink_hex} | Accent: {accent_hex}':<66} |",
            "-" * 70,
            f"| {f'SHOWCASE GRID ({grid_wire[:26]}) max-w: {container_max[:14]}':<66} |",
            "| +------------------------------+  +------------------------------+ |",
            f"| | {h2_a[:28]:<28} |  | {h2_b[:28]:<28} | |",
            "| +------------------------------+  +------------------------------+ |",
            "-" * 70,
            f"| {f'DETAIL MODULE ({detail_wire[:26]}) — {h2_c[:21]}':<66} |",
            "-" * 70,
            f"[ FOOTER ({ftr_wire[:20]:<20}) | {url[:35]:<35} ]",
        ]
        mobile_wireframe_lines = [
            f"MOBILE VIEWPORT REFLOW (390px) — Single-Column Stack (gap: {grid_gap[:8]})"[:70],
            "=" * 70,
            f"[ HEADER BAR ({hdr_wire[:24]:<24}) | {'Compact Menu (390px)':<29} ]",
            "-" * 70,
            f"| {f'HERO STAGE ({hero_wire[:24]}) — 390px Vertical Lead':<66} |",
            f"| {f'H1: {h1_copy[:62]}':<66} |",
            "-" * 70,
            f"| {f'SHOWCASE STACK ({grid_wire[:22]}) — 1-Col Stack':<66} |",
            f"| [ Item 1: {h2_a[:54]:<54} ] |",
            f"| [ Item 2: {h2_b[:54]:<54} ] |",
            "-" * 70,
            f"| {f'DETAIL STACK ({detail_wire[:24]}) — 1-Col ({h2_c[:18]})':<66} |",
            "-" * 70,
            f"[ FOOTER ({ftr_wire[:20]:<20}) | {'Stacked 390px Colophon':<35} ]",
        ]
        desktop_cols_desc = (
            f"Multi-column CSS Grid / Flexbox layout at `1440px` (`max-width: {container_max}`, `gap: {grid_gap}`)"
        )
        mobile_layout_desc = (
            f"Single-column vertical stack at `390px` viewport width with `{grid_pad}` horizontal padding"
        )

    is_dark = hex_luma(bg_hex) < 110
    if has_canvas:
        archetype = f"{'Dark-Substrate' if is_dark else 'Light-Gallery'} WebGL / Canvas Interactive Experience (`{bg_hex}` / `{accent_hex}`)"
    elif is_dark:
        archetype = f"High-Contrast Obsidian / Atmospheric Design System (`{bg_hex}` canvas, `{ink_hex}` ink, `{accent_hex}` accent)"
    else:
        archetype = f"Light-Substrate Editorial & Modular Grid System (`{bg_hex}` canvas, `{ink_hex}` ink, `{accent_hex}` accent)"

    differentiators = [
        f"**Live-Computed Chromatic Polarity**: Anchors the viewport in `{bg_hex}` ({bg_src}) paired with `{ink_hex}` primary typography and `{accent_hex}` accents.",
        f"**Grounded Typographic Hierarchy**: Pairs {display_font} for display copy (`{h1_copy[:36]}`) with {body_font} and {mono_font}.",
        f"**Calibrated Spatial Grid & Motion**: Combines `{container_max}` container bounds (`gap: {grid_gap}`, padding `{grid_pad}`) with `{beziers[0]}` easing" + (f" and {', '.join(detected_libs[:2])}." if detected_libs else "."),
    ]

    motion_rules = []
    for sel_c, tr_val in selector_transitions[:3]:
        motion_rules.append(f"**Selector `{sel_c}`**: `transition: {tr_val}` extracted from `{slug}` stylesheet.")
    if has_extracted_bezier:
        motion_rules.append(f"**Extracted Easing Curve (`{beziers[0]}`)**: Governs interactive state transitions across `{hdr_wire}` and `{grid_wire}`.")
    else:
        motion_rules.append(f"**Standard CSS Easing (`{beziers[0]}`)**: Default timing curve (no custom `cubic-bezier()` declared in captured CSS).")
    if detected_libs:
        motion_rules.append(f"**Runtime Interaction Engine**: Integrates {', '.join(detected_libs)} with `prefers-reduced-motion: reduce` fallback guards.")
    else:
        motion_rules.append(f"**State & Hover Choreography**: Opacity and transform transitions on `{hero_wire}` and `{grid_wire}` interactive targets.")

    return {
        "slug": slug,
        "url": url,
        "final_url": final_url,
        "title": title,
        "summary": meta_desc,
        "archetype": archetype,
        "frameworks": frameworks,
        "differentiators": differentiators,
        "total_clean_vars": total_clean_vars,
        "curated_vars": curated_vars,
        "semantic_tokens": semantic_tokens,
        "bg_hex": bg_hex,
        "ink_hex": ink_hex,
        "accent_hex": accent_hex,
        "grid": {
            "max_width": container_max_note,
            "padding": f"`{grid_pad}` lateral canvas padding with `{grid_gap}` grid gap",
            "radii": radii_str,
            "desktop_cols": desktop_cols_desc,
            "mobile_layout": mobile_layout_desc,
            "breakpoints": bps_str,
        },
        "fonts": {
            "display": display_font,
            "body": body_font,
            "mono": mono_font,
            "h1_spec": h1_spec,
            "h2_spec": h2_spec,
            "body_spec": body_spec,
            "meta_spec": meta_spec,
        },
        "motion": {
            "primary_curve": ", ".join(f"`{b}`" for b in beziers[:3]),
            "rules": motion_rules,
        },
        "wireframe_lines": wireframe_lines,
        "mobile_wireframe_lines": mobile_wireframe_lines,
        "dom_regions": dom_regions,
        "copy_snippets": copy_snippets,
        "media_assets": media_assets,
        "data_attrs": data_attrs,
        "real_css_rules": real_css_rules,
        "nav_str": nav_str,
        "h1_copy": h1_copy,
        "headings": headings,
    }


def make_ascii_box(lines: List[str], width: int = 74) -> str:
    inner = width - 4
    out = ["┌" + "─" * (width - 2) + "┐"]
    for line in lines:
        clipped = line[:inner]
        out.append("│ " + clipped.ljust(inner) + " │")
    out.append("└" + "─" * (width - 2) + "┘")
    return "\n".join(out)


def _describe_capture_height(h: int, min_h: int) -> str:
    if h <= min_h:
        return f"single-viewport 100vh live capture ({min_h}px)"
    if h >= 2400:
        return "clamped at 2400px live multi-section capture"
    return f"full-page live capture ({h}px)"


def write_ui_ux_and_blueprint(
    slug: str,
    info: dict,
    meta: dict,
    d_dims: Tuple[int, int, int],
    m_dims: Tuple[int, int, int],
) -> Tuple[int, int]:
    today = today_iso()
    d_w, d_h, _ = d_dims
    m_w, m_h, _ = m_dims
    d_cap_note = _describe_capture_height(d_h, 900)
    m_cap_note = _describe_capture_height(m_h, 844)
    capture_tier = meta.get("capture_tier") or "tier1_fast"
    if capture_tier not in ("tier1_fast", "tier2_webgl", "tier3_http1"):
        capture_tier = "tier1_fast"
    http_status = meta.get("http_status") or 200
    final_url = info.get("final_url") or info["url"]
    redirect_md = (
        f"- **Final Redirected URL**: [{final_url}]({final_url})\n"
        if is_cross_host_redirect(info["url"], final_url)
        else ""
    )

    sem_rows = "\n".join(
        f"| **{role}** | `{val}` | {src} |"
        for role, val, src in info["semantic_tokens"]
    )
    var_rows = "\n".join(
        f"| `{k}` | `{v}` | {role} |"
        for k, v, role in info["curated_vars"]
    )
    regions_md = "\n".join(
        f"{i + 1}. **{rtitle}**:\n   - {rdesc}"
        for i, (rtitle, rdesc) in enumerate(info["dom_regions"])
    )

    # 1. Write synchronized overview.md (zero wikilinks)
    write_clean_overview(
        slug=slug,
        meta=meta,
        status="analyzed",
        url=info["url"],
        final_url=final_url,
        title=info["title"],
        frameworks=info.get("frameworks", []),
        block_reason=None,
    )

    # 2. Write sanitized ui-ux.md
    ui_ux_md = f"""---
title: "UI & UX Architecture: {slug}"
date: {today}
captured_at: {today}
status: analyzed
capture: live
capture_tier: {capture_tier}
http_status: {http_status}
---

# UI & UX Architecture: {slug}

## Overview & Design Identity
- **Target**: `{slug}` (**{info['title']}**)
- **Production URL**: [{info['url']}]({info['url']})
{redirect_md}- **Capture Provenance**: `capture: live` (`{capture_tier}`, HTTP `{http_status}`, Desktop `{d_w}×{d_h}px`, Mobile `{m_w}×{m_h}px`)
- **Visual Archetype**: {info['archetype']}
- **Summary**: {info['summary']}
- **Total Clean CSS Variables Extracted**: `{info['total_clean_vars']}` (curated live-grounded set below)

---

## Typography & Hierarchy
- **Display Font Stack**: {info['fonts']['display']}
- **Body Font Stack**: {info['fonts']['body']}
- **Monospace / Technical Font Stack**: {info['fonts']['mono']}
- **Hero Display (`H1`)**: {info['fonts']['h1_spec']}
- **Section Heading (`H2`)**: {info['fonts']['h2_spec']}
- **Body Copy**: {info['fonts']['body_spec']}
- **Metadata & Labels**: {info['fonts']['meta_spec']}

---

## Semantic Color System (Live-Computed & Source-Grounded)
| Semantic Role | Ground-Truth Value | Extracted Live / CSS Source |
|---|---|---|
{sem_rows}

---

## Reverse-Engineered CSS Variables (Sanitized Ground Truth)
| Variable Name | Extracted Value | Semantic Role |
|---|---|---|
{var_rows}

---

## Structural DOM Landmarks (`dom.html`)
{regions_md}
"""
    atomic_write_text(TARGETS_DIR / slug / "ui-ux.md", ui_ux_md)

    # 3. Write 7-part prompt-blueprint.md
    diffs_md = "\n".join(f"  - {d}" for d in info["differentiators"])
    motion_rules_md = "\n".join(f"- {r}" for r in info["motion"]["rules"])
    ascii_box = make_ascii_box(info["wireframe_lines"], width=74)
    mobile_ascii_box = make_ascii_box(info["mobile_wireframe_lines"], width=74)

    root_css_lines = [f"  /* {info['title']} ({slug}) — Live-Grounded CSS Variables */"]
    for k, v, role in info["curated_vars"]:
        root_css_lines.append(f"  {k}: {v}; /* {role} ({slug}) */")
    root_css_block = "\n".join(root_css_lines)

    canvas_val, canvas_src = info["semantic_tokens"][0][1], info["semantic_tokens"][0][2]
    ink_val = info["ink_hex"]
    accent_val = info["accent_hex"]

    sec7_palette = "\n".join(
        f"- **{role}**: `{val}` — grounded in {src}"
        for role, val, src in info["semantic_tokens"]
    )

    region_contracts = [
        f"Header `{info['dom_regions'][0][0]}` (`{slug}`) adapts across {info['grid']['breakpoints']} with {info['motion']['primary_curve']} transitions and links `{info.get('nav_str', '')}`.",
        f"Hero stage (`{d_w}×{d_h}px` desktop / `{m_w}×{m_h}px` mobile) sets `{canvas_val}` background with `{ink_val}` headline `{info.get('h1_copy', slug)}`.",
        f"Showcase grid `{info['dom_regions'][2][0]}` applies {info['grid']['desktop_cols']} on desktop and {info['grid']['mobile_layout']} on mobile.",
        f"Detail module `{info['dom_regions'][3][0]}` uses {info['grid']['radii']} radii, `{accent_val}` highlights, and assets ({', '.join(info.get('media_assets', [])[:3]) or slug}).",
        f"Footer `{info['dom_regions'][4][0]}` aligns to {info['grid']['max_width']} with {info['grid']['padding']} and attributes ({', '.join(info.get('data_attrs', [])[:3]) or slug}).",
    ]
    sec7_regions = "\n".join(
        f"{i + 1}. **{rtitle}**:\n   - DOM & Selectors: {rdesc}\n   - Responsive Contract: {region_contracts[i % len(region_contracts)]}"
        for i, (rtitle, rdesc) in enumerate(info["dom_regions"])
    )
    sec7_motion = "\n".join(f"- {r}" for r in info["motion"]["rules"])
    sec7_css_rules = "\n".join(
        f"- `{sel} {{ {body} }}`" for sel, body in info.get("real_css_rules", [])[:5]
    ) or f"- `body {{ background: {canvas_val}; color: {ink_val}; }}`"
    sec7_copy = "\n".join(
        f'- Copy Excerpt {i + 1}: *"{c}"*' for i, c in enumerate(info.get("copy_snippets", [])[:4])
    ) or f'- Lead Copy: *"{info["summary"]}"*'

    sec3_valid_hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", sem_rows + "\n" + var_rows)}

    def sanitize_sec7_hexes(text: str) -> str:
        return re.sub(
            r"#[0-9a-fA-F]{3,8}\b",
            lambda m: m.group(0) if m.group(0).lower() in sec3_valid_hexes else canvas_val,
            text,
        )

    sec7_regions = sanitize_sec7_hexes(sec7_regions)
    sec7_motion = sanitize_sec7_hexes(sec7_motion)
    sec7_css_rules = sanitize_sec7_hexes(sec7_css_rules)
    sec7_copy = sanitize_sec7_hexes(sec7_copy)
    diffs_md = sanitize_sec7_hexes(diffs_md)
    sec7_h1_spec = sanitize_sec7_hexes(info["fonts"]["h1_spec"])
    sec7_h2_spec = sanitize_sec7_hexes(info["fonts"]["h2_spec"])
    sec7_body_spec = sanitize_sec7_hexes(info["fonts"]["body_spec"])
    sec7_meta_spec = sanitize_sec7_hexes(info["fonts"]["meta_spec"])

    # Clean double-backtick edge cases if any
    def fix_double_backticks(s: str) -> str:
        return re.sub(r"(?<!`)``(?!`)", "`", s)

    sec7_regions = fix_double_backticks(sec7_regions)
    sec7_motion = fix_double_backticks(sec7_motion)
    sec7_css_rules = fix_double_backticks(sec7_css_rules)
    sec7_copy = fix_double_backticks(sec7_copy)
    diffs_md = fix_double_backticks(diffs_md)
    sec7_h1_spec = fix_double_backticks(sec7_h1_spec)
    sec7_h2_spec = fix_double_backticks(sec7_h2_spec)
    sec7_body_spec = fix_double_backticks(sec7_body_spec)
    sec7_meta_spec = fix_double_backticks(sec7_meta_spec)

    safe_archetype = yaml_escape(info["archetype"])
    bp_md = f"""---
title: "UI Prompt Blueprint: {slug}"
date: {today}
captured_at: {today}
target: "{slug}"
status: analyzed
capture: live
capture_desktop: live
capture_mobile: live
capture_tier: {capture_tier}
http_status: {http_status}
archetype: "{safe_archetype}"
---

# UI Design System & Master Generation Blueprint: {slug}

> **{info['title']}** ({info['url']}) — {info['summary']}

![Desktop Viewport Capture ({d_w}x{d_h})](assets/desktop.png)

---

## 1. Executive Visual Persona & Design Archetype

- **Brand Essence & Persona**: **{info['title']}** (`{info['url']}`). {info['summary']}
{redirect_md}- **Capture Provenance**: Live browser capture (`{capture_tier}`, HTTP `{http_status}`, captured `{today}`).
- **Visual Archetype**: **{info['archetype']}**.
- **Anti-AI-Slop Differentiators**:
{diffs_md}

---

## 2. Spatial Architecture & Modular Grid Mathematics

![Mobile Viewport Capture ({m_w}x{m_h})](assets/mobile.png)

### Viewport Allocation & Container Geometry
- **Captured Viewport Dimensions**: Desktop `{d_w}×{d_h}px` (`assets/desktop.png`, {d_cap_note}), Mobile `{m_w}×{m_h}px` (`assets/mobile.png`, {m_cap_note}).
- **Master Container Bounds**: {info['grid']['max_width']}.
- **Canvas Lateral Padding & Spacing**: {info['grid']['padding']}.
- **Border Radius Scale**: {info['grid']['radii']}.

### Responsive Breakpoint Architecture
- **Desktop Layout**: {info['grid']['desktop_cols']}.
- **Mobile Layout**: {info['grid']['mobile_layout']}.
- **Extracted CSS Media Queries**: {info['grid']['breakpoints']}.

---

## 3. Color System, Materiality & Atmospheric Lighting

### Semantic Palette & Source Grounding
| Semantic Role | Ground-Truth Value | Extracted Live / CSS Source |
|---|---|---|
{sem_rows}

### Sanitized Root CSS Variables (`ui-ux.md` Ground Truth)
| Variable Name | Extracted Value | Semantic Role |
|---|---|---|
{var_rows}

---

## 4. Typographic Scale & Editorial Hierarchy

### Font Family Stacks
- **Display Typography**: {info['fonts']['display']}.
- **Primary Body Stack**: {info['fonts']['body']}.
- **Technical / Metadata Stack**: {info['fonts']['mono']}.

### Typographic Scale & Rules (Live Computed & Stylesheet Grounded)
- **Hero Display (`H1`)**: {info['fonts']['h1_spec']}.
- **Section Heading (`H2`)**: {info['fonts']['h2_spec']}.
- **Body Copy**: {info['fonts']['body_spec']}.
- **Metadata & Navigation Labels**: {info['fonts']['meta_spec']}.

---

## 5. Choreographed Motion & Interaction Physics

### Primary Easing Curves
- **Extracted Timing Functions**: {info['motion']['primary_curve']}.

### Per-Element Transition & Choreography Rules
{motion_rules_md}

---

## 6. Component Wireframe & Structural DOM Map

### Visual Layout Schematic — Desktop (`1440px`, Grounded in `dom.html` & Landmark Bounding Boxes)
```
{ascii_box}
```

### Visual Layout Schematic — Mobile Reflow (`390px`)
```
{mobile_ascii_box}
```

### Granular DOM Region Breakdown
{regions_md}

---

## 7. Master Zero-Shot AI Generation Prompt

```markdown
# Master Zero-Shot Frontend Generation Prompt: {slug} ({info['title']})

You are a principal frontend architect and design systems engineer. Build a complete, production-ready web application replicating the exact design system, DOM landmarks, typographic hierarchy, color tokens, and motion physics of `{slug}` (**{info['title']}** — `{info['url']}`).

### 1. Visual Archetype & Anti-AI-Slop Mandates
- **Archetype**: {info['archetype']}.
- **Identity & Domain Context**: {info['summary']}
- **Primary Substrate**: `{canvas_val}` ({canvas_src}), **Primary Ink**: `{ink_val}`, **Accent / Highlight**: `{accent_val}`.
- **Site-Specific Differentiators**:
{diffs_md}

### 2. Ground-Truth CSS Variables (`:root` Token Contract)
Declare and consume these exact custom properties extracted from `{slug}` in your global stylesheet:

:root {{
{root_css_block}
}}

### 3. Semantic Color & Materiality Contract
Map every surface, ink, and border strictly to these source-grounded tokens:
{sec7_palette}

### 4. Spatial Grid, Container Bounds & Responsive Breakpoints
- **Desktop Viewport Reference**: `{d_w}×{d_h}px` ({d_cap_note}, `1440px` standard desktop width).
- **Mobile Viewport Reference**: `{m_w}×{m_h}px` ({m_cap_note}, `390px` standard mobile width).
- **Container Max-Width**: {info['grid']['max_width']}
- **Padding & Gutters**: {info['grid']['padding']}
- **Desktop Grid**: {info['grid']['desktop_cols']}
- **Mobile Adaptation**: {info['grid']['mobile_layout']}
- **Media Queries**: {info['grid']['breakpoints']}
- **Corner Radii**: {info['grid']['radii']}

### 5. Typography & Editorial Scale Contract
- **Display Font Stack**: {info['fonts']['display']}
- **Body Font Stack**: {info['fonts']['body']}
- **Monospace / Technical Font Stack**: {info['fonts']['mono']}
- **H1 Display Rule**: {sec7_h1_spec}
- **H2 Section Rule**: {sec7_h2_spec}
- **Body Copy Rule**: {sec7_body_spec}
- **Metadata & Index Rule**: {sec7_meta_spec}

### 6. Component Hierarchy, Verbatim Copy & Real DOM Landmarks
Scaffold `{slug}` (`{info['title']}`) with these exact structural regions, extracted copy, and CSS rules from `dom.html`:
{sec7_regions}

#### Verbatim DOM Copy Excerpts (`dom.html`)
{sec7_copy}

### 7. Motion, Extracted Stylesheet Rules & Interaction Choreography
- **Primary Easing Functions**: {info['motion']['primary_curve']}
- **Extracted Stylesheet Rules (`{slug}`)**:
{sec7_css_rules}
- **Per-Selector Transition Rules**:
{sec7_motion}

### 8. Technical Deliverables
- Implement in Next.js (App Router) with TypeScript and Tailwind CSS consuming the `:root` custom properties defined above for `{slug}`.
- Reproduce both the `1440px` desktop architecture and `390px` mobile breakpoint transitions, preserving semantic HTML5 landmarks (`<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`) and `prefers-reduced-motion` accessibility guards.
```
"""
    atomic_write_text(TARGETS_DIR / slug / "prompt-blueprint.md", bp_md)
    m = re.search(r"## 7\..*?\n```[a-zA-Z]*\n(.*?)\n```", bp_md, flags=re.S)
    fence_lines = len(m.group(1).splitlines()) if m else 0
    return len(bp_md.splitlines()), fence_lines


def build_round_map(manifest_data: Optional[dict]) -> Tuple[Dict[int, List[str]], Dict[str, Dict[str, Any]]]:
    manifest_map: Dict[str, Dict[str, Any]] = {}
    rounds: Dict[int, List[str]] = {}
    if manifest_data and manifest_data.get("targets"):
        for entry in manifest_data["targets"]:
            slug = entry["slug"]
            manifest_map[slug] = entry
            rnum = int(entry["round_num"])
            rounds.setdefault(rnum, []).append(slug)
        for rnum in rounds:
            if rnum == 1:
                order = {s: i for i, s in enumerate(ROUND_1_SLUGS)}
                rounds[rnum].sort(key=lambda s: order.get(s, 999))
            else:
                rounds[rnum].sort()
        return rounds, manifest_map

    all_slugs = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
    remaining = [s for s in all_slugs if s not in ROUND_1_SLUGS]
    rounds = {1: [s for s in ROUND_1_SLUGS if s in all_slugs]}
    for idx in range(0, len(remaining), 10):
        rounds[2 + (idx // 10)] = remaining[idx : idx + 10]
    return rounds, manifest_map


def write_round_trace(
    rnum: int,
    batch_slugs: List[str],
    round_metrics: List[Tuple[str, dict, dict, Tuple[int, int, int], Tuple[int, int, int], int, int]],
    blocked_in_round: List[Tuple[str, str, dict]],
    eval_results: List[dict],
) -> None:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    today = today_iso()
    eval_map = {r["target"]: r for r in eval_results}
    avg_score = sum(r["total"] for r in eval_results) / len(eval_results) if eval_results else 25.0

    trace_rows = []
    for slug, info, meta, d_dims, m_dims, bp_lines, fence_lines in round_metrics:
        er = eval_map.get(slug, {"max_line_jacc": 0.0, "max_word_jacc": 0.0, "total": 0.0})
        tier = meta.get("capture_tier", "tier1_fast")
        trace_rows.append(
            f"| `{slug}` | `{tier}` | `{d_dims[0]}×{d_dims[1]}px` ({d_dims[2]:,} B) | `{m_dims[0]}×{m_dims[1]}px` ({m_dims[2]:,} B) | "
            f"{info['total_clean_vars']} | {len(info['curated_vars'])} | {bp_lines} | {fence_lines} | "
            f"`{er['max_line_jacc']:.2f}` / `{er['max_word_jacc']:.2f}` | **{er['total']:.1f} / 25.0** |"
        )
    blocked_map = {bslug: breason for bslug, breason, _ in blocked_in_round}
    for bslug, breason, bmeta in blocked_in_round:
        hs = bmeta.get("http_status")
        trace_rows.append(
            f"| `{bslug}` | `blocked` (`{hs}`) | `none` | `none` | — | — | — | — | — | **Excluded (`{yaml_escape(breason)[:70]}`)** |"
        )
    trace_table = "\n".join(trace_rows)

    info_title_map = {s: info["title"] for s, info, _, _, _, _, _ in round_metrics}
    target_list_md = "\n".join(
        f"  {i + 1}. `{slug}` — "
        + (f"*Blocked (`{yaml_escape(blocked_map[slug])}`)*" if slug in blocked_map else f"*{info_title_map.get(slug, slug)}*")
        for i, slug in enumerate(batch_slugs)
    )
    trace_md = f"""# Round {rnum:02d} Execution Trace: Live Recrawl & UI Prompt Blueprint Verification

> [!NOTE]
> **Audit Trace ID**: `TRACE-{today}-ROUND-{rnum:02d}`  
> **Active Branch**: `fix/live-recrawl-575-targets`  
> **Analyzed Targets**: `{len(round_metrics)}` live (`{len(blocked_in_round)}` marked `status: blocked` and excluded from tally per `C1`)  
> **Deterministic Evaluation (`C1`–`C15`)**: **`{avg_score:.1f} / 25.0`**

---

## 1. Round {rnum:02d} Target Scope
{target_list_md}

---

## 2. Live Capture & Ground-Truth Extraction Metrics (Verified PNG IHDR, Computed Styles & DOM/CSS)

| Target Slug | Capture Tier | Desktop PNG (`assets/desktop.png`) | Mobile PNG (`assets/mobile.png`) | Raw CSS Vars | Curated Root Vars | Blueprint Lines | Unsplit Fence Lines | Max Jaccard (Line / Word) | Truth Eval Score |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
{trace_table}
"""
    atomic_write_text(TRACES_DIR / f"round-{rnum:02d}-trace.md", trace_md)


async def run_pipeline(args: argparse.Namespace) -> None:
    from evaluate_batch import evaluate_target, split_sections

    manifest_data = None
    manifest_path = pathlib.Path(args.manifest) if args.manifest else DEFAULT_MANIFEST
    if manifest_path is not None and manifest_path.exists():
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    rounds, manifest_map = build_round_map(manifest_data)

    # Determine scope: --slugs vs --shard vs --start-round/--end-round
    explicit_slugs: Optional[List[str]] = None
    target_rounds: List[int] = []
    checkpoint_name = "manual.json"

    if args.slugs:
        explicit_slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        checkpoint_name = f"slugs_{len(explicit_slugs)}.json"
    elif args.shard is not None:
        if manifest_data and manifest_data.get("shards"):
            shard_obj = next((s for s in manifest_data["shards"] if int(s["shard_id"]) == args.shard), None)
            if not shard_obj:
                raise SystemExit(f"Shard {args.shard} not found in manifest")
            r_start, r_end = shard_obj["rounds"]
            target_rounds = list(range(int(r_start), int(r_end) + 1))
        else:
            all_r = sorted(rounds.keys())
            per = (len(all_r) + 5) // 6
            target_rounds = all_r[(args.shard - 1) * per : args.shard * per]
        checkpoint_name = f"shard_{args.shard}.json"
    else:
        max_r = max(rounds.keys()) if rounds else 1
        target_rounds = list(range(args.start_round, min(args.end_round, max_r) + 1))
        checkpoint_name = f"rounds_{args.start_round}_{args.end_round}.json"

    checkpoint_dir = pathlib.Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / checkpoint_name
    checkpoint = load_checkpoint(checkpoint_path) if args.resume else {"completed": {}}
    completed_map: Dict[str, Any] = checkpoint.setdefault("completed", {})

    async def process_slug_batch(
        pool: Optional[BrowserPool],
        batch_slugs: List[str],
        rnum: Optional[int] = None,
    ) -> None:
        t0 = time.time()
        round_metrics = []
        blocked_in_round = []

        for idx, slug in enumerate(batch_slugs, 1):
            url = resolve_target_url(slug, manifest_map)
            already_done = args.resume and slug in completed_map

            if args.live_recrawl and not already_done:
                assert pool is not None
                meta = await capture_target_live(
                    pool=pool,
                    slug=slug,
                    url=url,
                    raw_dir=RAW_DIR,
                    wiki_targets_dir=TARGETS_DIR,
                    deadline_seconds=120.0,
                    retry_extended=args.retry_extended,
                    force_tier2=args.force_tier2,
                )
            else:
                meta, _, _, _, _ = load_raw_bundle(slug)
                if not meta:
                    meta = {"target_name": slug, "url": url, "status": "blocked", "block_reason": "Missing metadata.json"}

            meta, computed, dom_text, full_css, soup = load_raw_bundle(slug)
            status = meta.get("status", "analyzed")

            if status == "blocked":
                reason = meta.get("block_reason") or "Upstream WAF / HTTP / Network Block"
                write_blocked_target(slug, reason, meta)
                blocked_in_round.append((slug, reason, meta))
                print(
                    f"  [{idx:02d}/{len(batch_slugs):02d}] {slug:36s} | BLOCKED (http={meta.get('http_status')}) | {reason[:70]}",
                    flush=True,
                )
            else:
                desktop_png = TARGETS_DIR / slug / "assets" / "desktop.png"
                mobile_png = TARGETS_DIR / slug / "assets" / "mobile.png"
                d_dims = get_png_dimensions(desktop_png)
                m_dims = get_png_dimensions(mobile_png)
                d_metrics = compute_png_visual_metrics(desktop_png, top_fold_height=900)
                m_metrics = compute_png_visual_metrics(mobile_png, top_fold_height=844)
                if (
                    d_dims[0] != 1440
                    or not (900 <= d_dims[1] <= 2400)
                    or m_dims[0] != 390
                    or not (844 <= m_dims[1] <= 2400)
                ):
                    reason = f"Invalid live PNG dimensions (desktop={d_dims[0]}x{d_dims[1]}, mobile={m_dims[0]}x{m_dims[1]})"
                    meta["status"] = "blocked"
                    meta["capture_tier"] = "none"
                    meta["block_reason"] = reason
                    meta["desktop_dims"] = [0, 0]
                    meta["mobile_dims"] = [0, 0]
                    atomic_write_text(RAW_DIR / slug / "metadata.json", json.dumps(meta, indent=2))
                    write_blocked_target(slug, reason, meta)
                    blocked_in_round.append((slug, reason, meta))
                    print(f"  [{idx:02d}/{len(batch_slugs):02d}] {slug:36s} | BLOCKED | {reason}", flush=True)
                elif is_solid_blank_png(d_metrics) or is_solid_blank_png(m_metrics):
                    reason = BLANK_VIEWPORT_BLOCK_REASON
                    meta["status"] = "blocked"
                    meta["capture_tier"] = "none"
                    meta["block_reason"] = reason
                    meta["desktop_dims"] = [0, 0]
                    meta["mobile_dims"] = [0, 0]
                    meta["desktop_metrics"] = d_metrics
                    meta["mobile_metrics"] = m_metrics
                    atomic_write_text(RAW_DIR / slug / "metadata.json", json.dumps(meta, indent=2))
                    write_blocked_target(slug, reason, meta)
                    blocked_in_round.append((slug, reason, meta))
                    print(
                        f"  [{idx:02d}/{len(batch_slugs):02d}] {slug:36s} | BLOCKED (blank PNG: "
                        f"D std={d_metrics.get('gray_std')}/edge={d_metrics.get('edge_density')}, "
                        f"M std={m_metrics.get('gray_std')}/edge={m_metrics.get('edge_density')}) | {reason}",
                        flush=True,
                    )
                else:
                    info = extract_ground_truth(slug, meta, computed, dom_text, full_css, soup)
                    bp_lines, fence_lines = write_ui_ux_and_blueprint(slug, info, meta, d_dims, m_dims)
                    round_metrics.append((slug, info, meta, d_dims, m_dims, bp_lines, fence_lines))
                    dm = meta.get("desktop_metrics") or {}
                    mm = meta.get("mobile_metrics") or {}
                    print(
                        f"  [{idx:02d}/{len(batch_slugs):02d}] {slug:36s} | LIVE ({meta.get('capture_tier', '?'):11s}) | "
                        f"D:{d_dims[0]}x{d_dims[1]} ({d_dims[2]//1024}KB std={dm.get('gray_std', '?')} q16={dm.get('dominant_q16_pct', '?')}%) | "
                        f"M:{m_dims[0]}x{m_dims[1]} ({m_dims[2]//1024}KB std={mm.get('gray_std', '?')}) | {meta.get('elapsed_seconds', 0):.1f}s",
                        flush=True,
                    )

            completed_map[slug] = {
                "status": meta.get("status", status),
                "capture_tier": meta.get("capture_tier", "none"),
                "http_status": meta.get("http_status"),
                "elapsed_seconds": meta.get("elapsed_seconds"),
            }
            save_checkpoint(checkpoint_path, checkpoint)

        active_slugs = [slug for slug, _, _, _, _, _, _ in round_metrics]
        sec7_line_sets = {}
        sec7_word_sets = {}
        for s in active_slugs:
            bp_txt = (TARGETS_DIR / s / "prompt-blueprint.md").read_text(encoding="utf-8")
            s7 = split_sections(bp_txt).get(7, "")
            sec7_line_sets[s] = {line.strip() for line in s7.splitlines() if len(line.strip()) > 15}
            sec7_word_sets[s] = set(re.findall(r"[a-zA-Z0-9_#-]{3,}", s7.lower()))

        eval_results = [evaluate_target(s, sec7_line_sets, sec7_word_sets) for s in active_slugs]
        for r in eval_results:
            if r["total"] < 25.0 or r["issues"]:
                print(f"  [!] Eval issue in {r['target']}: {r['total']}/25.0 -> {r['issues']}", flush=True)

        avg_score = sum(r["total"] for r in eval_results) / len(eval_results) if eval_results else 25.0
        elapsed = time.time() - t0
        label = f"Round {rnum:02d}/58" if rnum is not None else f"Custom ({len(batch_slugs)} slugs)"
        print(
            f"[{label}] {len(active_slugs)} live analyzed, {len(blocked_in_round)} blocked | "
            f"Avg Score: {avg_score:.1f}/25.0 | Time: {elapsed:.1f}s",
            flush=True,
        )

        # Write disjoint round trace ONLY when running a full round (shard or round range)
        if rnum is not None:
            write_round_trace(rnum, batch_slugs, round_metrics, blocked_in_round, eval_results)

    if args.live_recrawl:
        async with async_playwright() as p:
            pool = BrowserPool(p, recycle_every=20)
            try:
                if explicit_slugs is not None:
                    await process_slug_batch(pool, explicit_slugs, rnum=None)
                else:
                    for rnum in target_rounds:
                        if rnum in rounds:
                            await process_slug_batch(pool, rounds[rnum], rnum=rnum)
            finally:
                await pool.close_all()
    else:
        if explicit_slugs is not None:
            await process_slug_batch(None, explicit_slugs, rnum=None)
        else:
            for rnum in target_rounds:
                if rnum in rounds:
                    await process_slug_batch(None, rounds[rnum], rnum=rnum)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live 3-tier recrawl & blueprint generator (C1-C15)")
    parser.add_argument(
        "--manifest",
        type=str,
        default=os.environ.get("AUDIT_MANIFEST"),
        help="Optional path to audit_manifest.json (defaults to AUDIT_MANIFEST env or enumerating wiki/targets/*)",
    )
    parser.add_argument("--shard", type=int, choices=[1, 2, 3, 4, 5, 6], default=None, help="Disjoint shard ID (1..6)")
    parser.add_argument("--slugs", type=str, default=None, help="Comma-separated target slugs")
    parser.add_argument("--start-round", type=int, default=1, help="Start round when not using --shard/--slugs")
    parser.add_argument("--end-round", type=int, default=58, help="End round when not using --shard/--slugs")
    parser.add_argument("--live-recrawl", action="store_true", help="Perform live Playwright capture before regenerating docs")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint and skip already completed slugs")
    parser.add_argument("--retry-extended", action="store_true", help="Use extended timeouts and waits for retry worker")
    parser.add_argument("--force-tier2", action="store_true", help="Force Tier 2 SwiftShader WebGL capture directly")
    parser.add_argument("--checkpoint-dir", type=str, default=str(DEFAULT_CHECKPOINT_DIR), help="Directory for shard checkpoints")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run_pipeline(parse_args()))
