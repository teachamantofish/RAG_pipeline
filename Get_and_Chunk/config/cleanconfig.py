# Markdown cleanup configuration.
# Each flag enables a group of cleanup passes in 2markdown_cleanup.py.
# (A previous version of this file held settings that no script read.)

ENABLE_STRUCTURE_FIXES = True           # Remove content before H1, drop configured headings, prune tiny sections, fix empty/missing top-level headings.
ENABLE_HEADING_NORMALIZATION = True     # Strip numbering from headings; ensure blank lines around markdown and bold headings.
ENABLE_LINK_FIXES = True                # Fix "See also" blocks, URL formatting, and duplicated standalone URL lines.
ENABLE_CODE_FENCE_FIXES = True          # Add language IDs to code fences, drop prose duplicated before fences, strip code line numbers.
ENABLE_TABLE_REPAIR = True              # Remove wrapped table rows and repair broken table rows.
ENABLE_LIST_NORMALIZATION = True        # Normalize unordered/ordered list spacing and caution admonitions.
ENABLE_CUSTOM_REGEX = True              # Apply CSV-driven regex replacements (common/regex_replacements.csv) as the final pass.
