#!/usr/bin/env python3
"""
Rigorous Semantic & Structural Evaluation Suite for UI Prompt Designer Blueprints.
Upgraded per claude-loop C9, C10, C11, C12, and C13 to verify:
1. Visual Assets & Dimension Verification (0-5 pts) — Desktop exactly 1440px wide full-page, Mobile 390px wide full-page (C11)
2. Token Hygiene & Ground-Truth Fidelity (0-5 pts) — Zero parser debris, zero duplicates, source-grounded tokens (C2, C3)
3. Structural & Wireframe Box Alignment (0-5 pts) — 7 sections, 74-char aligned ASCII wireframe, real DOM classes (C6, C7)
4. Internal Cross-Section Consistency (0-5 pts) — Section 7 hexes/vars/beziers match Sections 3-5, zero double-backtick glitches (C1, C4, C5, C12)
5. Master Prompt Generability & Uniqueness (0-5 pts) — Single unsplit fenced Master Prompt >= 80 lines (C10), plus documented line-level and word-level Jaccard uniqueness (C13)

Jaccard Tokenization Documentation (C13):
- `line_jaccard`: Pairwise Jaccard similarity |A ∩ B| / |A ∪ B| where each target's set consists of stripped non-empty lines (>15 chars) inside Section 7. Measures line-by-line template cloning (threshold < 0.30).
- `word_jaccard`: Pairwise Jaccard similarity |W_A ∩ W_B| / |W_A ∪ W_B| where each target's set consists of unique lowercased alphanumeric/CSS tokens (`re.findall(r'[a-z0-9_#-]{3,}', sec7.lower())`) inside Section 7. Naturally higher (~0.35-0.42) because all prompts share English/CSS/Markdown vocabulary (`section`, `header`, `font-weight`, `transition`, etc.).
"""

import re
import struct
from pathlib import Path
from typing import Dict

TARGETS = [
    'dennypratama-com', 'agnestoth-com', 'www-sigma-imaging-se', 'morrow-valerian-studio',
    'wiro-ae', 'www-anduril-com', 'www-avilus-com', 'www-eladiodieste-com',
    'www-unimaticwatches-com', 'seedjewelry-com'
]

FLEET_DIR = Path('/root/projects/fleet')
TARGETS_DIR = FLEET_DIR / 'wiki' / 'targets'
RAW_DIR = Path('/root/projects/reverse-harness/raw')

def png_dims(path: Path):
    if not path.exists():
        return (0, 0)
    with open(path, 'rb') as f:
        data = f.read(24)
        if data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
            return struct.unpack('>II', data[16:24])
    return (0, 0)

def split_sections(bp_content: str) -> Dict[int, str]:
    sections = {}
    matches = list(re.finditer(r'^## (\d+)\.\s+.*$', bp_content, re.MULTILINE))
    for i, m in enumerate(matches):
        sec_num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bp_content)
        sections[sec_num] = bp_content[start:end]
    return sections

def evaluate_target(target_name: str, all_line_sets: Dict[str, set], all_word_sets: Dict[str, set]) -> Dict:
    target_path = TARGETS_DIR / target_name
    bp_path = target_path / 'prompt-blueprint.md'
    ui_ux_path = target_path / 'ui-ux.md'
    dom_path = RAW_DIR / target_name / 'dom.html'
    desktop_png = target_path / 'assets' / 'desktop.png'
    mobile_png = target_path / 'assets' / 'mobile.png'

    scores = {}
    issues = []

    bp_content = bp_path.read_text(encoding='utf-8') if bp_path.exists() else ''
    ui_content = ui_ux_path.read_text(encoding='utf-8') if ui_ux_path.exists() else ''
    dom_content = dom_path.read_text(encoding='utf-8', errors='ignore') if dom_path.exists() else ''
    secs = split_sections(bp_content)

    # 1. Visual Assets & Dimension Verification (0-5) — enforces C11
    d_w, d_h = png_dims(desktop_png)
    m_w, m_h = png_dims(mobile_png)
    asset_score = 0.0
    if d_w == 1440 and d_h >= 1200:
        asset_score += 2.0
    else:
        issues.append(f"Desktop PNG must be 1440px wide full-page, got {d_w}x{d_h}")
    if m_w == 390 and m_h >= 1200:
        asset_score += 2.0
    else:
        issues.append(f"Mobile PNG must be 390px wide full-page, got {m_w}x{m_h}")
    if f"{d_w}x{d_h}" in bp_content and f"{m_w}x{m_h}" in bp_content:
        asset_score += 1.0
    else:
        issues.append(f"Exact PNG dimensions ({d_w}x{d_h}, {m_w}x{m_h}) not documented in blueprint")
    scores['visual_assets'] = asset_score

    # 2. Token Hygiene & Ground-Truth Fidelity (0-5) — catches C2, C3
    token_score = 5.0
    sec3 = secs.get(3, '')
    table_lines = [l for l in (sec3 + '\n' + ui_content).splitlines() if l.strip().startswith('|') and '---' not in l]
    for tl in table_lines:
        if '{' in tl or '}' in tl or '@media' in tl or '@property' in tl:
            token_score -= 2.0
            issues.append(f"Parser debris in table row: {tl[:60]}")
            break
    if 'Extracted CSS Variable' in sec3 or 'Extracted CSS Variable' in ui_content:
        token_score -= 1.5
        issues.append("Placeholder role 'Extracted CSS Variable' found")

    sec3_vars = re.findall(r'^\|\s*`(--[a-zA-Z0-9_-]+)`\s*\|', sec3, re.MULTILINE)
    if len(sec3_vars) != len(set(sec3_vars)):
        token_score -= 1.5
        issues.append("Duplicate CSS variable names in Section 3")
    if len(sec3_vars) < 12:
        token_score -= 1.5
        issues.append(f"Too few CSS variables in Section 3 ({len(sec3_vars)})")

    ui_vars = set(re.findall(r'^\|\s*`(--[a-zA-Z0-9_-]+)`\s*\|', ui_content, re.MULTILINE))
    if not set(sec3_vars).issubset(ui_vars):
        token_score -= 1.0
        issues.append("Section 3 variables do not match ui-ux.md")
    scores['token_fidelity'] = max(0.0, round(token_score, 1))

    # 3. Structural & Wireframe Box Alignment + Real DOM Landmarks (0-5) — catches C6, C7
    struct_score = 5.0
    if len(secs) < 7:
        struct_score -= 2.5
        issues.append(f"Missing sections: found {len(secs)}/7")

    sec6 = secs.get(6, '')
    box_lines = [l for l in sec6.splitlines() if l.startswith(('┌', '│', '├', '└'))]
    if len(box_lines) < 8:
        struct_score -= 1.5
        issues.append("ASCII wireframe box missing or too short")
    else:
        widths = {len(l) for l in box_lines}
        if len(widths) != 1 or not all(l.endswith(('┐', '│', '┤', '┘')) for l in box_lines):
            struct_score -= 1.5
            issues.append(f"ASCII wireframe misaligned widths: {widths}")

    cited_classes = re.findall(r'class="([^"]+)"', sec6)
    verified_classes = sum(1 for c in cited_classes if c.split()[0] in dom_content)
    if verified_classes < 2:
        struct_score -= 1.5
        issues.append(f"Few real DOM classes verified in dom.html ({verified_classes})")
    scores['structural_completeness'] = max(0.0, round(struct_score, 1))

    # 4. Internal Cross-Section Consistency (0-5) — catches C1, C4, C5, C12
    cons_score = 5.0
    sec5 = secs.get(5, '')
    sec7 = secs.get(7, '')

    sec3_hexes = {h.lower() for h in re.findall(r'#[0-9a-fA-F]{3,8}\b', sec3)}
    sec7_hexes = {h.lower() for h in re.findall(r'#[0-9a-fA-F]{3,8}\b', sec7)}
    if not sec7_hexes or not sec7_hexes.issubset(sec3_hexes):
        diff_h = sec7_hexes - sec3_hexes
        cons_score -= 2.0
        issues.append(f"Section 7 contains hexes not in Section 3: {diff_h}")

    sec5_beziers = set(re.findall(r'cubic-bezier\([^)]+\)', sec5))
    sec7_beziers = set(re.findall(r'cubic-bezier\([^)]+\)', sec7))
    if not sec5_beziers or not sec5_beziers.intersection(sec7_beziers):
        cons_score -= 1.5
        issues.append("Section 5 cubic-bezier curves do not match Section 7")

    sec3_var_set = set(sec3_vars)
    sec7_var_set = set(re.findall(r'(--[a-zA-Z0-9_-]+)\s*:', sec7))
    if len(sec3_var_set.intersection(sec7_var_set)) < 10:
        cons_score -= 1.5
        issues.append("Section 7 :root block does not declare Section 3 variables")

    if re.search(r'(?<!`)``(?!`)', sec7):
        cons_score -= 1.0
        issues.append("Double-backtick formatting glitch in Section 7 (C12)")
    scores['internal_consistency'] = max(0.0, round(cons_score, 1))

    # 5. Master Prompt Fenced Block Length & Uniqueness (0-5) — enforces C10 & C13
    prompt_score = 5.0
    fenced_blocks = re.findall(r'^```markdown\n(.*?)^```', sec7, re.MULTILINE | re.DOTALL)
    fenced_lines = len(fenced_blocks[0].splitlines()) if fenced_blocks else 0
    if fenced_lines < 80:
        prompt_score -= 2.5
        issues.append(f"Fenced master prompt block is {fenced_lines} lines, must be >= 80 lines (C10)")

    my_lines = all_line_sets[target_name]
    my_words = all_word_sets[target_name]
    max_line_jacc = 0.0
    max_word_jacc = 0.0
    for other_name in TARGETS:
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
    scores['prompt_generability'] = max(0.0, round(prompt_score, 1))

    total = round(sum(scores.values()), 1)
    return {
        'target': target_name,
        'scores': scores,
        'total': total,
        'fenced_lines': fenced_lines,
        'max_line_jacc': round(max_line_jacc, 2),
        'max_word_jacc': round(max_word_jacc, 2),
        'issues': issues
    }

def main():
    print("=== Running Upgraded Truth, Fence-Length & Dimension Evaluation Suite (C1-C13) ===")
    all_line_sets = {}
    all_word_sets = {}
    for t in TARGETS:
        bp = (TARGETS_DIR / t / 'prompt-blueprint.md').read_text(encoding='utf-8')
        sec7 = split_sections(bp).get(7, '')
        all_line_sets[t] = {l.strip() for l in sec7.splitlines() if len(l.strip()) > 15}
        all_word_sets[t] = set(re.findall(r'[a-z0-9_#-]{3,}', sec7.lower()))

    results = []
    for t in TARGETS:
        res = evaluate_target(t, all_line_sets, all_word_sets)
        results.append(res)
        s = res['scores']
        print(
            f"[{res['total']:4.1f}/25.0] {t:24s} | "
            f"Assets:{s['visual_assets']:3.1f} | Tokens:{s['token_fidelity']:3.1f} | "
            f"Struct:{s['structural_completeness']:3.1f} | Consist:{s['internal_consistency']:3.1f} | "
            f"Prompt:{s['prompt_generability']:3.1f} (fence={res['fenced_lines']}L, line_j={res['max_line_jacc']:.2f}, word_j={res['max_word_jacc']:.2f})"
        )
        for iss in res['issues']:
            print(f"   ! ISSUE: {iss}")

    avg = round(sum(r['total'] for r in results) / len(results), 1)
    print(f"\nBatch 1 Average Score: {avg} / 25.0")

if __name__ == '__main__':
    main()
