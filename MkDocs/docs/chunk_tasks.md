---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/chunk_tasks.md
type: reference
title: chunk_tasks
name: chunk_tasks
description: 1. Create a `chunk_markdown.py` file.
resource: /topomorph/apps/rag-master/MkDocs/docs/chunk_tasks.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 1084
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Archive planning tasks file


1. Create a `chunk_markdown.py` file.
2. Import the necessary libraries:
   - Import `Path` from `pathlib` for handling file and directory paths in a cross-platform way.
   - Import `SimpleDirectoryReader` from `llama_index.readers.file` to read markdown files from a directory.
   - Import `HierarchicalNodeParser` from `llama_index.node_parser` to maintain parent-child relationships and support future graphRAG.
   - Import `CodeSplitter` from `llama_index.node_parser` to further chunk long code blocks if necessary.
   - Import `VectorStoreIndex` from `llama_index` to create vectors for each chunk as they are processed.
   - Import `yaml` (PyYAML) to read custom metadata from a YAML file.
   - Import `openai` to generate one-line summaries for each chunk.
   - Import `OpenAIEmbedding` from `llama_index.embeddings` to generate vector embeddings for each chunk.
3. Specify the directory containing your markdown files using a `Path` object.
4. Read the custom metadata from a `metadata.yaml` file.
5. Use `SimpleDirectoryReader` to load all markdown files from the directory.
6. Initialize a `HierarchicalNodeParser` with `include_metadata=True` (and `include_prev_next_rel=True` if supported) for chunking the markdown content by heading.
7. For each loaded document, use the parser to chunk the content into nodes, preserving parent-child relationships. Chunking guidelines: 
  - Chunk by heading only. We are not using any other chunking method (for example, token length)
  - Headings 1, 2, 3, and 4 should be discrete chunks. 
  - Group any headings 5 and 6 with heading 4. 
  - Extract any code example over 5 lines and store it as a separate chunk. Reference the code example from the source chunk. 
  - Additional metadata should be created for each chunk according to the table below. At a high level: 
    - Get data from metadata.yaml
    - Use openai to create page (heading 1) and chunk summaries
    - Create the rest of the metadata during processing

| Schema Item        | Source         | Stored As   | Definitions                                                                                  |
|--------------------|---------------|-------------|-----------------------------------------------------------------------------------------------|
| document title     | user supplied | metadata    | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| source url         | user supplied | metadata    | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| date               | user supplied | metadata    | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| domain             | user supplied | metadata    | metadata.yaml Required for separating content in db using pseudo db instances                 |
| filename           | file system   | metadata    | Read from system                                                                              |
| id                 | generated     | metadata    | Unique UUID for each chunk                                                                    |
| parent_id          | generated     | metadata    | ID of the parent chunk (if any)                                                               |
| header_level       | generated     | metadata    | Inferred from heading. May use this with ranking and structuring                              |
| id_prev            | generated     | metadata    | Link to prev                                                                                  |
| id_next            | generated     | metadata    | Link to next                                                                                  |
| url_slug           | generated     | metadata    | Optional: markdown-style anchor slug (e.g., #installing-on-windows)                           |
| examples           | generated     | metadata    | List of code example references                                                               |
| ranking            | generated     | metadata    | Pre ranking based on # of matches in parents, children, summary, etc.                         |
| token count        | generated     | metadata    | Only used if we're interested in checking relative chunk sizes                                |
| embedding          | generated     | vector      | Optional: store the embedding vector separately                                               |
| heading            | document      | chunk       | Closest header (h1-h4)                                                                        |
| concat header path | generated     | chunk       | Header hierarchy path (e.g., "Getting Started > Installation")                                |
| content/text       | document      | chunk       | The chunked text                                                                              |
| summary            | generated     | chunk       | openai-generated summary of this chunk alone                                                    |
| context_summary    | generated     | chunk       | | openai-generated summary of the parent page (usually heading 1) for added semantic context           |

1.  Initialize the embedding model (e.g., `embed_model = OpenAIEmbedding(model="text-embedding-3-small")`).
2.  As you process each chunk, use `VectorStoreIndex` and the embedding model to create and store the vector for that chunk.
3.  Collect all chunks (nodes) into a list for further processing or inspection.
4.  Save output chunks and metadata to a specified directory.
5.  Print the token size of each chunk to a file in the output directory for review.
6.  Test the script to ensure it correctly chunks your markdown files, stores the required metadata, and creates vectors for each chunk.