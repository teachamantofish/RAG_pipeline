---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/unusedideas.md
type: reference
title: unusedideas
name: unusedideas
description: Might do later.
resource: /topomorph/apps/rag-master/MkDocs/docs/unusedideas.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 665
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Unused ideas

Might do later. 

watch: https://www.youtube.com/watch?v=yzPQaNhuVGU&t=713s: good ideas 
llamaindex directory reader reads any file in a dir regardless of format. 


## groupingHeadingsToReach400Tokens

Key points:
- This logic always includes the full content of each heading.
- It merges as many headings as possible into a chunk, up to the token limit.
- It never splits a heading’s content.
- You can still extract code examples and generate summaries for each chunk as before, just use combined_text instead of node.text.

How it works:
- It starts a chunk with the current heading.
- It tries to add as many subsequent headings as possible without exceeding 400 tokens.
- It never splits a heading’s content.
- When the next heading would push the chunk over 400 tokens, it starts a new chunk.


```python
MAX_TOKENS = 400

for doc in documents:
    nodes = markdown_parser.get_nodes_from_documents([doc])
    i = 0
    n = len(nodes)
    prev_id = None

    while i < n:
        # Start a new chunk with the current node
        chunk_nodes = [nodes[i]]
        chunk_tokens = len(TOKENIZER.encode(nodes[i].text))
        j = i + 1

        # Try to add as many subsequent nodes as possible without exceeding MAX_TOKENS
        while j < n:
            next_tokens = len(TOKENIZER.encode(nodes[j].text))
            if chunk_tokens + next_tokens > MAX_TOKENS:
                break  # Adding this node would exceed the limit
            chunk_nodes.append(nodes[j])
            chunk_tokens += next_tokens
            j += 1

        # Combine the content and metadata for the chunk
        chunk_id = str(uuid.uuid4())
        combined_text = "\n\n".join(node.text for node in chunk_nodes)
        chunk = {
            "id": chunk_id,
            "filename": doc.metadata.get("file_path", "unknown.md"),
            "heading": chunk_nodes[0].metadata.get("heading", ""),
            "header_level": chunk_nodes[0].metadata.get("header_level", None),
            "concat_header_path": chunk_nodes[0].metadata.get("header_path", ""),
            "content": combined_text,
            "parent_id": chunk_nodes[0].metadata.get("parent_id", None),
            "examples": [],
            "token_count": chunk_tokens,
            "summary": "",    # to be filled
            "context_summary": "",  # to be filled
            "id_prev": prev_id,
            "id_next": None,  # to be filled after loop
            "embedding": None, # to be filled
        }
        # Attach user metadata
        chunk.update(user_metadata)

        # (You can keep your code example extraction and summary logic here, using combined_text)

        all_chunks.append(chunk)
        if prev_id is not None:
            chunk_id_map[prev_id]["id_next"] = chunk_id
        chunk_id_map[chunk_id] = chunk
        prev_id = chunk_id

        i = j  # Move to the next unprocessed node
```