# This crawler code reads the crawlconfig.py file and fetches webpages which meet the requirements.
# Run selection and metadata now come from run_settings.py via common.run_context,
# while crawl behavior is still configured in crawlconfig.py.
import os  # For directory and file operations
import re
import sys
import gzip
import html as html_lib
from pathlib import Path
import logging  # For logging
import traceback  # For detailed error information
import asyncio  # For async crawling
from urllib.parse import urlparse, urldefrag, urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from langdetect import detect, LangDetectException
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai import BrowserConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter

# Import static config values
from config.crawlconfig import *
# Shared metadata utilities (centralized CSV loading + merge rules)
from common.metadata_utils import merge_page_metadata
from common.run_context import get_run_context
from Logger.custom_logger import setup_global_logger

ctx = get_run_context()
metadata = ctx['metadata']
CWD: Path = ctx['cwd']
CRAWL_URL = metadata['CRAWL_URL']

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)


def _configure_stdio_for_windows():
    """Avoid UnicodeEncodeError on Windows cp1252 consoles (rich/crawl4ai logs)."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                # If stream reconfiguration is unavailable, continue with existing encoding.
                pass


_configure_stdio_for_windows()

# --- Save Markdown ---
def _extract_h1_title_from_html(html_text: str) -> str | None:
    """Extract page title from HTML H1, preferring h1.page-title when present."""
    if not html_text:
        return None

    # Prefer explicit page-title class used by Adobe HelpX pages.
    m = re.search(
        r'<h1\b[^>]*class=["\"][^"\"]*\bpage-title\b[^"\"]*["\"][^>]*>(.*?)</h1>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        # Fallback to first h1 if page-title class is absent.
        m = re.search(r'<h1\b[^>]*>(.*?)</h1>', html_text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None

    text = re.sub(r'<[^>]+>', ' ', m.group(1))
    text = html_lib.unescape(text)
    text = ' '.join(text.split()).strip()
    return text or None


def _ensure_top_level_h1(markdown_body: str, title: str | None) -> str:
    """Ensure markdown begins with '# <title>' using the resolved page title."""
    if not title:
        return markdown_body

    normalized_title = ' '.join(title.split()).strip()
    if not normalized_title:
        return markdown_body

    lines = markdown_body.splitlines()
    first_content_idx = None
    for i, line in enumerate(lines):
        if line.strip():
            first_content_idx = i
            break

    heading_line = f"# {normalized_title}"
    if first_content_idx is not None and re.match(r"^#\s+", lines[first_content_idx]):
        lines[first_content_idx] = heading_line
        updated = "\n".join(lines)
        if markdown_body.endswith("\n"):
            updated += "\n"
        return updated

    body = markdown_body.lstrip("\n")
    if body:
        return f"{heading_line}\n\n{body}"
    return f"{heading_line}\n"


# Tracks files written during this run so URLs whose last path segment collides
# (e.g. /guide-a/install and /guide-b/install) don't silently overwrite each other.
_SAVED_FILE_URLS: dict = {}


def save_markdown(markdown_content: str, save_dir: str, url: str, page_title: str | None = None):
    """Save markdown applying standardized front matter merge.

    We generate a filename from the URL's last path segment and then prepend
    front matter built from global CSV metadata plus any original page-level
    YAML (if present) merged via merge_page_metadata.
    """
    clean_url = urldefrag(url)[0]
    parsed = urlparse(clean_url)
    last_segment = parsed.path.rstrip('/').split('/')[-1] or 'index'
    stem = os.path.splitext(last_segment)[0] or 'index'
    filename = f"{stem}.md"
    filepath = os.path.join(str(save_dir), filename)

    # Collision guard: if another URL already wrote this filename during this
    # run, disambiguate with a short hash of the full URL instead of overwriting.
    existing_url = _SAVED_FILE_URLS.get(filepath)
    if existing_url and existing_url != clean_url:
        import hashlib
        suffix = hashlib.sha1(clean_url.encode('utf-8')).hexdigest()[:8]
        filename = f"{stem}-{suffix}.md"
        filepath = os.path.join(str(save_dir), filename)
        logger.warning(
            f"Filename collision: '{stem}.md' already written for {existing_url}; "
            f"saving {clean_url} as {filename}"
        )
    _SAVED_FILE_URLS[filepath] = clean_url

    # Per-page Source URL: override CRAWL_URL so front matter points to the page actually crawled.
    page_meta = dict(metadata)
    page_meta['CRAWL_URL'] = clean_url
    if page_title:
        page_meta['METADATA_TITLE'] = page_title
    merged_front, cleaned_body = merge_page_metadata(page_meta, markdown_content)
    cleaned_body = _ensure_top_level_h1(cleaned_body, page_title or page_meta.get('METADATA_TITLE'))
    full_content = merged_front + cleaned_body

    with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(full_content)

def _build_browser_config() -> BrowserConfig:
    return BrowserConfig(headless=True, ignore_https_errors=True, verbose=False)


def _build_run_config(deep: bool) -> CrawlerRunConfig:
    """Build the CrawlerRunConfig from crawlconfig.py settings.

    Args:
        deep: attach the BFS deep-crawl strategy (start-URL mode). When False
              (URL-list / sitemap modes) each URL is fetched at depth 0.
    """
    deep_crawl_strategy = None
    if deep:
        # URLPatternFilter - ensure we have a list of patterns
        patterns = URL_PATTERN_FILTERS if isinstance(URL_PATTERN_FILTERS, list) else [URL_PATTERN_FILTERS]
        logger.info(f"URL Pattern Filters: {patterns}")
        deep_crawl_strategy = BFSDeepCrawlStrategy(
            max_depth=MAX_CRAWL_DEPTH,
            include_external=INCLUDE_EXTERNAL_DOMAIN,
            max_pages=MAX_URLS,
            filter_chain=FilterChain([URLPatternFilter(patterns=patterns)]),
        )

    md_options = {
        "ignore_links": IGNORE_LINKS,
        "ignore_images": IGNORE_IMAGES,
        "escape_html": ESCAPE_HTML,
        "body_width": BODY_WIDTH,
        "skip_internal_links": SKIP_INTERNAL_LINKS,
        "include_sup_sub": INCLUDE_SUP_SUB,
        "heading_style": HEADING_STYLE,
        "list_style": LIST_STYLE,
        "preserve_tables": PRESERVE_TABLES,
        "collapse_whitespace": COLLAPSE_WHITESPACE
    }
    md_generator = DefaultMarkdownGenerator(options=md_options)
    return CrawlerRunConfig(
        markdown_generator=md_generator,
        remove_forms=REMOVE_FORMS,
        remove_overlay_elements=REMOVE_OVERLAY_ELEMENTS,
        excluded_tags=EXCLUDED_TAGS,
        excluded_selector=EXCLUDED_SELECTOR,
        exclude_external_links=EXCLUDE_EXTERNAL_LINKS,
        exclude_social_media_links=EXCLUDE_SOCIAL_MEDIA_LINKS,
        exclude_domains=EXCLUDE_DOMAINS,
        exclude_social_media_domains=EXCLUDE_SOCIAL_MEDIA_DOMAINS,
        deep_crawl_strategy=deep_crawl_strategy,
        #cache_mode=CacheMode.BYPASS,
        css_selector=CSS_SELECTOR,
    )


def _process_result(result, allowed_domains: set | None) -> bool:
    """Validate and save a single crawl result. Returns True when a page was saved."""
    url = getattr(result, 'url', None)
    result_domain = urlparse(url).netloc if url else None
    depth = result.metadata.get('depth', 0) if hasattr(result, 'metadata') else 0

    if url and allowed_domains is not None and result_domain not in allowed_domains:
        logger.info(f"Skipping external domain: {url} (domain: {result_domain} not in {sorted(allowed_domains)})")
        return False

    if result.success:
        md_for_counts = getattr(result, 'markdown', '') or ''
        num_chars = len(md_for_counts)
        num_lines_for_log = md_for_counts.count('\n') + 1 if md_for_counts else 0
        status_field = 'success'
    else:
        num_chars = 0
        num_lines_for_log = 0
        error_msg = getattr(result, 'error_message', 'Unknown error')
        status_field = f"error:{error_msg.replace('|',' ')}"

    logger.info(f"url:{url}|depth:{depth}|{status_field}|chars:{num_chars}|lines:{num_lines_for_log}")

    if not result.success:
        return False

    markdown_content = result.markdown
    num_chars = len(markdown_content)
    num_lines = markdown_content.count('\n') + 1 if markdown_content else 0
    if num_chars < MIN_CONTENT_LENGTH or num_lines < MIN_CONTENT_LINES:
        logger.info(f"Skipping {url}: content too short (chars: {num_chars}, lines: {num_lines})")
        logger.info(f"SKIP_SHORT,{url}")
        return False

    if LANGUAGE and LANGUAGE.strip():
        try:
            sample_text = markdown_content[:1000] if len(markdown_content) > 1000 else markdown_content
            detected_language = detect(sample_text)
            if detected_language != LANGUAGE.strip():
                logger.info(f"SKIP_LANGUAGE,{url}")
                logger.info(f"Skipping {url}: Detected language '{detected_language}' != required '{LANGUAGE}'")
                return False
        except LangDetectException:
            logger.info(f"Skipping {url}: Language could not be detected, skipping to be safe.")
            return False

    page_html = (
        getattr(result, 'html', None)
        or getattr(result, 'cleaned_html', None)
        or getattr(result, 'raw_html', None)
        or ''
    )
    page_title = _extract_h1_title_from_html(page_html)
    save_markdown(markdown_content, CWD, url, page_title=page_title)
    logger.info(f"Successfully saved: {url}")
    return True


# Deep crawl using Crawl4AI's built-in BFSDeepCrawlStrategy.
# All configuration values are read directly from crawlconfig.py for consistency.
def deep_crawl_urls():
    """
    Execute deep crawling using Crawl4AI's BFS strategy with all settings from config.
    Returns the number of saved pages.
    """
    async def crawl():
        logger.info("Initializing Crawl4AI deep crawler with BFS strategy...")
        run_config = _build_run_config(deep=True)
        allowed_domains = {urlparse(CRAWL_URL).netloc}
        saved_count = 0

        logger.info(f"Starting deep crawl from: {CRAWL_URL}")
        async with AsyncWebCrawler(config=_build_browser_config()) as crawler:
            results = await crawler.arun(CRAWL_URL, config=run_config)
            logger.info(f"Deep crawl completed. Processing {len(results)} results...")
            for result in results:
                if _process_result(result, allowed_domains):
                    saved_count += 1

        logger.info(f"Deep crawl processing completed. Saved pages: {saved_count}")
        return saved_count

    return asyncio.run(crawl())


def crawl_url_list(urls: list) -> tuple:
    """Crawl an explicit list of URLs (depth 0) inside a single browser session.

    Returns (saved_count, failed_count). Using one AsyncWebCrawler for the whole
    list avoids relaunching a browser per URL, and depth 0 stops a "URL list"
    run from fanning out into a deep crawl.
    """
    async def crawl():
        run_config = _build_run_config(deep=False)
        saved_count = 0
        failed_count = 0

        logger.info(f"Starting URL-list crawl of {len(urls)} URLs (single browser session, depth 0)")
        async with AsyncWebCrawler(config=_build_browser_config()) as crawler:
            results = await crawler.arun_many(urls, config=run_config)
            for result in results:
                try:
                    if _process_result(result, allowed_domains=None):
                        saved_count += 1
                    elif not getattr(result, 'success', False):
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Error processing {getattr(result, 'url', '?')}: {e}")

        logger.info(f"URL-list crawl completed. Saved: {saved_count}, failed: {failed_count}")
        return saved_count, failed_count

    return asyncio.run(crawl())


def _resolve_url_list_path(url_list_file: str):
    """Resolve URL list location across common roots.

    Accept absolute paths and try relative paths under:
    1) current run output dir (CWD)
    2) this script directory
    """
    candidate = Path(url_list_file)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    if not candidate.is_absolute():
        for base in (CWD, Path(__file__).resolve().parent):
            resolved = (base / candidate).resolve()
            if resolved.exists():
                return resolved

    return None


def _validate_crawl_url(url: str):
    """Validate URL format before invoking crawl4ai for clearer errors."""
    allowed_prefixes = ("http://", "https://", "file://", "raw:")
    if not isinstance(url, str) or not url.startswith(allowed_prefixes):
        err_msg = (
            "URL must start with 'http://', 'https://', 'file://', or 'raw:'. "
            "Verify the URL in run_settings.py"
        )
        logger.error(err_msg)
        raise ValueError(err_msg)


def _fetch_url_bytes(url: str, timeout: int = 20):
    """Fetch URL bytes; returns None on fetch failures for candidate probing."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Z_Master_Rag/1.0)"})
        with urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception as e:
        logger.info(f"Sitemap probe skipped: {url} ({str(e)})")
        return None


def _parse_sitemap_xml(xml_text: str, source_url: str):
    """Parse sitemap XML and return (kind, locs) where kind is urlset/sitemapindex."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.info(f"Invalid sitemap XML at {source_url}: {str(e)}")
        return None, []

    tag = root.tag.split("}", 1)[-1].lower() if root.tag else ""
    locs = [
        (elem.text or "").strip()
        for elem in root.findall(".//{*}loc")
        if (elem.text or "").strip()
    ]

    if tag in ("urlset", "sitemapindex"):
        return tag, locs

    return None, locs


def _discover_urls_from_sitemap(seed_url: str):
    """Discover URLs under seed_url prefix from robots.txt + sitemap files."""
    parsed = urlparse(seed_url)
    site_root = f"{parsed.scheme}://{parsed.netloc}"
    seed_prefix = seed_url if seed_url.endswith("/") else seed_url + "/"

    default_candidates = [
        urljoin(site_root + "/", "sitemap.xml"),
        urljoin(site_root + "/", "sitemap_index.xml"),
        urljoin(seed_prefix, "sitemap.xml"),
    ]

    configured_candidates = []
    for candidate in globals().get("SITEMAP_CANDIDATES", []):
        candidate = str(candidate).strip()
        if not candidate:
            continue
        if candidate.startswith(("http://", "https://")):
            configured_candidates.append(candidate)
        else:
            configured_candidates.append(urljoin(site_root + "/", candidate.lstrip("/")))

    robots_sitemaps = []
    robots_url = urljoin(site_root + "/", "robots.txt")
    robots_bytes = _fetch_url_bytes(robots_url)
    if robots_bytes:
        robots_text = robots_bytes.decode("utf-8", "replace")
        for line in robots_text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                if sitemap_url:
                    robots_sitemaps.append(sitemap_url)

    pending = []
    for item in default_candidates + configured_candidates + robots_sitemaps:
        if item not in pending:
            pending.append(item)

    seen_sitemaps = set()
    discovered = []
    seen_urls = set()

    while pending:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        raw = _fetch_url_bytes(sitemap_url)
        if not raw:
            continue

        if sitemap_url.lower().endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except Exception as e:
                logger.info(f"Could not decompress sitemap {sitemap_url}: {str(e)}")
                continue

        xml_text = raw.decode("utf-8", "replace")
        kind, locs = _parse_sitemap_xml(xml_text, sitemap_url)
        if kind == "sitemapindex":
            for loc in locs:
                loc = urljoin(sitemap_url, loc)
                if loc not in seen_sitemaps and loc not in pending:
                    pending.append(loc)
            continue

        if kind == "urlset":
            for loc in locs:
                loc = urljoin(sitemap_url, loc)
                if loc.startswith(seed_prefix) and loc not in seen_urls:
                    seen_urls.add(loc)
                    discovered.append(loc)

    logger.info(
        f"Sitemap discovery complete. Seed prefix: {seed_prefix}. "
        f"Found {len(discovered)} URLs across {len(seen_sitemaps)} sitemap candidates."
    )
    return discovered

# ---- Command-line Interface ----
def main():
    """
    Start the crawl process using configuration values from crawlconfig.py.
    All settings are read directly from config for consistency and simplicity.
    """
    if USE_URL_LIST:
        # URL List Mode - crawl specific URLs from file
        url_list_path = _resolve_url_list_path(URL_LIST_FILE)
        if not url_list_path:
            err_msg = (
                "USE_URL_LIST is true, but no URL list is provided. "
                f"Expected URL list file: {URL_LIST_FILE}"
            )
            logger.error(err_msg)
            raise FileNotFoundError(err_msg)

        with open(url_list_path, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]

        if not urls:
            err_msg = (
                "USE_URL_LIST is true, but no URL list is provided. "
                f"URL list file exists but contains no crawlable URLs: {url_list_path}"
            )
            logger.error(err_msg)
            raise ValueError(err_msg)
        
        logger.info(f"URL List Mode: Found {len(urls)} URLs to crawl from {url_list_path}")
        try:
            total_saved, total_failed = crawl_url_list(urls)
        except Exception as e:
            logger.error(f"URL list crawl failed: {str(e)}")
            logger.error(traceback.format_exc())
            sys.exit(1)

        logger.info(f"URL List crawl completed. Total saved pages: {total_saved} (failed: {total_failed})")
        if total_saved == 0:
            logger.error("URL list crawl saved no pages; failing so the pipeline does not run on empty output.")
            sys.exit(1)

    else:
        # Standard Deep Crawl Mode
        _validate_crawl_url(CRAWL_URL)

        if globals().get("DISCOVER_URLS_FROM_SITEMAP", False):
            sitemap_urls = _discover_urls_from_sitemap(CRAWL_URL)
            if not sitemap_urls:
                err_msg = (
                    "Sitemap discovery is enabled, but no URLs were discovered under "
                    f"'{CRAWL_URL}'. Verify sitemap availability and URL scope."
                )
                logger.error(err_msg)
                raise ValueError(err_msg)

            logger.info(f"Sitemap URL Mode: Crawling {len(sitemap_urls)} URLs discovered under {CRAWL_URL}")
            try:
                total_saved, total_failed = crawl_url_list(sitemap_urls)
            except Exception as e:
                logger.error(f"Sitemap URL crawl failed: {str(e)}")
                logger.error(traceback.format_exc())
                sys.exit(1)
            logger.info(f"Sitemap URL crawl completed. Total saved pages: {total_saved} (failed: {total_failed})")
            if total_saved == 0:
                logger.error("Sitemap crawl saved no pages; failing so the pipeline does not run on empty output.")
                sys.exit(1)
            return

        logger.info(f"Starting crawl with config: output_dir={CWD}, max_depth={MAX_CRAWL_DEPTH}")
        try:
            saved_pages = deep_crawl_urls()
            logger.info(f"Crawl completed. Saved pages: {saved_pages}")
        except Exception as e:
            # Exit non-zero so pipelinemanager does not run downstream steps on
            # missing/stale output (a previous version swallowed this and exited 0).
            logger.error(f"Error during crawling: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"An error occurred during crawling: {str(e)}")
            sys.exit(1)
        if saved_pages == 0:
            logger.error("Crawl saved no pages; failing so the pipeline does not run on empty output.")
            sys.exit(1)

if __name__ == "__main__":
    main() 