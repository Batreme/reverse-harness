#!/usr/bin/env python3
"""
Unit test suite for catalog integrity, link resolution, PNG dimension compliance,
and fleet repository boundaries.
Runnable via: python3 -m unittest discover -s tests
"""
import os
import pathlib
import re
import struct
import unittest
from pathlib import Path

REVERSE_HARNESS_DIR = Path(__file__).resolve().parent.parent
FLEET_DIR = Path(os.environ.get("FLEET_ROOT", REVERSE_HARNESS_DIR.parent / "fleet"))
FLEET_WIKI = FLEET_DIR / "wiki"
TARGETS_DIR = FLEET_WIKI / "targets"


class TestCatalogIntegrity(unittest.TestCase):
    def test_repo_boundary_and_relative_symlinks(self):
        """(a) Fleet repo boundary: only skills/ and wiki/; relative symlinks in .agents/skills/"""
        # Fleet root should only have harness dirs
        allowed_root_entries = {
            ".agents", ".git", ".gitignore", "AGENTS.md", "CLAUDE.md", "README.md", "LICENSE",
            "skills", "wiki", "skills-lock.json", ".obsidian"
        }
        actual_entries = {p.name for p in FLEET_DIR.iterdir()}
        # Ignore temporary or hidden editor files
        unexpected = actual_entries - allowed_root_entries
        self.assertEqual(unexpected, set(), f"Unexpected entries in fleet root: {unexpected}")

        # Check relative symlinks in .agents/skills
        agents_skills = FLEET_DIR / ".agents" / "skills"
        if agents_skills.exists():
            for link in agents_skills.iterdir():
                if link.is_symlink():
                    target = os.readlink(link)
                    self.assertFalse(target.startswith("/"), f"Symlink {link.name} must be relative, got: {target}")
                    self.assertTrue(link.exists(), f"Symlink {link.name} points to non-existent target: {target}")

    def test_markdown_links_and_anchors(self):
        """(b) Zero broken local Markdown links and #7-master-zero-shot-ai-generation-prompt anchors"""
        dashboard_path = FLEET_WIKI / "UI_EVALS_DASHBOARD.md"
        index_path = FLEET_WIKI / "INDEX.md"

        self.assertTrue(dashboard_path.exists(), "UI_EVALS_DASHBOARD.md must exist")
        self.assertTrue(index_path.exists(), "INDEX.md must exist")

        for doc_path in [dashboard_path, index_path]:
            content = doc_path.read_text(encoding="utf-8")
            # Verify no Obsidian [[...]] wikilinks remain
            wikilinks = re.findall(r"\[\[(.*?)\]\]", content)
            self.assertEqual(len(wikilinks), 0, f"Found {len(wikilinks)} wikilinks in {doc_path.name}: {wikilinks[:5]}")

            # Verify local markdown links
            links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
            broken = []
            for label, url in links:
                if url.startswith(("http://", "https://", "mailto:")):
                    continue
                clean_path = url.split("#")[0]
                target_file = (FLEET_WIKI / clean_path).resolve()
                if not target_file.exists():
                    broken.append((label, url))
            self.assertEqual(broken, [], f"Found broken links in {doc_path.name}: {broken[:10]}")

        # Verify #7-master-zero-shot-ai-generation-prompt anchors exist in target prompt blueprints
        dash_content = dashboard_path.read_text(encoding="utf-8")
        blueprint_links = re.findall(r"\(targets/([a-zA-Z0-9_-]+)/prompt-blueprint\.md#([^\)]+)\)", dash_content)
        self.assertGreater(len(blueprint_links), 500, "Should have > 500 blueprint prompt links")
        for slug, anchor in blueprint_links[:50]:  # sample check
            self.assertEqual(anchor, "7-master-zero-shot-ai-generation-prompt")
            bp_file = TARGETS_DIR / slug / "prompt-blueprint.md"
            self.assertTrue(bp_file.exists(), f"Missing blueprint for {slug}")
            bp_txt = bp_file.read_text(encoding="utf-8")
            self.assertIn("## 7. Master Zero-Shot AI Generation Prompt", bp_txt)

    def test_png_dimensions_and_specimen_captions(self):
        """(c) PNG IHDR widths (1440/390) and honest capture: specimen caption matching"""
        def read_png_dims(p):
            with open(p, "rb") as f:
                d = f.read(24)
                if len(d) >= 24 and d[:8] == b"\x89PNG\r\n\x1a\n":
                    return struct.unpack(">II", d[16:24])
            return (0, 0)

        sample_slugs = ["dennypratama-com", "agnestoth-com", "100yearsmonet-com", "akaiito-ai", "wiro-ae"]
        for slug in sample_slugs:
            tdir = TARGETS_DIR / slug
            d_png = tdir / "assets" / "desktop.png"
            m_png = tdir / "assets" / "mobile.png"
            if d_png.exists():
                dw, dh = read_png_dims(d_png)
                self.assertEqual(dw, 1440, f"{slug} desktop width must be 1440, got {dw}")
                self.assertGreaterEqual(dh, 1200, f"{slug} desktop height must be >= 1200, got {dh}")
            if m_png.exists():
                mw, mh = read_png_dims(m_png)
                self.assertEqual(mw, 390, f"{slug} mobile width must be 390, got {mw}")
                self.assertGreaterEqual(mh, 1200, f"{slug} mobile height must be >= 1200, got {mh}")

            bp_file = tdir / "prompt-blueprint.md"
            if bp_file.exists():
                bp_txt = bp_file.read_text(encoding="utf-8")
                cap_m = re.search(r"^capture:\s*(dom|specimen|none)", bp_txt, re.MULTILINE)
                self.assertIsNotNone(cap_m, f"{slug} blueprint missing capture: frontmatter")
                if cap_m.group(1) == "specimen":
                    self.assertIn("Design Specimen (Offline WebGL/JS Fallback — not live site capture)", bp_txt)

    def test_evaluate_batch_sample(self):
        """(d) evaluate_batch evaluation runs and returns 25.0 on batch 1 targets"""
        import sys
        sys.path.insert(0, str(REVERSE_HARNESS_DIR))
        from evaluate_batch import TARGETS as ROUND_1_SLUGS, evaluate_target, split_sections

        line_sets = {}
        word_sets = {}
        for s in ROUND_1_SLUGS:
            bp_file = TARGETS_DIR / s / "prompt-blueprint.md"
            bp = bp_file.read_text(encoding="utf-8")
            s7 = split_sections(bp).get(7, "")
            line_sets[s] = {l.strip() for l in s7.splitlines() if len(l.strip()) > 15}
            word_sets[s] = set(re.findall(r"[a-z0-9_#-]{3,}", s7.lower()))

        for s in ROUND_1_SLUGS[:3]:
            res = evaluate_target(s, line_sets, word_sets)
            self.assertEqual(res["total"], 25.0, f"Target {s} scored {res['total']} with issues: {res['issues']}")
            self.assertEqual(res["issues"], [], f"Target {s} had issues: {res['issues']}")


if __name__ == "__main__":
    unittest.main()
