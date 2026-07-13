# Move out of chunker: 
#    parent_parts = [p.strip().lower() for p in parent_path.strip("/").split("/") if p.strip()]
#    current_heading = top_heading.strip().lower()
#    REMOVE heading numbering
#    remove code line numbers

import re
import csv
from pathlib import Path
try:
    from pygments.lexers import guess_lexer  # Guesslang may be more accurate
    from pygments.util import ClassNotFound
except Exception:  # Pygments may not be installed in some environments
    guess_lexer = None
    class ClassNotFound(Exception):
        pass
  
CHUNK_MIN_THRESHOLD = 30  # Minimum number of characters (not tokens) required to keep content under a heading
CODE_LENGTH = 8  # default minimal lines to consider code meaningful (fallback value)
KEYWORD_DENSITY = 0.2  # default density threshold

def remove_content_before_h1(markdown: str) -> str:
    """
    Removes all content before the first level-1 heading (# ...) after front matter.
    Preserves YAML front matter if present.
    """
    # Detect front matter block (--- ... ---)
    front_matter_match = re.match(r'^(---\s*\n[\s\S]*?---\s*\n)', markdown)
    if front_matter_match:
        front_matter = front_matter_match.group(1)
        rest = markdown[len(front_matter):]
    else:
        front_matter = ''
        rest = markdown

    # Iterate line-by-line to avoid cutting inside a fenced code block
    lines = rest.splitlines(keepends=True)
    in_code = False
    cut_index = None
    for i, line in enumerate(lines):
        # Toggle code fence state (supports ``` or ```lang)
        if line.startswith('```'):
            in_code = not in_code
            continue
        if not in_code and line.startswith('# '):  # level-1 heading outside code
            cut_index = i
            break

    if cut_index is not None:
        cleaned = ''.join(lines[cut_index:])
    else:
        cleaned = rest

    return front_matter + cleaned

def remove_content_under_heading_below_chunk_min_threshold(markdown: str) -> str:
    """Remove content under any heading when the content (in characters) is below CHUNK_MIN_THRESHOLD.

    The heading line itself is always preserved so later processing / concatenation can decide
    how to merge short sections. Only the immediate block of text until the next heading (or EOF)
    is evaluated. Character counting uses the raw content length (after stripping leading/trailing
    whitespace) rather than tokenization.

    Examples:
        # Heading\nShort text -> becomes just '# Heading'\n
        ## Another\n(longer content exceeding threshold) -> preserved unchanged
    """
    pattern = r'^(#{1,6}[^\n]*)\n(.*?)(?=^#{1,6}|\Z)'
    def replace_func(match):
        heading = match.group(1)
        content = match.group(2)
        char_len = len(content.strip())
        if char_len < CHUNK_MIN_THRESHOLD:
            return f"{heading}\n"
        else:
            return match.group(0)
    return re.sub(pattern, replace_func, markdown, flags=re.MULTILINE | re.DOTALL)

def normalize_headings(markdown: str) -> str:
    """
    Removes leading numbers from markdown headings (e.g., '# 1. Introduction' -> '# Introduction').
    Converts heading text to sentence case and strips whitespace.
    """
    out_lines = []
    in_fence = False
    fence_re = re.compile(r'^```')

    for line in markdown.splitlines(True):
        # Toggle fenced code block state and leave fences unchanged
        if fence_re.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue

        # Do not operate within code fences where a # might be present. 
        if not in_fence:
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                hashes, text = m.group(1), m.group(2)
                # Strip numeric prefixes like '3', '3.3', '1.2.3' (with or without trailing punctuation/spaces)
                text = re.sub(r'^\s*\d+(?:\.\d+)*\s*', '', text).strip()
                out_lines.append(f"{hashes} {text}\n")
                continue

        out_lines.append(line)

    return ''.join(out_lines)

    
def delete_specified_heading_content(markdown: str) -> str:
    """
    Removes any heading and the content immediately below it where the heading contains
    '#### On this page' or '##### Search'.
    """
    # Pattern for headings and their content until the next heading or EOF
    patterns = [
        r'^####[ \t]*On this page[^\n]*\n(?:.*?)(?=^#|\Z)',
        r'^#####[ \t]*Search[^\n]*\n(?:.*?)(?=^#|\Z)'
    ]
    for pat in patterns:
        markdown = re.sub(pat, '', markdown, flags=re.MULTILINE | re.DOTALL)
    markdown = re.sub(r'^# comment:\s*$\n?', '', markdown, flags=re.MULTILINE)  # remove only empty comment marker lines
    return markdown

def delete_content_under_heading_below_threshold(markdown: str, min_threshold: int) -> str:
    """
    Removes any heading and the content immediately below it if the content length is below the specified threshold.
    """
    # Pattern for headings and their content until the next heading or EOF
    pattern = r'^(#+[^\n]*)\n(.*?)(?=^#|\Z)'
    matches = re.finditer(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
    for match in matches:
        heading = match.group(1)
        content = match.group(2)
        if len(content.split()) < min_threshold:
            markdown = markdown.replace(match.group(0), '', 1)
    return markdown


def custom_regex(markdown_content, addlines_to_codeblocks=True):
    """
    Clean markdown content using custom regex. The regex list is stored in regex_replacements.csv.
    """
    # Do not normalize line endings here; allow CSV-driven rules to match CRLF/LF directly.

    # Load regex replacements from external CSV for easier maintenance
    csv_path = Path(__file__).parent / 'regex_replacements.csv'
    if csv_path.exists():
        rules = []
        with csv_path.open(encoding='utf-8') as f:
            reader = csv.reader(f)
            # Skip header if present
            header = next(reader, None)
            for row in reader:
                # Skip empty rows or comment lines
                if not row:
                    continue
                first = row[0].strip()
                if first.startswith('#') or first == '':
                    continue

                # Support an extra trailing comment column: map columns by position
                # Expect: pattern,replacement,flags,priority[,comment]
                pat = row[0]
                repl_raw = row[1] if len(row) > 1 else ''
                repl = repl_raw.encode('utf-8').decode('unicode_escape')
                flags_spec = (row[2] if len(row) > 2 else '') or ''
                flags_spec = flags_spec.upper()
                pr_raw = (row[3].strip() if len(row) > 3 else '')
                priority = int(pr_raw) if pr_raw.isdigit() else 100

                # Build flags bitmask
                flag_map = {'I': re.IGNORECASE, 'M': re.MULTILINE, 'S': re.DOTALL, 'X': re.VERBOSE}
                flag_bits = 0
                for ch in flags_spec:
                    flag_bits |= flag_map.get(ch, 0)
                try:
                    compiled = re.compile(pat, flag_bits)
                    rules.append((priority, compiled, repl))
                except re.error:
                    # Skip invalid pattern
                    continue

        # Sort by priority and apply
        for _, regex_obj, repl in sorted(rules, key=lambda r: r[0]):
            markdown_content = regex_obj.sub(repl, markdown_content)

    # Only run the code block comment logic if addlines_to_codeblocks is True
    if addlines_to_codeblocks:
        markdown_content = code_example_tweaks(markdown_content)

    # Normalize indented bullets: collapse 2+ leading spaces before asterisk to a single '* '
    markdown_content = re.sub(r'(?m)^[ ]{2,}\*\s+', '* ', markdown_content)
    # Normalize indented numbered lists: collapse 2+ leading spaces before N. to 'N. '
    markdown_content = re.sub(r'(?m)^[ ]{2,}(\d+)\.\s+', r'\1. ', markdown_content)

    return markdown_content

def add_language_to_code_fence(markdown: str) -> str:
    """
    For each fenced code block in markdown, detect the language and annotate the code fence.
    If a language is already present, do not overwrite. If language cannot be detected, leave as is.
    """
    # Rust structural & secondary keywords (split so we can enforce stronger conditions)
    RUST_STRUCTURAL = {
        'fn', 'pub', 'impl', 'trait', 'struct', 'enum', 'match', 'mod'
    }
    RUST_SECONDARY = {
        'use', 'crate', 'super', 'self', 'Self', 'type', 'where', 'unsafe', 'extern', 'move', 'dyn', 'async', 'await', 'let', 'mut', 'ref'
    }

    # Precompile regexes for performance & accuracy
    rust_structural_re = re.compile(r'\b(' + '|'.join(sorted(RUST_STRUCTURAL)) + r')\b')
    rust_secondary_re = re.compile(r'\b(' + '|'.join(sorted(RUST_SECONDARY)) + r')\b')
    # Heuristic for JSON detection: look for at least 2 key:value pairs with quotes
    json_keyval_re = re.compile(r'"[^"\n]{1,100}"\s*:')

    def looks_like_json(code: str) -> bool:
        snippet = code.strip()
        # Fast rejects
        if not snippet:
            return False
        # Allow for partial objects (common when docs split long JSON across multiple fences)
        opening = snippet.lstrip()[:1]
        if opening not in '{[':
            return False
        # Count quoted key:value patterns
        kv = len(json_keyval_re.findall(snippet))
        if kv >= 2:
            # Reject if we see obvious non-JSON constructs (function defs, angle-bracket generics, etc.)
            if re.search(r'\b(fn|class|def|trait|struct|enum|impl)\b', snippet):
                return False
            # Reject if semicolons appear at end of many lines (code-ish), unless inside JSON (allowed but rare)
            semi_lines = sum(1 for line in snippet.splitlines() if line.rstrip().endswith(';'))
            if semi_lines > 1 and semi_lines > kv:  # more code-like
                return False
            return True
        return False

    def detect_rust(code: str) -> bool:
        # Require at least one structural token AND either a second structural or a secondary token
        structural_hits = rust_structural_re.findall(code)
        if not structural_hits:
            return False
        # Secondary or another structural occurrence
        secondary_hits = rust_secondary_re.findall(code)
        if len(structural_hits) >= 2 or secondary_hits:
            # Avoid false positives on JSON keys containing these substrings by ensuring braces/semicolons typical of Rust
            if re.search(r'\bfn\b|;|->|::', code):
                return True
        return False

    def annotate_code_block(match):
        fence = match.group(1)
        lang = match.group(2)
        code = match.group(3)
        closing_fence = match.group(4)

        # Helper: detect JSON fragment (even if not fully wrapped by { } )
        def looks_like_json_fragment(s: str) -> bool:
            s_stripped = s.strip()
            if not s_stripped:
                return False
            # Full object/array test
            if looks_like_json(s):
                return True
            # Fragment: a sequence of quoted key:value pairs separated by commas and maybe braces missing
            # Count occurrences of "key":
            kv = len(json_keyval_re.findall(s_stripped))
            if kv >= 1 and ':' in s_stripped:
                # Avoid common code indicators
                if re.search(r'\b(fn|class|def|trait|struct|enum|impl)\b', s_stripped):
                    return False
                return True
            return False

        # Allow re-tagging if existing language is a known frequent misclassification for JSON
        MISLABELED_JSON_LANGS = { 'rust', 'scdoc', 'text', 'none', 'bash', 'sh', 'gdscript' }
        def looks_like_shell(cmd: str) -> bool:
            # Quick heuristic for shell command sequences
            indicators = [
                'apt-get', 'curl ', 'cmake', 'python3', 'chmod', 'export ', 'snap install', 'gpg --dearmor',
                'lsb_release', './configure', 'make ', ' ./', 'tar -x', 'bash ']
            if any(tok in cmd for tok in indicators):
                # Exclude if Rust structural tokens present
                if detect_rust(cmd):
                    return False
                return True
            return False

        if lang and lang.strip():
            current_lang = lang.strip().lower()
            # Retag obvious JSON fragments
            if current_lang in MISLABELED_JSON_LANGS and looks_like_json_fragment(code):
                return f"{fence}json\n{code}{closing_fence}"
            # Retag mis-labeled shell commands marked as rust/gdscript/text/none -> bash
            if current_lang in {'rust', 'text', 'none', 'gdscript'} and looks_like_shell(code):
                # Normalize multiple inline export statements into separate lines for readability
                norm_code = re.sub(r'(export [^\n]+?)\s+(export )', r'\1\n\2', code)
                # If still one long line with multiple ' export ' occurrences, split each export onto new line
                if norm_code.count('\n') == 0 and norm_code.lower().count('export ') > 1:
                    parts = [p.strip() for p in norm_code.split('export ') if p.strip()]
                    norm_code = '\n'.join('export ' + p for p in parts)
                return f"{fence}bash\n{norm_code}{closing_fence}"
            # Otherwise keep as-is
            return match.group(0)

        # 1. JSON detection first (takes precedence over other guesses)
        if looks_like_json(code):
            return f"{fence}json\n{code}{closing_fence}"

        # 2. Rust detection with stricter heuristic (avoid substrings like 'as' in 'Pages', 'use' in 'Issues')
        if detect_rust(code):
            return f"{fence}rust\n{code}{closing_fence}"

        # --- Fallback to Pygments language detection ---
        # If not Rust, use Pygments to guess the language
        detected_lang = None
        if guess_lexer is not None:
            try:
                lexer = guess_lexer(code)
                candidate = lexer.aliases[0] if lexer.aliases else lexer.name.lower()
                # Normalize some noisy / overly specific lexers to simpler names used commonly in markdown
                alias_map = {
                    'text': None,  # treat plain text as no detection
                    'json-object': 'json',
                    'json': 'json',
                    'scdoc': None,  # often mis-detected for generic structured text; we'll suppress unless strong signal
                }
                # If candidate is scdoc but code looks like json, override
                if candidate == 'scdoc' and looks_like_json(code):
                    detected_lang = 'json'
                else:
                    detected_lang = alias_map.get(candidate, candidate)
            except Exception:
                pass
        # If a language is detected, annotate the code fence
        if detected_lang:
            return f"{fence}{detected_lang}\n{code}{closing_fence}"
        # If no language is detected, leave the code fence as is
        else:
            return match.group(0)

    # Regex for fenced code blocks: ```[lang]?\n...code...\n```
    pattern = re.compile(r'(```)(\w*)\n([\s\S]*?)(```)', re.MULTILINE)
    return pattern.sub(annotate_code_block, markdown)

def code_example_tweaks(markdown_content: str) -> str:
    """Placeholder for code-example tweaks used by older logic.

    Current behavior: no-op. Kept for backward compatibility so CSV-driven
    regex operations can enable/disable this step without breaking callers.
    """
    return markdown_content

def remove_code_line_numbers(markdown: str) -> str:
    """
    Removes line numbers from code blocks in markdown (e.g., '1: print("hi")' -> 'print("hi")').
    """
    """
    - Detects ``` fences (with or without a language tag)
    - While inside a fence, removes 1–3 digits at the *start* of each line,
      plus one optional following space.
    - Stops when the closing ``` fence is hit.
    """
    out = []
    in_fence = False
    fence_re = re.compile(r'^```')
    # Remove line numbers at start of code lines: optional indent, 1-3 digits, optional : or ., then spaces.
    strip_re = re.compile(r'^(\s*)\d{1,3}(?:[:.])?\s+')

    for idx, line in enumerate(markdown.splitlines()):
        if fence_re.match(line):
            in_fence = not in_fence
            out.append(line)
            continue

        if in_fence:
            new_line = strip_re.sub(r'\1', line)
            out.append(new_line)
        else:
            out.append(line)

    return "\n".join(out)

def fix_no_toplevel_heading(markdown: str) -> str:
    """
    Fix missing headers: when the file starts wih content and there is no header: 
    Add # <insert title string from frontmatter Title.  
    """
    fm_match = re.match(r'(?s)\A---\r?\n(.*?)\r?\n---\r?\n(.*)', markdown)
    if not fm_match:
        # No YAML front matter to source a title from; keep content unchanged.
        return markdown

    fm, body = fm_match.groups()
    title_match = re.search(r'(?mi)^\s*title\s*:\s*(.+)$', fm)
    if not title_match:
        # Front matter exists but Title is missing; avoid injecting a blank H1.
        return markdown

    title = title_match.group(1).strip()
    if re.match(r'\s*#', body):  # next non-blank char is '#'? leave as-is
        return markdown
    return f"---\n{fm}\n---\n\n# {title}\n\n{body}"

def fix_empty_h1(markdown: str) -> str:
    """
    If the H1 header has no content between the title and the first H2 ("## <some string>"), 
    add an intro string with bullets containing all H2 titles in the file. This preserves
    document structure, and provides a way to have a meaningful chunk and page summary. 
    """
    # Find first H1
    h1 = re.search(r'(?m)^#\s+(.*)$', markdown)
    if not h1:
        return markdown

    h1_end = h1.end()
    # Search for first H2 after the H1
    rest = markdown[h1_end:]
    first_h2 = re.search(r'(?m)^##\s+(.*)$', rest)
    if not first_h2:
        return markdown

    # If there's any non-blank text between H1 and that first H2, do nothing
    if rest[: first_h2.start()].strip():
        return markdown

    # Collect all H2 titles in the remainder of the file (they are the children)
    h2_titles = [m.group(1).strip() for m in re.finditer(r'(?m)^##\s+(.*)$', rest)]

    # Build insertion text
    title = h1.group(1).strip()
    bullets = "\n".join(f"- {t}" for t in h2_titles)
    insert = f"\n\nFor information about {title}, see the following:\n\n{bullets}\n\n"

    # Insert right after the H1 line
    return markdown[:h1_end] + insert + markdown[h1_end:]


def ensure_blank_line_after_headings(markdown: str) -> str:
    """
    Ensure there's a blank line after every ATX markdown heading (# .. ######)
    when followed by a paragraph (non-blank, non-heading, non-code-fence line).

    - Skips changes inside fenced code blocks.
    - Does not insert if the next line is already blank, another heading, or a code fence.
    - Preserves original lines and appends a single "\n" as the blank line when needed.
    """
    import re

    lines = markdown.splitlines(keepends=True)
    out = []
    in_code = False
    fence_re = re.compile(r'^```')
    heading_re = re.compile(r'^(#{1,6})\s+.*\S.*$')

    for i, line in enumerate(lines):
        # Toggle fenced code state
        if fence_re.match(line):
            in_code = not in_code
            out.append(line)
            continue

        out.append(line)

        if in_code:
            continue

        if heading_re.match(line):
            # Look ahead
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                # If next is not blank, not another heading, not a fence -> insert blank line
                if (
                    nxt.strip() != ''
                    and not nxt.lstrip().startswith('#')
                    and not fence_re.match(nxt)
                ):
                    out.append('\n')
            else:
                # Heading at EOF: add a trailing newline to ensure separation
                out.append('\n')

    return ''.join(out)



r'''
# currently not used. 
def code_example_tweaks(markdown_content: str) -> str:
    """
    Insert a preceding paragraph as a '# comment:' line at the top of the following fenced code block.
            code = re.sub(r'(^|\n)\s*\d+\s*([:/]{1,2})?\s*', '\1', code)
    - Paragraph must not be a heading (#), code fence (```), blockquote (>), or list marker.
    - Avoid duplicate insertion if the first non-blank line in the code block already starts with '# comment:'.
    """
    def para_to_comment(match):
        para = match.group(1).strip()
        code_fence = match.group(2)
        code_content = match.group(3)
        closing_fence = match.group(4)
        comment = f'# comment: {para}\n'
        if code_content.lstrip().startswith('# comment:'):
            return match.group(0)
        return f'{comment}{code_fence}{code_content}{closing_fence}'

    pattern = re.compile(
        r'(^[^\n#`>][^\n]*?)\n*'      # Paragraph line
        r'(\n```[^\n]*\n)'            # Opening fence
        r'([\s\S]*?)(\n```)',         # Code content + closing fence
        re.MULTILINE
    )
    return pattern.sub(para_to_comment, markdown_content)


    def is_meaningful_code(code: str) -> bool:
        """
        Determines whether a code snippet should be kept separate using line count AND structure OR keyword density.
        For example, if the code length is more than CODE_LENGTH lines and contains a function, chunk it and store separately, OR, 
        if the keyword density is greater than 0.2, chunk it and store separately.

        I am primarily checking for Python and JavaScript code here.
        """
        code = code.strip()
        lines = code.splitlines()

        # --- Strategy 1 + 2: Line Count AND Structural Pattern ---
        structural_patterns = [
            r'^\s*(def|function|func)\s+\w+',         # Python, JS, Go
            r'^\s*class\s+\w+',                       # Python, Java, C++
            r'^\s*(if|for|while|switch|try|match)\b.*[:{]', # Control flow incl. Rust 'match'
            r'\b(public|private|protected|static|pub|impl|trait|struct|enum|mod|use|crate|super)\b', # Java, C#, Rust, etc.
            r'^\s*#[a-zA-Z0-9_]+\s*:',                # YAML or config blocks
            r'^\s*fn\s+\w'
            '+',                          # Rust function
            r'^\s*let\s+(mut\s+)?\w+',                # Rust variable declaration
            r'\{[^}]*\}',                             # Block enclosed in {}
            r';\s*$',                                 # Ends in ;
        ]

        matches_structure = any(re.search(pat, code, re.MULTILINE) for pat in structural_patterns)

        if len(lines) >= CODE_LENGTH and matches_structure:
            return True

        # --- Strategy 3: Keyword Density (OR condition) ---
        keywords = [
            # Common keywords
            'if', 'for', 'while', 'return', 'def', 'function', 'class', 'var', 'const', 'let', '=', '{', '}', ';',
            'else', 'elif', 'for', 'while', 'try', 'except', 'with', 'import', 'from', 'lambda', 'yield', 'break', 'continue', 'pass',
            # Rust keywords
            'fn', 'mut', 'pub', 'impl', 'trait', 'struct', 'enum', 'match', 'mod', 'use', 'crate', 'super', 'Self', 'self',
            'as', 'ref', 'type', 'where', 'unsafe', 'extern', 'move', 'dyn', 'async', 'await'
        ]
        words = code.split()
        keyword_hits = sum(any(kw in word for kw in keywords) for word in words)

        density = keyword_hits / max(len(words), 1)
        if density > KEYWORD_DENSITY:
            return True

        return False
'''

