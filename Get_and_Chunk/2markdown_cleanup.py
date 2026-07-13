import os
import sys
from pathlib import Path
from common.markdown_utils import *
from common.adobe_connect_cleanup import (
	add_blank_lines_around_bold_headings,
	add_blank_lines_around_markdown_headings,
	fix_see_also,
	dedupe_standalone_url_lines,
	normalize_caution_admonitions,
	normalize_ordered_list_spacing,
	normalize_unordered_lists,
	repair_broken_table_rows,
	remove_prose_duplicate_before_code_fence,
	remove_wrapped_table_rows,
	url_formatting_fixer,
)
from common.run_context import get_run_context
from Logger.custom_logger import setup_global_logger
from config.cleanconfig import (
	ENABLE_STRUCTURE_FIXES,
	ENABLE_HEADING_NORMALIZATION,
	ENABLE_LINK_FIXES,
	ENABLE_CODE_FENCE_FIXES,
	ENABLE_TABLE_REPAIR,
	ENABLE_LIST_NORMALIZATION,
	ENABLE_CUSTOM_REGEX,
)

ctx = get_run_context()
CWD: Path = ctx['cwd']

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

def clean_markdown_file_inplace(md_file):
	with open(md_file, 'r', encoding='utf-8') as f:
		markdown = f.read()
	cleaned = markdown
	if ENABLE_STRUCTURE_FIXES:
		cleaned = remove_content_before_h1(cleaned)
		cleaned = delete_specified_heading_content(cleaned)
		cleaned = remove_content_under_heading_below_chunk_min_threshold(cleaned)
		cleaned = fix_empty_h1(cleaned)
		cleaned = fix_no_toplevel_heading(cleaned)
	if ENABLE_HEADING_NORMALIZATION:
		cleaned = normalize_headings(cleaned)
		cleaned = add_blank_lines_around_markdown_headings(cleaned)
		cleaned = add_blank_lines_around_bold_headings(cleaned)
	if ENABLE_LINK_FIXES:
		cleaned = fix_see_also(cleaned)
		cleaned = url_formatting_fixer(cleaned)
		cleaned = dedupe_standalone_url_lines(cleaned)
	if ENABLE_CODE_FENCE_FIXES:
		cleaned = add_language_to_code_fence(cleaned)
		cleaned = remove_prose_duplicate_before_code_fence(cleaned)
	if ENABLE_TABLE_REPAIR:
		cleaned = remove_wrapped_table_rows(cleaned)
		cleaned = repair_broken_table_rows(cleaned)
	if ENABLE_LIST_NORMALIZATION:
		cleaned = normalize_unordered_lists(cleaned)
		cleaned = normalize_ordered_list_spacing(cleaned)
		cleaned = normalize_caution_admonitions(cleaned)
	if ENABLE_CODE_FENCE_FIXES:
		cleaned = remove_code_line_numbers(cleaned)
	if ENABLE_CUSTOM_REGEX:
		# Apply generic regex cleanup (CSV-driven) last so earlier structural removals don't interfere
		cleaned = custom_regex(cleaned)
	# Ensure LF newlines are written to disk so CSV-driven replacements (e.g., CRLF->LF) persist.
	with open(md_file, 'w', encoding='utf-8', newline='\n') as f:
		f.write(cleaned)

if __name__ == "__main__":
	# Use CWD resolved from run_settings.py at module import time
	CWD.mkdir(parents=True, exist_ok=True)
	# Find markdown files recursively (include .md, .markdown, .mdx)
	md_files = []
	for pat in ("*.md", "*.markdown", "*.mdx"):
		md_files.extend(CWD.rglob(pat))
	# Deduplicate & sort for stable processing order
	md_files = sorted(set(md_files))
	if not md_files:
		print(f"No markdown files found in {CWD}")
		sys.exit(0)
	print(f"Cleaning {len(md_files)} markdown files in {CWD} (overwriting)...")
	for md_file in md_files:
		clean_markdown_file_inplace(md_file)
		# Show path relative to the crawled output dir (parent crawled path + filename)
		try:
			display_path = md_file.relative_to(CWD)
		except Exception:
			display_path = md_file.name
		print(f"Cleaned: {display_path}")
		logger.info(f"{display_path}: cleaned")
