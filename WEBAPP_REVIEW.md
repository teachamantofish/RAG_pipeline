# Web App Review: Suggested Changes

Date: 2026-10-24
Scope: `Web_App/` (UI, Vite dev-server middleware) and the parts of the pipeline it drives
(`run_settings.py`, `run_python_entry.py`, `Logger/`, phase scripts).
Goal under review: a visual RAG workflow where every phase has a UI with **live logging** and
**user-editable settings**, so nobody has to open code.

Context: `IMPROVEMENT_PLAN.md` (2026-07-07) was implemented. This review covers what is left,
what regressed, and what the goal still needs. Each suggestion has three parts:
the suggestion, a **Codex comment** (independent review), and a **Verdict** for Ben to accept or reject.

Legend: **Severity** = Bug / Portability / UX / Feature / Cleanup. **Effort** = S (< 1 h), M (half day), L (1+ day).

---

## A. Correctness bugs

### S1. Config strings get corrupted on save when they contain escapes or newlines
- **Severity:** Bug · **Effort:** S
- **Where:** `Web_App/js/config-parser.js:29-30` (parse), `:138-147` (`serializeValue`); `config-writer.js:57`
- **Problem:** `parseValue` returns the *raw* text between the quotes without unescaping, and
  `serializeValue` escapes it again. Verified with a round-trip script: `A = "C:\\data"` becomes
  `"C:\\\\\\\\data"` after two saves, and `B = "say \"hi\""` grows backslashes every save. A value with a
  newline is written as a literal line break, which is invalid Python. `D = "a" + "b"` is misparsed as one
  string `a" + "b`. Nothing breaks today only because no current editable string holds an escape.
- **Suggestion:** Unescape on parse (`\\`, `\"`, `\'`, `\n`, `\t`) and escape `\n`/`\t` on serialize. Make the
  string regex reject an unescaped closing quote in the middle, so concatenations fall back to `isExpression`.
  Treat `r"..."` as a string with no unescaping. Add a vitest that round-trips escaped and multi-line values.

**Codex comment:**
> **Priority: High.** Confirmed: `Web_App/js/config-parser.js:29–30` preserves escape sequences verbatim, while `:145` escapes backslashes again; the regex also accepts concatenation as one string. The proposed fixes help, but supporting only five escapes would still misinterpret valid Python literals, and `parseConfig` splits multiline literals into separate lines (`:78`). Define a supported literal subset, reject unsupported syntax explicitly, and test repeated parse/write cycles plus Python syntax validity.

**Verdict:** **Accept, with Codex's change. Do first.** Define an explicit supported-literal subset (plain/raw strings with standard escapes, numbers, bools, None). Anything else, including multi-line literals, is refused: keep the line and show a warning instead of half-parsing it. Add a test that parses and writes a value repeatedly, then checks the output with `python -c 'import ast; ast.parse(...)'`. This blocks S7 auto-save and S18.

---

### S2. The Run poll loop never ends if the server forgets the run
- **Severity:** Bug · **Effort:** S
- **Where:** `Web_App/js/tab-init.js:270-279`
- **Problem:** The loop exits only on `status.known && status.done`. If the Vite server restarts mid-run,
  `scriptRunStates` is empty, `/api/run-status` returns `known: false` forever, and the tab spins with the
  Run button disabled until the page is reloaded.
- **Suggestion:** Treat `known: false` after a successful start as "lost track of run" and stop polling.
  Also stop after N consecutive fetch failures. Show a clear message either way.

**Codex comment:**
> **Priority: High.** Confirmed: `Web_App/js/tab-init.js:270–278` exits only for a known, completed run, while `Web_App/vite/vite.config.js:499–502` returns `known: false` for missing state. Stop monitoring with an explicit “run status lost” message and bound consecutive failures, including request timeouts. Distinguish lost monitoring from a terminated job so users do not assume restarting is safe.

**Verdict:** **Accept.** Use Codex's wording: show "monitoring lost, the job may still be running". Do not imply the job has stopped.

---

### S3. A page reload loses the running job, and the UI then reports a 409
- **Severity:** Bug/UX · **Effort:** M
- **Where:** `tab-init.js` (state lives only in the click handler); `vite.config.js:715`
  (`FullReload(['**/*.html'])` reloads the page on *any* HTML edit)
- **Problem:** Hours-long crawls and summaries are normal. After any reload (manual, or FullReload firing
  while you edit a tab fragment), the tab forgets the run. Run looks available, and clicking it returns
  "Script is already running". The live log stops updating.
- **Suggestion:** On tab init, call `/api/run-status?script=<runScript>`. If it is running, re-attach the same
  poll loop and set the buttons to running/stop. Consider narrowing FullReload to `index.html`, or turning
  it off while a job runs.

**Codex comment:**
> **Priority: High.** Confirmed: initialization never checks run status, and the active script exists only in the button-handler closure (`Web_App/js/tab-init.js:124,229`). Reattach during initialization using a shared monitoring function, ideally with a run identifier to distinguish successive executions. Narrowing FullReload is optional and does not solve manual reloads; preserve fragment-refresh behavior unless there is a separate reason to change it.

**Verdict:** **Accept, with a run ID.** Have `/api/run-script` return a `runId`, and have status report it, so a reattached tab can't confuse two runs. Leave FullReload alone, because reattaching fixes both manual and automatic reloads.

---

### S4. The live log can show the wrong file: it ignores `RAG_LOG_DIR` and shows the previous run first
- **Severity:** Bug · **Effort:** S
- **Where:** `vite.config.js:601-624` (`resolveLogDir`) vs `Logger/custom_logger.py:36`
- **Problem:** The logger honors the `RAG_LOG_DIR` env var. The log-tail API only regex-parses
  `run_settings.py`, so with the override set the UI waits forever for a log that is being written somewhere
  else. Separately, heavy scripts (torch, crawl4ai) take seconds before `setup_global_logger` truncates the
  log. During that time the UI shows the **previous run's** log as if it were live.
- **Suggestion:** Resolve the log dir in one place. The runner should compute it (env first, then
  run_settings) and pass `RAG_LOG_DIR` explicitly to the child so both sides agree. In log-tail, ignore a
  log whose mtime is older than `state.startedAt` and show "waiting for this run's log" instead.

**Codex comment:**
> **Priority: High.** Confirmed: the logger reads `RAG_LOG_DIR` (`Logger/custom_logger.py:36`), but the tail API independently parses and alters the configured path (`Web_App/vite/vite.config.js:601–624`). It also reads any existing log without checking run ownership, whereas the logger truncates only when initialized (`Logger/custom_logger.py:71`). Resolve and retain the effective log path when starting the run; run-specific files are more reliable than an mtime comparison, particularly for wrappers and overlapping runs.

**Verdict:** **Accept, with Codex's change.** The runner resolves the effective log path once, at start (env first, then run_settings), passes it to the child as `RAG_LOG_DIR`, and stores it in the run state. log-tail reads `state.logPath` instead of re-deriving it. Keep the mtime check only as a fallback for runs that started before the server knew about them.

---

### S5. Stop does not kill the process tree
- **Severity:** Bug · **Effort:** S
- **Where:** `vite.config.js:574-577`
- **Problem:** `child.kill('SIGTERM')` signals only the `python run_python_entry.py` process. crawl4ai
  launches Chromium, and torch/HF may start workers. These can be orphaned and keep holding files or GPU
  memory. On Windows, `kill` is TerminateProcess on a single PID.
- **Suggestion:** On POSIX, spawn with `detached: true` and stop with `process.kill(-child.pid, 'SIGTERM')`,
  escalating to SIGKILL after a timeout. On Windows, use `taskkill /PID <pid> /T /F`.

**Codex comment:**
> **Priority: High.** Confirmed: Stop signals only the tracked child (`Web_App/vite/vite.config.js:576`), with no descendant handling or escalation. Process-group termination on POSIX and tree termination on Windows are reasonable, but graceful cancellation should precede forced termination because scripts rewrite shared artifacts. The HF wrapper also pauses its endpoint in `finally` (`Get_and_Chunk/4.00summary_wrapper_hf.py:89–97`), which abrupt termination can bypass.

**Verdict:** **Accept, with Codex's change.** Send SIGTERM to the process group, wait about 10 s, then SIGKILL. On Windows use `taskkill /T` (graceful first, then `/F`). Note: Python's default SIGTERM handling skips `finally`, so the HF wrapper also needs a SIGTERM handler that raises `SystemExit`. Otherwise Stop can leave a paid endpoint running.

---

### S6. Captured stdout/stderr arrives late (Python block buffering)
- **Severity:** Bug/UX · **Effort:** S
- **Where:** `vite.config.js:426-430`
- **Problem:** Piped Python stdout is block-buffered, so `state.output` lags behind and arrives in bursts.
  It is also only shown when the run ends. Errors that happen before the logger starts (import errors, bad
  paths) are therefore invisible during the run.
- **Suggestion:** Add `PYTHONUNBUFFERED: '1'` (and `PYTHONIOENCODING: 'utf-8'`) to the spawn env. Show
  stdout/stderr in the live view next to the CSV log (see S11).

**Codex comment:**
> **Priority: Medium.** The spawn environment supplies no explicit unbuffered setting, and live rendering ignores captured process output until completion (`Web_App/vite/vite.config.js:426–429`; `Web_App/js/tab-init.js:284`). However, Python logging handlers already flush records, so buffering does not explain all delayed visibility; hiding stdout/stderr is the more general problem. Set unbuffered UTF-8 output and display it during execution, while avoiding duplicate presentation of logger messages emitted to both destinations.

**Verdict:** **Accept.** Set `PYTHONUNBUFFERED`/`PYTHONIOENCODING` and stream stdout/stderr live. Codex is right that the logger also echoes to stdout, so put raw stdout in a separate collapsed "Console" pane rather than mixing it into the log view.

---

### S7. Run ignores unsaved edits
- **Severity:** Bug/UX · **Effort:** S
- **Where:** `tab-init.js:221` (Run handler) vs `isDirty()` at `:126`
- **Problem:** The script reads the config **on disk**. If the user changes a field and clicks Run without
  saving, the run uses the old values while the form shows the new ones. This defeats the "change settings
  without code" goal.
- **Suggestion:** If the form is dirty, offer "Save & run" (default), "Run saved config", or "Cancel". Or
  auto-save before running.

**Codex comment:**
> **Priority: High.** Confirmed: the Run handler posts only the script name and never invokes `isDirty`, validation, or saving (`Web_App/js/tab-init.js:221–262`). Prefer an explicit “Save & run” action that awaits successful validation and persistence, with a clearly labeled option to use saved settings. Include unsaved Source settings because they also affect execution, and fix S1 before automatically saving configurations.

**Verdict:** **Accept.** Make "Save & run" the primary action, with "Run saved settings" as the secondary one. Include the Source tab's dirty state. Ship it after S1.

---

### S8. Summary tab cannot reach the configured model
- **Severity:** Bug · **Effort:** S–M
- **Where:** `Web_App/config/summary.schema.json:3` (`runScript: Get_and_Chunk/4.01summary.py`);
  `Get_and_Chunk/common/summary_backends.py:33-45`; `summaryconfig.py:7`
- **Problem:** `CHUNK_SUMMARY_MODEL = "qwen3-next-80b-a3b-instruct-hmt"` has no backend prefix. Without
  `HF_ENDPOINT_URL` in the env, `_split_backend` routes it to the **OpenAI** API, which fails. The UI runs
  `4.01summary.py` directly, so `4.00summary_wrapper_hf.py` (which starts the endpoint, sets the env, and
  pauses it afterwards) never runs. Summary from the UI works only if Vite was started from a shell that
  already exported `HF_ENDPOINT_URL`.
- **Suggestion:** Point the Summary tab's `runScript` at `4.00summary_wrapper_hf.py`, or add a "Backend"
  select (huggingface / openai / ollama) that writes the `backend:model` prefix. Show the resolved backend
  in the log's first line.

**Codex comment:**
> **Priority: High.** The default routing mismatch is real (`Web_App/config/summary.schema.json:3`; `Get_and_Chunk/common/summary_backends.py:33–45`), but “works only with HF_ENDPOINT_URL” is overstated: model environment overrides and an OpenAI-compatible endpoint are also supported. A backend selector alone cannot supply HF endpoint configuration, while blindly selecting the wrapper hard-codes endpoint management and overrides the chunk model (`4.00summary_wrapper_hf.py:20,32`). Make backend and endpoint mode explicit, validate prerequisites, and account for the wrapper’s child log filename and cancellation cleanup.

**Verdict:** **Accept, with Codex's change. My wording overstated the problem.** Add a Backend select (huggingface-endpoint / openai-compatible / ollama) plus the fields each backend needs. Add a preflight check when Run is clicked that fails fast with a plain message ("HF_ENDPOINT_URL not set"). When the HF backend is chosen, route through the wrapper, and fix its child-log naming and cancellation (see S5).

---

### S9. The theme toggle starts in the wrong state
- **Severity:** Bug (cosmetic) · **Effort:** S
- **Where:** `Web_App/index.html:21` and `:40`
- **Problem:** The switch is rendered `checked` ("dark"), but the init script removes `sl-theme-dark`. The
  first click therefore does nothing visible. The choice is also not remembered.
- **Suggestion:** Derive the initial state from `localStorage` or `prefers-color-scheme`, set the switch and
  the body class together, and persist the choice on change.

**Codex comment:**
> **Priority: Low.** Confirmed: `Web_App/index.html:21` removes the dark class while `:40` renders the switch checked, so its first transition to unchecked leaves the appearance unchanged. Synchronize the switch and theme from one initial value, then persist changes. System preference is a sensible default when no saved preference exists; this remains cosmetic compared with execution correctness.

**Verdict:** **Accept, low priority.** Bundle it with S16.

---

## B. Portability (you are now on Linux)

### S10. `run_settings.py` holds Windows paths with quadrupled backslashes
- **Severity:** Portability/Bug · **Effort:** S
- **Where:** `run_settings.py:6,9`: `CWD = Path(r"C:\\\\GIT\\\\Z_Master_Rag\\\\Data")`
- **Problem:** Raw strings with four backslashes per separator came from an earlier save bug (git history
  shows the clean single-backslash version before). Windows tolerated it. On Linux, `Path("C:\\\\GIT...")` is
  a **relative** path, so data and logs would land in a directory literally named `C:\\\\GIT...` under the
  repo root.
- **Suggestion:** Default to repo-relative paths (or `RAG_DATA_ROOT`/`RAG_LOG_DIR`). On the Source tab,
  validate the paths: add a tiny `/api/path-info?path=` that reports exists/is-dir/writable, and show a
  red/green badge next to CWD and LOG_DIR.

**Codex comment:**
> **Priority: High.** Confirmed: `run_settings.py:8,11` contains Windows raw paths with repeated separators, which are relative paths on this Linux host. Environment overrides are not a complete fix: `Get_and_Chunk/common/run_context.py:20` honors `RAG_DATA_ROOT`, but embedding and upsert directly import `run_settings.CWD`. Centralize effective path resolution across phases and the UI; path validation should allow deliberately new output directories and distinguish creatability from existing-directory writability.

**Verdict:** **Accept, with Codex's change.** Clean `run_settings.py` now: repo-relative or proper single-backslash paths. Route every phase (embedding and upsert import `run_settings.CWD` directly) through one resolver, such as `run_context`. Path badges should distinguish "exists" from "will be created".

---

### S11. The runner uses system `python3`, not the project's environment
- **Severity:** Portability · **Effort:** S
- **Where:** `vite.config.js:424`
- **Problem:** `spawn('python3' | 'python')` takes whatever interpreter is first on PATH. The pipeline needs
  torch, crawl4ai, sentence-transformers and so on, so this works only when Vite is started from an
  activated environment.
- **Suggestion:** Resolve the interpreter from `RAG_PYTHON`, then `<repo>/.venv/bin/python`
  (`Scripts\python.exe` on Windows), then PATH. Log the interpreter path as the first line of every run.

**Codex comment:**
> **Priority: High.** Confirmed: `Web_App/vite/vite.config.js:424` chooses an interpreter name solely by platform, although an activated environment or globally installed dependencies can already make that work. Explicit `RAG_PYTHON` selection with a documented project-environment default is appropriate. An invalid explicit interpreter must fail clearly rather than silently falling through to PATH, and diagnostics should report the actual executable and required dependency checks.

**Verdict:** **Accept.** Use `RAG_PYTHON`, then `.venv`, then PATH. A `RAG_PYTHON` that is set but invalid must be a hard error. Log the interpreter path and version on the first line of every run.

---

## C. Live logging

### S12. Give logs their own panel instead of hijacking the config viewer
- **Severity:** UX · **Effort:** M
- **Where:** `tab-init.js:41-47`, `:281-303`
- **Problem:** During a run, the right-hand "File reader" (the raw config view) is overwritten with log
  text and stays that way until reload. The log itself is raw CSV (`"10-24 14:02","INFO","..."`) in a
  textarea, with no auto-scroll, level colors or filtering. The 3-row "Notes" box gets the same dump.
- **Suggestion:** Add a third region per tab, or a collapsible bottom drawer, for the **Run log**. Parse the
  CSV rows into time/level/message, color the levels, auto-scroll with "pause on scroll-up", add a level
  filter and a "copy log" button. Show a stdout/stderr pane under it (S6). Keep "File reader" for the config.

**Codex comment:**
> **Priority: Medium.** Confirmed: `Web_App/js/tab-init.js:41–46,281–303` replaces the config viewer with logs and duplicates them into Notes. “Until reload” is slightly overstated because Save and Revert also restore the configuration text. A dedicated log panel is justified, but start with readable output, copying, and controlled scrolling; CSV parsing must handle quoted multiline messages and additional columns emitted by `Logger/custom_logger.py:46–65`.

**Verdict:** **Accept, starting small.** Phase 1: a separate Run Log panel with readable rows, level colors, copy, and auto-scroll that pauses when you scroll up; the config viewer is never overwritten. Use a real CSV parser (quoted multi-line fields). Leave level filtering for later.

---

### S13. Stream logs incrementally instead of re-reading everything every 1.2 s
- **Severity:** Performance/UX · **Effort:** M
- **Where:** `vite.config.js:682-704` (reads the whole log file per poll), `:505-514` (`run-status` returns up to
  512 KB of output on every poll)
- **Problem:** On a long crawl or embed, every poll re-reads and re-sends the whole log and output, yet
  shows only 120 lines.
- **Suggestion:** Add an SSE endpoint `/api/run-stream?script=` that pushes new log lines and stdout chunks
  as they arrive (`fs.watch` or a byte-offset tail), plus a final `done` event with the exit code. That one
  endpoint replaces both polling loops. If SSE is too much, at least add `?since=<byteOffset>` and stop
  returning `output` until `done`.

**Codex comment:**
> **Priority: Medium.** The whole log is reread on every request, but only the selected tail is transmitted (`Web_App/vite/vite.config.js:682–702`); the claim that the entire log is resent is incorrect. Status does resend its bounded output buffer, whose limit is characters rather than bytes (`:21,436–440,512`). Incremental offsets are a smaller first step than SSE, provided truncation, UTF-8 boundaries, and run identity are handled; withholding all output until completion would conflict with S6.

**Verdict:** **Accept the offset tail; defer SSE.** Codex is right that only the tail is sent, so my claim was partly wrong. Add `?since=<offset>` for both the log and the console output, keyed by run ID so truncation and new runs are detected. Revisit SSE only if polling still feels laggy.

---

### S14. Progress and elapsed time
- **Severity:** UX · **Effort:** M
- **Problem:** Long phases show only a spinner. There is no elapsed time or progress, and no estimate of
  "how far along".
- **Suggestion:** Show elapsed time from `startedAt`. Agree on a log convention such as
  `PROGRESS <done>/<total> <label>` that the scripts already roughly emit (embedding batches, summary chunks,
  crawl pages), and render it as an `sl-progress-bar`.

**Codex comment:**
> **Priority: Medium.** Confirmed: the UI shows a loading button and log text without using the available `startedAt` field (`Web_App/js/tab-init.js:250–278`; `Web_App/vite/vite.config.js:513`). Elapsed time is straightforward, but progress requires additional instrumentation: summaries emit occasional completed counts, while embedding uses a library progress bar (`VectorDB/embedding.py:291–300`). Prefer structured progress events with attempted/succeeded/failed counts, and use indeterminate progress when crawling has no known total.

**Verdict:** **Accept elapsed time now; defer progress bars.** Elapsed time is a few lines of code. Structured `PROGRESS` events need changes in each script, so add them phase by phase, starting with summary (the longest step).

---

## D. Settings UX

### S15. Generate form controls from the schema instead of hand-writing each tab's HTML
- **Severity:** Cleanup/UX · **Effort:** L
- **Where:** `Web_App/{crawl,crawl_pdf,clean,chunk,summary,embed,vector}.html` vs `config/*.schema.json`
- **Problem:** Each field is defined twice: label, type and options in the schema, and again by hand in the
  HTML. Some already disagree: the chunk HTML has `min`/`max` that are missing from the schema, and summary
  prompts are single-line `sl-input`. Adding a setting means editing the config, the schema, the HTML and the
  defaults.
- **Suggestion:** Write a `renderForm(schema)` that builds Shoelace controls from `type`, `options`, `min`,
  `max`, `step`, `help`, `readOnly` and a new `section`/`multiline` key. Tab fragments shrink to a common
  shell. The binder's `findControl` fallback (hard-coded `#chunk-${id}`, `config-binder.js:107`) goes away.

**Codex comment:**
> **Priority: Medium.** Duplication is real, but the cited chunk-limit discrepancy is not: the numeric constraints in `Web_App/chunk.html:31–64` match `Web_App/config/chunk.schema.json`. Schema-generated controls would still reduce drift and support multiline prompts consistently. Migrate incrementally while preserving grouping, accessibility, read-only behavior, and validation; defaults remain a separate maintenance concern even after rendering is centralized.

**Verdict:** **Defer.** The chunk min/max example I cited was wrong (Codex checked). It is still a good refactor, but it is large. Do it when adding the S22 tabs, so new tabs are schema-generated from the start and old tabs migrate one at a time.

---

### S16. Clear action buttons and a visible dirty state
- **Severity:** UX · **Effort:** S
- **Where:** all tab fragments, e.g. `chunk.html:9-20`
- **Problem:** The buttons are icon-only, and only some have tooltips. "Revert" uses a **trash** icon, which
  reads as "delete". Nothing shows that a form has unsaved changes, and leaving the page loses them without
  warning.
- **Suggestion:** Add text labels or tooltips to every button (Save / Revert / Defaults / Run / Stop), use
  `arrow-counterclockwise` for Revert, and a different icon for Defaults. Enable Save only when dirty, put a
  dot on the tab label when dirty, and add a `beforeunload` guard.

**Codex comment:**
> **Priority: Medium.** Confirmed: `Web_App/chunk.html:9–19` has unlabeled icon buttons, and dirty detection is used only for Revert confirmation (`Web_App/js/tab-init.js:126–169`). Add accessible names, clearer icons, visible dirty state, and a conditional unload warning. Fix dirty detection first: serialization differences can flag untouched fields, while validation exceptions currently return “not dirty,” potentially suppressing warnings.

**Verdict:** **Accept.** Fix dirty detection first, as Codex notes (an exception currently reports "not dirty"), then add the labels, icons, dirty dot and `beforeunload` guard.

---

### S17. Source tab: restore the feeds picker or remove its dead code; tighten the fields
- **Severity:** Bug/UX · **Effort:** M
- **Where:** `source.html` (has no table), `index.html:482,512` (calls `loadSourceTab()`),
  `js/datatables.js:610-649`, `js/util.js`, `metadataconfig.csv`
- **Problem:** The tab is labelled "RAG Feeds", but `source.html` is now only the run_settings form. On
  every load, `loadSourceTab()` polls 20 times for a table that doesn't exist, then gives up silently.
  About 900 lines (datatables.js, util.js, custom_data_tables.css, button images) are dead.
  `metadataconfig.csv` is no longer reachable from the UI. `PARSER` is free text, although only
  `crawlweb|crawlpdf|crawlgit` are valid.
- **Suggestion:** Either (a) bring back a compact feeds table where clicking a row fills the METADATA form
  (this was the original intent and suits the "no code" goal), or (b) delete the dead code and rename the
  tab "Run Settings". In both cases, make PARSER a select and dim the crawl tabs that don't apply to it.

**Codex comment:**
> **Priority: Medium.** Confirmed: `Web_App/source.html` contains no table, while `Web_App/js/datatables.js:619–623` repeatedly waits for one; PARSER is free text despite the manager’s explicit mapping. Renaming the tab and removing obsolete initialization is the smallest coherent fix; restoring a picker is a separate workflow feature whose selection must populate the actual run settings. Do not simply delete `datatables.js` while `index.html:179–181` still references its global state, and validate the CSV’s trailing non-record content before restoring it.

**Verdict:** **Your decision. My recommendation: option (b) now.** Rename the tab to "Run Settings", remove the dead `loadSourceTab` wiring (together with the `sourceTabState` references in `index.html`), and make PARSER a select. Bring back a feeds picker later as a deliberate feature if you still pick sources from `metadataconfig.csv`.

**Status (2026-10-24): Done, option (b).** Ben chose removal. The tab is renamed "Run Settings". `datatables.js`, `util.js`, `custom_data_tables.css` and the button PNGs are deleted, along with the `/pipeline/` proxies and `paths.json`. The form still edits CWD and source metadata. PARSER is still a free-text field (not done yet).

---

### S18. Make prompt templates editable
- **Severity:** UX/Feature · **Effort:** M
- **Where:** `summaryconfig.py:32,40,47` (f-strings → shown disabled), `summary.html:124,136,143,149`
- **Problem:** Prompts are probably the setting users most want to tune, yet three of the four are f-strings
  (expressions), so the UI greys them out. The editable one is a single-line input.
- **Suggestion:** Store them as plain strings with `{size}`-style placeholders and call `.format()` at
  runtime, as `CHUNK_SUMMARY_PROMPT_TEMPLATE` already does. Render them as `sl-textarea`. This depends on S1
  for safe multi-line round-trips.

**Codex comment:**
> **Priority: Medium.** Confirmed: the three prompts are explicitly disabled by both schema and HTML, while the editable chunk prompt uses `sl-input` (`Web_App/summary.html:20,32,39,45`). Plain templates and textareas are appropriate, but every consumer must format the template: file and code prompts currently pass directly to backend settings (`summary_backends.py:69`; `4.01summary.py:364`). Validate allowed placeholders and literal braces before saving, remove the old multiline assignment completely, and address S1 first.

**Verdict:** **Accept, after S1.** Every consumer must call `.format()`, including the file and code prompts. Validate placeholders before saving.

---

## E. Workflow features

### S19. Add a pipeline status board on the home tab
- **Severity:** Feature · **Effort:** M
- **Problem:** Nothing shows "where am I in the pipeline" for the current CWD. You can't see which phases
  ran, when, whether they passed, or which artifacts exist. `home.html` is a placeholder (`#ball`, "Choose a
  workflow above!") and isn't even mapped as a tab.
- **Suggestion:** Add an Overview tab that lists each phase with its last run (time, duration, exit code,
  from the runner's state persisted to `Web_App/.runs.json`) and its artifact (md file count, `a_chunks.json`
  chunk count, parquet present, DB row count). Add status badges to the tab labels (idle / running / ok /
  failed).

**Codex comment:**
> **Priority: Medium.** Confirmed: `Web_App/home.html:3–4` is a placeholder and is absent from the tab registry; current run state records neither dataset identity nor completion time (`Web_App/vite/vite.config.js:433,453–458`). An overview is useful, but persisting the existing per-script map would wrongly associate results across datasets and settings changes. Store run identity, effective dataset, configuration snapshot or fingerprint, and completion metadata; artifact existence should not imply that outputs are current or valid.

**Verdict:** **Defer until S3 and S21 are done.** Codex is right that per-script state without a run ID, dataset and config fingerprint would show misleading results. Build the board on the run records those items introduce.

---

### S20. Add a per-phase output inspector
- **Severity:** Feature · **Effort:** M–L
- **Problem:** After a phase, the user has to open files to check quality. That is the core feedback loop for
  RAG tuning, and the UI currently ends at the exit code.
- **Suggestion:** Add read-only endpoints and small panels: Crawl lists the markdown files with sizes and
  previews; Chunk shows count, a token histogram, the smallest and largest chunks, and a random sample;
  Summary shows coverage (% of chunks with a summary) and samples; Embed shows count, dimensions and nulls;
  Upsert shows row count and a test similarity query.

**Codex comment:**
> **Priority: Medium.** Confirmed: the completion UI presents logs and exit status only (`Web_App/js/tab-init.js:281–303`), and existing APIs expose no phase-specific inspection. Start with bounded chunk and summary samples, which directly support tuning, before implementing every proposed inspector. Scope reads to the effective dataset, escape preview content, handle partially written artifacts, and define coverage around eligible chunks; similarity testing additionally needs the same embedding configuration used during ingestion.

**Verdict:** **Accept partially: chunk and summary inspectors first.** These answer "are my chunks and summaries any good?", the main RAG tuning loop. Use bounded samples and escaped previews, scoped to the effective CWD. Embed and DB inspectors come later.

---

### S21. Add Run all / Run from here, with one global job lock
- **Severity:** Feature/Bug · **Effort:** M
- **Where:** `vite.config.js:392` (lock is keyed per script); `Get_and_Chunk/pipelinemanager.py`
- **Problem:** Chunk and Summary can run at the same time, and both rewrite `a_chunks.json`. The server only
  blocks starting the *same* script twice. There is also no UI way to run the whole pipeline.
- **Suggestion:** Allow one pipeline job at a time (global lock, or one per CWD). Add "Run all" and "Run from
  this phase" buttons that call `pipelinemanager.py` with a start step. Show the active step on the status
  board (S19).

**Codex comment:**
> **Priority: High.** Confirmed: the lock is keyed by the submitted script string (`Web_App/vite/vite.config.js:392`), while chunking and summarization can write the same chunk file. Add the concurrency guard independently of orchestration; a global lock is simplest while shared configuration remains mutable, and canonicalize script identities to prevent alias bypasses. “Run from here” requires new manager support because `Get_and_Chunk/pipelinemanager.py:139–167` always starts with crawling and accepts no start-step argument.

**Verdict:** **Accept the global lock now (high priority); defer Run all.** One job at a time, with canonical script identities. Run all / Run from here needs a `--from-step` argument in `pipelinemanager.py` first.

---

### S22. Fill or hide the phases the UI doesn't cover
- **Severity:** Feature/Cleanup · **Effort:** M
- **Problem:** Scripts with no tab: `1crawlgit.py`, `3.01codename_to_friendlyname.py`,
  `3.02add_codefriendly_names2chunk.py`, and `4.02summary_eval.py`. The **Training Data** tab is an empty
  shell with no script.
- **Suggestion:** Hide Training Data until it does something. Add a Crawl Git tab and an "Enrich" tab
  (3.01/3.02) using the same schema pattern. Put Summary Eval on the Summary tab as a secondary run button.

**Codex comment:**
> **Priority: Medium.** The missing tabs and empty Training Data shell are confirmed (`Web_App/index.html:94–104`; `Web_App/training_data.html:1–12`). Hide the empty tab now, but adding buttons alone would expose unfinished integrations: `3.01codename_to_friendlyname.py:34–38` uses a hard-coded dataset, and `4.02summary_eval.py:333` calls `input()` despite the runner providing no stdin. Standardize those scripts’ configuration, noninteractive execution, logging, and prerequisites before exposing them.

**Verdict:** **Accept hiding Training Data now; defer the new tabs.** Before exposing 3.01, 3.02 and 4.02, make them configurable through run_settings and remove the `input()` call (confirmed at `4.02summary_eval.py:333`).

---

## F. Cleanup

### S23. Remove the pywebview runtime split
- **Severity:** Cleanup · **Effort:** M
- **Where:** `js/app-bridge.js`; `index.html:123-137, 215-262, 332-454` (`loadConfigForTab`, `testAPI`,
  `reloadConfigFile`, `initializeCrawlConfig`, pywebview preload); pywebview branches in
  `js/source/source-init.js:177-243`, `js/util.js`, `js/datatables.js:540-571`
- **Problem:** `IMPROVEMENT_PLAN.md` §4.4 chose HTTP-only, but about 400 lines of pywebview and legacy loader
  code remain. Most of it is unreachable in the browser and hides the real control flow.
- **Suggestion:** Delete it. Keep one `api.js` module (`getText`, `putText`, `runScript`, `stopScript`,
  `status`) that every tab uses.

**Codex comment:**
> **Priority: Low.** The runtime split remains, including active pywebview-first branches in `Web_App/js/source/source-init.js:185–241`, so it is not entirely unreachable code. `IMPROVEMENT_PLAN.md:196–198` recommends HTTP-only or an isolated adapter rather than proving desktop support was already retired. Consolidating browser I/O is sensible, but remove legacy globals and event hooks together and verify whether any supported desktop entry point still depends on them.

**Verdict:** **Your decision: is the pywebview desktop shell still in use?** If not (my assumption, since everything now runs through Vite), delete it in one pass together with S24. If yes, isolate it behind a single adapter module.

**Status (2026-10-24): Done.** No Python host for pywebview exists in the repo. `app-bridge.js` and `app-config.js` are deleted, and the pywebview branches are removed from `index.html` and `source-init.js`.

---

### S24. Replace `index.html`'s inline script with a tab registry
- **Severity:** Cleanup · **Effort:** S
- **Where:** `index.html:92-515`
- **Problem:** The tab → html → module → init-function wiring is spread across three objects and an 8-way
  `if (tab === ...)` import chain in an inline script.
- **Suggestion:** Move it to `js/app.js` with one registry:
  `{ crawl: { html: 'crawl.html', schema: 'config/crawl.schema.json', config: 'Get_and_Chunk/config/crawlconfig.py' } }`.
  With `createTabInit`, the seven 11-line `*-init.js` files become unnecessary, because the registry entry
  is enough.

**Codex comment:**
> **Priority: Low.** Confirmed: `Web_App/index.html:94–115,271–318` duplicates tab mappings and initializer dispatch, and the phase modules mostly wrap `createTabInit`. A single registry would simplify this, but use the schema’s existing `configFile` rather than creating another competing configuration mapping. Preserve Source’s custom initializer and the `crawl_pdf` versus `crawl-pdf` naming distinction, and guard against duplicate asynchronous initialization.

**Verdict:** **Accept, low priority.** Bundle it with S23. Use each schema's existing `configFile` as the single source for the config path.

**Status (2026-10-24): Partly done with S23.** `index.html` now uses one `tabs` registry and a generic loader. The per-tab `*-init.js` wrappers remain.

---

### S25. Delete the Vite starter leftovers
- **Severity:** Cleanup · **Effort:** S
- **Where:** `Web_App/vite/src/{counter.ts,main.ts,style.css,typescript.svg}`, `vite/public/vite.svg`,
  `vite/tsconfig.json`, `"build": "tsc && vite build"`, `index.html:14`,
  `vite.config.js:711` (comment "your source is web/"), `Web_App/zzz_scratch.md`, `Web_App/plan.md`
- **Suggestion:** Remove the template files and the `tsc` step, and fix the stale comments. This is small,
  but it removes confusion about what is real.

**Codex comment:**
> **Priority: Low.** Only partially agree: `counter.ts` and template artwork are leftovers, but `Web_App/vite/src/main.ts:1–15` contains intentional HMR wiring and is loaded by `Web_App/index.html:14`. Remove genuinely unused assets, then deliberately retain or replace that entry before deleting TypeScript configuration and its build check. `Web_App/plan.md` also records design decisions, so archive or retain useful documentation instead of treating every listed file as disposable scaffolding.

**Verdict:** **Accept, with Codex's correction.** Keep `vite/src/main.ts` (it is real HMR wiring, confirmed). Delete only `counter.ts`, the template SVGs and `src/style.css`. Move `plan.md` and `zzz_scratch.md` to `docs/` or delete them, as you prefer.

---

### S26. Split and tidy `vite.config.js`
- **Severity:** Cleanup/Security · **Effort:** M
- **Where:** `vite.config.js` (744 lines, about 10 near-identical middlewares)
- **Problem:** Body reading, JSON responses and the prefix allow-lists are copy-pasted in each middleware.
  `run-script` allows *any* `.py` under `Get_and_Chunk/` or `VectorDB/` (including tests and one-off
  tools), and it checks traversal with `startsWith(repoRoot)` (`:409`), even though `isInsideDir` exists.
- **Suggestion:** Move the code into `vite/server/{files,run,logs}.js` with shared `readBody` / `sendJson`
  helpers. Build the run allow-list from the `runScript` values in the schemas, and use `isInsideDir`
  everywhere.

**Codex comment:**
> **Priority: High.** The execution allow-list is weaker than described: `Web_App/vite/vite.config.js:399–419` checks the submitted prefix and existence but does not even enforce a `.py` extension, and traversal can escape the intended script directories within the repository. The Python launcher does independently reject paths outside the repository (`run_python_entry.py:20–31`), so the sibling-prefix check is not by itself a demonstrated external-code execution path. Use a canonical explicit entry-point allow-list and shared bounded request handling now; extracting modules is secondary, and a schema-derived list must accommodate intentional wrapper and orchestration actions.

**Verdict:** **Accept the allow-list part now (high priority); refactor later.** Replace the prefix checks with an explicit list of runnable entry points (schema `runScript` values plus the wrapper and the manager), require `.py`, and use `isInsideDir`. Splitting the file into modules can wait.

---

### S27. Longer term: run jobs in a small Python service, not inside the Vite dev server
- **Severity:** Architecture · **Effort:** L
- **Problem:** Jobs are children of the Vite dev server. Restarting Vite, which happens often during UI
  work, kills them or breaks their stdout pipes. Run history is lost (S2, S3). Python configs are parsed with
  JS regexes, which is the source of the S1 class of bugs.
- **Suggestion:** Move the API (config read/write, run/stop/status/stream, inspectors) into a small FastAPI
  app in the repo's Python env. Config parsing and writing would use Python's `ast` and
  `tokenize`-preserving rewrites. Jobs would outlive UI reloads, and the UI would become a static build that
  Vite only serves in dev. Do this only if the UI keeps growing. S2–S6 are the cheap fixes for today.

**Codex comment:**
> **Priority: Low.** The coupling and volatile state are confirmed by module-level maps and subprocess spawning in `Web_App/vite/vite.config.js:18–20,426–459`, but server restart does not necessarily kill every child. A separate Python service could isolate jobs from UI development and improve Python-literal handling, yet it still needs explicit persistence, supervision, cancellation, and recovery to survive its own restart. AST parsing alone does not preserve source formatting; defer this migration until operational needs justify it, and fix the immediate lifecycle and serialization bugs first.

**Verdict:** **Defer.** Revisit if S2–S6 and S21 still leave jobs fragile, or if the UI grows a lot (inspectors, status board). Don't start it now.

---

## Notes
- The web test suite (`npx vitest run`) was not run during this review because a local permission rule
  blocks reading `node_modules`. Run it before acting on S1 and S15.
- Codex reviewed the file in a read-only sandbox (`codex exec -s read-only`) and changed no files. It
  corrected four of my claims: S8 overstated, S12 "until reload" overstated, S13 (only the tail is sent),
  S15 (the chunk min/max example was wrong). It also found the S25 `main.ts` exception and the S26 `.py`
  extension gap. The verdicts take these corrections into account.

## Verdict summary
- **Do now (correctness, mostly S effort):** S1 → S7, S2, S3, S4, S5, S6, S8, S10, S11, S21 (lock only),
  S26 (allow-list only)
- **Next (live logging and settings UX):** S12, S13 (offset tail), S14 (elapsed only), S16, S18, S9, S25
- **Then (features):** S20 (chunk/summary inspectors), S19, S22 (hide Training Data now; tabs later)
- **Your decision:** S17 (rename to Run Settings vs. restore feeds picker), S23 (is pywebview still used?)
- **Defer:** S15, S24 (bundle with S23), S27
