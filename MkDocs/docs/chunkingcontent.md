---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/chunkingcontent.md
type: reference
title: chunkingcontent
name: chunkingcontent
description: '**Done**.'
resource: /topomorph/apps/rag-master/MkDocs/docs/chunkingcontent.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 790
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Chunking content

**Done**.

 I use llamaindex as much as possible to keep the tech stack simple. There are a number of chunking strategies to choose from, but I prefer a strategy that retains simplicity while delivering high accuracy relative to more advanced strategies (such as LLM-based chunking). It should also make logical sense given the types of documents I'll be processing.  This research paper is worth a read: https://research.trychroma.com/evaluating-chunking. 
 
 Since all source documents are likely specifications, technical docs, or API references related to developing code, I've decided to use recursive chunking with an approximate chunk size of 400 and no overlap across chunks. The ChromadB research paper suggests a chunk size of about 400 tokens. This is a perfect size since that equates to approximately 15 to 20 lines per chunk at 80 characters wide. That matches fairly well with the likely structure I'll encounter in the technical content available in my source markdown documents. In this case. I expect most content under each heading to fall within the 200-400 token range. My plan is to increase the accuracy by saving to metadata the hierarchical heading path as well as the child headings under the same section. I will also be adding custom metadata during processing.

 ![Chunking strategy performance](images/chunkperformance.png)

## Chunking Strategy

- **Metadata**
  - Define metadata that cannot be programmatically created in a config file
  - Store metadata in well-defined fields in the same table row as the chunk
  - Use `llamaindex MarkdownNodeParser`:
    - `include_metadata` (bool): whether to include metadata in nodes
    - `include_prev_next_rel`
- **Chunking**
  - Chunk by heading (assuming structured, technical docs)
  - Don't chunk H5 (keep with H4)
  - If a heading and its child headings are < 500 chars, chunk together
  - Reference code examples from each chunk
  - Reference metadata from each chunk
  - Summaries in metadata:
    - Summarize each chunk and store in metadata
    - Summarize H1 and store in metadata

## Code example chunking

This strategy is evolving. . . 

- **Code Handling**
  - Use `CodeSplitter` from `llama_index.node_parser`:
    - Store code examples longer than 3 lines separately; reference from text chunk
    - Chunk by function/class

```python
splitter = CodeSplitter(
    language='python',          # or 'javascript', 'java', etc.
    chunk_lines=50,             # Number of lines per chunk
    chunk_overlap=5             # Overlap lines for continuity
```

## Embedding (create vectors)

- At embedding time, use an embedding model tuned for code:
  - OpenAI: `text-embedding-3-small` is OK
  - Use `embed_model = OpenAIEmbedding()` (creates vector representation)

## Packages used




## notes
Extractive vs. Abstractive Summarization: Crafting the Right Prompts

## Metadata strategy

Great question. Short answer: keep almost everything as metadata outside the embedding, and only inject a tiny, high-signal bit of structure into the text you embed. You’ll get better recall, cleaner filters, and less embedding noise.

What to include in the embedding text

Use a compact, standardized preamble that provides just enough context for retrieval:

Heading (normalized)

Concatenated header path (your concat_header_path)

Document title only if the chunk isn’t already under the H1

A micro-summary of the chunk (≤ 1 sentence, ~30–50 tokens) — optional for code (see note)


============ ideas only: 
need a better chunk id 
need to capture model used. 