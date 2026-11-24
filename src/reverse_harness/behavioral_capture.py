"""
Full Top-to-Bottom Behavioral Crawl & WebP Capture Engine (behavioral_journey_v2).

Implements:
- Dual-mode Desktop (1440x900) scroll driver:
  - Native scrolling (~850px steps up to true footer or 14,400px cap)
  - Automatic fallback to real mouse.wheel(0, 650) + ArrowDown bursts for scroll-jacked /
    Lenis / GSAP / 100vh WebGL sites where scrollHeight <= 1.2*vh or scrollY fails to advance.
- Visual pixel-diff deduplication (downscaled mean abs pixel diff < 3.5), keeping up to 8
  distinct keyframes (desktop-step-01.webp .. desktop-step-0N.webp), ALWAYS preserving Hero (0%)
  and deepest reached frame / Footer (100%).
- Labeled vertical filmstrip stitching (desktop-journey.webp 1280px wide, mobile-journey.webp
  390px wide, subpage-0N-<slug>.webp 1080px wide), strictly clamped < 16383px WebP limit.
- Safe non-destructive UI probe suite (Nav/Menu, Hover Physics CSS diffs, Interactive Controls,
  Theme Toggle). Never submits forms, logs in, or triggers payments.
- Multi-page internal crawl (up to 3 distinct templates: listing, detail, about) with SPA vs MPA
  transition detection and 3-keyframe filmstrip per subpage.
- Mobile condensed journey (390x844, Top / Mid / Bottom).
- WebP output (quality=82, method=4), legacy PNG cleanup, honest block detection, hard 160s
  per-target deadline, and raw/<slug>/behavior.json telemetry.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Browser, BrowserContext, Page

from reverse_harness.live_capture import (
    COMPUTED_STYLES_JS,
    DESKTOP_MIN_HEIGHT,
    DESKTOP_UA,
    DESKTOP_WIDTH,
    MOBILE_MIN_HEIGHT,
    MOBILE_UA,
    MOBILE_WIDTH,
    PRELOADER_CHECK_JS,
    RAF_PAUSE_INIT_SCRIPT,
    BrowserPool,
    _dismiss_consent_all_frames,
    _dismiss_promo_modals,
    _sanitize_asset_filename,
    atomic_write_bytes,
    atomic_write_text,
    compute_png_visual_metrics,
    detect_block_or_error,
    detect_preloader_text,
    is_visual_unhealthy_for_tier2,
    visual_entropy_score,
)

WEBP_QUALITY = 82
WEBP_METHOD = 4
WEBP_MAX_HEIGHT = 16383

DESKTOP_STEP_PX = 850
DESKTOP_MAX_SCROLL_Y = 14400
DESKTOP_MAX_KEYFRAMES = 8
DEDUP_DIFF_THRESHOLD = 3.5
STATE_DIFF_THRESHOLD = 4.0

FILMSTRIP_BAR_HEIGHT = 28
FILMSTRIP_BG_COLOR = (17, 19, 24)      # #111318
FILMSTRIP_BORDER_COLOR = (42, 47, 58)  # #2a2f3a
FILMSTRIP_TEXT_COLOR = (245, 247, 250)

SKIP_ROUTE_RE = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|signup|sign-up|register|account|auth|oauth|sso|logout|signout|"
    r"cart|add-to-cart|wishlist|search|checkout|basket|bag|pay|payment|billing|order|subscribe|"
    r"privacy|privacy-policy|terms|terms-of-service|terms-of-use|cookie|cookies|cookie-policy|"
    r"legal|gdpr|imprint|impressum|disclaimer|accessibility|sitemap|feed|rss|wp-admin|wp-login)(?:/|$|\?)",
    re.I,
)
SKIP_QUERY_RE = re.compile(r"(?:^|&)(?:add-to-cart|logout|action=)", re.I)
SKIP_EXT_RE = re.compile(
    r"\.(?:pdf|zip|tar|gz|rar|7z|png|jpe?g|gif|webp|svg|ico|mp4|mov|webm|mp3|wav|dmg|exe|apk|csv|xml|json)$",
    re.I,
)
LISTING_ROUTE_RE = re.compile(
    r"(?:^|/)(?:work|works|projects|portfolio|products|shop|store|collection|collections|catalog|"
    r"services|solutions|cases|case-studies|archive|blog|journal|news|articles|editorial|features|pricing|exhibitions)(?:/|$)",
    re.I,
)
DETAIL_ROUTE_RE = re.compile(
    r"(?:^|/)(?:work|works|projects|project|case|case-study|cases|portfolio|product|products|item|article|blog|journal|story|stories|exhibition)/[^/]+",
    re.I,
)
ABOUT_ROUTE_RE = re.compile(
    r"(?:^|/)(?:about|about-us|studio|agency|company|team|people|contact|info|information|manifesto|culture|careers|jobs|who-we-are|story|profile)(?:/|$)",
    re.I,
)

# ---------------------------------------------------------------------------
# JavaScript Telemetry & Probe Snippets
# ---------------------------------------------------------------------------

STEP_TELEMETRY_JS = """
() => {
  try {
    const vh = window.innerHeight || 900;
    const vw = window.innerWidth || 1440;
    const scrollY = Math.round(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0);
    const scrollHeight = Math.max(
      document.body ? document.body.scrollHeight : 0,
      document.documentElement ? document.documentElement.scrollHeight : 0,
      vh
    );
    const maxScroll = Math.max(1, scrollHeight - vh);
    const scrollPct = Math.min(100, Math.max(0, Math.round((scrollY / maxScroll) * 100)));

    // Find best visible heading / landmark in the current viewport
    let activeLandmark = '';
    const candidates = Array.from(document.querySelectorAll('h1, h2, h3, section[id], footer, main > section, [data-section]')).slice(0, 120);
    let bestScore = -1e9;
    for (const el of candidates) {
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) continue;
      if (r.bottom < 0 || r.top > vh) continue;
      const tag = (el.tagName || '').toLowerCase();
      let label = '';
      if (tag === 'h1' || tag === 'h2' || tag === 'h3') {
        label = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      } else if (tag === 'footer') {
        if (scrollPct < 88) continue;
        label = 'Footer';
      } else if (el.id && !/^(?:section|block|div|row|col|panel|container|wrapper|el|item|node)[-_]?\\d+$/i.test(el.id)) {
        label = '#' + el.id;
      } else if (el.getAttribute('aria-label')) {
        label = el.getAttribute('aria-label');
      }
      if (!label) continue;
      const tagWeight = tag === 'h1' ? 100 : (tag === 'h2' ? 85 : (tag === 'h3' ? 70 : (tag === 'footer' ? 60 : 30)));
      const posWeight = -Math.abs(r.top - vh * 0.28) * 0.15;
      const score = tagWeight + posWeight;
      if (score > bestScore) {
        bestScore = score;
        activeLandmark = label.slice(0, 56);
      }
    }
    if (!activeLandmark) {
      if (scrollY <= 20) activeLandmark = 'Hero';
      else if (scrollPct >= 95) activeLandmark = 'Footer / Bottom';
      else activeLandmark = `Section @ ${scrollPct}%`;
    }

    // Count visible sticky/fixed elements
    let stickyFixedCount = 0;
    const allEls = Array.from(document.querySelectorAll('header, nav, div, aside, section, button, footer')).slice(0, 220);
    for (const el of allEls) {
      const cs = window.getComputedStyle(el);
      if (!cs || (cs.position !== 'fixed' && cs.position !== 'sticky')) continue;
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.05) continue;
      const r = el.getBoundingClientRect();
      if (r.width >= 24 && r.height >= 16 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw) {
        stickyFixedCount += 1;
      }
    }

    return {
      scrollY,
      scrollHeight,
      innerHeight: vh,
      scrollPct,
      activeLandmark,
      stickyFixedCount
    };
  } catch (e) {
    return {
      scrollY: 0,
      scrollHeight: 900,
      innerHeight: 900,
      scrollPct: 0,
      activeLandmark: 'Viewport',
      stickyFixedCount: 0,
      error: String(e)
    };
  }
}
"""

SCROLL_LIB_DETECT_JS = """
() => {
  try {
    const libs = [];
    const htmlCls = (document.documentElement.className || '').toLowerCase();
    const bodyCls = (document.body ? (document.body.className || '') : '').toLowerCase();
    const allScripts = Array.from(document.querySelectorAll('script[src]')).map(s => (s.src || '').toLowerCase()).join(' ');

    if (window.lenis || window.Lenis || htmlCls.includes('lenis') || bodyCls.includes('lenis') || document.querySelector('[data-lenis-prevent]') || allScripts.includes('lenis')) {
      libs.push('Lenis');
    }
    if (window.gsap || window.ScrollTrigger || document.querySelector('.pin-spacer') || allScripts.includes('gsap') || allScripts.includes('scrolltrigger')) {
      libs.push('GSAP/ScrollTrigger');
    }
    if (window.LocomotiveScroll || htmlCls.includes('has-scroll-smooth') || document.querySelector('[data-scroll-container]') || allScripts.includes('locomotive')) {
      libs.push('Locomotive Scroll');
    }
    if (document.querySelector('[data-framer-appear-id], [data-projection-id]') || allScripts.includes('framer-motion')) {
      libs.push('Framer Motion');
    }
    if (window.fullpage_api || document.querySelector('.fp-section, .fullpage-wrapper')) {
      libs.push('fullPage.js');
    }
    if (document.querySelector('[data-scrollbar]')) {
      libs.push('Smooth Scrollbar');
    }
    const canvases = document.querySelectorAll('canvas');
    if (canvases.length > 0) {
      libs.push('WebGL/Canvas');
    }
    return { libs, canvasCount: canvases.length };
  } catch (e) {
    return { libs: [], canvasCount: 0 };
  }
}
"""

NAV_TRIGGER_FIND_JS = """
() => {
  try {
    const FORBIDDEN = /captcha|turnstile|login|sign[- ]?in|sign[- ]?up|checkout|cart|basket|pay|cookie|consent|privacy|search|language|lang|close|dismiss/i;
    const isVis = (el) => {
      if (!el) return false;
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) return false;
      const r = el.getBoundingClientRect();
      return r.width >= 16 && r.height >= 16 && r.width <= 260 && r.height <= 120 && r.top >= -5 && r.top < 220 && r.left >= 0 && r.right <= (window.innerWidth || 1440) + 5;
    };
    const sels = [
      'button[aria-label*="menu" i]',
      'button[aria-label*="nav" i]',
      'header button[aria-expanded="false"]',
      'nav button[aria-expanded="false"]',
      'button.menu-toggle',
      'button.nav-toggle',
      '.menu-toggle',
      '.nav-toggle',
      '.hamburger',
      '[data-menu-toggle]',
      'header [role="button"]'
    ];
    for (const sel of sels) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 15);
      for (const el of els) {
        if (!isVis(el)) continue;
        const meta = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).slice(0, 160);
        if (FORBIDDEN.test(meta)) continue;
        const r = el.getBoundingClientRect();
        return {
          found: true,
          selector: sel,
          label: (el.getAttribute('aria-label') || el.innerText || sel).replace(/\\s+/g, ' ').trim().slice(0, 50),
          cx: Math.round(r.left + r.width / 2),
          cy: Math.round(r.top + r.height / 2)
        };
      }
    }
    return { found: false };
  } catch (e) {
    return { found: false, error: String(e) };
  }
}
"""

VISIBLE_NAV_LINKS_JS = """
() => {
  try {
    const out = [];
    const seen = new Set();
    const vw = window.innerWidth || 1440;
    const vh = window.innerHeight || 900;
    for (const a of Array.from(document.querySelectorAll('a[href]')).slice(0, 120)) {
      const cs = window.getComputedStyle(a);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) continue;
      const r = a.getBoundingClientRect();
      if (r.width < 10 || r.height < 10 || r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
      const txt = (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 48);
      const href = a.getAttribute('href') || '';
      const key = txt + '|' + href;
      if (txt && !seen.has(key)) {
        seen.add(key);
        out.push({ text: txt, href: href.slice(0, 120) });
        if (out.length >= 25) break;
      }
    }
    return out;
  } catch (e) {
    return [];
  }
}
"""

HOVER_CANDIDATES_JS = """
() => {
  try {
    const FORBIDDEN = /captcha|turnstile|cookie|consent|privacy|close|dismiss/i;
    const vh = window.innerHeight || 900;
    const vw = window.innerWidth || 1440;
    const readStyle = (el) => {
      const cs = window.getComputedStyle(el);
      return {
        transform: cs.transform,
        color: cs.color,
        backgroundColor: cs.backgroundColor,
        borderColor: cs.borderColor,
        opacity: cs.opacity,
        boxShadow: cs.boxShadow,
        transitionDuration: cs.transitionDuration,
        transitionTimingFunction: cs.transitionTimingFunction
      };
    };
    const isVis = (el, minW, minH) => {
      if (!el) return false;
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) return false;
      const r = el.getBoundingClientRect();
      return r.width >= minW && r.height >= minH && r.top >= 10 && r.bottom <= vh - 10 && r.left >= 5 && r.right <= vw - 5;
    };

    const results = [];
    window.__HOVER_TARGETS = [];

    // 1. Primary CTA
    const ctaSels = ['main a[href]', 'main button', 'header a[href]', 'header button', '.cta', '.btn', 'a.button'];
    for (const sel of ctaSels) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 20);
      let picked = null;
      for (const el of els) {
        if (!isVis(el, 48, 22)) continue;
        const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!txt || txt.length > 48 || FORBIDDEN.test(txt)) continue;
        picked = el;
        break;
      }
      if (picked) {
        const idx = window.__HOVER_TARGETS.length;
        window.__HOVER_TARGETS.push(picked);
        const r = picked.getBoundingClientRect();
        results.push({
          idx,
          role: 'primary_cta',
          tag: picked.tagName.toLowerCase(),
          label: (picked.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 48),
          cx: Math.round(r.left + r.width / 2),
          cy: Math.round(r.top + r.height / 2),
          before: readStyle(picked)
        });
        break;
      }
    }

    // 2. First Card / Project item
    const cardSels = ['article', '.card', '[class*="card" i]', '[class*="project" i]', '[class*="work" i] a', 'main section a[href]'];
    for (const sel of cardSels) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 20);
      let picked = null;
      for (const el of els) {
        if (window.__HOVER_TARGETS.includes(el)) continue;
        if (!isVis(el, 120, 80)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > vw * 0.92 && r.height > vh * 0.85) continue;
        picked = el;
        break;
      }
      if (picked) {
        const idx = window.__HOVER_TARGETS.length;
        window.__HOVER_TARGETS.push(picked);
        const r = picked.getBoundingClientRect();
        results.push({
          idx,
          role: 'first_card',
          tag: picked.tagName.toLowerCase(),
          label: (picked.innerText || picked.className || sel).toString().replace(/\\s+/g, ' ').trim().slice(0, 48),
          cx: Math.round(r.left + r.width / 2),
          cy: Math.round(r.top + r.height / 2),
          before: readStyle(picked)
        });
        break;
      }
    }

    return results;
  } catch (e) {
    return [];
  }
}
"""

READ_HOVER_STYLE_JS = """
(idx) => {
  try {
    const el = (window.__HOVER_TARGETS || [])[idx];
    if (!el) return null;
    const cs = window.getComputedStyle(el);
    return {
      transform: cs.transform,
      color: cs.color,
      backgroundColor: cs.backgroundColor,
      borderColor: cs.borderColor,
      opacity: cs.opacity,
      boxShadow: cs.boxShadow,
      transitionDuration: cs.transitionDuration,
      transitionTimingFunction: cs.transitionTimingFunction
    };
  } catch (e) {
    return null;
  }
}
"""

INTERACTIVE_CONTROL_FIND_JS = """
() => {
  try {
    const FORBIDDEN = /\\b(?:captcha|turnstile|login|sign[- ]?in|submit|checkout|cart|pay|delete|cookie|consent|privacy|menu|nav)\\b/i;
    const isVis = (el) => {
      if (!el) return false;
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) return false;
      const r = el.getBoundingClientRect();
      return r.width >= 16 && r.height >= 16;
    };

    const groups = [
      ['tab', '[role="tab"]:not([aria-selected="true"])'],
      ['accordion', 'details:not([open]) > summary, main button[aria-expanded="false"]'],
      ['carousel_next', 'button[aria-label*="next" i], button[aria-label*="slide" i], .swiper-button-next, .slick-next']
    ];
    for (const [kind, sel] of groups) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 15);
      for (const el of els) {
        if (!isVis(el)) continue;
        if (el.closest('header, nav, form')) continue;
        const meta = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).slice(0, 160);
        if (FORBIDDEN.test(meta)) continue;
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
        const r = el.getBoundingClientRect();
        if (r.width < 16 || r.height < 16) continue;
        window.__INTERACTIVE_TARGET = el;
        return {
          found: true,
          kind,
          label: (el.getAttribute('aria-label') || el.innerText || kind).replace(/\\s+/g, ' ').trim().slice(0, 56),
          beforeAttr: el.getAttribute('aria-expanded') || el.getAttribute('aria-selected') || '',
          cx: Math.round(r.left + r.width / 2),
          cy: Math.round(r.top + r.height / 2)
        };
      }
    }
    return { found: false };
  } catch (e) {
    return { found: false, error: String(e) };
  }
}
"""

THEME_TOGGLE_FIND_JS = """
() => {
  try {
    const sels = [
      'button[aria-label*="theme" i]',
      'button[aria-label*="dark" i]',
      'button[aria-label*="light" i]',
      '[data-theme-toggle]',
      '.theme-toggle',
      '#theme-toggle'
    ];
    for (const sel of sels) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const cs = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (cs.display === 'none' || cs.visibility === 'hidden' || r.width < 12 || r.height < 12) continue;
      window.__THEME_TOGGLE = el;
      const bcs = window.getComputedStyle(document.body);
      return {
        found: true,
        label: (el.getAttribute('aria-label') || el.innerText || sel).replace(/\\s+/g, ' ').trim().slice(0, 48),
        beforeBg: bcs.backgroundColor,
        beforeColor: bcs.color,
        beforeHtmlClass: (document.documentElement.className || '').slice(0, 80)
      };
    }
    return { found: false };
  } catch (e) {
    return { found: false };
  }
}
"""

EXTRACT_INTERNAL_LINKS_JS = """
() => {
  try {
    const out = [];
    const seen = new Set();
    const origin = window.location.origin;
    const sources = [
      ['header', document.querySelectorAll('header a[href]')],
      ['nav', document.querySelectorAll('nav a[href]')],
      ['cta', document.querySelectorAll('main a[href], section a[href]')]
    ];
    for (const [src, nodes] of sources) {
      for (const a of Array.from(nodes).slice(0, 80)) {
        const href = a.getAttribute('href') || '';
        if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) continue;
        let abs = '';
        try { abs = new URL(href, window.location.href).href; } catch (_) { continue; }
        if (!abs.startsWith(origin)) continue;
        const clean = abs.split('#')[0];
        if (seen.has(clean)) continue;
        seen.add(clean);
        const r = a.getBoundingClientRect();
        const cs = window.getComputedStyle(a);
        const visible = cs && cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity || '1') > 0.1 && r.width > 10 && r.height > 10;
        out.push({
          url: clean,
          href,
          text: (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
          source: src,
          visible,
          cx: visible ? Math.round(r.left + r.width / 2) : 0,
          cy: visible ? Math.round(r.top + r.height / 2) : 0
        });
        if (out.length >= 100) break;
      }
      if (out.length >= 100) break;
    }
    return out;
  } catch (e) {
    return [];
  }
}
"""


# ---------------------------------------------------------------------------
# Pure Python Helpers (Unit-Testable)
# ---------------------------------------------------------------------------

def _load_label_font(size: int = 14) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def encode_webp_bytes(img: Image.Image, quality: int = WEBP_QUALITY, method: int = WEBP_METHOD) -> bytes:
    """Encode a PIL Image to WebP bytes."""
    rgb = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
    buf = io.BytesIO()
    rgb.save(buf, format="WEBP", quality=quality, method=method)
    return buf.getvalue()


def compute_frame_diff(img_a: Image.Image, img_b: Image.Image) -> float:
    """Compute downscaled (160x100) mean absolute pixel difference (0..255) between two frames."""
    a = np.asarray(img_a.convert("RGB").resize((160, 100), Image.Resampling.BILINEAR), dtype=np.float32)
    b = np.asarray(img_b.convert("RGB").resize((160, 100), Image.Resampling.BILINEAR), dtype=np.float32)
    return round(float(np.abs(a - b).mean()), 3)


def deduplicate_frames(
    frames: List[Dict[str, Any]],
    threshold: float = DEDUP_DIFF_THRESHOLD,
    max_frames: int = DESKTOP_MAX_KEYFRAMES,
) -> List[Dict[str, Any]]:
    """
    Deduplicate visually near-identical frames (diff < threshold), keeping up to max_frames
    distinct keyframes, ALWAYS preserving Hero (index 0) and deepest reached / Footer (index -1)
    when the page has >= 2 visually distinct states.
    """
    if len(frames) <= 1:
        return list(frames)

    kept: List[Dict[str, Any]] = [frames[0]]
    for frame in frames[1:-1]:
        diff = compute_frame_diff(kept[-1]["image"], frame["image"])
        if diff >= threshold:
            frame_copy = dict(frame)
            frame_copy["diff_from_prev"] = diff
            kept.append(frame_copy)

    last_frame = frames[-1]
    last_diff = compute_frame_diff(kept[-1]["image"], last_frame["image"])
    if last_diff >= threshold:
        lf_copy = dict(last_frame)
        lf_copy["diff_from_prev"] = last_diff
        kept.append(lf_copy)
    elif len(kept) > 1:
        # Replace final kept frame with true deepest frame so 100% / Footer telemetry is preserved
        lf_copy = dict(last_frame)
        lf_copy["diff_from_prev"] = compute_frame_diff(kept[-2]["image"], last_frame["image"])
        kept[-1] = lf_copy

    if len(kept) > max_frames:
        # Evenly sample intermediate frames while ALWAYS preserving Hero [0] and Footer [-1]
        inner_slots = max_frames - 2
        inner_pool = kept[1:-1]
        if inner_slots <= 0:
            kept = [kept[0], kept[-1]]
        else:
            indices = [
                round(i * (len(inner_pool) - 1) / max(1, inner_slots - 1))
                if inner_slots > 1 else len(inner_pool) // 2
                for i in range(inner_slots)
            ]
            # Ensure strictly unique sorted indices
            uniq_idx = sorted(set(indices))
            kept = [kept[0]] + [inner_pool[i] for i in uniq_idx] + [kept[-1]]

    return kept


def build_labeled_filmstrip(
    labeled_frames: List[Tuple[Image.Image, str]],
    target_width: int = 1280,
    frame_height: int = 800,
    bar_height: int = FILMSTRIP_BAR_HEIGHT,
) -> Image.Image:
    """
    Stitch a vertical filmstrip with dark #111318 divider bars and crisp white labels.
    Automatically clamps total height strictly below the 16,383px WebP dimension limit.
    """
    if not labeled_frames:
        return Image.new("RGB", (target_width, bar_height + frame_height), FILMSTRIP_BG_COLOR)

    n = len(labeled_frames)
    max_total_h = WEBP_MAX_HEIGHT - 83  # 16,300px safety margin
    eff_frame_h = frame_height
    if n * (bar_height + eff_frame_h) > max_total_h:
        eff_frame_h = max(120, (max_total_h // n) - bar_height)

    total_h = n * (bar_height + eff_frame_h)
    canvas = Image.new("RGB", (target_width, total_h), FILMSTRIP_BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    font_size = 11 if target_width <= 420 else 14
    font = _load_label_font(font_size)

    y_cursor = 0
    for img, label in labeled_frames:
        # Draw dark header bar
        draw.rectangle([0, y_cursor, target_width - 1, y_cursor + bar_height - 1], fill=FILMSTRIP_BG_COLOR)
        draw.line([(0, y_cursor + bar_height - 1), (target_width - 1, y_cursor + bar_height - 1)], fill=FILMSTRIP_BORDER_COLOR, width=1)
        safe_label = re.sub(r"\s+", " ", label or "").strip()
        max_chars = max(28, (target_width - 20) // (7 if font_size <= 11 else 9))
        if len(safe_label) > max_chars:
            safe_label = safe_label[: max_chars - 3].rstrip() + "..."
        draw.text((10, y_cursor + (bar_height - font_size) // 2 - 1), safe_label, fill=FILMSTRIP_TEXT_COLOR, font=font)
        y_cursor += bar_height

        # Paste resized frame
        resized = img.convert("RGB").resize((target_width, eff_frame_h), Image.Resampling.LANCZOS)
        canvas.paste(resized, (0, y_cursor))
        y_cursor += eff_frame_h

    return canvas


def decide_scroll_driver_mode(
    scroll_height: int,
    viewport_height: int,
    observed_scroll_y_after_jump: int,
) -> str:
    """
    Pure helper deciding 'native' vs 'wheel' scroll driver mode:
    Switch to 'wheel' if scrollHeight <= 1.2 * viewportHeight or scrollY fails to advance.
    """
    if scroll_height <= int(viewport_height * 1.2):
        return "wheel"
    if observed_scroll_y_after_jump < 100:
        return "wheel"
    return "native"


def should_fallback_to_wheel_mid_scroll(
    frame_diff: float,
    observed_scroll_y: int,
    scroll_height: int,
    viewport_height: int = DESKTOP_MIN_HEIGHT,
    threshold: float = DEDUP_DIFF_THRESHOLD,
) -> bool:
    """
    C1: Detect mid-scroll native scrollTo stall (e.g. Lenis/GSAP pin after first jump).
    Returns True if frame is near-identical to previous while scrollY is still well above footer.
    """
    if scroll_height <= int(viewport_height * 1.2):
        return False
    remaining = (scroll_height - viewport_height) - observed_scroll_y
    return frame_diff < threshold and remaining > 120


def compute_native_scroll_targets(
    scroll_height: int,
    viewport_height: int = DESKTOP_MIN_HEIGHT,
    step_px: int = DESKTOP_STEP_PX,
    max_scroll_y: int = DESKTOP_MAX_SCROLL_Y,
) -> List[int]:
    """Compute native scrollY offsets starting after y=0 up to true footer or max_scroll_y cap."""
    effective_max = max(0, min(scroll_height - viewport_height, max_scroll_y))
    if effective_max <= 0:
        return []
    targets: List[int] = []
    y = step_px
    while y < effective_max:
        targets.append(y)
        y += step_px
    if not targets or targets[-1] != effective_max:
        targets.append(effective_max)
    return targets


def _slugify_route(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "page"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", path.lower()).strip("-")
    return slug[:36] or "page"


def select_internal_subpages(
    home_url: str,
    raw_links: List[Dict[str, Any]],
    max_pages: int = 3,
) -> List[Dict[str, Any]]:
    """
    Filter and prioritize up to 3 distinct internal same-origin templates:
    (1) listing/work/projects/products/services
    (2) detail/case-study/article/item
    (3) about/studio/contact/info
    Skips auth, checkout, legal, downloads, external origins, and homepage duplicates.
    """
    home_parsed = urlparse(home_url)
    home_origin = f"{home_parsed.scheme}://{home_parsed.netloc}".lower()
    home_path = (home_parsed.path or "/").rstrip("/") or "/"

    seen_paths = {home_path}
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "listing": [],
        "detail": [],
        "about": [],
        "other": [],
    }

    for link in raw_links:
        raw_u = (link.get("url") or "").strip()
        if not raw_u:
            continue
        p = urlparse(raw_u)
        origin = f"{p.scheme}://{p.netloc}".lower()
        if origin != home_origin:
            continue
        path = (p.path or "/").rstrip("/") or "/"
        if path in seen_paths:
            continue
        if SKIP_ROUTE_RE.search(path) or SKIP_EXT_RE.search(path) or SKIP_QUERY_RE.search(p.query or ""):
            continue
        seen_paths.add(path)

        if DETAIL_ROUTE_RE.search(path):
            cat = "detail"
        elif LISTING_ROUTE_RE.search(path):
            cat = "listing"
        elif ABOUT_ROUTE_RE.search(path):
            cat = "about"
        else:
            cat = "other"

        entry = dict(link)
        entry["path"] = path
        entry["category"] = cat
        entry["route_slug"] = _slugify_route(raw_u)
        buckets[cat].append(entry)

    selected: List[Dict[str, Any]] = []
    for cat in ("listing", "detail", "about"):
        if buckets[cat] and len(selected) < max_pages:
            # Prefer header/nav visible links first
            buckets[cat].sort(key=lambda x: (0 if x.get("source") in ("header", "nav") else 1, 0 if x.get("visible") else 1))
            selected.append(buckets[cat][0])

    # Backfill if fewer than max_pages
    for cat in ("listing", "detail", "about", "other"):
        for item in buckets[cat]:
            if len(selected) >= max_pages:
                break
            if all(s["path"] != item["path"] for s in selected):
                selected.append(item)

    return selected[:max_pages]


# ---------------------------------------------------------------------------
# Async Playwright Behavioral Capture Helpers
# ---------------------------------------------------------------------------

async def _capture_viewport_pil(
    page: Page,
    tier: str = "tier1_fast",
    timeout_s: float = 6.0,
    cdp: Any = None,
) -> Optional[Image.Image]:
    """Capture the current viewport (not full_page) as a PIL RGB Image via fast CDP Page.captureScreenshot."""
    paused = False
    if tier == "tier2_webgl":
        try:
            await asyncio.wait_for(
                page.evaluate("() => { window.__PAUSE_RAF = true; }"),
                timeout=1.2,
            )
            paused = True
            await asyncio.sleep(0.08)
        except Exception:
            pass

    own_cdp = False
    session = cdp
    try:
        if session is None:
            session = await asyncio.wait_for(page.context.new_cdp_session(page), timeout=2.0)
            own_cdp = True
        res = await asyncio.wait_for(
            session.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True},
            ),
            timeout=timeout_s,
        )
        raw = base64.b64decode(res.get("data") or "")
        if len(raw) >= 100:
            with Image.open(io.BytesIO(raw)) as im:
                return im.convert("RGB")
    except Exception:
        # Fallback to Playwright page.screenshot
        try:
            raw = await asyncio.wait_for(
                page.screenshot(full_page=False, type="png", animations="disabled", timeout=int(timeout_s * 1000)),
                timeout=timeout_s + 1.5,
            )
            if raw and len(raw) >= 100:
                with Image.open(io.BytesIO(raw)) as im:
                    return im.convert("RGB")
        except Exception:
            return None
    finally:
        if own_cdp and session is not None:
            try:
                await session.detach()
            except Exception:
                pass
        if paused:
            try:
                await asyncio.wait_for(
                    page.evaluate("() => { if (window.__RESUME_RAF) window.__RESUME_RAF(); }"),
                    timeout=1.2,
                )
            except Exception:
                pass
    return None


async def _read_step_telemetry(page: Page) -> Dict[str, Any]:
    try:
        res = await asyncio.wait_for(page.evaluate(STEP_TELEMETRY_JS), timeout=2.5)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    return {
        "scrollY": 0,
        "scrollHeight": DESKTOP_MIN_HEIGHT,
        "innerHeight": DESKTOP_MIN_HEIGHT,
        "scrollPct": 0,
        "activeLandmark": "Viewport",
        "stickyFixedCount": 0,
    }


async def _run_desktop_scroll_journey(
    page: Page,
    tier: str,
    deadline_ts: float,
    pre_hero_img: Optional[Image.Image] = None,
    pre_hero_tel: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """
    Execute Dual-Mode Desktop Scroll Driver (1440x900).
    Returns (scroll_driver, deduplicated_keyframes, detected_scroll_libs).
    """
    libs_info: Dict[str, Any] = {"libs": [], "canvasCount": 0}
    try:
        libs_info = await asyncio.wait_for(page.evaluate(SCROLL_LIB_DETECT_JS), timeout=2.0)
    except Exception:
        pass
    scroll_libs = list(libs_info.get("libs") or [])

    cdp = None
    try:
        cdp = await asyncio.wait_for(page.context.new_cdp_session(page), timeout=2.0)
    except Exception:
        cdp = None

    try:
        # Always capture Hero (y=0)
        hero_tel = pre_hero_tel if pre_hero_tel is not None else await _read_step_telemetry(page)
        hero_img = pre_hero_img if pre_hero_img is not None else await _capture_viewport_pil(page, tier=tier, timeout_s=8.0, cdp=cdp)
        if hero_img is None:
            return "native", [], scroll_libs

        raw_frames: List[Dict[str, Any]] = [
            {
                "image": hero_img,
                "scrollY": hero_tel.get("scrollY", 0),
                "scrollPct": 0,
                "scrollHeight": hero_tel.get("scrollHeight", DESKTOP_MIN_HEIGHT),
                "activeLandmark": hero_tel.get("activeLandmark") or "Hero",
                "stickyFixedCount": hero_tel.get("stickyFixedCount", 0),
                "diff_from_prev": 0.0,
            }
        ]

        scroll_h = int(hero_tel.get("scrollHeight", DESKTOP_MIN_HEIGHT))
        test_y = min(DESKTOP_STEP_PX, max(0, scroll_h - DESKTOP_MIN_HEIGHT))

        observed_y = 0
        if scroll_h > int(DESKTOP_MIN_HEIGHT * 1.2) and test_y >= 100:
            try:
                await asyncio.wait_for(page.evaluate(f"() => window.scrollTo(0, {test_y})"), timeout=2.0)
                await asyncio.sleep(0.38)
                observed_y = int(await asyncio.wait_for(page.evaluate("() => Math.round(window.scrollY || 0)"), timeout=1.5))
            except Exception:
                observed_y = 0

        driver_mode = decide_scroll_driver_mode(scroll_h, DESKTOP_MIN_HEIGHT, observed_y)

        switched_to_wheel = False
        if driver_mode == "native":
            img1 = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0, cdp=cdp)
            if img1 is not None:
                tel1 = await _read_step_telemetry(page)
                diff1 = compute_frame_diff(raw_frames[-1]["image"], img1)
                raw_frames.append({
                    "image": img1,
                    "scrollY": tel1.get("scrollY", observed_y),
                    "scrollPct": tel1.get("scrollPct", 0),
                    "scrollHeight": tel1.get("scrollHeight", scroll_h),
                    "activeLandmark": tel1.get("activeLandmark") or "Section",
                    "stickyFixedCount": tel1.get("stickyFixedCount", 0),
                    "diff_from_prev": diff1,
                })
                scroll_h = max(scroll_h, int(tel1.get("scrollHeight", scroll_h)))
                if should_fallback_to_wheel_mid_scroll(diff1, int(tel1.get("scrollY", observed_y)), scroll_h, DESKTOP_MIN_HEIGHT):
                    switched_to_wheel = True
                    driver_mode = "native+wheel"

            if not switched_to_wheel:
                targets = compute_native_scroll_targets(scroll_h, DESKTOP_MIN_HEIGHT, DESKTOP_STEP_PX, DESKTOP_MAX_SCROLL_Y)
                native_stalls = 0
                prev_obs_y = observed_y
                for target_y in targets:
                    if target_y <= observed_y + 40:
                        continue
                    if time.time() > deadline_ts - 52.0:
                        break
                    try:
                        await asyncio.wait_for(page.evaluate(f"() => window.scrollTo(0, {target_y})"), timeout=2.0)
                    except Exception:
                        break
                    await asyncio.sleep(0.38)
                    tel = await _read_step_telemetry(page)
                    img = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0, cdp=cdp)
                    if img is None:
                        continue
                    cur_obs_y = int(tel.get("scrollY", target_y))
                    cur_sh = max(scroll_h, int(tel.get("scrollHeight", scroll_h)))
                    diff = compute_frame_diff(raw_frames[-1]["image"], img)
                    stalled_y = (cur_obs_y <= prev_obs_y + 40) and ((cur_sh - DESKTOP_MIN_HEIGHT) - cur_obs_y > 120)
                    if should_fallback_to_wheel_mid_scroll(diff, cur_obs_y, cur_sh, DESKTOP_MIN_HEIGHT) or stalled_y:
                        native_stalls += 1
                        if native_stalls >= 1:
                            switched_to_wheel = True
                            driver_mode = "native+wheel"
                            break
                    else:
                        native_stalls = 0
                    prev_obs_y = cur_obs_y
                    scroll_h = cur_sh
                    raw_frames.append({
                        "image": img,
                        "scrollY": cur_obs_y,
                        "scrollPct": tel.get("scrollPct", 0),
                        "scrollHeight": cur_sh,
                        "activeLandmark": tel.get("activeLandmark") or "Section",
                        "stickyFixedCount": tel.get("stickyFixedCount", 0),
                        "diff_from_prev": diff,
                    })

        if driver_mode != "native" or switched_to_wheel:
            # Wheel-driven mode for scroll-jacked / Lenis / GSAP / 100vh WebGL sites (or mid-scroll fallback)
            try:
                await asyncio.wait_for(page.mouse.move(720, 450), timeout=3.0)
            except Exception:
                pass
            consecutive_identical = 0
            distinct_scenes = len(deduplicate_frames(raw_frames, threshold=DEDUP_DIFF_THRESHOLD, max_frames=DESKTOP_MAX_KEYFRAMES))
            max_bursts = 6 if tier == "tier2_webgl" else 14
            for burst_idx in range(1, max_bursts + 1):
                if time.time() > deadline_ts - 52.0:
                    break
                try:
                    await asyncio.wait_for(page.mouse.wheel(0, 750), timeout=3.0)
                except Exception:
                    break
                await asyncio.sleep(0.55)
                img = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0, cdp=cdp)
                if img is None:
                    break
                diff = compute_frame_diff(raw_frames[-1]["image"], img)
                if diff < DEDUP_DIFF_THRESHOLD and tier != "tier2_webgl":
                    try:
                        for _ in range(4):
                            await asyncio.wait_for(page.keyboard.press("ArrowDown"), timeout=2.0)
                            await asyncio.sleep(0.08)
                    except Exception:
                        pass
                    await asyncio.sleep(0.35)
                    img2 = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0, cdp=cdp)
                    if img2 is not None:
                        diff2 = compute_frame_diff(raw_frames[-1]["image"], img2)
                        if diff2 > diff:
                            img, diff = img2, diff2
                tel = await _read_step_telemetry(page)
                if diff < DEDUP_DIFF_THRESHOLD:
                    consecutive_identical += 1
                    if consecutive_identical >= 3:
                        break
                else:
                    consecutive_identical = 0
                    distinct_scenes += 1
                    est_y = tel.get("scrollY", 0) or (raw_frames[-1].get("scrollY", 0) + 750)
                    raw_frames.append({
                        "image": img,
                        "scrollY": est_y,
                        "scrollPct": min(100, int(round(burst_idx / max_bursts * 100))) if tel.get("scrollPct", 0) == 0 else tel.get("scrollPct", 0),
                        "scrollHeight": max(scroll_h, int(tel.get("scrollHeight", DESKTOP_MIN_HEIGHT))),
                        "activeLandmark": tel.get("activeLandmark") or f"Scene {distinct_scenes}",
                        "stickyFixedCount": tel.get("stickyFixedCount", 0),
                        "diff_from_prev": diff,
                    })
                    if distinct_scenes >= DESKTOP_MAX_KEYFRAMES:
                        break

        keyframes = deduplicate_frames(raw_frames, threshold=DEDUP_DIFF_THRESHOLD, max_frames=DESKTOP_MAX_KEYFRAMES)
        if keyframes:
            keyframes[0]["scrollPct"] = 0
            if len(keyframes) > 1 and keyframes[-1]["scrollPct"] == 0:
                keyframes[-1]["scrollPct"] = 100
        return driver_mode, keyframes, scroll_libs
    finally:
        if cdp is not None:
            try:
                await cdp.detach()
            except Exception:
                pass


async def _run_ui_probes(
    page: Page,
    tier: str,
    baseline_img: Image.Image,
    deadline_ts: float,
) -> Dict[str, Any]:
    """Run safe non-destructive UI probe suite on desktop homepage."""
    probes: Dict[str, Any] = {
        "nav_probe": {"tested": False, "triggered": False, "diff": 0.0, "opened_links": []},
        "hover_probes": [],
        "interactive_probe": {"tested": False, "triggered": False, "diff": 0.0},
        "theme_probe": {"tested": False, "changed": False},
        "state_images": {},
    }

    # Return to top for homepage probes
    try:
        await asyncio.wait_for(page.evaluate("() => window.scrollTo(0, 0)"), timeout=2.0)
        await asyncio.sleep(0.35)
    except Exception:
        pass

    if time.time() > deadline_ts - 35.0:
        return probes

    # 1. Nav / Menu Probe
    try:
        nav_info = await asyncio.wait_for(page.evaluate(NAV_TRIGGER_FIND_JS), timeout=2.0)
        if isinstance(nav_info, dict) and nav_info.get("found"):
            probes["nav_probe"]["tested"] = True
            probes["nav_probe"]["label"] = nav_info.get("label", "")
            before_links = await asyncio.wait_for(page.evaluate(VISIBLE_NAV_LINKS_JS), timeout=2.0)
            before_set = {(l["text"], l["href"]) for l in (before_links or [])}
            await asyncio.wait_for(page.mouse.click(nav_info["cx"], nav_info["cy"]), timeout=3.0)
            await asyncio.sleep(0.55)
            after_img = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0)
            if after_img is not None:
                diff = compute_frame_diff(baseline_img, after_img)
                probes["nav_probe"]["diff"] = diff
                if diff > STATE_DIFF_THRESHOLD:
                    probes["nav_probe"]["triggered"] = True
                    probes["state_images"]["state-nav.webp"] = after_img
                    after_links = await asyncio.wait_for(page.evaluate(VISIBLE_NAV_LINKS_JS), timeout=2.0)
                    new_links = [
                        l for l in (after_links or [])
                        if (l["text"], l["href"]) not in before_set
                    ]
                    probes["nav_probe"]["opened_links"] = (new_links or after_links or [])[:12]
            # Close menu safely and verify it closed (C8)
            try:
                await asyncio.wait_for(page.keyboard.press("Escape"), timeout=2.0)
                await asyncio.sleep(0.3)
                if probes["nav_probe"]["triggered"]:
                    post_links = await asyncio.wait_for(page.evaluate(VISIBLE_NAV_LINKS_JS), timeout=2.0)
                    still_open = any((l["text"], l["href"]) not in before_set for l in (post_links or []))
                    if still_open:
                        await asyncio.wait_for(page.mouse.click(nav_info["cx"], nav_info["cy"]), timeout=2.5)
                        await asyncio.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass

    if time.time() > deadline_ts - 35.0:
        return probes

    # 2. Hover Physics Probe (Primary CTA + First Card)
    try:
        candidates = await asyncio.wait_for(page.evaluate(HOVER_CANDIDATES_JS), timeout=2.5)
        for cand in (candidates or [])[:2]:
            before_st = cand.get("before") or {}
            await asyncio.wait_for(page.mouse.move(cand["cx"], cand["cy"]), timeout=2.5)
            await asyncio.sleep(0.35)
            after_st = await asyncio.wait_for(page.evaluate(READ_HOVER_STYLE_JS, cand["idx"]), timeout=1.8)
            await asyncio.wait_for(page.mouse.move(10, 10), timeout=2.0)
            if isinstance(after_st, dict):
                diffs = {}
                for prop in ("transform", "color", "backgroundColor", "borderColor", "opacity", "boxShadow"):
                    if before_st.get(prop) != after_st.get(prop):
                        diffs[prop] = {"from": before_st.get(prop), "to": after_st.get(prop)}
                probes["hover_probes"].append({
                    "role": cand.get("role"),
                    "tag": cand.get("tag"),
                    "label": cand.get("label"),
                    "changed": bool(diffs),
                    "diffs": diffs,
                    "transitionDuration": after_st.get("transitionDuration") or before_st.get("transitionDuration"),
                    "transitionTimingFunction": after_st.get("transitionTimingFunction") or before_st.get("transitionTimingFunction"),
                })
    except Exception:
        pass

    if time.time() > deadline_ts - 25.0:
        return probes

    # 3. Theme Toggle Probe
    try:
        theme_info = await asyncio.wait_for(page.evaluate(THEME_TOGGLE_FIND_JS), timeout=2.0)
        if isinstance(theme_info, dict) and theme_info.get("found"):
            probes["theme_probe"]["tested"] = True
            probes["theme_probe"]["label"] = theme_info.get("label", "")
            await asyncio.wait_for(page.evaluate("() => { if (window.__THEME_TOGGLE) window.__THEME_TOGGLE.click(); }"), timeout=2.0)
            await asyncio.sleep(0.4)
            after_theme = await asyncio.wait_for(
                page.evaluate("""() => {
                    const bcs = window.getComputedStyle(document.body);
                    return { bg: bcs.backgroundColor, color: bcs.color, htmlClass: (document.documentElement.className || '').slice(0, 80) };
                }"""),
                timeout=1.8,
            )
            if isinstance(after_theme, dict):
                changed = (
                    after_theme.get("bg") != theme_info.get("beforeBg")
                    or after_theme.get("color") != theme_info.get("beforeColor")
                    or after_theme.get("htmlClass") != theme_info.get("beforeHtmlClass")
                )
                probes["theme_probe"].update({
                    "changed": changed,
                    "beforeBg": theme_info.get("beforeBg"),
                    "afterBg": after_theme.get("bg"),
                    "beforeColor": theme_info.get("beforeColor"),
                    "afterColor": after_theme.get("color"),
                })
            # Restore original theme
            await asyncio.wait_for(page.evaluate("() => { if (window.__THEME_TOGGLE) window.__THEME_TOGGLE.click(); }"), timeout=2.0)
            await asyncio.sleep(0.25)
    except Exception:
        pass

    if time.time() > deadline_ts - 22.0:
        return probes

    # 4. Interactive Controls Probe (Tab / Accordion / Carousel-Next)
    try:
        ctrl = await asyncio.wait_for(page.evaluate(INTERACTIVE_CONTROL_FIND_JS), timeout=2.5)
        if isinstance(ctrl, dict) and ctrl.get("found"):
            probes["interactive_probe"]["tested"] = True
            probes["interactive_probe"]["kind"] = ctrl.get("kind")
            probes["interactive_probe"]["label"] = ctrl.get("label")
            await asyncio.sleep(0.25)
            before_ctrl_img = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0)
            await asyncio.wait_for(page.evaluate("() => { if (window.__INTERACTIVE_TARGET) window.__INTERACTIVE_TARGET.click(); }"), timeout=2.0)
            await asyncio.sleep(0.55)
            after_ctrl_img = await _capture_viewport_pil(page, tier=tier, timeout_s=6.0)
            after_attr = await asyncio.wait_for(
                page.evaluate("() => window.__INTERACTIVE_TARGET ? (window.__INTERACTIVE_TARGET.getAttribute('aria-expanded') || window.__INTERACTIVE_TARGET.getAttribute('aria-selected') || '') : ''"),
                timeout=1.5,
            )
            if before_ctrl_img is not None and after_ctrl_img is not None:
                diff = compute_frame_diff(before_ctrl_img, after_ctrl_img)
                probes["interactive_probe"]["diff"] = diff
                probes["interactive_probe"]["attr_change"] = f"{ctrl.get('beforeAttr', '')} -> {after_attr}"
                if diff > STATE_DIFF_THRESHOLD:
                    probes["interactive_probe"]["triggered"] = True
                    probes["state_images"]["state-interactive.webp"] = after_ctrl_img
    except Exception:
        pass

    return probes


async def _run_subpage_crawl(
    page: Page,
    home_url: str,
    tier: str,
    deadline_ts: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, bytes]]:
    """
    Crawl up to 3 distinct internal routes, detect SPA vs MPA on first route,
    and capture 3 desktop keyframes (Top / Mid / Bottom) stitched into subpage-0N-<slug>.webp.
    """
    subpages_meta: List[Dict[str, Any]] = []
    subpage_assets: Dict[str, bytes] = {}

    if time.time() > deadline_ts - 30.0:
        return subpages_meta, subpage_assets

    try:
        await asyncio.wait_for(page.evaluate("() => window.scrollTo(0, 0)"), timeout=2.0)
        await asyncio.sleep(0.25)
        raw_links = await asyncio.wait_for(page.evaluate(EXTRACT_INTERNAL_LINKS_JS), timeout=2.5)
    except Exception:
        raw_links = []

    routes = select_internal_subpages(home_url, raw_links or [], max_pages=1 if tier == "tier2_webgl" else 3)
    if not routes:
        return subpages_meta, subpage_assets

    for idx, route in enumerate(routes, 1):
        if time.time() > deadline_ts - 22.0:
            break
        target_url = route["url"]
        transition_type = "direct"
        transition_ms = 0
        http_status: Optional[int] = 200
        nav_ok = False

        if idx == 1 and route.get("visible") and route.get("cx", 0) > 0 and route.get("cy", 0) > 0:
            # Click first route to measure SPA vs MPA transition
            pages_before = list(page.context.pages)
            try:
                await asyncio.wait_for(page.evaluate("() => { window.__SPA_MARKER = 'alive_v2'; }"), timeout=1.5)
                prev_url = page.url
                t_nav = time.time()
                await asyncio.wait_for(page.mouse.click(route["cx"], route["cy"]), timeout=3.0)
                # Wait up to 4.5s for URL change
                for _ in range(18):
                    await asyncio.sleep(0.25)
                    if page.url.split("#")[0].rstrip("/") != prev_url.split("#")[0].rstrip("/"):
                        nav_ok = True
                        break
                if nav_ok:
                    await asyncio.sleep(0.8)
                    transition_ms = int((time.time() - t_nav) * 1000)
                    marker = None
                    try:
                        marker = await asyncio.wait_for(page.evaluate("() => window.__SPA_MARKER || null"), timeout=1.5)
                    except Exception:
                        marker = None
                    transition_type = "SPA" if marker == "alive_v2" else "MPA"
            except Exception:
                nav_ok = False
            finally:
                # P9: close any popup tabs opened by target=_blank links
                for extra_page in list(page.context.pages):
                    if extra_page not in pages_before and extra_page is not page:
                        try:
                            await asyncio.wait_for(extra_page.close(), timeout=2.0)
                        except Exception:
                            pass

        if not nav_ok:
            t_nav = time.time()
            try:
                resp = await asyncio.wait_for(
                    page.goto(target_url, wait_until="domcontentloaded", timeout=15000),
                    timeout=17.0,
                )
                http_status = resp.status if resp is not None else None
                transition_ms = int((time.time() - t_nav) * 1000)
                if idx == 1:
                    transition_type = "direct"
                nav_ok = http_status is not None and http_status < 400
            except Exception:
                nav_ok = False

        if not nav_ok:
            continue

        try:
            await asyncio.wait_for(page.wait_for_load_state("networkidle", timeout=2500), timeout=3.0)
        except Exception:
            pass
        await asyncio.sleep(0.6)
        try:
            await _dismiss_consent_all_frames(page, allow_hide_fallback=True)
            await _dismiss_promo_modals(page)
        except Exception:
            pass

        # Quick block check on subpage
        sub_title = ""
        sub_h1 = ""
        sub_scroll_h = DESKTOP_MIN_HEIGHT
        try:
            sub_info = await asyncio.wait_for(
                page.evaluate("""() => {
                    const h1 = document.querySelector('h1');
                    const bodyText = (document.body ? (document.body.innerText || '') : '').slice(0, 600);
                    return {
                        title: document.title || '',
                        h1: h1 ? (h1.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80) : '',
                        scrollHeight: Math.max(
                            document.body ? document.body.scrollHeight : 0,
                            document.documentElement ? document.documentElement.scrollHeight : 0,
                            900
                        ),
                        visibleText: bodyText
                    };
                }"""),
                timeout=2.5,
            )
            sub_title = (sub_info or {}).get("title", "")
            sub_h1 = (sub_info or {}).get("h1", "")
            sub_scroll_h = int((sub_info or {}).get("scrollHeight", DESKTOP_MIN_HEIGHT))
            if detect_block_or_error(http_status, sub_title, (sub_info or {}).get("visibleText", ""), "<html><body>ok</body></html>"):
                continue
            # C6: skip empty/stub subpages (short viewport with no H1)
            if sub_scroll_h <= int(DESKTOP_MIN_HEIGHT * 1.2) and not sub_h1.strip():
                continue
        except Exception:
            pass

        # Capture 3 desktop keyframes: Top (0%), Mid (~50%), Bottom (100%)
        max_y = max(0, min(sub_scroll_h - DESKTOP_MIN_HEIGHT, DESKTOP_MAX_SCROLL_Y))
        positions = [("Top", 0, 0)]
        if max_y >= 200:
            positions.append(("Mid", max_y // 2, 50))
            positions.append(("Bottom", max_y, 100))

        sub_frames: List[Dict[str, Any]] = []
        for pos_label, pos_y, pos_pct in positions:
            try:
                await asyncio.wait_for(page.evaluate(f"() => window.scrollTo(0, {pos_y})"), timeout=1.8)
            except Exception:
                pass
            await asyncio.sleep(0.35)
            im = await _capture_viewport_pil(page, tier=tier, timeout_s=7.0)
            if im is not None:
                sub_frames.append({"image": im, "label": pos_label, "scrollY": pos_y, "scrollPct": pos_pct})

        sub_dedup = deduplicate_frames(sub_frames, threshold=DEDUP_DIFF_THRESHOLD, max_frames=3)
        if not sub_dedup:
            continue

        labeled_pairs = [
            (
                f["image"],
                f"#{i} · y={f['scrollY']}px ({f['scrollPct']}%) · {route['route_slug']} · {f['label']}",
            )
            for i, f in enumerate(sub_dedup, 1)
        ]
        strip = build_labeled_filmstrip(labeled_pairs, target_width=1080, frame_height=675)
        asset_name = f"subpage-{idx:02d}-{route['route_slug']}.webp"
        subpage_assets[asset_name] = encode_webp_bytes(strip)
        subpages_meta.append({
            "index": idx,
            "route_slug": route["route_slug"],
            "category": route["category"],
            "url": target_url,
            "final_url": page.url or target_url,
            "title": sub_title,
            "h1": sub_h1,
            "scroll_height": sub_scroll_h,
            "transition_type": transition_type,
            "transition_ms": transition_ms,
            "keyframes_count": len(sub_dedup),
            "asset_file": f"assets/{asset_name}",
            "strip_dims": [strip.width, strip.height],
        })

    return subpages_meta, subpage_assets


async def _capture_mobile_journey(
    browser: Browser,
    url: str,
    tier: str,
    deadline_ts: float,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture 3-step condensed mobile journey (390x844: Top, Mid, Bottom) and stitch filmstrip."""
    ctx_kwargs: Dict[str, Any] = {
        "viewport": {"width": MOBILE_WIDTH, "height": MOBILE_MIN_HEIGHT},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "user_agent": MOBILE_UA,
        "is_mobile": True,
        "has_touch": True,
    }
    if auth_storage_state:
        ctx_kwargs["storage_state"] = auth_storage_state

    context = await browser.new_context(**ctx_kwargs)
    try:
        await context.add_init_script(RAF_PAUSE_INIT_SCRIPT)
        page = await context.new_page()
        nav_timeout = 18000 if tier == "tier2_webgl" else 22000
        try:
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout),
                timeout=(nav_timeout / 1000.0) + 3.0,
            )
        except Exception as exc:
            return {"valid": False, "error": str(exc), "steps": [], "strip_bytes": b"", "metrics": {"valid": False}}

        if tier == "tier2_webgl":
            await asyncio.sleep(2.5)
        else:
            try:
                await asyncio.wait_for(page.wait_for_load_state("networkidle", timeout=2500), timeout=3.0)
            except Exception:
                pass
            await asyncio.sleep(0.5)

        try:
            await _dismiss_consent_all_frames(page, allow_hide_fallback=True)
            await _dismiss_promo_modals(page)
        except Exception:
            pass

        top_tel = await _read_step_telemetry(page)
        scroll_h = int(top_tel.get("scrollHeight", MOBILE_MIN_HEIGHT))
        max_y = max(0, min(scroll_h - MOBILE_MIN_HEIGHT, DESKTOP_MAX_SCROLL_Y))

        positions = [("Top", 0, 0)]
        if max_y >= 200:
            positions.append(("Mid", max_y // 2, 50))
            positions.append(("Bottom", max_y, 100))

        raw_frames: List[Dict[str, Any]] = []
        use_mob_wheel = scroll_h <= int(MOBILE_MIN_HEIGHT * 1.2)
        for pos_label, pos_y, pos_pct in positions:
            if time.time() > deadline_ts - 6.0 and raw_frames:
                break
            if pos_y > 0 and not use_mob_wheel:
                try:
                    await asyncio.wait_for(page.evaluate(f"() => window.scrollTo(0, {pos_y})"), timeout=1.8)
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            im = await _capture_viewport_pil(page, tier=tier, timeout_s=8.0)
            if im is None:
                continue
            tel = await _read_step_telemetry(page)
            if pos_y > 0 and raw_frames:
                mdiff = compute_frame_diff(raw_frames[-1]["image"], im)
                if mdiff < DEDUP_DIFF_THRESHOLD:
                    use_mob_wheel = True
            raw_frames.append({
                "image": im,
                "scrollY": tel.get("scrollY", pos_y),
                "scrollPct": pos_pct,
                "activeLandmark": tel.get("activeLandmark") or pos_label,
                "label": pos_label,
            })

        # C1: Mobile wheel fallback when native scrollTo stalled or scrollHeight <= 1.2*vh
        if use_mob_wheel and len(deduplicate_frames(raw_frames, threshold=DEDUP_DIFF_THRESHOLD, max_frames=3)) < 2:
            try:
                await asyncio.wait_for(page.mouse.move(MOBILE_WIDTH // 2, MOBILE_MIN_HEIGHT // 2), timeout=2.0)
            except Exception:
                pass
            for step_i, (lbl, pct) in enumerate([("Mid", 50), ("Bottom", 100)], 1):
                if time.time() > deadline_ts - 6.0:
                    break
                try:
                    for _ in range(3):
                        await asyncio.wait_for(page.mouse.wheel(0, 650), timeout=2.0)
                        await asyncio.sleep(0.18)
                except Exception:
                    break
                await asyncio.sleep(0.35)
                im = await _capture_viewport_pil(page, tier=tier, timeout_s=7.0)
                if im is None:
                    continue
                tel = await _read_step_telemetry(page)
                raw_frames.append({
                    "image": im,
                    "scrollY": tel.get("scrollY", step_i * 1200),
                    "scrollPct": pct,
                    "activeLandmark": tel.get("activeLandmark") or lbl,
                    "label": lbl,
                })

        dedup = deduplicate_frames(raw_frames, threshold=DEDUP_DIFF_THRESHOLD, max_frames=3)
        if not dedup:
            return {"valid": False, "error": "No mobile frames captured", "steps": [], "strip_bytes": b"", "metrics": {"valid": False}}

        step_assets: Dict[str, bytes] = {}
        step_meta: List[Dict[str, Any]] = []
        labeled_pairs: List[Tuple[Image.Image, str]] = []
        for i, fr in enumerate(dedup, 1):
            fname = f"mobile-step-{i:02d}.webp"
            step_assets[fname] = encode_webp_bytes(fr["image"])
            lbl = f"#{i} · y={fr['scrollY']}px ({fr['scrollPct']}%) · {fr['activeLandmark']}"
            labeled_pairs.append((fr["image"], lbl))
            step_meta.append({
                "step": i,
                "file": f"assets/{fname}",
                "scrollY": fr["scrollY"],
                "scrollPct": fr["scrollPct"],
                "activeLandmark": fr["activeLandmark"],
            })

        strip_img = build_labeled_filmstrip(labeled_pairs, target_width=MOBILE_WIDTH, frame_height=MOBILE_MIN_HEIGHT)
        strip_bytes = encode_webp_bytes(strip_img)
        metrics = compute_png_visual_metrics(strip_bytes, top_fold_height=MOBILE_MIN_HEIGHT + FILMSTRIP_BAR_HEIGHT)
        return {
            "valid": bool(metrics.get("valid")),
            "steps": step_meta,
            "step_assets": step_assets,
            "strip_bytes": strip_bytes,
            "metrics": metrics,
        }
    finally:
        try:
            await asyncio.wait_for(context.close(), timeout=3.0)
        except Exception:
            pass


async def _run_desktop_session(
    browser: Browser,
    url: str,
    tier: str,
    deadline_ts: float,
    retry_extended: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Run full desktop session: load, dismiss modals, extract DOM/CSS, scroll journey, UI probes, subpages."""
    ctx_kwargs: Dict[str, Any] = {
        "viewport": {"width": DESKTOP_WIDTH, "height": DESKTOP_MIN_HEIGHT},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "user_agent": DESKTOP_UA,
    }
    if auth_storage_state:
        ctx_kwargs["storage_state"] = auth_storage_state

    context = await browser.new_context(**ctx_kwargs)
    collected_styles: List[Tuple[str, bytes]] = []
    collected_apis: List[Dict[str, Any]] = []
    scripts_count = 0

    try:
        await context.add_init_script(RAF_PAUSE_INIT_SCRIPT)
        page = await context.new_page()

        async def on_response(response):
            nonlocal scripts_count
            try:
                res_url = response.url
                status = response.status
                headers = response.headers or {}
                ctype = headers.get("content-type", "").lower()
                clean_u = res_url.split("?")[0].lower()
                if "javascript" in ctype or clean_u.endswith(".js"):
                    scripts_count += 1
                elif ("text/css" in ctype or clean_u.endswith(".css")) and len(collected_styles) < 40 and status < 400:
                    try:
                        body = await asyncio.wait_for(response.body(), timeout=2.0)
                        if body and len(body) <= 2_500_000:
                            collected_styles.append((res_url, body))
                    except Exception:
                        pass
                elif ("application/json" in ctype or "graphql" in clean_u or "/api/" in clean_u) and len(collected_apis) < 50:
                    collected_apis.append({
                        "url": res_url[:300],
                        "path": urlparse(res_url).path[:160],
                        "method": response.request.method,
                        "status": status,
                        "content_type": ctype[:80],
                    })
            except Exception:
                pass

        page.on("response", lambda resp: asyncio.create_task(on_response(resp)))

        nav_timeout_ms = 30000 if retry_extended else 22000
        http_status: Optional[int] = None
        nav_error: Optional[str] = None
        redirect_chain: List[str] = []

        try:
            resp = await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms),
                timeout=(nav_timeout_ms / 1000.0) + 3.0,
            )
            if resp is not None:
                http_status = resp.status
                req = resp.request
                chain = []
                while req is not None:
                    chain.append(req.url)
                    req = req.redirected_from
                redirect_chain = list(reversed(chain))
        except Exception as exc:
            nav_error = str(exc)

        final_url = page.url or url
        if nav_error and http_status is None:
            return {
                "http_status": None,
                "initial_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "nav_error": nav_error,
                "title": "",
                "dom_html": "",
                "visible_text": "",
                "canvas_count": 0,
                "computed_styles": {},
                "storage": {},
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
                "keyframes": [],
                "scroll_driver": "native",
                "scroll_libs": [],
                "probes": {},
                "subpages": [],
                "subpage_assets": {},
                "hero_metrics": {"valid": False},
            }

        # Settle page
        if tier == "tier2_webgl":
            await asyncio.sleep(3.5)
        else:
            try:
                await asyncio.wait_for(page.wait_for_load_state("networkidle", timeout=3500), timeout=4.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true"),
                    timeout=2.2,
                )
            except Exception:
                pass

        # Dismiss consent & promo modals
        cookie_dismissed = False
        try:
            if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                cookie_dismissed = True
        except Exception:
            pass
        try:
            await _dismiss_promo_modals(page)
        except Exception:
            pass

        # Preloader check + wheel/mouse wakeup attempt if stuck
        canvas_count = 0
        preloader_stuck = False
        preloader_reason = ""
        try:
            pcheck = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.5)
            vis_len = int(pcheck.get("visibleTextLen", 0))
            canvas_count = int(pcheck.get("canvasCount", 0))
            preloader_active = bool(pcheck.get("preloaderActive", False))
            preloader_reason = str(pcheck.get("preloaderReason") or "")
            if preloader_active or vis_len < 120:
                # Wakeup interaction: gentle mouse move + wheel tick + wait up to 6s
                try:
                    await asyncio.wait_for(page.mouse.move(720, 450), timeout=2.5)
                    await asyncio.wait_for(page.mouse.wheel(0, 200), timeout=2.5)
                except Exception:
                    pass
                for _ in range(3):
                    await asyncio.sleep(2.0)
                    p2 = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.0)
                    vis_len = int(p2.get("visibleTextLen", vis_len))
                    canvas_count = max(canvas_count, int(p2.get("canvasCount", 0)))
                    preloader_active = bool(p2.get("preloaderActive", False))
                    if p2.get("preloaderReason"):
                        preloader_reason = str(p2["preloaderReason"])
                    if not preloader_active and vis_len >= 120:
                        break
                try:
                    await page.evaluate("() => window.scrollTo(0, 0)")
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
            preloader_stuck = bool(preloader_active)
        except Exception:
            pass

        # Extract title, DOM, computed styles, storage
        title = ""
        dom_html = ""
        computed_styles: Dict[str, Any] = {}
        storage_data: Dict[str, Any] = {"localStorage": {}, "sessionStorage": {}}
        visible_text = ""

        try:
            title = await asyncio.wait_for(page.title(), timeout=3.0)
        except Exception:
            pass
        try:
            dom_html = await asyncio.wait_for(page.content(), timeout=8.0)
        except Exception:
            pass
        try:
            computed_styles = await asyncio.wait_for(page.evaluate(COMPUTED_STYLES_JS), timeout=8.0)
            visible_text = computed_styles.get("visibleTextSample", "")
            dm = computed_styles.get("domMetrics", {})
            if isinstance(dm.get("canvasCount"), int):
                canvas_count = max(canvas_count, dm["canvasCount"])
        except Exception:
            pass
        try:
            storage_data = await asyncio.wait_for(
                page.evaluate("""() => {
                    const ls = {}, ss = {};
                    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); ls[k] = String(localStorage.getItem(k) || '').slice(0, 500); } } catch (_) {}
                    try { for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); ss[k] = String(sessionStorage.getItem(k) || '').slice(0, 500); } } catch (_) {}
                    return { localStorage: ls, sessionStorage: ss };
                }"""),
                timeout=2.0,
            )
        except Exception:
            pass

        # Early block check before spending time on scrolling & subpages
        block_reason = detect_block_or_error(http_status, title, visible_text, dom_html, canvas_count, nav_error)
        if block_reason:
            return {
                "http_status": http_status,
                "initial_url": url,
                "final_url": page.url or final_url,
                "redirect_chain": redirect_chain,
                "nav_error": nav_error,
                "title": title,
                "dom_html": dom_html,
                "visible_text": visible_text,
                "canvas_count": canvas_count,
                "cookie_dismissed": cookie_dismissed,
                "preloader_stuck": preloader_stuck,
                "preloader_reason": preloader_reason,
                "computed_styles": computed_styles,
                "storage": storage_data,
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
                "keyframes": [],
                "scroll_driver": "native",
                "scroll_libs": [],
                "probes": {},
                "subpages": [],
                "subpage_assets": {},
                "hero_metrics": {"valid": False},
                "block_reason": block_reason,
            }

        # Capture Hero frame FIRST and immediately check Tier 2 escalation BEFORE wasting time scrolling on Tier 1
        hero_tel = await _read_step_telemetry(page)
        hero_img = await _capture_viewport_pil(page, tier=tier, timeout_s=7.0)
        hero_metrics = {"valid": False}
        if hero_img is not None:
            hero_metrics = compute_png_visual_metrics(encode_webp_bytes(hero_img), top_fold_height=DESKTOP_MIN_HEIGHT)

        if tier == "tier1_fast" and http_status == 200 and (
            not hero_metrics.get("valid")
            or (canvas_count > 0 and (is_visual_unhealthy_for_tier2(hero_metrics) or preloader_stuck))
            or (preloader_stuck and (is_visual_unhealthy_for_tier2(hero_metrics) or retry_extended))
        ):
            return {
                "http_status": http_status,
                "initial_url": url,
                "final_url": page.url or final_url,
                "redirect_chain": redirect_chain,
                "nav_error": nav_error,
                "title": title,
                "dom_html": dom_html,
                "visible_text": visible_text,
                "canvas_count": canvas_count,
                "cookie_dismissed": cookie_dismissed,
                "preloader_stuck": preloader_stuck,
                "preloader_reason": preloader_reason,
                "computed_styles": computed_styles,
                "storage": storage_data,
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
                "keyframes": [{"image": hero_img, "scrollY": 0, "scrollPct": 0, "scrollHeight": hero_tel.get("scrollHeight", 900), "activeLandmark": hero_tel.get("activeLandmark", "Hero"), "stickyFixedCount": hero_tel.get("stickyFixedCount", 0)}] if hero_img is not None else [],
                "scroll_driver": "native",
                "scroll_libs": [],
                "probes": {},
                "subpages": [],
                "subpage_assets": {},
                "hero_metrics": hero_metrics,
                "needs_tier2": True,
            }

        # Run Dual-Mode Desktop Scroll Journey (reusing already-captured hero frame)
        scroll_driver, keyframes, scroll_libs = await _run_desktop_scroll_journey(
            page, tier=tier, deadline_ts=deadline_ts, pre_hero_img=hero_img, pre_hero_tel=hero_tel
        )

        # Run Safe Non-Destructive UI Probe Suite
        probes = await _run_ui_probes(page, tier=tier, baseline_img=keyframes[0]["image"], deadline_ts=deadline_ts) if keyframes else {}

        # Run Multi-Page Crawl (up to 3 distinct internal routes)
        subpages, subpage_assets = await _run_subpage_crawl(page, home_url=page.url or final_url, tier=tier, deadline_ts=deadline_ts)

        if not preloader_stuck:
            vis_len_final = (computed_styles.get("domMetrics") or {}).get("visibleTextLen") if computed_styles else None
            p_txt = detect_preloader_text(visible_text, vis_len_final)
            if p_txt and len(keyframes) <= 1:
                preloader_stuck = True
                preloader_reason = p_txt

        return {
            "http_status": http_status,
            "initial_url": url,
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "nav_error": nav_error,
            "title": title,
            "dom_html": dom_html,
            "visible_text": visible_text,
            "canvas_count": canvas_count,
            "cookie_dismissed": cookie_dismissed,
            "preloader_stuck": preloader_stuck,
            "preloader_reason": preloader_reason,
            "computed_styles": computed_styles,
            "storage": storage_data,
            "styles": collected_styles,
            "apis": collected_apis,
            "scripts_count": scripts_count,
            "keyframes": keyframes,
            "scroll_driver": scroll_driver,
            "scroll_libs": scroll_libs,
            "probes": probes,
            "subpages": subpages,
            "subpage_assets": subpage_assets,
            "hero_metrics": hero_metrics,
        }
    finally:
        try:
            await asyncio.wait_for(context.close(), timeout=3.0)
        except Exception:
            pass


def _cleanup_assets_dir(assets_dir: Path, remove_all: bool = False) -> None:
    """Remove legacy PNGs (and optionally all WebP assets when blocked or re-capturing)."""
    if not assets_dir.exists():
        return
    for p in list(assets_dir.iterdir()):
        if not p.is_file():
            continue
        if remove_all and p.suffix.lower() in (".png", ".webp"):
            try:
                p.unlink()
            except OSError:
                pass
        elif p.name in ("desktop.png", "mobile.png") or p.suffix.lower() == ".png":
            try:
                p.unlink()
            except OSError:
                pass


async def _capture_behavioral_impl(
    pool: BrowserPool,
    slug: str,
    url: str,
    raw_dir: Path,
    wiki_targets_dir: Optional[Path] = None,
    deadline_seconds: float = 160.0,
    retry_extended: bool = False,
    force_tier2: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    t0 = time.time()
    deadline_ts = t0 + deadline_seconds
    chosen_tier = "tier1_fast"

    if force_tier2:
        t2_browser = await pool.launch_ephemeral("tier2_webgl")
        try:
            desk = await _run_desktop_session(
                t2_browser, url, tier="tier2_webgl", deadline_ts=deadline_ts,
                retry_extended=retry_extended, auth_storage_state=auth_storage_state,
            )
            chosen_tier = "tier2_webgl"
            block_reason = desk.get("block_reason") or detect_block_or_error(
                desk["http_status"], desk["title"], desk["visible_text"],
                desk["dom_html"], desk["canvas_count"], desk["nav_error"],
            )
            mob = None
            if not block_reason:
                mob = await _capture_mobile_journey(
                    t2_browser, desk["final_url"] or url, tier="tier2_webgl",
                    deadline_ts=deadline_ts, auth_storage_state=auth_storage_state,
                )
        finally:
            try:
                await t2_browser.close()
            except Exception:
                pass
            gc.collect()
    else:
        b1 = await pool.get_tier1()
        desk_budget = max(35.0, deadline_seconds - 42.0)
        try:
            desk = await asyncio.wait_for(
                _run_desktop_session(
                    b1, url, tier="tier1_fast", deadline_ts=deadline_ts,
                    retry_extended=retry_extended, auth_storage_state=auth_storage_state,
                ),
                timeout=desk_budget,
            )
        except asyncio.TimeoutError:
            desk = {
                "http_status": None, "initial_url": url, "final_url": url, "redirect_chain": [url],
                "nav_error": f"Desktop session exceeded {desk_budget:.0f}s sub-budget",
                "title": "", "dom_html": "", "visible_text": "", "canvas_count": 0,
                "cookie_dismissed": False, "preloader_stuck": False, "preloader_reason": "",
                "computed_styles": None, "storage": {}, "styles": [], "apis": [], "scripts_count": 0,
                "keyframes": [], "scroll_driver": "native", "scroll_libs": [], "probes": {},
                "subpages": [], "subpage_assets": {}, "hero_metrics": {"valid": False},
            }

        # Tier 3 escalation on ERR_HTTP2_*
        if desk.get("nav_error") and "ERR_HTTP2" in desk["nav_error"]:
            t3_browser = await pool.launch_ephemeral("tier3_http1")
            try:
                desk = await _run_desktop_session(
                    t3_browser, url, tier="tier3_http1", deadline_ts=deadline_ts,
                    retry_extended=retry_extended, auth_storage_state=auth_storage_state,
                )
                chosen_tier = "tier3_http1"
                block_reason = desk.get("block_reason") or detect_block_or_error(
                    desk["http_status"], desk["title"], desk["visible_text"],
                    desk["dom_html"], desk["canvas_count"], desk["nav_error"],
                )
                mob = None
                if not block_reason:
                    mob = await _capture_mobile_journey(
                        t3_browser, desk["final_url"] or url, tier="tier3_http1",
                        deadline_ts=deadline_ts, auth_storage_state=auth_storage_state,
                    )
            finally:
                try:
                    await t3_browser.close()
                except Exception:
                    pass
                gc.collect()
        elif desk.get("needs_tier2"):
            t2_browser = await pool.launch_ephemeral("tier2_webgl")
            try:
                t2_desk_budget = max(25.0, min(75.0, deadline_ts - time.time() - 35.0))
                try:
                    desk_t2 = await asyncio.wait_for(
                        _run_desktop_session(
                            t2_browser, url, tier="tier2_webgl", deadline_ts=min(deadline_ts, time.time() + t2_desk_budget + 30.0),
                            retry_extended=retry_extended, auth_storage_state=auth_storage_state,
                        ),
                        timeout=t2_desk_budget,
                    )
                except asyncio.TimeoutError:
                    desk_t2 = None

                if desk_t2 is not None:
                    # Backfill DOM/styles if Tier 2 missed them
                    for field in ("dom_html", "computed_styles", "styles", "title", "storage", "apis"):
                        if not desk_t2.get(field) and desk.get(field):
                            desk_t2[field] = desk[field]
                    if visual_entropy_score(desk_t2.get("hero_metrics") or {}) >= visual_entropy_score(desk.get("hero_metrics") or {}):
                        desk = desk_t2
                        chosen_tier = "tier2_webgl"

                block_reason = desk.get("block_reason") or detect_block_or_error(
                    desk["http_status"], desk["title"], desk["visible_text"],
                    desk["dom_html"], desk["canvas_count"], desk["nav_error"],
                )
                mob = None
                if not block_reason:
                    mob_budget = max(15.0, min(32.0, deadline_ts - time.time() - 5.0))
                    try:
                        mob = await asyncio.wait_for(
                            _capture_mobile_journey(
                                t2_browser if chosen_tier == "tier2_webgl" else b1,
                                desk["final_url"] or url,
                                tier=chosen_tier,
                                deadline_ts=deadline_ts,
                                auth_storage_state=auth_storage_state,
                            ),
                            timeout=mob_budget,
                        )
                    except asyncio.TimeoutError:
                        mob = None
            finally:
                try:
                    await asyncio.wait_for(t2_browser.close(), timeout=5.0)
                except Exception:
                    pass
                gc.collect()
        else:
            block_reason = desk.get("block_reason") or detect_block_or_error(
                desk["http_status"], desk["title"], desk["visible_text"],
                desk["dom_html"], desk["canvas_count"], desk["nav_error"],
            )
            mob = None
            if not block_reason:
                mob_budget = max(15.0, min(35.0, deadline_ts - time.time() - 6.0))
                try:
                    mob = await asyncio.wait_for(
                        _capture_mobile_journey(
                            b1, desk["final_url"] or url, tier="tier1_fast",
                            deadline_ts=deadline_ts, auth_storage_state=auth_storage_state,
                        ),
                        timeout=mob_budget,
                    )
                except asyncio.TimeoutError:
                    mob = None

    # Final validity checks
    keyframes = desk.get("keyframes") or []
    hero_m = desk.get("hero_metrics") or {}
    if not block_reason and not keyframes:
        block_reason = "Live desktop behavioral capture failed (0 valid keyframes)"
    if not block_reason and desk.get("preloader_stuck"):
        p_reason = (desk.get("preloader_reason") or "").strip()
        hero_is_blank = (
            not hero_m.get("valid")
            or (float(hero_m.get("gray_std", 0.0)) < 8.0 and float(hero_m.get("edge_density", 0.0)) < 0.003)
        )
        # Only block on preloader_stuck if text actually matches preloader/loading copy OR hero is solid-blank
        if detect_preloader_text(p_reason, len(p_reason)) or hero_is_blank:
            detail = (p_reason or "stuck on unhydrated preloader")[:70]
            block_reason = f"Headless Preloader / Blank Viewport Capture ({detail})"

    # Check if desktop hero is solid blank
    if (
        not block_reason
        and len(keyframes) <= 1
        and hero_m.get("valid")
        and float(hero_m.get("gray_std", 0.0)) < 8.0
        and float(hero_m.get("edge_density", 0.0)) < 0.003
    ):
        block_reason = "Headless Preloader / Blank Viewport Capture (solid blank desktop viewport)"

    if not block_reason and (mob is None or not mob.get("valid")):
        block_reason = "Live mobile behavioral capture failed (0 valid WebP frames)"

    status = "blocked" if block_reason else "analyzed"
    now_iso = datetime.now(timezone.utc).isoformat()
    target_raw = raw_dir / slug
    target_raw.mkdir(parents=True, exist_ok=True)
    styles_dir = target_raw / "styles"
    styles_dir.mkdir(exist_ok=True)

    # Save raw DOM, computed styles, storage, APIs, stylesheets
    if desk.get("dom_html"):
        atomic_write_text(target_raw / "dom.html", desk["dom_html"])
    comp_to_save = desk.get("computed_styles")
    if status == "analyzed" and not comp_to_save:
        dom_src = desk.get("dom_html") or ""
        tm = re.search(r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\'](#[0-9a-fA-F]{3,6})["\']', dom_src, re.I)
        d_luma = hero_m.get("modal_luma", 128.0)
        eff_bg = tm.group(1).lower() if tm else ("#000000" if d_luma < 80 else "#ffffff")
        comp_to_save = {
            "effectiveBgHex": eff_bg,
            "effectiveBgSource": "meta-theme-color" if tm else "canvas-modal-luma",
            "elements": {},
            "rootVars": {},
            "loadedFonts": [],
            "landmarks": [],
            "domMetrics": {"canvasCount": max(1, int(desk.get("canvas_count") or 1)), "scrollHeight": DESKTOP_MIN_HEIGHT},
            "captureNote": "webgl-main-thread-busy-fallback",
        }
    if comp_to_save:
        atomic_write_text(target_raw / "computed_styles.json", json.dumps(comp_to_save, indent=2))
    if desk.get("storage"):
        atomic_write_text(target_raw / "storage.json", json.dumps(desk["storage"], indent=2))
    if desk.get("apis"):
        atomic_write_text(target_raw / "network_apis.json", json.dumps(desk["apis"], indent=2))

    styles_saved = 0
    if desk.get("styles"):
        for old_css in styles_dir.glob("*.css"):
            try:
                old_css.unlink()
            except OSError:
                pass
        for idx, (surl, sbytes) in enumerate(desk["styles"]):
            fname = _sanitize_asset_filename(surl, idx, ".css")
            atomic_write_bytes(styles_dir / fname, sbytes)
            styles_saved += 1
    else:
        styles_saved = len(list(styles_dir.glob("*.css")))

    d_strip_metrics: Dict[str, Any] = {}
    m_strip_metrics: Dict[str, Any] = {}
    behavior_record: Dict[str, Any] = {}

    if status == "analyzed":
        # Build desktop steps & labeled vertical filmstrip
        desk_step_meta: List[Dict[str, Any]] = []
        desk_labeled_pairs: List[Tuple[Image.Image, str]] = []
        assets_dir = (wiki_targets_dir / slug / "assets") if wiki_targets_dir is not None else None
        if assets_dir is not None:
            assets_dir.mkdir(parents=True, exist_ok=True)
            _cleanup_assets_dir(assets_dir, remove_all=True)

        total_asset_bytes = 0
        for idx, kf in enumerate(keyframes, 1):
            fname = f"desktop-step-{idx:02d}.webp"
            step_bytes = encode_webp_bytes(kf["image"])
            if assets_dir is not None:
                atomic_write_bytes(assets_dir / fname, step_bytes)
                total_asset_bytes += len(step_bytes)
            lbl = f"#{idx} · y={kf['scrollY']}px ({kf['scrollPct']}%) · {kf['activeLandmark']}"
            desk_labeled_pairs.append((kf["image"], lbl))
            desk_step_meta.append({
                "step": idx,
                "file": f"assets/{fname}",
                "scrollY": kf["scrollY"],
                "scrollPct": kf["scrollPct"],
                "scrollHeight": kf.get("scrollHeight", DESKTOP_MIN_HEIGHT),
                "activeLandmark": kf["activeLandmark"],
                "stickyFixedCount": kf.get("stickyFixedCount", 0),
                "diff_from_prev": kf.get("diff_from_prev", 0.0),
                "bytes": len(step_bytes),
            })

        d_strip = build_labeled_filmstrip(desk_labeled_pairs, target_width=1280, frame_height=800)
        d_strip_bytes = encode_webp_bytes(d_strip)
        d_strip_metrics = compute_png_visual_metrics(d_strip_bytes, top_fold_height=800 + FILMSTRIP_BAR_HEIGHT)

        m_strip_bytes = mob["strip_bytes"]
        m_strip_metrics = mob["metrics"]

        if assets_dir is not None:
            atomic_write_bytes(assets_dir / "desktop-journey.webp", d_strip_bytes)
            atomic_write_bytes(assets_dir / "mobile-journey.webp", m_strip_bytes)
            total_asset_bytes += len(d_strip_bytes) + len(m_strip_bytes)
            for fname, bdata in (mob.get("step_assets") or {}).items():
                atomic_write_bytes(assets_dir / fname, bdata)
                total_asset_bytes += len(bdata)

        # Save UI probe state images
        probes = desk.get("probes") or {}
        state_files: List[Dict[str, Any]] = []
        for sname, simg in (probes.get("state_images") or {}).items():
            sbytes = encode_webp_bytes(simg)
            if assets_dir is not None:
                atomic_write_bytes(assets_dir / sname, sbytes)
                total_asset_bytes += len(sbytes)
            state_files.append({
                "file": f"assets/{sname}",
                "kind": "nav_menu" if "nav" in sname else "interactive_control",
                "bytes": len(sbytes),
            })

        # Save subpage filmstrips
        for sname, sbytes in (desk.get("subpage_assets") or {}).items():
            if assets_dir is not None:
                atomic_write_bytes(assets_dir / sname, sbytes)
                total_asset_bytes += len(sbytes)

        max_scroll_h = max([kf.get("scrollHeight", DESKTOP_MIN_HEIGHT) for kf in keyframes] + [DESKTOP_MIN_HEIGHT])
        max_y_reached = max([kf.get("scrollY", 0) for kf in keyframes] + [0])

        behavior_record = {
            "capture_mode": "behavioral_journey_v2",
            "asset_format": "webp",
            "target_name": slug,
            "url": url,
            "final_url": desk.get("final_url") or url,
            "capture_tier": chosen_tier,
            "scroll_driver": desk.get("scroll_driver", "native"),
            "scroll_libs": desk.get("scroll_libs", []),
            "max_scroll_height": max_scroll_h,
            "max_scroll_y_reached": max_y_reached,
            "desktop_keyframes": desk_step_meta,
            "desktop_journey": {
                "file": "assets/desktop-journey.webp",
                "width": d_strip_metrics.get("width", 0),
                "height": d_strip_metrics.get("height", 0),
                "bytes": len(d_strip_bytes),
                "sha256": d_strip_metrics.get("sha256", ""),
            },
            "mobile_keyframes": mob.get("steps", []),
            "mobile_journey": {
                "file": "assets/mobile-journey.webp",
                "width": m_strip_metrics.get("width", 0),
                "height": m_strip_metrics.get("height", 0),
                "bytes": len(m_strip_bytes),
                "sha256": m_strip_metrics.get("sha256", ""),
            },
            "ui_probes": {
                "nav_probe": probes.get("nav_probe", {}),
                "hover_probes": probes.get("hover_probes", []),
                "interactive_probe": probes.get("interactive_probe", {}),
                "theme_probe": probes.get("theme_probe", {}),
                "state_files": state_files,
            },
            "subpages": desk.get("subpages", []),
            "total_asset_bytes": total_asset_bytes,
            "captured_at": now_iso,
        }
        atomic_write_text(target_raw / "behavior.json", json.dumps(behavior_record, indent=2))
    else:
        if wiki_targets_dir is not None:
            _cleanup_assets_dir(wiki_targets_dir / slug / "assets", remove_all=True)
        stale_beh = target_raw / "behavior.json"
        if stale_beh.exists():
            try:
                stale_beh.unlink()
            except OSError:
                pass

    metadata = {
        "target_name": slug,
        "url": url,
        "final_url": desk.get("final_url") or url,
        "redirect_chain": desk.get("redirect_chain") or [url],
        "http_status": desk.get("http_status"),
        "capture_mode": "behavioral_journey_v2",
        "asset_format": "webp" if status == "analyzed" else "none",
        "capture_tier": chosen_tier if status == "analyzed" else "none",
        "scroll_driver": desk.get("scroll_driver", "none") if status == "analyzed" else "none",
        "desktop_keyframes": len(keyframes) if status == "analyzed" else 0,
        "subpages_crawled": len(desk.get("subpages") or []) if status == "analyzed" else 0,
        "capture_timestamp": now_iso,
        "desktop_dims": [d_strip_metrics.get("width", 0), d_strip_metrics.get("height", 0)] if status == "analyzed" else [0, 0],
        "mobile_dims": [m_strip_metrics.get("width", 0), m_strip_metrics.get("height", 0)] if status == "analyzed" else [0, 0],
        "desktop_metrics": d_strip_metrics if status == "analyzed" else {},
        "mobile_metrics": m_strip_metrics if status == "analyzed" else {},
        "status": status,
        "block_reason": block_reason,
        "cookie_dismissed": bool(desk.get("cookie_dismissed")),
        "title": desk.get("title") or "",
        "scripts_count": desk.get("scripts_count", 0),
        "styles_count": styles_saved,
        "storage_keys": list((desk.get("storage") or {}).get("localStorage", {}).keys()),
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    atomic_write_text(target_raw / "metadata.json", json.dumps(metadata, indent=2))
    return metadata


async def capture_target_behavioral(
    pool: BrowserPool,
    slug: str,
    url: str,
    raw_dir: Path,
    wiki_targets_dir: Optional[Path] = None,
    deadline_seconds: float = 160.0,
    retry_extended: bool = False,
    force_tier2: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute full behavioral crawl under a hard 160s per-target deadline."""
    t0 = time.time()
    try:
        return await asyncio.wait_for(
            _capture_behavioral_impl(
                pool=pool,
                slug=slug,
                url=url,
                raw_dir=raw_dir,
                wiki_targets_dir=wiki_targets_dir,
                deadline_seconds=deadline_seconds,
                retry_extended=retry_extended,
                force_tier2=force_tier2,
                auth_storage_state=auth_storage_state,
            ),
            timeout=deadline_seconds,
        )
    except asyncio.TimeoutError:
        reason = f"Hard per-target capture deadline exceeded ({deadline_seconds:.0f}s)"
        await pool.recycle_tier1()
        target_raw = raw_dir / slug
        target_raw.mkdir(parents=True, exist_ok=True)
        if wiki_targets_dir is not None:
            _cleanup_assets_dir(wiki_targets_dir / slug / "assets", remove_all=True)
        metadata = {
            "target_name": slug,
            "url": url,
            "final_url": url,
            "redirect_chain": [url],
            "http_status": None,
            "capture_mode": "behavioral_journey_v2",
            "asset_format": "none",
            "capture_tier": "none",
            "scroll_driver": "none",
            "desktop_keyframes": 0,
            "subpages_crawled": 0,
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
            "desktop_dims": [0, 0],
            "mobile_dims": [0, 0],
            "desktop_metrics": {},
            "mobile_metrics": {},
            "status": "blocked",
            "block_reason": reason,
            "cookie_dismissed": False,
            "title": "",
            "scripts_count": 0,
            "styles_count": 0,
            "storage_keys": [],
            "elapsed_seconds": round(time.time() - t0, 2),
        }
        atomic_write_text(target_raw / "metadata.json", json.dumps(metadata, indent=2))
        return metadata
