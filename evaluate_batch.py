#!/usr/bin/env python3
"""
Rigorous Semantic, Structural, Visual & Ground-Truth Evaluation Suite (C1-C15).

Verifies:
1. Visual Assets, Dimension & Live Provenance Verification (0-5 pts — C2, C11, C14):
   - Desktop PNG 1440x[900..2400], Mobile PNG 390x[844..2400]
   - Live provenance frontmatter (capture: live, capture_desktop: live, capture_mobile: live, capture_tier)
   - Zero synthetic specimen fallbacks
   - PIL/NumPy pixel health (rejects corrupt/0B and solid-blank gray_std < 8.0 & edge_density < 0.003;
     surfaces warnings for low-entropy/minimal frames with dominant_q16_pct > 95% & edge_density < 0.005)
2. Token Hygiene, Traceable Ground-Truth & Live Grounding (0-5 pts — C1, C2, C3, C5, C14, C15):
   - Zero parser debris, zero duplicates, traceable citations in Section 3, zero WAF block-page text
   - Live computed_styles.json & metadata.json exist and match overview.md / ui-ux.md / prompt-blueprint.md
   - Canvas polarity match between Section 3 bg_hex and computed_styles.json effectiveBgHex
   - Zero [[wikilinks]] in overview.md
   - Global PNG SHA256 uniqueness across batch
3. Structural, Typography & Wireframe Fidelity (0-5 pts — C3, C4, C6, C7, C14):
   - 7 sections, 74-char aligned ASCII wireframe, real DOM classes, real type scale, zero icon fonts
4. Internal Cross-Section Consistency (0-5 pts — C1, C4, C5, C12):
   - Section 7 hexes/vars/beziers match Sections 3-5, zero double-backtick glitches
5. Master Prompt Generability & Uniqueness (0-5 pts — C10, C13):
   - Single unsplit fenced Master Prompt >= 80 lines, pairwise line Jaccard <= 0.30
Plus Blocked Target C15 Audit:
   - Zero blocked body corruption (capture: none strictly in YAML frontmatter only)
   - Zero stray PNGs in assets/ on blocked targets
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REVERSE_HARNESS_DIR = Path(__file__).resolve().parent
SRC_DIR = REVERSE_HARNESS_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from reverse_harness.live_capture import compute_png_visual_metrics, detect_preloader_text


def check_behavioral_scroll_coverage(behavior: Dict[str, Any]) -> Optional[str]:
    """
    C2: Verify top-to-bottom vertical scroll coverage on behavioral_journey_v2 targets.
    Flags tall pages (>2500px) that reached <60% of scrollHeight AND captured <3 keyframes.
    """
    if not isinstance(behavior, dict):
        return None
    max_h = int(behavior.get("max_scroll_height") or 0)
    max_y = int(behavior.get("max_scroll_y_reached") or 0)
    kfs = behavior.get("desktop_keyframes") or []
    if max_h <= 2500:
        return None
    eff_max_h = min(max_h, 14400 + 900)
    coverage = min(1.0, (max_y + 900) / max(1, eff_max_h))
    if coverage < 0.60 and len(kfs) < 3:
        return (
            f"Shallow scroll journey coverage: reached y={max_y}px/{max_h}px "
            f"({coverage:.0%}) with {len(kfs)} desktop keyframe(s) (C2)"
        )
    return None

TARGETS = [
    "dennypratama-com", "agnestoth-com", "www-sigma-imaging-se", "morrow-valerian-studio",
    "wiro-ae", "www-anduril-com", "www-avilus-com", "www-eladiodieste-com",
    "www-unimaticwatches-com", "seedjewelry-com",
]

FLEET_DIR = Path(os.environ.get("FLEET_ROOT", REVERSE_HARNESS_DIR.parent / "fleet"))
TARGETS_DIR = FLEET_DIR / "wiki" / "targets"
RAW_DIR = Path(os.environ.get("RAW_DIR", REVERSE_HARNESS_DIR / "raw"))
DEFAULT_MANIFEST: Optional[Path] = (
    Path(os.environ["AUDIT_MANIFEST"]) if os.environ.get("AUDIT_MANIFEST") else None
)

BLOCK_PAGE_PATTERNS = re.compile(
    r"Access Denied|Checking your browser|Confirm you are human|Vercel Security Checkpoint|"
    r"request could not be satisfied|Deployment Paused|Site Not Found \| Framer|520: Web server|"
    r"Just a moment\.\.\.",
    re.IGNORECASE,
)


def normalize_hex(h: str) -> str:
    hs = (h or "").strip().lower()
    if re.match(r"^#[0-9a-f]{3}$", hs):
        return "#" + "".join(c * 2 for c in hs[1:])
    if re.match(r"^#[0-9a-f]{6}$", hs):
        return hs
    if re.match(r"^#[0-9a-f]{8}$", hs):
        return hs[:7]
    return hs


def split_frontmatter(md_text: str) -> Tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", md_text or "", flags=re.S)
    if m:
        return m.group(1), m.group(2)
    return "", md_text or ""


def png_dims_and_size(path: Path) -> Tuple[int, int, int]:
    if not path.exists():
        return (0, 0, 0)
    sz = path.stat().st_size
    with open(path, "rb") as f:
        data = f.read(24)
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return (w, h, sz)
    return (0, 0, sz)


def split_sections(bp_content: str) -> Dict[int, str]:
    sections: Dict[int, str] = {}
    matches = list(re.finditer(r"^## (\d+)\.\s+.*$", bp_content, re.MULTILINE))
    for i, m in enumerate(matches):
        sec_num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bp_content)
        sections[sec_num] = bp_content[start:end]
    return sections


def is_blocked_target(target_name: str) -> bool:
    bp_path = TARGETS_DIR / target_name / "prompt-blueprint.md"
    if not bp_path.exists():
        return False
    fm, _ = split_frontmatter(bp_path.read_text(encoding="utf-8", errors="ignore"))
    return bool(re.search(r"^status:\s*blocked\b", fm, re.MULTILINE))


def evaluate_blocked_target(target_name: str) -> Dict[str, Any]:
    """C15 verification for status: blocked targets: zero body corruption, zero stray PNGs, valid frontmatter."""
    target_path = TARGETS_DIR / target_name
    bp_path = target_path / "prompt-blueprint.md"
    ui_path = target_path / "ui-ux.md"
    ov_path = target_path / "overview.md"
    meta_path = RAW_DIR / target_name / "metadata.json"

    issues: List[str] = []
    if not bp_path.exists():
        issues.append("Missing prompt-blueprint.md")
        return {"target": target_name, "status": "blocked", "ok": False, "issues": issues}

    bp_txt = bp_path.read_text(encoding="utf-8", errors="ignore")
    fm, body = split_frontmatter(bp_txt)

    if not re.search(r"^status:\s*blocked\b", fm, re.MULTILINE):
        issues.append("Missing status: blocked in prompt-blueprint.md frontmatter")
    for field in ("capture", "capture_desktop", "capture_mobile"):
        if not re.search(rf"^{field}:\s*none\b", fm, re.MULTILINE):
            issues.append(f"Missing '{field}: none' in blocked frontmatter")
    if not re.search(r"^block_reason:\s*.+", fm, re.MULTILINE):
        issues.append("Missing block_reason in blocked frontmatter")

    # C15: zero blocked body corruption (no capture: none leaked into Markdown body)
    if re.search(r"^\s*capture(?:_desktop|_mobile)?:\s*none\b", body, re.MULTILINE):
        issues.append("Blocked blueprint body corruption: stray 'capture: none' outside YAML frontmatter (C15)")

    # C15: zero stray PNGs or WebPs on blocked targets
    assets_dir = target_path / "assets"
    if assets_dir.exists():
        for ap in sorted(assets_dir.iterdir()):
            if ap.is_file() and ap.suffix.lower() in (".png", ".webp"):
                issues.append(f"Stray visual asset exists on blocked target: assets/{ap.name} (C15)")

    if ui_path.exists():
        ui_fm, _ = split_frontmatter(ui_path.read_text(encoding="utf-8", errors="ignore"))
        if not re.search(r"^status:\s*blocked\b", ui_fm, re.MULTILINE):
            issues.append("ui-ux.md frontmatter missing status: blocked (C15)")
    else:
        issues.append("Missing ui-ux.md")

    if ov_path.exists():
        ov_txt = ov_path.read_text(encoding="utf-8", errors="ignore")
        ov_fm, _ = split_frontmatter(ov_txt)
        if not re.search(r"^status:\s*blocked\b", ov_fm, re.MULTILINE):
            issues.append("overview.md frontmatter missing status: blocked (C15)")
        if re.search(r"\[\[.*?\]\]", ov_txt):
            issues.append("overview.md contains raw [[wikilinks]] (C15)")
    else:
        issues.append("Missing overview.md")

    if not meta_path.exists():
        issues.append("Missing raw/<slug>/metadata.json (C15)")

    return {
        "target": target_name,
        "status": "blocked",
        "ok": len(issues) == 0,
        "issues": issues,
    }


def evaluate_target(
    target_name: str,
    all_line_sets: Dict[str, Set[str]],
    all_word_sets: Dict[str, Set[str]],
    duplicate_hashes: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    target_path = TARGETS_DIR / target_name
    bp_path = target_path / "prompt-blueprint.md"
    ui_ux_path = target_path / "ui-ux.md"
    ov_path = target_path / "overview.md"
    dom_path = RAW_DIR / target_name / "dom.html"
    meta_path = RAW_DIR / target_name / "metadata.json"
    comp_path = RAW_DIR / target_name / "computed_styles.json"
    desktop_png = target_path / "assets" / "desktop.png"
    mobile_png = target_path / "assets" / "mobile.png"

    scores: Dict[str, float] = {}
    issues: List[str] = []
    warnings: List[str] = []

    bp_content = bp_path.read_text(encoding="utf-8") if bp_path.exists() else ""
    ui_content = ui_ux_path.read_text(encoding="utf-8") if ui_ux_path.exists() else ""
    ov_content = ov_path.read_text(encoding="utf-8") if ov_path.exists() else ""
    dom_content = dom_path.read_text(encoding="utf-8", errors="ignore") if dom_path.exists() else ""
    fm, bp_body = split_frontmatter(bp_content)
    secs = split_sections(bp_content)

    # 1. Visual Assets, Dimension & Real Live Provenance Verification (0-5 pts — C2, C11, C14)
    is_v2 = bool(re.search(r"^capture_mode:\s*behavioral_journey_v2\b", fm, re.MULTILINE))
    assets_dir = target_path / "assets"
    if is_v2:
        desktop_asset = assets_dir / "desktop-journey.webp"
        mobile_asset = assets_dir / "mobile-journey.webp"
        d_metrics = compute_png_visual_metrics(desktop_asset, top_fold_height=828)
        m_metrics = compute_png_visual_metrics(mobile_asset, top_fold_height=872)
    else:
        desktop_asset = desktop_png
        mobile_asset = mobile_png
        d_metrics = compute_png_visual_metrics(desktop_asset, top_fold_height=900)
        m_metrics = compute_png_visual_metrics(mobile_asset, top_fold_height=844)

    d_w, d_h, d_sz = d_metrics.get("width", 0), d_metrics.get("height", 0), d_metrics.get("bytes", 0)
    m_w, m_h, m_sz = m_metrics.get("width", 0), m_metrics.get("height", 0), m_metrics.get("bytes", 0)

    asset_score = 0.0
    if is_v2:
        if d_metrics.get("valid") and d_w == 1280 and 828 <= d_h < 16383:
            if d_metrics["gray_std"] < 8.0 and d_metrics["edge_density"] < 0.003:
                issues.append(f"Desktop WebP journey is solid/blank (std={d_metrics['gray_std']}, edge={d_metrics['edge_density']}) (C14)")
            else:
                asset_score += 2.0
                if d_metrics["dominant_q16_pct"] > 95.0 and d_metrics["edge_density"] < 0.005:
                    warnings.append(
                        f"Desktop WebP journey low-entropy/minimal (q16={d_metrics['dominant_q16_pct']}%, edge={d_metrics['edge_density']}) — visual review (P2)"
                    )
        else:
            issues.append(f"Desktop WebP journey must be valid 1280x[828..16382], got {d_w}x{d_h} ({d_sz}B) (C14)")

        if m_metrics.get("valid") and m_w == 390 and 872 <= m_h < 16383:
            if m_metrics["gray_std"] < 8.0 and m_metrics["edge_density"] < 0.003:
                issues.append(f"Mobile WebP journey is solid/blank (std={m_metrics['gray_std']}, edge={m_metrics['edge_density']}) (C14)")
            else:
                asset_score += 2.0
                if m_metrics["dominant_q16_pct"] > 95.0 and m_metrics["edge_density"] < 0.005:
                    warnings.append(
                        f"Mobile WebP journey low-entropy/minimal (q16={m_metrics['dominant_q16_pct']}%, edge={m_metrics['edge_density']}) — visual review (P2)"
                    )
        else:
            issues.append(f"Mobile WebP journey must be valid 390x[872..16382], got {m_w}x{m_h} ({m_sz}B) (C14)")

        # Strict v2 WebP hygiene: zero leftover .png files in assets/
        leftover_pngs = [p.name for p in assets_dir.glob("*.png")] if assets_dir.exists() else []
        if leftover_pngs:
            asset_score -= 1.0
            issues.append(f"Leftover legacy PNG(s) in behavioral_journey_v2 target: {leftover_pngs}")
        if not re.search(r"^asset_format:\s*webp\b", fm, re.MULTILINE):
            asset_score -= 0.5
            issues.append("Missing 'asset_format: webp' in behavioral_journey_v2 frontmatter")

        # C2 / P3: Verify vertical scroll journey coverage against raw/<slug>/behavior.json
        beh_path = RAW_DIR / target_name / "behavior.json"
        if beh_path.exists():
            try:
                beh_data = json.loads(beh_path.read_text(encoding="utf-8", errors="ignore"))
                cov_warn = check_behavioral_scroll_coverage(beh_data)
                if cov_warn:
                    warnings.append(cov_warn)
            except Exception as exc:
                warnings.append(f"Corrupt behavior.json ({exc}) — skipped C2 scroll coverage check (P3)")
    else:
        if d_metrics.get("valid") and d_w == 1440 and 900 <= d_h <= 2400:
            if d_metrics["gray_std"] < 8.0 and d_metrics["edge_density"] < 0.003:
                issues.append(
                    f"Desktop PNG is solid/blank (std={d_metrics['gray_std']}, edge={d_metrics['edge_density']}) (C14)"
                )
            else:
                asset_score += 2.0
                if d_metrics["dominant_q16_pct"] > 95.0 and d_metrics["edge_density"] < 0.005:
                    warnings.append(
                        f"Desktop PNG low-entropy/minimal (q16={d_metrics['dominant_q16_pct']}%, edge={d_metrics['edge_density']}) — queue for visual review"
                    )
                if d_metrics.get("has_blank_tail"):
                    warnings.append(
                        f"Desktop PNG blank bottom tail (tail_std={d_metrics.get('tail_gray_std')}, tail_edge={d_metrics.get('tail_edge_density')}) — R3-1/R4 visual review"
                    )
                if d_metrics.get("has_mid_blank_void"):
                    warnings.append(
                        f"Desktop PNG mid-page solid void ({d_metrics.get('max_blank_void_px')}px at y={d_metrics.get('mid_blank_void_range')}) — R3-1 visual review"
                    )
        else:
            issues.append(f"Desktop PNG must be valid 1440x[900..2400], got {d_w}x{d_h} ({d_sz}B) (C14)")

        if m_metrics.get("valid") and m_w == 390 and 844 <= m_h <= 2400:
            if m_metrics["gray_std"] < 8.0 and m_metrics["edge_density"] < 0.003:
                issues.append(
                    f"Mobile PNG is solid/blank (std={m_metrics['gray_std']}, edge={m_metrics['edge_density']}) (C14)"
                )
            else:
                asset_score += 2.0
                if m_metrics["dominant_q16_pct"] > 95.0 and m_metrics["edge_density"] < 0.005:
                    warnings.append(
                        f"Mobile PNG low-entropy/minimal (q16={m_metrics['dominant_q16_pct']}%, edge={m_metrics['edge_density']}) — queue for visual review"
                    )
                if m_metrics.get("has_blank_tail"):
                    warnings.append(
                        f"Mobile PNG blank bottom tail (tail_std={m_metrics.get('tail_gray_std')}, tail_edge={m_metrics.get('tail_edge_density')}) — R3-1/R4 visual review"
                    )
                if m_metrics.get("has_mid_blank_void"):
                    warnings.append(
                        f"Mobile PNG mid-page solid void ({m_metrics.get('max_blank_void_px')}px at y={m_metrics.get('mid_blank_void_range')}) — R3-1 visual review"
                    )
        else:
            issues.append(f"Mobile PNG must be valid 390x[844..2400], got {m_w}x{m_h} ({m_sz}B) (C14)")

    has_dw = f"{d_w}x{d_h}" in bp_content or f"{d_w}×{d_h}" in bp_content
    has_mw = f"{m_w}x{m_h}" in bp_content or f"{m_w}×{m_h}" in bp_content
    if has_dw and has_mw:
        asset_score += 1.0
    else:
        issues.append(f"Exact asset dimensions ({d_w}x{d_h}, {m_w}x{m_h}) not documented in blueprint")

    cap_m = re.search(r"^capture:\s*(\S+)", fm, re.MULTILINE)
    cap_d_m = re.search(r"^capture_desktop:\s*(\S+)", fm, re.MULTILINE)
    cap_m_m = re.search(r"^capture_mobile:\s*(\S+)", fm, re.MULTILINE)
    tier_m = re.search(r"^capture_tier:\s*(tier1_fast|tier2_webgl|tier3_http1)\b", fm, re.MULTILINE)

    if not (cap_m and cap_m.group(1) == "live" and cap_d_m and cap_d_m.group(1) == "live" and cap_m_m and cap_m_m.group(1) == "live"):
        asset_score -= 1.5
        issues.append(
            f"C14 requires capture: live, capture_desktop: live, capture_mobile: live "
            f"(got capture={cap_m.group(1) if cap_m else None}, d={cap_d_m.group(1) if cap_d_m else None}, m={cap_m_m.group(1) if cap_m_m else None})"
        )
    if not tier_m:
        asset_score -= 0.5
        issues.append("Missing valid capture_tier (tier1_fast|tier2_webgl|tier3_http1) in frontmatter (C14)")
    if "Design Specimen" in bp_content:
        asset_score -= 2.0
        issues.append("Synthetic 'Design Specimen' fallback text found in live blueprint (C14)")
    if is_v2:
        if "Desktop Journey Filmstrip" not in bp_body or "Mobile Journey Filmstrip" not in bp_body:
            asset_score -= 0.5
            issues.append("Missing Desktop/Mobile Journey Filmstrip captions in v2 blueprint (C14)")
    else:
        if "Desktop Viewport Capture" not in bp_body or "Mobile Viewport Capture" not in bp_body:
            asset_score -= 0.5
            issues.append("Missing live Desktop/Mobile Viewport Capture captions in blueprint (C14)")

    if duplicate_hashes:
        if d_metrics.get("sha256") in duplicate_hashes:
            asset_score -= 1.5
            issues.append(f"Duplicate desktop asset SHA256 hash across catalog: {d_metrics['sha256'][:12]} (C15)")
        if m_metrics.get("sha256") in duplicate_hashes:
            asset_score -= 1.5
            issues.append(f"Duplicate mobile asset SHA256 hash across catalog: {m_metrics['sha256'][:12]} (C15)")

    scores["visual_assets"] = max(0.0, round(asset_score, 1))

    # 2. Token Hygiene, Traceable Citations & Live C15 Grounding (0-5 pts)
    token_score = 5.0
    sec3 = secs.get(3, "")
    if BLOCK_PAGE_PATTERNS.search(ui_content[:600]) or BLOCK_PAGE_PATTERNS.search(bp_body[:600]):
        token_score -= 3.0
        issues.append("WAF/Access-Denied/Error block-page text found in analyzed target (C1/C14)")

    table_lines = [l for l in (sec3 + "\n" + ui_content).splitlines() if l.strip().startswith("|") and "---" not in l]
    for tl in table_lines:
        unquoted = re.sub(r"`[^`]*`", "", tl)
        if "{" in unquoted or "}" in unquoted or "@media" in tl or "@property" in tl:
            token_score -= 2.0
            issues.append(f"Parser debris in table row: {tl[:60]}")
            break
    if "Extracted CSS Variable" in sec3 or "Extracted CSS Variable" in ui_content:
        token_score -= 1.5
        issues.append("Placeholder role 'Extracted CSS Variable' found")

    if re.search(r"\|\s*(?:surface|primary ink|muted copy|accent highlight|structural divider)\s+(?:token|rule)\s+in\s+`", sec3):
        token_score -= 1.5
        issues.append("Untraceable generic color citation in Section 3 Semantic Palette (C5)")

    sec3_vars = re.findall(r"^\|\s*`(--[a-zA-Z0-9_-]+)`\s*\|", sec3, re.MULTILINE)
    if len(sec3_vars) != len(set(sec3_vars)):
        token_score -= 1.5
        issues.append("Duplicate CSS variable names in Section 3")
    if len(sec3_vars) < 12:
        token_score -= 1.5
        issues.append(f"Too few CSS variables in Section 3 ({len(sec3_vars)})")

    ui_vars = set(re.findall(r"^\|\s*`(--[a-zA-Z0-9_-]+)`\s*\|", ui_content, re.MULTILINE))
    if not set(sec3_vars).issubset(ui_vars):
        token_score -= 1.0
        issues.append("Section 3 variables do not match ui-ux.md")

    # C15: Live computed_styles.json & metadata.json grounding + cross-file consistency + polarity
    meta_data = None
    comp_data = None
    if not meta_path.exists():
        token_score -= 1.0
        issues.append("Missing raw/<slug>/metadata.json (C15)")
    else:
        try:
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            token_score -= 1.0
            issues.append("Corrupt raw/<slug>/metadata.json (C15)")

    if not comp_path.exists():
        token_score -= 1.0
        issues.append("Missing raw/<slug>/computed_styles.json (C15)")
    else:
        try:
            comp_data = json.loads(comp_path.read_text(encoding="utf-8"))
        except Exception:
            token_score -= 1.0
            issues.append("Corrupt raw/<slug>/computed_styles.json (C15)")

    if ov_content:
        if re.search(r"\[\[.*?\]\]", ov_content):
            token_score -= 0.5
            issues.append("overview.md contains raw [[wikilinks]] (C15)")
        ov_fm, _ = split_frontmatter(ov_content)
        if not re.search(r"^status:\s*analyzed\b", ov_fm, re.MULTILINE):
            token_score -= 0.5
            issues.append("overview.md status does not match analyzed blueprint (C15)")
    else:
        token_score -= 0.5
        issues.append("Missing overview.md (C15)")

    if meta_data:
        meta_url = meta_data.get("url", "")
        if meta_url and meta_url not in bp_content:
            token_score -= 0.5
            issues.append("metadata.json URL missing from prompt-blueprint.md (C15)")
        if meta_data.get("status") != "analyzed":
            token_score -= 1.0
            issues.append(f"metadata.json status={meta_data.get('status')} contradicts analyzed blueprint (C15)")

    if comp_data:
        eff_bg = normalize_hex(comp_data.get("effectiveBgHex") or "")
        bg_row_m = re.search(r"^\|\s*\*\*Primary Canvas Substrate\*\*\s*\|\s*`(#[0-9a-fA-F]{3,8})`", sec3, re.MULTILINE)
        if eff_bg and bg_row_m:
            bp_bg = normalize_hex(bg_row_m.group(1))
            if bp_bg != eff_bg:
                token_score -= 1.0
                issues.append(f"Section 3 canvas substrate {bp_bg} does not match live computed_styles.json {eff_bg} (C15)")
        # Polarity warning check against screenshot modal luma
        arch_m = re.search(r'^archetype:\s*"([^"]+)"', fm, re.MULTILINE)
        arch_txt = arch_m.group(1).lower() if arch_m else ""
        modal_luma = d_metrics.get("modal_luma", 128.0)
        if ("dark-substrate" in arch_txt or "obsidian" in arch_txt) and modal_luma > 215.0:
            warnings.append(f"Polarity note: dark archetype but desktop screenshot modal_luma={modal_luma}")
        elif ("light-substrate" in arch_txt or "light-gallery" in arch_txt) and modal_luma < 40.0:
            warnings.append(f"Polarity note: light archetype but desktop screenshot modal_luma={modal_luma}")
        # R3 preloader-frame warning check
        vis_sample = str(comp_data.get("visibleTextSample") or "")
        vis_len = (comp_data.get("domMetrics") or {}).get("visibleTextLen")
        pre_hit = detect_preloader_text(vis_sample, vis_len)
        if pre_hit:
            warnings.append(f"Preloader-frame text detected in computed_styles.json: {pre_hit} (R3)")

    scores["token_fidelity"] = max(0.0, round(token_score, 1))

    # 3. Structural, Typography & Wireframe Fidelity (0-5 pts — C3, C4, C6, C7, C14)
    struct_score = 5.0
    if len(secs) < 7:
        struct_score -= 2.5
        issues.append(f"Missing sections: found {len(secs)}/7")

    sec4 = secs.get(4, "")
    if "clamp(2.25rem, 4.8vw, 4.75rem)" in sec4 or "clamp(2.25rem, 4.8vw, 4.75rem)" in ui_content:
        struct_score -= 1.5
        issues.append("Boilerplate clamp(2.25rem, 4.8vw, 4.75rem) type scale found (C3/C14)")
    if re.search(r"webflow-icons|icomoon|fontawesome", sec4, re.IGNORECASE):
        struct_score -= 1.5
        issues.append("Icon font (webflow-icons/icomoon/fontawesome) listed as typography stack (C4/C14)")

    sec6 = secs.get(6, "")
    box_lines = [l for l in sec6.splitlines() if l.startswith(("┌", "│", "├", "└", "+", "|"))]
    if len(box_lines) < 8:
        struct_score -= 1.5
        issues.append("ASCII wireframe box missing or too short")
    else:
        widths = {len(l) for l in box_lines}
        if len(widths) != 1 or not all(l.endswith(("┐", "│", "┤", "┘", "+", "|")) for l in box_lines):
            struct_score -= 1.5
            issues.append(f"ASCII wireframe misaligned widths: {widths}")

    cited_classes = re.findall(r'class="([^"]+)"', sec6)
    verified_classes = sum(1 for c in cited_classes if c.split()[0] in dom_content)
    total_dom_classes = len(re.findall(r'class="([^"]+)"', dom_content))
    if verified_classes < 2 and total_dom_classes >= 2:
        struct_score -= 1.5
        issues.append(f"Few real DOM classes verified in dom.html ({verified_classes})")
    if is_v2:
        sec8 = secs.get(8, "")
        if not sec8.strip():
            struct_score -= 1.5
            issues.append("Missing Section 8 (Full-Page Scroll Journey & Subpage Inventory) in v2 blueprint")
        else:
            s8_links = re.findall(r"\((assets/[^)]+\.webp)\)", sec8)
            if not s8_links:
                struct_score -= 1.0
                issues.append("Section 8 contains zero WebP asset links")
            else:
                missing_s8 = [lnk for lnk in s8_links if not (target_path / lnk).exists() or (target_path / lnk).stat().st_size == 0]
                if missing_s8:
                    struct_score -= 1.5
                    issues.append(f"Broken Section 8 WebP links: {missing_s8[:5]}")

    scores["structural_completeness"] = max(0.0, round(struct_score, 1))

    # 4. Internal Cross-Section Consistency (0-5 pts — C1, C4, C5, C12)
    cons_score = 5.0
    sec5 = secs.get(5, "")
    sec7 = secs.get(7, "")

    sec3_hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", sec3)}
    sec7_hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", sec7)}
    if not sec7_hexes or not sec7_hexes.issubset(sec3_hexes):
        diff_h = sec7_hexes - sec3_hexes
        cons_score -= 2.0
        issues.append(f"Section 7 contains hexes not in Section 3: {diff_h}")

    sec5_beziers = set(re.findall(r"cubic-bezier\([^)]+\)", sec5))
    sec7_beziers = set(re.findall(r"cubic-bezier\([^)]+\)", sec7))
    if not sec5_beziers or not sec5_beziers.intersection(sec7_beziers):
        cons_score -= 1.5
        issues.append("Section 5 cubic-bezier curves do not match Section 7")

    sec3_var_set = set(sec3_vars)
    sec7_var_set = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", sec7))
    if len(sec3_var_set.intersection(sec7_var_set)) < 10:
        cons_score -= 1.5
        issues.append("Section 7 :root block does not declare Section 3 variables")

    if re.search(r"(?<!`)``(?!`)", sec7):
        cons_score -= 1.0
        issues.append("Double-backtick formatting glitch in Section 7 (C12)")
    scores["internal_consistency"] = max(0.0, round(cons_score, 1))

    # 5. Master Prompt Fenced Block Length & Uniqueness (0-5 pts — C10, C13)
    prompt_score = 5.0
    fenced_blocks = re.findall(r"^```markdown\n(.*?)^```", sec7, re.MULTILINE | re.DOTALL)
    fenced_lines = len(fenced_blocks[0].splitlines()) if fenced_blocks else 0
    if fenced_lines < 80:
        prompt_score -= 2.5
        issues.append(f"Fenced master prompt block is {fenced_lines} lines, must be >= 80 lines (C10)")

    my_lines = all_line_sets.get(target_name, set())
    my_words = all_word_sets.get(target_name, set())
    max_line_jacc = 0.0
    max_word_jacc = 0.0
    for other_name in all_line_sets:
        if other_name == target_name:
            continue
        ol = all_line_sets[other_name]
        ow = all_word_sets[other_name]
        lj = len(my_lines & ol) / len(my_lines | ol) if (my_lines | ol) else 0.0
        wj = len(my_words & ow) / len(my_words | ow) if (my_words | ow) else 0.0
        max_line_jacc = max(max_line_jacc, lj)
        max_word_jacc = max(max_word_jacc, wj)

    if max_line_jacc > 0.30:
        prompt_score -= 2.5
        issues.append(f"High Section 7 line Jaccard overlap ({max_line_jacc:.2f})")
    scores["prompt_generability"] = max(0.0, round(prompt_score, 1))

    total = round(sum(scores.values()), 1)
    return {
        "target": target_name,
        "scores": scores,
        "total": total,
        "fenced_lines": fenced_lines,
        "max_line_jacc": round(max_line_jacc, 2),
        "max_word_jacc": round(max_word_jacc, 2),
        "desktop_metrics": d_metrics,
        "mobile_metrics": m_metrics,
        "issues": issues,
        "warnings": warnings,
    }


def find_duplicate_png_hashes(target_list: List[str]) -> Set[str]:
    """Compute SHA256 across all desktop & mobile primary assets (WebP v2 or legacy PNG) and return duplicated hashes."""
    import hashlib
    counts: Dict[str, int] = {}
    for slug in target_list:
        assets_dir = TARGETS_DIR / slug / "assets"
        d_webp = assets_dir / "desktop-journey.webp"
        m_webp = assets_dir / "mobile-journey.webp"
        pair = (d_webp, m_webp) if d_webp.exists() else (assets_dir / "desktop.png", assets_dir / "mobile.png")
        for p in pair:
            if p.exists() and p.stat().st_size > 0:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                counts[h] = counts.get(h, 0) + 1
    return {h for h, c in counts.items() if c > 1}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate UI Prompt Blueprints (C1-C15)")
    parser.add_argument("--all", action="store_true", help="Evaluate all targets in wiki/targets/")
    parser.add_argument("--shard", type=int, choices=[1, 2, 3, 4, 5, 6], default=None, help="Evaluate one manifest shard")
    parser.add_argument(
        "--manifest",
        type=str,
        default=os.environ.get("AUDIT_MANIFEST"),
        help="Optional path to audit_manifest.json (defaults to AUDIT_MANIFEST env or enumerating wiki/targets/*)",
    )
    parser.add_argument("--slugs", type=str, default=None, help="Comma-separated target slugs")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if any target fails C1-C15")
    args = parser.parse_args()

    if args.slugs:
        raw_list = [s.strip() for s in args.slugs.split(",") if s.strip()]
        scope_label = f"Custom ({len(raw_list)} targets)"
    elif args.shard is not None:
        mpath = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST
        if mpath is not None and mpath.exists():
            mdata = json.loads(mpath.read_text(encoding="utf-8"))
            sobj = next((s for s in mdata["shards"] if int(s["shard_id"]) == args.shard), None)
            if not sobj:
                raise SystemExit(f"Shard {args.shard} not found")
            raw_list = list(sobj["slugs"])
        else:
            all_dirs = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
            per = (len(all_dirs) + 5) // 6
            raw_list = all_dirs[(args.shard - 1) * per : args.shard * per]
        scope_label = f"Shard {args.shard} ({len(raw_list)} targets)"
    elif args.all:
        raw_list = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
        scope_label = f"All {len(raw_list)} Catalog Targets"
    else:
        raw_list = list(TARGETS)
        scope_label = "Batch 1 (10 Targets)"

    blocked_targets = [t for t in raw_list if is_blocked_target(t)]
    target_list = [t for t in raw_list if t not in blocked_targets]

    print(
        f"=== Running Upgraded Live Provenance, Visual Health & Grounding Evaluation Suite (C1-C15) on "
        f"{scope_label} ({len(target_list)} analyzed, {len(blocked_targets)} status:blocked) ==="
    )

    duplicate_hashes = find_duplicate_png_hashes(target_list)
    if duplicate_hashes:
        print(f"  [!] GLOBAL C15 DUPLICATE PNG HASHES DETECTED: {len(duplicate_hashes)}")

    all_line_sets: Dict[str, Set[str]] = {}
    all_word_sets: Dict[str, Set[str]] = {}
    for t in target_list:
        bp_file = TARGETS_DIR / t / "prompt-blueprint.md"
        bp = bp_file.read_text(encoding="utf-8") if bp_file.exists() else ""
        sec7 = split_sections(bp).get(7, "")
        all_line_sets[t] = {l.strip() for l in sec7.splitlines() if len(l.strip()) > 15}
        all_word_sets[t] = set(re.findall(r"[a-z0-9_#-]{3,}", sec7.lower()))

    results = []
    perfect = 0
    warn_count = 0
    for idx, t in enumerate(target_list):
        if args.all or args.shard is not None:
            slice_start = (idx // 10) * 10
            peers = target_list[slice_start : slice_start + 10]
            peer_lines = {k: all_line_sets[k] for k in peers}
            peer_words = {k: all_word_sets[k] for k in peers}
            res = evaluate_target(t, peer_lines, peer_words, duplicate_hashes=duplicate_hashes)
        else:
            res = evaluate_target(t, all_line_sets, all_word_sets, duplicate_hashes=duplicate_hashes)
        results.append(res)
        if res["total"] == 25.0 and not res["issues"]:
            perfect += 1
        if res["warnings"]:
            warn_count += 1
        s = res["scores"]
        if not args.all or res["total"] < 25.0 or res["issues"] or args.slugs:
            dm = res["desktop_metrics"]
            mm = res["mobile_metrics"]
            print(
                f"[{res['total']:4.1f}/25.0] {t:30s} | "
                f"Assets:{s['visual_assets']:3.1f} (D:{dm.get('width',0)}x{dm.get('height',0)} std={dm.get('gray_std',0)} | M:{mm.get('width',0)}x{mm.get('height',0)} std={mm.get('gray_std',0)}) | "
                f"Tokens:{s['token_fidelity']:3.1f} | Struct:{s['structural_completeness']:3.1f} | "
                f"Consist:{s['internal_consistency']:3.1f} | Prompt:{s['prompt_generability']:3.1f} "
                f"(fence={res['fenced_lines']}L, lj={res['max_line_jacc']:.2f})"
            )
            for iss in res["issues"]:
                print(f"   ! ISSUE: {iss}")
            for w in res["warnings"]:
                print(f"   ~ WARN:  {w}")

    blocked_results = [evaluate_blocked_target(b) for b in blocked_targets]
    blocked_ok = sum(1 for br in blocked_results if br["ok"])
    for br in blocked_results:
        if not br["ok"] or args.slugs:
            status_tag = "OK" if br["ok"] else "FAIL"
            print(f"[BLOCKED {status_tag}] {br['target']:30s} | issues={len(br['issues'])}")
            for iss in br["issues"]:
                print(f"   ! BLOCKED ISSUE: {iss}")

    avg = round(sum(r["total"] for r in results) / len(results), 2) if results else 25.0
    print(
        f"\n{scope_label} Summary: {perfect}/{len(results)} analyzable targets scored 25.0/25.0 "
        f"(Avg: {avg}/25.0, Warnings: {warn_count}) | "
        f"Blocked C15 Audit: {blocked_ok}/{len(blocked_results)} clean | "
        f"Global Duplicate PNG Hashes: {len(duplicate_hashes)}"
    )

    if args.strict and (perfect < len(results) or blocked_ok < len(blocked_results) or duplicate_hashes):
        sys.exit(1)


if __name__ == "__main__":
    main()
