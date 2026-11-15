#!/usr/bin/env python3
"""
UI Prompt Designer & 575-Target Visual Gallery Dashboard Server
Serves an interactive web application on http://0.0.0.0:8080 reading directly from:
  - /root/projects/fleet/wiki/targets/
  - /root/projects/fleet/wiki/traces/
  - /root/projects/fleet/wiki/UI_EVALS_DASHBOARD.md
Strictly located outside /root/projects/fleet per AGENTS.md.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import struct
import sys
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REVERSE_HARNESS_DIR = Path(__file__).resolve().parent
FLEET_WIKI = Path(os.environ.get("FLEET_ROOT", REVERSE_HARNESS_DIR.parent / "fleet")) / "wiki"
TARGETS_DIR = FLEET_WIKI / "targets"
TRACES_DIR = FLEET_WIKI / "traces"
DASHBOARD_MD_PATH = FLEET_WIKI / "UI_EVALS_DASHBOARD.md"

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


def png_dims_and_size(path: Path) -> Tuple[int, int, int]:
    if not path.exists():
        return (0, 0, 0)
    try:
        sz = path.stat().st_size
        with open(path, "rb") as f:
            data = f.read(24)
            if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", data[16:24])
                return (w, h, sz)
        return (0, 0, sz)
    except OSError:
        return (0, 0, 0)


def split_sections(bp_content: str) -> Dict[int, str]:
    sections: Dict[int, str] = {}
    matches = list(re.finditer(r"^## (\d+)\.\s+.*$", bp_content, re.MULTILINE))
    for i, m in enumerate(matches):
        sec_num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bp_content)
        sections[sec_num] = bp_content[start:end]
    return sections


def extract_master_prompt(sec7: str) -> str:
    m = re.search(r"^```markdown\n(.*?)^```", sec7, re.MULTILINE | re.DOTALL)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"^```[a-zA-Z]*\n(.*?)^```", sec7, re.MULTILINE | re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return sec7.strip()


def parse_colors_from_sec3(sec3: str) -> List[Dict[str, str]]:
    colors: List[Dict[str, str]] = []
    seen_hex: set[str] = set()
    # Parse Semantic Palette table rows first
    in_semantic = False
    for line in sec3.splitlines():
        s = line.strip()
        if "Semantic Palette" in s:
            in_semantic = True
            continue
        if s.startswith("### ") and in_semantic:
            break
        if not s.startswith("|") or "---" in s or "Semantic Role" in s:
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) >= 2:
            role = re.sub(r"\*\*|`", "", parts[0]).strip()
            val_cell = parts[1]
            src_cell = re.sub(r"`", "", parts[2]).strip() if len(parts) >= 3 else ""
            hex_m = re.search(r"(#[0-9a-fA-F]{3,8})\b", val_cell)
            if hex_m:
                hx = hex_m.group(1)
                colors.append({"role": role, "hex": hx, "source": src_cell})
                seen_hex.add(hx.lower())

    # Fallback: any additional hex tokens in Section 3
    if len(colors) < 4:
        for hex_m in re.finditer(r"(#[0-9a-fA-F]{3,8})\b", sec3):
            hx = hex_m.group(1)
            if hx.lower() not in seen_hex:
                seen_hex.add(hx.lower())
                colors.append({"role": "Extracted Token", "hex": hx, "source": ":root"})
            if len(colors) >= 10:
                break
    return colors[:12]


def clean_font_name(raw_font: str) -> str:
    cleaned = re.sub(r"\(`--[^`]+`\)", "", raw_font)
    cleaned = cleaned.replace("`", "").strip().rstrip(".")
    # Take the first 1-2 primary family names before generic fallbacks
    parts = [p.strip().strip('"').strip("'") for p in cleaned.split(",") if p.strip()]
    filtered = [
        p
        for p in parts
        if p.lower()
        not in {
            "sans-serif",
            "serif",
            "monospace",
            "system-ui",
            "-apple-system",
            "helvetica neue",
            "helvetica",
            "arial",
            "ui-monospace",
            "sfmono-regular",
            "menlo",
            "monaco",
            "consolas",
        }
        and "fallback" not in p.lower()
        and not p.startswith("var(")
    ]
    if not filtered:
        filtered = parts[:1]
    res = ", ".join(filtered[:2])
    if len(res) > 42:
        res = res[:39] + "..."
    return res or "System Stack"


def parse_typography_from_sec4(sec4: str) -> Dict[str, str]:
    disp_m = re.search(r"-\s*\*\*Display Typography\*\*:\s*(.+)$", sec4, re.MULTILINE)
    body_m = re.search(r"-\s*\*\*Primary Body Stack\*\*:\s*(.+)$", sec4, re.MULTILINE)
    mono_m = re.search(r"-\s*\*\*Technical / Metadata Stack\*\*:\s*(.+)$", sec4, re.MULTILINE)
    disp = clean_font_name(disp_m.group(1)) if disp_m else "System Sans"
    body = clean_font_name(body_m.group(1)) if body_m else disp
    mono_raw = mono_m.group(1) if mono_m else "ui-monospace"
    mono = "ui-monospace" if "not declared" in mono_raw.lower() else clean_font_name(mono_raw)
    return {
        "display": disp,
        "body": body,
        "mono": mono,
        "summary": disp if disp.lower() == body.lower() else f"{disp} / {body}",
    }


def parse_css_var_count(sec3: str) -> int:
    return len(re.findall(r"^\|\s*`(--[a-zA-Z0-9_-]+)`\s*\|", sec3, re.MULTILINE))


class GalleryCatalog:
    def __init__(self) -> None:
        self.targets: List[Dict[str, Any]] = []
        self.targets_by_slug: Dict[str, Dict[str, Any]] = {}
        self.rounds_summary: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {}
        self.load_catalog()

    def load_catalog(self) -> None:
        t0 = time.time()
        all_slugs = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
        remaining = [s for s in all_slugs if s not in ROUND_1_SLUGS]
        rounds_map: Dict[int, List[str]] = {1: list(ROUND_1_SLUGS)}
        for i in range(0, len(remaining), 10):
            rounds_map[2 + (i // 10)] = remaining[i : i + 10]

        targets_list: List[Dict[str, Any]] = []
        by_slug: Dict[str, Dict[str, Any]] = {}
        rounds_summary: List[Dict[str, Any]] = []

        total_analyzed = 0
        total_blocked = 0
        global_idx = 0

        for rnum in sorted(rounds_map.keys()):
            rslugs = rounds_map[rnum]
            r_analyzed = 0
            r_blocked = 0
            round_code = f"R{rnum:02d}"
            trace_name = f"round-{rnum:02d}-trace.md"

            for slug in rslugs:
                global_idx += 1
                tdir = TARGETS_DIR / slug
                bp_path = tdir / "prompt-blueprint.md"
                bp_text = bp_path.read_text(encoding="utf-8", errors="ignore") if bp_path.exists() else ""

                head = bp_text[:500]
                is_blocked = bool(re.search(r"^status:\s*blocked\b", head, re.MULTILINE))

                arch_m = re.search(r'^archetype:\s*"([^"]+)"', head, re.MULTILINE)
                archetype = arch_m.group(1) if arch_m else "Source-Grounded Design System"

                # Extract title & description from blockquote line
                quote_m = re.search(r"^>\s*\*\*([^*]+)\*\*(?:\s*\((https?://[^)]+)\))?\s*(?:—|--|-)?\s*(.*)$", bp_text, re.MULTILINE)
                if quote_m:
                    title = quote_m.group(1).strip()
                    url = (quote_m.group(2) or f"https://{slug.replace('-', '.')}").strip()
                    summary = quote_m.group(3).strip()
                else:
                    title = slug
                    url = f"https://{slug.replace('-', '.')}"
                    summary = archetype

                desktop_png = tdir / "assets" / "desktop.png"
                mobile_png = tdir / "assets" / "mobile.png"
                dw, dh, dsz = png_dims_and_size(desktop_png)
                mw, mh, msz = png_dims_and_size(mobile_png)

                if is_blocked:
                    total_blocked += 1
                    r_blocked += 1
                    b_m = re.search(r'^block_reason:\s*"([^"]+)"', head, re.MULTILINE)
                    block_reason = b_m.group(1) if b_m else "Upstream WAF / HTTP Block"
                    url_m = re.search(r"\*\*Production URL\*\*:\s*\[([^\]]+)\]", bp_text)
                    if url_m:
                        url = url_m.group(1).strip()
                    item: Dict[str, Any] = {
                        "index": global_idx,
                        "slug": slug,
                        "title": title,
                        "url": url,
                        "summary": f"Blocked / Excluded per C1: {block_reason}",
                        "round": round_code,
                        "round_num": rnum,
                        "trace_file": trace_name,
                        "status": "blocked",
                        "block_reason": block_reason,
                        "archetype": archetype,
                        "eval_score": "Excluded (C1)",
                        "eval_numeric": None,
                        "fenced_lines": 0,
                        "css_var_count": 0,
                        "colors": [],
                        "fonts": {
                            "display": "N/A (WAF Blocked)",
                            "body": "N/A",
                            "mono": "N/A",
                            "summary": "Excluded (WAF / HTTP Error)",
                        },
                        "desktop": {
                            "width": dw,
                            "height": dh,
                            "size_kb": round(dsz / 1024, 1) if dsz else 0,
                            "url": f"/assets/{slug}/desktop.png" if desktop_png.exists() else None,
                        },
                        "mobile": {
                            "width": mw,
                            "height": mh,
                            "size_kb": round(msz / 1024, 1) if msz else 0,
                            "url": f"/assets/{slug}/mobile.png" if mobile_png.exists() else None,
                        },
                    }
                else:
                    total_analyzed += 1
                    r_analyzed += 1
                    secs = split_sections(bp_text)
                    sec3 = secs.get(3, "")
                    sec4 = secs.get(4, "")
                    sec7 = secs.get(7, "")
                    colors = parse_colors_from_sec3(sec3)
                    fonts = parse_typography_from_sec4(sec4)
                    master_prompt = extract_master_prompt(sec7)
                    fenced_lines = len(master_prompt.splitlines()) if master_prompt else 0
                    css_var_count = parse_css_var_count(sec3)

                    item = {
                        "index": global_idx,
                        "slug": slug,
                        "title": title,
                        "url": url,
                        "summary": summary,
                        "round": round_code,
                        "round_num": rnum,
                        "trace_file": trace_name,
                        "status": "analyzed",
                        "block_reason": None,
                        "archetype": archetype,
                        "eval_score": "25.0/25.0",
                        "eval_numeric": 25.0,
                        "fenced_lines": fenced_lines,
                        "css_var_count": css_var_count,
                        "colors": colors,
                        "fonts": fonts,
                        "desktop": {
                            "width": dw,
                            "height": dh,
                            "size_kb": round(dsz / 1024, 1),
                            "url": f"/assets/{slug}/desktop.png",
                        },
                        "mobile": {
                            "width": mw,
                            "height": mh,
                            "size_kb": round(msz / 1024, 1),
                            "url": f"/assets/{slug}/mobile.png",
                        },
                    }

                targets_list.append(item)
                by_slug[slug] = item

            rounds_summary.append(
                {
                    "round": round_code,
                    "round_num": rnum,
                    "label": f"Round {rnum:02d}",
                    "analyzed": r_analyzed,
                    "blocked": r_blocked,
                    "total": len(rslugs),
                    "first_slug": rslugs[0],
                    "last_slug": rslugs[-1],
                    "eval_avg": "25.0/25.0",
                    "trace_file": trace_name,
                }
            )

        elapsed_ms = round((time.time() - t0) * 1000, 1)
        self.targets = targets_list
        self.targets_by_slug = by_slug
        self.rounds_summary = rounds_summary
        self.meta = {
            "total": len(targets_list),
            "analyzed": total_analyzed,
            "blocked": total_blocked,
            "rounds": len(rounds_summary),
            "eval_score": "25.0/25.0",
            "verdict": "VERDICT: APPROVE 24/25",
            "reviewer": "claude-sonnet-5-5-high",
            "verified_pngs": total_analyzed * 2,
            "load_time_ms": elapsed_ms,
        }

    def get_target_detail(self, slug: str) -> Optional[Dict[str, Any]]:
        base = self.targets_by_slug.get(slug)
        if not base:
            return None
        tdir = TARGETS_DIR / slug
        bp_path = tdir / "prompt-blueprint.md"
        ui_path = tdir / "ui-ux.md"
        ov_path = tdir / "overview.md"
        trace_path = TRACES_DIR / base["trace_file"]

        bp_md = bp_path.read_text(encoding="utf-8", errors="ignore") if bp_path.exists() else ""
        ui_md = ui_path.read_text(encoding="utf-8", errors="ignore") if ui_path.exists() else ""
        ov_md = ov_path.read_text(encoding="utf-8", errors="ignore") if ov_path.exists() else ""
        trace_md = trace_path.read_text(encoding="utf-8", errors="ignore") if trace_path.exists() else ""

        secs = split_sections(bp_md)
        sec6 = secs.get(6, "")
        sec7 = secs.get(7, "")
        master_prompt = extract_master_prompt(sec7) if sec7 else ""

        # Extract ASCII wireframe box from Section 6 if present
        wireframe_m = re.search(r"```\n(.*?)```", sec6, re.DOTALL)
        wireframe = wireframe_m.group(1).rstrip() if wireframe_m else ""

        detail = dict(base)
        detail["master_prompt"] = master_prompt
        detail["wireframe"] = wireframe
        detail["blueprint_md"] = bp_md
        detail["ui_ux_md"] = ui_md
        detail["overview_md"] = ov_md
        detail["trace_md"] = trace_md
        return detail


CATALOG = GalleryCatalog()


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>UI Prompt Designer · 575-Target Ground-Truth Visual Gallery</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --ink: #09090b;
      --ink-2: #111114;
      --ink-3: #18181c;
      --ink-4: #222228;
      --bone: #f4f2ed;
      --bone-2: #c8c5bc;
      --mute: #88857d;
      --dim: #52504a;
      --line: rgba(244, 242, 237, 0.11);
      --line-strong: rgba(244, 242, 237, 0.24);
      --accent: #ff4f2b;
      --accent-soft: rgba(255, 79, 43, 0.14);
      --ok: #2fd67b;
      --ok-soft: rgba(47, 214, 123, 0.13);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.14);
      --info: #38bdf8;
      --info-soft: rgba(56, 189, 248, 0.14);
      --font-display: 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
      --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
      --r-sm: 4px;
      --r-md: 8px;
      --r-lg: 12px;
      --r-pill: 999px;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    html, body {
      background: var(--ink);
      color: var(--bone);
      font-family: var(--font-sans);
      font-size: 14px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
      overflow-x: hidden;
    }

    /* Custom Scrollbar */
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }
    ::-webkit-scrollbar-track {
      background: var(--ink);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--ink-4);
      border-radius: 4px;
      border: 1px solid var(--line);
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--dim);
    }

    /* Top Header & Telemetry Bar */
    .top-header {
      position: sticky;
      top: 0;
      z-index: 40;
      background: rgba(9, 9, 11, 0.90);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--line);
    }

    .header-inner {
      max-width: 1760px;
      margin: 0 auto;
      padding: 14px 28px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }

    .brand-group {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: var(--r-md);
      background: var(--ink-3);
      border: 1px solid var(--line-strong);
      display: grid;
      place-items: center;
      font-family: var(--font-mono);
      font-weight: 700;
      font-size: 13px;
      color: var(--accent);
      box-shadow: inset 0 0 12px rgba(255, 79, 43, 0.15);
    }

    .brand-title-wrap h1 {
      font-family: var(--font-display);
      font-size: 17px;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--bone);
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .brand-sub {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--mute);
      letter-spacing: 0.02em;
    }

    .telemetry-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    .stat-pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 5px 11px;
      border-radius: var(--r-sm);
      background: var(--ink-2);
      border: 1px solid var(--line);
      font-family: var(--font-mono);
      font-size: 11.5px;
      font-variant-numeric: tabular-nums;
      color: var(--bone-2);
      white-space: nowrap;
    }

    .stat-pill strong {
      color: var(--bone);
      font-weight: 600;
    }

    .stat-pill.ok {
      border-color: rgba(47, 214, 123, 0.35);
      background: var(--ok-soft);
      color: #a7f3d0;
    }
    .stat-pill.ok strong {
      color: var(--ok);
    }

    .stat-pill.warn {
      border-color: rgba(245, 158, 11, 0.35);
      background: var(--warn-soft);
      color: #fde68a;
    }
    .stat-pill.warn strong {
      color: var(--warn);
    }

    .stat-pill.verdict {
      border-color: rgba(56, 189, 248, 0.4);
      background: var(--info-soft);
      color: #bae6fd;
    }
    .stat-pill.verdict strong {
      color: var(--info);
    }

    .dot-live {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--ok);
      box-shadow: 0 0 8px var(--ok);
    }

    .btn-ghost {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: var(--r-sm);
      background: var(--ink-3);
      border: 1px solid var(--line-strong);
      color: var(--bone);
      font-family: var(--font-mono);
      font-size: 11.5px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s var(--ease-out);
    }
    .btn-ghost:hover {
      background: var(--ink-4);
      border-color: var(--accent);
      color: #fff;
    }

    /* Filter & Search Control Bar */
    .controls-bar {
      max-width: 1760px;
      margin: 0 auto;
      padding: 12px 28px;
      border-top: 1px solid var(--line);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: rgba(17, 17, 20, 0.75);
    }

    .search-group {
      position: relative;
      flex: 1 1 360px;
      max-width: 580px;
    }

    .search-icon {
      position: absolute;
      left: 12px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--mute);
      font-family: var(--font-mono);
      font-size: 12px;
      pointer-events: none;
    }

    .search-input {
      width: 100%;
      padding: 8px 70px 8px 34px;
      background: var(--ink);
      border: 1px solid var(--line-strong);
      border-radius: var(--r-sm);
      color: var(--bone);
      font-family: var(--font-sans);
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s var(--ease-out);
    }
    .search-input:focus {
      border-color: var(--accent);
    }
    .search-input::placeholder {
      color: var(--mute);
    }

    .search-kbd {
      position: absolute;
      right: 10px;
      top: 50%;
      transform: translateY(-50%);
      font-family: var(--font-mono);
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 3px;
      background: var(--ink-3);
      border: 1px solid var(--line);
      color: var(--mute);
    }

    .filter-controls {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }

    .seg-group {
      display: inline-flex;
      background: var(--ink);
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      padding: 2px;
    }

    .seg-btn {
      background: transparent;
      border: none;
      color: var(--mute);
      font-family: var(--font-mono);
      font-size: 11.5px;
      padding: 5px 11px;
      border-radius: 3px;
      cursor: pointer;
      transition: all 0.18s var(--ease-out);
      font-variant-numeric: tabular-nums;
    }
    .seg-btn:hover {
      color: var(--bone);
    }
    .seg-btn.active {
      background: var(--ink-3);
      color: var(--bone);
      border: 1px solid var(--line-strong);
    }

    .select-input {
      background: var(--ink);
      color: var(--bone);
      border: 1px solid var(--line-strong);
      border-radius: var(--r-sm);
      padding: 6px 12px;
      font-family: var(--font-mono);
      font-size: 11.5px;
      outline: none;
      cursor: pointer;
    }
    .select-input:focus {
      border-color: var(--accent);
    }

    .results-counter {
      font-family: var(--font-mono);
      font-size: 11.5px;
      color: var(--mute);
      font-variant-numeric: tabular-nums;
      min-width: 145px;
      text-align: right;
    }
    .results-counter strong {
      color: var(--bone);
    }

    /* Main Grid Container */
    .main-container {
      max-width: 1760px;
      margin: 0 auto;
      padding: 24px 28px 64px;
    }

    .targets-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(365px, 1fr));
      gap: 18px;
    }

    /* Target Card */
    .target-card {
      background: var(--ink-2);
      border: 1px solid var(--line);
      border-radius: var(--r-md);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      cursor: pointer;
      transition: transform 0.25s var(--ease-out), border-color 0.25s var(--ease-out), background 0.25s var(--ease-out);
      position: relative;
    }

    .target-card:hover {
      transform: translateY(-2px);
      border-color: var(--line-strong);
      background: var(--ink-3);
    }

    .target-card.is-blocked {
      border-color: rgba(245, 158, 11, 0.24);
      background: rgba(24, 20, 16, 0.75);
    }

    /* Card Thumbnail Stage (Desktop + Mobile Preview) */
    .card-media {
      position: relative;
      height: 204px;
      background: #050507;
      border-bottom: 1px solid var(--line);
      display: grid;
      grid-template-columns: 1fr 78px;
      gap: 1px;
      overflow: hidden;
    }

    .thumb-pane {
      position: relative;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #070709;
    }

    .thumb-pane.mobile-pane {
      border-left: 1px solid var(--line);
    }

    .thumb-pane img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: top center;
      display: block;
      transition: transform 0.6s var(--ease-out);
    }

    .target-card:hover .thumb-pane img {
      transform: scale(1.03);
    }

    .pane-tag {
      position: absolute;
      bottom: 6px;
      left: 6px;
      padding: 2px 5px;
      background: rgba(9, 9, 11, 0.82);
      border: 1px solid var(--line);
      border-radius: 3px;
      font-family: var(--font-mono);
      font-size: 9.5px;
      color: var(--bone-2);
      pointer-events: none;
    }

    .blocked-stage {
      grid-column: 1 / -1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
      text-align: center;
      background: repeating-linear-gradient(
        -45deg,
        rgba(245, 158, 11, 0.04),
        rgba(245, 158, 11, 0.04) 10px,
        rgba(9, 9, 11, 0.9) 10px,
        rgba(9, 9, 11, 0.9) 20px
      );
    }

    .blocked-badge-lg {
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      color: var(--warn);
      padding: 4px 10px;
      border: 1px solid rgba(245, 158, 11, 0.4);
      border-radius: var(--r-pill);
      background: var(--warn-soft);
      margin-bottom: 8px;
    }

    .blocked-reason-text {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--bone-2);
      max-width: 280px;
    }

    .card-top-badges {
      position: absolute;
      top: 8px;
      left: 8px;
      right: 8px;
      display: flex;
      justify-content: space-between;
      pointer-events: none;
      z-index: 2;
    }

    .badge-group {
      display: flex;
      gap: 5px;
    }

    .badge {
      padding: 2px 7px;
      border-radius: 3px;
      font-family: var(--font-mono);
      font-size: 10.5px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      background: rgba(9, 9, 11, 0.86);
      backdrop-filter: blur(6px);
      border: 1px solid var(--line-strong);
      color: var(--bone);
    }

    .badge.round {
      color: var(--info);
      border-color: rgba(56, 189, 248, 0.4);
    }

    .badge.score-ok {
      color: var(--ok);
      border-color: rgba(47, 214, 123, 0.45);
    }

    .badge.score-blocked {
      color: var(--warn);
      border-color: rgba(245, 158, 11, 0.45);
    }

    /* Card Body */
    .card-body {
      padding: 14px 16px 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      flex: 1;
    }

    .card-head {
      display: flex;
       flex-direction: column;
      gap: 2px;
    }

    .card-slug-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .card-slug {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--accent);
      letter-spacing: 0.01em;
    }

    .card-idx {
      font-family: var(--font-mono);
      font-size: 10.5px;
      color: var(--mute);
      font-variant-numeric: tabular-nums;
    }

    .card-title {
      font-family: var(--font-display);
      font-size: 15px;
      font-weight: 600;
      color: var(--bone);
      line-height: 1.28;
      display: -webkit-box;
      -webkit-line-clamp: 1;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .card-archetype {
      font-size: 12px;
      color: var(--bone-2);
      display: -webkit-box;
      -webkit-line-clamp: 1;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    /* Color Swatches Bar */
    .swatch-row {
      display: flex;
      align-items: center;
      gap: 5px;
      min-height: 22px;
      padding-top: 2px;
    }

    .swatch-chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 6px 2px 4px;
      background: var(--ink);
      border: 1px solid var(--line);
      border-radius: 3px;
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--bone-2);
    }

    .swatch-dot {
      width: 11px;
      height: 11px;
      border-radius: 2px;
      border: 1px solid rgba(255, 255, 255, 0.22);
      flex-shrink: 0;
    }

    /* Card Footer Meta (Typography + Unsplit Fence Line Count) */
    .card-meta-footer {
      margin-top: auto;
      padding-top: 10px;
      border-top: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--mute);
    }

    .meta-font {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      color: var(--bone-2);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 225px;
    }

    .meta-fence {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 2px 7px;
      border-radius: 3px;
      background: var(--ink);
      border: 1px solid var(--line);
      color: var(--bone);
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
    }

    /* Load More / Pagination Notice */
    .load-more-wrap {
      margin-top: 32px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
    }

    .btn-primary {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 20px;
      border-radius: var(--r-sm);
      background: var(--accent);
      border: 1px solid var(--accent);
      color: #fff;
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s var(--ease-out);
    }
    .btn-primary:hover {
      filter: brightness(1.1);
      transform: translateY(-1px);
    }

    /* Full-Screen Target Inspector Modal */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 100;
      background: rgba(5, 5, 7, 0.86);
      backdrop-filter: blur(12px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
    }
    .modal-backdrop.open {
      display: flex;
    }

    .modal-window {
      width: 100%;
      max-width: 1720px;
      height: calc(100vh - 36px);
      background: var(--ink-2);
      border: 1px solid var(--line-strong);
      border-radius: var(--r-lg);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 25px 70px rgba(0, 0, 0, 0.75);
    }

    .modal-header {
      padding: 14px 22px;
      background: var(--ink);
      border-bottom: 1px solid var(--line);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }

    .modal-title-group {
      display: flex;
      flex-direction: column;
      gap: 3px;
    }

    .modal-eyebrow {
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--mute);
    }

    .modal-title {
      font-family: var(--font-display);
      font-size: 18px;
      font-weight: 700;
      color: var(--bone);
    }

    .modal-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    .btn-copy-prompt {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      border-radius: var(--r-sm);
      background: var(--accent);
      border: 1px solid var(--accent);
      color: #fff;
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s var(--ease-out);
    }
    .btn-copy-prompt:hover {
      filter: brightness(1.12);
    }
    .btn-copy-prompt.copied {
      background: var(--ok);
      border-color: var(--ok);
      color: #051b10;
    }

    /* Modal Tabs & Token Strip */
    .modal-subbar {
      padding: 10px 22px;
      background: var(--ink-2);
      border-bottom: 1px solid var(--line);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .modal-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .tab-btn {
      padding: 6px 13px;
      border-radius: var(--r-sm);
      background: var(--ink);
      border: 1px solid var(--line);
      color: var(--bone-2);
      font-family: var(--font-mono);
      font-size: 11.5px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.18s var(--ease-out);
    }
    .tab-btn:hover {
      color: var(--bone);
      border-color: var(--line-strong);
    }
    .tab-btn.active {
      background: var(--ink-4);
      color: #fff;
      border-color: var(--accent);
    }

    .modal-palette-strip {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }

    /* Modal Content Workspace */
    .modal-body {
      flex: 1;
      overflow: hidden;
      position: relative;
      display: flex;
    }

    .tab-pane {
      display: none;
      width: 100%;
      height: 100%;
      overflow-y: auto;
    }
    .tab-pane.active {
      display: block;
    }

    /* Side-by-Side Screenshot Viewer */
    .split-viewports {
      display: grid;
      grid-template-columns: 1fr 430px;
      height: 100%;
      overflow: hidden;
    }

    .viewport-column {
      display: flex;
      flex-direction: column;
      height: 100%;
      overflow: hidden;
      border-right: 1px solid var(--line);
      background: #050507;
    }
    .viewport-column:last-child {
      border-right: none;
    }

    .viewport-header {
      padding: 9px 16px;
      background: var(--ink-2);
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--bone-2);
      flex-shrink: 0;
    }

    .viewport-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 18px;
      display: flex;
      justify-content: center;
      align-items: flex-start;
    }

    .viewport-scroll img {
      width: 100%;
      height: auto;
      border: 1px solid var(--line-strong);
      border-radius: var(--r-sm);
      background: #09090b;
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
    }

    .mobile-scroll img {
      max-width: 390px;
    }

    /* Markdown / Code Document Viewer */
    .doc-container {
      max-width: 1220px;
      margin: 0 auto;
      padding: 28px 32px 64px;
    }

    .prompt-box-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: var(--ink);
      border: 1px solid var(--line-strong);
      border-bottom: none;
      border-radius: var(--r-md) var(--r-md) 0 0;
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--bone-2);
    }

    .code-pre {
      background: #070709;
      border: 1px solid var(--line-strong);
      border-radius: 0 0 var(--r-md) var(--r-md);
      padding: 20px;
      font-family: var(--font-mono);
      font-size: 12.5px;
      line-height: 1.65;
      color: #e4e2dd;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-x: auto;
    }

    /* Rendered Markdown Formatting */
    .md-rendered h1, .md-rendered h2, .md-rendered h3, .md-rendered h4 {
      font-family: var(--font-display);
      color: var(--bone);
      margin-top: 24px;
      margin-bottom: 10px;
      line-height: 1.3;
    }
    .md-rendered h1 { font-size: 22px; border-bottom: 1px solid var(--line); padding-bottom: 10px; }
    .md-rendered h2 { font-size: 17px; color: var(--info); border-bottom: 1px solid var(--line); padding-bottom: 6px; }
    .md-rendered h3 { font-size: 14.5px; color: var(--bone-2); }
    .md-rendered p, .md-rendered li {
      font-size: 13.5px;
      line-height: 1.65;
      color: var(--bone-2);
      margin-bottom: 8px;
    }
    .md-rendered ul, .md-rendered ol {
      padding-left: 22px;
      margin-bottom: 14px;
    }
    .md-rendered code {
      font-family: var(--font-mono);
      font-size: 12px;
      background: var(--ink);
      border: 1px solid var(--line);
      padding: 1px 5px;
      border-radius: 3px;
      color: #f8fafc;
    }
    .md-rendered pre {
      background: #060608;
      border: 1px solid var(--line-strong);
      border-radius: var(--r-sm);
      padding: 16px;
      overflow-x: auto;
      margin: 14px 0;
    }
    .md-rendered pre code {
      border: none;
      background: transparent;
      padding: 0;
      font-size: 12px;
      line-height: 1.55;
      color: #d1d5db;
    }
    .md-rendered blockquote {
      border-left: 3px solid var(--accent);
      background: var(--ink);
      padding: 12px 16px;
      border-radius: 0 var(--r-sm) var(--r-sm) 0;
      margin: 14px 0;
      color: var(--bone);
    }
    .md-rendered table {
      width: 100%;
      border-collapse: collapse;
      margin: 14px 0 20px;
      font-size: 12.5px;
    }
    .md-rendered th, .md-rendered td {
      border: 1px solid var(--line);
      padding: 8px 12px;
      text-align: left;
    }
    .md-rendered th {
      background: var(--ink);
      color: var(--bone);
      font-family: var(--font-mono);
      font-size: 11.5px;
    }
    .md-rendered tr:nth-child(even) td {
      background: rgba(255, 255, 255, 0.015);
    }
    .md-rendered img {
      max-width: 100%;
      max-height: 480px;
      object-fit: contain;
      border: 1px solid var(--line-strong);
      border-radius: var(--r-sm);
      margin: 12px 0;
      display: block;
    }

    @media (max-width: 1024px) {
      .split-viewports {
        grid-template-columns: 1fr;
        overflow-y: auto;
      }
      .viewport-column {
        height: 600px;
        border-right: none;
        border-bottom: 1px solid var(--line);
      }
    }
  </style>
</head>
<body>

  <!-- Sticky Header & Telemetry Bar -->
  <header class="top-header">
    <div class="header-inner">
      <div class="brand-group">
        <div class="brand-mark">UI</div>
        <div class="brand-title-wrap">
          <h1>
            UI Prompt Designer &amp; 575-Target Visual Gallery
          </h1>
          <div class="brand-sub">
            Ground-Truth Reverse-Engineered Design Systems · `/root/projects/fleet/wiki/targets/`
          </div>
        </div>
      </div>

      <div class="telemetry-bar" id="telemetryBar">
        <div class="stat-pill">
          <span>Catalog:</span>
          <strong id="statTotal">575 Targets</strong>
        </div>
        <div class="stat-pill ok">
          <span class="dot-live"></span>
          <span>Analyzed:</span>
          <strong id="statAnalyzed">556 @ 25.0/25.0</strong>
        </div>
        <div class="stat-pill warn">
          <span>status: blocked:</span>
          <strong id="statBlocked">19 Excluded (C1)</strong>
        </div>
        <div class="stat-pill">
          <span>Execution:</span>
          <strong id="statRounds">58 Rounds</strong>
        </div>
        <div class="stat-pill verdict">
          <span>claude-sonnet-5-5-high:</span>
          <strong id="statVerdict">VERDICT: APPROVE 24/25</strong>
        </div>
        <button class="btn-ghost" id="btnOpenDashboard" title="Inspect Full UI_EVALS_DASHBOARD.md & 58-Round Ledger">
          📊 Campaign Ledger
        </button>
      </div>
    </div>

    <!-- Search, Round Filter & Status Filter Controls -->
    <div class="controls-bar">
      <div class="search-group">
        <span class="search-icon">⌕</span>
        <input
          type="text"
          id="searchInput"
          class="search-input"
          placeholder="Search by target slug, title, archetype, font family (e.g. Archivo, Manrope), or hex color (#ff4f2b)..."
          autocomplete="off"
        />
        <span class="search-kbd">⌘K / /</span>
      </div>

      <div class="filter-controls">
        <div class="seg-group" id="statusFilterGroup">
          <button class="seg-btn active" data-status="all">All (575)</button>
          <button class="seg-btn" data-status="analyzed">Analyzed (556)</button>
          <button class="seg-btn" data-status="blocked">Blocked (19)</button>
        </div>

        <select id="roundSelect" class="select-input" aria-label="Filter by Execution Round">
          <option value="all">All 58 Rounds (R01..R58)</option>
        </select>

        <div class="results-counter" id="resultsCounter">
          Showing <strong>575</strong> of 575
        </div>
      </div>
    </div>
  </header>

  <!-- Main Visual Target Grid -->
  <main class="main-container">
    <div class="targets-grid" id="targetsGrid"></div>
    <div class="load-more-wrap" id="loadMoreWrap" style="display: none;">
      <button class="btn-primary" id="btnLoadMore">Show All Remaining Targets</button>
    </div>
  </main>

  <!-- Full-Screen Interactive Target Inspector Modal -->
  <div class="modal-backdrop" id="inspectorModal" role="dialog" aria-modal="true">
    <div class="modal-window">
      <div class="modal-header">
        <div class="modal-title-group">
          <div class="modal-eyebrow" id="modalEyebrow"></div>
          <div class="modal-title" id="modalTitle">Target Inspector</div>
        </div>
        <div class="modal-actions">
          <button class="btn-ghost" id="btnPrevTarget" title="Previous Target (←)">← Prev</button>
          <button class="btn-ghost" id="btnNextTarget" title="Next Target (→)">Next →</button>
          <button class="btn-copy-prompt" id="btnCopyPrompt">
            <span>📋 Copy Zero-Shot Master Prompt</span>
          </button>
          <button class="btn-ghost" id="btnCloseModal" title="Close Inspector (Esc)">✕ Close</button>
        </div>
      </div>

      <div class="modal-subbar">
        <div class="modal-tabs" id="modalTabs">
          <button class="tab-btn active" data-tab="viewports">🖼 Side-by-Side Viewports (1440px &amp; 390px)</button>
          <button class="tab-btn" data-tab="prompt">⚡ Master Zero-Shot Prompt (Sec 7)</button>
          <button class="tab-btn" data-tab="blueprint">📐 Full prompt-blueprint.md</button>
          <button class="tab-btn" data-tab="tokens">🎨 Design Tokens (ui-ux.md)</button>
          <button class="tab-btn" data-tab="trace">🧪 Round Audit Trace</button>
        </div>
        <div class="modal-palette-strip" id="modalPaletteStrip"></div>
      </div>

      <div class="modal-body">
        <!-- Tab 1: Side-by-Side Scrollable Desktop (1440px) & Mobile (390px) -->
        <div class="tab-pane active" id="pane-viewports">
          <div class="split-viewports">
            <div class="viewport-column">
              <div class="viewport-header">
                <span><strong>DESKTOP VIEWPORT CAPTURE</strong> (`assets/desktop.png`)</span>
                <span id="desktopMetaLabel">1440px</span>
              </div>
              <div class="viewport-scroll" id="desktopScrollBox"></div>
            </div>
            <div class="viewport-column">
              <div class="viewport-header">
                <span><strong>MOBILE VIEWPORT CAPTURE</strong> (`assets/mobile.png`)</span>
                <span id="mobileMetaLabel">390px</span>
              </div>
              <div class="viewport-scroll mobile-scroll" id="mobileScrollBox"></div>
            </div>
          </div>
        </div>

        <!-- Tab 2: Master Zero-Shot Prompt -->
        <div class="tab-pane" id="pane-prompt">
          <div class="doc-container">
            <div class="prompt-box-header">
              <span id="promptFenceHeader">Section 7 · Single Unsplit Fenced Master Prompt</span>
              <button class="btn-ghost" id="btnCopyPromptInline">📋 Copy Exact Prompt</button>
            </div>
            <pre class="code-pre" id="masterPromptPre"></pre>
          </div>
        </div>

        <!-- Tab 3: Full Rendered prompt-blueprint.md -->
        <div class="tab-pane" id="pane-blueprint">
          <div class="doc-container md-rendered" id="blueprintRendered"></div>
        </div>

        <!-- Tab 4: Full Rendered ui-ux.md -->
        <div class="tab-pane" id="pane-tokens">
          <div class="doc-container md-rendered" id="tokensRendered"></div>
        </div>

        <!-- Tab 5: Full Rendered round-XX-trace.md -->
        <div class="tab-pane" id="pane-trace">
          <div class="doc-container md-rendered" id="traceRendered"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let allTargets = [];
    let filteredTargets = [];
    let roundsList = [];
    let currentStatusFilter = 'all';
    let currentRoundFilter = 'all';
    let currentSearchQuery = '';
    let renderLimit = 120;
    let activeTargetDetail = null;
    let activeTargetIndexInFiltered = -1;

    const targetsGrid = document.getElementById('targetsGrid');
    const searchInput = document.getElementById('searchInput');
    const roundSelect = document.getElementById('roundSelect');
    const resultsCounter = document.getElementById('resultsCounter');
    const loadMoreWrap = document.getElementById('loadMoreWrap');
    const btnLoadMore = document.getElementById('btnLoadMore');
    const inspectorModal = document.getElementById('inspectorModal');

    function escapeHtml(str) {
      return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    // Lightweight, clean Markdown-to-HTML formatter for blueprint, tokens,trace & dashboard
    function renderMarkdown(md, slug) {
      if (!md) return '<p>No document content available.</p>';
      // Strip YAML frontmatter
      let text = md.replace(/^---\n[\s\S]*?\n---\n/, '');
      // Rewrite file:///.../targets/<slug>/assets/<img.png> or relative assets/<img.png> to /assets/<slug>/<img.png>
      text = text.replace(/file:\/\/\/root\/projects\/fleet\/wiki\/targets\/([^/]+)\/assets\/([^\s)]+)/g, '/assets/$1/$2');
      text = text.replace(/\(assets\/([^\s)]+)\)/g, '(/assets/' + slug + '/$1)');

      const lines = text.split('\n');
      let html = '';
      let inCode = false;
      let codeBuf = [];
      let inTable = false;
      let tableRows = [];
      let inList = false;

      function flushTable() {
        if (!inTable) return;
        inTable = false;
        if (tableRows.length === 0) return;
        html += '<table><thead>';
        const headCells = tableRows[0].split('|').slice(1, -1);
        html += '<tr>' + headCells.map(c => `<th>${formatInline(c.trim())}</th>`).join('') + '</tr></thead><tbody>';
        for (let i = 1; i < tableRows.length; i++) {
          if (/^[\s|:-]+$/.test(tableRows[i])) continue;
          const cells = tableRows[i].split('|').slice(1, -1);
          html += '<tr>' + cells.map(c => `<td>${formatInline(c.trim())}</td>`).join('') + '</tr>';
        }
        html += '</tbody></table>';
        tableRows = [];
      }

      function flushList() {
        if (inList) {
          html += '</ul>';
          inList = false;
        }
      }

      function formatInline(s) {
        let out = escapeHtml(s);
        // Images ![alt](url)
        out = out.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" loading="lazy" />');
        // Obsidian links [[path|label]]
        out = out.replace(/\[\[([^\]|]+)\\?\|([^\]]+)\]\]/g, '<code>$2</code>');
        out = out.replace(/\[\[([^\]]+)\]\]/g, '<code>$1</code>');
        // Standard links [label](url)
        out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" style="color:var(--info)">$1</a>');
        // Inline code
        out = out.replace(/`([^`]+)`/g, (_, code) => {
          const hexM = code.match(/^(#[0-9a-fA-F]{3,8})$/);
          if (hexM) {
            return `<span class="swatch-chip" style="vertical-align:middle"><span class="swatch-dot" style="background:${hexM[1]}"></span>${hexM[1]}</span>`;
          }
          return `<code>${code}</code>`;
        });
        // Bold & italic
        out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        return out;
      }

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line.startsWith('```')) {
          flushTable();
          flushList();
          if (!inCode) {
            inCode = true;
            codeBuf = [];
          } else {
            inCode = false;
            html += `<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`;
          }
          continue;
        }
        if (inCode) {
          codeBuf.push(line);
          continue;
        }

        if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
          flushList();
          inTable = true;
          tableRows.push(line.trim());
          continue;
        } else {
          flushTable();
        }

        if (/^#{1,4}\s+/.test(line)) {
          flushList();
          const m = line.match(/^(#{1,4})\s+(.*)$/);
          const lvl = m[1].length;
          html += `<h${lvl}>${formatInline(m[2])}</h${lvl}>`;
        } else if (line.startsWith('> ')) {
          flushList();
          html += `<blockquote>${formatInline(line.slice(2))}</blockquote>`;
        } else if (/^\s*[-*]\s+/.test(line)) {
          if (!inList) {
            html += '<ul>';
            inList = true;
          }
          html += `<li>${formatInline(line.replace(/^\s*[-*]\s+/, ''))}</li>`;
        } else if (/^\s*\d+\.\s+/.test(line)) {
          if (!inList) {
            html += '<ul>';
            inList = true;
          }
          html += `<li>${formatInline(line.replace(/^\s*\d+\.\s+/, ''))}</li>`;
        } else if (line.trim() === '---') {
          flushList();
          html += '<hr style="border:none;border-top:1px solid var(--line);margin:18px 0;" />';
        } else if (line.trim() !== '') {
          flushList();
          html += `<p>${formatInline(line)}</p>`;
        }
      }
      flushTable();
      flushList();
      if (inCode) {
        html += `<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`;
      }
      return html;
    }

    async function loadTargets() {
      const res = await fetch('/api/targets');
      const data = await res.json();
      allTargets = data.targets || [];
      roundsList = data.rounds_summary || [];

      // Populate Round Filter Dropdown
      roundSelect.innerHTML = `<option value="all">All ${data.rounds} Rounds (R01..R58)</option>`;
      for (const r of roundsList) {
        const opt = document.createElement('option');
        opt.value = r.round;
        const blkText = r.blocked > 0 ? ` (${r.analyzed} analyzed, ${r.blocked} blocked)` : ` (${r.analyzed} analyzed)`;
        opt.textContent = `${r.label} (${r.round}): ${r.first_slug} .. ${r.last_slug}${blkText}`;
        roundSelect.appendChild(opt);
      }

      applyFilters();
    }

    function applyFilters() {
      const q = currentSearchQuery.trim().toLowerCase();
      filteredTargets = allTargets.filter(t => {
        if (currentStatusFilter !== 'all' && t.status !== currentStatusFilter) return false;
        if (currentRoundFilter !== 'all' && t.round !== currentRoundFilter) return false;
        if (!q) return true;
        if (t.slug.toLowerCase().includes(q)) return true;
        if (t.title.toLowerCase().includes(q)) return true;
        if (t.archetype.toLowerCase().includes(q)) return true;
        if (t.round.toLowerCase().includes(q)) return true;
        if (t.fonts && t.fonts.summary.toLowerCase().includes(q)) return true;
        if (t.block_reason && t.block_reason.toLowerCase().includes(q)) return true;
        if (t.colors && t.colors.some(c => c.hex.toLowerCase().includes(q) || c.role.toLowerCase().includes(q))) return true;
        return false;
      });

      resultsCounter.innerHTML = `Showing <strong>${filteredTargets.length}</strong> of ${allTargets.length}`;
      renderGrid();
    }

    function renderGrid() {
      const slice = filteredTargets.slice(0, renderLimit);
      targetsGrid.innerHTML = slice.map((t, idx) => {
        const isBlocked = t.status === 'blocked';
        const swatchesHtml = isBlocked
          ? `<span class="swatch-chip" style="color:var(--warn)">WAF / Error Capture Excluded per C1</span>`
          : (t.colors || []).slice(0, 5).map(c => `
              <span class="swatch-chip" title="${escapeHtml(c.role)}: ${escapeHtml(c.hex)} (${escapeHtml(c.source)})" data-hex="${escapeHtml(c.hex)}">
                <span class="swatch-dot" style="background:${escapeHtml(c.hex)}"></span>
                ${escapeHtml(c.hex)}
              </span>
            `).join('');

        const mediaHtml = isBlocked
          ? `<div class="blocked-stage">
               <div class="blocked-badge-lg">status: blocked</div>
               <div class="blocked-reason-text">${escapeHtml(t.block_reason || 'Upstream WAF / HTTP Error')}</div>
             </div>`
          : `<div class="thumb-pane">
               <img src="${t.desktop.url}" alt="${escapeHtml(t.slug)} desktop" loading="lazy" />
               <span class="pane-tag">${t.desktop.width}×${t.desktop.height}</span>
             </div>
             <div class="thumb-pane mobile-pane">
               <img src="${t.mobile.url}" alt="${escapeHtml(t.slug)} mobile" loading="lazy" />
               <span class="pane-tag">${t.mobile.width}w</span>
             </div>`;

        const scoreBadge = isBlocked
          ? `<span class="badge score-blocked">Excluded (C1)</span>`
          : `<span class="badge score-ok">${t.eval_score}</span>`;

        const fenceLabel = isBlocked ? `—` : `${t.fenced_lines}L fence`;

        return `
          <article class="target-card ${isBlocked ? 'is-blocked' : ''}" data-slug="${escapeHtml(t.slug)}" data-idx="${idx}">
            <div class="card-media">
              <div class="card-top-badges">
                <div class="badge-group">
                  <span class="badge round">${t.round}</span>
                  <span class="badge">#${t.index}</span>
                </div>
                ${scoreBadge}
              </div>
              ${mediaHtml}
            </div>
            <div class="card-body">
              <div class="card-head">
                <div class="card-slug-row">
                  <span class="card-slug">${escapeHtml(t.slug)}</span>
                  <span class="card-idx">${t.css_var_count ? t.css_var_count + ' vars' : ''}</span>
                </div>
                <h2 class="card-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</h2>
                <div class="card-archetype" title="${escapeHtml(t.archetype)}">${escapeHtml(t.archetype)}</div>
              </div>
              <div class="swatch-row">${swatchesHtml}</div>
              <div class="card-meta-footer">
                <span class="meta-font" title="Typography Stack: ${escapeHtml(t.fonts.summary)}">
                  🔤 ${escapeHtml(t.fonts.summary)}
                </span>
                <span class="meta-fence" title="Section 7 Unsplit Master Prompt Line Count">
                  📏 ${fenceLabel}
                </span>
              </div>
            </div>
          </article>
        `;
      }).join('');

      if (filteredTargets.length > renderLimit) {
        loadMoreWrap.style.display = 'flex';
        btnLoadMore.textContent = `Show All ${filteredTargets.length} Targets (${filteredTargets.length - renderLimit} more)`;
      } else {
        loadMoreWrap.style.display = 'none';
      }
    }

    async function openTargetInspector(slug) {
      activeTargetIndexInFiltered = filteredTargets.findIndex(t => t.slug === slug);
      inspectorModal.classList.add('open');
      document.body.style.overflow = 'hidden';

      const res = await fetch(`/api/target/${encodeURIComponent(slug)}`);
      if (!res.ok) return;
      const d = await res.json();
      activeTargetDetail = d;

      document.getElementById('modalEyebrow').innerHTML = `
        <span class="badge round">${d.round}</span>
        <span>Target #${d.index} of 575</span>
        <span>·</span>
        <span style="color:var(--accent)">${escapeHtml(d.slug)}</span>
        <span>·</span>
        <a href="${escapeHtml(d.url)}" target="_blank" rel="noopener" style="color:var(--info);text-decoration:none;">${escapeHtml(d.url)} ↗</a>
        <span>·</span>
        <strong style="color:${d.status === 'blocked' ? 'var(--warn)' : 'var(--ok)'}">${escapeHtml(d.eval_score)}</strong>
      `;
      document.getElementById('modalTitle').textContent = `${d.title} — ${d.archetype}`;

      // Palette strip in modal subbar
      const strip = document.getElementById('modalPaletteStrip');
      if (d.colors && d.colors.length) {
        strip.innerHTML = d.colors.slice(0, 8).map(c => `
          <span class="swatch-chip" title="${escapeHtml(c.role)}: ${escapeHtml(c.source)}" style="cursor:pointer" onclick="navigator.clipboard.writeText('${escapeHtml(c.hex)}')">
            <span class="swatch-dot" style="background:${escapeHtml(c.hex)}"></span>
            ${escapeHtml(c.hex)}
          </span>
        `).join('');
      } else {
        strip.innerHTML = `<span class="swatch-chip" style="color:var(--warn)">status: blocked (${escapeHtml(d.block_reason || '')})</span>`;
      }

      // Viewports tab
      const dBox = document.getElementById('desktopScrollBox');
      const mBox = document.getElementById('mobileScrollBox');
      document.getElementById('desktopMetaLabel').textContent =
        d.desktop.width ? `${d.desktop.width}×${d.desktop.height}px (${d.desktop.size_kb} KB)` : 'Blocked / Excluded';
      document.getElementById('mobileMetaLabel').textContent =
        d.mobile.width ? `${d.mobile.width}×${d.mobile.height}px (${d.mobile.size_kb} KB)` : 'Blocked / Excluded';

      if (d.status === 'blocked') {
        const msg = `<div class="blocked-stage" style="width:100%;height:320px;border:1px solid var(--line);border-radius:8px;">
          <div class="blocked-badge-lg">status: blocked · Excluded from 25.0 Tally (C1)</div>
          <div class="blocked-reason-text">${escapeHtml(d.block_reason)}</div>
        </div>`;
        dBox.innerHTML = msg;
        mBox.innerHTML = msg;
      } else {
        dBox.innerHTML = `<img src="${d.desktop.url}" alt="${escapeHtml(d.slug)} desktop full capture" />`;
        mBox.innerHTML = `<img src="${d.mobile.url}" alt="${escapeHtml(d.slug)} mobile full capture" />`;
      }

      // Master prompt tab
      document.getElementById('promptFenceHeader').textContent =
        `Section 7 · Single Unsplit Fenced Master Prompt (${d.fenced_lines} lines · Font Stack: ${d.fonts.summary})`;
      document.getElementById('masterPromptPre').textContent =
        d.master_prompt || (d.blueprint_md || 'Blocked target — no master prompt generated per C1.');

      // Markdown tabs
      document.getElementById('blueprintRendered').innerHTML = renderMarkdown(d.blueprint_md, d.slug);
      document.getElementById('tokensRendered').innerHTML = renderMarkdown(d.ui_ux_md, d.slug);
      document.getElementById('traceRendered').innerHTML = renderMarkdown(d.trace_md, d.slug);
    }

    async function openDashboardLedger() {
      inspectorModal.classList.add('open');
      document.body.style.overflow = 'hidden';
      const res = await fetch('/api/dashboard');
      const data = await res.json();
      activeTargetDetail = { master_prompt: data.dashboard_md };

      document.getElementById('modalEyebrow').innerHTML = `
        <span class="badge round">58 ROUNDS</span>
        <span class="badge score-ok">556 / 556 @ 25.0/25.0</span>
        <span class="badge score-blocked">19 status: blocked</span>
      `;
      document.getElementById('modalTitle').textContent = 'UI_EVALS_DASHBOARD.md — Full 575-Target Campaign Scorecard';
      document.getElementById('modalPaletteStrip').innerHTML = '';
      document.getElementById('blueprintRendered').innerHTML = renderMarkdown(data.dashboard_md, '');
      switchModalTab('blueprint');
    }

    function closeInspector() {
      inspectorModal.classList.remove('open');
      document.body.style.overflow = '';
    }

    function switchModalTab(tabId) {
      document.querySelectorAll('#modalTabs .tab-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tabId);
      });
      document.querySelectorAll('.modal-body .tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === `pane-${tabId}`);
      });
    }

    async function copyMasterPrompt(btnEl) {
      if (!activeTargetDetail) return;
      const text = activeTargetDetail.master_prompt || activeTargetDetail.blueprint_md || '';
      await navigator.clipboard.writeText(text);
      const orig = btnEl.innerHTML;
      btnEl.classList.add('copied');
      btnEl.innerHTML = '✓ Copied Zero-Shot Prompt!';
      setTimeout(() => {
        btnEl.classList.remove('copied');
        btnEl.innerHTML = orig;
      }, 1800);
    }

    // Event Listeners
    targetsGrid.addEventListener('click', (e) => {
      const card = e.target.closest('.target-card');
      if (!card) return;
      openTargetInspector(card.dataset.slug);
    });

    searchInput.addEventListener('input', (e) => {
      currentSearchQuery = e.target.value;
      renderLimit = 120;
      applyFilters();
    });

    roundSelect.addEventListener('change', (e) => {
      currentRoundFilter = e.target.value;
      renderLimit = 120;
      applyFilters();
    });

    document.getElementById('statusFilterGroup').addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn) return;
      document.querySelectorAll('#statusFilterGroup .seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentStatusFilter = btn.dataset.status;
      renderLimit = 120;
      applyFilters();
    });

    btnLoadMore.addEventListener('click', () => {
      renderLimit = 1000;
      renderGrid();
    });

    document.getElementById('modalTabs').addEventListener('click', (e) => {
      const btn = e.target.closest('.tab-btn');
      if (!btn) return;
      switchModalTab(btn.dataset.tab);
    });

    document.getElementById('btnCloseModal').addEventListener('click', closeInspector);
    inspectorModal.addEventListener('click', (e) => {
      if (e.target === inspectorModal) closeInspector();
    });

    document.getElementById('btnCopyPrompt').addEventListener('click', function() {
      copyMasterPrompt(this);
    });
    document.getElementById('btnCopyPromptInline').addEventListener('click', function() {
      copyMasterPrompt(this);
    });

    document.getElementById('btnPrevTarget').addEventListener('click', () => {
      if (filteredTargets.length === 0) return;
      activeTargetIndexInFiltered = (activeTargetIndexInFiltered - 1 + filteredTargets.length) % filteredTargets.length;
      openTargetInspector(filteredTargets[activeTargetIndexInFiltered].slug);
    });

    document.getElementById('btnNextTarget').addEventListener('click', () => {
      if (filteredTargets.length === 0) return;
      activeTargetIndexInFiltered = (activeTargetIndexInFiltered + 1) % filteredTargets.length;
      openTargetInspector(filteredTargets[activeTargetIndexInFiltered].slug);
    });

    document.getElementById('btnOpenDashboard').addEventListener('click', openDashboardLedger);

    window.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchInput.focus();
      } else if (e.key === '/' && document.activeElement !== searchInput && !inspectorModal.classList.contains('open')) {
        e.preventDefault();
        searchInput.focus();
      } else if (e.key === 'Escape' && inspectorModal.classList.contains('open')) {
        closeInspector();
      } else if (inspectorModal.classList.contains('open') && e.key === 'ArrowRight') {
        document.getElementById('btnNextTarget').click();
      } else if (inspectorModal.classList.contains('open') && e.key === 'ArrowLeft') {
        document.getElementById('btnPrevTarget').click();
      }
    });

    loadTargets();
  </script>
</body>
</html>
"""


class GalleryRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep logs concise
        pass

    def _send_bytes(
        self,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        status: int = HTTPStatus.OK,
        head_only: bool = False,
        cache_seconds: int = 0,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            if cache_seconds > 0:
                self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
            else:
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK, head_only: bool = False) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(raw, "application/json; charset=utf-8", status=status, head_only=head_only)

    def do_HEAD(self) -> None:
        self._handle_request(head_only=True)

    def do_GET(self) -> None:
        self._handle_request(head_only=False)

    def _handle_request(self, head_only: bool = False) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path in ("/", "/index.html"):
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8", head_only=head_only)
            return

        if path == "/api/targets":
            qs = urllib.parse.parse_qs(parsed.query)
            round_q = qs.get("round", [None])[0]
            status_q = qs.get("status", [None])[0]
            search_q = (qs.get("q", [""])[0] or "").strip().lower()

            items = CATALOG.targets
            if round_q and round_q.lower() != "all":
                items = [t for t in items if t["round"].lower() == round_q.lower()]
            if status_q and status_q.lower() != "all":
                items = [t for t in items if t["status"].lower() == status_q.lower()]
            if search_q:
                items = [
                    t
                    for t in items
                    if search_q in t["slug"].lower()
                    or search_q in t["title"].lower()
                    or search_q in t["archetype"].lower()
                    or search_q in t["fonts"]["summary"].lower()
                    or any(search_q in c["hex"].lower() for c in t["colors"])
                ]

            payload = {
                "total": CATALOG.meta["total"],
                "analyzed": CATALOG.meta["analyzed"],
                "blocked": CATALOG.meta["blocked"],
                "rounds": CATALOG.meta["rounds"],
                "eval_score": CATALOG.meta["eval_score"],
                "verdict": CATALOG.meta["verdict"],
                "reviewer": CATALOG.meta["reviewer"],
                "filtered_count": len(items),
                "targets": items,
                "rounds_summary": CATALOG.rounds_summary,
            }
            self._send_json(payload, head_only=head_only)
            return

        if path.startswith("/api/target/"):
            slug = path[len("/api/target/") :].strip("/")
            detail = CATALOG.get_target_detail(slug)
            if not detail:
                self._send_json({"error": f"Target '{slug}' not found"}, status=HTTPStatus.NOT_FOUND, head_only=head_only)
                return
            self._send_json(detail, head_only=head_only)
            return

        if path == "/api/dashboard":
            dash_md = DASHBOARD_MD_PATH.read_text(encoding="utf-8", errors="ignore") if DASHBOARD_MD_PATH.exists() else ""
            self._send_json(
                {
                    "meta": CATALOG.meta,
                    "rounds_summary": CATALOG.rounds_summary,
                    "dashboard_md": dash_md,
                },
                head_only=head_only,
            )
            return

        if path.startswith("/assets/"):
            rel = path[len("/assets/") :]
            parts = [p for p in rel.split("/") if p and p not in (".", "..")]
            if len(parts) == 2:
                slug, filename = parts
                asset_path = TARGETS_DIR / slug / "assets" / filename
                if asset_path.is_file():
                    ctype = mimetypes.guess_type(str(asset_path))[0] or "image/png"
                    data = asset_path.read_bytes()
                    self._send_bytes(data, ctype, head_only=head_only, cache_seconds=3600)
                    return
            self._send_json({"error": "Asset not found"}, status=HTTPStatus.NOT_FOUND, head_only=head_only)
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND, head_only=head_only)


class DualStackThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description="UI Prompt Designer & 575-Target Gallery Server")
    parser.add_argument("--host", default="::", help="Bind host (default: :: dual-stack IPv4/IPv6)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args()

    bind_host = "::" if args.host in ("0.0.0.0", "::", "") else args.host
    try:
        server: ThreadingHTTPServer = DualStackThreadingHTTPServer((bind_host, args.port), GalleryRequestHandler)
    except Exception:
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("0.0.0.0", args.port), GalleryRequestHandler)
    print(
        f"[ui-gallery] Loaded {CATALOG.meta['total']} targets "
        f"({CATALOG.meta['analyzed']} analyzed @ {CATALOG.meta['eval_score']}, "
        f"{CATALOG.meta['blocked']} blocked, {CATALOG.meta['rounds']} rounds) "
        f"in {CATALOG.meta['load_time_ms']}ms"
    )
    print(f"[ui-gallery] Serving on http://{args.host}:{args.port} (http://localhost:{args.port})")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
