---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/gettingcontent.md
type: reference
title: gettingcontent
name: gettingcontent
description: '**Done**.'
resource: /topomorph/apps/rag-master/MkDocs/docs/gettingcontent.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 609
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Getting content

**Done**.

Scraping the web primarily uses llamaindex with some addons such as BeautifulSoup. I'm choosing to get HTML here even if there's markdown in a repo for the sake of consistency. All sources docs whether HTML, PDF, DOCX, or MD are converted to markdown.

- Convert all sources to markdown
- Clean and normalize markdown (llamaindex, markdowncleaner, and custom regex): 
- Don't remove frontmatter that might be needed for metadata
- Strip non-semantic syntax; e.g. bold, images, etc.
- Unwrap hard wrapped lines
- Remove image links and images, retain alt text
- HTML cleaning
- Whitespace normalization
- Code example processing: retain whitespace. I've chosen to add the previous paragraph line as a comment prefacing the code since that usually is a code example title or summary. 

## Setup notes

1. Install crawl4AI: https://docs.crawl4ai.com/core/installation/
2. pip install markdowncleaner. This is perhaps more useful for PDF source, but run it anyway just in case.
3. Configure cmd_crawl.py
    1. Set crawl_config.py options. I set a target directory outside the directory where this script runs so my IDE agent does not index it. 
    2. Set options in recursive_crawl_urls for CrawlerRunConfig and DefaultMarkdownGenerator (from llamaindex)
    3. Set options in clean_markdown for MarkdownCleaner. See https://github.com/josk0/markdowncleaner/tree/main/src/markdowncleaner.
    4. Create any needed custom regex in custom_regex.
4. Run `python -m cmd_crawl.py`

> **Note**
> crawl4ai has many options, so read the docs to solve scraping issues. Pandoc is another good post-crawl processing choice. There does not appear to be a "one size fits all" tool so get the tools you need and customize.

## Packages used

```python
import os  # For directory and file operations
import re
import asyncio  # For async crawling
import argparse  # For command-line argument parsing
from urllib.parse import urljoin, urlparse, urldefrag
from collections import deque
from markdowncleaner import MarkdownCleaner, CleanerOptions # for additional markdown post-processing
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
# Import Crawl4AI core classes and strategies from the installed package
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher
from crawl4ai import CrawlerMonitor, DisplayMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai import BrowserConfig

import logging  # For logging
import traceback  # For detailed error information
```
