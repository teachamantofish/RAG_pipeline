import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from run_settings import CWD as SETTINGS_CWD, METADATA as SETTINGS_METADATA
except ModuleNotFoundError:
    # Allow scripts under Get_and_Chunk to run from either repo root or subfolders.
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    from run_settings import CWD as SETTINGS_CWD, METADATA as SETTINGS_METADATA


def get_run_context(output_name: Optional[str] = None) -> Dict[str, Any]:
    # RAG_DATA_ROOT env var overrides run_settings.CWD so the pipeline can run
    # on machines other than the one the (absolute) configured path came from.
    cwd = Path(os.getenv("RAG_DATA_ROOT") or SETTINGS_CWD)
    return {
        "cwd": cwd,
        "metadata": dict(SETTINGS_METADATA),
        "md_to_chunk": cwd / f"{cwd.name}.md",
        "output_path": (cwd / output_name) if output_name else None,
    }
