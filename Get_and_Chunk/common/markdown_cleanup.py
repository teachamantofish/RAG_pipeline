import re


_THIN_SPACE_RE = re.compile(r"[\u2000-\u200A\u202F\u205F\u3000\u00A0]")
_CODE_ONLY_HEADINGS = {
    "request url",
    "response structure",
    "sample request",
    "sample response",
}


def _normalize_line(line: str) -> str:
    """Normalize line text for robust comparison between prose and fenced code."""
    # Some pages include thin spaces/non-breaking spaces before code examples.
    line = _THIN_SPACE_RE.sub(" ", line)
    return " ".join(line.strip().split())


def _strip_all_space(text: str) -> str:
    """Space-insensitive normalization for matching wrapped vs inline examples."""
    return "".join(_normalize_line(text).split())


def _is_example_like_line(line: str) -> bool:
    """Heuristic: line looks like request/response sample payload content."""
    n = _normalize_line(line)
    if not n:
        return False
    if n.startswith("http") or n.startswith("https"):
        return True
    if n.startswith("?action=") or n.startswith("&"):
        return True
    if n.startswith("<?xml") or n.startswith("<") or n in ("...", "…"):
        return True
    if "api/xml" in n:
        return True
    return False


def _normalized_bold_heading(line: str) -> str | None:
    """Return normalized text for '**Heading**' lines, else None."""
    s = line.strip()
    m = re.match(r"^\*\*(.+?)\*\*$", s)
    if not m:
        return None
    return " ".join(m.group(1).split()).strip().lower()


def _looks_like_adobe_connect(markdown: str) -> bool:
    """Return True only for Adobe Connect pages based on YAML front matter."""
    if not markdown.startswith("---\n"):
        return False

    end = markdown.find("\n---\n", 4)
    if end == -1:
        return False

    fm = markdown[4:end]
    fm_lower = fm.lower()
    return (
        "category: adobe connect" in fm_lower
        or "title: adobe connect" in fm_lower
        or "description: adobe connect" in fm_lower
    )


def _is_table_separator_row(line: str) -> bool:
    """Return True when line is a markdown table separator row."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    parts = [p.strip() for p in s.split("|")[1:-1]]
    if not parts:
        return False
    for p in parts:
        if not p:
            return False
        if not re.fullmatch(r":?-{3,}:?", p):
            return False
    return True


def _is_empty_table_row(line: str) -> bool:
    """Return True when all table cells are blank (non-separator)."""
    s = line.strip()
    if not s.startswith("|"):
        return False
    if _is_table_separator_row(s):
        return False
    parts = [p.strip() for p in s.split("|")[1:-1]]
    if not parts:
        return False
    return all(not p for p in parts)


def _merge_table_row(previous: str, continuation: str) -> str:
    """Merge a wrapped table continuation row into the previous row."""
    prev = previous.rstrip()
    cont = continuation.lstrip()
    if cont.startswith("|"):
        cont = cont[1:]
    if prev.endswith("|"):
        prev = prev[:-1].rstrip()
    return f"{prev} |{cont}"


def remove_wrapped_table_rows(markdown: str) -> str:
    """Repair wrapped markdown table rows and remove accidental empty rows."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line.lstrip().startswith("|"):
            out.append(line)
            i += 1
            continue

        # Process one contiguous markdown table block.
        block: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i])
            i += 1

        fixed_block: list[str] = []
        open_row_idx: int | None = None

        for raw in block:
            stripped = raw.lstrip()
            has_leading_whitespace = len(raw) != len(stripped)

            # Drop markdown separator-only rows (e.g., "| --- | --- |")
            # because these pages treat them as visual blank rows.
            if _is_table_separator_row(stripped):
                open_row_idx = None
                continue

            if _is_empty_table_row(stripped):
                continue

            if open_row_idx is not None and has_leading_whitespace and stripped.startswith("|"):
                fixed_block[open_row_idx] = _merge_table_row(fixed_block[open_row_idx], stripped)
                if fixed_block[open_row_idx].rstrip().endswith("|"):
                    open_row_idx = None
                continue

            fixed_block.append(raw)
            if stripped.startswith("|") and not raw.rstrip().endswith("|"):
                open_row_idx = len(fixed_block) - 1
            else:
                open_row_idx = None

        out.extend(fixed_block)

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def _table_column_count(line: str) -> int:
    """Return number of cells for a markdown table row, else 0."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return 0
    return len(s.split("|")) - 2


def _is_table_header_and_separator(header: str, sep: str) -> bool:
    """Return True when two lines form a markdown table header block."""
    h_cols = _table_column_count(header)
    if h_cols <= 0:
        return False
    s = sep.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    parts = [p.strip() for p in s.split("|")[1:-1]]
    if len(parts) != h_cols:
        return False
    return all(bool(re.fullmatch(r":?-{3,}:?", p)) for p in parts)


def repair_broken_table_rows(markdown: str) -> str:
    """Merge table rows that are split across multiple physical lines."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    in_fence = False
    table_cols = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence:
            out.append(line)
            i += 1
            continue

        # Detect table entry from header + separator.
        if i + 1 < len(lines) and _is_table_header_and_separator(lines[i], lines[i + 1]):
            table_cols = _table_column_count(lines[i])
            out.append(lines[i])
            out.append(lines[i + 1])
            i += 2
            continue

        # Not currently parsing table rows.
        if table_cols == 0:
            out.append(line)
            i += 1
            continue

        s = line.strip()

        # End table when we hit a clear boundary.
        if not s:
            out.append(line)
            table_cols = 0
            i += 1
            continue
        if not line.lstrip().startswith("|"):
            out.append(line)
            table_cols = 0
            i += 1
            continue

        # Already-valid row.
        if s.endswith("|") and s.count("|") >= table_cols + 1:
            out.append(line)
            i += 1
            continue

        # Broken row start: collect continuation text until we can close the row.
        merged = s
        open_pipes = merged.count("|")
        i += 1
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()

            if not ns:
                i += 1
                continue

            if nxt.startswith("```") or ns.startswith("#") or re.match(r"^\*\*.+\*\*$", ns):
                break

            if ns.startswith("|"):
                if ns.endswith("|") and ns.count("|") >= table_cols + 1:
                    # This is the next full row, not a continuation.
                    break
                fragment = ns[1:].strip()
                if fragment.endswith("|"):
                    fragment = fragment[:-1].strip()
                    if fragment:
                        if open_pipes == table_cols - 1 and merged.count("|") == table_cols - 1:
                            merged = f"{merged} | {fragment} |"
                        else:
                            merged = f"{merged} {fragment} |"
                        open_pipes = merged.count("|")
                    else:
                        merged = f"{merged} |"
                        open_pipes = merged.count("|")
                    i += 1
                    break
                if fragment:
                    if open_pipes == table_cols - 1 and merged.count("|") == table_cols - 1:
                        merged = f"{merged} | {fragment}"
                    else:
                        merged = f"{merged} {fragment}"
                    open_pipes = merged.count("|")
                i += 1
                continue

            fragment = re.sub(r"^[-*+]\s+", "", ns)
            if open_pipes == table_cols - 1 and merged.count("|") == table_cols - 1:
                merged = f"{merged} | {fragment}"
            else:
                merged = f"{merged} {fragment}"
            open_pipes = merged.count("|")
            i += 1

        if not merged.endswith("|"):
            merged = f"{merged} |"
        out.append(merged)

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def _is_unordered_bullet_line(line: str) -> bool:
    """Return True when line is an unordered markdown list item."""
    return bool(re.match(r"^\s*[*+-]\s+\S", line))


def _is_ordered_list_line(line: str) -> bool:
    """Return True when line is an ordered markdown list item."""
    return bool(re.match(r"^\s*\d+\.\s+\S", line))


def _is_assignment_like_line(line: str) -> bool:
    """Return True for key=value style lines commonly wrapped under bullets."""
    s = line.strip()
    return bool(re.match(r"^[A-Za-z0-9_-]+=[^\s].*", s))


def _is_see_also_boundary_line(line: str) -> bool:
    """Return True when a line indicates the end of a See also content block."""
    s = line.lstrip()
    if not s:
        return True
    if s.startswith("```"):
        return True
    if s.startswith("#"):
        return True
    if s.startswith("|"):
        return True
    if re.match(r"^\*\*.+\*\*$", s):
        return True
    return False


def _is_see_also_heading(line: str) -> bool:
    """Return True for bold 'See also' headings, with optional trailing colon."""
    heading = _normalized_bold_heading(line)
    if heading is None:
        return False
    return heading.rstrip(":").strip() == "see also"


def add_blank_lines_around_bold_headings(markdown: str) -> str:
    """Ensure fully bold standalone lines have one blank line before and after."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    in_fence = False

    for idx, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue

        is_bold_heading = (not in_fence) and (_normalized_bold_heading(line) is not None)
        if not is_bold_heading:
            out.append(line)
            continue

        # Ensure a single blank line before heading.
        while len(out) >= 2 and not out[-1].strip() and not out[-2].strip():
            out.pop()
        if out and out[-1].strip():
            out.append("")

        out.append(line)

        # Ensure a blank line after heading unless next source line is already blank.
        if idx + 1 < len(lines) and lines[idx + 1].strip():
            out.append("")

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def add_blank_lines_around_markdown_headings(markdown: str) -> str:
    """Ensure ATX headings (# to ####) have one blank line before and after."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    in_fence = False
    heading_re = re.compile(r"^\s{0,3}#{1,4}\s+\S")

    for idx, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue

        is_heading = (not in_fence) and bool(heading_re.match(line))
        if not is_heading:
            out.append(line)
            continue

        # Ensure a single blank line before heading.
        while len(out) >= 2 and not out[-1].strip() and not out[-2].strip():
            out.pop()
        if out and out[-1].strip():
            out.append("")

        out.append(line)

        # Ensure a blank line after heading unless next source line is already blank.
        if idx + 1 < len(lines) and lines[idx + 1].strip():
            out.append("")

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def fix_see_also(markdown: str) -> str:
    """Convert non-bulleted '**See also**' lists into linked markdown bullets."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence or not _is_see_also_heading(line):
            out.append(line)
            i += 1
            continue

        out.append(line)
        i += 1

        # Consume blank lines after heading and rewrite with a single blank line.
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines):
            out.append("")

        start = i
        while i < len(lines) and not _is_see_also_boundary_line(lines[i]):
            i += 1
        block = lines[start:i]

        if not block:
            continue

        # Leave existing bulleted see-also sections unchanged.
        if any(_is_unordered_bullet_line(x) for x in block):
            out.extend(block)
            continue

        normalized_lines = [" ".join(x.split()) for x in block if x.strip()]
        if any(re.search(r"[,;]", x) for x in normalized_lines):
            joined = " ".join(normalized_lines)
            raw_items = [x.strip() for x in re.split(r"[,;]", joined) if x.strip()]
        else:
            # One title per line format.
            raw_items = [x.strip() for x in normalized_lines if x.strip()]

        if not raw_items:
            out.extend(block)
            continue

        for item in raw_items:
            display_text = item.strip()
            slug_source = display_text.lower().replace("'", "").replace("’", "")
            slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")
            if not slug:
                continue
            target = f"{slug}.md"
            out.append(f"- [{display_text}]({target})")

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def _looks_like_url_continuation(fragment: str) -> bool:
    """Return True when a line fragment looks like a URL continuation token."""
    return fragment.startswith(("?", "&", "#", "/"))


def _parse_standalone_url_line(line: str) -> tuple[str, bool] | None:
    """Return (url, is_backticked) for standalone URL lines, else None."""
    s = _normalize_line(line)
    m = re.match(r"^`?(https?://[^`\s]+)`?$", s)
    if not m:
        return None
    is_backticked = s.startswith("`") and s.endswith("`")
    return m.group(1), is_backticked


def url_formatting_fixer(markdown: str) -> str:
    """Convert URL-only code fences to single-line inline code URLs."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line.startswith("```"):
            out.append(line)
            i += 1
            continue

        open_fence = line
        j = i + 1
        while j < len(lines) and not lines[j].startswith("```"):
            j += 1

        # Keep unclosed fences unchanged.
        if j >= len(lines):
            out.append(open_fence)
            out.extend(lines[i + 1 :])
            break

        block = lines[i + 1 : j]
        normalized = [_normalize_line(x) for x in block if _normalize_line(x)]

        if not normalized:
            out.append(open_fence)
            out.extend(block)
            out.append(lines[j])
            i = j + 1
            continue

        first = "".join(normalized[0].split())
        rest = ["".join(x.split()) for x in normalized[1:]]

        is_url_block = first.startswith(("http://", "https://")) and all(
            _looks_like_url_continuation(x) for x in rest
        )

        if is_url_block:
            merged_url = first + "".join(rest)
            out.append(f"`{merged_url}`")
        else:
            out.append(open_fence)
            out.extend(block)
            out.append(lines[j])

        i = j + 1

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def dedupe_standalone_url_lines(markdown: str) -> str:
    """Collapse duplicate standalone URL lines and prefer backticked form."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence:
            out.append(line)
            i += 1
            continue

        parsed = _parse_standalone_url_line(line)
        if parsed is None:
            out.append(line)
            i += 1
            continue

        url, is_backticked = parsed
        j = i + 1
        saw_duplicate = False
        saw_backticked = is_backticked

        while j < len(lines):
            nxt = lines[j]
            if nxt.startswith("```"):
                break
            if not nxt.strip():
                j += 1
                continue

            nxt_parsed = _parse_standalone_url_line(nxt)
            if nxt_parsed is None:
                break
            if nxt_parsed[0] != url:
                break

            saw_duplicate = True
            saw_backticked = saw_backticked or nxt_parsed[1]
            j += 1

        if saw_duplicate:
            out.append(f"`{url}`" if saw_backticked else url)
            i = j
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def normalize_unordered_lists(markdown: str) -> str:
    """Force '-' bullets and add blank lines around unordered list items."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence or not _is_unordered_bullet_line(line):
            out.append(line)
            i += 1
            continue

        # Ensure one blank line before each list item.
        while out and not out[-1].strip() and len(out) >= 2 and not out[-2].strip():
            out.pop()
        if out and out[-1].strip():
            out.append("")

        # Start new list item and force dash marker.
        item_text = re.sub(r"^\s*[*+-]\s+", "", line).strip()
        out.append(f"- {item_text}")
        i += 1

        # Attach key=value continuation lines as part of the same item.
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith("```"):
                break
            if not nxt.strip():
                i += 1
                break
            if nxt.lstrip().startswith("#"):
                break
            if nxt.lstrip().startswith("|"):
                break
            if _is_unordered_bullet_line(nxt) or _is_ordered_list_line(nxt):
                break
            if not _is_assignment_like_line(nxt):
                break

            out.append(f"  {nxt.strip()}")
            i += 1

        # Ensure one blank line after each list item.
        if not out or out[-1].strip():
            out.append("")

    # Collapse 3+ consecutive blanks to a single blank line.
    collapsed: list[str] = []
    blank_run = 0
    for ln in out:
        if ln.strip():
            blank_run = 0
            collapsed.append(ln)
        else:
            blank_run += 1
            if blank_run <= 1:
                collapsed.append(ln)

    return "\n".join(collapsed) + ("\n" if markdown.endswith("\n") else "")


def normalize_ordered_list_spacing(markdown: str) -> str:
    """Ensure each ordered list item has a blank line before it."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    in_fence = False

    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue

        if not in_fence and _is_ordered_list_line(line):
            while out and not out[-1].strip() and len(out) >= 2 and not out[-2].strip():
                out.pop()
            if out and out[-1].strip():
                out.append("")

        out.append(line)

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def _is_admonition_boundary_line(line: str) -> bool:
    """Return True when line should end a caution admonition block."""
    s = line.lstrip()
    if not s:
        return True
    if s.startswith("```"):
        return True
    if s.startswith("#"):
        return True
    if re.match(r"^\*\*.+\*\*$", s):
        return True
    if s.startswith("|"):
        return True
    if _is_unordered_bullet_line(s) or _is_ordered_list_line(s):
        return True
    return False


def normalize_caution_admonitions(markdown: str) -> str:
    """Convert standalone 'Caution' blocks into markdown admonition syntax."""
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence or line.strip().lower() != "caution":
            out.append(line)
            i += 1
            continue

        # Ensure one blank line before admonition.
        while out and not out[-1].strip() and len(out) >= 2 and not out[-2].strip():
            out.pop()
        if out and out[-1].strip():
            out.append("")

        out.append("> [!CAUTION]")
        out.append(">")
        i += 1

        # Consume following prose lines as admonition content.
        while i < len(lines) and not _is_admonition_boundary_line(lines[i]):
            out.append(f"> {lines[i].strip()}")
            i += 1

        if i < len(lines) and not lines[i].strip():
            i += 1

        if i < len(lines) and lines[i].strip() and (not out or out[-1].strip()):
            out.append("")

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def remove_prose_duplicate_before_code_fence(markdown: str) -> str:
    """Remove plain-text duplicates that appear immediately before fenced code blocks.

    This targets an Adobe Connect rendering quirk where the same command/XML appears
    as prose plus a fenced block. We keep fenced code and remove adjacent prose-only
    duplicates right above each code fence.
    """
    if not _looks_like_adobe_connect(markdown):
        return markdown

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("```"):
            i += 1
            continue

        # Capture fenced code lines using explicit opening/closing pair.
        open_i = i
        j = open_i + 1
        while j < len(lines) and not lines[j].startswith("```"):
            j += 1
        if j >= len(lines):
            break
        code_lines = lines[open_i + 1 : j]

        code_norm_lines = [_normalize_line(x) for x in code_lines if _normalize_line(x)]
        code_joined = " ".join(code_norm_lines)
        code_compact = _strip_all_space(code_joined)

        # Skip blank lines directly above opening fence.
        k = open_i - 1
        while k >= 0 and not lines[k].strip():
            k -= 1

        # Section-aware cleanup: for known example sections, keep only fenced code.
        heading_idx = None
        heading_name = None
        t = k
        while t >= 0:
            if lines[t].strip().startswith("#"):
                break
            candidate_heading = _normalized_bold_heading(lines[t])
            if candidate_heading:
                heading_idx = t
                heading_name = candidate_heading
                break
            t -= 1

        if heading_idx is not None and heading_name in _CODE_ONLY_HEADINGS and heading_idx + 1 <= open_i - 1:
            # For known code-only sections, drop all prose lines before the fence.
            del lines[heading_idx + 1 : open_i]
            removed = open_i - (heading_idx + 1)
            open_i -= removed
            j -= removed

            # Keep a single visual separator between heading and fenced code.
            if open_i == heading_idx + 1:
                lines.insert(open_i, "")
                open_i += 1
                j += 1

            # Refresh k after section-aware removals.
            k = open_i - 1
            while k >= 0 and not lines[k].strip():
                k -= 1

        # Remove contiguous example-like prose above the fence if it contains
        # the same payload as the fenced block (possibly repeated/wrapped).
        if k >= 0 and code_compact:
            start = k
            while start >= 0 and _is_example_like_line(lines[start]):
                start -= 1
            start += 1

            if start <= k:
                candidate_norm = " ".join(_normalize_line(x) for x in lines[start : k + 1] if _normalize_line(x))
                candidate_compact = _strip_all_space(candidate_norm)
                if code_compact in candidate_compact:
                    del lines[start : k + 1]
                    removed = (k - start + 1)
                    open_i -= removed
                    j -= removed

        # Continue after the closing fence so we do not re-process it as an opener.
        i = max(j + 1, 0)

    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
