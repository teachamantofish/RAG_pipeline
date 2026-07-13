# RAG Pipeline — Analysis & Improvement Plan

> **Implementation status (2026-07-07):** the plan below has been implemented on this branch.
> - Phase 1 (correctness) + Phase 2 (performance): commit `183ee00`
> - Consolidation (configs, chunker refactor, tests, hygiene): commit `0e41b09`
> - Web app: commit `d2d5522`
>
> Not done, by design: Shoelace self-hosting (§4.5 — still CDN), moving scratch scripts
> (`2crawlwebtest.py`, `xxcrawlwebtest.py`), untracking `MkDocs/site/` (left in case it is
> deployed from the repo), and the 1crawlpdf page-range off-by-one audit (§2.8 — the
> URL-vs-path validation was added; verify TOC_RANGE/REMOVE_RANGES semantics with a real PDF).
> Test suites: `pytest Get_and_Chunk/tests` (27) and `Web_App/vite: npx vitest run` (72) both pass.

Date: 2026-07-07
Scope: full repo review — pipeline scripts (`Get_and_Chunk/`, `VectorDB/`, `Logger/`, root launchers) and web app (`Web_App/`, Vite dev server).

This document lists confirmed bugs, likely bugs, inefficiencies, and improvement opportunities, ordered by severity, followed by a phased remediation roadmap. File references use `path:line` from the current `main` head (d79e503).

---

## 1. Critical bugs (pipeline-breaking or data-loss)

### 1.1 `4.01summary.py` references config that does not exist — summary step cannot run
- **Where:** `Get_and_Chunk/4.01summary.py:27-29, 63-66, 249-251, 298-311`
- **Problem:** The script calls `run_summary_backend(...)` and reads `SUMMARY_SETTINGS["chunk"]` / `SUMMARY_SETTINGS["file"]`, but neither name is defined in `Get_and_Chunk/config/summaryconfig.py` (which only defines flat constants like `CHUNK_SUMMARY_MODEL`, `CHUNK_SUMMARY_SIZE`). Grep confirms `SUMMARY_SETTINGS`/`run_summary_backend` exist only in the tests (where they are monkeypatched in) and in `4.01summary.py` itself.
- **Effect:** `check_llm_connection()` raises `NameError`, caught by the broad `except`, and the script exits 1. Step 4 of `pipelinemanager.py` always fails. The pipeline is currently broken at the summarization phase.
- **Fix:** Reintroduce the missing layer: either (a) add a `SUMMARY_SETTINGS` dict + `run_summary_backend(backend, text, params)` implementation (OpenAI-compatible + HF TGI backends, selected by `CHUNK_SUMMARY_MODEL`/`HF_ENDPOINT_URL` env override used by `4.00summary_wrapper_hf.py`) to `summaryconfig.py` or a new `common/summary_backends.py`, or (b) rewrite `4.01summary.py` to use the flat constants that actually exist. Add a smoke test that *imports and calls* the real config path (the current tests stub everything, which is why this regression is invisible).

### 1.2 `TESTINGMODE = "Null"` silently forces testing mode forever
- **Where:** `Get_and_Chunk/config/summaryconfig.py:11`, consumed at `4.01summary.py:358-372`
- **Problem:** `TESTINGMODE` is the *string* `"Null"`, which is truthy. `get_testing_mode()` returns it, so `testing_mode is None` is always false and the script always takes the testing branch: `summarize_summaries()` never runs, `run_token_counter` never runs, and it prints "Only processed Null chunks."
- **Effect:** Even after fixing 1.1, page summaries and final token counts are silently skipped on every run.
- **Fix:** Use `TESTINGMODE = None` in the config (and in `Web_App/config/defaults/summaryconfig.py`, which has the same value). In the summary schema, make `TESTINGMODE` a nullable number field, and in `get_testing_mode()` treat any non-int as `None` explicitly with a logged warning.

### 1.3 Testing mode truncates and overwrites `a_chunks.json` (data loss)
- **Where:** `Get_and_Chunk/4.01summary.py:282-287` and `352-354`
- **Problem:** `summarize_chunks(testing_limit=N)` does `chunks = chunks[:limit]` and later `json.dump(chunks, ...)` to the same file. Running with a numeric `TESTINGMODE` (the documented workflow: "Test on 1-N chunks first") permanently deletes every chunk past N from `a_chunks.json`.
- **Fix:** Keep the full list; iterate over a bounded slice (`for chunk in chunks[:limit]`) but always save the full `chunks` list. Better: in testing mode write to a separate `a_chunks_test.json`.

### 1.4 Embeddings are lost when `USE_PARQUET = False`
- **Where:** `VectorDB/embedding.py:381-401` (writes only the *stripped* JSON), `546-579` (Parquet write is conditional)
- **Problem:** The script computes embeddings in memory, writes `a_chunks_postembedding.json` with all vectors removed, and writes vectors only to Parquet when `USE_PARQUET` is true. It never writes embeddings back to `a_chunks.json`. With `USE_PARQUET=False` (a supported config value, and `STRIP_EMBEDDINGS_IN_JSON` is documented as controlling this but is never referenced by code), vectors exist nowhere on disk; `upsert_to_vectorydb.py` then finds zero content embeddings and aborts.
- **Fix:** Honor `STRIP_EMBEDDINGS_IN_JSON`: when Parquet is off (or stripping is off), persist embeddings into the chunks JSON the upsert step reads. Add an end-of-run assertion: "at least one persisted artifact contains the vectors."

### 1.5 Crawler failures exit with code 0 — pipeline proceeds on garbage
- **Where:** `Get_and_Chunk/1crawlweb.py:503-509` (deep-crawl mode), `458-467` (URL-list mode)
- **Problem:** `main()` catches all exceptions, logs them, and returns normally. `pipelinemanager.py` checks subprocess exit codes; a completely failed crawl still reports exit 0, so cleanup/chunking run against an empty or stale directory.
- **Fix:** Re-raise or `sys.exit(1)` after logging; in URL-list mode, track per-URL failures and exit non-zero if all (or a configurable fraction of) URLs failed.

### 1.6 Crawler output filenames collide and silently overwrite
- **Where:** `Get_and_Chunk/1crawlweb.py:110-134` (`save_markdown`)
- **Problem:** The filename is derived only from the URL's last path segment (`.../guide-a/install` and `.../guide-b/install` both become `install.md`; any URL ending in `/` becomes `index.md`). Later pages overwrite earlier ones with no warning — silent content loss on any site with repeated slug names.
- **Fix:** Derive filenames from the full path relative to the crawl root (mirroring directory structure), or append a short hash of the full URL on collision. Log every overwrite decision.

### 1.7 Partial DB upserts report success
- **Where:** `VectorDB/upsert_to_vectorydb.py:479-488`
- **Problem:** A failed batch is logged and rolled back, then the loop continues; the script always exits 0. `pipelinemanager` (and the web UI Run button) reports "completed successfully" even if most batches failed.
- **Fix:** Count failed batches and `sys.exit(1)` if any failed; include failed-id ranges in the log. Consider retrying a failed batch row-by-row to isolate the poison row.

---

## 2. Bugs (incorrect behavior, dead code, drift)

### 2.1 Code summaries can never trigger
- **Where:** `Get_and_Chunk/4.01summary.py:330` — checks `chunk.get("type") == "code_example"`, but the chunker emits `chunk_type == "example"` (`3.00chunker.py:107-129`). Also `openai` is used at line 332 but never imported (`import` exists only in the test stub). Two independent reasons this branch is dead. Same stale key in `_prepend_table_summary` fallback (`chunk.get("type")`).
- **Fix:** Standardize on `chunk_type` with values `heading|example|table` everywhere; if code summaries are wanted, route them through the same backend abstraction as 1.1 instead of a raw `openai` global.

### 2.2 `summarize_summaries()` pads every chunk regardless of threshold
- **Where:** `4.01summary.py:258-261`
- **Problem:** `_prepend_metadata_to_small_chunk(c)` is documented as "Assumes caller has already checked token_count < PAD_CHUNK_THRESHOLD", but the page-summary loop calls it unconditionally for every chunk in every group. Large chunks get header-path/summary text prepended, inflating them past the max-size budget enforced by the chunker, and the mixed `\r\n` line endings it inserts (lines 105-120) contaminate otherwise-LF content.
- **Fix:** Apply the same `token_count < PAD_CHUNK_THRESHOLD` guard, and use `\n` consistently.

### 2.3 Chunker's node↔heading-block pairing is fragile
- **Where:** `3.00chunker.py:276-367` (`build_candidates_from_linear`)
- **Problem:** The function walks LlamaIndex `MarkdownNodeParser` nodes but ignores their text entirely, instead popping one heading block per node from its own regex-extracted deque (`heading_blocks[filename].popleft()`). This assumes MarkdownNodeParser produces exactly one node per heading. Any divergence — preamble text before the first heading, setext (`===`) headings, parser version changes, nodes split on other boundaries — silently pairs the wrong heading with the wrong body or drops trailing sections (`if not blocks: continue`).
- **Fix:** Drop LlamaIndex here: you already have a correct heading-block extractor; iterate files directly (glob `*.md`) and consume `_extract_heading_blocks` output as the single source of truth. This removes a heavyweight dependency from the hot path, removes the desync class of bugs, and makes `SimpleDirectoryReader`'s file-ordering quirks irrelevant. (Also removes the unused `_extract_heading()` helper.)

### 2.4 `prev`/`next` links cross document boundaries
- **Where:** `3.00chunker.py:842-846`
- **Problem:** `link_prev_next` links the flat chunk list, so the last chunk of `fileA.md` points to the first chunk of `fileB.md`, and peeled example/table component chunks are interleaved into the sequence. Retrieval-time "expand context" walks will leak across documents.
- **Fix:** Group by `filename` before linking; consider linking only `heading` chunks and letting components hang off `examples`/`parent_id`.

### 2.5 Component chunks get the wrong `parent_id`
- **Where:** `3.00chunker.py:488-511` (`_make_component_chunk` uses `source.parent_id`)
- **Problem:** A code example peeled out of heading H becomes a *sibling* of H (parented to H's parent) rather than a child of H, while `H.examples` claims it. Hierarchy queries (e.g., `_resolve_h1_root` in the summary phase) will attach examples to the wrong section.
- **Fix:** Use `parent_id=source.id` for peeled components.

### 2.6 `embedding: "false"` string sentinel is type-unsafe
- **Where:** `3.00chunker.py:958-1001` (`chunks_to_dicts` sets `embedding="false"`), consumed via `str(chunk.get('embedding')).lower() == 'false'` in `VectorDB/embedding.py:294`
- **Problem:** One field holds three types (list | None | the string "false"), and stringly-typed checks propagate (`upsert` has `_coerce_vector` defensive code purely because of this). Chunk summary/page summary get the same `"false"` treatment.
- **Fix:** Add an explicit boolean `embed: false` flag (or `skip_embedding: true`) and keep `embedding` strictly `list | None`.

### 2.7 Duplicate language-detection block in crawler
- **Where:** `1crawlweb.py:246-257` — `if detected_language != LANGUAGE.strip():` is tested twice; the first hit logs `SKIP_LANGUAGE` without `continue`, the second logs again and skips. Harmless duplication now, but a classic edit-slip site.
- **Fix:** Collapse to a single check; sample more than the first 1000 chars (or skip detection for pages whose URL matched an allow-list) to reduce false skips on code-heavy pages.

### 2.8 `1crawlpdf.py` treats `CRAWL_URL` as a filesystem path
- **Where:** `1crawlpdf.py:20` — `JOB_CWD = Path(CRAWL_URL) / base_dir`, while `run_settings.py` currently holds `CRAWL_URL = "https://helpx.adobe.com/..."` with `PARSER = "crawlpdf"`.
- **Problem:** The current run settings would make the PDF pipeline look for `https:/helpx.adobe.com/.../connect/connect.pdf` as a local path. The field is overloaded: URL for web crawls, directory for PDF crawls, with no validation.
- **Fix:** Split into `CRAWL_URL` and `SOURCE_DIR` (or validate per-parser in `run_context` and fail fast with a clear message). Also note `trim_pdf_and_write_toc_json` mixes 0-based `range(start, end)` with 1-based `drop(p + 1)` ranges — document the convention in `crawlpdfconfig.py` and add a unit test with a synthetic PDF.

### 2.9 Clean tab edits a config file nothing reads
- **Where:** `Get_and_Chunk/config/cleanconfig.py` (REMOVE_IMAGES, INPUT_DIRECTORY, …) vs `2markdown_cleanup.py` (imports only `markdown_utils` / `adobe_connect_cleanup`; never imports `cleanconfig`).
- **Problem:** The web app's Clean tab (schema `clean.schema.json`, config URL `Get_and_Chunk/config/cleanconfig.py`) presents settings that have zero effect. A user toggling "Remove images: off" will believe they changed pipeline behavior.
- **Fix:** Either wire `2markdown_cleanup.py` to read `cleanconfig.py` (make each cleanup pass toggleable — several functions in the pipeline are Adobe-Connect-specific and *should* be toggleable per source), or delete the config + tab fields until real settings exist.

### 2.10 Duplicated config files drift
- **Where:** `Get_and_Chunk/config/embedconfig.py` vs `VectorDB/config/embedconfig.py` (already whitespace-divergent); `Get_and_Chunk/config/vectorconfig.py` vs `VectorDB/config/vectorconfig.py`; `Web_App/config/defaults/*` is a third copy of everything.
- **Problem:** The UI edits the `VectorDB/` copies (per `embed-init.js`/`vector-init.js`), the chunker imports the `Get_and_Chunk/` copy (`3.00chunker.py:54` `from config.embedconfig import *`), and the two can disagree. The chunker doesn't appear to use anything from embedconfig or summaryconfig — those star-imports also pull `sqlalchemy` (see 2.11) into the chunker's import chain.
- **Fix:** One config per concern, one location (e.g., a top-level `config/` package). Delete the `Get_and_Chunk/config/{embed,vector}config.py` copies and remove the unused star-imports from the chunker. Keep `Web_App/config/defaults/` as the only sanctioned copy, and add a CI check (extend the existing vitest schema-coverage tests) that the defaults parse and match schema keys.

### 2.11 `summaryconfig.py` imports SQLAlchemy for nothing
- **Where:** `Get_and_Chunk/config/summaryconfig.py:2` — `from sqlalchemy import null` (unused; probably an autocomplete accident for the `TESTINGMODE` "Null"). It forces a heavyweight dependency on every consumer, including the chunker via star-import.
- **Fix:** Delete the import (also in `Web_App/config/defaults/summaryconfig.py`).

### 2.12 `pipelinemanager.py` skips the friendly-name enrichment steps
- **Where:** `pipelinemanager.py:47-53` — `PIPELINE_STEPS` jumps from chunker to summary; `3.01codename_to_friendlyname.py` / `3.02add_codefriendly_names2chunk.py` are never run, yet `embedding.py:305` embeds `code_friendly_name` and the DB schema has the column.
- **Fix:** Either add the 3.01/3.02 steps (behind a config flag) or document that they're manual. Also: the orchestrator runs `4.01summary.py` directly, but that file's own header says "Don't run this directly; use summary_wrapper_hf.py" — the manager will run summaries without the HF endpoint env vars the backend needs. Decide one entry point.

### 2.13 Web app dead references
- `Web_App/index.html:97` maps a `crawl_git` tab to `crawl_git.html`, which doesn't exist (no git-crawl tab is rendered, so this is latent).
- `Web_App/config/paths.json` points the Source tab CSV at `../pipeline/metadataconfig.csv` and the Vite proxy maps `/pipeline/` to `<repo>/pipeline/` (`vite.config.js:10`), but there is **no `pipeline/` directory** — the CSV lives at repo root (`metadataconfig.csv`). The Source tab's data table cannot load its CSV over HTTP.
- `Web_App/js/app-config.js:5` and `source-init.js` pywebview paths reference `../web/...` and `../Web_App/...` inconsistently; the pywebview runtime paths were clearly written for a different directory name (`web/`).
- **Fix:** Point paths.json/proxy at the real CSV location (or move the CSV into a `pipeline/` dir), delete the `crawl_git` mapping or add the page, and normalize the pywebview base paths (or drop pywebview support — see 4.4).

### 2.14 Saving the Embed tab can poison `MAX_EMBED_CHUNKS`
- **Where:** `embed.schema.json` declares `MAX_EMBED_CHUNKS` as `text`; `config-binder.js` `getControlValue` returns the raw string; `serializeValue` quotes strings.
- **Problem:** The current value `None` parses to `null`, renders as `""` (or the user types `None`), and on save is written as `MAX_EMBED_CHUNKS = ""` or `"None"` — a truthy string. `embedding.py:134` then sets `MAX_N = "None"`, and `min(MAX_N, total)` at line 141 raises `TypeError`, killing the embed step.
- **Fix:** Make the field a nullable number in the schema; in `serializeValue`, map `''` for nullable numbers to `None`. Add the same treatment for `DEVICE_ID` (nullable) and audit other nullable keys.

### 2.15 `config-parser.js` round-trip hazards for expressions
- **Where:** `config-parser.js:45-47` marks unquoted RHS (lists, `Path(...)`, f-strings, tuples like `TOC_RANGE = (1, 69)`) as expressions but stores them as *strings* in the model; `collectValues()` returns every writable key; `writeConfig` re-serializes with quotes.
- **Current mitigation:** the schemas mark list-valued keys `readOnly`, which keeps them out of the patch. But this is convention, not enforcement: any future schema field whose config value is an expression (e.g., `crawl_pdf`'s `TOC_RANGE`, `REMOVE_RANGES` if ever made editable) will be corrupted into a quoted string on first save.
- **Fix:** Have `writeConfig` refuse to serialize a value whose parsed form was `isExpression: true` unless the schema declares an explicit type; surface a UI warning instead. Add a vitest that round-trips every real config file through parse→write with an untouched model and asserts byte-equality.

### 2.16 Logger duplicates handlers/headers on reuse
- **Where:** `Logger/custom_logger.py:69-80`
- **Problem:** Every call re-opens the file with `mode="w"` and writes the header manually — fine for one setup per process, but each pipeline script uses the same logger name `"pipeline"`; when tests (or a future in-process orchestrator) call `setup_global_logger` repeatedly, old `FileHandler`s are cleared from the logger but never `close()`d (leaked fds on Windows lock the log file). The `["Date","Level","Message","TBD","TBD"]` header in `1crawlweb.py:38` also ships literal "TBD" columns.
- **Fix:** Close removed handlers; name loggers per script; fix the TBD headers.

---

## 3. Inefficiencies

### 3.1 Embedding is unbatched and re-embeds identical text (largest speed win)
- **Where:** `VectorDB/embedding.py:287-360`
- **Problems:**
  1. `encode()` is called once per chunk, then twice more per chunk for summaries — three GPU round-trips per chunk with batch size 1. SentenceTransformers gets 10–50× throughput from batched encode.
  2. `page_summary` is identical for every chunk in an H1 group, but is re-embedded once per chunk. For a 50-chunk page that's 50 identical forward passes.
  3. The per-chunk `logger.info` at INFO level writes 4+ log lines per chunk.
- **Fix:** Collect texts up front, deduplicate summary texts via a `{text: vector}` cache, call `embed_model.encode(list_of_texts, batch_size=64, show_progress_bar=True)` once per field, then scatter results back by index. Log per-100-chunks. Expected: minutes → seconds for typical corpora.

### 3.2 Summarization is serial with no retry/backoff
- **Where:** `4.01summary.py:289-327`
- **Problem:** One blocking LLM call per chunk; a 5k-chunk corpus at ~2 s/call is ~3 hours, and one transient endpoint error writes `chunk_summary = ""` permanently (no retry, no "resume only missing summaries" mode).
- **Fix:** Add a small worker pool (e.g., `concurrent.futures.ThreadPoolExecutor(max_workers=4-8)` — HF TGI endpoints handle concurrency well), exponential-backoff retry on 429/5xx, and an idempotent resume mode that skips chunks whose `chunk_summary` is already non-empty. Persist progress incrementally (write JSON every N chunks) so a crash doesn't lose hours of paid inference.

### 3.3 Pipeline manager discards live output and imposes a global 1-hour cap
- **Where:** `pipelinemanager.py:70-77`
- **Problem:** `capture_output=True` buffers everything (no live progress; large crawls can buffer tens of MB), stdout is thrown away even on success, and `timeout=3600` kills legitimate long crawls/embeds.
- **Fix:** Stream child output to the logger line-by-line (`Popen` + iterate), make timeout per-step configurable, and log the child's stdout tail on failure alongside stderr.

### 3.4 Chunker re-reads and re-parses every file twice
- **Where:** `3.00chunker.py:233-273` (LlamaIndex reads all files) + `296-308` (each file re-opened and re-parsed by regex).
- **Fix:** Falls out of the 2.3 refactor — read each file once.

### 3.5 Crawler URL-list mode launches a full browser session per URL
- **Where:** `1crawlweb.py:458-467` — each URL calls `deep_crawl_urls()`, which creates a new `AsyncWebCrawler` (a Chromium launch) per URL, and also runs at configured `MAX_CRAWL_DEPTH` (not depth 0), so each "single URL" can fan out into a deep crawl. Contrast: sitemap mode correctly forces `MAX_CRAWL_DEPTH = 0`.
- **Fix:** In URL-list mode, use `crawler.arun_many(urls, config=...)` inside one browser session with depth 0 (crawl4ai supports it), or at least reuse one `AsyncWebCrawler` across the loop. Decide explicitly whether URL-list mode should deep-crawl.

### 3.6 Web UI run flow holds one HTTP request open for the whole script
- **Where:** `vite.config.js` `createRunScriptApi` + `tab-init.js` run button
- **Problem:** `/api/run-script` responds only when the child exits; a multi-hour crawl rides on a single fetch (browser/proxy timeouts, dev-server restarts kill the association). Log polling papers over it, but the completion result is lost if the request drops. Output also accumulates unbounded in a string.
- **Fix:** Make run-script return immediately with a run id; add `/api/run-status?script=` (exit code + done flag), and let the existing log polling drive the UI until status reports completion. Cap the retained output buffer.

### 3.7 Repo hygiene / weight
- `MkDocs/site/` (built docs incl. minified lunr bundles) and `Web_App/config/backups/*.bak` (12 timestamped backups) are committed. `.hf_cache` is created relative to whatever CWD the wrapper runs from (`4.00summary_wrapper_hf.py:21`). `xxcrawlwebtest.py`, `2crawlwebtest.py`, `zzz_scratch.md`, and `Web_App/plan.md` are scratch artifacts alongside production scripts.
- **Fix:** Add `MkDocs/site/`, `Web_App/config/backups/`, `.hf_cache/` to `.gitignore` and remove from tracking; move scratch/test scripts into a `sandbox/` dir or delete. Also fix the typo'd filename `upsert_to_vectorydb.py` → `upsert_to_vectordb.py` (and the schema `runScript` reference).

---

## 4. Web app — correctness & architecture

### 4.1 `chunk-init.js` is a stale fork of `tab-init.js`
- **Where:** `Web_App/js/chunk/chunk-init.js` (335 lines) duplicates `createTabInit` almost verbatim but lacks the Stop button wiring and the abort controller that `tab-init.js` has. Every fix to the shared flow must be made twice, and the chunk tab already lags (no stop support).
- **Fix:** Replace with the 11-line `createTabInit({phase:'chunk', ...})` form used by every other tab. This deletes ~320 lines.

### 4.2 Boolean binding depends on control type
- **Where:** `config-binder.js:114-151` — booleans are written as the strings `'true'/'false'` into `control.value` and read back by string comparison. That works for `sl-select` but silently fails for `sl-switch`/`sl-checkbox` (which use `.checked`). Nothing validates that the schema's control expectation matches the HTML.
- **Fix:** Branch on `control.tagName` (or a `data-control` attribute) for boolean fields; add a vitest that mounts each tab fragment (jsdom) and asserts every schema key finds a control and round-trips a value.

### 4.3 Vite dev middleware: over-broad write + weak path checks
- **Where:** `vite.config.js` `createWriteProxy` allows `PUT` of **any `.py` file** under `Get_and_Chunk/` and `VectorDB/` — including the pipeline scripts themselves — and `createRunScriptApi` will then execute them. Combined, any process that can reach `localhost:5173` can achieve arbitrary code execution. The prefix check `normalized.startsWith(baseDir)` is also the classic prefix bug (`/foo` passes for baseDir `/fo`), though the join+normalize largely mitigates traversal.
- **Fix (dev-only but cheap):** restrict writes to `*/config/*.py` and `run_settings.py` allow-list; compare with `path.relative()` instead of `startsWith`; optionally require a session token header that the page injects. This matters because Vite may be started with `--host` someday.

### 4.4 Split-brain runtime: pywebview vs HTTP
- **Where:** `app-bridge.js`, `app-config.js`, `source-init.js`, `util.js` all carry dual pywebview/HTTP code paths; `util.js` (`SourceActions.saveCheckRows`) is pywebview-*only* and throws "pywebview save_file API is not available" in the browser, so the Source table's Save button is broken in the HTTP mode that everything else now targets ("No pywebview dependency" per the headers of tab-init/chunk-init).
- **Fix:** Pick HTTP as the single runtime (the Vite middleware already covers load/save/run). Route `SourceActions` saves through a `PUT` endpoint; delete the pywebview branches or isolate them behind one adapter module. This removes an entire class of "works in one shell, broken in the other" bugs (see also 2.13's inconsistent pywebview paths).

### 4.5 Shoelace from CDN
- **Where:** `index.html:9-13` — UI is unusable offline and version-pinned to a remote CDN while everything else is local-first.
- **Fix:** `npm install @shoelace-style/shoelace` in the vite project and self-host the assets in the build.

### 4.6 `datatables.js` size/config sprawl
- 683 lines with a frozen default config exposing ~9 button systems (yaml/qa/pdf/excel/email...) most of which are unused placeholders. Not a bug, but it's the least navigable file in the app.
- **Fix:** Split table-core vs button-registry; delete unused button configs until needed.

### 4.7 Missing tab schemas ↔ reality checks
- The vitest schema-coverage tests are a good foundation, but they validate schema↔default-config coverage only. Gaps found manually: `clean` schema fields map to an unused config (2.9), `summary` schema exposes `TESTINGMODE` as text (1.2/2.14 class), `crawl_pdf` schema exists but tuple/list fields can't be safely edited (2.15).
- **Fix:** Extend the test factory to (a) parse the *live* configs, not just defaults; (b) assert every schema field's type is representable by the parser (no `isExpression` values on writable fields); (c) assert `runScript` targets exist on disk.

---

## 5. Security & operational

1. **DB credentials hardcoded** in `VectorDB/config/vectorconfig.py` (password `5774`) and duplicated in `Get_and_Chunk/config/vectorconfig.py` and the Web_App defaults, all committed and editable/served via HTTP by the dev proxy. → Read `VECTOR_DB_PASSWORD` from an env var (`os.getenv`) with the config providing only non-secret defaults; scrub git history if this password matters.
2. **Windows-absolute paths in `run_settings.py`** (`C:\\GIT\\Z_Master_Rag\\...`) make the repo single-machine. → Support env-var override (`RAG_DATA_ROOT`) with the current values as fallback; `run_context.get_run_context()` is the single choke point, so this is a small change.
3. **`pgvector` index is never created** — `ensure_chunk_table` builds the table but no HNSW/IVFFlat index on the three vector columns, so every similarity query is a sequential scan. → Add `CREATE INDEX IF NOT EXISTS ... USING hnsw (embedding vector_cosine_ops)` (and for the summary columns you actually query) after upsert; document `maintenance_work_mem` needs.
4. **`ensure_provenance_table(conn)` called twice** (`upsert_to_vectorydb.py:378,380`) and `register_vector` imported twice — trivial cleanups.
5. **HF endpoint auto-pause** (`4.00summary_wrapper_hf.py`): good cost hygiene, but pause runs in `finally` even when the run failed mid-corpus; combined with 3.2's lack of resume, re-runs pay the 5-min cold start repeatedly. Resume-mode (3.2) addresses this.

---

## 6. Architecture improvements (medium-term)

1. **Single orchestrator + per-step artifacts contract.** Each step reads/writes `a_chunks.json` in place, so a failed run leaves the file in an ambiguous state (post-chunk? post-summary?). Adopt versioned artifacts (`a_chunks.chunked.json` → `a_chunks.summarized.json` → parquet) or add a `pipeline_stage` field in provenance that each step asserts before running. This makes resume logic and the UI's "what state am I in" display trivial.
2. **Replace `from config.X import *` with explicit imports** (crawler, chunker, summary, embedding all star-import). Star-imports are why the missing `SUMMARY_SETTINGS` (1.1) surfaced as a runtime `NameError` instead of an import error, and why `sqlalchemy` sneaks into the chunker. A tiny `config.py` loader with a dataclass per phase gives IDE/type checking and makes the web UI schema the *generated* artifact rather than a hand-maintained parallel structure.
3. **Make the web UI run the pipeline manager.** Tabs currently run individual scripts; there's no UI affordance for "run everything from crawl to upsert" even though `pipelinemanager.py` exists. Add it as a `runScript` on the home/source tab once 1.5/1.7 exit codes are trustworthy.
4. **Consolidate `Logger` CSV logs + UI log-tail** into JSONL: the CSV formatter escapes but the log-tail endpoint splits on raw newlines, so multi-line messages (tracebacks) render as broken rows in the UI. JSONL lines with a tiny client renderer fix both.
5. **Testing depth.** Existing tests: 4 Python test files (with all external deps stubbed) + JS schema-coverage/parser tests. None execute a real end-to-end slice, which is why 1.1–1.4 shipped. Add one "golden corpus" integration test: 2 small markdown files → chunk → (mock LLM) summarize → (CPU, tiny model or fake encoder) embed → assert parquet/JSON invariants (ids unique, prev/next intra-file, no `"false"` strings, vector dims consistent).
6. **Readme refresh.** `readme.md` still documents a Flask `app.py` plan, pip-era setup, and the old schema; the actual entry points (`run_settings.py`, `pipelinemanager.py`, Vite UI) are undocumented. A 20-line "how to run today" section would materially help.

---

## 7. Phased roadmap

### Phase 1 — Restore pipeline correctness (do first, ~1 day)
1. Fix `SUMMARY_SETTINGS`/`run_summary_backend` (1.1) and `TESTINGMODE=None` (1.2).
2. Fix testing-mode truncation (1.3) and embeddings-lost-without-parquet (1.4).
3. Make crawler and upsert exit codes honest (1.5, 1.7); fix filename collisions (1.6).
4. Delete the sqlalchemy import (2.11); fix `chunk_type` mismatch (2.1); guard padding (2.2).

### Phase 2 — Performance (~1 day)
5. Batch + dedupe embeddings (3.1).
6. Concurrency, retry, and resume for summaries (3.2).
7. Stream pipelinemanager output; configurable timeouts (3.3).
8. Single-browser URL-list crawling (3.5).

### Phase 3 — Web app integrity (~1–2 days)
9. Fold `chunk-init.js` into `createTabInit` (4.1); add Stop everywhere.
10. Fix paths.json `/pipeline/` mapping and dead `crawl_git` entry (2.13).
11. Nullable-number schema handling (`MAX_EMBED_CHUNKS`, `DEVICE_ID`) (2.14); expression write-guard + round-trip test (2.15).
12. Decide HTTP-only runtime; fix or remove pywebview paths and `SourceActions` save (4.4).
13. Wire or remove the Clean tab's config (2.9).
14. Async run API with status polling (3.6); write-proxy allow-list (4.3).

### Phase 4 — Consolidation (~2 days)
15. Single config location; kill duplicates; explicit imports (2.10, §6.2).
16. Secrets to env vars; portable paths (§5.1–5.2).
17. pgvector indexes + upsert verification query (§5.3, upsert TODO).
18. Golden-corpus integration test (§6.5); repo hygiene & `.gitignore` (3.7).
19. Chunker refactor off LlamaIndex node-pairing (2.3–2.5) — last, because it needs the integration test from #18 as a safety net.

---

## Appendix: minor nits (fix opportunistically)
- `1crawlweb.py:38` log header literal `"TBD"` columns.
- `3.00chunker.py:343` logs empty headings at ERROR level; should be INFO/WARNING.
- `4.01summary.py` `_prepend_metadata_to_small_chunk` uses `\r\n` joins (2.2).
- `embedding.py` top-level script body (no `main()`), so importing it runs the pipeline; wrap in `main()` + guard.
- `embedding.py:264` `emb_dim` may be unbound if the dimension probe raised (masked by blanket try/except).
- `upsert_to_vectorydb.py` filename typo ("vectory"); double `ensure_provenance_table`; unused `re`, `logging` imports.
- `summaryconfig.py:30` prompt typo "tive voice" → "Active voice".
- `pipelinemanager.py` `run_script` logs stderr only on failure; success stderr (warnings) vanish.
- `config-parser.js` doesn't handle triple-quoted multi-line strings spanning lines (single-line regex only) — fine today, worth a comment.
- `find_duplicate_headings.py`, `reposition_error_tables.py`, `html_tables_toCSV.py` appear to be manual one-off tools; move to a `tools/` dir to keep `common/` importable-only.
