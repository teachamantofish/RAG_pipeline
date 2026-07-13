# Don't run this directly; use summary_wrapper_hf.py to use a large model on Hugging Face.
import os
import json
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config.summaryconfig import *
from config.chunkerconfig import TOKENIZER
from common.run_context import get_run_context
from common.summary_backends import build_summary_settings, run_summary_backend
from Logger.custom_logger import setup_global_logger
from common.token_counter import main as run_token_counter

ctx = get_run_context()
CWD = ctx['cwd']
chunkfile = CWD / "a_chunks.json"

# Nested settings consumed by the summarize functions; built from the flat
# summaryconfig constants + env overrides (see common/summary_backends.py).
SUMMARY_SETTINGS = build_summary_settings()

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message", "Chunk Summary", "Page Summary"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

# Serializes writes of the chunk list during concurrent summarization.
_FLUSH_LOCK = threading.Lock()


def _save_chunks(chunks: list) -> None:
    """Persist the FULL chunk list back to a_chunks.json."""
    with _FLUSH_LOCK:
        with open(chunkfile, "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)


# Exit if there is no connection to the LLM backend.
def check_llm_connection():
    """Send a test prompt to the LLM backend and exit if it fails."""
    try:
        test_prompt = "Test LLM connection."
        backend = SUMMARY_SETTINGS["chunk"]["backend"]
        params = SUMMARY_SETTINGS["chunk"]
        result = run_summary_backend(backend, test_prompt, params)
        if not result:
            print("[ERROR] LLM backend test returned an empty response.")
            import sys
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] LLM backend test exception: {e}")
        import sys
        sys.exit(1)


def get_testing_mode() -> Optional[int]:
    """Return the testing-mode chunk limit, or None for a full run.

    Accepts int or numeric string; any other value (historically the string
    "Null") is treated as None with a warning so a config typo can't silently
    force testing mode.
    """
    value = globals().get("TESTINGMODE")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(
            "TESTINGMODE=%r is not an integer; running the full pipeline. "
            "Use None or a number.", value
        )
        return None


# Append summary details to the existing provenance file
def update_provenance_with_summary():
    """Load a_provenance.json, add summary details, and save back."""
    prov_path = CWD / "a_provenance.json"

    # Load existing provenance
    try:
        with open(prov_path, "r", encoding="utf-8") as f:
            provenance = json.load(f)
    except FileNotFoundError:
        logger.warning(f"Provenance file not found at {prov_path}, creating new one")
        provenance = {}

    # Update summary details (fill in existing keys)
    provenance["summary"] = {
        "model": f"{SUMMARY_SETTINGS['chunk']['backend']}:{SUMMARY_SETTINGS['chunk']['model']}",
        "prompt_template": SUMMARY_SETTINGS["chunk"]["system_prompt_template"],
        "size": SUMMARY_SETTINGS["chunk"]["size"],
        "temperature": SUMMARY_SETTINGS["chunk"]["temperature"],
    }

    # Save back
    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated provenance file at {prov_path}")


# Chunks under this size receive a metadata preface before their content.
def _tok_len(text: str) -> int:
    """Count tokens using the shared tokenizer; fall back to whitespace split on error."""
    if not text:
        return 0
    try:
        return len(TOKENIZER.encode(text))
    except Exception:
        return len(text.split())


def _prepend_metadata_to_small_chunk(chunk: dict) -> None:
    """
    Insert concatenated heading/page/chunk summaries ahead of short chunk bodies.

    Assumes caller has already checked token_count < PAD_CHUNK_THRESHOLD.
    """
    content = chunk.get("content") or ""

    # Collect metadata lines based on config flags
    metadata_lines = []
    if ADD_CONCAT_HEADER_PATH and chunk.get("concat_header_path"):
        metadata_lines.append(chunk.get("concat_header_path"))
    if ADD_PAGE_SUMMARY and chunk.get("page_summary"):
        metadata_lines.append(chunk.get("page_summary"))
    if ADD_CHUNK_SUMMARY and chunk.get("chunk_summary"):
        metadata_lines.append(chunk.get("chunk_summary"))

    if not metadata_lines:
        return

    prefix_block = "\n".join(metadata_lines)

    lines = content.splitlines()

    # Drop any existing metadata prefix blocks that match the current metadata set.
    while len(lines) >= len(metadata_lines) and all(
        lines[i] == metadata_lines[i] for i in range(len(metadata_lines))
    ):
        lines = lines[len(metadata_lines):]

    body = "\n".join(lines)

    if body:
        new_content = prefix_block + "\n" + body
    else:
        new_content = prefix_block

    chunk["content"] = new_content
    # token_count will be recalculated by token_counter script at end of pipeline


def _is_table_chunk(chunk: dict) -> bool:
    return (chunk.get("chunk_type") or chunk.get("type")) == "table"


def _is_code_chunk(chunk: dict) -> bool:
    # The chunker emits chunk_type="example" for peeled code blocks;
    # tolerate the legacy type="code_example" spelling.
    return (chunk.get("chunk_type") or chunk.get("type")) in ("example", "code_example")


def _prepend_table_summary(chunk: dict) -> None:
    """Ensure table chunks start with their summary followed by the table body."""
    if not _is_table_chunk(chunk):
        return
    summary = (chunk.get("chunk_summary") or "").strip()
    if not summary:
        return
    body = chunk.get("content") or ""
    body_lstrip = body.lstrip()
    if body_lstrip.startswith(summary):
        # Summary already prepended (avoid duplication when re-running script).
        return
    separator = "\n\n" if body else ""
    chunk["content"] = f"{summary}{separator}{body}" if body else summary


def _resolve_h1_root(chunk: dict, by_id: dict) -> Optional[dict]:
    """Follow parent links to find the nearest heading level 1 ancestor."""
    current = chunk
    visited = set()
    while current:
        level = current.get("header_level")
        if isinstance(level, int) and level == 1:
            return current
        parent_id = current.get("parent_id")
        if not parent_id or parent_id in visited:
            break
        visited.add(parent_id)
        current = by_id.get(parent_id)
    return None


def _group_chunks_by_top_heading(chunks: list[dict]) -> "OrderedDict[tuple, dict]":
    """Group chunks by their top-level H1 ancestor (fallback to filename)."""
    by_id = {c.get("id"): c for c in chunks if c.get("id")}
    grouped: OrderedDict[tuple, dict] = OrderedDict()

    for chunk in chunks:
        root = None
        level = chunk.get("header_level")
        if isinstance(level, int):
            root = _resolve_h1_root(chunk, by_id)

        if root is not None:
            key = ("h1", root.get("id"))
            label = root.get("concat_header_path") or root.get("heading") or root.get("id")
            payload = grouped.setdefault(key, {"chunks": [], "label": label, "top_id": root.get("id")})
        else:
            fname = chunk.get("filename") or "unknown"
            key = ("file", fname)
            payload = grouped.setdefault(key, {"chunks": [], "label": fname, "top_id": None})

        payload["chunks"].append(chunk)

    return grouped


def summarize_summaries():
    """
    After chunking and summarizing the chunks, create a page summary rollup of the chunk summaries,
    and store them in each chunk's "page_summary" field.

    For each H1 group:
    - Identify the highest page ancestor (H1, or filename fallback).
    - Concat all chunk summaries for the H1 and its children.
    - Send that to the LLM for summarization.
    - Write the result to every group member's chunk["page_summary"].
    """
    # Load chunks as plain list
    with open(chunkfile, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    logger.info("Loaded chunks from %s; count=%d", chunkfile, len(chunks) if hasattr(chunks, '__len__') else 0)

    grouped = _group_chunks_by_top_heading(chunks)

    for key, payload in grouped.items():
        group = payload["chunks"]
        label = payload["label"]

        if not group:
            continue

        top_chunk = None
        if key[0] == "h1" and payload["top_id"]:
            top_chunk = next((c for c in group if c.get("id") == payload["top_id"]), group[0])
        else:
            levels = [c.get("header_level") for c in group if isinstance(c.get("header_level"), int)]
            if levels:
                min_lvl = min(levels)
                top_chunk = next((c for c in group if c.get("header_level") == min_lvl), group[0])
            else:
                top_chunk = group[0]
                payload["top_id"] = top_chunk.get("id")

        # Build bullet list: start with top node text, then bullets of summaries
        parts = [top_chunk.get("content", "").strip()]

        top_sum = top_chunk.get("chunk_summary")
        if top_sum:
            parts.append(f"- {top_sum.strip()}")
        else:
            logger.warning("Missing chunk_summary for top node %s in %s", top_chunk.get("id"), label)

        # Add child summaries in document order (skip empties, but log)
        for c in group:
            if c is top_chunk:
                continue
            s = c.get("chunk_summary")
            if s:
                parts.append(f"- {s.strip()}")
            else:
                logger.debug("Missing chunk_summary for chunk %s in %s; skipping bullet", c.get("id"), label)

        assembled = "\n".join([p for p in parts if p])

        # If file-level summaries are disabled, skip LLM call but write empty page_summary
        if not ENABLE_FILE_SUMMARY:
            logger.info("FILE_SUMMARY disabled; skipping page summary for %s", label)
            page_summary = ""
        else:
            try:
                logger.debug("Requesting file-level summary (LLM) for %s", label)
                page_summary = run_summary_backend(
                    SUMMARY_SETTINGS["file"]["backend"],
                    assembled,
                    SUMMARY_SETTINGS["file"]
                )
                logger.debug("Received file-level summary for %s (len=%d)", label, len(page_summary or ""))
            except Exception as e:
                logger.error("File summary error for %s: %s", label, e)
                page_summary = ""

        # Write the returned page_summary to every chunk in the same doc.
        # Only small chunks get the metadata preface (same threshold as
        # summarize_chunks); padding everything would blow past the chunker's
        # size budget.
        for c in group:
            c["page_summary"] = page_summary
            if c.get("token_count", 0) < PAD_CHUNK_THRESHOLD:
                _prepend_metadata_to_small_chunk(c)
        logger.debug("Assigned page_summary to %d chunks for %s", len(group), label)

        # Emit a structured CSV log row using the global logger; ensure extras
        # map to the declared headers (Date,Level,Message,Chunk Summary,Page Summary)
        try:
            logger.info(f"{label} page summary", extra={"Chunk Summary": "", "Page Summary": page_summary or ""})
        except Exception:
            logger.exception("Failed to emit log for page summary %s", label)

    # Persist changes
    _save_chunks(chunks)
    logger.info("Wrote page-level summaries back to %s", chunkfile)


def _summarize_one_chunk(chunk: dict) -> None:
    """Generate the chunk summary (and code summary) for a single chunk.

    Mutates only ``chunk``; safe to run from a worker thread.
    """
    try:
        # Build the system prompt with heading context
        heading_context = chunk.get("concat_header_path", "unknown section")
        system_prompt = SUMMARY_SETTINGS["chunk"]["system_prompt_template"].format(
            heading_context=heading_context,
            size=SUMMARY_SETTINGS["chunk"]["size"]
        )

        # Create a params dict with the formatted prompt
        params = SUMMARY_SETTINGS["chunk"].copy()
        params["system_prompt"] = system_prompt

        chunk["chunk_summary"] = run_summary_backend(
            SUMMARY_SETTINGS["chunk"]["backend"],
            chunk["content"],
            params
        )
        try:
            summary_text = " ".join(chunk["chunk_summary"].splitlines()).strip()
            logger.info(f"{chunk.get('id')} chunk summary", extra={"Chunk Summary": summary_text, "Page Summary": ""})
        except Exception:
            logger.exception("Failed emitting structured log for chunk %s", chunk.get('id'))
    except Exception as e:
        logger.error(f"Chunk summary error for chunk {chunk.get('id')}: {e}")
        chunk["chunk_summary"] = ""

    _prepend_table_summary(chunk)

    # Prepend metadata to small chunks after summarization
    if chunk.get("token_count", 0) < PAD_CHUNK_THRESHOLD:
        _prepend_metadata_to_small_chunk(chunk)

    # Code-level summary for peeled code-example chunks.
    if _is_code_chunk(chunk) and ENABLE_CODE_SUMMARY:
        try:
            code_params = {
                "model": CODE_SUMMARY_MODEL,
                "system_prompt": CODE_SUMMARY_PROMPT,
                "size": CODE_SUMMARY_SIZE,
                "temperature": CODE_SUMMARY_TEMPERATURE,
            }
            chunk["code_summary"] = run_summary_backend(
                SUMMARY_SETTINGS["chunk"]["backend"],
                chunk["content"],
                code_params,
            )
            try:
                code_text = " ".join(chunk["code_summary"].splitlines()).strip()
                logger.info(f"Code summarized {chunk.get('id')}", extra={"Chunk Summary": code_text, "Page Summary": ""})
            except Exception:
                logger.exception("Failed emitting structured log for code summary %s", chunk.get('id'))
        except Exception as e:
            logger.error(f"Code summary error for chunk {chunk.get('id')}: {e}")
            chunk["code_summary"] = ""


def _needs_summary(chunk: dict) -> bool:
    """Decide whether a chunk needs a (re)generated summary."""
    if chunk.get("token_count", 0) < SKIP_CHUNK_THRESHOLD:
        return False
    existing = chunk.get("chunk_summary")
    if globals().get("RESUME_SUMMARIES", True) and isinstance(existing, str) and existing and existing != "false":
        # Already summarized on a previous (possibly interrupted) run.
        return False
    return True


def summarize_chunks(testing_limit=None):
    # Load chunks as plain list
    with open(chunkfile, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    logger.info("Loaded chunks for chunk-level summarization; count=%d", len(chunks) if hasattr(chunks, '__len__') else 0)

    # In testing mode only the first N chunks are PROCESSED, but the full list
    # is always kept and saved (a previous version truncated and overwrote
    # a_chunks.json here, permanently deleting the rest of the corpus).
    process_pool = chunks
    if testing_limit is not None:
        try:
            limit = int(testing_limit)
            process_pool = chunks[:limit]
            logger.info("Testing mode: summarizing only the first %d of %d chunks", len(process_pool), len(chunks))
        except (TypeError, ValueError):
            logger.warning("Invalid testing limit %r; summarizing all chunks", testing_limit)

    todo = []
    for chunk in process_pool:
        if chunk.get("token_count", 0) < SKIP_CHUNK_THRESHOLD:
            # Below threshold: mark explicitly empty unless resuming an existing summary.
            if not (globals().get("RESUME_SUMMARIES", True) and chunk.get("chunk_summary")):
                chunk["chunk_summary"] = ""
            continue
        if _needs_summary(chunk):
            todo.append(chunk)
        else:
            logger.debug("Resume: chunk %s already summarized; skipping", chunk.get("id"))

    logger.info("Chunks needing summaries: %d", len(todo))

    max_workers = max(1, int(globals().get("SUMMARY_MAX_WORKERS", 1) or 1))
    flush_every = max(1, int(globals().get("SUMMARY_FLUSH_EVERY", 50) or 50))

    completed = 0
    if max_workers == 1:
        for chunk in todo:
            _summarize_one_chunk(chunk)
            completed += 1
            if completed % flush_every == 0:
                _save_chunks(chunks)
                logger.info("Progress saved: %d/%d summaries", completed, len(todo))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for start in range(0, len(todo), flush_every):
                batch = todo[start:start + flush_every]
                list(pool.map(_summarize_one_chunk, batch))
                completed += len(batch)
                _save_chunks(chunks)
                logger.info("Progress saved: %d/%d summaries", completed, len(todo))

    # Save chunks as plain list (full list, regardless of testing limit)
    _save_chunks(chunks)
    logger.info("Summarized chunks saved to %s", chunkfile)


if __name__ == "__main__":
    check_llm_connection()
    testing_mode = get_testing_mode()
    if testing_mode is None:
        if ENABLE_CHUNK_SUMMARY:
            summarize_chunks()
        if ENABLE_SUMMARY_SUMMARY:
            summarize_summaries()
        update_provenance_with_summary()
        run_token_counter([str(chunkfile)])
    else:
        if ENABLE_CHUNK_SUMMARY:
            summarize_chunks(testing_limit=testing_mode)
        update_provenance_with_summary()
        print(f"[INFO] TESTINGMODE set to {testing_mode}: Only processed {testing_mode} chunks.")
