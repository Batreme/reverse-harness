import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from rich.console import Console

from reverse_harness.config import DEFAULT_RAW_DIR, DEFAULT_FLEET_WIKI_DIR

console = Console()

def infer_json_schema(data: Any, max_depth: int = 3) -> Dict[str, Any]:
    """Infers simplified JSON schema representation from an arbitrary Python object."""
    if max_depth <= 0:
        return {"type": "any"}
    if isinstance(data, dict):
        props = {}
        for k, v in list(data.items())[:20]: # limit keys for brevity
            props[k] = infer_json_schema(v, max_depth - 1)
        return {"type": "object", "properties": props}
    elif isinstance(data, list):
        if not data:
            return {"type": "array", "items": "any"}
        item_sample = infer_json_schema(data[0], max_depth - 1)
        return {"type": "array", "items": item_sample}
    elif isinstance(data, bool):
        return {"type": "boolean"}
    elif isinstance(data, (int, float)):
        return {"type": "number"}
    elif isinstance(data, str):
        return {"type": "string"}
    elif data is None:
        return {"type": "null"}
    return {"type": type(data).__name__}

def parse_har_apis(har_path: Path) -> List[Dict[str, Any]]:
    """Extracts non-static API network traffic from HAR."""
    if not har_path.exists():
        return []
    try:
        har_data = json.loads(har_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    entries = har_data.get("log", {}).get("entries", [])
    api_entries = []

    static_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".css"}

    for entry in entries:
        req = entry.get("request", {})
        res = entry.get("response", {})
        url = req.get("url", "")
        method = req.get("method", "GET")
        parsed_url = urlparse(url)
        path = parsed_url.path

        # Skip typical static asset paths
        if any(path.lower().endswith(ext) for ext in static_extensions):
            continue

        res_mime = res.get("content", {}).get("mimeType", "").lower()
        res_text = res.get("content", {}).get("text", "")
        req_post_data = req.get("postData", {})

        is_api = (
            "json" in res_mime
            or "graphql" in path.lower()
            or "/api/" in path.lower()
            or "/v1/" in path.lower()
            or "/v2/" in path.lower()
            or "/v3/" in path.lower()
            or "json" in req_post_data.get("mimeType", "").lower()
        )

        if not is_api:
            continue

        # Inferred request payload schema
        req_body = None
        req_schema = None
        if req_post_data.get("text"):
            try:
                req_body = json.loads(req_post_data["text"])
                req_schema = infer_json_schema(req_body)
            except Exception:
                req_body = req_post_data["text"][:500]

        # Inferred response payload schema
        res_body = None
        res_schema = None
        if res_text:
            try:
                res_body = json.loads(res_text)
                res_schema = infer_json_schema(res_body)
            except Exception:
                pass

        # Identify GraphQL operations
        graphql_op = None
        if req_body and isinstance(req_body, dict):
            if "query" in req_body or "operationName" in req_body:
                graphql_op = req_body.get("operationName") or "AnonymousQuery"

        api_entries.append({
            "url": url,
            "path": path,
            "method": method,
            "status": res.get("status"),
            "graphql_op": graphql_op,
            "req_schema": req_schema,
            "res_schema": res_schema,
            "headers": {h["name"]: h["value"] for h in req.get("headers", []) if h["name"].lower() in {"authorization", "content-type", "x-requested-with"}},
        })

    return api_entries

def parse_source_maps(scripts_dir: Path) -> List[Dict[str, Any]]:
    """Scans downloaded scripts for source maps and unpacks file lists."""
    if not scripts_dir.exists():
        return []
    
    unpacked_sources = []
    for script_file in scripts_dir.glob("*.js"):
        content = script_file.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"//#\s*sourceMappingURL=(.+)$", content, re.MULTILINE)
        if match:
            map_url = match.group(1).strip()
            # If map is inline base64
            if map_url.startswith("data:application/json;base64,"):
                import base64
                try:
                    raw_b64 = map_url.split(",", 1)[1]
                    map_json = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
                    sources = map_json.get("sources", [])
                    unpacked_sources.append({
                        "script": script_file.name,
                        "type": "inline-base64",
                        "sources_count": len(sources),
                        "sources": sources[:50], # sample
                    })
                except Exception:
                    pass
            else:
                unpacked_sources.append({
                    "script": script_file.name,
                    "type": "external-ref",
                    "map_ref": map_url,
                })
    return unpacked_sources

def extract_dom_and_css_tokens(dom_path: Path, styles_dir: Path) -> Dict[str, Any]:
    """Extracts UI design tokens, framework signatures, and component layout primitives."""
    css_variables = []
    frameworks = []
    
    # Analyze styles for CSS variables
    if styles_dir.exists():
        for css_file in styles_dir.glob("*.css"):
            content = css_file.read_text(encoding="utf-8", errors="ignore")
            var_matches = re.findall(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;]+);", content)
            for var_name, var_val in var_matches[:100]:
                css_variables.append({"name": var_name.strip(), "value": var_val.strip()[:60]})

    ui_components = []
    if dom_path.exists():
        soup = BeautifulSoup(dom_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        # Check frameworks
        if soup.find("div", id="__next") or soup.find("script", id="__NEXT_DATA__"):
            frameworks.append("Next.js")
        if soup.find(attrs={"data-reactroot": True}) or "__reactFiber" in str(soup):
            frameworks.append("React")
        if soup.find(attrs={"data-v-app": True}) or soup.find(attrs={"data-server-rendered": True}):
            frameworks.append("Vue")
        if "tailwind" in str(soup).lower() or any("tw-" in (c or "") for c in [tag.get("class") for tag in soup.find_all(class_=True)]):
            frameworks.append("Tailwind CSS")

        # Layout primitives
        for tag_name in ["nav", "header", "aside", "main", "footer", "dialog"]:
            for el in soup.find_all(tag_name):
                ui_components.append({
                    "type": tag_name,
                    "id": el.get("id"),
                    "classes": " ".join(el.get("class", []))[:100],
                })

    return {
        "frameworks": list(set(frameworks)),
        "css_variables": css_variables[:40],
        "layout_components": ui_components[:20],
    }

def analyze_target(
    target_name: str,
    raw_dir: Optional[Path] = None,
    wiki_dir: Optional[Path] = None,
) -> Path:
    """Analyzes captured artifacts and generates the 5-document Obsidian dossier."""
    r_dir = raw_dir or DEFAULT_RAW_DIR
    target_raw = r_dir / target_name
    if not target_raw.exists():
        raise FileNotFoundError(f"Raw capture not found for target '{target_name}' at {target_raw}")

    w_dir = wiki_dir or DEFAULT_FLEET_WIKI_DIR
    target_wiki = w_dir / target_name
    target_wiki.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold cyan]Analyzing target:[/bold cyan] [green]{target_name}[/green]")

    # 1. Load metadata & storage
    metadata_path = target_raw / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    storage_path = target_raw / "storage.json"
    storage_data = {}
    if storage_path.exists():
        try:
            storage_data = json.loads(storage_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2. Extract APIs, Source Maps, and UI Tokens
    apis = parse_har_apis(target_raw / "traffic.har")
    source_maps = parse_source_maps(target_raw / "scripts")
    ui_data = extract_dom_and_css_tokens(target_raw / "dom.html", target_raw / "styles")

    target_url = metadata.get("url", f"https://{target_name}")
    today = datetime.now().strftime("%Y-%m-%d")

    # Document 1: overview.md
    frameworks_str = ", ".join(ui_data.get("frameworks", [])) or "Modern Web Application"
    overview_content = f"""---
title: "Target Overview: {target_name}"
date: {today}
url: "{target_url}"
status: analyzed
tags:
  - target
  - reverse-engineering
  - {target_name}
auth_required: {str(bool(metadata.get('auth_profile'))).lower()}
---

# Target Overview: {target_name}

## 🧭 Dossier Navigation
- [[apis|Discovered APIs & Network Protocols]]
- [[data-models|Data Models & Local State]]
- [[ui-ux|UI Architecture & Design Tokens]]
- [[source-maps|De-obfuscated Source Maps]]

---

## 🏗️ Architecture & Stack Fingerprint
- **Target URL**: `{target_url}`
- **Page Title**: {metadata.get('title', 'N/A')}
- **Identified Frameworks**: {frameworks_str}
- **Auth Profile Used**: {metadata.get('auth_profile') or 'None (Public session)'}
- **Recorded API Endpoints**: {len(apis)}
- **Recovered Scripts**: {metadata.get('scripts_count', 0)}
- **Recovered Stylesheets**: {metadata.get('styles_count', 0)}

---

## 💡 Key Architectural Lessons (Anti-AI-Slop Insights)
- **Domain Modeling**: See [[data-models]] for concrete entity structures observed in network requests and local storage.
- **Client Architecture**: Inferred from UI layout primitives and client storage persistence.
"""
    (target_wiki / "overview.md").write_text(overview_content, encoding="utf-8")

    # Document 2: apis.md
    apis_md_rows = []
    for a in apis[:30]:
        method = a.get("method")
        path = a.get("path")
        status = a.get("status")
        gop = a.get("graphql_op")
        desc = f"GraphQL: `{gop}`" if gop else "REST endpoint"
        apis_md_rows.append(f"| `{method}` | `{path}` | `{status}` | {desc} |")

    apis_table = "\n".join(apis_md_rows) if apis_md_rows else "*No API endpoints captured in session.*"

    schemas_blocks = []
    for idx, a in enumerate(apis[:10]):
        if a.get("res_schema"):
            schemas_blocks.append(f"### `{a.get('method')}` {a.get('path')}\n```json\n" + json.dumps(a["res_schema"], indent=2) + "\n```\n")

    apis_content = f"""---
title: "APIs & Protocols: {target_name}"
date: {today}
target: "[[overview|{target_name}]]"
---

# Discovered APIs & Protocols: {target_name}

## 📡 Endpoint Catalog

| Method | Path | Status | Protocol / Operation |
|---|---|---|---|
{apis_table}

---

## 📦 Inferred Payload Schemas

{"".join(schemas_blocks) if schemas_blocks else "*No payload schemas inferred.*"}
"""
    (target_wiki / "apis.md").write_text(apis_content, encoding="utf-8")

    # Document 3: data-models.md
    local_storage = storage_data.get("localStorage", {})
    ls_blocks = []
    for k, v in list(local_storage.items())[:15]:
        val_preview = str(v)[:120]
        ls_blocks.append(f"- **`{k}`**: `{val_preview}`")

    data_models_content = f"""---
title: "Data Models & Local State: {target_name}"
date: {today}
target: "[[overview|{target_name}]]"
---

# Data Models & Client Storage: {target_name}

## 🗄️ Inferred Client State (LocalStorage / SessionStorage)

Total LocalStorage Keys: `{len(local_storage)}`

{chr(10).join(ls_blocks) if ls_blocks else "*No local storage keys identified.*"}

---

## 🧩 Inferred Domain Entities
*(Extracted from API response records)*
"""
    (target_wiki / "data-models.md").write_text(data_models_content, encoding="utf-8")

    # Document 4: ui-ux.md
    tokens_rows = []
    for var in ui_data.get("css_variables", [])[:25]:
        tokens_rows.append(f"| `{var['name']}` | `{var['value']}` |")

    tokens_table = "\n".join(tokens_rows) if tokens_rows else "*No CSS custom property tokens extracted.*"

    components_rows = []
    for c in ui_data.get("layout_components", [])[:15]:
        components_rows.append(f"| `<{c['type']}>` | `{c.get('id') or '-'}` | `{c.get('classes')}` |")
    components_table = "\n".join(components_rows) if components_rows else "*No layout primitives identified.*"

    ui_content = f"""---
title: "UI & Design System: {target_name}"
date: {today}
target: "[[overview|{target_name}]]"
---

# UI Architecture & Design Tokens: {target_name}

## 🎨 Design Tokens (CSS Variables)

| Token Name | Inferred Value |
|---|---|
{tokens_table}

---

## 🧱 Layout Primitives & Component Tree

| Primitive | ID | Classes / Selectors |
|---|---|---|
{components_table}
"""
    (target_wiki / "ui-ux.md").write_text(ui_content, encoding="utf-8")

    # Document 5: source-maps.md
    source_map_blocks = []
    for sm in source_maps:
        sources_list = "\n".join([f"  - `{s}`" for s in sm.get("sources", [])[:20]])
        source_map_blocks.append(f"### Bundle: `{sm['script']}` ({sm['type']})\nDiscovered Files ({sm.get('sources_count', 0)} total):\n{sources_list}\n")

    source_maps_content = f"""---
title: "Source Maps & Client AST: {target_name}"
date: {today}
target: "[[overview|{target_name}]]"
---

# De-obfuscated Source Maps: {target_name}

{"".join(source_map_blocks) if source_map_blocks else "*No source maps discovered or unpacked for this target.*"}
"""
    (target_wiki / "source-maps.md").write_text(source_maps_content, encoding="utf-8")

    console.print(f"[bold green]✓ Generated 5 Obsidian dossier files in:[/bold green] [cyan]{target_wiki}[/cyan]")
    return target_wiki
