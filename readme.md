# Running the pipeline (current state)

1. Configure the run in `run_settings.py` (data dir `CWD`, `LOG_DIR`, and the `METADATA` block — `PARSER` selects the crawler: `crawlweb`, `crawlpdf`, or `crawlgit`). The `RAG_DATA_ROOT` / `RAG_LOG_DIR` env vars override the paths for other machines.
2. Per-phase settings live in `Get_and_Chunk/config/*.py` and `VectorDB/config/*.py` (editable via the web UI).
3. Run everything: `python run_python_entry.py Get_and_Chunk/pipelinemanager.py` — or run individual steps (`1crawlweb.py`, `2markdown_cleanup.py`, `3.00chunker.py`, `4.01summary.py` or `4.00summary_wrapper_hf.py`, `VectorDB/embedding.py`, `VectorDB/upsert_to_vectordb.py`) through the same launcher.
4. Secrets come from env vars: `HF_TOKEN` (summaries via HF endpoint), `OPENAI_API_KEY` (openai backend), `VECTOR_DB_PASSWORD` (Postgres).
5. Web UI: `cd Web_App/vite && npm install && npm run dev`, then open http://localhost:5173/index.html.
6. Tests: `python -m pytest Get_and_Chunk/tests` (offline; external deps are stubbed) and `cd Web_App/vite && npm test`.

---

# End game


Content data: fine tune, optimize, enrich
Rag: tuned, hier, etc. 
Train embed model, tuned embeddings
Vector store
Hybrid retrieval: keyword/semantic
Reranker training: reranking
Eval data set
prompt engineering
ask: tuned question + tuned assets 
Result

Code faster, better, and cheaper: 

- Intelligent web scraping: Tuned web scraping of any document type. 
- Chunking tuned for technical documents.
- Vectorize document chunks stored in open source dB and tuned for specific domains. 
- dB of rich sources for tuning context and prompts so my coding agent can behave as a domain expert.
- Agentic rag (example: https://www.youtube.com/watch?v=c5jHhMXmXyo&t=173s and blog: https://weaviate.io/blog/what-is-agentic-rag

> **Note**
> At a high level these documents are up to date. However, I'm not trying to keep all the details current because the scripts change daily as a result of testing and encountering new issues with newly added docs.

## Architectural decisions

- Convert only markdown. Convert all file types to markdown. 
- Target a 400 token chunk size, but don't be too picky. Source docs are random in quality and nature. *Usually* split code chunks; keep code together unless query responses are imprecise.
- Design a schema that supports future use of re-ranking and graphrag.
- Don't use any code or rely on any Youtube video more than 4 months old!
- Use only free and open source tools: Python, Crawl4AI, LlamaIndex, PostgreSQL
- - Place parameters in separate config files so settings and script execution can be controlled via a web UI.
- Pipeline phases will be in discrete scripts: 
    - crawl_config.py: pipeline configs
    - crawler.py: get and preprocess data. Convert all formats to markdown
    - chunker.py: Chunk, summarize, and create embeddings
    - vector.py: Push to database
    - query.py: Get answers from the database


## RAG pipeline tech stack

**Current pipeline choice**: CrawlforAI > Preprocessing (llamaindex + custom) > Chunking (llamaindex) > postprocessing (llamaindex) > openAI embedding (OpenAI via (llamaindex)> store in postgreSQL/pgvector dB > custom web UI

**Future**: 
- DONE: Support PDF
- DONE: Support Word
- Improve dB with pgscale or timescale
- Add llamaindex reranking
- Add agentic rag
- web app UI.

## Getting content

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

Setup notes: 

1. Install crawl4AI: https://docs.crawl4ai.com/core/installation/
2. pip install markdowncleaner. This is perhaps more useful for PDF source, but run it anyway just in case.
3. Configure cmd_crawl.py
    1. Set crawl_config.py options. I set a target directory outside the directory where this script runs so my IDE agent does not index it. 
    2. Set options in recursive_crawl_urls for CrawlerRunConfig and DefaultMarkdownGenerator (from llamaindex)
    3. Set options in clean_markdown for MarkdownCleaner. See https://github.com/josk0/markdowncleaner/tree/main/src/markdowncleaner.
    4. Create any needed custom regex in custom_regex.

> **Note**
> crawl4ai has many options, so read the docs to solve scraping issues. Pandoc is another good post-crawl processing choice. There does not appear to be a "one size fits all" tool so get the tools you need and customize.

1. Run `python -m cmd_crawl.py`
 
 ## Chunking content

 I've decided to stick with llamaindex as much as possible to keep the tech stack simple. There are a number of chunking strategies to choose from, but I prefer a strategy that with a high accuracy relative to some of the more advanced strategies, such as LLM-based chunking. It should also make logical sense given the types of documents I'll be processing.  This research paper is worth a read: https://research.trychroma.com/evaluating-chunking. 
 
 Since all source documents will be specifications, technical docs, or API references related to developing code, I've decided to use recursive chunking with an approximate chunk size of 400 and no overlap across chunks. The ChromadB research paper suggests a chunk size of about 400 tokens. This is a perfect size since that equates to approximately 15 to 20 lines per chunk at 80 characters wide. That matches fairly well with the likely structure I'll encounter in the technical content available in my source markdown documents. In this case. I expect most content under each heading to fall within the 200-400 token range. My plan is to increase the accuracy by saving to metadata the hierarchical heading path as well as the child headings under the same section. I will also be adding custom metadata during processing.

 ![Chunking strategy performance](images/chunkperformance.png)

### Chunking Strategy

- **Metadata**
  - Define metadata that cannot be programmatically created in a config file
  - Store metadata in well-defined fields in the same table row as the chunk
  - Use `llamaindex` `MarkdownNodeParser`:
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

#### Code example chunking

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

## Example data 

**Note**: The actual schema is better and continues to evolve (see the table below).

```json
{
  "id": "chunk-0458",
  "embedding": [0.123, -0.456, 0.789, ...],  // 1536 floats for `text-embedding-3-small`
  "text": "To install the CLI, run `npm install -g example-cli`...",
  "metadata": {
    "section_header": "Installation",
    "full_path": "Docs > Getting Started > Installation",
    "summary": "Explains how to install the CLI via npm.",
    "context_summary": "Covers the setup and configuration for first-time users.",
    "parent_node": "chunk-0457",
    "child_nodes": ["chunk-0459", "chunk-0460"],
    "doc_id": "doc-101",
    "title": "Getting Started Guide",
    "custom_title": "Beginner Setup",
    "source_url": "https://example.com/docs/getting-started",
    "file_name": "getting-started.md",
    "date": "2024-06-01",
    "code_example_ref": "example-CLI-install-1",  // could link to a repo or blob store
    "tokens": 138,
    "chunk_index": 3
  }
}
```

## Set up a data store

After much research I decided that I do not need a vector database such as Pinecone or Supabase. This decision is based on the fact that I'll probably never index more than 20,000 pages. My chunk token count should stay under 4 million tokens. Therefore, I'm going with open source postgresql and pgvector. 

### Strategy

It's worth noting that we only need a single database because disparate content will be identified by metadata. For example, a FrameMaker query doesn't need to know anything about Markdown or PDF tools. Both custom and generated metadata should adequately isolate content by domain. In addition to script-extracted or generated metadata, I will be adding custom metadata that includes the domain, content type, and a short description. For example: " FrameScript Specification (for manipulating FrameMaker documents)." The strategy supports:

- **Semantic Isolation**: Vector similarity search should have a high level of retrieval quality since similar embeddings are semantically meaningful in the same context.
- **Query Precision**: Reduced noise to increase performance and relevance for agents working in a specific domain.
- **Efficient Resource Use**: Small, domain-specific collections are faster to query and easier to manage. Isolated contexts provide a way to modify the architecture as the pipeline evolves.
- **Agent-Specific Retrieval**: Specific domains provide a way to use domain-specialized agents and create multi-agent workflows. This keeps the mental model and toolchain cleaner.

LlamaIndex makes this easy:

- Use multiple VectorStoreIndex instances, each pointed to its own domain data and vector store.
- Query the correct index depending on the agent context.
- Use a single PostgreSQL database.
- Create a single table for vectors, but include a domain column ('framemaker', 'markdown', etc.).
- Configure LlamaIndex to use pgvector with filtering by domain at query time.

### Schema

Everything that provides semantic context to the embedding model is stored with the chunk. Other items are stored in the metadata and stored separately. It's important to preserve the hierarchical and logical placement of the chunk relative to the document.

| Schema Item        | Source        | Text type   | Data Type   | Definitions                                                                                   |
|--------------------|---------------|-------------|------------|-----------------------------------------------------------------------------------------------|
| custom title       | user supplied | metadata    | TEXT       | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| source url         | user supplied | metadata    | TEXT       | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| date               | user supplied | metadata    | TEXT       | metadata.yaml Source may not have it and/or doc cleanup may remove it                         |
| domain             | user supplied | metadata    | TEXT       | metadata.yaml Required for separating content in dB using pseudo dB instances                 |
| filename           | file system   | metadata    | TEXT       | Read from system                                                                              |
| id                 | generated     | metadata    | TEXT       | Unique UUID for each chunk                                                                    |
| parent_id          | generated     | metadata    | TEXT       | ID of the parent chunk (if any)                                                               |
| header_level       | generated     | metadata    | INTEGER    | Inferred from heading. May use this with ranking and structuring                              |
| id_prev            | generated     | metadata    | TEXT       | Link to prev                                                                                  |
| id_next            | generated     | metadata    | TEXT       | Link to next                                                                                  |
| examples           | generated     | metadata    | TEXT       | List of code example references                                                               |
| ranking            | generated     | metadata    | REAL       | Pre ranking based on # of matches in parents, children, summary, etc.                         |
| token count        | generated     | metadata    | INTEGER    | Only used if we're interested in checking relative chunk sizes                                |
| type               | generated     | metadata    | TEXT       | Code example only: null if text, "code_example" if code                                       |
| language           | generated     | metadata    | TEXT       | Code example only: The auto-detected language of the code example                             |
| embedding          | generated     | vector      | vector     | Optional: store the embedding (1536) vector separately                                               |
| heading            | document      | chunk       | TEXT       | Closest header (h1-h4)                                                                        |
| concat header path | generated     | chunk       | TEXT       | Header hierarchy path (e.g., "Getting Started > Installation")                                |
| content/text       | document      | chunk       | TEXT       | The chunked text                                                                              |
| summary            | generated     | chunk       | TEXT       | Auto-generated summary of this chunk alone                                                    |
| context_summary    | generated     | chunk       | TEXT       | Generated summary of the parent section for added semantic context                            |

### Install llamaiindex packages

1. pip install llama-index
2. pip install llama-index-vector-stores-postgres
3. pip install psycopg2-binary: a Python package that provides a PostgreSQL database adapter for Python. It allows your Python code to connect to and interact with a PostgreSQL database. It is commonly used in Python projects that need to run SQL queries, manage tables, or interact with PostgreSQL in any way.
4. pip install openai: The openai Python package is the official client library for accessing OpenAI's APIs (such as GPT-3, GPT-4, embeddings, etc.) from Python code. It allows you to send requests to OpenAI models (for text generation, embeddings, etc.) and receive responses in your Python programs.

### Set up postgreSQL and pgvector

1. Install https://www.enterprisedB.com/downloads/postgres-postgresql-downloads
2. pip install pgvector (to verify the package) installed (does not install pgvector extension)
3. Compile/build pgvector: ⌛This step was filled with landmines and pitfalls. There are many install paths, including Docker, VMs, Windows build tools, etc. I didn't want to install 7 GB of Visual Studio files, and my AI agent told me I could use MSYS. However, that rabbit hole cost me several hours. In the end, I installed Visual Studio and followed the instructions ![in this article](https://www.mindfiretechnology.com/blog/archive/installing-pgvector-in-preparation-for-retrieval-augmented-generation/), and was up and running in 30 minutes.
4. pwd: xxxx; port 5432
5. Get pgadmin: https://www.postgresql.org/ftp/pgadmin/pgadmin4/v9.4/windows/
6. Verify postgresql server is running (services.msc)
7. Open pgadmin
8. Create a new server
9. Connect to the server
10. Open the query workspace tool


11. Choose the server to query.
12. ????


### Push to database

1. Launch pgAdmin (auto starts the server)
2. Create a server.
3. Right click on the server.
4. Choose **Register > Server**.
5. Provide a name, host name, port, username
6. Adobe gotcha: Enable the port via [Windows Defender Firewall if needed](images/postgres_windows.png).
7. Select the Server.
8. Select the Query tool.
9. Create the dB.
10. Connecting to the dB took several hours of troubleshooting. Hopefully you're not a novice.
11. Run vectory.py
12. Verify the chunks exist. To view your data in pgAdmin: In the Browser panel, expand **Databases > postgres > Schemas > public > Tables**. Select **Select View/Edit Data > All Rows**.

### Use the data 

There are several ways to retrieve and use the data, each with varying levels of complexity. I will implement them one by one here.

- llamaiindex retriever: Query the database and get results based on a semantic match. No AI or LLM is involved. 
- llamaindex local chatbot
- Using agent outside of Cursor and use my personal OpenAI key
- Build a custom agent that interacts with a web UI to query my database and return results.
- Use the process above but automatically feed those results along with other prompt instructions to the agent inside of Cursor using an MCP server. 
- (Don't know if I'll do this). Instead of using my personal OpenAI key for the agent usage above, install an LLM locally and run the agent locally.

## TBD: User interface for pipeline config



## Setup (future work)

Run it all from a web UI. . . 

1. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
2. Run the Flask app:
   ```sh
   python app.py
   ```
3. Open your browser to [http://localhost:5000](http://localhost:5000) and click "Start Crawl".


