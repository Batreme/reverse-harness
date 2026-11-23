"""
3-Tier Adaptive Live Playwright Capture Engine (Desktop 1440px + Mobile 390px).

Implements:
- Tier 1 (tier1_fast): Fast Skia (--disable-gpu) + direct CDP Page.captureScreenshot
  clamped to 1440x[900..2400] desktop and 390x[844..2400] mobile.
- Tier 2 (tier2_webgl): Auto-escalates ONLY when Tier 1 returns HTTP 200 with a <canvas>
  element AND fails PIL/NumPy visual health (gray_std < 12 or dominant_q16_pct > 93% or
  edge_density < 0.004). Uses SwiftShader WebGL with an unthrottled preload rAF hook
  that sets window.__PAUSE_RAF = true for 150ms immediately before CDP screenshot,
  keeping whichever capture (Tier 1 vs Tier 2) achieves higher visual entropy.
- Tier 3 (tier3_http1): Auto-escalates ONLY on ERR_HTTP2_* transport errors using
  --disable-http2.
- Multilingual GDPR cookie-banner dismissal (strictly scoped to consent dialogs;
  never clicks CAPTCHA, Turnstile, human-verification, or age-gate controls).
- Incremental scroll-to-hydrate (0 -> min(scrollHeight, 2400) in 4 steps) + preloader
  settle wait + live computed_styles.json extraction.
- Strict honest block/dead/WAF/CAPTCHA detection with zero bypass or spoofing.
- Hard 75s per-target deadline, Chromium browser recycle every 20 targets + gc.collect(),
  lossless PIL PNG optimization, atomic file writes, and per-shard checkpointing.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
from PIL import Image
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

DESKTOP_WIDTH = 1440
DESKTOP_MIN_HEIGHT = 900
MOBILE_WIDTH = 390
MOBILE_MIN_HEIGHT = 844
MAX_CAPTURE_HEIGHT = 2400

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)

TIER1_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--js-flags=--max-old-space-size=512",
]

TIER2_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-webgl",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--js-flags=--max-old-space-size=768",
]

TIER3_ARGS = TIER1_ARGS + ["--disable-http2"]

RAF_PAUSE_INIT_SCRIPT = """
(() => {
  try {
    window.__PAUSE_RAF = false;
    const origRAF = window.requestAnimationFrame.bind(window);
    const queued = [];
    window.requestAnimationFrame = function(cb) {
      if (window.__PAUSE_RAF) {
        queued.push(cb);
        return 0;
      }
      return origRAF(cb);
    };
    window.__RESUME_RAF = function() {
      window.__PAUSE_RAF = false;
      while (queued.length > 0) {
        const fn = queued.shift();
        try { origRAF(fn); } catch (_) {}
      }
    };
  } catch (_) {}
})();
"""

COOKIE_DISMISS_JS = """
(() => {
  try {
    const FORBIDDEN_RE = /captcha|turnstile|recaptcha|hcaptcha|challenge|human|verify|verification|robot|security\\s*check|cloudflare|age[-_ ]?gate|confirm\\s*your\\s*age|legal\\s*drinking\\s*age|i\\s*am\\s*over\\s*(?:18|21)|over\\s*18|over\\s*21|18\\+|21\\+|enter\\s*password|sign\\s*in|log\\s*in/i;
    const CONSENT_CTX_RE = /cookie|cookies|consent|gdpr|rgpd|privacy|privacy\\s+settings|we\\s+value\\s+your\\s+privacy|manage\\s+consent|usercentrics|cookiebot|didomi|onetrust|sourcepoint|informasjonskapsler|personvern|samtykke|einwilligung|privatsph[aä]re|vie\\s+priv|datenschutz|privacidad|informativa|confidentialit|polityka|クッキー/i;
    const ACCEPT_TEXT_RE = /^(accept\\s*(all(\\s*cookies)?|cookies|and\\s*continue|(&|and)\\s*enter|(&|and)\\s*close)?|allow\\s*(all(\\s*cookies)?|cookies)?|i\\s*agree|agree(\\s*(&|and)\\s*(continue|close|to\\s*all))?|i\\s*understand|ok,?\\s*i\\s*understand|got\\s*it|ok,?\\s*got\\s*it|save\\s*(&|and)\\s*exit|continue\\s*with\\s*cookies|tillat\\s*alle(\\s*informasjonskapsler)?|godta\\s*alle(\\s*informasjonskapsler)?|accepter\\s*(tout|et\\s*fermer|les\\s*cookies)?|tout\\s*accepter|j['’]accepte|alle\\s*(akzeptieren|cookies\\s*akzeptieren|cookies\\s*zulassen|zulassen|annehmen)|alles\\s*akzeptieren|akzeptieren|zustimmen|aceptar\\s*(todo|todas|cookies)?|accetta\\s*(tutto|tutti|i\\s*cookie|e\\s*chiudi)?|alles\\s*accepteren|accepteren|akceptuj\\s*(wszystko|wszystkie)?|zgadzam\\s*si[eę]|同意する|すべて同意する|すべて許可)$/i;

    const isVisible = (el) => {
      if (!el) return false;
      const style = window.getComputedStyle(el);
      if (!style || style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.05) {
        return false;
      }
      const r = el.getBoundingClientRect();
      return r.width >= 16 && r.height >= 12 && r.bottom > 0 && r.right > 0;
    };

    const isForbidden = (el) => {
      let cur = el;
      for (let i = 0; i < 8 && cur; i++) {
        const meta = ((cur.id || '') + ' ' + (typeof cur.className === 'string' ? cur.className : '') + ' ' + (cur.getAttribute && (cur.getAttribute('aria-label') || '') || '')).slice(0, 300);
        if (FORBIDDEN_RE.test(meta)) return true;
        if (cur.parentElement) {
          cur = cur.parentElement;
        } else if (cur.getRootNode && cur.getRootNode().host) {
          cur = cur.getRootNode().host;
        } else {
          cur = null;
        }
      }
      const txt = (el.innerText || el.textContent || '').trim().slice(0, 120);
      if (FORBIDDEN_RE.test(txt)) return true;
      return false;
    };

    // Recursively collect document + all open shadow roots (up to depth 4)
    const allRoots = [document];
    const collectShadows = (root, depth) => {
      if (!root || depth > 4 || allRoots.length > 60) return;
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll('*')); } catch (_) { return; }
      for (const node of nodes) {
        if (node && node.shadowRoot && !allRoots.includes(node.shadowRoot)) {
          allRoots.push(node.shadowRoot);
          collectShadows(node.shadowRoot, depth + 1);
          if (allRoots.length > 60) break;
        }
      }
    };
    collectShadows(document, 0);

    // 1. Known CMP direct accept selectors across light DOM + all open shadow roots
    const knownSelectors = [
      '#onetrust-accept-btn-handler',
      '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
      '#CybotCookiebotDialogBodyButtonAccept',
      '#didomi-notice-agree-button',
      '.cky-btn-accept',
      '.qc-cmp2-summary-buttons button[mode="primary"]',
      '.cmplz-btn.cmplz-accept',
      '.cmplz-accept',
      '.osano-cm-accept-all',
      '.osano-cm-accept',
      '#axeptio_btn_acceptAll',
      '[data-testid="uc-accept-all-button"]',
      'button[data-testid="uc-accept-all-button"]',
      'button[data-action-type="accept"]',
      'button#accept[data-action-type="accept"]',
      'button.uc-accept-button',
      'button[data-action="accept"]',
      'button[data-cookiebanner="accept_button"]',
      'button[data-cc="accept-all"]',
      'button[data-action="accept-all"]',
      'button#accept-all-cookies',
      '.sp_choice_type_11',
      'button[title="Agree"]',
      'button[title="Accept All"]',
      'button[title="Accept all"]',
      '.cc-btn.cc-allow',
      '.cc-btn.cc-dismiss',
      '#hs-eu-confirmation-button',
      '.iubenda-cs-accept-btn',
      '.tarteaucitronCTAButton#tarteaucitronPersonalize2',
      '.tarteaucitronAllAllowed',
      '#wt-cli-accept-all-btn',
      '#moove_gdpr_cookie_info_bar .moove-gdpr-infobar-allow-all'
    ];
    for (const root of allRoots) {
      for (const sel of knownSelectors) {
        const btn = root.querySelector(sel);
        if (btn && isVisible(btn) && !isForbidden(btn)) {
          btn.click();
          return {
            dismissed: true,
            method: root === document ? 'cmp_selector' : 'shadow_cmp_selector',
            selector: sel,
            text: (btn.innerText || btn.textContent || '').trim().slice(0, 60)
          };
        }
      }
    }

    // 1b. Open shadow roots with consent copy
    for (const root of allRoots) {
      if (root === document) continue;
      const host = root.host;
      const hostMeta = host ? ((host.id || '') + ' ' + (typeof host.className === 'string' ? host.className : '')).slice(0, 200) : '';
      if (FORBIDDEN_RE.test(hostMeta)) continue;
      const srText = (root.textContent || '').replace(/\\s+/g, ' ').slice(0, 1800);
      if (FORBIDDEN_RE.test(srText)) continue;
      if (!CONSENT_CTX_RE.test(srText) && !CONSENT_CTX_RE.test(hostMeta)) continue;
      const sbtns = Array.from(root.querySelectorAll('button, a, [role="button"]')).slice(0, 40);
      for (const sbtn of sbtns) {
        if (!isVisible(sbtn) || isForbidden(sbtn)) continue;
        const label = ((sbtn.innerText || sbtn.textContent || sbtn.getAttribute('aria-label') || sbtn.getAttribute('title') || '').replace(/\\s+/g, ' ')).trim();
        if (label.length >= 2 && label.length <= 48 && ACCEPT_TEXT_RE.test(label)) {
          sbtn.click();
          return { dismissed: true, method: 'shadow_scoped_label', text: label };
        }
      }
    }

    // 2. Scoped consent banner/dialog containers (light DOM — including tail of body!)
    const containerSet = new Set(Array.from(document.querySelectorAll(
      '[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], ' +
      '[id*="gdpr" i], [class*="gdpr" i], [id*="rgpd" i], [class*="rgpd" i], ' +
      '[id*="privacy" i], [class*="privacy" i], [id*="axeptio" i], [class*="axeptio" i], ' +
      '[id*="didomi" i], [class*="didomi" i], [id*="usercentrics" i], [class*="usercentrics" i], ' +
      '[id*="sp_message" i], [class*="sp_message" i], [id*="cybot" i], [class*="cybot" i], ' +
      '[role="dialog"], [aria-modal="true"], [aria-label*="cookie" i], [aria-label*="consent" i], [aria-label*="privacy" i]'
    )).slice(0, 80));

    // Also inspect direct body children and last 180 layout blocks (CMP banners append to end of body)
    const allBlocks = Array.from(document.querySelectorAll('div, section, aside, form'));
    const scanBlocks = [
      ...Array.from(document.body ? document.body.children : []),
      ...allBlocks.slice(0, 60),
      ...allBlocks.slice(-180)
    ];
    for (const el of scanBlocks) {
      if (!el || containerSet.has(el)) continue;
      const cs = window.getComputedStyle(el);
      if ((cs.position === 'fixed' || cs.position === 'sticky' || parseInt(cs.zIndex || '0', 10) >= 50) && isVisible(el)) {
        const t = (el.innerText || '').slice(0, 700);
        if (t.length > 15 && CONSENT_CTX_RE.test(t) && !FORBIDDEN_RE.test(t)) {
          containerSet.add(el);
        }
      }
      if (containerSet.size >= 110) break;
    }

    // Inside consent iframes (e.g. Sourcepoint sp_message), the entire document.body IS the consent modal
    if (window.self !== window.top && document.body) {
      const bt = (document.body.innerText || '').slice(0, 1600);
      if (CONSENT_CTX_RE.test(bt) && !FORBIDDEN_RE.test(bt)) {
        containerSet.add(document.body);
      }
    }

    const containers = Array.from(containerSet);
    for (const box of containers) {
      if (!isVisible(box)) continue;
      const boxText = (box.innerText || '').slice(0, 1600);
      if (FORBIDDEN_RE.test(boxText)) continue;
      const boxMeta = ((box.id || '') + ' ' + (typeof box.className === 'string' ? box.className : '') + ' ' + (box.getAttribute('aria-label') || ''));
      if (!CONSENT_CTX_RE.test(boxText) && !CONSENT_CTX_RE.test(boxMeta)) continue;

      const btns = Array.from(box.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')).slice(0, 40);
      for (const btn of btns) {
        if (!isVisible(btn) || isForbidden(btn)) continue;
        const label = ((btn.innerText || btn.value || btn.getAttribute('aria-label') || btn.getAttribute('title') || '').replace(/\\s+/g, ' ')).trim();
        if (label.length >= 2 && label.length <= 48 && ACCEPT_TEXT_RE.test(label)) {
          btn.click();
          return { dismissed: true, method: 'scoped_label', text: label };
        }
      }
    }
    return { dismissed: false };
  } catch (e) {
    return { dismissed: false, error: String(e) };
  }
})();
"""

COOKIE_HIDE_FALLBACK_JS = """
(() => {
  try {
    const FORBIDDEN_RE = /captcha|turnstile|recaptcha|hcaptcha|challenge|human|verify|verification|robot|security\\s*check|cloudflare|age[-_ ]?gate|confirm\\s*your\\s*age|legal\\s*drinking\\s*age|i\\s*am\\s*over\\s*(?:18|21)|over\\s*18|over\\s*21|18\\+|21\\+|enter\\s*password|sign\\s*in|log\\s*in|paywall|subscribe/i;
    const CONSENT_CTX_RE = /cookie|cookies|consent|gdpr|rgpd|privacy|privacy\\s+settings|we\\s+value\\s+your\\s+privacy|manage\\s+consent|usercentrics|cookiebot|didomi|onetrust|sourcepoint|informasjonskapsler|personvern|samtykke|einwilligung|datenschutz/i;
    const CMP_ID_RE = /usercentrics|cookiebot|cybot|didomi|onetrust|sp_message|sp-message|axeptio|cookieyes|cky-|cmplz|osano|iubenda|tarteaucitron/i;

    const candidates = Array.from(document.querySelectorAll(
      '[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], ' +
      '[id*="usercentrics" i], [class*="usercentrics" i], [id*="didomi" i], [class*="didomi" i], ' +
      '[id*="onetrust" i], [class*="onetrust" i], [id*="sp_message" i], [class*="sp_message" i], ' +
      '[id*="sp-message" i], [class*="sp-message" i], [id*="cookiebot" i], [class*="cookiebot" i], ' +
      '[id*="cybot" i], [class*="cybot" i], iframe[id*="sp_message" i], iframe[src*="consent" i], iframe[src*="cookiebot" i]'
    ));

    let hidden = 0;
    for (const el of candidates) {
      if (!el || el === document.body || el === document.documentElement) continue;
      if (el.tagName === 'MAIN' || el.tagName === 'HEADER' || el.tagName === 'FOOTER' || el.tagName === 'NAV') continue;
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden') continue;
      const meta = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('src') || '')).slice(0, 350);
      const txt = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').slice(0, 900);
      if (FORBIDDEN_RE.test(meta) || FORBIDDEN_RE.test(txt)) continue;
      const isOverlay = (
        cs.position === 'fixed' ||
        cs.position === 'sticky' ||
        (parseInt(cs.zIndex || '0', 10) >= 10) ||
        el.getAttribute('role') === 'dialog' ||
        el.getAttribute('aria-modal') === 'true' ||
        el.tagName === 'IFRAME' ||
        CMP_ID_RE.test(meta)
      );
      if (!isOverlay) continue;
      if (!CMP_ID_RE.test(meta) && !CONSENT_CTX_RE.test(txt)) continue;
      // Avoid hiding huge primary content wrappers that happen to have a cookie class
      if (el.querySelector('main, article, h1') && !CMP_ID_RE.test(meta)) continue;
      el.style.setProperty('display', 'none', 'important');
      el.style.setProperty('visibility', 'hidden', 'important');
      el.style.setProperty('opacity', '0', 'important');
      el.style.setProperty('pointer-events', 'none', 'important');
      hidden += 1;
    }

    // Also hide fixed/modal backdrops left behind by Sourcepoint/Cookiebot/Usercentrics
    if (hidden > 0) {
      try {
        document.documentElement.classList.remove('sp-message-open');
        if (window.getComputedStyle(document.documentElement).overflow === 'hidden') {
          document.documentElement.style.setProperty('overflow', 'auto', 'important');
        }
        if (document.body && window.getComputedStyle(document.body).overflow === 'hidden') {
          document.body.style.setProperty('overflow', 'auto', 'important');
        }
      } catch (_) {}
    }
    return { hidden };
  } catch (e) {
    return { hidden: 0, error: String(e) };
  }
})();
"""

PROMO_MODAL_DISMISS_JS = """
(() => {
  try {
    const FORBIDDEN_RE = /captcha|turnstile|recaptcha|hcaptcha|verify\\s+(?:you\\s+are|that\\s+you(?:'re|\\s+are))\\s+human|confirm\\s+you\\s+are\\s+human|are\\s+you\\s+human|i\\s+am\\s+human|human\\s+verification|verify\\s+your\\s+identity|not\\s+a\\s+robot|security\\s*check|cloudflare\\s+challenge|age[-_ ]?gate|confirm\\s*your\\s*age|legal\\s*drinking\\s*age|i\\s*am\\s*over\\s*(?:18|21)|\\bover\\s*(?:18|21)\\b|\\b(?:18|21)\\+|enter\\s*password|\\b(?:sign[- ]?in|log[- ]?in)\\b|create\\s*account|two[- ]?factor|\\b2fa\\b|otp\\s*code|site\\s*search|search\\s*results/i;
    const NAV_EXCLUSION_RE = /\\b(nav|navigation|navbar|main[-_]?menu|mobile[-_]?menu|header[-_]?menu|global[-_]?menu|burger|hamburger|search[-_]?bar|search[-_]?modal|search[-_]?drawer|cart|basket|checkout|cookie|consent|gdpr|onetrust|usercentrics|didomi|cookiebot)\\b/i;
    const PROMO_HINT_RE = /newsletter|voucher|discount|coupon|promo|subscribe|subscribing|deal!|special\\s*offer|get\\s*\\d+%|€\\s*\\d+|\\$\\s*\\d+|£\\s*\\d+|free\\s*shipping|first\\s*order|sign\\s*up\\s*for|join\\s*our\\s*(list|club|newsletter)|popup|modal|lightbox|dialog|chatbot|assistant|how\\s+may\\s+i\\s+help/i;
    const CLOSE_META_RE = /close|dismiss|schlie[sß]en|fermer|cerrar|chiudi|sluiten/i;
    const CLOSE_TEXT_RE = /^([×✕✖╳xX]|close|dismiss|no\\s*,?\\s*thanks?|maybe\\s*later|not\\s*now|skip|nein\\s*,?\\s*danke|schlie[sß]en|fermer)$/i;

    const vw = window.innerWidth || 1440;
    const vh = window.innerHeight || 900;

    const isVisibleEl = (el, minW = 12, minH = 12) => {
      if (!el) return false;
      const cs = window.getComputedStyle(el);
      if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.1) {
        return false;
      }
      const r = el.getBoundingClientRect();
      return (
        r.width >= minW &&
        r.height >= minH &&
        r.bottom > 10 &&
        r.top < vh - 10 &&
        r.right > 10 &&
        r.left < vw - 10
      );
    };

    const isForbiddenEl = (el) => {
      let cur = el;
      for (let i = 0; i < 6 && cur; i++) {
        const meta = ((cur.id || '') + ' ' + (typeof cur.className === 'string' ? cur.className : '') + ' ' + (cur.getAttribute && (cur.getAttribute('aria-label') || '') || '')).slice(0, 300);
        if (FORBIDDEN_RE.test(meta)) return true;
        if (cur.parentElement) {
          cur = cur.parentElement;
        } else if (cur.getRootNode && cur.getRootNode().host) {
          cur = cur.getRootNode().host;
        } else {
          cur = null;
        }
      }
      const txt = (el.innerText || el.textContent || '').trim().slice(0, 200);
      if (FORBIDDEN_RE.test(txt)) return true;
      return false;
    };

    // Collect document + open shadow roots (up to depth 4)
    const allRoots = [document];
    const collectShadows = (root, depth) => {
      if (!root || depth > 4 || allRoots.length > 60) return;
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll('*')); } catch (_) { return; }
      for (const node of nodes) {
        if (node && node.shadowRoot && !allRoots.includes(node.shadowRoot)) {
          allRoots.push(node.shadowRoot);
          collectShadows(node.shadowRoot, depth + 1);
          if (allRoots.length > 60) break;
        }
      }
    };
    collectShadows(document, 0);

    const candidateOverlays = [];
    for (const root of allRoots) {
      const nodeSet = new Set();
      try {
        for (const n of Array.from(root.querySelectorAll(
          '[role="dialog"], [aria-modal="true"], dialog[open], ' +
          '[class*="modal" i], [id*="modal" i], [class*="popup" i], [id*="popup" i], ' +
          '[class*="newsletter" i], [id*="newsletter" i], [class*="voucher" i], [id*="voucher" i], ' +
          '[class*="lightbox" i], [class*="promo" i], [id*="chatbot" i], [class*="chatbot" i]'
        )).slice(0, 120)) {
          nodeSet.add(n);
        }
        const allBlocks = Array.from(root.querySelectorAll('div, section, aside'));
        if (root === document && document.body) {
          for (const ch of Array.from(document.body.children)) nodeSet.add(ch);
        }
        for (const n of allBlocks.slice(0, 60)) nodeSet.add(n);
        for (const n of allBlocks.slice(-180)) nodeSet.add(n);
      } catch (_) {
        continue;
      }

      for (const el of Array.from(nodeSet)) {
        if (!el || el === document.body || el === document.documentElement) continue;
        const tag = (el.tagName || '').toUpperCase();
        if (tag === 'NAV' || tag === 'HEADER' || tag === 'FOOTER' || tag === 'MAIN' || tag === 'ARTICLE') continue;
        if (el.closest && el.closest('nav, header')) continue;
        if (!isVisibleEl(el, 180, 120)) continue;

        const cs = window.getComputedStyle(el);
        const z = parseInt(cs.zIndex || '0', 10) || 0;
        const isDialog = el.getAttribute('role') === 'dialog' || el.getAttribute('aria-modal') === 'true' || tag === 'DIALOG';
        const isFixedOverlay = cs.position === 'fixed' || (cs.position === 'absolute' && z >= 50);
        if (!isDialog && !isFixedOverlay && z < 100) continue;

        // Avoid hiding full-page app shells that contain <main> or <article>
        if (el.querySelector && el.querySelector('main, article')) continue;

        const meta = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('aria-label') || '')).slice(0, 320);
        const txt = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 900);
        if (FORBIDDEN_RE.test(meta) || FORBIDDEN_RE.test(txt)) continue;
        if (NAV_EXCLUSION_RE.test(meta) && !PROMO_HINT_RE.test(txt)) continue;
        if (el.querySelector && el.querySelector('nav') && !PROMO_HINT_RE.test(txt)) continue;

        const r = el.getBoundingClientRect();
        const score = (z > 0 ? Math.min(z, 10000) : 0) + (isDialog ? 500 : 0) + (PROMO_HINT_RE.test(meta + ' ' + txt) ? 400 : 0) + (r.height > 200 ? 100 : 0);
        candidateOverlays.push({ el, score, meta, txt, root });
      }
    }

    candidateOverlays.sort((a, b) => b.score - a.score);

    const dismissedItems = [];
    const touchedRoots = new Set();
    for (const item of candidateOverlays.slice(0, 20)) {
      if (dismissedItems.length >= 3) break;
      const box = item.el;
      if (touchedRoots.has(item.root) && item.root !== document) continue;
      if (!isVisibleEl(box, 180, 120)) continue;

      const controls = Array.from(box.querySelectorAll(
        'button, a, [role="button"], [aria-label], [title], [class*="close" i], [class*="dismiss" i], [data-dismiss], [data-close], svg'
      )).slice(0, 80);

      for (const rawCtrl of controls) {
        const ctrl = rawCtrl.tagName && rawCtrl.tagName.toUpperCase() === 'SVG' ? (rawCtrl.parentElement || rawCtrl) : rawCtrl;
        if (!ctrl || !isVisibleEl(ctrl, 12, 12) || isForbiddenEl(ctrl)) continue;
        const cr = ctrl.getBoundingClientRect();
        if (cr.width > 180 || cr.height > 85) continue;
        const cmeta = ((ctrl.id || '') + ' ' + (typeof ctrl.className === 'string' ? ctrl.className : '') + ' ' + (ctrl.getAttribute('aria-label') || '') + ' ' + (ctrl.getAttribute('title') || '')).slice(0, 240);
        if (NAV_EXCLUSION_RE.test(cmeta)) continue;
        const ctxt = (ctrl.innerText || ctrl.textContent || '').replace(/\\s+/g, ' ').trim();
        const isCloseByMeta = CLOSE_META_RE.test(cmeta) || ctrl.hasAttribute('data-dismiss') || ctrl.hasAttribute('data-close');
        const isCloseByText = ctxt.length >= 1 && ctxt.length <= 24 && CLOSE_TEXT_RE.test(ctxt);
        if (isCloseByMeta || isCloseByText) {
          try { ctrl.click(); } catch (_) {}
          touchedRoots.add(item.root);
          dismissedItems.push({
            method: isCloseByMeta ? 'promo_close_attr' : 'promo_close_text',
            text: (ctxt || cmeta).slice(0, 60),
            modalMeta: item.meta.slice(0, 80)
          });
          break;
        }
      }
    }
    if (dismissedItems.length > 0) {
      return { dismissed: true, count: dismissedItems.length, items: dismissedItems };
    }
    return { dismissed: false };
  } catch (e) {
    return { dismissed: false, error: String(e) };
  }
})();
"""

COMPUTED_STYLES_JS = """
(() => {
  try {
    const rgbToHex = (colorStr) => {
      if (!colorStr) return null;
      const m = colorStr.match(/rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)(?:\\s*,\\s*([0-9.]+))?\\s*\\)/i);
      if (!m) return null;
      const a = m[4] !== undefined ? parseFloat(m[4]) : 1.0;
      if (a <= 0.05) return null;
      const r = Math.max(0, Math.min(255, parseInt(m[1], 10)));
      const g = Math.max(0, Math.min(255, parseInt(m[2], 10)));
      const b = Math.max(0, Math.min(255, parseInt(m[3], 10)));
      return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
    };

    const pickVisibleEl = (selector) => {
      const els = Array.from(document.querySelectorAll(selector)).slice(0, 25);
      for (const el of els) {
        const cs = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if (cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity || '1') > 0.05 && r.width > 0 && r.height > 0) {
          return el;
        }
      }
      return els[0] || null;
    };

    const pickLargestVisibleHeading = (selector) => {
      const els = Array.from(document.querySelectorAll(selector)).slice(0, 40);
      let best = null;
      let bestSize = -1;
      for (const el of els) {
        const cs = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (
          cs.display !== 'none' &&
          cs.visibility !== 'hidden' &&
          parseFloat(cs.opacity || '1') > 0.05 &&
          r.width > 0 &&
          r.height > 0 &&
          txt.length >= 2
        ) {
          const fs = parseFloat(cs.fontSize || '0') || 0;
          if (fs > bestSize) {
            bestSize = fs;
            best = el;
          }
        }
      }
      return best || pickVisibleEl(selector);
    };

    const sampleEl = (el) => {
      if (!el) return null;
      const cs = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const bwTop = parseFloat(cs.borderTopWidth || '0') || 0;
      const bwBottom = parseFloat(cs.borderBottomWidth || '0') || 0;
      const bwLeft = parseFloat(cs.borderLeftWidth || '0') || 0;
      const bwRight = parseFloat(cs.borderRightWidth || '0') || 0;
      const hasVisibleBorder = (
        (bwTop > 0 && cs.borderTopStyle && cs.borderTopStyle !== 'none' && cs.borderTopStyle !== 'hidden') ||
        (bwBottom > 0 && cs.borderBottomStyle && cs.borderBottomStyle !== 'none' && cs.borderBottomStyle !== 'hidden') ||
        (bwLeft > 0 && cs.borderLeftStyle && cs.borderLeftStyle !== 'none' && cs.borderLeftStyle !== 'hidden') ||
        (bwRight > 0 && cs.borderRightStyle && cs.borderRightStyle !== 'none' && cs.borderRightStyle !== 'hidden')
      );
      return {
        tag: el.tagName ? el.tagName.toLowerCase() : '',
        id: el.id || '',
        className: (typeof el.className === 'string' ? el.className : '').slice(0, 140),
        textSample: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90),
        backgroundColor: cs.backgroundColor,
        backgroundHex: rgbToHex(cs.backgroundColor),
        color: cs.color,
        colorHex: rgbToHex(cs.color),
        borderColor: cs.borderColor,
        borderHex: rgbToHex(cs.borderColor),
        borderTopWidth: cs.borderTopWidth,
        borderBottomWidth: cs.borderBottomWidth,
        borderLeftWidth: cs.borderLeftWidth,
        borderRightWidth: cs.borderRightWidth,
        hasVisibleBorder,
        fontFamily: cs.fontFamily,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        lineHeight: cs.lineHeight,
        letterSpacing: cs.letterSpacing,
        borderRadius: cs.borderRadius,
        maxWidth: cs.maxWidth,
        padding: cs.padding,
        paddingLeft: cs.paddingLeft,
        paddingRight: cs.paddingRight,
        gap: cs.gap,
        rect: {
          x: Math.round(r.x),
          y: Math.round(r.y),
          width: Math.round(r.width),
          height: Math.round(r.height)
        }
      };
    };

    const tags = ['html', 'body', 'header', 'nav', 'aside', 'main', 'section', 'hr', 'h1', 'h2', 'h3', 'p', 'a', 'button', 'footer', 'code'];
    const elements = {};
    for (const t of tags) {
      if (t === 'h1' || t === 'h2' || t === 'h3') {
        elements[t] = sampleEl(pickLargestVisibleHeading(t));
      } else {
        elements[t] = sampleEl(pickVisibleEl(t));
      }
    }

    // Effective canvas background color: walk body -> html -> main -> top full-width container
    let effectiveBgHex = null;
    let effectiveBgSource = null;
    const bgCandidates = [
      ['body', document.body],
      ['html', document.documentElement],
      ['main', document.querySelector('main')],
      ['#__next', document.querySelector('#__next')],
      ['#root', document.querySelector('#root')],
      ['#app', document.querySelector('#app')],
      ['section', document.querySelector('section')],
      ['canvas', document.querySelector('canvas')]
    ];
    for (const [label, el] of bgCandidates) {
      if (!el) continue;
      const hx = rgbToHex(window.getComputedStyle(el).backgroundColor);
      if (hx) {
        effectiveBgHex = hx;
        effectiveBgSource = label;
        break;
      }
    }
    if (!effectiveBgHex) {
      effectiveBgHex = '#ffffff';
      effectiveBgSource = 'browser-default';
    }

    // Extract :root CSS custom properties from accessible stylesheets + computed root
    const rootVars = {};
    try {
      for (const sheet of Array.from(document.styleSheets)) {
        let rules = null;
        try { rules = sheet.cssRules; } catch (_) { continue; }
        if (!rules) continue;
        for (const rule of Array.from(rules)) {
          if (rule.type === 1 && rule.selectorText && /(^|,|\\s)(:root|html)(\\s|,|$)/.test(rule.selectorText)) {
            const st = rule.style;
            for (let i = 0; i < st.length; i++) {
              const prop = st[i];
              if (prop && prop.startsWith('--') && !(prop in rootVars)) {
                const val = st.getPropertyValue(prop).trim();
                if (val && val.length <= 90) {
                  rootVars[prop] = val;
                }
                if (Object.keys(rootVars).length >= 160) break;
              }
            }
          }
          if (Object.keys(rootVars).length >= 160) break;
        }
        if (Object.keys(rootVars).length >= 160) break;
      }
    } catch (_) {}

    // Loaded document.fonts
    const loadedFonts = [];
    const seenFonts = new Set();
    try {
      if (document.fonts) {
        for (const f of document.fonts) {
          if (f.status === 'loaded') {
            const fam = (f.family || '').replace(/^["']|["']$/g, '').trim();
            const key = fam + '|' + f.weight + '|' + f.style;
            if (fam && !seenFonts.has(key)) {
              seenFonts.add(key);
              loadedFonts.push({ family: fam, weight: String(f.weight || ''), style: String(f.style || '') });
            }
          }
        }
      }
    } catch (_) {}

    // Landmarks
    const landmarks = [];
    for (const el of Array.from(document.querySelectorAll('header, nav, aside, main, section, article, footer')).slice(0, 25)) {
      const r = el.getBoundingClientRect();
      if (r.width > 20 && r.height > 10) {
        const lcs = window.getComputedStyle(el);
        const lbTop = parseFloat(lcs.borderTopWidth || '0') || 0;
        const lbBot = parseFloat(lcs.borderBottomWidth || '0') || 0;
        const lbLeft = parseFloat(lcs.borderLeftWidth || '0') || 0;
        const lbRight = parseFloat(lcs.borderRightWidth || '0') || 0;
        const lHasBorder = (
          (lbTop > 0 && lcs.borderTopStyle && lcs.borderTopStyle !== 'none' && lcs.borderTopStyle !== 'hidden') ||
          (lbBot > 0 && lcs.borderBottomStyle && lcs.borderBottomStyle !== 'none' && lcs.borderBottomStyle !== 'hidden') ||
          (lbLeft > 0 && lcs.borderLeftStyle && lcs.borderLeftStyle !== 'none' && lcs.borderLeftStyle !== 'hidden') ||
          (lbRight > 0 && lcs.borderRightStyle && lcs.borderRightStyle !== 'none' && lcs.borderRightStyle !== 'hidden')
        );
        landmarks.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          className: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
          x: Math.round(r.x),
          y: Math.round(r.y),
          width: Math.round(r.width),
          height: Math.round(r.height),
          borderHex: rgbToHex(lcs.borderColor),
          borderTopWidth: lcs.borderTopWidth,
          borderBottomWidth: lcs.borderBottomWidth,
          borderLeftWidth: lcs.borderLeftWidth,
          borderRightWidth: lcs.borderRightWidth,
          hasVisibleBorder: lHasBorder
        });
      }
      if (landmarks.length >= 16) break;
    }

    const imgs = Array.from(document.querySelectorAll('img'));
    const loadedImgs = imgs.filter(img => img.complete && img.naturalWidth > 0).length;
    const bodyText = (document.body ? (document.body.innerText || '') : '').replace(/\\s+/g, ' ').trim();

    return {
      effectiveBgHex,
      effectiveBgSource,
      elements,
      rootVars,
      loadedFonts: loadedFonts.slice(0, 40),
      landmarks,
      domMetrics: {
        imgCount: imgs.length,
        loadedImgs,
        canvasCount: document.querySelectorAll('canvas').length,
        svgCount: document.querySelectorAll('svg').length,
        videoCount: document.querySelectorAll('video').length,
        scrollHeight: Math.max(
          document.body ? document.body.scrollHeight : 0,
          document.documentElement ? document.documentElement.scrollHeight : 0
        ),
        visibleTextLen: bodyText.length,
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight
      },
      visibleTextSample: bodyText.slice(0, 1200)
    };
  } catch (e) {
    return { error: String(e), elements: {}, rootVars: {}, loadedFonts: [], landmarks: [], domMetrics: {} };
  }
})();
"""

PRELOADER_CHECK_JS = """
(() => {
  try {
    const bodyText = (document.body ? (document.body.innerText || '') : '').replace(/\\s+/g, ' ').trim();
    const canvasCount = document.querySelectorAll('canvas').length;
    const vw = window.innerWidth || 1440;
    const vh = window.innerHeight || 900;
    let preloaderActive = false;
    let preloaderReason = '';
    const PRELOADER_TEXT_RE = /setting\\s+up\\s+the\\s+paper\\s+theatre|loading\\s+armor\\s+experience|\\b(setting\\s+up|loading\\.{2,3}|synchronizing|initializing|please\\s*wait|media\\s*loading|entering|preparing)\\b|(?:^|\\s|\\()0?\\d{1,2}\\s*%|100\\s*%/i;
    const STRONG_LOADER_PHRASE_RE = /setting\\s+up\\s+the\\s+paper\\s+theatre|loading\\s+armor\\s+experience|\\b(synchronizing|initializing|please\\s*wait|media\\s*loading)\\b/i;

    const candidates = document.querySelectorAll(
      '[class*="preload" i], [id*="preload" i], [class*="loader" i], [id*="loader" i], ' +
      '[class*="loading" i], [class*="curtain" i], [class*="splash" i], [class*="intro" i], [id*="intro" i]'
    );
    for (const el of Array.from(candidates).slice(0, 30)) {
      const cs = window.getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.25) continue;
      if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
      const r = el.getBoundingClientRect();
      if (r.width >= vw * 0.7 && r.height >= vh * 0.7) {
        const cls = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '')).toLowerCase();
        const z = parseInt(cs.zIndex || '0', 10) || 0;
        const elText = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!cls.includes('intro') || (cs.position === 'fixed' && (z >= 10 || elText.length < 80 || PRELOADER_TEXT_RE.test(elText)))) {
          preloaderActive = true;
          preloaderReason = elText.slice(0, 80) || ('fullscreen overlay .' + cls.slice(0, 40));
          break;
        }
      }
    }

    // Check visible elements currently in the viewport for active loader phrases / percentage counters
    if (!preloaderActive) {
      for (const el of Array.from(document.querySelectorAll('div, p, span, h1, h2, h3, section')).slice(0, 260)) {
        const cs = window.getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') < 0.2) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0 || r.bottom < 0 || r.top > vh) continue;
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!t || t.length > 160) continue;
        if (STRONG_LOADER_PHRASE_RE.test(t)) {
          preloaderActive = true;
          preloaderReason = t.slice(0, 80);
          break;
        }
        if (/^\\s*(?:loading|setting\\s+up|initializing|synchronizing)\\b/i.test(t) && t.length < 60) {
          preloaderActive = true;
          preloaderReason = t.slice(0, 80);
          break;
        }
      }
    }

    if (!preloaderActive && bodyText.length > 0 && bodyText.length < 260) {
      if (PRELOADER_TEXT_RE.test(bodyText) || /\\bloading\\b/i.test(bodyText) || /^0?\\d{1,3}$/.test(bodyText)) {
        preloaderActive = true;
        preloaderReason = bodyText.slice(0, 80);
      }
    }
    const scrollHeight = Math.max(
      document.body ? document.body.scrollHeight : 0,
      document.documentElement ? document.documentElement.scrollHeight : 0
    );
    return {
      visibleTextLen: bodyText.length,
      canvasCount,
      preloaderActive,
      preloaderReason,
      scrollHeight
    };
  } catch (e) {
    return { visibleTextLen: 0, canvasCount: 0, preloaderActive: false, preloaderReason: '', scrollHeight: 900 };
  }
})();
"""

SCROLL_HYDRATE_DEEP_JS = """
async () => {
  try {
    const maxH = Math.min(
      Math.max(
        document.body ? document.body.scrollHeight : 0,
        document.documentElement ? document.documentElement.scrollHeight : 0
      ),
      2400
    );
    if (maxH > 600) {
      const steps = 10;
      for (let i = 1; i <= steps; i++) {
        window.scrollTo(0, Math.round((maxH * i) / steps));
        await new Promise(r => setTimeout(r, 420));
      }
    }
    const imgs = Array.from(document.querySelectorAll('img')).slice(0, 80).filter(img => {
      const r = img.getBoundingClientRect();
      return (r.width > 0 || r.height > 0 || img.src || img.currentSrc) && !img.complete;
    });
    if (imgs.length > 0) {
      await Promise.race([
        Promise.all(imgs.map(img => new Promise(res => {
          img.addEventListener('load', res, { once: true });
          img.addEventListener('error', res, { once: true });
        }))),
        new Promise(r => setTimeout(r, 3000))
      ]);
    }
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 850));
    return Math.max(
      document.body ? document.body.scrollHeight : 0,
      document.documentElement ? document.documentElement.scrollHeight : 0
    );
  } catch (_) {
    try { window.scrollTo(0, 0); } catch (__) {}
    return 900;
  }
}
"""

SCROLL_HYDRATE_JS = """
async () => {
  try {
    const maxH = Math.min(
      Math.max(
        document.body ? document.body.scrollHeight : 0,
        document.documentElement ? document.documentElement.scrollHeight : 0
      ),
      2400
    );
    if (maxH > 600) {
      const steps = 4;
      for (let i = 1; i <= steps; i++) {
        window.scrollTo(0, Math.round((maxH * i) / steps));
        await new Promise(r => setTimeout(r, 250));
      }
    }
    // Wait up to 2s for first 40 visible images to complete
    const imgs = Array.from(document.querySelectorAll('img')).slice(0, 40).filter(img => {
      const r = img.getBoundingClientRect();
      return (r.width > 0 || r.height > 0 || img.src || img.currentSrc) && !img.complete;
    });
    if (imgs.length > 0) {
      await Promise.race([
        Promise.all(imgs.map(img => new Promise(res => {
          img.addEventListener('load', res, { once: true });
          img.addEventListener('error', res, { once: true });
        }))),
        new Promise(r => setTimeout(r, 2000))
      ]);
    }
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 600));
    return Math.max(
      document.body ? document.body.scrollHeight : 0,
      document.documentElement ? document.documentElement.scrollHeight : 0
    );
  } catch (_) {
    try { window.scrollTo(0, 0); } catch (__) {}
    return 900;
  }
}
"""


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def optimize_png_bytes(raw_bytes: bytes) -> bytes:
    """Losslessly optimize PNG via PIL optimize=True, compress_level=9."""
    if not raw_bytes:
        return raw_bytes
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True, compress_level=9)
            opt = out.getvalue()
            return opt if len(opt) < len(raw_bytes) else raw_bytes
    except Exception:
        return raw_bytes


def _slice_visual_stats(gray: np.ndarray) -> Tuple[float, float, float, float]:
    """Compute (gray_std, dominant_q16_pct, edge_density, modal_luma) on a 2D float32 array."""
    if gray.size == 0 or gray.shape[0] < 2 or gray.shape[1] < 2:
        return (0.0, 100.0, 0.0, 0.0)
    gray_std = float(gray.std())
    q = np.clip((gray // 16).astype(np.int32), 0, 15)
    counts = np.bincount(q.ravel(), minlength=16)
    dom_bin = int(np.argmax(counts))
    dominant_q16_pct = float(counts[dom_bin] / q.size * 100.0)
    modal_luma = float(dom_bin * 16 + 8)
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    edges = (dx[:-1, :] > 15.0) | (dy[:, :-1] > 15.0)
    edge_density = float(edges.mean())
    return (
        round(gray_std, 2),
        round(dominant_q16_pct, 2),
        round(edge_density, 5),
        round(modal_luma, 1),
    )


def _analyze_blank_runs(
    gray: np.ndarray,
    row_std_thresh: float = 3.0,
    row_edge_thresh: float = 0.002,
) -> Dict[str, Any]:
    """
    100% color-agnostic vertical blank-void & blank-tail analyzer (detects BOTH dark & light solid voids).
    Flags any contiguous vertical run of rows where row_std < 3.0 and row_edge_density < 0.002:
    - blank tail: on tall PNGs (h > 1200), a bottom/near-bottom run covering >= max(350px, 35% of h)
      (reaching within 80px of the bottom edge, catching peeking slivers after an unhydrated void),
      OR classic bottom-40% slice flatness (tail_std < 6.0 and tail_edge < 0.002).
    - mid-page blank void: any contiguous solid run >= 450px tall anywhere in the image.
    """
    h = int(gray.shape[0]) if gray.ndim >= 2 else 0
    w = int(gray.shape[1]) if gray.ndim >= 2 else 0
    if h < 4 or w < 4:
        return {
            "has_blank_tail": False,
            "blank_tail_start_y": None,
            "has_mid_blank_void": False,
            "max_blank_void_px": 0,
            "mid_blank_void_range": None,
            "tail_gray_std": 0.0,
            "tail_edge_density": 0.0,
        }

    row_std = gray.std(axis=1)
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    row_edge = np.zeros(h, dtype=np.float32)
    row_edge[:-1] = ((dx[:-1, :] > 15.0) | (dy[:, :-1] > 15.0)).mean(axis=1)
    row_edge[-1] = (dx[-1, :] > 15.0).mean()
    blank_rows = (row_std < row_std_thresh) & (row_edge < row_edge_thresh)

    # Extract contiguous runs of blank rows (color-agnostic: black, charcoal, gray, beige, or white)
    runs: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    for y, is_blank in enumerate(blank_rows):
        if is_blank and run_start is None:
            run_start = y
        elif not is_blank and run_start is not None:
            runs.append((run_start, y))
            run_start = None
    if run_start is not None:
        runs.append((run_start, h))

    max_run_px = 0
    max_run_range: Optional[Tuple[int, int]] = None
    for y0, y1 in runs:
        rlen = y1 - y0
        if rlen > max_run_px:
            max_run_px = rlen
            max_run_range = (y0, y1)

    tail_start_40 = int(h * 0.60)
    tail_std, _, tail_edge, _ = _slice_visual_stats(gray[tail_start_40:, :]) if h > 1200 else _slice_visual_stats(gray)

    has_blank_tail = False
    blank_tail_start_y: Optional[int] = None
    if h > 1200:
        min_tail_run = max(350, int(h * 0.35))
        # Check bottom / near-bottom contiguous solid run (allows <= 80px peeking bottom sliver)
        tail_candidates = [
            (y0, y1) for y0, y1 in runs
            if (y1 - y0) >= min_tail_run and y1 >= (h - 80)
        ]
        if tail_candidates:
            has_blank_tail = True
            blank_tail_start_y = tail_candidates[-1][0]
            ty0, ty1 = tail_candidates[-1]
            t_std, _, t_edge, _ = _slice_visual_stats(gray[ty0:ty1, :])
            tail_std = min(tail_std, t_std)
            tail_edge = min(tail_edge, t_edge)
        elif tail_edge < 0.002 and tail_std < 6.0:
            has_blank_tail = True
            # Find last non-blank row above tail_start_40
            non_blank_ys = np.flatnonzero(~blank_rows)
            blank_tail_start_y = int(non_blank_ys[-1]) + 1 if non_blank_ys.size else tail_start_40

    # Any contiguous solid void >= 450px tall (dark or light)
    mid_runs = [(y0, y1) for y0, y1 in runs if (y1 - y0) >= 450]
    has_mid_blank_void = bool(mid_runs)
    mid_void_range = list(max(mid_runs, key=lambda r: r[1] - r[0])) if mid_runs else None

    return {
        "has_blank_tail": has_blank_tail,
        "blank_tail_start_y": blank_tail_start_y,
        "has_mid_blank_void": has_mid_blank_void,
        "max_blank_void_px": int(max_run_px),
        "mid_blank_void_range": mid_void_range,
        "tail_gray_std": tail_std,
        "tail_edge_density": tail_edge,
    }


def compute_png_visual_metrics(
    source: Path | bytes,
    top_fold_height: int = 900,
) -> Dict[str, Any]:
    """Compute deterministic PIL/NumPy visual health metrics for a PNG file or bytes."""
    try:
        if isinstance(source, Path):
            if not source.exists():
                return {"valid": False, "width": 0, "height": 0, "bytes": 0, "sha256": ""}
            raw = source.read_bytes()
        else:
            raw = source
        if not raw:
            return {"valid": False, "width": 0, "height": 0, "bytes": 0, "sha256": ""}
        sha256 = hashlib.sha256(raw).hexdigest()
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
            gray = np.asarray(img.convert("L"), dtype=np.float32)
        gray_std, dom_q16, edge_dens, modal_luma = _slice_visual_stats(gray)
        mean_luma = round(float(gray.mean()), 1) if gray.size else 0.0
        fold_h = min(h, top_fold_height)
        tf_std, tf_q16, tf_edge, tf_luma = _slice_visual_stats(gray[:fold_h, :])
        blank_info = _analyze_blank_runs(gray)
        return {
            "valid": True,
            "width": int(w),
            "height": int(h),
            "bytes": len(raw),
            "sha256": sha256,
            "gray_std": gray_std,
            "dominant_q16_pct": dom_q16,
            "edge_density": edge_dens,
            "modal_luma": modal_luma,
            "mean_luma": mean_luma,
            "top_fold_gray_std": tf_std,
            "top_fold_dominant_q16_pct": tf_q16,
            "top_fold_edge_density": tf_edge,
            "top_fold_modal_luma": tf_luma,
            "tail_gray_std": blank_info["tail_gray_std"],
            "tail_edge_density": blank_info["tail_edge_density"],
            "has_blank_tail": blank_info["has_blank_tail"],
            "blank_tail_start_y": blank_info["blank_tail_start_y"],
            "has_mid_blank_void": blank_info["has_mid_blank_void"],
            "max_blank_void_px": blank_info["max_blank_void_px"],
            "mid_blank_void_range": blank_info["mid_blank_void_range"],
        }
    except Exception as exc:
        return {
            "valid": False,
            "width": 0,
            "height": 0,
            "bytes": len(source) if isinstance(source, bytes) else 0,
            "sha256": "",
            "error": str(exc),
        }


def crop_blank_tail_png(png_bytes: bytes, min_h: int, crop_mid_void: bool = True) -> Tuple[bytes, bool]:
    """
    Color-agnostic crop of unhydrated dark OR light blank bottom tails (>1200px PNG where bottom
    >=35% / >=350px is flat void) or >=450px solid mid-page sticky-scroll voids starting at/after min_h.
    """
    if not png_bytes:
        return png_bytes, False
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            w, h = img.size
            if h <= 1200:
                return png_bytes, False
            gray = np.asarray(img.convert("L"), dtype=np.float32)
            blank_info = _analyze_blank_runs(gray)
            target_h: Optional[int] = None

            if blank_info["has_blank_tail"] and blank_info["blank_tail_start_y"] is not None:
                cut_y = int(blank_info["blank_tail_start_y"])
                target_h = max(min_h, min(h, cut_y + 48))
            elif crop_mid_void and blank_info["has_mid_blank_void"] and blank_info["mid_blank_void_range"]:
                vy0, _ = blank_info["mid_blank_void_range"]
                if vy0 >= (min_h - 40):
                    target_h = max(min_h, min(h, int(vy0)))

            if target_h is None or target_h >= h - 60:
                return png_bytes, False
            cropped = img.crop((0, 0, w, target_h))
            out = io.BytesIO()
            cropped.save(out, format="PNG", optimize=True, compress_level=9)
            return out.getvalue(), True
    except Exception:
        return png_bytes, False


def detect_preloader_text(visible_text: str, visible_text_len: Optional[int] = None) -> Optional[str]:
    """
    Detect unhydrated preloader-frame visible text (e.g. '4%', 'Setting up the paper theatre',
    'Loading...', 'Synchronizing', 'Initializing', 'Please wait') on short or splash-dominated captures.
    """
    vis = re.sub(r"\s+", " ", (visible_text or "")).strip()
    vlen = int(visible_text_len) if visible_text_len is not None else len(vis)
    if not vis:
        return None
    if re.search(r"setting\s+up\s+the\s+paper\s+theatre", vis, re.I):
        return "Setting up the paper theatre"
    if re.search(r"loading\s+armor\s+experience", vis, re.I):
        return "LOADING ARMOR EXPERIENCE"
    if vlen < 250:
        m_pct = re.search(r"\b\d{1,2}\s*%", vis)
        if m_pct:
            return f"preloader progress '{m_pct.group(0)}' ({vlen} chars visible)"
        m_kw = re.search(
            r"\b(setting\s+up|loading\.{2,3}|synchronizing|initializing|please\s+wait)\b",
            vis,
            re.I,
        )
        if m_kw:
            return f"preloader phrase '{m_kw.group(0)}' ({vlen} chars visible)"
        if re.search(r"(?:^|\s)loading(?:\s|$|[.!…])", vis, re.I):
            return f"preloader text 'loading' ({vlen} chars visible)"
    if vlen < 420 and re.search(r"\bLOADING\b", vis[:120]):
        return f"preloader splash 'LOADING' ({vlen} chars visible)"
    return None


def is_visual_unhealthy_for_tier2(metrics: Dict[str, Any]) -> bool:
    if not metrics or not metrics.get("valid"):
        return True
    return (
        metrics.get("gray_std", 0.0) < 12.0
        or metrics.get("dominant_q16_pct", 100.0) > 93.0
        or metrics.get("edge_density", 0.0) < 0.004
    )


def _is_viewport_blank_or_unhealthy(metrics: Dict[str, Any]) -> bool:
    if is_visual_unhealthy_for_tier2(metrics):
        return True
    if (
        metrics.get("top_fold_gray_std", 99.0) < 3.0
        and metrics.get("top_fold_dominant_q16_pct", 0.0) > 98.5
    ):
        return True
    return False


def visual_entropy_score(metrics: Dict[str, Any]) -> float:
    if not metrics or not metrics.get("valid"):
        return -1.0
    return (
        float(metrics.get("gray_std", 0.0))
        + float(metrics.get("edge_density", 0.0)) * 1000.0
        + (100.0 - float(metrics.get("dominant_q16_pct", 100.0))) * 0.5
    )


def detect_block_or_error(
    http_status: Optional[int],
    title: str,
    visible_text: str,
    dom_html: str,
    canvas_count: int = 0,
    nav_error: Optional[str] = None,
) -> Optional[str]:
    """
    Strictly and honestly detect WAF/CAPTCHA/platform-error/dead/browser-gate pages.
    Inspects HTTP status, page title, and visible rendered body text (NOT raw <script> tags),
    so HTTP 200 sites embedding Cloudflare Turnstile contact-form scripts are NEVER misclassified.
    """
    if nav_error and http_status is None:
        return f"Network/Navigation failure ({nav_error[:140]})"

    if http_status is None:
        return "No HTTP response received (connection closed / DNS / TLS error)"

    t_clean = re.sub(r"\s+", " ", (title or "")).strip()
    v_clean = re.sub(r"\s+", " ", (visible_text or "")).strip()
    combined = f"{t_clean} {v_clean}"
    vlen = len(v_clean)

    if http_status >= 400:
        detail = ""
        if re.search(r"cloudfront|request could not be satisfied", combined, re.I):
            detail = " — CloudFront WAF Block"
        elif re.search(r"akamai|access denied|reference #", combined, re.I):
            detail = " — Edge WAF Access Denied"
        elif re.search(r"cloudflare|just a moment|520:|521:|522:|523:|524:|525:|526:|web server is returning an unknown error", combined, re.I):
            detail = " — Cloudflare Origin/WAF Error"
        elif re.search(r"vercel security checkpoint", combined, re.I):
            detail = " — Vercel Security Checkpoint"
        elif re.search(r"deployment paused", combined, re.I):
            detail = " — Vercel Deployment Paused"
        elif re.search(r"framer", combined, re.I) and http_status == 404:
            detail = " — Site Not Found | Framer"
        elif t_clean:
            detail = f" ({t_clean[:70]})"
        return f"Upstream HTTP {http_status}{detail}"

    # HTTP 2xx / 3xx content-level honest block / dead / gate checks on visible text & title
    if re.search(r"your browser is not supported|browser not supported", combined, re.I) and vlen < 900:
        return f"Browser Compatibility Gate ({t_clean or 'Your browser is not supported'})"

    if re.search(
        r"this site needs webgpu|needs webgpu|graphics acceleration on|can['’]t light in this browser",
        f"{t_clean} {v_clean[:500]}",
        re.I,
    ) and (
        vlen < 600
        or re.search(r"this site needs webgpu|can['’]t light in this browser", v_clean[:120], re.I)
    ):
        return f"Browser/WebGPU Hardware Gate ({t_clean or 'WebGPU / Hardware GPU Required'})"

    if re.search(r"vercel security checkpoint", combined, re.I) and vlen < 1200:
        return "Vercel Anti-Bot Challenge Gate (Vercel Security Checkpoint)"

    if re.search(r"deployment paused|this deployment is temporarily paused", combined, re.I) and vlen < 1200:
        return "Vercel Platform Gate (Deployment Paused)"

    if re.search(r"just a moment\.\.\.|checking your browser|attention required!\s*\|\s*cloudflare|enable javascript and cookies to continue", combined, re.I) and vlen < 1200:
        return f"Cloudflare Anti-Bot Challenge ({t_clean or 'Just a moment...'})"

    if re.search(r"error\s+52[0-6]|web server is returning an unknown error", combined, re.I) and vlen < 1500:
        return f"Cloudflare Origin Error ({t_clean or 'Error 52x'})"

    if re.search(r"access denied", t_clean, re.I) and (re.search(r"permission to access|reference #|request could not be satisfied", v_clean, re.I) or vlen < 600):
        return f"Edge WAF Block ({t_clean})"

    if re.search(r"the request could not be satisfied", v_clean, re.I) and vlen < 1000:
        return "CloudFront WAF Block (The request could not be satisfied)"

    if re.search(r"confirm you are human|verify you are human|complete the security check", combined, re.I) and vlen < 900:
        return "Anti-Bot Human Verification Gate"

    if re.search(r"site not found\s*\|\s*framer|webflow.*404|page not found.*netlify", combined, re.I) and vlen < 800:
        return f"Platform 404 / Unhosted Site ({t_clean or 'Site Not Found'})"

    if re.search(r"^example domain$", t_clean, re.I) or ("this domain is for use in illustrative examples" in v_clean.lower() and vlen < 600):
        return "Placeholder Example Domain (no production site)"

    if re.search(r"(this domain is for sale|buy this domain|domain is parked|parked free, courtesy of)", combined, re.I) and vlen < 700:
        return f"Parked / For-Sale Domain ({t_clean[:60]})"

    if len((dom_html or "").strip()) < 100 and vlen == 0 and canvas_count == 0:
        return f"Empty DOM response ({len((dom_html or '').strip())} bytes, 0 visible text)"

    return None


def _sanitize_asset_filename(url: str, idx: int, ext: str) -> str:
    clean = url.split("?")[0].split("#")[0]
    base = clean.rstrip("/").split("/")[-1] or f"asset_{idx}{ext}"
    base = re.sub(r"[^a-zA-Z0-9._-]", "_", base)[:64]
    if not base.lower().endswith(ext):
        base += ext
    return f"{idx:03d}_{base}"


async def _cdp_clamped_screenshot(
    context: BrowserContext,
    page: Page,
    vw: int,
    min_h: int,
    scroll_height: int,
    pause_raf: bool = True,
    tier: str = "tier1_fast",
) -> bytes:
    """Capture a clamped PNG directly via CDP Page.captureScreenshot with timeout fallback."""
    # On Tier 2 SwiftShader WebGL pages (often fixed-viewport 100vh canvases with scroll-driver spacers),
    # capture the exact viewport (1440x900 / 390x844) so SwiftShader never stalls re-rasterizing 2400px tall WebGL.
    target_h = min_h if tier == "tier2_webgl" else max(min_h, min(int(scroll_height or min_h), MAX_CAPTURE_HEIGHT))
    cdp = await context.new_cdp_session(page)
    try:
        if pause_raf:
            try:
                await asyncio.wait_for(
                    page.evaluate(
                        """() => {
                            window.__PAUSE_RAF = true;
                            document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch (_) {} });
                        }"""
                    ),
                    timeout=2.5,
                )
            except Exception:
                pass
            await asyncio.sleep(0.15)

        if tier == "tier2_webgl":
            try:
                png = await asyncio.wait_for(
                    page.screenshot(full_page=False, animations="disabled", timeout=16000),
                    timeout=17.0,
                )
                if png and len(png) > 100:
                    return png
            except Exception:
                pass

        try:
            res = await asyncio.wait_for(
                cdp.send(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "fromSurface": True,
                        "captureBeyondViewport": target_h > min_h,
                        "clip": {
                            "x": 0,
                            "y": 0,
                            "width": vw,
                            "height": target_h,
                            "scale": 1,
                        },
                    },
                ),
                timeout=8.0 if tier == "tier2_webgl" else 15.0,
            )
            raw = base64.b64decode(res.get("data") or "")
            if len(raw) <= 100:
                raise RuntimeError("Empty CDP screenshot bytes")
            return raw
        except Exception:
            # Viewport-only CDP fallback, then Playwright page.screenshot fallback
            try:
                res = await asyncio.wait_for(
                    cdp.send(
                        "Page.captureScreenshot",
                        {
                            "format": "png",
                            "fromSurface": True,
                            "captureBeyondViewport": False,
                            "clip": {
                                "x": 0,
                                "y": 0,
                                "width": vw,
                                "height": min_h,
                                "scale": 1,
                            },
                        },
                    ),
                    timeout=6.0 if tier == "tier2_webgl" else 10.0,
                )
                raw = base64.b64decode(res.get("data") or "")
                if len(raw) <= 100:
                    raise RuntimeError("Empty viewport CDP screenshot bytes")
                return raw
            except Exception:
                return await asyncio.wait_for(
                    page.screenshot(full_page=False, animations="disabled", timeout=15000),
                    timeout=16.0,
                )
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


async def _dismiss_consent_all_frames(page: Page, allow_hide_fallback: bool = True) -> bool:
    """
    Dismiss GDPR/cookie consent modals across main frame, open shadow roots, and child iframes
    (e.g. Sourcepoint/Politico, Usercentrics, Cookiebot, Didomi, OneTrust).
    Strictly consent-scoped; NEVER touches CAPTCHA, Turnstile, age-gate, or login gates.
    """
    dismissed = False
    # 1. Try clicking accept inside main frame + all child iframes
    frames = []
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = [page.main_frame]

    for fr in frames[:12]:
        try:
            cd = await asyncio.wait_for(fr.evaluate(COOKIE_DISMISS_JS), timeout=1.8)
            if isinstance(cd, dict) and cd.get("dismissed"):
                dismissed = True
                await asyncio.sleep(0.45)
                break
        except Exception:
            continue

    # 2. Safe strictly-consent-scoped overlay hide fallback (only hides consent/CMP dialogs & backdrops)
    if allow_hide_fallback:
        try:
            hd = await asyncio.wait_for(page.evaluate(COOKIE_HIDE_FALLBACK_JS), timeout=1.5)
            if isinstance(hd, dict) and int(hd.get("hidden", 0)) > 0:
                dismissed = True
                await asyncio.sleep(0.2)
        except Exception:
            pass
    return dismissed


async def _dismiss_promo_modals(page: Page) -> bool:
    """
    R4-1: Generic promotional/newsletter/voucher modal dismissal across main frame, open shadow roots,
    and child iframes. Clicks visible close/dismiss controls inside fixed/dialog overlays while strictly
    excluding <nav>/<header>/<footer>/<main>, search bars, login/password modals, age gates, and CAPTCHAs.
    """
    dismissed = False
    frames = []
    try:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        frames = [page.main_frame]

    for fr in frames[:8]:
        try:
            # Temporarily resume rAF if paused so Vue/React modal leave-transitions execute cleanly
            if fr == page.main_frame:
                try:
                    await asyncio.wait_for(
                        fr.evaluate("() => { if (window.__PAUSE_RAF && window.__RESUME_RAF) window.__RESUME_RAF(); }"),
                        timeout=1.0,
                    )
                except Exception:
                    pass
            res = await asyncio.wait_for(fr.evaluate(PROMO_MODAL_DISMISS_JS), timeout=2.0)
            if isinstance(res, dict) and res.get("dismissed"):
                dismissed = True
                await asyncio.sleep(0.65)
        except Exception:
            continue
    return dismissed


async def _capture_single_viewport(
    browser: Browser,
    url: str,
    is_mobile: bool,
    tier: str,
    collect_assets: bool = False,
    retry_extended: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one live viewport capture (desktop or mobile) on a given Browser instance."""
    vw = MOBILE_WIDTH if is_mobile else DESKTOP_WIDTH
    min_h = MOBILE_MIN_HEIGHT if is_mobile else DESKTOP_MIN_HEIGHT

    ctx_kwargs: Dict[str, Any] = {
        "viewport": {"width": vw, "height": min_h},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "user_agent": MOBILE_UA if is_mobile else DESKTOP_UA,
    }
    if is_mobile:
        ctx_kwargs["is_mobile"] = True
        ctx_kwargs["has_touch"] = True
    if auth_storage_state:
        ctx_kwargs["storage_state"] = auth_storage_state

    context = await browser.new_context(**ctx_kwargs)
    collected_styles: List[Tuple[str, bytes]] = []
    collected_apis: List[Dict[str, Any]] = []
    scripts_count = 0

    try:
        # Always inject self-yielding rAF pause hook (0ms overhead on fast frames;
        # prevents 60fps WebGL/JS loops from starving CDP or page.evaluate)
        await context.add_init_script(RAF_PAUSE_INIT_SCRIPT)
        page = await context.new_page()

        if collect_assets:
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

        nav_timeout_ms = 35000 if retry_extended else 22000
        nav_timeout_s = 38.0 if retry_extended else 25.0
        http_status: Optional[int] = None
        nav_error: Optional[str] = None
        redirect_chain: List[str] = []

        try:
            resp = await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms),
                timeout=nav_timeout_s,
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

        # If transport failed completely (no HTTP response), return early so Tier 3 or block check handles it
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
                "dom_timed_out": False,
                "cookie_dismissed": False,
                "computed_styles": {},
                "storage": {},
                "png_bytes": b"",
                "metrics": {"valid": False, "width": 0, "height": 0, "bytes": 0},
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
            }

        # Fast-path for Tier 2 SwiftShader WebGL: avoid stacking 25s of sequential eval timeouts
        # against a heavy software-WebGL renderer. Let preload settle unthrottled, pause rAF + video,
        # extract DOM/computed_styles cleanly, then capture the exact viewport.
        if tier == "tier2_webgl":
            await asyncio.sleep((4.0 if not is_mobile else 3.0) if retry_extended else (3.0 if not is_mobile else 2.0))
            preloader_stuck = False
            preloader_reason = ""
            if retry_extended:
                waited_t2 = 0.0
                max_t2 = 16.0 if not is_mobile else 10.0
                while waited_t2 < max_t2:
                    try:
                        pcheck_t2 = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.0)
                        preloader_stuck = bool(pcheck_t2.get("preloaderActive"))
                        preloader_reason = str(pcheck_t2.get("preloaderReason") or "")
                        vis_t2 = int(pcheck_t2.get("visibleTextLen", 0))
                        if not preloader_stuck and vis_t2 >= 120:
                            break
                    except Exception:
                        break
                    step = min(2.0, max_t2 - waited_t2)
                    await asyncio.sleep(step)
                    waited_t2 += step
                    if waited_t2 == 6.0:
                        try:
                            await page.evaluate("() => window.scrollBy(0, 120)")
                        except Exception:
                            pass
                try:
                    pfinal_t2 = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.0)
                    preloader_stuck = bool(pfinal_t2.get("preloaderActive"))
                    preloader_reason = str(pfinal_t2.get("preloaderReason") or preloader_reason)
                except Exception:
                    pass

            cookie_dismissed = False
            try:
                if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                    cookie_dismissed = True
            except Exception:
                pass

            pause_ok = False
            try:
                await asyncio.wait_for(
                    page.evaluate(
                        """() => {
                            window.__PAUSE_RAF = true;
                            document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch (_) {} });
                        }"""
                    ),
                    timeout=2.5,
                )
                pause_ok = True
            except Exception:
                pass
            if pause_ok:
                await asyncio.sleep(0.15)

            title = ""
            dom_html = ""
            computed_styles: Dict[str, Any] = {}
            storage_data: Dict[str, Any] = {"localStorage": {}, "sessionStorage": {}}
            visible_text = ""
            canvas_count = 1
            dom_timed_out = False

            # Capture viewport screenshot first when main thread is saturated by SwiftShader WebGL
            png_bytes = b""
            metrics: Dict[str, Any] = {"valid": False, "width": 0, "height": 0, "bytes": 0}

            if pause_ok and not cookie_dismissed:
                try:
                    if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                        cookie_dismissed = True
                except Exception:
                    pass

            if http_status is not None and http_status < 400:
                try:
                    png_bytes = await _cdp_clamped_screenshot(
                        context=context,
                        page=page,
                        vw=vw,
                        min_h=min_h,
                        scroll_height=min_h,
                        pause_raf=False,
                        tier="tier2_webgl",
                    )
                    metrics = compute_png_visual_metrics(png_bytes, top_fold_height=min_h)
                except Exception as exc:
                    metrics = {"valid": False, "width": 0, "height": 0, "bytes": 0, "error": str(exc)}
                # On 0B/invalid PNG, retry once with viewport-only page.screenshot before giving up
                if not metrics.get("valid"):
                    try:
                        png_bytes = await asyncio.wait_for(
                            page.screenshot(full_page=False, animations="disabled", timeout=15000),
                            timeout=16.5,
                        )
                        metrics = compute_png_visual_metrics(png_bytes, top_fold_height=min_h)
                    except Exception as exc:
                        metrics = {"valid": False, "width": 0, "height": 0, "bytes": 0, "error": str(exc)}

            if collect_assets:
                t2_dom_timeout = 6.0 if retry_extended else 4.0
                try:
                    title = await asyncio.wait_for(page.title(), timeout=2.5)
                except Exception:
                    pass
                try:
                    dom_html = await asyncio.wait_for(page.content(), timeout=t2_dom_timeout)
                except Exception:
                    dom_timed_out = True
                try:
                    computed_styles = await asyncio.wait_for(page.evaluate(COMPUTED_STYLES_JS), timeout=t2_dom_timeout)
                    visible_text = computed_styles.get("visibleTextSample", "")
                    dm = computed_styles.get("domMetrics", {})
                    if isinstance(dm.get("canvasCount"), int):
                        canvas_count = max(canvas_count, dm["canvasCount"])
                    if not preloader_stuck:
                        p_txt = detect_preloader_text(visible_text, dm.get("visibleTextLen"))
                        if p_txt:
                            preloader_stuck = True
                            preloader_reason = p_txt
                except Exception:
                    pass
                if pause_ok:
                    try:
                        storage_data = await asyncio.wait_for(
                            page.evaluate("""() => {
                                const ls = {}, ss = {};
                                try {
                                  for (let i = 0; i < localStorage.length; i++) {
                                    const k = localStorage.key(i);
                                    ls[k] = String(localStorage.getItem(k) || '').slice(0, 500);
                                  }
                                } catch (_) {}
                                try {
                                  for (let i = 0; i < sessionStorage.length; i++) {
                                    const k = sessionStorage.key(i);
                                    ss[k] = String(sessionStorage.getItem(k) || '').slice(0, 500);
                                  }
                                } catch (_) {}
                                return { localStorage: ls, sessionStorage: ss };
                            }"""),
                            timeout=1.5,
                        )
                    except Exception:
                        pass

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
                "dom_timed_out": dom_timed_out,
                "cookie_dismissed": cookie_dismissed,
                "preloader_stuck": preloader_stuck,
                "preloader_reason": preloader_reason,
                "computed_styles": computed_styles,
                "storage": storage_data,
                "png_bytes": png_bytes,
                "metrics": metrics,
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
            }

        # Non-fatal networkidle wait (Tier 1 & Tier 3)
        if is_mobile:
            idle_ms = 3500 if retry_extended else 2000
        else:
            idle_ms = 5500 if retry_extended else 3000
        try:
            await asyncio.wait_for(page.wait_for_load_state("networkidle", timeout=idle_ms), timeout=(idle_ms / 1000.0) + 0.5)
        except Exception:
            pass

        # Non-fatal fonts.ready wait
        eval_timeouts = 0
        if is_mobile:
            fonts_cap = 2.5 if retry_extended else 1.8
        else:
            fonts_cap = 3.5 if retry_extended else 2.0
        try:
            await asyncio.wait_for(
                page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true"),
                timeout=fonts_cap,
            )
        except Exception:
            eval_timeouts += 1

        # Multilingual GDPR cookie-banner dismissal across main frame, shadow roots & iframes (strictly consent-only)
        cookie_dismissed = False
        try:
            if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                cookie_dismissed = True
        except Exception:
            eval_timeouts += 1

        # Fast-fail Tier 1 stall guard: if a heavy WebGL/JS loop stalls headless shell under --disable-gpu,
        # bail out early so Tier 2 (full Chromium + SwiftShader) takes over with plenty of deadline left.
        if tier == "tier1_fast" and not is_mobile and eval_timeouts >= 2 and http_status == 200:
            quick_dom = ""
            try:
                quick_dom = await asyncio.wait_for(page.content(), timeout=2.5)
            except Exception:
                pass
            has_webgl_hint = any(k in quick_dom.lower() for k in ("<canvas", "webgl", "three", "glsl", "shader"))
            return {
                "http_status": http_status,
                "initial_url": url,
                "final_url": page.url or final_url,
                "redirect_chain": redirect_chain,
                "nav_error": nav_error,
                "title": "",
                "dom_html": quick_dom,
                "visible_text": "",
                "canvas_count": 1 if has_webgl_hint else 0,
                "tier1_stalled": True,
                "dom_timed_out": not bool(quick_dom),
                "cookie_dismissed": cookie_dismissed,
                "preloader_stuck": False,
                "preloader_reason": "",
                "computed_styles": {},
                "storage": {},
                "png_bytes": b"",
                "metrics": {"valid": False, "width": 0, "height": 0, "bytes": 0, "error": "tier1_eval_stall"},
                "styles": collected_styles,
                "apis": collected_apis,
                "scripts_count": scripts_count,
            }

        # Scroll-to-hydrate lazy media & reveal animations (skip scroll on Tier 2 WebGL 100vh stages)
        scroll_height = min_h
        try:
            scroll_script = SCROLL_HYDRATE_DEEP_JS if retry_extended else SCROLL_HYDRATE_JS
            scroll_timeout = 12.0 if retry_extended else 5.5
            sh = await asyncio.wait_for(page.evaluate(scroll_script), timeout=scroll_timeout)
            if isinstance(sh, (int, float)) and sh > 0:
                scroll_height = int(sh)
        except Exception:
            pass

        # Second pass cookie-banner dismiss + generic promo/newsletter modal dismiss
        try:
            if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                cookie_dismissed = True
        except Exception:
            pass
        try:
            await _dismiss_promo_modals(page)
        except Exception:
            pass

        # Preloader settlement check (up to 16.0s polling when retry_extended is set)
        canvas_count = 0
        preloader_stuck = False
        preloader_reason = ""
        try:
            pcheck = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.5)
            vis_len = int(pcheck.get("visibleTextLen", 0))
            canvas_count = int(pcheck.get("canvasCount", 0))
            preloader_active = bool(pcheck.get("preloaderActive", False))
            preloader_reason = str(pcheck.get("preloaderReason") or "")
            if pcheck.get("scrollHeight"):
                scroll_height = max(scroll_height, int(pcheck["scrollHeight"]))

            if tier == "tier2_webgl":
                # Already settled above; avoid redundant double-sleep on SwiftShader
                pass
            elif vis_len < 150 or preloader_active:
                if retry_extended:
                    waited = 0.0
                    max_settle = 16.0 if not is_mobile else 10.0
                    while waited < max_settle and (vis_len < 150 or preloader_active):
                        step = min(2.0, max_settle - waited)
                        await asyncio.sleep(step)
                        waited += step
                        if waited == 6.0:
                            try:
                                await page.evaluate("() => window.scrollBy(0, 120)")
                            except Exception:
                                pass
                        try:
                            p2 = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.0)
                            vis_len = int(p2.get("visibleTextLen", vis_len))
                            canvas_count = max(canvas_count, int(p2.get("canvasCount", 0)))
                            preloader_active = bool(p2.get("preloaderActive", False))
                            if p2.get("preloaderReason"):
                                preloader_reason = str(p2["preloaderReason"])
                            if p2.get("scrollHeight"):
                                scroll_height = max(scroll_height, int(p2["scrollHeight"]))
                        except Exception:
                            break
                else:
                    settle = 3.5 if canvas_count > 0 else 2.5
                    await asyncio.sleep(settle)
                    try:
                        p2 = await asyncio.wait_for(page.evaluate(PRELOADER_CHECK_JS), timeout=2.0)
                        preloader_active = bool(p2.get("preloaderActive", False))
                        if p2.get("preloaderReason"):
                            preloader_reason = str(p2["preloaderReason"])
                    except Exception:
                        pass
                # Re-try cookie dismiss once after preloader lifts
                try:
                    if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                        cookie_dismissed = True
                except Exception:
                    pass

            preloader_stuck = bool(preloader_active)

            # Ensure scrolled back to top and re-read final scrollHeight
            try:
                sh2 = await asyncio.wait_for(
                    page.evaluate("""() => {
                        window.scrollTo(0, 0);
                        return Math.max(
                            document.body ? document.body.scrollHeight : 0,
                            document.documentElement ? document.documentElement.scrollHeight : 0
                        );
                    }"""),
                    timeout=2.0,
                )
                if isinstance(sh2, (int, float)) and sh2 > 0:
                    scroll_height = int(sh2)
            except Exception:
                pass
        except Exception:
            pass

        # Pause rAF + videos BEFORE DOM/computed-style extraction so heavy 60fps JS/rAF loops
        # cannot starve page.content() or COMPUTED_STYLES_JS under multi-worker load.
        raf_paused = False
        try:
            await asyncio.wait_for(
                page.evaluate(
                    """() => {
                        window.__PAUSE_RAF = true;
                        document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch (_) {} });
                    }"""
                ),
                timeout=3.0 if retry_extended else 2.5,
            )
            raf_paused = True
            await asyncio.sleep(0.15)
        except Exception:
            pass

        # Extract title, DOM, computed styles, and client storage (desktop captures full artifacts)
        title = ""
        try:
            title = await asyncio.wait_for(page.title(), timeout=3.0)
        except Exception:
            pass

        dom_html = ""
        computed_styles: Dict[str, Any] = {}
        storage_data: Dict[str, Any] = {}
        visible_text = ""
        dom_timed_out = False
        dom_timeout = 14.0 if retry_extended else 10.0

        if collect_assets:
            try:
                dom_html = await asyncio.wait_for(page.content(), timeout=dom_timeout)
            except Exception:
                dom_timed_out = True
            try:
                computed_styles = await asyncio.wait_for(page.evaluate(COMPUTED_STYLES_JS), timeout=dom_timeout)
                visible_text = computed_styles.get("visibleTextSample", "")
                dm = computed_styles.get("domMetrics", {})
                if isinstance(dm.get("canvasCount"), int):
                    canvas_count = max(canvas_count, dm["canvasCount"])
            except Exception:
                if not dom_html:
                    dom_timed_out = True
            try:
                storage_data = await asyncio.wait_for(
                    page.evaluate("""() => {
                        const ls = {}, ss = {};
                        try {
                          for (let i = 0; i < localStorage.length; i++) {
                            const k = localStorage.key(i);
                            ls[k] = String(localStorage.getItem(k) || '').slice(0, 500);
                          }
                        } catch (_) {}
                        try {
                          for (let i = 0; i < sessionStorage.length; i++) {
                            const k = sessionStorage.key(i);
                            ss[k] = String(sessionStorage.getItem(k) || '').slice(0, 500);
                          }
                        } catch (_) {}
                        return { localStorage: ls, sessionStorage: ss };
                    }"""),
                    timeout=2.5,
                )
            except Exception:
                storage_data = {"localStorage": {}, "sessionStorage": {}}
        else:
            try:
                visible_text = await asyncio.wait_for(
                    page.evaluate("() => (document.body ? (document.body.innerText || '') : '').slice(0, 800)"),
                    timeout=2.0,
                )
            except Exception:
                pass

        # Final consent + promo/newsletter modal dismissal pass right before screenshot
        if http_status is not None and http_status < 400:
            try:
                if await _dismiss_consent_all_frames(page, allow_hide_fallback=True):
                    cookie_dismissed = True
            except Exception:
                pass
            try:
                if await _dismiss_promo_modals(page):
                    raf_paused = False
            except Exception:
                pass

        # Capture screenshot only if not already a hard HTTP >=400 error (still check content block after)
        png_bytes = b""
        metrics: Dict[str, Any] = {"valid": False, "width": 0, "height": 0, "bytes": 0}
        if http_status is not None and http_status < 400:
            try:
                png_bytes = await _cdp_clamped_screenshot(
                    context=context,
                    page=page,
                    vw=vw,
                    min_h=min_h,
                    scroll_height=scroll_height,
                    pause_raf=not raf_paused,
                    tier=tier,
                )
                metrics = compute_png_visual_metrics(png_bytes, top_fold_height=min_h)
            except Exception as exc:
                metrics = {"valid": False, "width": 0, "height": 0, "bytes": 0, "error": str(exc)}
            # On 0B/invalid PNG capture failure, retry once with viewport-only page.screenshot
            if not metrics.get("valid"):
                try:
                    png_bytes = await asyncio.wait_for(
                        page.screenshot(full_page=False, animations="disabled", timeout=15000),
                        timeout=16.5,
                    )
                    metrics = compute_png_visual_metrics(png_bytes, top_fold_height=min_h)
                except Exception as exc:
                    metrics = {"valid": False, "width": 0, "height": 0, "bytes": 0, "error": str(exc)}

            # R3-1 / R4 Color-Agnostic Blank-Tail & Mid-Page Blank-Void Guard:
            # if bottom >=35% / >=350px OR a >=450px mid-page void (dark OR light) is flat,
            # deep-scroll slower to hydrate and re-capture; if still blank, crop cleanly above the void.
            if (
                metrics.get("valid")
                and (metrics.get("has_blank_tail") or metrics.get("has_mid_blank_void"))
                and tier != "tier2_webgl"
            ):
                try:
                    if raf_paused:
                        await asyncio.wait_for(page.evaluate("() => { if (window.__RESUME_RAF) window.__RESUME_RAF(); }"), timeout=1.5)
                        raf_paused = False
                    await _dismiss_consent_all_frames(page, allow_hide_fallback=True)
                    await _dismiss_promo_modals(page)
                    sh_deep = await asyncio.wait_for(page.evaluate(SCROLL_HYDRATE_DEEP_JS), timeout=12.0)
                    if isinstance(sh_deep, (int, float)) and sh_deep > 0:
                        scroll_height = int(sh_deep)
                    await _dismiss_promo_modals(page)
                    png_retry = await _cdp_clamped_screenshot(
                        context=context,
                        page=page,
                        vw=vw,
                        min_h=min_h,
                        scroll_height=scroll_height,
                        pause_raf=True,
                        tier=tier,
                    )
                    m_retry = compute_png_visual_metrics(png_retry, top_fold_height=min_h)
                    if m_retry.get("valid") and (
                        (not m_retry.get("has_blank_tail") and not m_retry.get("has_mid_blank_void"))
                        or m_retry.get("max_blank_void_px", 9999) < metrics.get("max_blank_void_px", 9999)
                        or visual_entropy_score(m_retry) > visual_entropy_score(metrics)
                    ):
                        png_bytes = png_retry
                        metrics = m_retry
                except Exception:
                    pass
                if metrics.get("valid") and (metrics.get("has_blank_tail") or metrics.get("has_mid_blank_void")):
                    cropped, did_crop = crop_blank_tail_png(png_bytes, min_h=min_h, crop_mid_void=True)
                    if did_crop:
                        png_bytes = cropped
                        metrics = compute_png_visual_metrics(png_bytes, top_fold_height=min_h)

        if not preloader_stuck:
            vis_len_final = (computed_styles.get("domMetrics") or {}).get("visibleTextLen") if computed_styles else None
            p_txt = detect_preloader_text(visible_text, vis_len_final)
            if p_txt:
                preloader_stuck = True
                preloader_reason = p_txt

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
            "dom_timed_out": dom_timed_out,
            "cookie_dismissed": cookie_dismissed,
            "preloader_stuck": preloader_stuck,
            "preloader_reason": preloader_reason,
            "computed_styles": computed_styles,
            "storage": storage_data,
            "png_bytes": png_bytes,
            "metrics": metrics,
            "styles": collected_styles,
            "apis": collected_apis,
            "scripts_count": scripts_count,
        }
    finally:
        try:
            await asyncio.wait_for(context.close(), timeout=3.0)
        except Exception:
            pass


class BrowserPool:
    """Memory-bounded Chromium pool: recycles Tier 1 every 20 targets + gc.collect()."""

    def __init__(self, playwright: Playwright, recycle_every: int = 20):
        self._p = playwright
        self._recycle_every = recycle_every
        self._tier1_browser: Optional[Browser] = None
        self._tier1_count = 0

    async def get_tier1(self) -> Browser:
        if self._tier1_browser is not None and self._tier1_count >= self._recycle_every:
            await self.recycle_tier1()
        if self._tier1_browser is None or not self._tier1_browser.is_connected():
            self._tier1_browser = await self._p.chromium.launch(headless=True, args=TIER1_ARGS)
            self._tier1_count = 0
        self._tier1_count += 1
        return self._tier1_browser

    async def recycle_tier1(self) -> None:
        if self._tier1_browser is not None:
            try:
                await self._tier1_browser.close()
            except Exception:
                pass
            self._tier1_browser = None
        self._tier1_count = 0
        gc.collect()

    async def launch_ephemeral(self, tier: str) -> Browser:
        if tier == "tier2_webgl":
            # Full Chromium headless channel avoids chromium_headless_shell SwiftShader WebGL deadlocks
            return await self._p.chromium.launch(headless=True, channel="chromium", args=TIER2_ARGS)
        return await self._p.chromium.launch(headless=True, args=TIER3_ARGS)

    async def close_all(self) -> None:
        await self.recycle_tier1()


async def _capture_target_impl(
    pool: BrowserPool,
    slug: str,
    url: str,
    raw_dir: Path,
    wiki_targets_dir: Optional[Path] = None,
    retry_extended: bool = False,
    force_tier2: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Core 3-tier adaptive capture workflow for one target."""
    t0 = time.time()
    chosen_tier = "tier1_fast"

    # Step 1: Tier 1 Desktop Capture (unless force_tier2)
    if force_tier2:
        t2_browser = await pool.launch_ephemeral("tier2_webgl")
        try:
            desk = await _capture_single_viewport(
                t2_browser, url, is_mobile=False, tier="tier2_webgl",
                collect_assets=True, retry_extended=retry_extended,
                auth_storage_state=auth_storage_state,
            )
            chosen_tier = "tier2_webgl"
            block_reason = detect_block_or_error(
                desk["http_status"], desk["title"], desk["visible_text"],
                desk["dom_html"], desk["canvas_count"], desk["nav_error"],
            )
            mob = None
            if not block_reason:
                mob = await _capture_single_viewport(
                    t2_browser, desk["final_url"] or url, is_mobile=True, tier="tier2_webgl",
                    collect_assets=False, retry_extended=retry_extended,
                    auth_storage_state=auth_storage_state,
                )
        finally:
            try:
                await t2_browser.close()
            except Exception:
                pass
            gc.collect()
    else:
        b1 = await pool.get_tier1()
        desk = await _capture_single_viewport(
            b1, url, is_mobile=False, tier="tier1_fast",
            collect_assets=True, retry_extended=retry_extended,
            auth_storage_state=auth_storage_state,
        )

        # Step 2: Tier 3 (--disable-http2) escalation ONLY on ERR_HTTP2_* transport errors
        if desk["nav_error"] and "ERR_HTTP2" in desk["nav_error"]:
            t3_browser = await pool.launch_ephemeral("tier3_http1")
            try:
                desk_t3 = await _capture_single_viewport(
                    t3_browser, url, is_mobile=False, tier="tier3_http1",
                    collect_assets=True, retry_extended=retry_extended,
                    auth_storage_state=auth_storage_state,
                )
                desk = desk_t3
                chosen_tier = "tier3_http1"
                block_reason = detect_block_or_error(
                    desk["http_status"], desk["title"], desk["visible_text"],
                    desk["dom_html"], desk["canvas_count"], desk["nav_error"],
                )
                mob = None
                if not block_reason:
                    mob = await _capture_single_viewport(
                        t3_browser, desk["final_url"] or url, is_mobile=True, tier="tier3_http1",
                        collect_assets=False, retry_extended=retry_extended,
                        auth_storage_state=auth_storage_state,
                    )
            finally:
                try:
                    await t3_browser.close()
                except Exception:
                    pass
                gc.collect()
        else:
            def _backfill_artifacts(primary: Dict[str, Any], secondary: Dict[str, Any]) -> None:
                for field in ("dom_html", "computed_styles", "styles", "title", "storage", "apis"):
                    if not primary.get(field) and secondary.get(field):
                        primary[field] = secondary[field]
                if not primary.get("scripts_count") and secondary.get("scripts_count"):
                    primary["scripts_count"] = secondary["scripts_count"]

            t1_empty_dom_with_assets = (
                desk["http_status"] == 200
                and (desk.get("scripts_count", 0) > 0 or len(desk.get("styles") or []) > 0)
                and not (desk.get("dom_html") or "").strip()
            )
            t1_stalled = bool(
                desk.get("tier1_stalled")
                or desk.get("dom_timed_out")
                or t1_empty_dom_with_assets
            )
            block_reason = None if t1_stalled else detect_block_or_error(
                desk["http_status"], desk["title"], desk["visible_text"],
                desk["dom_html"], desk["canvas_count"], desk["nav_error"],
            )
            mob = None
            desk_unhealthy = (
                _is_viewport_blank_or_unhealthy(desk["metrics"])
                if retry_extended
                else is_visual_unhealthy_for_tier2(desk["metrics"])
            )
            desk_needs_t2 = (
                not block_reason
                and desk["http_status"] == 200
                and (
                    t1_stalled
                    or not desk["metrics"].get("valid")
                    or (desk["canvas_count"] > 0 and (desk_unhealthy or desk.get("preloader_stuck")))
                    or (retry_extended and (desk_unhealthy or desk.get("preloader_stuck")))
                )
            )

            if desk_needs_t2:
                # Immediately escalate to Tier 2 SwiftShader WebGL (full Chromium channel) without wasting time on Tier 1 mobile
                t2_browser = await pool.launch_ephemeral("tier2_webgl")
                try:
                    desk_t2 = await _capture_single_viewport(
                        t2_browser, url, is_mobile=False, tier="tier2_webgl",
                        collect_assets=True, retry_extended=retry_extended,
                        auth_storage_state=auth_storage_state,
                    )
                    _backfill_artifacts(desk_t2, desk)
                    block_reason = detect_block_or_error(
                        desk_t2["http_status"], desk_t2["title"], desk_t2["visible_text"],
                        desk_t2["dom_html"], desk_t2["canvas_count"], desk_t2["nav_error"],
                    )
                    if not block_reason:
                        mob_t2 = await _capture_single_viewport(
                            t2_browser, desk_t2["final_url"] or url, is_mobile=True, tier="tier2_webgl",
                            collect_assets=False, retry_extended=retry_extended,
                            auth_storage_state=auth_storage_state,
                        )
                        t1_lacks_dom = not (desk.get("dom_html") or "").strip()
                        t2_has_dom = bool((desk_t2.get("dom_html") or "").strip())
                        if (
                            (desk.get("preloader_stuck") and not desk_t2.get("preloader_stuck") and desk_t2["metrics"].get("valid"))
                            or (
                                not (desk_t2.get("preloader_stuck") and not desk.get("preloader_stuck"))
                                and (
                                    visual_entropy_score(desk_t2["metrics"]) > visual_entropy_score(desk["metrics"])
                                    or (t1_lacks_dom and t2_has_dom and desk_t2["metrics"].get("valid"))
                                )
                            )
                        ):
                            _backfill_artifacts(desk_t2, desk)
                            desk = desk_t2
                            chosen_tier = "tier2_webgl"
                        else:
                            _backfill_artifacts(desk, desk_t2)
                        mob = mob_t2
                        if mob["metrics"].get("valid") and not is_visual_unhealthy_for_tier2(mob["metrics"]):
                            chosen_tier = "tier2_webgl"
                        elif retry_extended and (
                            not mob["metrics"].get("valid") or _is_viewport_blank_or_unhealthy(mob["metrics"])
                        ):
                            # Tier 2 mobile was blank/invalid; try Tier 1 mobile and keep higher entropy
                            try:
                                mob_t1 = await _capture_single_viewport(
                                    b1, desk["final_url"] or url, is_mobile=True, tier="tier1_fast",
                                    collect_assets=False, retry_extended=retry_extended,
                                    auth_storage_state=auth_storage_state,
                                )
                                if visual_entropy_score(mob_t1["metrics"]) > visual_entropy_score(mob["metrics"]):
                                    mob = mob_t1
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    try:
                        await t2_browser.close()
                    except Exception:
                        pass
                    gc.collect()
            elif not block_reason:
                mob = await _capture_single_viewport(
                    b1, desk["final_url"] or url, is_mobile=True, tier="tier1_fast",
                    collect_assets=False, retry_extended=retry_extended,
                    auth_storage_state=auth_storage_state,
                )
                mob_unhealthy = (
                    _is_viewport_blank_or_unhealthy(mob["metrics"])
                    if retry_extended
                    else is_visual_unhealthy_for_tier2(mob["metrics"])
                )
                # Escalate failing mobile viewport to Tier 2 on <canvas> sites, 0B PNGs, or --retry-extended
                if (
                    desk["http_status"] == 200
                    and mob_unhealthy
                    and (desk["canvas_count"] > 0 or retry_extended or not mob["metrics"].get("valid"))
                ):
                    t2_browser = await pool.launch_ephemeral("tier2_webgl")
                    try:
                        mob_t2 = await _capture_single_viewport(
                            t2_browser, desk["final_url"] or url, is_mobile=True, tier="tier2_webgl",
                            collect_assets=False, retry_extended=retry_extended,
                            auth_storage_state=auth_storage_state,
                        )
                        if visual_entropy_score(mob_t2["metrics"]) > visual_entropy_score(mob["metrics"]):
                            mob = mob_t2
                            chosen_tier = "tier2_webgl"
                    except Exception:
                        pass
                    finally:
                        try:
                            await t2_browser.close()
                        except Exception:
                            pass
                        gc.collect()

    # Re-evaluate block status if desktop/mobile capture failed or stayed stuck on a preloader frame
    if not block_reason and not desk["metrics"].get("valid"):
        block_reason = "Live desktop screenshot capture failed (0 valid PNG bytes)"
    if not block_reason and desk.get("preloader_stuck"):
        detail = (desk.get("preloader_reason") or "stuck on unhydrated preloader").strip()[:70]
        block_reason = f"Headless Preloader / Blank Viewport Capture ({detail})"
    if (
        not block_reason
        and retry_extended
        and desk["metrics"].get("top_fold_gray_std", 99.0) < 3.0
        and desk["metrics"].get("top_fold_dominant_q16_pct", 0.0) > 98.5
        and desk["metrics"].get("top_fold_edge_density", 1.0) < 0.001
    ):
        block_reason = "Headless Preloader / Blank Viewport Capture (blank >98.5% top-fold hero viewport)"
    if not block_reason and (mob is None or not mob["metrics"].get("valid")):
        # One last attempt on mobile via Tier 1 if missing
        try:
            b_retry = await pool.get_tier1()
            mob = await _capture_single_viewport(
                b_retry, desk["final_url"] or url, is_mobile=True, tier="tier1_fast",
                collect_assets=False, retry_extended=retry_extended,
                auth_storage_state=auth_storage_state,
            )
        except Exception:
            pass
        if mob is None or not mob["metrics"].get("valid"):
            block_reason = "Live mobile screenshot capture failed (0 valid PNG bytes)"
    if not block_reason and mob and mob.get("preloader_stuck"):
        detail = (mob.get("preloader_reason") or "mobile stuck on unhydrated preloader").strip()[:70]
        block_reason = f"Headless Preloader / Blank Viewport Capture ({detail})"

    status = "blocked" if block_reason else "analyzed"
    now_iso = datetime.now(timezone.utc).isoformat()
    target_raw = raw_dir / slug
    target_raw.mkdir(parents=True, exist_ok=True)
    styles_dir = target_raw / "styles"
    styles_dir.mkdir(exist_ok=True)

    # Atomic writes to raw/<slug>/
    if desk.get("dom_html"):
        atomic_write_text(target_raw / "dom.html", desk["dom_html"])
    comp_to_save = desk.get("computed_styles")
    if status == "analyzed" and not comp_to_save:
        # Synthesize minimal grounded computed_styles.json when a heavy WebGL main thread timed out JS eval
        dom_src = desk.get("dom_html") or ""
        if not dom_src and (target_raw / "dom.html").exists():
            dom_src = (target_raw / "dom.html").read_text(encoding="utf-8", errors="ignore")
        tm = re.search(r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\'](#[0-9a-fA-F]{3,6})["\']', dom_src, re.I)
        d_luma = (desk.get("metrics") or {}).get("modal_luma", 128.0)
        eff_bg = tm.group(1).lower() if tm else ("#000000" if d_luma < 80 else "#ffffff")
        comp_to_save = {
            "effectiveBgHex": eff_bg,
            "effectiveBgSource": "meta-theme-color" if tm else "canvas-modal-luma",
            "elements": {},
            "rootVars": {},
            "loadedFonts": [],
            "landmarks": [],
            "domMetrics": {
                "canvasCount": max(1, int(desk.get("canvas_count") or 1)),
                "scrollHeight": (desk.get("metrics") or {}).get("height", 900),
            },
            "captureNote": "webgl-main-thread-busy-fallback",
        }
    if comp_to_save:
        atomic_write_text(target_raw / "computed_styles.json", json.dumps(comp_to_save, indent=2))
    if desk.get("storage"):
        atomic_write_text(target_raw / "storage.json", json.dumps(desk["storage"], indent=2))
    if desk.get("apis"):
        atomic_write_text(target_raw / "network_apis.json", json.dumps(desk["apis"], indent=2))

    # Save captured external stylesheets (replace old styles only when fresh ones were captured)
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

    d_metrics = desk.get("metrics") or {}
    m_metrics = (mob.get("metrics") if mob else {}) or {}

    if status == "analyzed":
        d_png_opt = optimize_png_bytes(desk["png_bytes"])
        m_png_opt = optimize_png_bytes(mob["png_bytes"])
        d_metrics["bytes"] = len(d_png_opt)
        d_metrics["sha256"] = hashlib.sha256(d_png_opt).hexdigest()
        m_metrics["bytes"] = len(m_png_opt)
        m_metrics["sha256"] = hashlib.sha256(m_png_opt).hexdigest()
        atomic_write_bytes(target_raw / "screenshot.png", d_png_opt)
        atomic_write_bytes(target_raw / "screenshot_mobile.png", m_png_opt)
        if wiki_targets_dir is not None:
            assets_dir = wiki_targets_dir / slug / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(assets_dir / "desktop.png", d_png_opt)
            atomic_write_bytes(assets_dir / "mobile.png", m_png_opt)
    else:
        # Delete any stale PNGs in wiki/targets/<slug>/assets/ when status == blocked
        if wiki_targets_dir is not None:
            assets_dir = wiki_targets_dir / slug / "assets"
            for stale_name in ("desktop.png", "mobile.png"):
                stale_p = assets_dir / stale_name
                if stale_p.exists():
                    try:
                        stale_p.unlink()
                    except OSError:
                        pass

    metadata = {
        "target_name": slug,
        "url": url,
        "final_url": desk.get("final_url") or url,
        "redirect_chain": desk.get("redirect_chain") or [url],
        "http_status": desk.get("http_status"),
        "capture_tier": chosen_tier if status == "analyzed" else "none",
        "capture_timestamp": now_iso,
        "desktop_dims": [d_metrics.get("width", 0), d_metrics.get("height", 0)] if status == "analyzed" else [0, 0],
        "mobile_dims": [m_metrics.get("width", 0), m_metrics.get("height", 0)] if status == "analyzed" else [0, 0],
        "desktop_metrics": d_metrics if status == "analyzed" else {},
        "mobile_metrics": m_metrics if status == "analyzed" else {},
        "status": status,
        "block_reason": block_reason,
        "cookie_dismissed": bool(desk.get("cookie_dismissed") or (mob and mob.get("cookie_dismissed"))),
        "title": desk.get("title") or "",
        "scripts_count": desk.get("scripts_count", 0),
        "styles_count": styles_saved,
        "storage_keys": list((desk.get("storage") or {}).get("localStorage", {}).keys()),
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    atomic_write_text(target_raw / "metadata.json", json.dumps(metadata, indent=2))
    return metadata


async def capture_target_live(
    pool: BrowserPool,
    slug: str,
    url: str,
    raw_dir: Path,
    wiki_targets_dir: Optional[Path] = None,
    deadline_seconds: float = 120.0,
    retry_extended: bool = False,
    force_tier2: bool = False,
    auth_storage_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute capture_target_impl under a hard asyncio.wait_for deadline."""
    effective_deadline = max(deadline_seconds, 150.0) if retry_extended else deadline_seconds
    t0 = time.time()
    try:
        return await asyncio.wait_for(
            _capture_target_impl(
                pool=pool,
                slug=slug,
                url=url,
                raw_dir=raw_dir,
                wiki_targets_dir=wiki_targets_dir,
                retry_extended=retry_extended,
                force_tier2=force_tier2,
                auth_storage_state=auth_storage_state,
            ),
            timeout=effective_deadline,
        )
    except asyncio.TimeoutError:
        reason = f"Hard per-target capture deadline exceeded ({effective_deadline:.0f}s)"
        # Recycle Tier 1 browser after a hard timeout so no hung tabs remain
        await pool.recycle_tier1()
        target_raw = raw_dir / slug
        target_raw.mkdir(parents=True, exist_ok=True)
        if wiki_targets_dir is not None:
            for stale_name in ("desktop.png", "mobile.png"):
                stale_p = wiki_targets_dir / slug / "assets" / stale_name
                if stale_p.exists():
                    try:
                        stale_p.unlink()
                    except OSError:
                        pass
        metadata = {
            "target_name": slug,
            "url": url,
            "final_url": url,
            "redirect_chain": [url],
            "http_status": None,
            "capture_tier": "none",
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


def load_checkpoint(checkpoint_path: Path) -> Dict[str, Any]:
    if checkpoint_path.exists():
        try:
            return json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": {}, "updated_at": None}


def save_checkpoint(checkpoint_path: Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(checkpoint_path, json.dumps(data, indent=2))


async def capture_single_url_standalone(
    url: str,
    target_name: str,
    raw_dir: Path,
    wiki_targets_dir: Optional[Path] = None,
    auth_storage_state: Optional[str] = None,
    retry_extended: bool = False,
) -> Dict[str, Any]:
    """Convenience entry point for single-target CLI captures."""
    async with async_playwright() as p:
        pool = BrowserPool(p, recycle_every=20)
        try:
            return await capture_target_live(
                pool=pool,
                slug=target_name,
                url=url,
                raw_dir=raw_dir,
                wiki_targets_dir=wiki_targets_dir,
                retry_extended=retry_extended,
                auth_storage_state=auth_storage_state,
            )
        finally:
            await pool.close_all()
