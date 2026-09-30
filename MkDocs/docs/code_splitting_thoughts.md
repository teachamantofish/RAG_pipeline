---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/code_splitting_thoughts.md
type: reference
title: code_splitting_thoughts
name: code_splitting_thoughts
description: I asked several agents what my approach should be with respect to long code examples. I've decided to generate a code example chunk summary via an LLM agent and double the size of that summary in hopes of enhancing retrieval.
resource: /topomorph/apps/rag-master/MkDocs/docs/code_splitting_thoughts.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs, "text-chunking", "semantic-retrieval", "text-summarization"]
domain: tools
tokens: 802
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Splitting long code into chunks

I asked several agents what my approach should be with respect to long code examples. I've decided to generate a code example chunk summary via an LLM agent and double the size of that summary in hopes of enhancing retrieval. 

I've attached my agent responses below.

## Question

Some of the code examples Im going to use in an agentic RAG pipeline range from 800 to 1500 tokens long. I'm wondering if I should split them up or leave in one piece. What is going to be the difference for a agentic RAG pipeline with respect to accuracy and precision if I keep a discrete code example unchunked even if it's long?

On the one hand, keeping a code example entirely on one chunk seems like it makes sense because the code belongs together and can't function without the other pieces. On the other hand, a lot of what I'm reading says the chunk should be around 400 tokens. What are your thoughts?
ChatGPT said:

## Agent answer 

This is a nuanced tradeoff between semantic coherence and retrieval precision. Since you're building an agentic RAG pipeline (where an LLM may act in a planning/execution loop), the stakes for chunking strategy are a bit higher. Here's how to think about it:
🧩 Keeping Long Code Examples Intact (800–1500 tokens)

Pros:

    Semantic coherence: The entire code context is preserved. This is especially important for:

        Functions/classes that span many lines.

        Examples that include setup, execution, and output.

    Less hallucination: The LLM sees all relevant parts of the code together, reducing risk of incorrect assumptions.

    Better for reasoning or tool use: If your agent will manipulate or execute code, it needs full access to dependencies and scope.

Cons:

    Reduced retriever precision: Vector similarity search becomes less granular. A query might match a large chunk even if only a small part is relevant.

    Embedding dilution: Embeddings are an averaged representation; long chunks can blur focus.

    May not fit in context: Especially if you're assembling multiple chunks in a prompt for the LLM.

✂️ Splitting Long Code into Smaller Chunks (~400 tokens)

Pros:

    Higher retrieval precision: Smaller chunks are easier to rank and score accurately.

    Easier reranking: If you’re using hybrid search (e.g. dense + keyword or reranker), small chunks give finer granularity.

    Faster embedding updates: Changing a small part of the code doesn’t require re-embedding a giant chunk.

Cons:

    Loss of semantic integrity: Many programming constructs require prior definitions or context.

    Agent may need to stitch context: In agentic workflows, this adds planning and increases risk of failure.

💡 Hybrid Strategy (Recommended)

Use adaptive chunking with the following logic:

    Keep code examples together if they are tightly coupled and meant to function as a whole. It’s better to keep one 1200-token example intact than split a class or function in two.

    Use metadata to help the retriever understand boundaries — e.g., include "type": "code-example" or "function": "init_model()" in metadata.

    Augment with summaries: For large chunks, precompute a 100–200 token natural language summary to index alongside. This helps retrieval and reranking.

    Enable cross-chunk linking or stitching: If you do split, build logic into your RAG pipeline to allow pulling in neighboring chunks if needed (e.g. via parent-child or sibling references).

🛠 Agentic RAG-Specific Considerations

Since your agent may:

    Select tools based on code examples.

    Modify, run, or generate similar code.

You should bias toward keeping semantically complete code blocks intact. This will:

    Make reasoning steps easier.

    Allow planning without additional retrieval steps.

    Avoid requiring the agent to guess or reconstruct partial logic.

