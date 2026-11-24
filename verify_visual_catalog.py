#!/usr/bin/env python3
"""
Dedicated Visual Catalog Verifier & 5x5 Contact-Sheet Generator.

Inspects all 575 targets (or --shard N / --slugs ...), validates live capture provenance,
PNG integrity, clamped dimensions (1440x[900..2400] desktop, 390x[844..2400] mobile),
zero synthetic specimens, zero duplicate SHA256 hashes, zero stray PNGs / body corruption
on blocked targets, and PIL/NumPy visual health metrics.

Outputs:
- Structured JSON report in .cache/recrawl_review/report_<scope>.json (or $RECRAWL_REVIEW_DIR)
- 5x5 paired Desktop+Mobile thumbnail contact sheets in .cache/recrawl_review/ for
  borderline/warn/fail captures, sampled analyzed targets, and Tier 2 WebGL captures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

REVERSE_HARNESS_DIR = Path(__file__).resolve().parent
SRC_DIR = REVERSE_HARNESS_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REVERSE_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(REVERSE_HARNESS_DIR))

from evaluate_batch import (
    DEFAULT_MANIFEST,
    RAW_DIR,
    TARGETS_DIR,
    check_behavioral_scroll_coverage,
    evaluate_blocked_target,
    is_blocked_target,
    split_frontmatter,
)
from reverse_harness.live_capture import compute_png_visual_metrics, detect_preloader_text

DEFAULT_OUTPUT_DIR = Path(
    os.environ.get("RECRAWL_REVIEW_DIR", REVERSE_HARNESS_DIR / ".cache" / "recrawl_review")
)


def _render_contact_sheets(
    items: List[Dict[str, Any]],
    out_dir: Path,
    prefix: str,
    cols: int = 5,
    rows: int = 5,
) -> List[str]:
    """Render 5x5 paired desktop+mobile thumbnail contact sheets."""
    if not items:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    per_page = cols * rows
    tile_w = 356
    tile_h = 216
    pad = 10
    desk_w, desk_h = 256, 160
    mob_w, mob_h = 74, 160
    header_h = 34

    saved_paths: List[str] = []
    font = ImageFont.load_default()

    for page_idx in range(0, len(items), per_page):
        chunk = items[page_idx : page_idx + per_page]
        actual_rows = (len(chunk) + cols - 1) // cols
        sheet_w = cols * tile_w + (cols + 1) * pad
        sheet_h = header_h + actual_rows * tile_h + (actual_rows + 1) * pad

        sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 20, 24))
        draw = ImageDraw.Draw(sheet)
        page_num = (page_idx // per_page) + 1
        total_pages = (len(items) + per_page - 1) // per_page
        draw.text(
            (pad, 10),
            f"{prefix} — Page {page_num}/{total_pages} ({len(chunk)} targets, total={len(items)})",
            fill=(235, 238, 245),
            font=font,
        )

        for idx, item in enumerate(chunk):
            r = idx // cols
            c = idx % cols
            x0 = pad + c * (tile_w + pad)
            y0 = header_h + pad + r * (tile_h + pad)

            verdict = item["verdict"]
            border_col = (46, 160, 67) if verdict == "pass" else ((210, 153, 34) if verdict == "warn" else (248, 81, 73))
            draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], fill=(28, 31, 38), outline=border_col, width=2)

            slug = item["slug"]
            assets_dir = TARGETS_DIR / slug / "assets"
            d_path = assets_dir / "desktop-journey.webp" if (assets_dir / "desktop-journey.webp").exists() else (assets_dir / "desktop.png")
            m_path = assets_dir / "mobile-journey.webp" if (assets_dir / "mobile-journey.webp").exists() else (assets_dir / "mobile.png")
            d_is_webp = d_path.suffix == ".webp"
            m_is_webp = m_path.suffix == ".webp"

            # Desktop top-fold thumbnail
            if d_path.exists():
                try:
                    with Image.open(d_path) as im:
                        w, h = im.size
                        y_start = 28 if d_is_webp else 0
                        y_end = min(h, y_start + (800 if d_is_webp else 900))
                        crop = im.crop((0, y_start, w, y_end)).convert("RGB")
                        crop = crop.resize((desk_w, desk_h), Image.Resampling.LANCZOS)
                        sheet.paste(crop, (x0 + 8, y0 + 8))
                except Exception:
                    draw.rectangle([x0 + 8, y0 + 8, x0 + 8 + desk_w, y0 + 8 + desk_h], fill=(60, 20, 20))
            else:
                draw.rectangle([x0 + 8, y0 + 8, x0 + 8 + desk_w, y0 + 8 + desk_h], fill=(45, 45, 50))

            # Mobile top-fold thumbnail
            if m_path.exists():
                try:
                    with Image.open(m_path) as im:
                        w, h = im.size
                        y_start = 28 if m_is_webp else 0
                        y_end = min(h, y_start + 844)
                        crop = im.crop((0, y_start, w, y_end)).convert("RGB")
                        crop = crop.resize((mob_w, mob_h), Image.Resampling.LANCZOS)
                        sheet.paste(crop, (x0 + 8 + desk_w + 8, y0 + 8))
                except Exception:
                    draw.rectangle([x0 + 8 + desk_w + 8, y0 + 8, x0 + 8 + desk_w + 8 + mob_w, y0 + 8 + mob_h], fill=(60, 20, 20))
            else:
                draw.rectangle([x0 + 8 + desk_w + 8, y0 + 8, x0 + 8 + desk_w + 8 + mob_w, y0 + 8 + mob_h], fill=(45, 45, 50))

            dm = item.get("desktop_metrics") or {}
            mm = item.get("mobile_metrics") or {}
            line1 = f"[{verdict.upper()}] {slug[:28]} ({item.get('capture_tier', '?')})"
            line2 = (
                f"D:{dm.get('width',0)}x{dm.get('height',0)} std={dm.get('gray_std',0):.1f} q16={dm.get('dominant_q16_pct',0):.0f}% | "
                f"M:{mm.get('width',0)}x{mm.get('height',0)} std={mm.get('gray_std',0):.1f}"
            )
            line3 = (item["reasons"][0][:52] if item.get("reasons") else f"edge={dm.get('edge_density',0):.4f} luma={dm.get('modal_luma',0):.0f}")
            draw.text((x0 + 8, y0 + 172), line1, fill=border_col if verdict != "pass" else (240, 245, 250), font=font)
            draw.text((x0 + 8, y0 + 186), line2, fill=(195, 202, 214), font=font)
            draw.text((x0 + 8, y0 + 200), line3, fill=(165, 172, 184), font=font)

        out_path = out_dir / f"{prefix}_p{page_num}.png"
        sheet.save(out_path, format="PNG", optimize=True)
        saved_paths.append(str(out_path))

    return saved_paths


def verify_catalog(
    slugs: List[str],
    scope_name: str,
    output_dir: Path,
    sample_size: int = 25,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pass 1: hash all existing analyzed primary assets (WebP v2 or legacy PNG) to catch global duplicates
    hash_owners: Dict[str, List[str]] = {}
    for slug in slugs:
        if is_blocked_target(slug):
            continue
        adir = TARGETS_DIR / slug / "assets"
        pair = ("desktop-journey.webp", "mobile-journey.webp") if (adir / "desktop-journey.webp").exists() else ("desktop.png", "mobile.png")
        for kind in pair:
            p = adir / kind
            if p.exists() and p.stat().st_size > 0:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                hash_owners.setdefault(h, []).append(f"{slug}/{kind}")
    dup_hashes = {h: owners for h, owners in hash_owners.items() if len(owners) > 1}

    records: List[Dict[str, Any]] = []
    counts = {
        "total": len(slugs),
        "analyzed": 0,
        "blocked": 0,
        "pass": 0,
        "warn": 0,
        "fail": 0,
        "blocked_ok": 0,
        "blocked_fail": 0,
        "specimens_found": 0,
        "non_live_analyzed": 0,
        "extreme_height_gt_2400": 0,
        "corrupt_or_missing_png": 0,
        "duplicate_png_hashes": len(dup_hashes),
        "tiers": {},
    }

    for slug in slugs:
        tdir = TARGETS_DIR / slug
        bp_path = tdir / "prompt-blueprint.md"
        meta_path = RAW_DIR / slug / "metadata.json"
        comp_path = RAW_DIR / slug / "computed_styles.json"

        if is_blocked_target(slug):
            counts["blocked"] += 1
            br = evaluate_blocked_target(slug)
            verdict = "blocked_ok" if br["ok"] else "blocked_fail"
            counts[verdict] += 1
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            records.append({
                "slug": slug,
                "status": "blocked",
                "verdict": verdict,
                "http_status": meta.get("http_status"),
                "block_reason": meta.get("block_reason"),
                "reasons": br["issues"],
            })
            continue

        counts["analyzed"] += 1
        bp_txt = bp_path.read_text(encoding="utf-8", errors="ignore") if bp_path.exists() else ""
        fm, _ = split_frontmatter(bp_txt)
        cap_m = re.search(r"^capture:\s*(\S+)", fm, re.MULTILINE)
        cap_d_m = re.search(r"^capture_desktop:\s*(\S+)", fm, re.MULTILINE)
        cap_m_m = re.search(r"^capture_mobile:\s*(\S+)", fm, re.MULTILINE)
        tier_m = re.search(r"^capture_tier:\s*(\S+)", fm, re.MULTILINE)

        cap_val = cap_m.group(1) if cap_m else "missing"
        cap_d = cap_d_m.group(1) if cap_d_m else "missing"
        cap_m_v = cap_m_m.group(1) if cap_m_m else "missing"
        tier_val = tier_m.group(1) if tier_m else "unknown"
        counts["tiers"][tier_val] = counts["tiers"].get(tier_val, 0) + 1

        fail_reasons: List[str] = []
        warn_reasons: List[str] = []

        if "specimen" in (cap_val, cap_d, cap_m_v) or "Design Specimen" in bp_txt:
            counts["specimens_found"] += 1
            fail_reasons.append(f"Synthetic specimen detected (capture={cap_val})")
        if not (cap_val == "live" and cap_d == "live" and cap_m_v == "live"):
            counts["non_live_analyzed"] += 1
            fail_reasons.append(f"Non-live provenance (capture={cap_val}, d={cap_d}, m={cap_m_v})")

        is_v2 = bool(re.search(r"^capture_mode:\s*behavioral_journey_v2\b", fm, re.MULTILINE))
        adir = tdir / "assets"
        if is_v2:
            d_metrics = compute_png_visual_metrics(adir / "desktop-journey.webp", top_fold_height=828)
            m_metrics = compute_png_visual_metrics(adir / "mobile-journey.webp", top_fold_height=872)
            leftover_pngs = [p.name for p in adir.glob("*.png")] if adir.exists() else []
            if leftover_pngs:
                fail_reasons.append(f"Leftover legacy PNG(s) in v2 target: {leftover_pngs}")
            beh_path = RAW_DIR / slug / "behavior.json"
            if beh_path.exists():
                try:
                    beh_data = json.loads(beh_path.read_text(encoding="utf-8", errors="ignore"))
                    cov_warn = check_behavioral_scroll_coverage(beh_data)
                    if cov_warn:
                        warn_reasons.append(cov_warn)
                except Exception:
                    pass
        else:
            d_metrics = compute_png_visual_metrics(adir / "desktop.png", top_fold_height=900)
            m_metrics = compute_png_visual_metrics(adir / "mobile.png", top_fold_height=844)

        if not d_metrics.get("valid"):
            counts["corrupt_or_missing_png"] += 1
            fail_reasons.append("Desktop visual asset missing or corrupt")
        else:
            if is_v2:
                if d_metrics["width"] != 1280 or not (828 <= d_metrics["height"] < 16383):
                    fail_reasons.append(f"Desktop WebP journey dims out of bounds: {d_metrics['width']}x{d_metrics['height']}")
            else:
                if d_metrics["height"] > 2400:
                    counts["extreme_height_gt_2400"] += 1
                if d_metrics["width"] != 1440 or not (900 <= d_metrics["height"] <= 2400):
                    fail_reasons.append(f"Desktop dims out of bounds: {d_metrics['width']}x{d_metrics['height']}")
            if d_metrics["gray_std"] < 8.0 and d_metrics["edge_density"] < 0.003:
                fail_reasons.append(f"Desktop solid blank (std={d_metrics['gray_std']}, edge={d_metrics['edge_density']})")
            elif not is_v2 and (d_metrics["dominant_q16_pct"] > 85.0 or d_metrics["gray_std"] < 18.0 or d_metrics["top_fold_dominant_q16_pct"] > 92.0):
                warn_reasons.append(
                    f"Desktop borderline low-entropy (std={d_metrics['gray_std']}, q16={d_metrics['dominant_q16_pct']}%, tf_q16={d_metrics['top_fold_dominant_q16_pct']}%)"
                )
            if not is_v2 and d_metrics.get("has_blank_tail"):
                warn_reasons.append(
                    f"Desktop blank bottom tail (tail_std={d_metrics.get('tail_gray_std')}, tail_edge={d_metrics.get('tail_edge_density')})"
                )
            if not is_v2 and d_metrics.get("has_mid_blank_void"):
                warn_reasons.append(
                    f"Desktop mid-page solid void ({d_metrics.get('max_blank_void_px')}px at y={d_metrics.get('mid_blank_void_range')})"
                )
            if d_metrics.get("sha256") in dup_hashes:
                fail_reasons.append(f"Duplicate desktop SHA256 shared with {dup_hashes[d_metrics['sha256']]}")

        if not m_metrics.get("valid"):
            counts["corrupt_or_missing_png"] += 1
            fail_reasons.append("Mobile visual asset missing or corrupt")
        else:
            if is_v2:
                if m_metrics["width"] != 390 or not (872 <= m_metrics["height"] < 16383):
                    fail_reasons.append(f"Mobile WebP journey dims out of bounds: {m_metrics['width']}x{m_metrics['height']}")
            else:
                if m_metrics["height"] > 2400:
                    counts["extreme_height_gt_2400"] += 1
                if m_metrics["width"] != 390 or not (844 <= m_metrics["height"] <= 2400):
                    fail_reasons.append(f"Mobile dims out of bounds: {m_metrics['width']}x{m_metrics['height']}")
            if m_metrics["gray_std"] < 8.0 and m_metrics["edge_density"] < 0.003:
                fail_reasons.append(f"Mobile solid blank (std={m_metrics['gray_std']}, edge={m_metrics['edge_density']})")
            elif not is_v2 and (m_metrics["dominant_q16_pct"] > 85.0 or m_metrics["gray_std"] < 18.0 or m_metrics["top_fold_dominant_q16_pct"] > 92.0):
                warn_reasons.append(
                    f"Mobile borderline low-entropy (std={m_metrics['gray_std']}, q16={m_metrics['dominant_q16_pct']}%)"
                )
            if not is_v2 and m_metrics.get("has_blank_tail"):
                warn_reasons.append(
                    f"Mobile blank bottom tail (tail_std={m_metrics.get('tail_gray_std')}, tail_edge={m_metrics.get('tail_edge_density')})"
                )
            if not is_v2 and m_metrics.get("has_mid_blank_void"):
                warn_reasons.append(
                    f"Mobile mid-page solid void ({m_metrics.get('max_blank_void_px')}px at y={m_metrics.get('mid_blank_void_range')})"
                )
            if m_metrics.get("sha256") in dup_hashes:
                fail_reasons.append(f"Duplicate mobile SHA256 shared with {dup_hashes[m_metrics['sha256']]}")

        if not meta_path.exists():
            fail_reasons.append("Missing raw/<slug>/metadata.json")
        if not comp_path.exists():
            fail_reasons.append("Missing raw/<slug>/computed_styles.json")
        else:
            try:
                cdata = json.loads(comp_path.read_text(encoding="utf-8"))
                pre_hit = detect_preloader_text(
                    str(cdata.get("visibleTextSample") or ""),
                    (cdata.get("domMetrics") or {}).get("visibleTextLen"),
                )
                if pre_hit:
                    warn_reasons.append(f"Preloader text detected: {pre_hit}")
            except Exception:
                pass

        if fail_reasons:
            verdict = "fail"
        elif warn_reasons:
            verdict = "warn"
        else:
            verdict = "pass"
        counts[verdict] += 1

        records.append({
            "slug": slug,
            "status": "analyzed",
            "verdict": verdict,
            "capture": cap_val,
            "capture_tier": tier_val,
            "desktop_metrics": d_metrics,
            "mobile_metrics": m_metrics,
            "reasons": fail_reasons + warn_reasons,
        })

    # Generate 5x5 contact sheets
    analyzed_recs = [r for r in records if r["status"] == "analyzed"]
    borderline_recs = [r for r in analyzed_recs if r["verdict"] in ("fail", "warn")]
    webgl_recs = [r for r in analyzed_recs if r.get("capture_tier") == "tier2_webgl"]

    if len(analyzed_recs) <= sample_size:
        sample_recs = analyzed_recs
    else:
        step = max(1, len(analyzed_recs) // sample_size)
        sample_recs = analyzed_recs[::step][:sample_size]

    sheets = {
        "borderline": _render_contact_sheets(borderline_recs, output_dir, f"contact_borderline_{scope_name}"),
        "sample": _render_contact_sheets(sample_recs, output_dir, f"contact_sample_{scope_name}"),
        "webgl": _render_contact_sheets(webgl_recs, output_dir, f"contact_webgl_{scope_name}"),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_name,
        "summary": counts,
        "duplicate_hashes": dup_hashes,
        "contact_sheets": sheets,
        "records": records,
    }
    report_path = output_dir / f"report_{scope_name}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"=== Visual Catalog Verification ({scope_name}) ===")
    print(
        f"Total: {counts['total']} | Analyzed: {counts['analyzed']} "
        f"(pass={counts['pass']}, warn={counts['warn']}, fail={counts['fail']}) | "
        f"Blocked: {counts['blocked']} (ok={counts['blocked_ok']}, fail={counts['blocked_fail']})"
    )
    print(
        f"Specimens: {counts['specimens_found']} | Non-live: {counts['non_live_analyzed']} | "
        f"Extreme (>2400px): {counts['extreme_height_gt_2400']} | Corrupt/Missing PNGs: {counts['corrupt_or_missing_png']} | "
        f"Duplicate Hashes: {counts['duplicate_png_hashes']}"
    )
    print(f"Tiers: {counts['tiers']}")
    print(f"JSON Report: {report_path}")
    for k, paths in sheets.items():
        if paths:
            print(f"Contact Sheet ({k}): {', '.join(paths)}")
    for r in records:
        if r["verdict"] in ("fail", "blocked_fail"):
            print(f"  [FAIL] {r['slug']}: {r['reasons']}")
        elif r["verdict"] == "warn":
            print(f"  [WARN] {r['slug']}: {r['reasons']}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify visual catalog PNGs & generate 5x5 contact sheets")
    parser.add_argument("--all", action="store_true", help="Verify all targets in wiki/targets/")
    parser.add_argument("--shard", type=int, choices=[1, 2, 3, 4, 5, 6], default=None, help="Verify one shard from manifest")
    parser.add_argument("--slugs", type=str, default=None, help="Comma-separated slugs")
    parser.add_argument(
        "--manifest",
        type=str,
        default=os.environ.get("AUDIT_MANIFEST"),
        help="Optional path to audit_manifest.json (defaults to AUDIT_MANIFEST env or enumerating wiki/targets/*)",
    )
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON report and contact sheets")
    parser.add_argument("--sample-size", type=int, default=25, help="Sample size for random/evenly-spaced contact sheet")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if any fail or blocked_fail found")
    args = parser.parse_args()

    if args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        scope = "slugs"
    elif args.shard is not None:
        mpath = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST
        if mpath is not None and mpath.exists():
            mdata = json.loads(mpath.read_text(encoding="utf-8"))
            sobj = next((s for s in mdata["shards"] if int(s["shard_id"]) == args.shard), None)
            if not sobj:
                raise SystemExit(f"Shard {args.shard} not found in manifest")
            slugs = list(sobj["slugs"])
        else:
            all_dirs = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
            per = (len(all_dirs) + 5) // 6
            slugs = all_dirs[(args.shard - 1) * per : args.shard * per]
        scope = f"shard_{args.shard}"
    elif args.all:
        slugs = sorted([p.name for p in TARGETS_DIR.iterdir() if p.is_dir()])
        scope = "all"
    else:
        raise SystemExit("Specify one of --all, --shard N, or --slugs a,b,c")

    report = verify_catalog(slugs, scope, Path(args.output_dir), sample_size=args.sample_size)
    s = report["summary"]
    if args.strict and (s["fail"] > 0 or s["blocked_fail"] > 0 or s["duplicate_png_hashes"] > 0):
        sys.exit(1)


if __name__ == "__main__":
    main()
