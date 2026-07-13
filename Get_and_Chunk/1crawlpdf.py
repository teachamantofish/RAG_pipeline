# PDF pipeline: trim PDF > create toc.json + parse PDF2MD via docling + add metadata (same script) 
import os
from pathlib import Path
import fitz, json
from common.metadata_utils import merge_page_metadata
from common.run_context import get_run_context
from Logger.custom_logger import setup_global_logger
from common.pdftools.docling_pdf2md import convert_in_two_jobs              # your docling code (returns MD path)
from common.pdftools.pdftoc_fixer import rebuild_md_headings_from_toc    # your JSON-driven heading fixer
from common.codeexample_fixer import process_markdown                     # your code fence fixer
from config.crawlpdfconfig import *  # import all config toggles and constants

ctx = get_run_context()
metadata = ctx['metadata']
CRAWL_URL = metadata['CRAWL_URL']
CWD: Path = ctx['cwd']

# derive paths directly from CSV: CRAWL_URL / BASE_DIR, PDF name = BASE_DIR.pdf
# For the PDF pipeline CRAWL_URL must be a LOCAL DIRECTORY containing
# <BASE_DIR>/<BASE_DIR>.pdf — not a web URL. Fail fast with a clear message,
# because a URL here would silently produce a nonsense path.
if str(CRAWL_URL).lower().startswith(("http://", "https://")):
    raise ValueError(
        f"PARSER=crawlpdf expects CRAWL_URL to be a local directory path, got a URL: {CRAWL_URL}. "
        "Set CRAWL_URL in run_settings.py to the folder that contains "
        f"{metadata.get('BASE_DIR', '<BASE_DIR>')}/{metadata.get('BASE_DIR', '<BASE_DIR>')}.pdf"
    )
base_dir = metadata['BASE_DIR']
JOB_CWD = Path(CRAWL_URL) / base_dir
SRC_PDF     = JOB_CWD / f"{base_dir}.pdf"        # PDF file (trimmed in-place)
TOC_JSON    = JOB_CWD / "toc.json"               # logical TOC
MD_FILE     = JOB_CWD / f"{base_dir}.md"         # markdown file (processed in-place)

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

def trim_pdf_and_write_toc_json(
    src_pdf: Path,
    toc_json: Path,
    remove_front: int,
    remove_back: int,
    remove_ranges,
    toc_range
):
    doc = fitz.open(src_pdf)
    n = doc.page_count

    # write full logical TOC (bookmarks) to JSON, no page filtering
    toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    data = [{"level": int(l), "title": t or "", "page": int(p)} for (l, t, p) in toc]
    toc_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # build keep pages
    start = remove_front
    end   = max(remove_front, n - remove_back)  # non-inclusive
    drops = list(remove_ranges)
    if toc_range:
        drops.append(toc_range)

    def drop(p1):
        return any(lo <= p1 <= hi for (lo, hi) in drops)

    keep = [p for p in range(start, end) if not drop(p + 1)]

    out = fitz.open()
    for p in keep:
        out.insert_pdf(doc, from_page=p, to_page=p)
    
    # Close the original document first before saving to the same path
    doc.close()
    
    # Now save the trimmed PDF to the same file
    out.save(src_pdf, garbage=4, deflate=True, clean=True)
    out.close()

    print(f"[trim] input={n} -> keep={len(keep)}  wrote: {src_pdf}")
    print(f"[toc ] entries={len(data)}         wrote: {toc_json}")

# 3) glue it all together (pure Python)
def run_pipeline():
    # A) trim & toc.json
    if RUN_PDF_TRIM:
        trim_pdf_and_write_toc_json(
            src_pdf=SRC_PDF,
            toc_json=TOC_JSON,
            remove_front=REMOVE_FRONT,
            remove_back=REMOVE_BACK,
            remove_ranges=REMOVE_RANGES,
            toc_range=TOC_RANGE
        )
        print(f"[trim] completed")
    else:
        print(f"[trim] SKIPPED")

    # B) docling on reduced PDF -> returns path to merged .md
    if RUN_DOCLING:
        md_path = Path(convert_in_two_jobs(str(SRC_PDF), str(JOB_CWD), csvrow_data=metadata))
        print(f"[docling] wrote: {md_path}")
    else:
        # If skipping docling, assume the MD file exists from previous run
        md_path = MD_FILE
        print(f"[docling] SKIPPED - using existing: {md_path}")

    # C) fix headings using saved toc.json (overwrite same file)
    if RUN_TOC_FIXER:
        rebuild_md_headings_from_toc(
            toc_json_path=TOC_JSON,
            md_in_path=md_path,
            md_out_path=md_path,  # Same file for input and output
            fuzzy=True,
            cutoff=0.92
        )
        print(f"[fix ] wrote: {md_path}")
    else:
        print(f"[fix ] SKIPPED")

    if RUN_CODE_FIXER:
        # Process all .md files in the job directory to format code blocks
        from common.codeexample_fixer import process_directory
        process_directory(str(JOB_CWD))
        print(f"[code] Completed code block formatting")
    else:
        print(f"[code] SKIPPED")

if __name__ == "__main__":
    run_pipeline()
