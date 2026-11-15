# Reverse-Harness

An autonomous reverse-engineering harness for modern web applications and multi-domain security targets.

It pairs automated headless capture (Playwright + Chrome DevTools Protocol) with source map de-obfuscation, SQLite + `sqlite-vec` semantic indexing, Obsidian wiki dossiers in `fleet/wiki/targets/`, and upstream security routing via [`zhaoxuya520/reverse-skill`](https://github.com/zhaoxuya520/reverse-skill).

---

## 🚀 Quick Start

### 1. Installation
This project uses [`uv`](https://docs.astral.sh/uv/) for high-speed Python package management:

```bash
# Clone with submodules
git clone --recursive <repo-url> reverse-harness
cd reverse-harness

# Initialize environment, database, and Playwright browsers
uv run reverse-harness init
```

---

## 🛠️ CLI Commands

### 1. Interactive Authentication (for gated apps)
Launch a visible browser to manually log in to applications (Linear, Notion, GitHub):
```bash
uv run reverse-harness auth login <profile-name> --url "https://app.example.com/login"
```

### 2. Automated Web Capture
Perform headless Chromium capture with full network HAR, script bundles, stylesheets, DOM snapshots, and client storage:
```bash
uv run reverse-harness capture "https://app.example.com" --name "my-app"
# Or with authentication:
uv run reverse-harness capture "https://app.example.com/dashboard" --name "my-app" --auth <profile-name>
```

### 3. Analyze & Generate Obsidian Dossier
Process the captured HAR, unpack source maps, extract REST/GraphQL schemas and CSS design tokens:
```bash
uv run reverse-harness analyze "my-app"
```
This generates the standardized dossier in `fleet/wiki/targets/my-app/`:
- `overview.md`: Architecture summary, framework fingerprint, metadata.
- `apis.md`: Endpoint catalog and inferred JSON schemas.
- `data-models.md`: Client storage (LocalStorage/IndexedDB) and entity structures.
- `ui-ux.md`: Component primitives and CSS custom property design tokens.
- `source-maps.md`: Reconstructed client source trees.
- `prompt-blueprint.md`: 7-part UI design system analysis, viewport captures (`assets/desktop.png` and `assets/mobile.png`), and Section 7 Master Zero-Shot AI Generation Prompt.

### 4. Index Knowledge Base
Sync all dossiers into the local SQLite database with `sqlite-vec` vector embeddings:
```bash
uv run reverse-harness index
```

### 5. Semantic Pattern Search (Anti-AI-Slop Engine)
Query real-world production architectures before scaffolding generic boilerplate:
```bash
uv run reverse-harness query "optimistic updates kanban board"
uv run reverse-harness query "workspace role permissions schema"
```

### 6. Upstream Security Router
Route general reverse engineering, APK, binary, or CTF tasks through the upstream `reverse-skill` matrix:
```bash
uv run reverse-harness route "analyze encrypted flutter android apk"
```

### 7. UI Prompt Blueprint & Evaluation Tools
- **`run_all_rounds.py`**: Full-catalog 58-round orchestrator extracting ground-truth design tokens, typography, motion physics, DOM wireframes, and Section 7 master zero-shot generation prompts.
- **`evaluate_batch.py --all`**: Upgraded 14-point deterministic truth, entropy, type-scale, and citation evaluation suite (`C1`–`C14`).
- **`build_full_dashboard.py`**: Builds the complete 575-target / 58-round `UI_EVALS_DASHBOARD.md` scorecard with 1-click clickable GitHub links and honest capture breakdown.
- **`ui_gallery_server.py`**: Interactive local web application (`http://localhost:8080`) serving the visual target grid, side-by-side screenshot viewer, color palette swatches, and prompt inspector.
- **`tests/test_catalog_integrity.py`**: Comprehensive test suite verifying repo boundaries, relative symlinks, local Markdown link integrity, PNG dimensions, honest capture labeling, and evaluation logic.

---

## License

Non-Commercial Software License. Copyright (c) 2026 Jakub Szkop (Batreme). All rights reserved.  
See [`LICENSE`](LICENSE) for complete terms. For commercial licensing inquiries, contact: Jakub Szkop `<szkopjakub@gmail.com>`.

All third-party trademarks, brand names, copy, and visual designs referenced in reverse-engineered target dossiers remain the property of their respective owners and are cataloged strictly for non-commercial educational and research analysis.
