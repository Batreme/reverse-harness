import json
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table

import sqlite_vec
from fastembed import TextEmbedding

from reverse_harness.config import DEFAULT_DB_PATH, DEFAULT_STORAGE_DIR, DEFAULT_FLEET_WIKI_DIR

console = Console()

_EMBED_MODEL: Optional[TextEmbedding] = None

def get_embed_model() -> TextEmbedding:
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        _EMBED_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _EMBED_MODEL

def pack_vector(vector: List[float]) -> bytes:
    """Packs float list into binary buffer for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)

def get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Initializes SQLite connection with sqlite-vec extension enabled."""
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)

    # Initialize schema
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                url TEXT,
                indexed_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_name TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                title TEXT,
                chunk_index INTEGER,
                content TEXT
            );
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
                id INTEGER PRIMARY KEY,
                embedding float[384]
            );
        """)

    return conn

def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
    """Splits long text into overlapping chunks for semantic vector search."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

def index_wiki_targets(wiki_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> int:
    """Scans Obsidian target dossiers, extracts chunks, computes embeddings, and syncs SQLite."""
    w_dir = wiki_dir or DEFAULT_FLEET_WIKI_DIR
    if not w_dir.exists():
        console.print(f"[yellow]Target wiki directory does not exist yet: {w_dir}[/yellow]")
        return 0

    conn = get_db(db_path)
    model = get_embed_model()

    target_folders = [d for d in w_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    console.print(f"[bold cyan]Scanning target dossiers in:[/bold cyan] [green]{w_dir}[/green] ({len(target_folders)} targets)")

    total_chunks_indexed = 0

    for target_dir in target_folders:
        target_name = target_dir.name
        
        # Check target metadata
        overview_file = target_dir / "overview.md"
        url = ""
        if overview_file.exists():
            for line in overview_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("url:"):
                    url = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break

        with conn:
            conn.execute("""
                INSERT INTO targets(name, url, indexed_at)
                VALUES(?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    url = excluded.url,
                    indexed_at = excluded.indexed_at
            """, (target_name, url, datetime.now().isoformat()))

            # Remove previous chunks for this target
            old_doc_ids = [row[0] for row in conn.execute("SELECT id FROM documents WHERE target_name = ?", (target_name,)).fetchall()]
            for doc_id in old_doc_ids:
                conn.execute("DELETE FROM vec_documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE target_name = ?", (target_name,))

        # Collect all markdown files for this target
        target_docs = []
        for md_file in target_dir.glob("*.md"):
            doc_type = md_file.stem
            content = md_file.read_text(encoding="utf-8")
            chunks = chunk_text(content)
            for idx, chunk in enumerate(chunks):
                target_docs.append({
                    "target_name": target_name,
                    "doc_type": doc_type,
                    "rel_path": str(md_file.relative_to(w_dir)),
                    "title": f"{target_name} / {doc_type} (chunk {idx + 1})",
                    "chunk_index": idx,
                    "content": chunk,
                })

        if not target_docs:
            continue

        # Batch embed chunks
        texts_to_embed = [d["content"] for d in target_docs]
        embeddings = list(model.embed(texts_to_embed))

        with conn:
            for doc, emb in zip(target_docs, embeddings):
                cur = conn.execute("""
                    INSERT INTO documents(target_name, doc_type, rel_path, title, chunk_index, content)
                    VALUES(?, ?, ?, ?, ?, ?)
                """, (doc["target_name"], doc["doc_type"], doc["rel_path"], doc["title"], doc["chunk_index"], doc["content"]))
                
                doc_id = cur.lastrowid
                conn.execute("""
                    INSERT INTO vec_documents(id, embedding)
                    VALUES(?, ?)
                """, (doc_id, pack_vector(list(emb))))
                total_chunks_indexed += 1

        console.print(f"  • Indexed [green]{target_name}[/green]: {len(target_docs)} chunks")

    console.print(f"[bold green]✓ Indexing complete! Total chunks indexed: {total_chunks_indexed}[/bold green]")
    return total_chunks_indexed

def search_patterns(
    query_str: str,
    limit: int = 5,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Performs hybrid vector similarity and keyword search across all indexed target dossiers."""
    conn = get_db(db_path)
    model = get_embed_model()

    # Compute query embedding
    query_vector = list(model.embed([query_str]))[0]
    packed_query = pack_vector(list(query_vector))

    # KNN vector query
    cursor = conn.execute("""
        SELECT 
            d.id,
            d.target_name,
            d.doc_type,
            d.rel_path,
            d.title,
            d.content,
            v.distance
        FROM vec_documents v
        JOIN documents d ON d.id = v.id
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance ASC
    """, (packed_query, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "target": row[1],
            "doc_type": row[2],
            "rel_path": row[3],
            "title": row[4],
            "content": row[5],
            "distance": round(row[6], 4),
        })

    return results

def render_search_results(results: List[Dict[str, Any]], query: str) -> None:
    """Renders formatted search results to console."""
    if not results:
        console.print(f"[yellow]No matching patterns or schemas found for query: '{query}'[/yellow]")
        return

    console.print(f"\n[bold green]Found {len(results)} matching architectural patterns for:[/bold green] [yellow]\"{query}\"[/yellow]\n")

    for idx, res in enumerate(results, start=1):
        table = Table(title=f"Match #{idx}: {res['target']} ({res['doc_type']}) — Distance: {res['distance']}", show_header=False)
        table.add_row("[bold cyan]File:[/bold cyan]", res['rel_path'])
        
        # Display clean preview snippet
        snippet = res['content'].strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "..."
        table.add_row("[bold cyan]Excerpt:[/bold cyan]", snippet)
        console.print(table)
        console.print()
