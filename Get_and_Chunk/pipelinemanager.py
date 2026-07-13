#!/usr/bin/env python3
"""
Pipeline Manager - Orchestrates the complete RAG pipeline

Executes the numbered pipeline scripts in order:
1. Crawler (selected based on PARSER field in run_settings metadata)
2. Markdown cleanup
3. Chunking
4. Summarization (via the HF endpoint wrapper when HF_TOKEN is set)
5. Embedding
6. Vector database upload

The crawler selection is based on the PARSER field in run_settings.py metadata:
- crawlweb -> Get_and_Chunk/1crawlweb.py
- crawlpdf -> Get_and_Chunk/1crawlpdf.py
- crawlgit -> Get_and_Chunk/1crawlgit.py

Child output is streamed line-by-line to the logger (no buffering until exit),
and each step's timeout is configurable via STEP_TIMEOUTS.
"""

import subprocess
import sys
import os
from pathlib import Path
from common.run_context import get_run_context
from Logger.custom_logger import setup_global_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "run_python_entry.py"

# Get run configuration
ctx = get_run_context()
metadata = ctx['metadata']
CWD = ctx['cwd']

# Set up global logger
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message", "Script", "Exit Code"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

# Mapping from PARSER column to crawler script
CRAWLER_MAPPING = {
    'crawlweb': 'Get_and_Chunk/1crawlweb.py',
    'crawlpdf': 'Get_and_Chunk/1crawlpdf.py',
    'crawlgit': 'Get_and_Chunk/1crawlgit.py'
}

# Summaries need an LLM backend. When HF_TOKEN is present, run through the
# wrapper that resumes/pauses the Hugging Face inference endpoint; otherwise
# run the summary script directly (openai/ollama backends).
SUMMARY_SCRIPT = (
    'Get_and_Chunk/4.00summary_wrapper_hf.py'
    if os.getenv('HF_TOKEN')
    else 'Get_and_Chunk/4.01summary.py'
)

# Optional enrichment: derive code-friendly names and attach them to chunks.
# Off by default because it requires the codename CSV to be current.
RUN_FRIENDLY_NAME_STEPS = False
FRIENDLY_NAME_STEPS = [
    'Get_and_Chunk/3.01codename_to_friendlyname.py',
    'Get_and_Chunk/3.02add_codefriendly_names2chunk.py',
]

# Pipeline steps (after crawler selection)
PIPELINE_STEPS = [
    'Get_and_Chunk/2markdown_cleanup.py',
    'Get_and_Chunk/3.00chunker.py',
    *(FRIENDLY_NAME_STEPS if RUN_FRIENDLY_NAME_STEPS else []),
    SUMMARY_SCRIPT,
    'VectorDB/embedding.py',
    'VectorDB/upsert_to_vectordb.py'
]

# Per-script timeout in seconds. None = no timeout. Crawls and summarization
# of large corpora routinely exceed the old blanket 1-hour cap.
DEFAULT_STEP_TIMEOUT = None
STEP_TIMEOUTS = {
    # 'Get_and_Chunk/1crawlweb.py': 4 * 3600,
}


def run_script(script_name: str, step_number: int = None) -> int:
    """
    Run a Python script, streaming its output to the logger, and return its exit code.

    Args:
        script_name: Name of the Python script to run
        step_number: Optional step number for logging context

    Returns:
        Exit code from the script (0 = success, non-zero = error)
    """
    step_info = f" (Step {step_number})" if step_number else ""
    logger.info(f"Starting {script_name}{step_info}", extra={"Script": script_name, "Exit Code": ""})
    timeout = STEP_TIMEOUTS.get(script_name, DEFAULT_STEP_TIMEOUT)

    try:
        process = subprocess.Popen(
            [sys.executable, str(RUNNER_SCRIPT), script_name],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # interleave so progress reads in order
            text=True,
            encoding='utf-8',
            errors='replace',
        )

        # Stream child output live instead of buffering until exit.
        for line in process.stdout:
            line = line.rstrip()
            if line:
                logger.info(f"[{script_name}] {line}", extra={"Script": script_name, "Exit Code": ""})

        exit_code = process.wait(timeout=timeout)

        if exit_code == 0:
            logger.info(f"Completed {script_name}{step_info} successfully",
                       extra={"Script": script_name, "Exit Code": exit_code})
        else:
            logger.error(f"Failed {script_name}{step_info} with exit code {exit_code}",
                        extra={"Script": script_name, "Exit Code": exit_code})

        return exit_code

    except subprocess.TimeoutExpired:
        process.kill()
        logger.error(f"Timeout ({timeout}s) running {script_name}{step_info}",
                    extra={"Script": script_name, "Exit Code": "TIMEOUT"})
        return -1

    except Exception as e:
        logger.error(f"Exception running {script_name}{step_info}: {e}",
                    extra={"Script": script_name, "Exit Code": "EXCEPTION"})
        return -1


def run_pipeline() -> bool:
    """
    Execute the complete RAG pipeline.

    Returns:
        True if all steps completed successfully, False if any step failed
    """
    # Get parser type from run metadata
    parser_type = metadata.get('PARSER', '').strip().lower()
    crawl_id = metadata.get('ID', 'unknown')

    logger.info(f"Starting RAG pipeline for crawl ID {crawl_id} with parser type '{parser_type}'")

    # Step 1: Select and run appropriate crawler
    if parser_type not in CRAWLER_MAPPING:
        logger.error(f"Unknown parser type '{parser_type}'. Valid types: {list(CRAWLER_MAPPING.keys())}")
        return False

    crawler_script = CRAWLER_MAPPING[parser_type]
    logger.info(f"Selected crawler: {crawler_script} for parser type '{parser_type}'")

    exit_code = run_script(crawler_script, step_number=1)
    if exit_code != 0:
        logger.error(f"Pipeline failed at Step 1 ({crawler_script})")
        return False

    # Steps 2+: Run remaining pipeline steps
    for i, script in enumerate(PIPELINE_STEPS, start=2):
        exit_code = run_script(script, step_number=i)
        if exit_code != 0:
            logger.error(f"Pipeline failed at Step {i} ({script})")
            return False

    logger.info("RAG pipeline completed successfully!")
    return True


def main():
    """Main entry point for pipeline execution."""
    logger.info(f"Pipeline Manager starting for crawl ID {metadata.get('ID', 'unknown')}")
    logger.info(f"Working directory: {CWD}")
    logger.info(f"Target URL: {metadata.get('CRAWL_URL', 'unknown')}")

    success = run_pipeline()

    if success:
        logger.info("Pipeline execution completed successfully")
        sys.exit(0)
    else:
        logger.error("Pipeline execution failed")
        sys.exit(1)

if __name__ == '__main__':
    main()
