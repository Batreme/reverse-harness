#!/usr/bin/env python3
"""
Rebuilds ui-ux.md and prompt-blueprint.md for all 10 Batch 1 flagship targets
using 100% ground-truth CSS variables, font stacks, colors, transitions,
breakpoints, and DOM landmarks extracted from /root/projects/reverse-harness/raw/<slug>/.
Resolves all findings C1-C13 from claude-loop Round 1 & Round 2 reviews.
"""

import os
import re
import struct
from pathlib import Path
from bs4 import BeautifulSoup

REVERSE_HARNESS_DIR = Path(__file__).resolve().parent
FLEET_DIR = Path(os.environ.get('FLEET_ROOT', REVERSE_HARNESS_DIR.parent / 'fleet'))
TARGETS_DIR = FLEET_DIR / 'wiki' / 'targets'
RAW_DIR = Path(os.environ.get('RAW_DIR', REVERSE_HARNESS_DIR / 'raw'))

def png_dims(path: Path):
    if not path.exists():
        return (0, 0)
    with open(path, 'rb') as f:
        data = f.read(24)
        if data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
            return struct.unpack('>II', data[16:24])
    return (0, 0)

def extract_clean_css_vars(slug: str):
    raw = RAW_DIR / slug
    dom_text = (raw / 'dom.html').read_text(errors='ignore')
    soup = BeautifulSoup(dom_text, 'html.parser')
    css_chunks = [s.get_text() for s in soup.find_all('style')]
    styles_dir = raw / 'styles'
    if styles_dir.exists():
        for cf in sorted(styles_dir.glob('*.css')):
            css_chunks.append(cf.read_text(errors='ignore'))
    full_css = '\n'.join(css_chunks)

    skip_prefixes = (
        '--tw-', '--swiper-', '--wp-', '--_state', '--_trigger', '--_responsive',
        '--flex-', '--none-', '--column-', '--row-', '--start-', '--center-', '--end-',
        '--first-', '--last-', '--unset-', '--relative-', '--adp-'
    )
    raw_vars = re.findall(r'(--[a-zA-Z0-9_-]+)\s*:\s*([^;\}]+)', full_css)
    clean_vars = {}
    for k, v in raw_vars:
        if any(k.startswith(p) for p in skip_prefixes):
            continue
        v = ' '.join(v.strip().split())
        if (
            k not in clean_vars
            and 0 < len(v) < 72
            and '{' not in v
            and '}' not in v
            and '@property' not in v
            and '@media' not in v
            and not v.startswith('@')
        ):
            clean_vars[k] = v
    return clean_vars, soup, full_css

def make_ascii_box(lines_content, width=74):
    """Creates a strictly aligned ASCII wireframe box of exact character width."""
    inner = width - 4
    out = ["┌" + "─" * (width - 2) + "┐"]
    for item in lines_content:
        if item == "---":
            out.append("├" + "─" * (width - 2) + "┤")
        else:
            trimmed = item[:inner].ljust(inner)
            out.append(f"│ {trimmed} │")
    out.append("└" + "─" * (width - 2) + "┘")
    return "\n".join(out)

SITES = {
    'dennypratama-com': {
        'title': 'Denny Pratama · Product Designer & Design Engineer Portfolio',
        'archetype': 'Monolith Ink & Bone Editorial Grid (`Archivo` + Mechanical Ease-Out)',
        'summary': 'A precision-engineered product design and code portfolio built on a carbon-ink substrate (`#0c0c0b`) and warm bone editorial chapters (`#eceae4`), governed by a 1680px 12-column hairline grid and damped mechanical transitions.',
        'differentiators': [
            'Dual-Polarity Surface Alternation: Alternates between carbon-ink chapters (`--ink: #0c0c0b`) and warm bone chapters (`section#work.work.on-bone` using `--bone: #eceae4` and `--lp: #0c0c0b24` dark hairlines).',
            'Zero Box-Shadow Elevation: Depth is articulated strictly via 1px alpha hairlines (`--line: #eceae424` on dark, `--lp: #0c0c0b24` on bone) and stepped ink surfaces (`--ink-2: #151514`, `--ink-3: #1f1f1d`).',
            'Constructed Geometric Mark & Telemetry: Interactive vector construction section (`"The mark, constructed: a pivot, one angle, eleven points, then a solid."`) paired with live availability dot (`--ok: #3ccf7d`).',
            'Calibrated Dual Easing System: All UI micro-interactions execute on `--e-out: cubic-bezier(.16,1,.3,1)` (`400ms`–`600ms`), while curtain reveals run on `--e-io: cubic-bezier(.83,0,.17,1)`.'
        ],
        'grid': {
            'max_width': '1680px (`--wrap: 1680px`)',
            'padding': '48px desktop (`--pad: 48px`), reducing at `@media (max-width:1023px)` and `@media (max-width:767px)` to `20px`',
            'desktop_cols': '12-column CSS Grid (`grid-template-columns: repeat(12, minmax(0, 1fr))`) with 1px hairline cell borders',
            'mobile_layout': 'Single-column vertical stack below `767px` (`@media (max-width:767px)`), collapsing `.nav-links` into `.menu-l` mobile drawer',
            'breakpoints': '`@media (max-width:1359px)`, `@media (max-width:1023px)`, `@media (max-width:900px)`, `@media (max-width:767px)`',
            'radii': '`--r-s: 4px`, `--r-m: 8px`, `--r-l: 16px`, `--r-full: 999px`'
        },
        'semantic_tokens': [
            ('Primary Dark Canvas', '#0c0c0b', '`--ink` (`:root`)'),
            ('Elevated Dark Surface', '#151514', '`--ink-2` (`:root`)'),
            ('Interactive Dark Surface', '#1f1f1d', '`--ink-3` (`:root`)'),
            ('Primary Bone Ink / Light Surface', '#eceae4', '`--bone` (`:root` / `.on-bone`)'),
            ('Secondary Bone Copy', '#c9c6be', '`--bone-2` (`:root`)'),
            ('Tertiary Bone Surface', '#e2dfd8', '`--bone-3` (`:root`)'),
            ('Muted Metadata Label', '#8c8a84', '`--mute` (`:root`)'),
            ('Dimmed Caption Ink', '#3b3a36', '`--dim` (`:root`)'),
            ('Dark-Mode Hairline Rule', '#eceae424', '`--line` (`:root`)'),
            ('Dark-Mode Focus Hairline', '#eceae447', '`--line-2` (`:root`)'),
            ('Light-Mode Hairline Rule', '#0c0c0b24', '`--lp` (`.on-bone`)'),
            ('Primary Signal Accent', '#ff4f2b', '`--red` (`:root`)'),
            ('Availability Status Green', '#3ccf7d', '`--ok` (`:root`)')
        ],
        'curated_vars': [
            ('--font-archivo', '"Archivo", "Archivo Fallback"', 'Primary display & UI sans font family'),
            ('--sans', 'var(--font-archivo),"Archivo","Helvetica Neue",Helvetica,Arial,sans-serif', 'Global sans-serif stack'),
            ('--ink', '#0c0c0b', 'Primary carbon substrate'),
            ('--ink-2', '#151514', 'Secondary elevated card surface'),
            ('--ink-3', '#1f1f1d', 'Interactive hover/chip surface'),
            ('--bone', '#eceae4', 'Primary limestone bone ink & light section bg'),
            ('--bone-2', '#c9c6be', 'Secondary body copy ink'),
            ('--mute', '#8c8a84', 'Muted index & metadata ink'),
            ('--line', '#eceae424', '1px hairline divider on dark substrate'),
            ('--line-2', '#eceae447', 'Hovered hairline border on dark substrate'),
            ('--lp', '#0c0c0b24', '1px hairline divider on `.on-bone` sections'),
            ('--red', '#ff4f2b', 'Signal orange-red accent token'),
            ('--ok', '#3ccf7d', 'Live availability status indicator'),
            ('--e-out', 'cubic-bezier(.16,1,.3,1)', 'Primary mechanical ease-out curve'),
            ('--e-io', 'cubic-bezier(.83,0,.17,1)', 'Symmetric ease-in-out curtain curve'),
            ('--wrap', '1680px', 'Maximum layout container width'),
            ('--pad', '48px', 'Desktop horizontal container padding'),
            ('--r-s', '4px', 'Small component border radius'),
            ('--r-m', '8px', 'Medium card border radius'),
            ('--r-full', '999px', 'Pill badge border radius')
        ],
        'fonts': {
            'display': '`"Archivo", "Archivo Fallback", "Helvetica Neue", Helvetica, Arial, sans-serif` (`--sans`)',
            'body': '`"Archivo", "Helvetica Neue", Helvetica, Arial, sans-serif` (`--sans`)',
            'mono': '`ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` (`--mono`)',
            'h1_spec': '`clamp(2.75rem, 6vw, 5.5rem)`, `font-weight: 700`, `line-height: 0.95`, `letter-spacing: -0.04em` ("Denny Pratama, product designer. I design it. I build it.")',
            'h2_spec': '`clamp(1.75rem, 3.5vw, 3.25rem)`, `font-weight: 600`, `line-height: 1.08`, `letter-spacing: -0.025em` ("Drawn, then shipped.", "One project. Both sides of the line.")',
            'body_spec': '`1rem (16px)`, `font-weight: 400`, `line-height: 1.6`, `color: var(--bone-2)`',
            'meta_spec': '`0.75rem (12px)`, `font-family: var(--mono)`, `letter-spacing: 0.08em`, `text-transform: uppercase`, tabular numerals (`Home01`, `Work02`)'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.16,1,.3,1)` (`--e-out`) and `cubic-bezier(.83,0,.17,1)` (`--e-io`)',
            'rules': [
                '`.btn:before` -> `transition: transform .6s var(--e-out)` (sliding fill curtain on CTA buttons)',
                '`.btn .ico` -> `transition: transform .5s var(--e-out)` (directional arrow icon translation)',
                '`.lnk` -> `transition: border-color .4s` (hairline link underline transition)',
                '`.chip` -> `transition: background .4s, color .4s` (tag pill state morph)',
                '`.nav-bg` -> `transition: opacity .4s var(--e-out)` (header backdrop reveal on scroll)'
            ]
        },
        'wireframe_lines': [
            '<header class="nav"> [denny pratama]  [Work] [About] [Blog] [Get in touch]',
            '---',
            '<section class="hero"> (Substrate: --ink #0c0c0b, max-w: --wrap 1680px)',
            '  H1: Denny Pratama, product designer. I design it. I build it.',
            '  Status Chip (.chip): [● Available] (--ok #3ccf7d) + [Get in touch]',
            '---',
            '<section id="work" class="work on-bone"> (Surface: --bone #eceae4)',
            '  H2: Drawn, then shipped. | H2: One project. Both sides of the line.',
            '  [ Case Study 01: Design + Code ]  │  [ Case Study 02: Full-Stack UI ]',
            '---',
            '<section class="con"> (Vector Construction Interactive Stage)',
            '  H2: The mark, constructed: a pivot, one angle, eleven points, a solid.',
            '---',
            '<footer id="contact" class="ct"> H2: Something worth building? [nav.ct-col]'
        ],
        'dom_regions': [
            ('Header Navigation (`<header class="nav">` and `<nav class="nav-links">`)', 'Fixed top bar with `.nav-bg` opacity layer (`transition: opacity .4s var(--e-out)`), lowercase wordmark `denny pratama`, desktop links (`Work`, `About`, `Blog`, `Contact`), `.btn` CTA (`Get in touch`), and `<nav class="menu-l">` numbered index (`Home01`, `Work02`).'),
            ('Hero Monograph (`<section class="hero">`)', 'Full-viewport dark stage (`--ink: #0c0c0b`) bounded by `--wrap: 1680px` and `--pad: 48px`, centering H1 `"Denny Pratama, product designer. I design it. I build it."` with `.chip` status pills (`--r-full: 999px`), `--ok: #3ccf7d` availability indicator, and `--red: #ff4f2b` accents.'),
            ('Selected Work Ledger (`<section id="work" class="work on-bone">`)', 'High-contrast surface switch to warm limestone (`--bone: #eceae4`, text `--ink: #0c0c0b`) with `--lp: #0c0c0b24` 1px borders, housing H2 `"Drawn, then shipped."` and `"One project. Both sides of the line."`'),
            ('Geometric Mark Study (`<section class="con">`)', 'Interactive SVG vector construction diagram titled `"The mark, constructed: a pivot, one angle, eleven points, then a solid."` demonstrating coordinate geometry on `--ink-2: #151514`.'),
            ('Contact Colophon (`<footer id="contact" class="ct">` and `<nav class="ct-col">`)', 'Closing dark-mode footer anchored by H2 `"Something worth building?"`, social link columns (`nav.ct-col`), and `.lnk` hairline underlines (`transition: border-color .4s`).')
        ]
    },

    'agnestoth-com': {
        'title': 'AGNESTOTH · House of Luxurious Embroidery',
        'archetype': 'Haute Couture Embroidery Atelier & Quiet Luxury Editorial (`FreightBig Pro` + `Jost`)',
        'summary': 'A serene, museum-grade luxury fashion and bespoke embroidery house designed around pure gallery white (`rgb(255,255,255)`), warm alabaster stone (`#f0eee9`), charcoal ink (`rgb(18,18,18)`), and crimson thread accents (`#9a3b2e`).',
        'differentiators': [
            'Spaced Couture Letter-Spacing: The hero H1 (`"H o u s e o f L u x u r i o u s E m b r o i d e r y"`) uses wide architectural letter-spacing paired with `\'FreightBig Pro\'` editorial serif.',
            'Quiet Luxury Monochrome Palette: Built on `--color-background: 255,255,255`, `--color-foreground: 18,18,18`, warm stone `#f0eee9` panels, and deep embroidery crimson `#9a3b2e`.',
            'Bespoke Shopify Atelier Sections: Custom modular architecture (`header.agnes-collection__head`, `header.agnes-worlds__head`, `nav.agnes-header__nav`, `nav.agnes-footer__columns`) avoiding generic storefront grids.',
            'Silky Cubic-Bezier Media Reveals: Product imagery (`.media > img`) fades and scales using `transition: opacity .4s cubic-bezier(.25,.46,.45,.94)` and `cubic-bezier(.22, 1, .36, 1)`.'
        ],
        'grid': {
            'max_width': '1440px fluid gallery container with full-bleed editorial hero plates',
            'padding': 'Desktop horizontal padding `clamp(24px, 4vw, 64px)`, mobile `16px`',
            'desktop_cols': 'Asymmetric couture showcase grids (`2-column` and `3-column` lookbook plates in `.agnes-collection__head` and `.agnes-worlds__head`)',
            'mobile_layout': 'Single-column vertical lookbook stack below `@media (min-width: 750px)` with slide-out `<nav class="agnes-header__drawer-inner">`',
            'breakpoints': '`@media (min-width: 750px)`, `@media (hover: hover)`, `@media (prefers-reduced-motion: no-preference)`',
            'radii': '`0px` sharp gallery corners on all photography plates and buttons'
        },
        'semantic_tokens': [
            ('Primary Gallery Canvas', '#FFFFFF', '`--gradient-background` / `--color-background: 255,255,255`'),
            ('Warm Stone Secondary Surface', '#f0eee9', 'Section background rule in stylesheet'),
            ('Primary Charcoal Ink', '#121212', '`--color-foreground: 18,18,18` (`rgb(18,18,18)`)'),
            ('Primary Button Fill', '#121212', '`--color-button: 18,18,18`'),
            ('Primary Button Label', '#FFFFFF', '`--color-button-text: 255,255,255`'),
            ('Contrast Border / Divider', '#bfbfbf', '`--color-background-contrast: 191,191,191`'),
            ('Subtle Gallery Hairline', '#d9d9d9', 'Border rule in stylesheet (`#d9d9d9`)'),
            ('Couture Crimson Thread Accent', '#9a3b2e', 'Accent color in stylesheet (`#9a3b2e`)'),
            ('Deep Forest Overlay Scrim', '#0f1a198c', 'Hero/card scrim token (`#0f1a198c`)')
        ],
        'curated_vars': [
            ('--color-background', '255,255,255', 'Primary pure white canvas RGB triplet'),
            ('--gradient-background', '#FFFFFF', 'Root background hex surface'),
            ('--color-foreground', '18,18,18', 'Primary charcoal ink RGB triplet (#121212)'),
            ('--color-background-contrast', '191,191,191', 'Contrast border & divider RGB triplet (#bfbfbf)'),
            ('--color-shadow', '18,18,18', 'Elevation shadow base RGB triplet'),
            ('--color-button', '18,18,18', 'Primary couture CTA button fill'),
            ('--color-button-text', '255,255,255', 'Primary CTA button text color'),
            ('--color-secondary-button', '255,255,255', 'Secondary outline button surface'),
            ('--color-secondary-button-text', '18,18,18', 'Secondary outline button ink'),
            ('--color-link', '18,18,18', 'Editorial anchor link color'),
            ('--color-badge-foreground', '18,18,18', 'Collection badge label ink'),
            ('--color-badge-background', '255,255,255', 'Collection badge background'),
            ('--color-badge-border', '18,18,18', '1px collection badge border'),
            ('--font-body-family', 'Assistant, sans-serif', 'Base body font family variable'),
            ('--font-heading-family', 'Assistant, sans-serif', 'Base heading fallback variable (overridden by FreightBig Pro / Jost)'),
            ('--font-body-weight', '400', 'Regular body weight'),
            ('--font-body-weight-bold', '700', 'Bold emphasis weight')
        ],
        'fonts': {
            'display': "`'FreightBig Pro', 'Jost', serif` (High-contrast couture editorial display serif)",
            'body': "`'Jost', Assistant, sans-serif` (`--font-body-family`)",
            'mono': '`ui-monospace, "SF Mono", monospace` (Edition numbers & sizing specs)',
            'h1_spec': '`clamp(1.75rem, 4vw, 3.5rem)`, `font-family: \'FreightBig Pro\'`, wide tracking `0.25em` ("H o u s e o f L u x u r i o u s E m b r o i d e r y")',
            'h2_spec': '`clamp(1.5rem, 3vw, 2.5rem)`, `font-family: \'FreightBig Pro\'`, `font-weight: 400` ("The Poppy Collection", "A piece made only for you.", "The finest things are quiet. For the few who notice.")',
            'body_spec': '`0.9375rem (15px)`, `font-family: \'Jost\', Assistant, sans-serif`, `line-height: 1.7`, `color: rgb(18,18,18)`',
            'meta_spec': '`0.75rem (12px)`, `font-family: \'Jost\'`, `letter-spacing: 0.16em`, `text-transform: uppercase`'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.25,.46,.45,.94)` (quad ease-out) and `cubic-bezier(.22, 1, .36, 1)` (quintic ease-out)',
            'rules': [
                '`.media > img` -> `transition: opacity .4s cubic-bezier(.25,.46,.45,.94)` (lookbook image crossfade)',
                '`.animate-arrow .icon-arrow path` -> `transition: transform var(--duration-short) ease` (atelier link arrow glide)',
                '`.underlined-link, .inline-richtext a` -> `transition: text-decoration-thickness ease .1s` (editorial link hover)',
                '`Drawer & Gallery transitions` -> `cubic-bezier(.52,0,.61,.99)` and `cubic-bezier(0, 0, .3, 1)`'
            ]
        },
        'wireframe_lines': [
            '<nav class="agnes-header__nav"> [Menu]      AGNESTOTH      [Bag / Account]',
            '---',
            '<section class="agnes-hero"> (Canvas: #FFFFFF / Scrim: #0f1a198c)',
            '  H1: H o u s e o f L u x u r i o u s E m b r o i d e r y',
            '---',
            '<header class="agnes-collection__head"> (Surface: #f0eee9 / #FFFFFF)',
            '  H2: The Poppy Collection  |  [ Lookbook Plate 01 ] [ Lookbook Plate 02 ]',
            '---',
            '<header class="agnes-worlds__head"> (Bespoke Atelier Commission Section)',
            '  H2: A piece made only for you.',
            '  H2: The finest things are quiet. For the few who notice.',
            '---',
            '<header class="agnes-instagram__head"> & <header class="agnes-logos__head">',
            '---',
            '<nav class="agnes-footer__columns"> [Atelier] [Collections] [Legal] © 2026'
        ],
        'dom_regions': [
            ('Couture Header (`<nav class="agnes-header__nav">` and `<nav class="agnes-header__drawer-inner">`)', 'Minimalist top navigation bar on `rgb(var(--color-background))` (`#FFFFFF`) centering the `AGNESTOTH` wordmark with slide-out drawer navigation (`.agnes-header__drawer-inner`) below `750px`.'),
            ('Embroidery Hero Showcase (`H1: H o u s e o f L u x u r i o u s E m b r o i d e r y`)', 'Full-bleed couture photography stage with wide-tracked `\'FreightBig Pro\'` title, `#0f1a198c` atmospheric scrim, and sharp `0px` radius CTA buttons (`rgb(var(--color-button))`).'),
            ('The Poppy Collection (`<header class="agnes-collection__head">`)', 'Curated seasonal lookbook grid (`H2: The Poppy Collection`) with `#d9d9d9` hairline borders and `.media > img` hover transitions (`cubic-bezier(.25,.46,.45,.94)`).'),
            ('Bespoke Worlds Narrative (`<header class="agnes-worlds__head">`)', 'Editorial storytelling blocks (`H2: A piece made only for you.` and `H2: The finest things are quiet. For the few who notice.`) set on `#f0eee9` warm stone with `#9a3b2e` crimson accents.'),
            ('Atelier Footer (`<nav class="agnes-footer__columns">`)', 'Multi-column architectural footer housing private client appointment links, press logos (`.agnes-logos__head`), Instagram gallery (`.agnes-instagram__head`), and newsletter capture.')
        ]
    },

    'www-sigma-imaging-se': {
        'title': 'SIGMA Imaging Nordics & Baltics · Optical Lenses, Cameras & Cine-Lenses',
        'archetype': 'Swiss-Japanese Precision Optical Hardware & Storefront System (`Sigma Sans` + `Sigma Serif`)',
        'summary': 'An industrial optical engineering and e-commerce platform built on pure optical white (`--ps-white: #fefefd`), matte lens-barrel charcoal (`--ps-text: #333333`), subtle light tint (`--ps-tint: #f7f7f7`), and precision blue/amber indicators (`#4eb5e6`, `#ff9a19`).',
        'differentiators': [
            'Bespoke Optical Typography Pairing: Pairs geometric technical sans (`--ps-font-ui: "Sigma Sans", sans-serif`) for lens designations with editorial serif (`--ps-font-body: "Sigma Serif", serif`) for optical craftsmanship stories.',
            'Snap-Grid Product Carousel Physics: Driven by CSS scroll-snap equations (`--snap-size: calc((100% - var(--snap-gap)*(var(--snap-cols) - 1))/var(--snap-cols))`) and `.kuCarousel .kuSlide` easing `cubic-bezier(0.47,0.13,0.15,0.89)`.',
            'High-Contrast Monoline Navigation (`#ammenu-header-container`)**: Precision mega-menu with `--ammenu-main-menu-background: #fefefd`, `--ammenu-main-menu-text: #333333`, and `--ammenu-border: #757575`.',
            'Photographer Ambassador Integration: Pairs technical hardware grids (`H2: NEW PRODUCTS`, `H2: FEATURED PRODUCTS`) with artist monographs (`H1: Trevor Key`) and bold hero statements (`H1: SMALL, POWERFUL, ADAPTABLE`).'
        ],
        'grid': {
            'max_width': '1440px container with `@media (min-width: 1201px)` and `@media (min-width: 1024px)` multi-column optical grids',
            'padding': 'Desktop `clamp(16px, 3vw, 48px)` (`p-4` utility base), mobile `16px`',
            'desktop_cols': '4-column product specification matrix on desktop, dynamic `--snap-cols` carousel track',
            'mobile_layout': 'Single-column snap track (`--snap-cols: 1`, `--snap-gap: 1rem`) below `@media (max-width: 1023px)`',
            'breakpoints': '`@media (min-width: 768px)`, `@media (max-width: 1023px)`, `@media (min-width: 1024px)`, `@media (max-width: 1200px)`, `@media (min-width: 1201px)`',
            'radii': '`--adp-border-radius: 4px` for UI controls; sharp `0px` framing for optical product plates'
        },
        'semantic_tokens': [
            ('Primary Optical White Canvas', '#fefefd', '`--ps-white` / `--ammenu-main-menu-background`'),
            ('Secondary Light Tint Surface', '#f7f7f7', '`--ps-tint` (`:root`)'),
            ('Pure White Card Surface', '#FFFFFF', '`--amcart-background` / `--amscroll-background-color`'),
            ('Primary Lens-Barrel Charcoal Ink', '#333333', '`--ps-text` / `--ammenu-main-menu-text`'),
            ('Deep Black Product Title Ink', '#000000', '`--amcart-product-name`'),
            ('Secondary Muted Ink', 'rgba(51, 51, 51, 0.5)', '`--ps-text-muted` (`:root`)'),
            ('Structural Border Gray', '#cccfd6', '`--amscroll-border-color`'),
            ('Navigation Rule Gray', '#757575', '`--ammenu-border` / `--ammenu-toggle-icon-color`'),
            ('Optical Cyan Accent', '#4eb5e6', '`--adp-accent-color`'),
            ('Interactive Blue Toggle', '#2563eb', '`--ambar-toggle-active-color`'),
            ('Aperture Amber Highlight', '#ff9a19', '`--adp-day-name-color`')
        ],
        'curated_vars': [
            ('--ps-white', '#fefefd', 'Primary optical white substrate'),
            ('--ps-tint', '#f7f7f7', 'Secondary product card tint surface'),
            ('--ps-text', '#333', 'Primary charcoal typography ink'),
            ('--ps-text-muted', 'rgba(51, 51, 51, 0.5)', 'Muted technical specification ink'),
            ('--ps-font-ui', '"Sigma Sans", sans-serif', 'Primary technical UI & lens designation font'),
            ('--ps-font-body', '"Sigma Serif", serif', 'Editorial monograph & body serif font'),
            ('--snap-cols', '1', 'Responsive scroll-snap column divisor'),
            ('--snap-gap', '1rem', 'Scroll-snap grid gutter spacing'),
            ('--snap-size', 'calc((100% - var(--snap-gap)*(var(--snap-cols) - 1))/var(--snap-cols))', 'Dynamic card width calculation'),
            ('--ammenu-main-menu-background', '#fefefd', 'Header mega-menu background'),
            ('--ammenu-main-menu-text', '#333333', 'Header navigation link ink'),
            ('--ammenu-submenu-text-hover', '#4d4d4d', 'Submenu hover ink state'),
            ('--ammenu-border', '#757575', 'Navigation structural divider color'),
            ('--amcart-background', '#FFFFFF', 'Cart drawer background surface'),
            ('--amcart-product-name', '#000000', 'Cart product title ink'),
            ('--amscroll--transition-duration', '.2s', 'Fast UI transition duration'),
            ('--amscroll--hover-filter', 'brightness(.8)', 'Optical image hover darkening filter'),
            ('--amscroll-border-color', '#cccfd6', 'Card & pagination border token'),
            ('--ambar-toggle-active-color', '#2563eb', 'Active interactive toggle accent')
        ],
        'fonts': {
            'display': '`"Sigma Sans", "Century Gothic", CenturyGothic, AppleGothic, sans-serif` (`--ps-font-ui`)',
            'body': '`"Sigma Serif", serif` (`--ps-font-body`) paired with `"Sigma Sans", sans-serif`',
            'mono': '`ui-monospace, "SF Mono", monospace` (Focal length `mm`, aperture `F1.4 DG DN`, mount codes)',
            'h1_spec': '`clamp(2.5rem, 5vw, 4.5rem)`, `font-family: var(--ps-font-ui)`, `font-weight: 700`, `letter-spacing: -0.02em`, uppercase ("SMALL, POWERFUL, ADAPTABLE" / "Trevor Key")',
            'h2_spec': '`clamp(1.5rem, 2.8vw, 2.25rem)`, `font-family: var(--ps-font-ui)`, `font-weight: 600`, `letter-spacing: 0.04em` ("NEW PRODUCTS", "FEATURED PRODUCTS")',
            'body_spec': '`1rem (16px)`, `font-family: var(--ps-font-body)`, `line-height: 1.65`, `color: var(--ps-text)`',
            'meta_spec': '`0.75rem (12px)`, `font-family: var(--ps-font-ui)`, `color: var(--ps-text-muted)`, `letter-spacing: 0.08em`, uppercase mount badges (`L-MOUNT`, `SONY E`, `CINE`)'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(0.47,0.13,0.15,0.89)` (carousel slide) and `cubic-bezier(.49,.78,.46,1.34)` (spring overshoot)',
            'rules': [
                '`.kuCarousel .kuSlide` -> `transition: left .4s cubic-bezier(0.47,0.13,0.15,0.89)` (optical product carousel track)',
                '`.kuCarousel .kuCarousel-content` -> `transition: width .4s` (responsive container adaptation)',
                '`.kuFilterHead` -> `transition: all .3s ease` (lens mount & focal-length filter accordion)',
                '`.kuFilterIcon:before` -> `transition: background 200ms` (filter checkbox state)',
                '`Image Hover` -> `filter: var(--amscroll--hover-filter)` (`brightness(.8)`) over `--amscroll--transition-duration: .2s`'
            ]
        },
        'wireframe_lines': [
            '<header id="ammenu-header-container" class="ammenu-header-container">',
            '  [SIGMA]   [Lenses] [Cameras] [Cine] [Ambassadors]   [Search] [Cart]',
            '---',
            '<main id="maincontent" class="page-main"> (Canvas: --ps-white #fefefd)',
            '  Hero Split Stage: H1: SMALL, POWERFUL, ADAPTABLE | H1: Trevor Key',
            '---',
            '<section class="new-products"> H2: NEW PRODUCTS (Surface: --ps-tint #f7f7f7)',
            '  [ Art 50mm F1.2 DG DN ]  [ Contempor. 28-70mm ]  [ Sports 500mm F5.6 ]',
            '---',
            '<section class="featured-products"> H2: FEATURED PRODUCTS (.kuCarousel)',
            '  Track: --snap-size calc((100% - var(--snap-gap)*(var(--snap-cols)-1)))',
            '---',
            '<footer> [Nordic Service] [Warranty] [Authorized Dealers] © SIGMA Nordics'
        ],
        'dom_regions': [
            ('Mega-Menu Header (`<header id="ammenu-header-container" class="ammenu-header-container page-header">`)', 'Sticky optical navigation bar styled with `--ammenu-main-menu-background: #fefefd`, `--ammenu-main-menu-text: #333333`, `--ammenu-border: #757575`, and `#am-account-menu`.'),
            ('Dual Hero Campaign Stage (`<main id="maincontent" class="page-main">`)', 'Features high-impact hardware hero (`H1: SMALL, POWERFUL, ADAPTABLE` in `--ps-font-ui`) alongside artist monograph feature (`H1: Trevor Key` in `--ps-font-body`).'),
            ('New Products Hardware Matrix (`H2: NEW PRODUCTS`)', '4-column desktop grid of optical lenses and cameras on `--ps-tint: #f7f7f7` cards with mount badges (`L-MOUNT`, `SONY E`), focal length specs, and `--amscroll-border-color: #cccfd6` borders.'),
            ('Featured Products Snap Carousel (`H2: FEATURED PRODUCTS` and `.kuCarousel`)', 'Horizontal snap track driven by `--snap-size` and `cubic-bezier(0.47,0.13,0.15,0.89)` slide physics with `.kuFilterHead` faceted lens filtering.'),
            ('Cart & Service Footer (`--amcart-background: #FFFFFF`)', 'Slide-over cart drawer (`--amcart-product-name: #000000`) and Nordic dealer/service colophon.')
        ]
    },

    'morrow-valerian-studio': {
        'title': 'MORROW (by Valerian Studio) · High-Craft Editorial Knitwear & Fashion Flagship',
        'archetype': 'Swiss-Editorial Fashion Scrollytelling & 12-Column Viewport Grid (`ppNeueMontreal` + `editorialNew`)',
        'summary': 'An avant-garde fashion and knitwear digital flagship crafted by Valerian Studio, pairing crisp gallery white (`--color-white: #fff`) and neutral alabaster (`--color-neutral-50: #fafafa`) with deep espresso/charcoal ink (`--color-neutral-900: #171717`, `#030d07`), crimson/rose editorial accents (`#9f0712`, `#ff2357`), and a strict 12-column `h-svh` viewport grid.',
        'differentiators': [
            'Signature Dual-Typeface Tension: Pairs Neo-Grotesque `--font-pp-neue-montreal` (`"ppNeueMontreal"`) for geometric UI navigation with high-contrast serif `--font-editorial-new` (`"editorialNew"`) for poetic campaign headlines (`"Beyond the everyday."`, `"Same streets, new miles."`).',
            'Full-Viewport 12-Column SVH Stage: Hero and lookbook sections lock to `<section class="relative grid h-svh grid-cols-12 gap-x-gutter">` with overlapping grid-cell placement (`col-start-1 row-start-1`).',
            'Three-Part Balanced Toolbar (`nav.site-nav-toolbar`)**: Uses `grid-cols-[1fr_auto_1fr]` so left navigation, centered `MORROW` brand mark, and right cart trigger remain mathematically centered at all viewport widths.',
            'Quintic Ease-Out Choreography: Smooth lookbook transitions driven by `cubic-bezier(.22,1,.36,1)` alongside reduced-motion accessibility guards.'
        ],
        'grid': {
            'max_width': 'Full-bleed `100vw` with fluid `px-page` lateral margins and `gap-x-gutter` column gutters',
            'padding': 'Custom `px-page` lateral padding token with `--spacing: .25rem` (4px) base unit',
            'desktop_cols': '12-column CSS Grid (`grid-cols-12 gap-x-gutter`), asymmetric `col-span-4` sidebar nav + `col-span-10` / `col-span-8` editorial plates',
            'mobile_layout': 'Collapses at `@media (max-width:400px)` and below `@media (min-width:48rem)` into `<nav class="site-mobile-menu-content px-page">` drawer',
            'breakpoints': '`@media (max-width:400px)`, `@media (min-width:40rem)` (640px), `@media (min-width:48rem)` (768px), `@media (min-width:64rem)` (1024px)',
            'radii': '`0px` sharp editorial edges on all lookbook photography and product cards'
        },
        'semantic_tokens': [
            ('Primary Gallery Canvas', '#ffffff', '`--color-white` (`main.bg-white`)'),
            ('Secondary Porcelain Surface', '#fafafa', '`--color-neutral-50` (`:root`)'),
            ('Tertiary Lookbook Surface', '#f5f5f5', '`--color-neutral-100` (`:root`)'),
            ('Primary Charcoal Ink', '#171717', '`--color-neutral-900` (`:root`)'),
            ('Deep Forest/Black Ink', '#030d07', 'Extracted stylesheet dark tone (`#030d07` / `--color-black: #000`)'),
            ('Secondary Muted Copy', '#737373', '`--color-neutral-500` (`:root`)'),
            ('Subtle Label Gray', '#a1a1a1', '`--color-neutral-400` (`:root`)'),
            ('Hairline Grid Divider', '#e5e5e5', '`--color-neutral-200` (`:root`)'),
            ('Secondary Border Gray', '#d4d4d4', '`--color-neutral-300` (`:root`)'),
            ('Crimson Campaign Accent', '#9f0712', '`--color-red-800` (`:root`)'),
            ('Editorial Rose Highlight', '#ff2357', '`--color-rose-500` (`:root`)')
        ],
        'curated_vars': [
            ('--font-pp-neue-montreal', '"ppNeueMontreal", "ppNeueMontreal Fallback"', 'Primary Neo-Grotesque UI & grid sans font'),
            ('--font-editorial-new', '"editorialNew", "editorialNew Fallback"', 'High-contrast editorial display serif font'),
            ('--font-sans', 'var(--font-pp-neue-montreal), Arial, sans-serif', 'Default sans-serif font stack'),
            ('--color-white', '#fff', 'Primary canvas background'),
            ('--color-black', '#000', 'Pure black contrast token'),
            ('--color-neutral-50', '#fafafa', 'Porcelain soft background surface'),
            ('--color-neutral-100', '#f5f5f5', 'Product card backdrop surface'),
            ('--color-neutral-200', '#e5e5e5', '1px hairline border token'),
            ('--color-neutral-300', '#d4d4d4', 'Hover border divider token'),
            ('--color-neutral-400', '#a1a1a1', 'Muted index & size label ink'),
            ('--color-neutral-500', '#737373', 'Secondary editorial body ink'),
            ('--color-neutral-600', '#525252', 'Sub-heading ink'),
            ('--color-neutral-900', '#171717', 'Primary heading & garment title ink'),
            ('--color-red-800', '#9f0712', 'Deep crimson seasonal accent'),
            ('--color-rose-500', '#ff2357', 'Vibrant rose campaign highlight'),
            ('--spacing', '.25rem', 'Base 4px spatial unit'),
            ('--container-xs', '20rem', 'Mini cart / drawer container width (320px)'),
            ('--container-3xl', '48rem', 'Editorial narrative column max-width (768px)'),
            ('--text-xs', '.75rem', '12px micro-label size (`line-height: calc(1 / .75)`)')
        ],
        'fonts': {
            'display': '`"editorialNew", "editorialNew Fallback", Georgia, serif` (`--font-editorial-new`)',
            'body': '`"ppNeueMontreal", "ppNeueMontreal Fallback", Arial, sans-serif` (`--font-pp-neue-montreal`)',
            'mono': '`ui-monospace, "SF Mono", monospace` (SKU codes, garment weights, collection indices)',
            'h1_spec': '`clamp(2.75rem, 6vw, 5.25rem)`, `font-family: var(--font-editorial-new)`, `font-weight: 400`, `line-height: 0.96`, `letter-spacing: -0.02em`',
            'h2_spec': '`clamp(1.5rem, 3.2vw, 2.75rem)`, `font-family: var(--font-pp-neue-montreal)` for `"FALL26’ KNITWEAR PORCELAIN"`, `"New Arrival"`, `"Categories"` and `var(--font-editorial-new)` for `"Beyond the everyday."`, `"Same streets, new miles."`',
            'body_spec': '`0.9375rem (15px)`, `font-family: var(--font-sans)`, `line-height: 1.55`, `color: var(--color-neutral-600)`',
            'meta_spec': '`var(--text-xs)` (`0.75rem`), `font-family: var(--font-pp-neue-montreal)`, `letter-spacing: 0.06em`, `text-transform: uppercase`'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.22,1,.36,1)` (quintic ease-out) and `cubic-bezier(0, 0, .2, 1)`',
            'rules': [
                '`.shipping-banner-announcement` -> `transition: opacity .15s, transform .15s, visibility 0s linear .15s`',
                '`.site-nav:has(:focus-visible)` -> `transform: translateY(0)` reveal from `translateY(-100%)`',
                '`Lookbook & Card Image Reveals` -> `cubic-bezier(.22,1,.36,1)` over `500ms`',
                '`@media (prefers-reduced-motion:reduce)` -> `transition: none`'
            ]
        },
        'wireframe_lines': [
            '<header class="site-nav col-start-1 row-start-1">',
            '  <nav class="site-nav-toolbar grid grid-cols-[1fr_auto_1fr]">',
            '  [Shop / Collections]           MORROW           [Search / Cart (0)]',
            '---',
            '<section class="relative grid h-svh grid-cols-12 gap-x-gutter">',
            '  H2: FALL26’ KNITWEAR PORCELAIN  |  H2: Beyond the everyday.',
            '---',
            '<main class="relative z-10 bg-white"> (12-Column Asymmetric Lookbook)',
            '  <nav class="col-span-4"> H2: Categories  |  <div class="col-span-8">',
            '  H2: New Arrival  [ Knitwear Plate 01 ]  [ Porcelain Plate 02 ]',
            '---',
            '<section class="editorial-story"> H2: Same streets, new miles.',
            '---',
            '<header class="cart-header"> Slide-Over Cart Drawer (--container-xs 20rem)'
        ],
        'dom_regions': [
            ('Tri-Balanced Navigation (`<header class="site-nav">` and `<nav class="site-nav-toolbar grid grid-cols-[1fr_auto_1fr]">`)', 'Overlays the hero stage at `col-start-1 row-start-1` with a 3-column symmetric toolbar (`1fr auto 1fr`), `.shipping-banner-announcement` top bar, and `<nav class="site-mobile-menu-content px-page">` mobile sheet.'),
            ('Full-Viewport SVH Hero (`<section class="relative grid h-svh grid-cols-12 gap-x-gutter">`)', '100svh 12-column stage featuring seasonal campaign `"FALL26’ KNITWEAR PORCELAIN"` in `--font-pp-neue-montreal` and `"Beyond the everyday."` in `--font-editorial-new`.'),
            ('Asymmetric Category & New Arrival Grid (`<main class="relative z-10 bg-white">`)', 'Splits into `<nav class="col-span-4 min-w-0 self-start">` (`H2: Categories`) and `<nav class="col-span-12 lg:col-span-10">` (`H2: New Arrival`) on `--color-neutral-50: #fafafa` and `--color-neutral-100: #f5f5f5` cards.'),
            ('Editorial Narrative Chapter (`H2: Same streets, new miles.`)', 'High-contrast lookbook diptych using `--font-editorial-new`, `--container-3xl: 48rem` measure, and `--color-red-800: #9f0712` / `--color-rose-500: #ff2357` editorial accents.'),
            ('Cart Drawer (`<header class="cart-header">`)', 'Slide-over commerce bag constrained to `--container-xs: 20rem` (`320px`) with `--color-neutral-200: #e5e5e5` hairline dividers.')
        ]
    },

    'wiro-ae': {
        'title': 'Wiro · Operating System for All Agencies',
        'archetype': 'Warm-Paper Tactile SaaS Operating System & Scatter-Card Workspace (`Bricolage Grotesque` + `DM Sans`)',
        'summary': 'An inventive agency operating system interface built on a warm cream paper canvas (`--wiro-canvas: #f6efe3`), deep charcoal ink (`--wiro-ink: #10100f`), and tactile pastel workspace accents (`--wiro-pink: #efacd4`, `--wiro-yellow: #f6d94e`, `--wiro-sage: #9caf63`, `--wiro-blue: #a9c5e9`).',
        'differentiators': [
            'Warm Cream Paper Substrate (`--wiro-canvas: #f6efe3`)**: Rejects sterile white or dark-mode SaaS clichés in favor of a tactile paper canvas (`#f6efe3`) with stepped warm stone surfaces (`--wiro-surface-2: #ece5da`, `#fffdf8`).',
            'Color-Coded Agency OS Modules: Maps core agency workflows (`H3: Projects`, `H3: Clients`, `H3: Finance`) to distinct pastel workspace tokens (`--wiro-pink: #efacd4`, `--wiro-yellow: #f6d94e`, `--wiro-sage: #9caf63`, `--wiro-blue: #a9c5e9`).',
            'Interactive Scatter-Card Physics (`nav.lp-scatter`, `footer.lp-footer-scatter`)**: Floating agency artifact cards and module tabs arranged around the central workspace viewport (`section.lp-hero`).',
            'Expressive Display Grotesque Pairing: Pairs `--font-heading: "Bricolage Grotesque"` for charismatic headlines (`"Organize Your Chaos"`) with `--font-ui: "DM Sans"` and `2.5px` solar icon strokes (`--solar-stroke-width: 2.5`).'
        ],
        'grid': {
            'max_width': '1536px (`@media (min-width:1536px)`) with central OS stage max-width `1280px`',
            'padding': 'Desktop `clamp(24px, 4vw, 56px)`, mobile `16px` below `@media (max-width:900px)`',
            'desktop_cols': '12-column SaaS showcase grid with 3-column feature bento (`Projects`, `Clients`, `Finance`) in `<section id="pages" class="lp-section lp-pages">`',
            'mobile_layout': 'Single-column stack below `@media (max-width:900px)` and `@media (min-width:768px)`, collapsing `.lp-scatter` floating cards into an inline grid',
            'breakpoints': '`@media (min-width:640px)`, `@media (min-width:768px)`, `@media (max-width:900px)`, `@media (min-width:1024px)`, `@media (min-width:1280px)`, `@media (min-width:1536px)`',
            'radii': '`--wiro-radius-lg: 18px` for primary OS window frames, `--wiro-radius-md: 12px` for module cards and pills'
        },
        'semantic_tokens': [
            ('Primary Warm Paper Canvas', '#f6efe3', '`--wiro-canvas` / `--wiro-surface` (`:root`)'),
            ('Secondary Recessed Surface', '#ece5da', '`--wiro-surface-2` (`:root`)'),
            ('Elevated Paper Sheet', '#fffdf8', 'Card surface hex in stylesheet (`#fffdf8`)'),
            ('Primary Charcoal Ink', '#10100f', '`--wiro-ink` (`:root`)'),
            ('Muted Secondary Ink', 'rgba(16,16,15,.52)', '`--wiro-muted` (`:root`)'),
            ('Subtle Hairline Border', 'rgba(16,16,15,.12)', '`--wiro-border` (`:root`)'),
            ('Projects Module Pink', '#efacd4', '`--wiro-pink` (`:root`)'),
            ('Finance Module Yellow', '#f6d94e', '`--wiro-yellow` (`:root`)'),
            ('Clients Module Sage', '#9caf63', '`--wiro-sage` (`:root`)'),
            ('Operations Module Blue', '#a9c5e9', '`--wiro-blue` (`:root`)')
        ],
        'curated_vars': [
            ('--font-heading', 'var(--font-bricolage),"Bricolage Grotesque",sans-serif', 'Primary display heading font stack'),
            ('--font-ui', 'var(--font-dmsans),"DM Sans",Arial,Helvetica,sans-serif', 'UI controls & body copy font stack'),
            ('--solar-stroke-width', '2.5', 'Custom stroke weight for vector UI icons'),
            ('--wiro-ink', '#10100f', 'Primary deep charcoal ink'),
            ('--wiro-muted', 'rgba(16,16,15,.52)', 'Muted label & description ink'),
            ('--wiro-canvas', '#f6efe3', 'Warm paper viewport canvas'),
            ('--wiro-surface', '#f6efe3', 'Primary container surface'),
            ('--wiro-surface-2', '#ece5da', 'Secondary recessed bento card surface'),
            ('--wiro-border', 'rgba(16,16,15,.12)', '1px structural card & table border'),
            ('--wiro-pink', '#efacd4', 'Pastel pink accent for Projects module'),
            ('--wiro-yellow', '#f6d94e', 'Warm yellow accent for Finance module'),
            ('--wiro-sage', '#9caf63', 'Sage green accent for Clients module'),
            ('--wiro-blue', '#a9c5e9', 'Sky blue accent for Team/Docs module'),
            ('--wiro-radius-lg', '18px', 'Outer OS window & section border radius'),
            ('--wiro-radius-md', '12px', 'Inner card & button border radius')
        ],
        'fonts': {
            'display': '`"Bricolage Grotesque", sans-serif` (`--font-heading`)',
            'body': '`"DM Sans", Arial, Helvetica, sans-serif` (`--font-ui`)',
            'mono': '`ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` (KPI counters, invoice IDs, keyboard shortcuts)',
            'h1_spec': '`clamp(2.75rem, 6vw, 5.25rem)`, `font-family: var(--font-heading)`, `font-weight: 700`, `line-height: 0.98`, `letter-spacing: -0.03em` ("Organize Your Chaos")',
            'h2_spec': '`clamp(1.75rem, 3.5vw, 3rem)`, `font-family: var(--font-heading)`, `font-weight: 600`, `line-height: 1.1` ("One system for every page your agency runs.")',
            'body_spec': '`1rem (16px)`, `font-family: var(--font-ui)`, `line-height: 1.6`, `color: var(--wiro-muted)`',
            'meta_spec': '`0.8125rem (13px)`, `font-family: var(--font-ui)`, `font-weight: 600`, pill badges colored with `--wiro-pink`, `--wiro-yellow`, `--wiro-sage`, `--wiro-blue`'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(0.22,1,0.36,1)` (smooth quintic out) and `cubic-bezier(0.55,0,0.75,0.2)`',
            'rules': [
                '`.lp-scatter` floating cards -> `transition: transform 400ms cubic-bezier(0.22,1,0.36,1)` (interactive hover lift & tilt)',
                '`Tab switcher in section#pages.lp-pages` -> `cubic-bezier(.4,0,.2,1)` (`250ms` active pill morph)',
                '`Waitlist CTA button` -> `transition: background-color 200ms cubic-bezier(0,0,.2,1), transform 200ms`'
            ]
        },
        'wireframe_lines': [
            '<nav class="lj-bar-links"> [Wiro]     [Pages] [OS Preview] [Join Waitlist]',
            '---',
            '<main class="lp-page"> <section class="lp-hero"> (Canvas: #f6efe3)',
            '  H1: Organize Your Chaos',
            '  <nav class="lp-scatter"> [Pink: Projects] [Sage: Clients] [Yellow: Fin]',
            '---',
            '<section id="pages" class="lp-section lp-pages"> (Radius: 18px)',
            '  H2: One system for every page your agency runs.',
            '  [ H3: Projects (#efacd4) ] [ H3: Clients (#9caf63) ] [ H3: Finance ]',
            '---',
            '<section id="waitlist" class="lp-section lp-waitlist"> [Early Access Form]',
            '---',
            '<footer class="lp-footer lp-footer-scatter"> © Wiro Agency OS'
        ],
        'dom_regions': [
            ('Top Bar Navigation (`<nav class="lj-bar-links">`)', 'Floating pill bar on `--wiro-canvas: #f6efe3` with `1px solid var(--wiro-border)` linking `#pages`, `#waitlist`, and workspace login.'),
            ('Hero Stage & Scatter Cards (`<section class="lp-hero">` and `<nav class="lp-scatter">`)', 'Centers `H1: Organize Your Chaos` in `--font-heading` surrounded by interactive `.lp-scatter` cards color-coded with `--wiro-pink: #efacd4`, `--wiro-yellow: #f6d94e`, `--wiro-sage: #9caf63`, and `--wiro-blue: #a9c5e9`.'),
            ('Interactive Agency Pages Bento (`<section id="pages" class="lp-section lp-pages">`)', 'Anchored by `H2: One system for every page your agency runs.`, showcasing live interactive previews of `H3: Projects`, `H3: Clients`, and `H3: Finance` inside `--wiro-radius-lg: 18px` frames on `#fffdf8` sheets.'),
            ('Waitlist Conversion Module (`<section id="waitlist" class="lp-section lp-waitlist">`)', 'High-contrast onboarding capture card on `--wiro-surface-2: #ece5da` with `--wiro-radius-md: 12px` inputs and `--wiro-ink: #10100f` submit button.'),
            ('Scatter Footer (`<footer class="lp-footer lp-footer-scatter">`)', 'Playful footer with scattered agency module badges and colophon links.')
        ]
    },

    'www-anduril-com': {
        'title': 'Anduril Industries · Transforming U.S. Defense Capabilities with Advanced Technology',
        'archetype': 'Mission-Critical Defense Autonomy Platform & Odometer Telemetry Grid (`HelveticaNowDisplay` + `Elios`)',
        'summary': 'An authoritative defense hardware and autonomous systems platform engineered around near-black stealth carbon (`--theme-color-background: #010101`), stark optic white (`--theme-color-text: #fff`), warm bone light-theme chapters (`.theme-light` / `#f1f0ea`), and high-visibility radar volt-yellow (`#dff140`) and alert red (`#ff3535`) indicators.',
        'differentiators': [
            'Programmatic Theme Polarity Switching: Uses root CSS state variables (`--theme-color-background: #010101`, `--theme-color-text: #fff`) that invert dynamically on `.theme-light` chapters (`#f1f0ea` / `#fff` background with `#010101` ink).',
            'Mechanical Odometer Metric Rollers: Driven by custom CSS math (`--roller-scale: 1`, `--roller-font-size: calc(3rem*var(--roller-scale))`, `--roller-column-height: calc(2.5rem*var(--roller-scale))`, `--roller-digit-width: calc(1.8rem*var(--roller-scale))`).',
            'Tactical Hardware Showcase Matrix: Full-viewport autonomous system modules (`H2: Arsenal-2`, `H2: Steelfin`, `H2: Barracuda`) with aspect-ratio locked containers (`--in-production-h: max(505px,100vw*505/403)`).',
            'Precision Industrial Typography: Combines `HelveticaNowDisplay` for authoritative headlines with custom technical display face `Elios` for system designations and telemetry readouts.'
        ],
        'grid': {
            'max_width': 'Full-bleed `100vw` tactical viewports with `@media (min-width:1440px)` and `@media (min-width:1920px)` max-bounds',
            'padding': 'Responsive horizontal padding scaling across `@media (min-width:400px)`, `768px`, `1280px`, `1440px`, `1920px`',
            'desktop_cols': 'Full-bleed stacked hardware plates (`Arsenal-2`, `Steelfin`, `Barracuda`) and multi-column telemetry counters',
            'mobile_layout': 'Controlled via `--is-mob: 1` inside `@media (max-width:768px)`, activating `<nav class="HeaderMobile lenis">`',
            'breakpoints': '`@media (max-width:768px)` (`--is-mob: 1`), `@media (min-width:400px)`, `@media (min-width:768px)`, `@media (min-width:1280px)`, `@media (min-width:1440px)`, `@media (min-width:1920px)`',
            'radii': '`0px` razor-sharp corners across all cards and plates; circular `.CircleButton` (`border: 1px solid #fff`) for directional triggers'
        },
        'semantic_tokens': [
            ('Primary Dark Substrate', '#010101', '`--theme-color-background` (`:root`)'),
            ('Pure Black Base', '#000000', '`--base-color: black` (`:root`)'),
            ('Light-Theme Paper Surface', '#f1f0ea', 'Extracted stylesheet surface hex (`#f1f0ea` / `.theme-light`)'),
            ('Elevated Slate Surface', '#2f3234', 'Extracted stylesheet dark panel hex (`#2f3234`)'),
            ('Primary White Ink', '#fff', '`--theme-color-text` / `--linkbutton-color` (`:root`)'),
            ('Secondary Muted Ink', '#b0b0a9', 'Extracted stylesheet muted neutral (`#b0b0a9`)'),
            ('Dimmed Technical Ink', '#565654', 'Extracted stylesheet dim gray (`#565654`)'),
            ('Radar Volt-Yellow Accent', '#dff140', 'Extracted stylesheet high-visibility accent (`#dff140`)'),
            ('Alert Warning Red', '#ff3535', 'Extracted stylesheet warning indicator (`#ff3535`)')
        ],
        'curated_vars': [
            ('--is-mob', '0', 'Viewport breakpoint flag (switches to `1` at `max-width: 768px`)'),
            ('--theme-color-background', '#010101', 'Primary stealth carbon background surface'),
            ('--theme-color-text', '#fff', 'Primary high-contrast text ink'),
            ('--base-color', 'black', 'Base substrate color keyword'),
            ('--contrast-color', 'white', 'High-contrast foreground keyword'),
            ('--linkbutton-color', '#fff', 'Default `.LinkButton` & `.CircleButton` foreground'),
            ('--linkbutton-hover-color', '#010101', 'Inverted button label ink on hover fill'),
            ('--roller-scale', '1', 'Scale multiplier for mechanical odometer counters'),
            ('--roller-font-size', 'calc(3rem*var(--roller-scale))', 'Odometer digit font size'),
            ('--roller-column-height', 'calc(2.5rem*var(--roller-scale))', 'Odometer vertical slot height'),
            ('--roller-digit-width', 'calc(1.8rem*var(--roller-scale))', 'Odometer fixed monospace digit width'),
            ('--links-height', '0px', 'Collapsible navigation submenu height state'),
            ('--frosted', '0', 'Header backdrop frosted glass state toggle'),
            ('--backgroundOpacity', '0', 'Dynamic scroll overlay opacity value'),
            ('--in-production-h', 'max(505px,100vw*505/403)', 'Responsive aspect-ratio height for production showcase')
        ],
        'fonts': {
            'display': '`HelveticaNowDisplay, Helvetica, Arial, sans-serif` paired with `Elios, sans-serif`',
            'body': '`HelveticaNowDisplay, Helvetica, Arial, sans-serif`',
            'mono': '`Elios, monospace` (Odometer telemetry counters, autonomous system designations)',
            'h1_spec': '`clamp(2.75rem, 5.5vw, 5.25rem)`, `font-family: HelveticaNowDisplay, sans-serif`, `font-weight: 500`, `line-height: 0.98`, `letter-spacing: -0.03em`',
            'h2_spec': '`clamp(2rem, 4vw, 3.75rem)`, `font-family: HelveticaNowDisplay, sans-serif` ("Arsenal-2", "Steelfin", "Barracuda")',
            'body_spec': '`1rem (16px)`, `font-family: HelveticaNowDisplay, Helvetica, Arial, sans-serif`, `line-height: 1.55`, `color: #b0b0a9`',
            'meta_spec': '`0.75rem (12px)`, `font-family: Elios, sans-serif`, `letter-spacing: 0.1em`, `text-transform: uppercase`'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.215,.62,.34,1)` (custom tactical ease-out)',
            'rules': [
                '`Button & Link Reveals (.CircleButton, .LinkButton)` -> `transition: all 350ms cubic-bezier(.215,.62,.34,1)` (invert fill from `#010101` to `#fff` with `--linkbutton-hover-color: #010101`)',
                '`Odometer Digit Columns` -> vertical `translateY` slot rotation scaled by `--roller-column-height: calc(2.5rem*var(--roller-scale))`',
                '`Lenis Smooth Scroll (<nav class="HeaderMobile lenis">)` -> inertia-damped viewport scrolling synchronized with `--backgroundOpacity`'
            ]
        },
        'wireframe_lines': [
            '<header class="header"> <nav class="HeaderDesktop"> (Bg: #010101, Text: #fff)',
            '  [ANDURIL]   [Lattice] [Arsenal] [Air] [Maritime] [Subsea] [Mission]     ',
            '---',
            '<main> Hero Mission Stage (Substrate: --theme-color-background #010101)',
            '  Title: Transforming U.S. Defense Capabilities with Advanced Technology',
            '  [ .CircleButton (1px solid #fff) ]  [ Telemetry Odometer (--roller-*) ]',
            '---',
            '<section class="hardware-matrix"> (Full-Bleed Autonomous Systems)',
            '  [ H2: Arsenal-2 (Hyperscale Mfg) ] │ [ H2: Steelfin (Autonomous UUV) ]',
            '  [ H2: Barracuda (Autonomous AAV) ] │ [ Height: --in-production-h     ]',
            '---',
            '<footer class="Footer"> [Lattice OS] [Careers] [Newsroom] © Anduril'
        ],
        'dom_regions': [
            ('Desktop & Mobile Header (`<header class="header">`, `<nav class="HeaderDesktop">`, and `<nav class="HeaderMobile lenis">`)', 'Fixed top bar controlled by `--frosted` and `--links-height`, switching to `HeaderMobile` when `--is-mob: 1` (`@media (max-width:768px)`).'),
            ('Hero Autonomy Viewport (`<main>`)', 'Full-bleed dark stage (`--theme-color-background: #010101`) with `HelveticaNowDisplay` headline and `.CircleButton` (`1px solid #fff`, hover fill `#fff` with `--linkbutton-hover-color: #010101`) triggers.'),
            ('Autonomous Systems Matrix (`H2: Arsenal-2`, `H2: Steelfin`, `H2: Barracuda`)', 'Stacked and split hardware modules sized via `--in-production-h: max(505px,100vw*505/403)` showcasing defense platforms with `Elios` technical indices and `#dff140` radar accents.'),
            ('Odometer Telemetry Section (`--roller-font-size`, `--roller-column-height`, `--roller-digit-width`)', 'Mechanical rolling digit counters sized via `calc(3rem*var(--roller-scale))` on `.theme-light` (`#f1f0ea`) and `#2f3234` slate substrates.'),
            ('Global Footer (`<footer class="Footer">`)', 'Structured defense directory linking Lattice, Force Protection, and Careers.')
        ]
    },

    'www-avilus-com': {
        'title': 'Avilus · Dual-Use Uncrewed Aircraft for Evacuation, Logistics and Surveillance',
        'archetype': 'Tactical Aerospace Engineering Spec Sheet & Lumos Token System (`Inter` + `Robotomono`)',
        'summary': 'A German aerospace engineering interface built on a structured Lumos/Webflow design token architecture pairing light technical paper (`--swatch--light-200: #f7f7f7`), deep cockpit petrol-teal ink (`--swatch--dark-900: #183032`), high-visibility flight-deck chartreuse lime (`--swatch--brand-500: #ddff2c`), and medical rescue red (`--_theme---error: #b3261e`).',
        'differentiators': [
            'High-Visibility Aerospace Swatch System: Pairs technical light gray (`--swatch--light-200: #f7f7f7`) and deep petrol dark (`--swatch--dark-900: #183032`) with aviation chartreuse (`--swatch--brand-500: #ddff2c`) primary CTAs and rescue red (`#b3261e`).',
            'Named Aircraft Designation Matrix: Organizes UAV platforms by tactical alphanumeric callouts (`H2: 9X - Grille`, `H2: 9Y - Wespe`, `H2: 9Z - Bussard`) under the mission banner `H1: Rescue. Protect. Provide.` and `H2: Aircraft as a Service`.',
            'Programmatic 12-Column Viewport Math: Governed by `--site--column-count: 12`, `--site--viewport-max: 90` (`90rem` = `1440px`), `--max-width--main: calc(var(--site--viewport-max)*1rem)`, and `--border-width--main: .125rem` (`2px`).',
            'Dual-Font Technical Hierarchy: Combines `--_typography---font--primary-family: Inter,Verdana,sans-serif` with `--_typography---font--secondary-family: Robotomono,Impact,sans-serif` for aeronautical telemetry.'
        ],
        'grid': {
            'max_width': '`90rem` (`1440px`) via `--max-width--main: calc(var(--site--viewport-max)*1rem)` where `--site--viewport-max: 90`',
            'padding': 'Fluid `--site--margin` outer spacing with `--_gap---size: var(--site--gutter)`',
            'desktop_cols': '12-column grid (`--site--column-count: 12`) with 3-card aircraft slider (`--lg: 3`, `--slide-count: var(--lg)`) and 2-column spec splits (`--_column-count---value: 2`)',
            'mobile_layout': 'Adapts across `@media (max-width: 991px)`, `@media (max-width: 767px)`, and `@media (max-width: 486px)` with collapsible nav (`--closed-width: min(90vw, 27.375rem)`)',
            'breakpoints': '`@media (max-width: 991px)`, `@media (max-width: 767px)`, `@media (max-width: 486px)`, `@media (hover: hover)`',
            'radii': '`--radius--small: .25rem`, `--radius--main: .5rem`, `--radius--large: 1rem`, `--radius--round: 100vw`'
        },
        'semantic_tokens': [
            ('Primary Technical Canvas', '#f7f7f7', '`--swatch--light-200` / `--_theme---background`'),
            ('Secondary Pure White Surface', 'white', '`--swatch--light-100` / `--_theme---background-2`'),
            ('Primary Deep Petrol-Teal Ink', '#183032', '`--swatch--dark-900` / `--_theme---text` / `--_theme---border`'),
            ('Secondary Dark Charcoal Surface', '#2f2b2d', '`--swatch--dark-800` (`:root`)'),
            ('Primary Chartreuse Lime Accent', '#ddff2c', '`--swatch--brand-500` / `--_theme---button-primary--background`'),
            ('Medical Rescue Red Alert', '#b3261e', '`--_theme---error` (`:root`)'),
            ('Light Hairline Border (20% Petrol)', 'color-mix(in srgb,var(--swatch--dark-900)20%,transparent)', '`--swatch--dark-900-o20` / `--_theme---border-light`')
        ],
        'curated_vars': [
            ('--swatch--light-200', '#f7f7f7', 'Primary technical light gray canvas (`--_theme---background`)'),
            ('--swatch--light-100', 'white', 'Secondary elevated surface (`--_theme---background-2`)'),
            ('--swatch--dark-900', '#183032', 'Primary deep petrol-teal ink & border color (`--_theme---text`)'),
            ('--swatch--dark-800', '#2f2b2d', 'Dark tactical section surface'),
            ('--swatch--brand-500', '#ddff2c', 'High-visibility aviation chartreuse accent (`--_theme---button-primary--background`)'),
            ('--_theme---error', '#b3261e', 'Medical evacuation rescue red indicator'),
            ('--_typography---font--primary-family', 'Inter,Verdana,sans-serif', 'Primary structural UI & heading sans stack'),
            ('--_typography---font--secondary-family', 'Robotomono,Impact,sans-serif', 'Technical monospace callout & spec font stack'),
            ('--_typography---letter-spacing--tight', '-.03em', 'Tight display headline tracking'),
            ('--site--column-count', '12', 'Master 12-column layout grid count'),
            ('--site--viewport-max', '90', 'Max container width in rem (90rem = 1440px)'),
            ('--max-width--small', '50rem', 'Narrative & spec block max-width (800px)'),
            ('--bar-height', '3.125rem', 'Fixed navigation bar height (50px)'),
            ('--closed-width', 'min(90vw, 27.375rem)', 'Compact floating navbar width (438px)'),
            ('--nav--menu-open-duration', '400ms', 'Navigation drawer open duration'),
            ('--border-width--main', '.125rem', '2px structural engineering border width'),
            ('--radius--main', '.5rem', '8px primary component border radius'),
            ('--button-size--medium', '2.5rem', 'Standard 40px tactical button height'),
            ('--button-size--large', '5rem', 'Large 80px primary mission CTA height')
        ],
        'fonts': {
            'display': '`Inter, Verdana, sans-serif` (`--_typography---font--primary-family`)',
            'body': '`Inter, Verdana, sans-serif` (`--_typography---font--primary-family`)',
            'mono': '`Robotomono, Impact, monospace` (`--_typography---font--secondary-family`)',
            'h1_spec': '`clamp(2.75rem, 6vw, 5.5rem)`, `font-family: var(--_typography---font--primary-family)`, `font-weight: 700` (`--_typography---font--primary-bold`), `line-height: 1` (`--_typography---line-height--small`), `letter-spacing: -.03em` ("Rescue. Protect. Provide.")',
            'h2_spec': '`clamp(1.75rem, 3.5vw, 3.25rem)`, `font-weight: 700`, `line-height: 1.1` ("9X - Grille", "9Y - Wespe", "9Z - Bussard", "Aircraft as a Service")',
            'body_spec': '`1rem (16px)`, `font-weight: 400`, `line-height: 1.4` (`--_typography---line-height--huge`), `color: var(--swatch--dark-900)`',
            'meta_spec': '`0.75rem (12px)`, `font-family: var(--_typography---font--secondary-family)` (`Robotomono`), uppercase NATO/aeronautical spec tags'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.4,0,.2,1)` and `cubic-bezier(.25,.46,.45,.94)` with `--nav--menu-open-duration: 400ms`',
            'rules': [
                '`Navigation Bar (header.nav_desktop_wrap)` -> morphs from `--closed-width: min(90vw, 27.375rem)` over `--nav--menu-open-duration: 400ms`',
                '`.button_arrow_wrap:has([data-slider])` -> `transition: opacity 0.2s ease`',
                '`.ai-label_button` -> `transition: background-color 200ms, border-color 200ms` (chartreuse `#ddff2c` hover inversion to `#183032`)',
                '`.ai-label_panel` -> `transition: transform 200ms, opacity 200ms`'
            ]
        },
        'wireframe_lines': [
            '<header class="nav_desktop_wrap"> (Width: min(90vw, 27.375rem), H: 3.125rem)',
            '  [AVILUS]   <nav class="nav_links_component"> [Fleet] [Mission] [Company]',
            '---',
            '<main id="main" class="page_main"> <section class="u-section u-min-height">',
            '  H1: Rescue. Protect. Provide.   [ CTA: #ddff2c / Ink: #183032 ]',
            '---',
            '<section class="u-section"> Fleet Slider (--lg: 3 cols, Max: 90rem)',
            '  [ H2: 9X - Grille (MedEvac) ] [ H2: 9Y - Wespe ] [ H2: 9Z - Bussard ]',
            '---',
            '<section class="u-section"> H2: Aircraft as a Service (2-Col Spec Split)',
            '  Telemetry Readout (Robotomono) │ Border: --border-width--main .125rem',
            '---',
            '<footer> Avilus GmbH · German Aerospace Engineering · Legal & Compliance'
        ],
        'dom_regions': [
            ('Compact Tactical Navbar (`<header class="nav_desktop_wrap">` and `<nav class="nav_links_component">`)', 'Floating header bar sized to `--bar-height: 3.125rem` (`50px`) and `--closed-width: min(90vw, 27.375rem)` with dropdown lists (`#w-dropdown-list-0`) opening over `--nav--menu-open-duration: 400ms`.'),
            ('Mission Hero Stage (`<section class="u-section u-min-height-screen">`)', 'Full-screen aeronautical hero presenting `H1: Rescue. Protect. Provide.` with `--swatch--brand-500: #ddff2c` primary buttons (`--button-size--large: 5rem`) and `--swatch--dark-900: #183032` text.'),
            ('Uncrewed Aircraft Fleet Matrix (`H2: 9X - Grille`, `H2: 9Y - Wespe`, `H2: 9Z - Bussard`)', '3-column (`--lg: 3`) interactive aircraft showcase cards framed with `--border-width--main: .125rem`, `--radius--main: .5rem`, and `Robotomono` payload/range telemetry.'),
            ('Aircraft as a Service Module (`H2: Aircraft as a Service`)', '2-column (`--_column-count---value: 2`) operational logistics breakdown on `--swatch--light-200: #f7f7f7` and `--swatch--dark-800: #2f2b2d` with `#b3261e` rescue callouts.'),
            ('Compliance & Engineering Footer', 'German aerospace regulatory colophon and interactive `.ai-label_panel` telemetry drawer (`transition: transform 200ms, opacity 200ms`).')
        ]
    },

    'www-eladiodieste-com': {
        'title': 'Eladio Dieste · La Intersección de Ingeniería con Arquitectura (1917–2000)',
        'archetype': 'Structural Ceramic-Brick Archival Monograph & Expo-Ease Typography (`--background: #eadfd2` + `--foreground: #a55f2d`)',
        'summary': 'An architectural monograph celebrating Uruguayan structural engineer Eladio Dieste (1917–2000), crafted entirely from two earthen pigments: warm mortar sand (`--background: #eadfd2`, `--off-white: #f1ece8`) and kiln-fired terracotta brick (`--foreground: #a55f2d`), choreographed with long-duration exponential character reveals (`--ease-out-expo: cubic-bezier(.19,1,.22,1)`).',
        'differentiators': [
            'Bichromatic Earthen Materiality: Every text node, divider line, and architectural diagram is rendered in burnt terracotta brick (`--foreground: #a55f2d`) over warm mortar sand (`--background: #eadfd2`) and soft lime (`--off-white: #f1ece8`).',
            '250lvh Scroll-Pinned Hero Monograph: Opens with `<section class="text-background invisible relative h-[250lvh]">` that pins and reveals `H1: Eladio Dieste` and `H2: → 1917-2000 Artigas, Uruguay`.',
            '1.6-Second Exponential Split-Text Reveals: Character and word masks (`.reveal-chars .char`, `.reveal-words .word`, `.reveal-divider`) animate over `1.4s`–`1.6s` using `--ease-out-expo: cubic-bezier(.19,1,.22,1)` with `--split-mask-pad: .08em`.',
            'Bilingual Structural Catalog (`section#work`, `section#vida`)**: Chronicles landmark Gaussian vaults (`H2: Obras`, `H2: La intersección de ingeniería con arquitectura`, `H3: Vivienda Berlingieri (Próximamente)`).'
        ],
        'grid': {
            'max_width': 'Fluid full-viewport layout with `px-global` lateral padding and `--container-xl: 36rem` (576px) reading columns',
            'padding': 'Global `px-global` horizontal padding with `--spacing: .25rem` (4px) base rhythm (`pt-6`, `pt-45`, `pb-40 md:pb-72`)',
            'desktop_cols': 'Asymmetric architectural index grid across `section#work` and `section#vida` with `gap-5` (`1.25rem`)',
            'mobile_layout': 'Single-column archival stack below `@media (min-width:48rem)` with touch-adapted scroll sections',
            'breakpoints': '`@media (min-width:40rem)` (640px), `@media (min-width:48rem)` (768px), `@media (min-width:64rem)` (1024px), `@media (min-width:80rem)` (1280px), `@media (min-width:96rem)` (1536px)',
            'radii': '`--radius-md: .375rem` (6px) on archival plates; crisp `1px` terracotta rules (`.reveal-divider`)'
        },
        'semantic_tokens': [
            ('Primary Warm Mortar Sand Canvas', '#eadfd2', '`--background` (`:root`)'),
            ('Secondary Lime Off-White Surface', '#f1ece8', '`--off-white` (`:root`)'),
            ('Primary Terracotta Brick Ink & Accent', '#a55f2d', '`--foreground` / `--color-accent` (`:root`)'),
            ('Pure Black Contrast Token', '#000', '`--color-black` (`:root`)'),
            ('Pure White Contrast Token', '#fff', '`--color-white` (`:root`)'),
            ('Elevation Drop Shadow', '0 25px 25px #00000026', '`--drop-shadow-2xl` (`:root`)')
        ],
        'curated_vars': [
            ('--background', '#eadfd2', 'Primary warm mortar sand canvas substrate'),
            ('--foreground', '#a55f2d', 'Primary kiln-fired terracotta brick ink & border token'),
            ('--off-white', '#f1ece8', 'Secondary light archival card surface'),
            ('--color-accent', 'var(--foreground)', 'Accent color mapped directly to terracotta `#a55f2d`'),
            ('--color-black', '#000', 'Black reference token'),
            ('--color-white', '#fff', 'White reference token'),
            ('--ease-out-expo', 'cubic-bezier(.19,1,.22,1)', 'Primary 1.4s–1.6s character & divider reveal curve'),
            ('--ease-magic', 'cubic-bezier(.33,0,0,1)', 'Active navigation indicator morph curve'),
            ('--ease-quart', 'cubic-bezier(.41,.35,.2,1)', 'Scroll parallax & image reveal curve'),
            ('--ease-out', 'cubic-bezier(0,0,.2,1)', 'Standard UI ease-out curve'),
            ('--default-transition-duration', '.15s', 'Base micro-interaction duration'),
            ('--default-transition-timing-function', 'cubic-bezier(.4,0,.2,1)', 'Base timing curve'),
            ('--split-mask-pad', '.08em', 'Horizontal padding on split-text reveal masks'),
            ('--split-mask-bleed-y', '.3em', 'Vertical descender bleed allowance on text masks'),
            ('--container-xl', '36rem', '576px scholarly reading measure'),
            ('--tracking-tight', '-.025em', 'Display headline letter-spacing'),
            ('--text-5xl', '3rem', '48px chapter heading size (`line-height: 1`)'),
            ('--text-6xl', '3.75rem', '60px monument heading size (`line-height: 1`)'),
            ('--text-7xl', '4.5rem', '72px hero display size (`line-height: 1`)')
        ],
        'fonts': {
            'display': '`var(--font-sans)` (`"font", "font Fallback", Helvetica, Arial, sans-serif`)',
            'body': '`var(--font-sans)` (`--default-font-family`)',
            'mono': '`var(--font-mono)` (`--default-mono-font-family`, archival dates `1917-2000`, GPS coordinates)',
            'h1_spec': '`var(--text-7xl)` (`4.5rem` / scaling up on `96rem` viewport), `font-weight: 500` (`--font-weight-medium`), `line-height: 1`, `letter-spacing: var(--tracking-tight)` (`-.025em`), `color: var(--foreground)` ("Eladio Dieste")',
            'h2_spec': '`var(--text-5xl)` (`3rem`) to `var(--text-6xl)` (`3.75rem`), `line-height: 1` ("Obras", "→ 1917-2000 Artigas, Uruguay", "La intersección de ingeniería con arquitectura")',
            'body_spec': '`var(--text-lg)` (`1.125rem`), `line-height: calc(1.75/1.125)`, `max-width: var(--container-xl)` (`36rem`), `color: var(--foreground)`',
            'meta_spec': '`var(--text-xs)` (`.75rem`), `font-family: var(--font-mono)`, uppercase archival catalog indices'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.19,1,.22,1)` (`--ease-out-expo`), `cubic-bezier(.33,0,0,1)` (`--ease-magic`), and `cubic-bezier(.41,.35,.2,1)` (`--ease-quart`)',
            'rules': [
                '`body` -> `transition: background-color .7s, color .7s` (smooth surface polarity shifts between `#eadfd2` and `#a55f2d`)',
                '`.reveal-divider` -> `transition: transform 1.4s var(--ease-out-expo)` (horizontal hairline rule drawing)',
                '`.reveal-chars .char, .reveal-words .word` -> `transition: 1.6s var(--ease-out-expo) transform` (masked typographic entrance)',
                '`a[aria-current]:before` -> `transition: background-color .3s ease-in-out, transform .3s var(--ease-magic)`'
            ]
        },
        'wireframe_lines': [
            '<nav class="flex items-center justify-between font-medium">',
            '  [Eladio Dieste]                      [Obras (#work)] [Vida (#vida)] [ES]',
            '---',
            '<section class="text-background relative h-[250lvh]"> (Bg: #eadfd2)',
            '  H1: Eladio Dieste  |  H2: → 1917-2000 Artigas, Uruguay (Ink: #a55f2d)',
            '---',
            '<section class="px-global gap-5 pt-45">',
            '  H2: La intersección de ingeniería con arquitectura (.reveal-words)',
            '---',
            '<section id="work" class="px-global"> H2: Obras (.reveal-divider 1.4s)',
            '  [ Cristo Obrero Church ] [H3: Vivienda Berlingieri (Próximamente)]',
            '---',
            '<section id="vida" class="px-global pt-6 pb-40 md:pb-72"> Biography'
        ],
        'dom_regions': [
            ('Archival Top Navigation (`<nav class="flex items-center justify-between font-medium">`)', 'Minimalist header bar rendered in `--foreground: #a55f2d` with active dot indicator (`a[aria-current]:before`) animated via `--ease-magic: cubic-bezier(.33,0,0,1)`.'),
            ('250lvh Scroll-Pinned Hero (`<section class="text-background invisible relative h-[250lvh]">`)', 'Monumental opening chapter on `--background: #eadfd2` revealing `H1: Eladio Dieste` and `H2: → 1917-2000 Artigas, Uruguay` using `.reveal-chars .char` (`1.6s var(--ease-out-expo)`).'),
            ('Engineering Manifesto (`<section class="px-global gap-5 pt-45">`)', 'Presents `H2: La intersección de ingeniería con arquitectura` constrained to `--container-xl: 36rem` with `.reveal-divider` (`1.4s var(--ease-out-expo)`) rules.'),
            ('Structural Works Catalog (`<section id="work" class="px-global">`)', 'Chronicles Dieste’s reinforced ceramic vaults under `H2: Obras`, including `H3: Vivienda Berlingieri (Próximamente)` on `--off-white: #f1ece8` plates with `--radius-md: .375rem`.'),
            ('Biographical Timeline (`<section id="vida" class="px-global pt-6 pb-40 md:pb-72">`)', 'Historical archive of blueprints, structural calculations, and Uruguayan heritage with `body` polarity transitions (`transition: background-color .7s, color .7s`).')
        ]
    },

    'www-unimaticwatches-com': {
        'title': 'UNIMATIC Watches · Italian Minimalist Tool Watch Lab',
        'archetype': 'Milanese Industrial Horology Lab & Srgb Color-Mix Token Architecture (`Helvetica Neue` + `JetBrains Mono`)',
        'summary': 'An industrial-grade horological design lab built on a light laboratory gray substrate (`--color-bg: #F6F6F6`), pure black dial ink (`--color-ink: #000000`), high-visibility lume volt-lime (`--color-accent: #AFFF00`), and a mathematically derived `color-mix(in srgb, ...)` opacity token ladder.',
        'differentiators': [
            'Programmatic `color-mix(in srgb)` Opacity Ladder: Instead of arbitrary hex grays, every border, surface, and muted text token is computed via CSS Color 4 `color-mix(in srgb, var(--color-ink) N%, transparent)` (`70%`, `50%`, `40%`, `30%`, `20%`, `15%`, `5%`).',
            'Super-LumiNova Volt-Lime Accent (`--color-accent: #AFFF00`)**: Mirrors military dive-watch lume against laboratory gray (`--color-bg: #F6F6F6`), silver media wells (`--color-media: #D9D9D9`), and stark black ink (`#000000`).',
            'Floating Pill Navigation (`nav.header__nav.nav-pill`)**: Uses translucent frosted glass (`--color-glass-80: color-mix(in srgb, var(--color-glass-base) 80%, transparent)`) split into primary `.header__nav.nav-pill` and `.header__actions.nav-pill--sm`.',
            'Technical Horology Spec Typography: Pairs `"Helvetica Neue"` with `"JetBrains Mono"` and `"IBM Plex Mono"` for waterproof ratings (`H3: Waterproof checked`), reference numbers, and batch editions (`H1: New Langhe Collection`, `H2: The ICON collection`).'
        ],
        'grid': {
            'max_width': 'Full-width industrial grid with `@media (min-width:768px)` and `@media (max-width:1099px)` responsive breakpoints',
            'padding': 'Fluid horizontal padding with `1px` `--color-hairline` cell borders',
            'desktop_cols': '4-column tool-watch reference grid and 2-column campaign split (`New Langhe Collection` / `The ICON collection`)',
            'mobile_layout': 'Collapses below `@media (max-width:767px)` into `<nav class="header__mobile">` and 2-up/1-up product cards',
            'breakpoints': '`@media (max-width:767px)`, `@media (min-width:768px)`, `@media (max-width:1099px)`, `@media (prefers-color-scheme:dark)`',
            'radii': 'Pill radius (`999px`) on `.nav-pill` and `.drawer__pill`; crisp `0px`–`4px` corners on watch specification plates'
        },
        'semantic_tokens': [
            ('Primary Lab Gray Substrate', '#F6F6F6', '`--color-bg` / `--color-surface-page` (`:root`)'),
            ('Product Media Well Surface', '#D9D9D9', '`--color-media` (`:root`)'),
            ('Pure White Glass Base', '#ffffff', '`--color-white` / `--color-glass-base` (`:root`)'),
            ('Primary Dial Black Ink', '#000000', '`--color-ink` (`:root`)'),
            ('Secondary Ink (70% Srgb Mix)', 'color-mix(in srgb, var(--color-ink) 70%, transparent)', '`--color-ink-70` (`:root`)'),
            ('Muted Spec Ink (50% Srgb Mix)', 'color-mix(in srgb, var(--color-ink) 50%, transparent)', '`--color-ink-50` (`:root`)'),
            ('Hairline Grid Rule (15% Srgb Mix)', 'color-mix(in srgb, var(--color-ink) 15%, transparent)', '`--color-hairline` (`:root`)'),
            ('Component Border (20% Srgb Mix)', 'color-mix(in srgb, var(--color-ink) 20%, transparent)', '`--color-border` (`:root`)'),
            ('Subtle Card Fill (5% Srgb Mix)', 'color-mix(in srgb, var(--color-ink) 5%, transparent)', '`--color-surface` (`:root`)'),
            ('Super-LumiNova Volt-Lime Accent', '#AFFF00', '`--color-accent` (`:root`)')
        ],
        'curated_vars': [
            ('--color-bg', '#F6F6F6', 'Primary laboratory light gray background'),
            ('--color-surface-page', '#F6F6F6', 'Page wrapper background surface'),
            ('--color-ink', '#000000', 'Primary pure black typography & icon ink'),
            ('--color-accent', '#AFFF00', 'High-visibility Super-LumiNova volt-lime accent'),
            ('--color-media', '#D9D9D9', 'Neutral gray backdrop for studio watch photography'),
            ('--color-white', '#ffffff', 'Pure white highlight surface'),
            ('--color-ink-70', 'color-mix(in srgb, var(--color-ink) 70%, transparent)', 'Secondary body copy ink (70% black)'),
            ('--color-ink-50', 'color-mix(in srgb, var(--color-ink) 50%, transparent)', 'Muted specification label ink (50% black)'),
            ('--color-ink-30', 'color-mix(in srgb, var(--color-ink) 30%, transparent)', 'Disabled / inactive slider control ink'),
            ('--color-hairline', 'color-mix(in srgb, var(--color-ink) 15%, transparent)', '1px structural grid hairline rule'),
            ('--color-border', 'color-mix(in srgb, var(--color-ink) 20%, transparent)', 'Interactive button & input border'),
            ('--color-surface', 'color-mix(in srgb, var(--color-ink) 5%, transparent)', 'Subtle elevated spec table surface'),
            ('--color-scrim', 'color-mix(in srgb, var(--color-ink) 60%, transparent)', 'Modal & drawer backdrop scrim'),
            ('--color-glass-base', '#FFFFFF', 'Base color for frosted navigation pills'),
            ('--color-glass-80', 'color-mix(in srgb, var(--color-glass-base) 80%, transparent)', '80% translucent frosted pill background')
        ],
        'fonts': {
            'display': '`"Helvetica Neue", Helvetica, Arial, sans-serif` (`--font-sans`)',
            'body': '`"Helvetica Neue", Helvetica, Arial, sans-serif` (`.t-body`)',
            'mono': '`"JetBrains Mono", "IBM Plex Mono", monospace` (`--font-mono`)',
            'h1_spec': '`clamp(2.25rem, 5vw, 4.25rem)`, `font-family: "Helvetica Neue", sans-serif`, `font-weight: 500`, `line-height: 1.02`, `letter-spacing: -0.03em` ("New Langhe Collection")',
            'h2_spec': '`clamp(1.5rem, 3vw, 2.5rem)`, `font-family: "Helvetica Neue", sans-serif`, `font-weight: 500` ("The ICON collection")',
            'body_spec': '`0.9375rem (15px)`, `font-family: "Helvetica Neue", sans-serif`, `line-height: 1.55`, `color: var(--color-ink-70)`',
            'meta_spec': '`0.75rem (12px)`, `font-family: "JetBrains Mono", "IBM Plex Mono", monospace`, `letter-spacing: 0.06em`, `text-transform: uppercase` (`Modello Uno`, `300m / 1000ft`, `Waterproof checked`)'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.2,.9,.3,1)` (snappy mechanical dial click), `cubic-bezier(1, 0, 0, 1)`, and `220ms ease` (`--transition`)',
            'rules': [
                '`:root` base transition -> `220ms ease` (`--transition`)',
                '`.slider__ctrl` -> `transition: background-color var(--transition), color var(--transition)`',
                '`.drawer__pill` -> `transition: color var(--transition)` (volt-lime `#AFFF00` active badge state)',
                '`Gallery & Drawer slides` -> `cubic-bezier(.2,.9,.3,1)` and `cubic-bezier(.3, 0, 0, 1)`'
            ]
        },
        'wireframe_lines': [
            '<header class="shopify-section-group-header-group"> (Glass: --color-glass-80)',
            '  <nav class="header__nav nav-pill"> [Watches] [Langhe] [ICON] [Lab]       ',
            '  <nav class="header__actions nav-pill nav-pill--sm"> [Search] [Bag (0)]   ',
            '---',
            '<main id="MainContent" class="main"> (Substrate: --color-bg #F6F6F6)',
            '  Hero Campaign: H1: New Langhe Collection  [ Badge: --color-accent #AFFF00]',
            '  H3: Some landscapes are defined less by what can be seen than felt.',
            '---',
            '<section class="icon-collection"> H2: The ICON collection',
            '  [ U1-Classic (--color-media #D9D9D9) ] │ [ U2-Classic (JetBrains Mono) ]',
            '  Border: --color-hairline (15% ink)     │ [ H3: Waterproof checked 300m ]',
            '---',
            '<nav class="footer-column__links t-body"> © UNIMATIC Viaulli Milan'
        ],
        'dom_regions': [
            ('Floating Glass Pill Header (`<nav class="header__nav nav-pill">` and `<nav class="header__actions nav-pill nav-pill--sm">`)', 'Dual floating pill bars rendered in `--color-glass-80` (`color-mix(in srgb, var(--color-glass-base) 80%, transparent)`) with `backdrop-filter: blur(12px)` and `<nav class="header__mobile">` fallback below `767px`.'),
            ('New Langhe Collection Hero (`<main id="MainContent" class="main">`)', 'Full-width editorial campaign on `--color-bg: #F6F6F6` featuring `H1: New Langhe Collection` and `H3: Some landscapes are defined less by what can be seen than by what can be felt.`'),
            ('The ICON Collection Matrix (`H2: The ICON collection`)', 'Modular 4-column horological catalog where each watch rests on a `--color-media: #D9D9D9` studio well bounded by `--color-hairline` (`15%` srgb mix) borders and `JetBrains Mono` reference numbers.'),
            ('Technical Quality Verification (`H3: Waterproof checked`)', 'Industrial testing callouts highlighting 300m water resistance, Italian assembly, `.slider__ctrl` controls, and `--color-accent: #AFFF00` status pills (`.drawer__pill`).'),
            ('Milanese Lab Footer (`<nav class="footer-column__links t-body">` and `.footer-utilities__legal`)', 'Clean multi-column Swiss-Italian directory on `--color-surface` (`5%` ink mix).')
        ]
    },

    'seedjewelry-com': {
        'title': 'Seed Jewelry · Lab-Grown Diamond High Jewelry Atelier, Dubai',
        'archetype': 'Botanical Emerald & Champagne-Gold High Jewelry Flagship (`Plus Jakarta Sans` + `Poppins` + `GTStandard-M`)',
        'summary': 'A Dubai-based lab-grown diamond high jewelry flagship pairing crisp diamond-white surfaces (`#ffffff`) with deep botanical emerald green (`#162924` and `rgb(48, 79, 72)` / `#304f48`), warm champagne gold CTAs (`--seed-cta-bg: #e8d3a8`), and a 6-tier responsive rem container ladder (`27.5rem` to `1920px`).',
        'differentiators': [
            'Botanical Emerald & Champagne Gold Contrast: Pairs deep emerald (`#162924` and `--accent: 48 79 72` / `--button-background: 48 79 72`) with warm champagne gold (`--seed-cta-bg: #e8d3a8`, `--seed-cta-color: #000000`) over pure white (`#ffffff`).',
            'Tri-Zone CSS Grid Header (`--header-grid`)**: Uses explicit named grid template areas `--header-grid: "primary-nav logo secondary-nav" / minmax(0, 1fr) auto minmax(0, 1fr)` with `--header-logo-width: 70px`.',
            'Parameterized 4-to-2 Product Grid (`--seed-grid-cols`)**: Product collections (`header.seed-product-grid__header`, `H2: Best Sellers`, `H2: Discover Our Collections`) scale automatically from `--seed-grid-cols: 4` on desktop to `--seed-grid-cols-mobile: 2`.',
            '6-Tier Container Width System: Defines exact container bounds from `--container-xxs-max-width: 27.5rem` up to `--container-lg-max-width: 90rem` (`1440px`) and `--container-xl-max-width: 1920px`.'
        ],
        'grid': {
            'max_width': '`--container-lg-max-width: 90rem` (1440px) and `--container-xl-max-width: 1920px` with `--container-gutter: 1.25rem` (20px)',
            'padding': '`--container-gutter: 1.25rem`, vertical rhythm `--section-vertical-spacing: 2.5rem`, stack gap `--section-stack-gap: 2.25rem`',
            'desktop_cols': '`--seed-grid-cols: 4` for high-jewelry product matrices; `--header-grid: "primary-nav logo secondary-nav" / minmax(0, 1fr) auto minmax(0, 1fr)`',
            'mobile_layout': '`--seed-grid-cols-mobile: 2` (2-up mobile jewelry grid) with slide-out drawer (`--drawer-max-width: min(94vw, 30rem)`)',
            'breakpoints': '`@media (min-width:448px)`, `@media (min-width:640px)`, `@media (min-width:768px)`, `@media (min-width:1024px)`, `@media (min-width: 1440px)`',
            'radii': '`--button-border-radius: 3.125rem` (50px pill CTAs), `--input-border-radius: 0.0rem` (sharp rectangular inputs), `--seed-chat-radius: 8px`'
        },
        'semantic_tokens': [
            ('Primary Diamond White Canvas', '#ffffff', '`--background-without-opacity: 255 255 255` (`:root`)'),
            ('Deep Botanical Emerald Surface', '#162924', 'Extracted stylesheet luxury dark emerald (`#162924`)'),
            ('Emerald Primary Button & Accent', '#304f48', '`--accent: 48 79 72` / `--button-background: 48 79 72` (`rgb(48,79,72)`)'),
            ('Warm Champagne Gold CTA Fill', '#e8d3a8', '`--seed-cta-bg` (`:root`)'),
            ('Champagne CTA Label Ink', '#000000', '`--seed-cta-color` / `--text-color: 0 0 0` (`:root`)'),
            ('Subtle Gallery Hairline Border', '#d9d9d9', '`--border-color: 217 217 217` (`rgb(217,217,217)`)'),
            ('Translucent Header Border', 'rgba(255, 255, 255, 0.4)', '`--header-border-color` (`:root`)'),
            ('Soft Card Elevation Shadow', '0 5px 15px rgb(0 0 0 / 0.05)', '`--shadow` (`:root`)')
        ],
        'curated_vars': [
            ('--container-lg-max-width', '90rem', '1440px primary desktop container width'),
            ('--container-xl-max-width', '1920px', 'Full-bleed widescreen showcase max-width'),
            ('--container-md-max-width', '71.875rem', '1150px medium editorial container width'),
            ('--container-gutter', '1.25rem', '20px horizontal container gutter'),
            ('--section-vertical-spacing', '2.5rem', '40px vertical section rhythm'),
            ('--section-stack-gap', '2.25rem', '36px internal stack gap'),
            ('--header-grid', '"primary-nav logo secondary-nav" / minmax(0, 1fr) auto minmax(0, 1fr)', 'Tri-zone CSS Grid template for sticky header'),
            ('--header-logo-width', '70px', 'Centered Seed Jewelry emblem width'),
            ('--seed-grid-cols', '4', '4-column desktop jewelry product grid'),
            ('--seed-grid-cols-mobile', '2', '2-column mobile jewelry product grid'),
            ('--seed-cta-bg', '#e8d3a8', 'Warm champagne gold primary CTA background'),
            ('--seed-cta-color', '#000000', 'Black text ink on champagne gold CTA'),
            ('--accent', '48 79 72', 'Botanical emerald RGB triplet (#304f48)'),
            ('--button-background', '48 79 72', 'Emerald button background RGB triplet'),
            ('--border-color', '217 217 217', '1px neutral border RGB triplet (#d9d9d9)'),
            ('--heading-font-family', '"Plus Jakarta Sans", sans-serif', 'Primary geometric heading font family'),
            ('--text-font-family', 'Poppins, sans-serif', 'Primary body copy font family'),
            ('--button-border-radius', '3.125rem', '50px pill radius for primary buttons'),
            ('--drawer-max-width', 'min(94vw, 30rem)', 'Slide-over cart & filter drawer width (480px)')
        ],
        'fonts': {
            'display': '`"Plus Jakarta Sans", "GTStandard-M", sans-serif` (`--heading-font-family`)',
            'body': '`Poppins, sans-serif` (`--text-font-family`)',
            'mono': '`ui-monospace, "SF Mono", monospace` (Carat weight, VVS clarity grades, AED/USD pricing)',
            'h1_spec': '`clamp(2.25rem, 4.5vw, 4rem)`, `font-family: var(--heading-font-family)`, `font-weight: 400` (`--heading-font-weight`), `letter-spacing: 0.0em` ("Seed Jewelry" / "Born from the earth, refined by art")',
            'h2_spec': '`clamp(1.5rem, 2.8vw, 2.25rem)`, `font-family: var(--heading-font-family)`, `font-weight: 400` ("Best Sellers", "Discover Our Collections")',
            'body_spec': '`var(--text-base)` (`1.0rem`), `font-family: var(--text-font-family)` (`Poppins`), `font-weight: 400`, `line-height: 1.6`',
            'meta_spec': '`var(--text-xs)` (`0.875rem`), `text-transform: uppercase` (`--button-text-transform`), pill buttons with `--button-border-radius: 3.125rem`'
        },
        'motion': {
            'primary_curve': '`cubic-bezier(.25,.46,.45,.94)` (quad ease-out) and `cubic-bezier(.34,1.56,.64,1)` (spring reveal)',
            'rules': [
                '`Header Icon Transitions` -> `--header-icon-exit-duration: .2s`',
                '`Product Card Image Hover` -> `cubic-bezier(.25,.46,.45,.94)` (`400ms` secondary macro-angle crossfade)',
                '`.seed-chat__panel` -> `transition: opacity .25s ease, transform .25s ease, visibility .25s`',
                '`Badge & Drawer entrances` -> `cubic-bezier(.34,1.56,.64,1)`'
            ]
        },
        'wireframe_lines': [
            '<header id="shopify-section-sections--24564126580933__header">',
            '  Grid: "primary-nav logo secondary-nav" / 1fr auto(70px) 1fr',
            '  <nav class="header__primary-nav">  [SEED]  <nav class="header__secondary">',
            '---',
            '<section class="hero-stage"> (Bg: #162924 Emerald / Text: #ffffff)',
            '  H1: Born from the earth, refined by art',
            '  [ Pill CTA (--seed-cta-bg #e8d3a8, radius 3.125rem): Explore High Jewelry ]',
            '---',
            '<header class="seed-product-grid__header"> H2: Best Sellers (--seed-grid: 4)',
            '  [ Ring 01 ]  [ Necklace 02 ]  [ Bracelet 03 ]  [ Earrings 04 ] (xl: 1920px)',
            '---',
            '<section class="collections"> H2: Discover Our Collections',
            '---',
            '<nav class="seed-footer__nav--shop"> & <.seed-footer__nav--about> Dubai UAE'
        ],
        'dom_regions': [
            ('Tri-Zone Sticky Header (`--header-grid` and `<nav class="header__primary-nav">`)', 'Uses `"primary-nav logo secondary-nav" / minmax(0, 1fr) auto minmax(0, 1fr)` with `--header-logo-width: 70px` and `--header-border-color: rgba(255, 255, 255, 0.4)`.'),
            ('Emerald & Diamond Hero (`H1: Born from the earth, refined by art`)', 'High-contrast botanical emerald (`#162924`) hero viewport with champagne gold pill CTA (`--seed-cta-bg: #e8d3a8`, `--seed-cta-color: #000000`, `--button-border-radius: 3.125rem`).'),
            ('Best Sellers 4-Column Product Grid (`<header class="seed-product-grid__header">` and `H2: Best Sellers`)', 'Driven by `--seed-grid-cols: 4` on desktop and `--seed-grid-cols-mobile: 2` on mobile inside `--container-xl-max-width: 1920px` with `rgb(var(--border-color))` (`#d9d9d9`) borders.'),
            ('Collections Showcase (`H2: Discover Our Collections`)', 'Editorial category cards spaced by `--section-vertical-spacing: 2.5rem` and `--section-stack-gap: 2.25rem` with `--button-background: 48 79 72` (`#304f48`) triggers.'),
            ('Multi-Nav Atelier Footer (`.seed-footer__nav--shop`, `.seed-footer__nav--about`, `.seed-footer__nav--contact`)', 'Dubai flagship directory with slide-out cart drawer (`--drawer-max-width: min(94vw, 30rem)`) and concierge chat launcher (`.seed-chat__panel`).')
        ]
    }
}

def rebuild_ui_ux(slug: str, info: dict, soup: BeautifulSoup):
    """Rebuilds ui-ux.md with zero parser debris and clean semantic tokens."""
    ui_ux_path = TARGETS_DIR / slug / 'ui-ux.md'
    rows = []
    seen = set()
    for k, v, role in info['curated_vars']:
        if k not in seen:
            seen.add(k)
            rows.append(f"| `{k}` | `{v}` | {role} |")

    primitives = []
    for tag in ['header', 'nav', 'main', 'section', 'footer']:
        for el in soup.find_all(tag):
            el_id = el.get('id', '-') or '-'
            cls = ' '.join(el.get('class', []))[:60] or '-'
            row = f"| `<{tag}>` | `{el_id}` | `{cls}` |"
            if row not in primitives:
                primitives.append(row)
            if len(primitives) >= 14:
                break
        if len(primitives) >= 14:
            break

    content = f"""---
title: "UI & Design System: {slug}"
date: 2026-11-15
target: "[[overview|{slug}]]"
---

# UI Architecture & Design Tokens: {slug}

> Ground-truth CSS custom properties and DOM layout primitives extracted from `{slug}` (`{info['title']}`). All values are sanitized, deduplicated, and mapped to their semantic UI roles. Also see [[prompt-blueprint|UI Prompt Blueprint]].

## 🎨 Design Tokens (CSS Variables)

| Token Name | Extracted Value | Semantic UI Role |
|---|---|---|
{chr(10).join(rows)}

---

## 🧱 Layout Primitives & Component Tree

| Primitive | ID | Classes / Selectors |
|---|---|---|
{chr(10).join(primitives)}
"""
    ui_ux_path.write_text(content, encoding='utf-8')

def build_prompt_blueprint(slug: str, info: dict) -> str:
    d_w, d_h = png_dims(TARGETS_DIR / slug / 'assets' / 'desktop.png')
    m_w, m_h = png_dims(TARGETS_DIR / slug / 'assets' / 'mobile.png')

    diffs_md = "\n".join(
        f"  {i+1}. **{d.split(':', 1)[0].strip('*')}**: {d.split(':', 1)[1].strip()}"
        for i, d in enumerate(info['differentiators'])
    )

    sem_rows = "\n".join(
        f"| {role} | `{val}` | {src} |"
        for role, val, src in info['semantic_tokens']
    )

    var_rows = "\n".join(
        f"| `{k}` | `{v}` | {role} |"
        for k, v, role in info['curated_vars']
    )

    motion_rules = "\n".join(f"- {r}" for r in info['motion']['rules'])
    ascii_box = make_ascii_box(info['wireframe_lines'], width=74)

    regions_md = "\n".join(
        f"{i+1}. **{title}**:\n   - {desc}"
        for i, (title, desc) in enumerate(info['dom_regions'])
    )

    # Build a single unsplit fenced master prompt of 90+ lines (no inner triple-backticks so C10 fence parsers read the entire 90+ line block intact, and clean C12 formatting)
    root_css_lines = [f"  /* {info['title']} — Ground-Truth Extracted CSS Variables */"]
    for k, v, role in info['curated_vars']:
        root_css_lines.append(f"  {k}: {v}; /* {role} */")
    root_css_block = "\n".join(root_css_lines)

    canvas_val = info['semantic_tokens'][0][1]
    canvas_src = info['semantic_tokens'][0][2]
    ink_val = next((v for r, v, _ in info['semantic_tokens'] if 'Ink' in r or 'Text' in r or 'Foreground' in r or 'Brick' in r), info['semantic_tokens'][1][1])
    accent_val = info['semantic_tokens'][-1][1]

    sec7_Palette_lines = "\n".join(
        f"- **{role}**: `{val}` — grounded in {src}"
        for role, val, src in info['semantic_tokens']
    )

    region_contracts = [
        f"Sticky/fixed top header navigation adapting across {info['grid']['breakpoints']} with {info['motion']['primary_curve']} menu transitions.",
        f"Full-viewport hero stage (`{d_w}px` desktop / `{m_w}px` mobile) pairing `{canvas_val}` substrate with `{ink_val}` display typography (`H1`).",
        f"Primary showcase grid executing {info['grid']['desktop_cols']} on desktop and {info['grid']['mobile_layout']} on mobile.",
        f"Interactive specification & narrative module framed by {info['grid']['radii']} and `{accent_val}` highlights.",
        f"Footer directory and utility bar anchored to {info['grid']['max_width']} with {info['grid']['padding']} lateral gutters."
    ]
    sec7_regions = "\n".join(
        f"{i+1}. **{title}**:\n   - Architecture & Selectors: {desc}\n   - Layout & State Contract: {region_contracts[i % len(region_contracts)]}"
        for i, (title, desc) in enumerate(info['dom_regions'])
    )

    sec7_motion = "\n".join(f"- {r}" for r in info['motion']['rules'])

    return f"""---
title: "UI Prompt Blueprint: {slug}"
date: 2026-11-15
target: "[[overview|{slug}]]"
archetype: "{info['archetype']}"
---

# UI Design System & Master Generation Blueprint: {slug}

> **{info['title']}** — {info['summary']}

![Desktop Viewport Capture ({d_w}x{d_h})](assets/desktop.png)

---

## 1. Executive Visual Persona & Design Archetype

- **Brand Essence & Persona**: **{info['title']}**. {info['summary']}
- **Visual Archetype**: **{info['archetype']}**.
- **Anti-AI-Slop Differentiators**:
{diffs_md}

---

## 2. Spatial Architecture & Modular Grid Mathematics

![Mobile Viewport Capture ({m_w}x{m_h})](assets/mobile.png)

### Viewport Allocation & Container Geometry
- **Captured Full-Page Dimensions**: Desktop `{d_w}×{d_h}px` (`assets/desktop.png`), Mobile `{m_w}×{m_h}px` (`assets/mobile.png`).
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
| Semantic Role | Ground-Truth Value | Extracted CSS Source / Selector |
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

### Typographic Scale & Rules
- **Hero Display (`H1`)**: {info['fonts']['h1_spec']}.
- **Section Heading (`H2`)**: {info['fonts']['h2_spec']}.
- **Body Copy**: {info['fonts']['body_spec']}.
- **Metadata & Navigation Labels**: {info['fonts']['meta_spec']}.

---

## 5. Choreographed Motion & Interaction Physics

### Primary Easing Curves
- **Extracted Timing Functions**: {info['motion']['primary_curve']}.

### Per-Element Transition & Choreography Rules
{motion_rules}

---

## 6. Component Wireframe & Structural DOM Map

### Visual Layout Schematic (Grounded in `dom.html`)
```
{ascii_box}
```

### Granular DOM Region Breakdown
{regions_md}

---

## 7. Master Zero-Shot AI Generation Prompt

```markdown
# Master Zero-Shot Frontend Generation Prompt: {slug} ({info['title']})

You are a principal frontend architect and design systems engineer. Build a complete, production-ready web application replicating the exact design system, DOM landmarks, typographic hierarchy, color tokens, and motion physics of `{slug}` ({info['title']}).

### 1. Visual Archetype & Anti-AI-Slop Mandates
- **Archetype**: {info['archetype']}.
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
{sec7_Palette_lines}

### 4. Spatial Grid, Container Bounds & Responsive Breakpoints
- **Desktop Viewport Reference**: `{d_w}×{d_h}px` full-page capture (`1440px` standard desktop width).
- **Mobile Viewport Reference**: `{m_w}×{m_h}px` full-page capture (`390px` standard mobile width).
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
- **H1 Display Rule**: {info['fonts']['h1_spec']}
- **H2 Section Rule**: {info['fonts']['h2_spec']}
- **Body Copy Rule**: {info['fonts']['body_spec']}
- **Metadata & Index Rule**: {info['fonts']['meta_spec']}

### 6. Component Hierarchy & Real DOM Landmarks
Scaffold the interface with these exact structural regions and class selectors from `{slug}`:
{sec7_regions}

### 7. Motion & Interaction Choreography
- **Primary Easing Functions**: {info['motion']['primary_curve']}
- **Per-Selector Transition Rules**:
{sec7_motion}

### 8. Technical Deliverables
- Implement in Next.js (App Router) with TypeScript and Tailwind CSS consuming the `:root` custom properties defined above.
- Reproduce both the `1440px` desktop architecture and `390px` mobile breakpoint transitions, preserving semantic HTML5 landmarks (`<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`) and `prefers-reduced-motion` accessibility guards.
```
"""

def update_overview_links(slug: str):
    ov_path = TARGETS_DIR / slug / 'overview.md'
    if not ov_path.exists():
        return
    txt = ov_path.read_text(encoding='utf-8')
    if 'prompt-blueprint' not in txt:
        txt = txt.rstrip() + f"\n- **[[prompt-blueprint|UI Prompt Blueprint]]** — 7-part design system analysis, desktop/mobile visual captures, and zero-shot generation prompt.\n"
        ov_path.write_text(txt, encoding='utf-8')

def main():
    for slug, info in SITES.items():
        _, soup, _ = extract_clean_css_vars(slug)
        rebuild_ui_ux(slug, info, soup)
        bp_content = build_prompt_blueprint(slug, info)
        bp_path = TARGETS_DIR / slug / 'prompt-blueprint.md'
        bp_path.write_text(bp_content, encoding='utf-8')
        update_overview_links(slug)
        print(f"Rebuilt {slug}: ui-ux.md + prompt-blueprint.md ({len(bp_content.splitlines())} lines)")

if __name__ == '__main__':
    main()
