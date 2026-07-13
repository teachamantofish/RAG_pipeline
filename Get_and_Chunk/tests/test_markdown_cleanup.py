import importlib

import pytest

import common.markdown_utils as markdown_utils


@pytest.fixture(autouse=True)
def _reload_markdown_utils():
    importlib.reload(markdown_utils)
    yield


def test_remove_content_before_h1_preserves_front_matter():
    text = (
        "---\n"
        "title: Example\n"
        "---\n"
        "intro text before heading\n"
        "# Heading\n"
        "Body content\n"
    )

    cleaned = markdown_utils.remove_content_before_h1(text)

    assert cleaned.startswith("---\ntitle: Example\n---\n# Heading")
    assert "intro text" not in cleaned


def test_remove_content_under_heading_below_threshold_drops_short_blocks(monkeypatch):
    text = (
        "# Top\n"
        "Short note\n\n"
        "## Child\n"
        "This content is definitely long enough to keep because it exceeds the threshold significantly.\n"
    )

    cleaned = markdown_utils.remove_content_under_heading_below_chunk_min_threshold(text)

    assert cleaned.startswith("# Top\n## Child")
    assert "Short note" not in cleaned
    assert "long enough" in cleaned


def test_delete_specified_heading_content_removes_known_sections():
    text = (
        "#### On this page\n"
        "- item\n"
        "##### Search\n"
        "content\n"
        "# Heading\n"
        "Body\n"
    )

    cleaned = markdown_utils.delete_specified_heading_content(text)

    assert "On this page" not in cleaned
    assert "Search" not in cleaned
    assert cleaned.strip() == "# Heading\nBody"


def test_normalize_headings_strips_numbers_and_ignores_code_fences():
    text = (
        "## 3.2 Example Heading\n"
        "```python\n"
        "# not a heading\n"
        "```\n"
        "### 12 Another\n"
    )

    cleaned = markdown_utils.normalize_headings(text)

    assert "## Example Heading" in cleaned
    assert "### Another" in cleaned
    assert "# not a heading" in cleaned


def test_add_language_to_code_fence_detects_json_and_respects_existing():
    text = (
        "```\n"
        "{\n  \"key\": \"value\",\n  \"another\": 42\n}\n"
        "```\n"
        "```python\nprint('hi')\n```\n"
    )

    cleaned = markdown_utils.add_language_to_code_fence(text)

    assert cleaned.splitlines()[0] == "```json"
    assert "```python" in cleaned


def test_remove_code_line_numbers_strips_leading_digits():
    # Line numbers are stripped only inside code fences; prose is untouched.
    text = (
        "1 leading prose stays\n"
        "```\n"
        "1 print('hi')\n"
        "2: return value\n"
        "```\n"
        "foo"
    )

    cleaned = markdown_utils.remove_code_line_numbers(text)

    lines = cleaned.splitlines()
    assert lines[0] == "1 leading prose stays"
    assert lines[2] == "print('hi')"
    assert lines[3] == "return value"
    assert lines[5] == "foo"


def test_fix_empty_h1_inserts_outline():
    text = "# Title\n\n## First\nContent\n## Second\n"

    cleaned = markdown_utils.fix_empty_h1(text)

    assert "For information about Title" in cleaned
    assert "- First" in cleaned and "- Second" in cleaned


def test_fix_no_toplevel_heading_injects_title():
    text = (
        "---\n"
        "title: Injected Title\n"
        "---\n"
        "Leading content without heading\n"
    )

    cleaned = markdown_utils.fix_no_toplevel_heading(text)

    assert "# Injected Title" in cleaned
    assert cleaned.count("# Injected Title") == 1
    assert cleaned.endswith("Leading content without heading\n")


def test_custom_regex_applies_csv_rules():
    text = "Foo &amp; Bar\r\nCopy\r\n  * item\r\n"

    cleaned = markdown_utils.custom_regex(text)

    assert cleaned == "Foo & Bar\n* item\n"


def test_clean_markdown_file_inplace_runs_full_pipeline(markdown_cleanup_module):
    module = markdown_cleanup_module
    source = (
        "---\n"
        "title: Title\n"
        "---\n"
        "preamble\n"
        "# Title\n"
        "#### On this page\n"
        "- link\n"
        "## Section 1\n"
        "Short\n"
        "## Code Sample\n"
        "```\n"
        "1 print('numbered')\n"
        "2 return 42\n"
        "```\n"
        "```\n"
        "{\"a\": 1, \"b\": 2}\n"
        "```\n"
    )

    target = module.CWD / "doc.md"
    target.write_text(source, encoding="utf-8")

    module.clean_markdown_file_inplace(target)

    result = target.read_text(encoding="utf-8")

    assert "preamble" not in result
    assert "On this page" not in result
    assert "Short" not in result
    assert "For information about Title" in result
    assert "- Section 1" in result and "- Code Sample" in result
    assert "```json" in result
    assert "print('numbered')" in result and "1 print" not in result
    assert "## Section 1\n## Code Sample" in result
