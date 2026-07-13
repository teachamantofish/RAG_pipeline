"""Heading-first chunking script used by the pipeline.

This module parses each markdown file into heading blocks, rebuilds the
document-level heading hierarchy, and emits a JSON file of chunks sized for
downstream retrieval/embedding.  Oversized headings are trimmed by peeling
code blocks and tables into dedicated component chunks, and the script keeps
track of relationships such as prev/next pointers and provenance metadata.

## Principles

- Trust the upstream metadata (front matter + heading stack) and retain every heading as its own chunk.
- There is no "too small" merge pass—an empty heading is still emitted (and flagged in the logs).
- When a heading chunk is oversized, peel self-contained structures—code examples and markdown 
tables—into standalone chunks until the heading fits.
- Every emitted chunk keeps the same filename, parent id, and concatenated header path.

## Oversize handling

1. Measure the heading chunk against `MAX_TOKENS_FOR_NODE`.
2. While the chunk is over budget:
   - Remove the largest fenced code block, emit it as an `example` chunk, and recompute the heading size.
   - If no code blocks remain (or the chunk is still too large), remove the largest markdown table next, 
   emit it as a `table` chunk, and continue.
3. If the chunk is still too large after all candidates are exhausted, log a warning and leave it intact.
4. Log each emitted chunk with its type (`heading`, `example`, `table`) and token count.

## Undersize handling

Chunks under a certain threshold are handled in the summary phase. Short chunks have summaries
prepended to their content to provide more context during retrieval.

Empty headings should be chunked. However, "embed" is set to False so that the embedding
script skips them. This allows us to retain the document structure without bloating the vector DB with
empty chunks. We also preserve the concat_header_path so that the UI can display the full context.
"""

import os
import re
import uuid
import json
import yaml
import csv
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Deque
from datetime import datetime
from pygments.lexers import guess_lexer, ClassNotFound

from config.chunkerconfig import *
from common.run_context import get_run_context
from Logger.custom_logger import setup_global_logger
from common.token_counter import main as run_token_counter

ctx = get_run_context(output_name=OUTPUT_NAME)
metadata = ctx['metadata']
CWD: Path = ctx['cwd']
MD_TO_CHUNK: Path = ctx['md_to_chunk']
CHUNK_OUTPUT = ctx['output_path']

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message", "Parent Page", "Token Count"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

# ----------------- Data -----------------
@dataclass
class LeafChunk:
    """In-memory representation of a chunk emitted by this pipeline."""
    # identity / linkage
    id: str
    filename: str
    parent_id: Optional[str]
    id_prev: Optional[str] = None
    id_next: Optional[str] = None

    # heading / structure
    heading: str = ""
    header_level: int = 0
    concat_header_path: str = ""

    # content
    content: str = ""
    examples: List[str] = field(default_factory=list)
    chunk_type: str = "heading"

    # summaries / metadata
    chunk_summary: Optional[str] = None
    page_summary: Optional[str] = None
    language: Optional[str] = None

    # metrics / vectors
    token_count: int = 0
    embedding: Optional[list] = None

# Use @dataclass default __repr__ for LeafChunk (keep representation simple)
# ----------------- Utilities -----------------
def _new_id(prefix: str = "n") -> str:
    """Generate a short, human-scannable identifier with the given prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _resolve_chunk_kind(kind: Optional[str], content: Optional[str]) -> str:
    """Normalize chunk kind based on explicit type or leading content markers."""
    normalized = (content or "").lstrip()
    detected = (kind or "heading").strip().lower()
    if detected == "heading":
        if normalized.startswith("```"):
            return "example"
        if normalized.startswith("<!-- Data Table -->"):
            return "table"
    return detected or "heading"


def build_chunk_id(header_level: int, *, chunk_type: str = "heading", content: Optional[str] = None) -> str:
    """Return an h#-prefixed chunk id and append _exa/_tab when needed."""
    # One place decides when IDs pick up the _exa/_tab suffix (either explicit chunk_type
    # or when heading content itself starts with a code fence / table marker).
    kind = _resolve_chunk_kind(chunk_type, content)
    suffix = ""
    if kind == "example":
        suffix = "_exa"
    elif kind == "table":
        suffix = "_tab"
    return f"{_new_id(f'h{header_level}')}" + suffix

# Define a global helper that all chunking code will use
def _tok(s: str) -> int:
    """Return token count using the global TOKENIZER."""
    if not s:
        return 0
    return len(TOKENIZER.encode(s))
# ----------------- Front-matter -----------------
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

def parse_front_matter_text(md: str) -> Tuple[Dict, str]:
    m = FM_RE.match(md)
    if not m:
        return {}, md
    block = m.group(1)
    try:
        fm = yaml.safe_load(block) or {}
    except Exception:
        fm = {}
    body = md[m.end():]
    return fm, body


def _extract_heading_blocks(md_body: str) -> Deque[Tuple[str, int, str]]:
    """
    Parse markdown body (front matter removed) into ordered heading blocks.
    Returns deque of (heading_text, level, content_text).
    """
    blocks: List[Tuple[str, int, str]] = []
    current_heading: Optional[str] = None
    current_level: Optional[int] = None
    current_lines: List[str] = []

    in_fence = False
    fence_delim = None  # Track fenced blocks so comments with a # at the start of a line are not treated as headings. 

    for line in md_body.splitlines():
        stripped = line.strip()
        fence_match = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence_match:
            delim = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_delim = delim
            elif fence_delim == delim:
                in_fence = False
                fence_delim = None
            if current_heading is not None:
                current_lines.append(line)
            continue

        if in_fence:
            if current_heading is not None:
                current_lines.append(line)
            continue
        # We are not in a fenced block, so we can check for headings
        match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if match:
            if current_heading is not None:
                blocks.append((current_heading, current_level or 0, "\n".join(current_lines).strip()))
            current_heading = match.group(2).strip()
            current_level = len(match.group(1))
            current_lines = []
        else:
            if current_heading is not None:
                current_lines.append(line)

    if current_heading is not None:
        blocks.append((current_heading, current_level or 0, "\n".join(current_lines).strip()))

    return deque(blocks)

# ----------------- File loading -----------------
def load_markdown_files() -> List[Tuple[str, str]]:
    """Return [(relative filename, raw markdown text)] in stable order.

    Reads each file exactly once. A previous version routed file discovery
    through LlamaIndex's MarkdownNodeParser and then re-read/re-parsed every
    file with a local regex, pairing one parser's nodes with the other parser's
    heading blocks by position — any disagreement silently attached the wrong
    body to a heading. The heading-block extractor below is now the single
    source of truth.
    """
    if MD_TO_CHUNK.exists():
        logger.info(f"Loading markdown from single file {MD_TO_CHUNK}")
        paths = [MD_TO_CHUNK]
    else:
        logger.info(f"Loading markdown from directory {CWD}")
        paths = sorted(CWD.rglob("*.md"))

    file_texts: List[Tuple[str, str]] = []
    for path in paths:
        try:
            rel = str(path.resolve().relative_to(CWD.resolve())).replace("\\", "/")
        except Exception:
            rel = path.name
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_texts.append((rel, f.read()))
        except Exception as e:
            logger.warning(f"Skipping unreadable file {path}: {e}")

    logger.info(f"Loaded {len(file_texts)} markdown files")
    return file_texts


# ----------------- Build heading stack & propose leaves -----------------
def build_candidates_from_files(file_texts: List[Tuple[str, str]]) -> Tuple[List[LeafChunk], Dict[str, Dict]]:
    """
    Walk each file's heading blocks in order, maintaining a heading stack to
    compute concat_header_path and parent_id. Emit one LeafChunk per heading.
    Also returns the per-file front-matter map.
    """
    candidates: List[LeafChunk] = []
    front_matter_by_file: Dict[str, Dict] = {}

    for filename, raw in file_texts:
        fm, body_text = parse_front_matter_text(raw)
        front_matter_by_file[filename] = fm

        blocks = _extract_heading_blocks(body_text)
        if not blocks:
            logger.warning(
                "No headings found in %s; file produced no chunks.",
                filename,
                extra={"Parent Page": filename, "Token Count": 0},
            )
            continue

        stack: List[Tuple[str, int, str]] = []  # [(heading, level, node_id)]
        for heading, level, body in blocks:
            body = (body or "").strip()
            if heading == "":
                continue

            # pop to parent lower than this level
            while stack and stack[-1][1] >= level:
                stack.pop()
            node_id = build_chunk_id(level, chunk_type="heading", content=body)
            stack.append((heading, level, node_id))

            concat = " > ".join([s[0] for s in stack])
            parent_id = stack[-2][2] if len(stack) >= 2 else None
            if not body:
                # Heading with no text, only subheadings. The chunk is still
                # emitted (embed flag False, zero tokens) so the hierarchy and
                # concat_header_path stay intact for the UI; the child headings
                # carry the content.
                logger.warning(
                    "Empty heading chunk encountered: %s",
                    heading,
                    extra={"Parent Page": filename, "Token Count": 0},
                )

            candidates.append(
                LeafChunk(
                    # identity / linkage
                    id=node_id,
                    filename=filename,
                    parent_id=parent_id,
                    # heading / structure
                    heading=heading,
                    header_level=level,
                    concat_header_path=concat,
                    # content
                    content=body,
                    examples=[],
                    # metrics / vectors
                    token_count=_tok(body) if body else 0,
                )
            )

    return candidates, front_matter_by_file

# ----------------- Long-code extraction -----------------

# None of this runs if ENABLE_CODE_EXTRACTION is False. 

CODE_BLOCK_RE = re.compile(r"```([A-Za-z0-9_\-+.]*)\s*\n(.*?)\n```", re.DOTALL)

def _find_code_blocks(text: str) -> List[dict]:
    """Return start/end offsets for fenced code blocks within ``text``."""
    out = []
    for m in CODE_BLOCK_RE.finditer(text):
        out.append({"start": m.start(), "end": m.end(), "lang": m.group(1) or None, "code": m.group(2)})
    return out

def _guess_lang(code: str, fallback: Optional[str]) -> Optional[str]:
    """Prefer the explicit fence language, otherwise lean on Pygments heuristics."""
    if fallback:
        return fallback.strip()

    try:
        lx = guess_lexer(code)
        return lx.name
    except ClassNotFound:
        return None

def _split_code_block(code: str, lang: Optional[str]) -> List[str]:
    """Split ``code`` into logical sub-blocks when possible."""
    language = (lang or "").lower()
    if language in {"python", "py"} or re.search(r"(?m)^def\s+\w", code):
        return _split_python_functions(code)
    if language in {"javascript", "js", "typescript", "ts", "jsx", "tsx", "extendscript"} or re.search(
        r"(?m)^(?:export\s+)?(?:async\s+)?function\s+\w", code
    ):
        return _split_js_functions(code)
    return [code]

def _split_python_functions(code: str) -> List[str]:
    """Split top-level Python functions while keeping leading comments."""
    lines = code.splitlines()
    if not lines:
        return [code]

    starts: List[int] = []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith("def ") and indent == 0:
            start = idx
            look = idx - 1
            while look >= 0:
                prev = lines[look]
                if not prev.strip():
                    break
                prev_stripped = prev.lstrip()
                if prev_stripped.startswith("#") and len(prev) - len(prev_stripped) == 0:
                    start = look
                    look -= 1
                    continue
                break
            starts.append(start)

    if not starts:
        return [code]

    starts.append(len(lines))
    segments: List[str] = []
    for left, right in zip(starts, starts[1:]):
        segment = "\n".join(lines[left:right]).strip("\n")
        if segment.strip():
            segments.append(segment)

    return segments or [code]

def _split_js_functions(code: str) -> List[str]:
    """Split top-level JavaScript/TypeScript-style functions, including leading comments."""
    lines = code.splitlines()
    if not lines:
        return [code]

    def is_function_start(text: str) -> bool:
        patterns = [
            r"(?:export\s+)?(?:async\s+)?function\s+\w+\s*\(",
            r"(?:export\s+)?default\s+function\s+\w*\s*\(",
            r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?function\s*\(",
            r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*{",
        ]
        return any(re.match(p, text) for p in patterns)

    starts: List[int] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if is_function_start(stripped):
            start = idx
            look = idx - 1
            while look >= 0:
                prev = lines[look]
                prev_stripped = prev.strip()
                if not prev_stripped:
                    break
                if prev_stripped.startswith("//") or prev_stripped.startswith("/*") or prev_stripped.startswith("*"):
                    start = look
                    look -= 1
                    continue
                break
            starts.append(start)

    if not starts:
        return [code]

    starts.append(len(lines))
    segments: List[str] = []
    for left, right in zip(starts, starts[1:]):
        segment = "\n".join(lines[left:right]).strip("\n")
        if segment.strip():
            segments.append(segment)

    return segments or [code]

def _make_component_chunk(source: LeafChunk, *, content: str, chunk_type: str, language: Optional[str] = None) -> LeafChunk:
    """Create a chunk derived from ``source`` that holds a peeled component."""
    normalized = content.strip()
    resolved_kind = _resolve_chunk_kind(chunk_type, normalized)
    if resolved_kind == "table":
        # Replace legacy table markers with a descriptive title tied to the parent heading.
        normalized = _decorate_table_chunk(source.heading, normalized)
    chunk_id = build_chunk_id(source.header_level, chunk_type=resolved_kind, content=normalized)
    return LeafChunk(
        id=chunk_id,
        filename=source.filename,
        # The component was peeled OUT of `source`, so it is a child of the
        # heading chunk itself (not a sibling parented to source's parent).
        parent_id=source.id,
        heading=source.heading,
        header_level=source.header_level,
        concat_header_path=source.concat_header_path,
        content=normalized,
        examples=[],
        chunk_type=resolved_kind,
        chunk_summary=None,
        page_summary=None,
        language=language or source.language,
        token_count=_tok(normalized),
        embedding=None,
    )


TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _find_tables(text: str) -> List[Dict[str, int]]:
    """Locate tabular regions (markdown pipe or CSV) outside of code fences."""
    tables: List[Dict[str, int]] = []
    if not text:
        return tables

    code_ranges = [(m.start(), m.end()) for m in CODE_BLOCK_RE.finditer(text)]

    def _in_code(idx: int) -> bool:
        return any(start <= idx < end for start, end in code_ranges)

    lines = text.splitlines(keepends=True)
    idx = 0
    block_start: Optional[int] = None
    block_type: Optional[str] = None  # "pipe" | "csv"
    expected_cols: Optional[int] = None
    block_lines = 0

    def count_header_columns(line: str) -> Optional[int]:
        if "," not in line:
            return None
        try:
            row = next(csv.reader([line]))
            if len(row) >= 2:
                return len(row)
        except Exception:
            pass

        # Fallback: simple comma count (header rows are usually clean).
        cols = sum(1 for _ in line.split(","))
        return cols if cols >= 2 else None

    def has_required_columns(line: str, expected: Optional[int]) -> bool:
        if expected is None or expected < 2:
            return False
        if "," not in line:
            return False
        parts = line.split(",", expected - 1)
        return len(parts) >= expected and any(p.strip() for p in parts[1:])

    def flush(end_idx: int) -> None:
        nonlocal block_start, block_type, expected_cols, block_lines
        if block_start is not None and block_lines >= 2:
            tables.append({"start": block_start, "end": end_idx})
        block_start = None
        block_type = None
        expected_cols = None
        block_lines = 0

    for line in lines:
        line_end = idx + len(line)

        if _in_code(idx):
            flush(idx)
            idx = line_end
            continue

        stripped = line.strip()
        if not stripped:
            flush(idx)
            idx = line_end
            continue

        is_pipe = bool(TABLE_ROW_RE.match(stripped))
        csv_candidate = not is_pipe and "," in stripped

        if is_pipe:
            if block_type not in ("pipe", None):
                flush(idx)
            if block_start is None:
                block_start = idx
                block_type = "pipe"
            block_lines += 1
        elif csv_candidate:
            if block_type not in ("csv", None):
                flush(idx)
            if block_type != "csv":
                header_cols = count_header_columns(stripped)
                if header_cols is None:
                    idx = line_end
                    continue
                block_start = idx
                block_type = "csv"
                expected_cols = header_cols
                block_lines = 1
            else:
                if expected_cols is None:
                    expected_cols = count_header_columns(stripped)
                if has_required_columns(stripped, expected_cols):
                    block_lines += 1
                else:
                    flush(idx)
                    header_cols = count_header_columns(stripped)
                    if header_cols is not None:
                        block_start = idx
                        block_type = "csv"
                        expected_cols = header_cols
                        block_lines = 1
                    else:
                        block_start = None
                        block_type = None
                        expected_cols = None
                        block_lines = 0
        else:
            flush(idx)

        idx = line_end

    flush(len(text))

    # Filter pipe tables that lack separator rows to avoid false positives.
    filtered: List[Dict[str, int]] = []
    for tb in tables:
        segment = text[tb["start"]:tb["end"]]
        if "|" in segment:
            if segment.count("\n") >= 1 and (TABLE_SEP_RE.search(segment) or "|---" in segment):
                filtered.append(tb)
        else:
            # CSV block - accept as-is
            filtered.append(tb)

    return filtered


def _expand_table_region(text: str, start_idx: int) -> int:
    """Extend a table slice upward to grab leading markers/blank lines."""
    expanded = start_idx
    while expanded > 0:
        prev_nl = text.rfind("\n", 0, max(expanded - 1, 0))
        line_start = 0 if prev_nl == -1 else prev_nl + 1
        candidate = text[line_start:expanded]
        stripped = candidate.strip()
        if not stripped:
            expanded = line_start
            continue
        if stripped == "<!-- Data Table -->" or stripped.startswith("Table:"):
            expanded = line_start
            continue
        break
    return expanded


def _strip_table_wrappers(table_text: str) -> str:
    """Drop legacy table markers/captions before rebuilding the title."""
    cleaned: List[str] = []
    for line in table_text.splitlines():
        stripped = line.strip()
        if not stripped and not cleaned:
            continue
        if stripped == "<!-- Data Table -->" or stripped.startswith("Table:"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _try_parse_csv_rows(table_body: str) -> List[List[str]]:
    lines = [ln for ln in table_body.splitlines() if ln.strip()]
    if not lines or not any("," in ln for ln in lines[:2]):
        return []
    try:
        reader = csv.reader(lines)
        rows = [[cell.strip() for cell in row] for row in reader]
    except Exception:
        return []
    if len(rows) == 1 and len(rows[0]) <= 1:
        return []
    return rows


def _try_parse_pipe_rows(table_body: str) -> List[List[str]]:
    lines = [ln for ln in table_body.splitlines() if ln.strip()]
    if not lines or not any("|" in ln for ln in lines):
        return []
    rows: List[List[str]] = []
    for line in lines:
        stripped = line.strip()
        if TABLE_SEP_RE.match(stripped):
            continue
        if "|" not in stripped:
            return []
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


def _summarize_table_rows(table_body: str) -> Optional[Dict[str, object]]:
    rows = _try_parse_csv_rows(table_body)
    header_offset = 0
    if rows:
        header_offset = 1 if len(rows) > 1 else 0
    else:
        rows = _try_parse_pipe_rows(table_body)
        header_offset = 1 if rows and len(rows) > 1 else 0
    if not rows:
        return None
    data_rows = rows[header_offset:] if len(rows) > header_offset else rows
    data_rows = [row for row in data_rows if row and any(cell.strip() for cell in row)]
    if not data_rows:
        return None
    first_value = (data_rows[0][0].strip() if data_rows[0] and data_rows[0][0].strip() else "n/a")
    last_value = (data_rows[-1][0].strip() if data_rows[-1] and data_rows[-1][0].strip() else "n/a")
    return {
        "first_value": first_value or "n/a",
        "last_value": last_value or "n/a",
        "row_count": len(data_rows),
    }


def _decorate_table_chunk(heading: str, table_text: str) -> str:
    """Inject a descriptive title using the parent heading and table bounds."""
    focus_heading = heading.strip() or "Untitled Table"
    core = _strip_table_wrappers(table_text)
    if not core:
        return f"Table: {focus_heading} (from n/a to n/a: 0)"
    summary = _summarize_table_rows(core)
    if summary:
        start_val = summary.get("first_value", "n/a") or "n/a"
        end_val = summary.get("last_value", "n/a") or "n/a"
        row_count = summary.get("row_count", 0) or 0
    else:
        start_val = "n/a"
        end_val = "n/a"
        row_count = 0
    title = f"Table: {focus_heading} (from {start_val} to {end_val}: {row_count} rows)"
    return f"{title}\n\n{core}" if core else title

def enforce_chunk_size(chunks: List[LeafChunk]) -> List[LeafChunk]:
    """Ensure chunks respect ``MAX_TOKENS_FOR_NODE`` by peeling components."""
    if not ENABLE_CODE_EXTRACTION:
        logger.info("Component extraction disabled by config; skipping chunk size enforcement.")
        return chunks

    final_chunks: List[LeafChunk] = []

    for chunk in chunks:
        text = (chunk.content or "").strip()
        chunk.content = text
        chunk.token_count = _tok(text)
        chunk.examples = []

        components: List[LeafChunk] = []

        while chunk.token_count > MAX_TOKENS_FOR_NODE:
            # Pass 1: bleed off the largest fenced code block (usually examples/snippets).
            blocks = _find_code_blocks(text)
            # Identify the largest fenced code block within the current chunk.
            largest_code = None
            largest_tokens = -1
            for block in blocks:
                block_text = text[block["start"]:block["end"]]
                tokens = _tok(block_text)
                if tokens > largest_tokens:
                    largest_tokens = tokens
                    largest_code = block

            if largest_code:
                # Promote the largest fenced code block into its own example component chunk.
                code_text = largest_code["code"].rstrip()
                lang = _guess_lang(code_text, largest_code["lang"])
                sub_blocks = _split_code_block(code_text, lang or largest_code["lang"])
                for sub in sub_blocks:
                    fenced = f"```{largest_code['lang'] or ''}\n{sub.strip()}\n```".strip()
                    example_chunk = _make_component_chunk(chunk, content=fenced, chunk_type="example", language=lang)
                    components.append(example_chunk)
                    if example_chunk.id not in chunk.examples:
                        chunk.examples.append(example_chunk.id)
                # Excise the peeled code block and recalculate the parent chunk tokens.
                text = (text[:largest_code["start"]] + text[largest_code["end"]:]).strip()
                chunk.content = text
                chunk.token_count = _tok(text)
                continue

            # Pass 2: if code peeling could not shrink enough, attempt the last table block found.
            # Repeats until the size constraint is satisfied or no tables are left.
            tables = _find_tables(text)
            tail_table = tables[-1] if tables else None

            if tail_table:
                table_start = _expand_table_region(text, tail_table["start"])
                table_body = text[table_start:tail_table["end"]].strip()
                # Strip the marker/comment wrapper alongside the CSV so the parent chunk keeps only real prose.
                components.append(_make_component_chunk(chunk, content=table_body, chunk_type="table"))
                text = (text[:table_start] + text[tail_table["end"]:]).strip()
                chunk.content = text
                chunk.token_count = _tok(text)
                continue

            # No removable components remain and the chunk is still large.
            logger.warning(
                "Chunk %s remains over max size (%s tokens) despite removing components",
                chunk.id,
                chunk.token_count,
            )
            break

        chunk.content = text
        chunk.token_count = _tok(text)
        chunk.chunk_type = _resolve_chunk_kind(chunk.chunk_type, chunk.content)
        if chunk.chunk_type == "table":
            first_line = chunk.content.splitlines()[0].strip() if chunk.content else ""
            needs_title = "<!-- Data Table -->" in chunk.content or (first_line.startswith("Table:") and "(from" not in first_line)
            if needs_title:
                chunk.content = _decorate_table_chunk(chunk.heading or chunk.concat_header_path, chunk.content)
                chunk.token_count = _tok(chunk.content)
        if not chunk.content:
            # Heading with only a code example: the chunk is emitted as a normal heading chunk unless
            # it exceeds MAX_TOKENS_FOR_NODE. When it’s oversized, the largest fenced block is peeled
            # into its own chunk_type="example" chunk, and the parent heading shrinks accordingly
            # If that was the only content, the heading chunk becomes empty; the
            # script logs it, but still keeps the chunk so the hierarchy remains intact, and later marks
            # it non-embeddable (embedding="false") because its token count is zero. In short, the 
            # code example survives as a separate component chunk while the heading stub persists for structure.
            logger.info(
                "Chunk %s has 0 token count after moving child chunks to their own chunk due to MAX_TOKENS_FOR_NODE threshold.",
                chunk.id,
                extra={"Parent Page": chunk.filename, "Token Count": chunk.token_count},
            )
        final_chunks.append(chunk)
        final_chunks.extend(components)

    logger.info(f"Chunks after size enforcement: {len(final_chunks)}")
    return final_chunks

# ----------------- prev/next -----------------
def link_prev_next(chunks: List[LeafChunk]) -> None:
    """Populate ``id_prev``/``id_next`` to support retrieval and downstream UI pagination.

    Links are scoped per source file so "expand context" walks never cross
    document boundaries (the list spans every file in the corpus).
    """
    by_file: Dict[str, List[LeafChunk]] = {}
    for ch in chunks:
        by_file.setdefault(ch.filename, []).append(ch)

    for file_chunks in by_file.values():
        for i, ch in enumerate(file_chunks):
            ch.id_prev = file_chunks[i-1].id if i > 0 else None
            ch.id_next = file_chunks[i+1].id if i < len(file_chunks)-1 else None

# ----------------- Save (your schema) -----------------
def save_chunks_with_ordered_fields(chunks: List[dict], path: str, metadata: Dict):
    """
    Persist a list of chunk dicts to JSON with a stable field order.
    Provenance is saved separately to a_provenance.json.

    Field ordering mirrors the LeafChunk dataclass grouping:
      1) identity / linkage
      2) heading / structure
      3) content
      4) summaries / metadata
      5) metrics / vectors

    Args:
        chunks: List of chunk dictionaries (already flattened, not dataclass instances).
        path:   Output JSON path.
        metadata: File-level front-matter dict; stamps standard keys
    """
    logger.info(f"Preparing to save {len(chunks)} chunks to {path}")
    if chunks:
        logger.info(f"First 5 chunk IDs: {[c.get('id') for c in chunks[:5]]}")

    ordered_chunks: List[OrderedDict] = []

    prov_id = f"prov_{datetime.now().strftime('%m_%d_%y')}"

    for chunk in chunks:
        ordered = OrderedDict()
        # --- 1) identity / linkage ---
        ordered["id"] = chunk.get("id")
        ordered["filename"] = chunk.get("filename")
        ordered["parent_id"] = chunk.get("parent_id")
        ordered["id_prev"] = chunk.get("id_prev")
        ordered["id_next"] = chunk.get("id_next")

        # --- 2) heading / structure ---
        ordered["heading"] = chunk.get("heading")
        ordered["header_level"] = chunk.get("header_level")
        ordered["concat_header_path"] = chunk.get("concat_header_path")
        ordered["chunk_type"] = chunk.get("chunk_type")

        # --- 3) content ---
        ordered["content"] = chunk.get("content")
        ordered["examples"] = chunk.get("examples")

        # --- 4) summaries / metadata ---
        ordered["chunk_summary"] = chunk.get("chunk_summary")
        ordered["page_summary"] = chunk.get("page_summary")
        ordered["title"] = metadata.get("METADATA_TITLE", "")
        ordered["author"] = metadata.get("METADATA_AUTHOR", "")
        ordered["category"] = metadata.get("METADATA_CATEGORY", "")
        ordered["description"] = metadata.get("METADATA_DESCRIPTION", "")
        ordered["language"] = chunk.get("language")

        # --- 5) metrics / vectors ---
        ordered["token_count"] = chunk.get("token_count")
        ordered["embed"] = chunk.get("embed", True)
        ordered["embedding"] = chunk.get("embedding")

        # --- 6) provenance reference ---
        ordered["prov_id"] = prov_id

        ordered_chunks.append(ordered)

    # Build provenance block once
    provenance = {
        "prov_id": prov_id,
        "timestamp": datetime.now().strftime('%m_%d_%y'),
        "chunk": {
            "model": CHUNK_MODEL,
            "chunk_size_range": CHUNK_SIZE_RANGE,
            "keyword_density": KEYWORD_DENSITY,
        },
        "summary": {
            "model": "",
            "prompt": "",
            "size": "",
            "temperature": "",
        },
        "embed": {
            "model": "",
            "vectorsize": "",
        },
    }

    # Write chunks as plain JSON array
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ordered_chunks, f, indent=2, ensure_ascii=False)

    # Write simple CSV with the same token counts stored in JSON
    csv_path = Path(path).with_name("chunk_token_counts_report.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["header_level", "heading", "token_count"])
        for ordered in ordered_chunks:
            writer.writerow([
                ordered.get("header_level", ""),
                ordered.get("heading", ""),
                ordered.get("token_count", ""),
            ])

    # Write provenance separately
    prov_path = Path(path).parent / "a_provenance.json"
    with open(prov_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(provenance, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(ordered_chunks)} chunks to {path}")
    logger.info(f"Saved token counts CSV to {csv_path}")
    logger.info(f"Saved provenance to {prov_path}")

def chunks_to_dicts(chunks: List[LeafChunk]) -> List[dict]:
    """
    Convert LeafChunk dataclass instances to plain dicts (no embeddings computed here),
    keeping keys in the same logical groups/order used by save_chunks_with_ordered_fields.

    Zero-token chunks (empty headings kept for structure) get embed=False so
    downstream stages skip them. The embedding field itself stays list|None —
    a previous version stored the string "false" there, forcing stringly-typed
    checks through the embedding and upsert scripts.
    """
    out: List[dict] = []
    for ch in chunks:
        out.append({
            # 1) identity / linkage
            "id": ch.id,
            "filename": ch.filename,
            "parent_id": ch.parent_id,
            "id_prev": ch.id_prev,
            "id_next": ch.id_next,

            # 2) heading / structure
            "heading": ch.heading,
            "header_level": ch.header_level,
            "concat_header_path": ch.concat_header_path,
            "chunk_type": ch.chunk_type,

            # 3) content
            "content": ch.content,
            "examples": ch.examples,

            # 4) summaries / metadata
            "chunk_summary": ch.chunk_summary,
            "page_summary": ch.page_summary,
            "language": ch.language,

            # 5) metrics / vectors
            "token_count": ch.token_count,
            "embed": ch.token_count > 0,
            "embedding": ch.embedding,
        })
    return out

# ----------------- Driver -----------------
def process_directory():
    """
    1) Read markdown files (single file or recursive directory)
    2) Reconstruct heading stacks → leaf candidates
    3) Enforce size limits by peeling code examples/tables into separate chunks
    4) Link prev/next pointers (per file)
    5) Emit ALL chunks to CWD/a_chunks.json
    """
    file_texts = load_markdown_files()

    candidates, fm_by_file = build_candidates_from_files(file_texts)
    logger.info(f"Candidates built: {len(candidates)}")

    final_chunks = enforce_chunk_size(candidates)
    logger.info(f"Final chunk count: {len(final_chunks)}")

    link_prev_next(final_chunks)

    # Log a CSV row per chunk using the declared extra columns in LOG_HEADER.
    # The CSV formatter in Logger/custom_logger.py will place these extras into the
    # corresponding columns (e.g. "Parent Page", "Token Count").
    for ch in final_chunks:
        # Message column: include chunk id and a short heading for context
        msg = f"chunk:{ch.id} type={ch.chunk_type} heading={ch.heading[:80]}"
        try:
            logger.info(msg, extra={"Parent Page": ch.filename, "Token Count": ch.token_count})
        except Exception:
            # Ensure logging never breaks the pipeline; fall back to simple info
            logger.info(f"{msg} parent={ch.filename} tokens={ch.token_count}")

    save_chunks_with_ordered_fields(chunks_to_dicts(final_chunks), CHUNK_OUTPUT, metadata=metadata)

    logger.info(f"Done. Wrote {len(final_chunks)} chunks to {CHUNK_OUTPUT}")

# If run directly:
if __name__ == "__main__":
    process_directory()
    run_token_counter([str(CHUNK_OUTPUT)])
