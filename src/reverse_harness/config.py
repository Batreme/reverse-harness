from pathlib import Path

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORAGE_DIR = ROOT_DIR / "storage"
DEFAULT_DB_PATH = DEFAULT_STORAGE_DIR / "index.sqlite"
DEFAULT_RAW_DIR = ROOT_DIR / "raw"
DEFAULT_SESSIONS_DIR = ROOT_DIR / ".sessions"
UPSTREAM_DIR = ROOT_DIR / "upstream" / "reverse-skill"

# Default path to fleet wiki targets directory
DEFAULT_FLEET_WIKI_DIR = (ROOT_DIR.parent / "fleet" / "wiki" / "targets").resolve()
