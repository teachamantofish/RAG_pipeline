---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/metadata_handling.md
type: reference
title: metadata_handling
name: metadata_handling
description: 'Strategy question about what to include in the embedding:'
resource: /topomorph/apps/rag-master/MkDocs/docs/metadata_handling.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs, "metadata-management", "embeddings", "semantic-retrieval", "text-summarization"]
domain: tools
tokens: 943
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
Strategy question about what to include in the embedding: 

Chunks have metadata. And the file that I'm chunking also has metadata. I have a question about what I should include in the chunk and what I should just save to the vector database separately. 

First question. Does everything that's embedded in the chunk automatically become part of the embedding? There are probably extra costs associated with that. Perhaps I need to decide what  information I need for excellent agentic retrieval from the vector db and then choose what information might be better kept out of the embedding. 

Second. I have additional metadata in each file that I'm going to chunk. For example, I have title author, create date, etc. I'm thinking this information does not need to be part of the chunk and therefore also part of the embedding. It could be stored separately in the vector database thereby users/agents to retrieve results based on date, author and so on. 

Let me know your thoughts on the appropriate strategy.
 
 
Does the field contain words a user will type in semantic queries? Include.
Is it mainly for filtering or sorting? Exclude from embedding.
Is it highly repetitive across chunks? Prefer a single doc-level embedding.



1. Does everything in the chunk become part of the embedding?

Yes — anything in the chunk text that gets passed into the embedding model is encoded into the vector.

Implication: If you put metadata (e.g., title: "Foo", author: "Bar") inside the chunk text, those tokens count toward embedding cost and will influence similarity search.

Cost trade-off: Embedding models charge per token, so unnecessary metadata bloats both cost and noise.

Strategy: Only embed what helps with semantic retrieval. For example:

✅ Keep: content sentences, section headers, bullet lists.

❌ Exclude: file IDs, timestamps, redundant system metadata (unless retrieval by those attributes semantically matters).

2. Where to put file-level metadata (title, author, date, etc.)?

You don’t need to embed these. Store them as structured metadata in the vector DB alongside each chunk.

Most vector DBs (like pgvector + Postgres, or Pinecone, Weaviate, etc.) support metadata fields.

Then you can:

Filter: WHERE author="Alice" or date > 2023-01-01

Rerank: retrieve semantically, then bias scoring by recency or author.

Benefit:

Reduces embedding cost (since you don’t embed it).

Keeps retrieval cleaner (semantic similarity isn’t “polluted” by boilerplate metadata).

Agents can still use metadata for reasoning and filtering.

3. What should sometimes be in the embedding?

There are gray areas:

Titles & section headers: often worth including in the chunk text, since they provide semantic context (“Chapter 4: Neural Networks” matters).

Keywords/taxonomy tags: if retrieval may benefit semantically (“biology, genomics, CRISPR”), you might inject them into the chunk text or store as metadata depending on use.

Structured attributes (author, version, creation date): better stored as metadata, not embedded.

4. Recommended strategy

Chunk text: only the human-readable content that needs semantic understanding (main text + headings).

Chunk metadata:

File-level: title, author, create/update date, source, file path.

Chunk-level: parent heading(s), section number, page number, hierarchy markers.

Vector DB usage:

Do similarity search on embeddings.

Do filtering on metadata.

Do reranking combining both.



What to do with summaries
1) Chunk summaries (1–2 sentences)

Purpose: boost recall on abstract queries, provide quick preview/snippet, aid reranking.

Storage: keep as chunk_summary (text) in metadata.

Embedding: optionally create a second embedding for the summary (chunk_summary_emb).

Helps when the chunk body is long/noisy and the summary is a clean semantic centroid.

Do NOT concatenate the summary into the embedded chunk text—this can shift/blur semantics and adds cost.

When to embed the summary

Queries are often high-level (“what’s the policy on X?”) → Yes.

Chunks are very short/clean already → Usually no (save tokens).

2) Page/section summaries (“roll-ups”)

Purpose: coarse-to-fine retrieval (route to the right page/section first, then pull child chunks).

Storage: as separate parent nodes with page_summary/section_summary.

Embedding: Yes—embed the roll-up summary as its own vector (centroid for its children).

Use: first-stage retrieval over roll-ups → select top parents → fetch child chunks → rerank.


DO not include numbering in any header paths. 