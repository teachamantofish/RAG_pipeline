---
id: C:/GIT/topomorph/apps/rag-master/Web_App/plan.md
type: plan
title: Web App
name: plan
description: Rebuild only the **chunk phase UI** as a reusable pattern for later tabs, while keeping **non-config Python code untouched**. The plan introduces a UI schema sidecar in `Web_App/config/`, modular chunk JS in `Web_App/js/`, and scoped CSS in `Web_App/css/` so index-level duplication drops and CDN/script loading is centralized once. Chunk controls become strongly bound to `chunkerconfig` values through a **parser → binder → strict validator** pipeline, with **minimal-diff write-back** to preserve comments/order/spacing in the Python config. Help text is shown via per-field **?** icons using one shared modal, sourced from curated schema descriptions. **Browser/Vite is primary** for this phase, with tests proving every rendered chunk control is mapped to a config key (or explicitly read-only/derived).
resource: /topomorph/apps/rag-master/Web_App/plan.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, web-app]
domain: tools
tokens: 3920
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Plan: Chunk Tab Config-Driven UI

> **Status:** DRAFT — approved decisions locked, ready for implementation.

## Goal

Rebuild only the **chunk phase UI** as a reusable pattern for later tabs, while keeping **non-config Python code untouched**. The plan introduces a UI schema sidecar in `Web_App/config/`, modular chunk JS in `Web_App/js/`, and scoped CSS in `Web_App/css/` so index-level duplication drops and CDN/script loading is centralized once. Chunk controls become strongly bound to `chunkerconfig` values through a **parser → binder → strict validator** pipeline, with **minimal-diff write-back** to preserve comments/order/spacing in the Python config. Help text is shown via per-field **?** icons using one shared modal, sourced from curated schema descriptions. **Browser/Vite is primary** for this phase, with tests proving every rendered chunk control is mapped to a config key (or explicitly read-only/derived).

---

## Decisions (locked)

| Decision | Choice |
|---|---|
| Schema source | JSON sidecar per phase in `Web_App/config/` |
| Chunk milestone behavior | Full round-trip now (read + edit + save to Python config) |
| Help content | Curated schema descriptions, shown via per-field ? icon + shared modal |
| Runtime target | Browser / Vite first |
| Path standard | `Get_and_Chunk/config/` (match this repo layout) |
| Unsupported types | Read-only fallback control with tooltip |
| Validation | Strict — block save on errors |
| Python write formatting | Minimal-diff assignment-value replacement only |
| Test stack | Vitest + jsdom unit tests |
| **pywebview dependency** | **NONE — all I/O uses standard HTTP (GET to load, PUT to save). The app must work fully in a browser without pywebview.** |

---

## Constraints

- **DO NOT** change any Python code in any directory except config files.
- Config file format/structure may change but **values must stay the same**.
- **No pywebview dependency.** The UI must be fully functional (load, edit, save) in a standard browser via the Vite dev server. The Vite dev server provides a write-capable PUT endpoint for saving config files to disk. AppBridge/pywebview code may remain in the codebase for legacy tabs but new config-driven tabs must not depend on it.
- All CDN assets referenced **once** (in index.html shell).
- Tab fragment files must be **small and clean** — no inline styles, no duplicated scripts.
- JS must be modular and live in `Web_App/js/`.
- CSS must live in `Web_App/css/`. No inline styles.

### Data flow rules

1. **On tab load:** The UI reads the config file from disk and populates every control with the **current saved values**. These are the defaults the user sees.
2. **Editing is in-memory only.** Changing a control updates the in-memory model but does **not** write anything to the config file.
3. **Save button:** Only when the user clicks Save are the in-memory values validated, serialized, and written back to the config file on disk.
4. **Reload / Refresh button:** Re-reads the config file from disk and replaces all control values with the **last saved state**. Any unsaved in-memory edits are discarded.

---

## Architecture

```
Web_App/
├── index.html              # Shell: CDN refs, tab nav, fragment loader, shared modal
├── chunk.html              # Fragment: minimal markup only, scoped IDs
├── config/
│   ├── paths.json          # (existing) app path metadata
│   └── chunk.schema.json   # NEW: field definitions, types, options, help, validation
├── js/
│   ├── app-bridge.js       # (existing) pywebview bridge wrapper
│   ├── app-config.js       # (existing) app path metadata loader
│   ├── config-parser.js    # NEW: Python config text → typed JS object (reusable)
│   ├── config-writer.js    # NEW: typed JS object → minimal-diff Python text (reusable)
│   ├── config-binder.js    # NEW: schema + model ↔ DOM controls (reusable)
│   ├── help-modal.js       # NEW: shared ? icon + modal wiring (reusable)
│   └── chunk/
│       └── chunk-init.js   # NEW: chunk tab lifecycle (load schema, parse, bind, actions)
├── css/
│   └── config-form.css     # NEW: form/control/help styles (shared across future tabs)
└── vite/
    ├── vite.config.js      # EDIT: add Vitest config
    ├── package.json         # EDIT: add vitest devDependency + test script
    └── tests/
        └── chunk/
            ├── config-parser.test.js   # NEW
            ├── config-binder.test.js   # NEW
            └── schema-coverage.test.js # NEW
```

### Schema file format (`chunk.schema.json`)

```jsonc
{
  "configFile": "Get_and_Chunk/config/chunkerconfig.py",
  "fields": [
    {
      "key": "TOKENIZER",
      "label": "Tokenizer",
      "type": "text",
      "readOnly": true,
      "readOnlyReason": "Python expression (tiktoken object) — edit in config file directly.",
      "help": "The tiktoken encoding used to count tokens per chunk. Currently set via tiktoken.get_encoding(\"cl100k_base\")."
    },
    {
      "key": "MAX_TOKENS_FOR_NODE",
      "label": "Max Tokens per Node",
      "type": "number",
      "min": 1,
      "help": "Maximum token count allowed in a single chunk node before splitting."
    },
    {
      "key": "CHUNK_SIZE_RANGE",
      "label": "Chunk Size Range",
      "type": "text",
      "readOnly": true,
      "readOnlyReason": "Derived from MAX_TOKENS_FOR_NODE — changes automatically.",
      "help": "f-string expression that resolves to the max token constraint string."
    },
    {
      "key": "CHUNK_MODEL",
      "label": "Chunk Model",
      "type": "select",
      "options": ["", "heading-safe", "greedy", "fixed"],
      "help": "Chunking strategy. 'heading-safe' preserves heading boundaries; 'greedy' maximizes chunk size; 'fixed' uses fixed-size windows."
    },
    {
      "key": "CODE_LENGTH",
      "label": "Code Length",
      "type": "number",
      "min": 1,
      "max": 50,
      "help": "Minimum number of lines for a code block to be treated as a standalone extractable unit."
    },
    {
      "key": "KEYWORD_DENSITY",
      "label": "Keyword Density",
      "type": "number",
      "step": 0.01,
      "min": 0,
      "max": 1,
      "help": "Threshold ratio of keyword tokens to total tokens. Chunks above this density may be flagged or handled differently."
    },
    {
      "key": "ENABLE_CODE_EXTRACTION",
      "label": "Enable Code Extraction",
      "type": "boolean",
      "help": "When enabled, code blocks meeting the CODE_LENGTH threshold are extracted into separate chunks."
    },
    {
      "key": "OUTPUT_NAME",
      "label": "Output Name",
      "type": "text",
      "help": "Base filename for the chunker output JSON file."
    }
  ]
}
```

### Key modules

#### `config-parser.js` (reusable across all tabs)

- Input: raw Python config file text.
- Output: `{ key: value }` object with typed values (bool, number, string, null, expression-string).
- Strategy: line-by-line regex for `KEY = value` assignments. Handles `True/False/None`, quoted strings, numbers, f-strings, and object expressions.
- Preserves original line text for write-back diffing.

#### `config-writer.js` (reusable across all tabs)

- Input: original file text + `{ key: newValue }` patch object.
- Output: updated file text with only assignment RHS replaced.
- Strategy: for each patched key, find the line matching `^KEY\s*=`, replace only the value portion, preserve inline comment. Skip read-only keys.
- Guarantees: comments, blank lines, ordering, and spacing are unchanged for unpatched keys.

#### `config-binder.js` (reusable across all tabs)

- Input: schema JSON + parsed config model + DOM container.
- Behavior: for each schema field, find/create the matching DOM control, set its value from the model, attach change listeners that update the model, and render a ? help icon wired to the shared modal.
- Handles: `boolean` → `sl-radio-group` (True/False), `select` → `sl-select`, `number` → `sl-input[type=number]` with min/max/step, `text` → `sl-input`, `readOnly` → disabled control with tooltip.

#### `help-modal.js` (reusable)

- Single `<sl-dialog>` in `index.html` shell, reused for all tabs.
- `showHelp(title, text)` API updates dialog title/body and opens it.
- ? icon buttons call `showHelp(field.label, field.help)`.

#### `chunk-init.js` (chunk-specific lifecycle)

- Called after chunk fragment is injected into DOM.
- Fetches `config/chunk.schema.json`.
- Loads `chunkerconfig.py` text via AppBridge or fetch.
- Parses config, binds to controls, wires save/reload/run buttons.
- Save: collects model → validates → writes back → persists via bridge/API.

---

## Implementation Steps

### Step 1 — Shell cleanup (`index.html`)

- Centralize all CDN `<link>` and `<script>` tags — remove any duplicated in fragments.
- Add a shared `<sl-dialog id="help-dialog">` for field help.
- Add `<script type="module">` imports for new shared JS modules.
- Extend `loadTab()` to call per-tab init function after fragment injection (keyed by tab name).
- Fix config path mapping to use `Get_and_Chunk/config/` instead of `../pipeline/config/`.

### Step 2 — Chunk schema (`config/chunk.schema.json`)

- Create schema file with all 8 chunkerconfig keys.
- Each field entry: key, label, type, options (if select), min/max/step (if number), readOnly flag, readOnlyReason, help text.
- Mark `TOKENIZER` and `CHUNK_SIZE_RANGE` as read-only with explanations.

### Step 3 — Shared JS modules

- `js/config-parser.js` — Python text → typed model.
- `js/config-writer.js` — model patch → minimal-diff Python text.
- `js/config-binder.js` — schema + model ↔ DOM controls.
- `js/help-modal.js` — shared modal wiring.

### Step 4 — Chunk fragment refactor (`chunk.html`)

- Strip all inline styles.
- Remove any `<script>` or `<link>` tags (moved to shell).
- Keep clean Shoelace form markup with stable IDs (prefixed `chunk-` to avoid cross-tab collisions).
- Add `<sl-icon-button name="question-circle">` next to each control for help.

### Step 5 — Chunk tab init (`js/chunk/chunk-init.js`)

- Fetch schema, load config, parse, bind, wire save/reload/run.
- Save handler: collect values → validate against schema rules → if errors, show field-level messages and block save → if valid, write back and persist.

### Step 6 — Scoped CSS (`css/config-form.css`)

- Form layout, control spacing, help icon positioning, radio group alignment, read-only control styling, validation error states.
- No hard-coded colors — use existing Shoelace/design tokens.

### Step 7 — Path alignment

- Update tab → config path mapping in `index.html` to `Get_and_Chunk/config/chunkerconfig.py`.
- Update Vite proxy config if needed for dev-mode file serving.

### Step 8 — Vitest setup + tests

- Add `vitest` + `jsdom` to `vite/package.json` devDependencies.
- Add `test` block to `vite/vite.config.js`.
- Create test files under `vite/tests/chunk/`.

### Step 9 — Regression checklist (manual)

See verification section below.

---

## Test Plan

### Automated (Vitest + jsdom)

#### `config-parser.test.js`
| Assertion | Input | Expected |
|---|---|---|
| Parses bool True | `ENABLE_CODE_EXTRACTION = True` | `{ ENABLE_CODE_EXTRACTION: true }` |
| Parses bool False | `ENABLE_CODE_EXTRACTION = False` | `{ ENABLE_CODE_EXTRACTION: false }` |
| Parses int | `CODE_LENGTH = 10` | `{ CODE_LENGTH: 10 }` |
| Parses float | `KEYWORD_DENSITY = 0.15` | `{ KEYWORD_DENSITY: 0.15 }` |
| Parses quoted string | `OUTPUT_NAME = "a_chunks"` | `{ OUTPUT_NAME: "a_chunks" }` |
| Parses None | `MAX_EMBED_CHUNKS = None` | `{ MAX_EMBED_CHUNKS: null }` |
| Parses expression (raw) | `TOKENIZER = tiktoken.get_encoding(...)` | `{ TOKENIZER: "tiktoken.get_encoding(\"cl100k_base\")" }` (raw string) |
| Preserves comment text | full config file | line map includes inline comments |
| Ignores comment-only lines | `# this is a comment` | not in output model |

#### `config-binder.test.js`
| Assertion | Setup | Expected |
|---|---|---|
| Boolean field renders radio group | schema type=boolean, value=true | radio "True" is checked |
| Select field renders options | schema type=select, options=[...] | sl-select has matching sl-option children |
| Number field has min/max/step | schema type=number, min=1, max=50 | sl-input attributes match |
| Read-only field is disabled | schema readOnly=true | control has `disabled` attribute |
| Help icon exists per field | any field | ? icon button is sibling of control |
| Value change updates model | user sets number input to 20 | model[key] === 20 |

#### `schema-coverage.test.js`
| Assertion | Setup | Expected |
|---|---|---|
| Every schema field has a DOM control | load schema + render chunk fragment | `document.querySelector` finds each control |
| Every config key is in schema or explicitly excluded | parse chunkerconfig + load schema | all keys accounted for |
| No orphan controls | scan fragment for data-config-key attrs | each matches a schema field |
| Round-trip preserves values | parse → bind → collect → write | output text values match input |
| Round-trip preserves comments | parse + write unmodified | output text === input text |

### Manual Regression Checklist

- [ ] Load chunk tab → all controls show values from `chunkerconfig.py`
- [ ] Read-only fields (TOKENIZER, CHUNK_SIZE_RANGE) show values but cannot be edited
- [ ] Read-only tooltip explains why field is locked
- [ ] Click ? icon on any field → shared modal opens with help text
- [ ] Change a boolean → radio switches, model updates
- [ ] Change a select → dropdown value updates model
- [ ] Change a number → respects min/max/step constraints
- [ ] Enter invalid number (out of range) → field shows error, save blocked
- [ ] Click Save with valid data → `chunkerconfig.py` is updated
- [ ] After save, open `chunkerconfig.py` → only changed values differ, comments/spacing intact
- [ ] Click Reload → controls revert to file values
- [ ] No inline styles remain in `chunk.html`
- [ ] No duplicate CDN references across shell + fragment
- [ ] All Vitest tests pass: `npm test` in `vite/`

---

## Field ↔ Config Mapping (chunk)

| Config Key | Python Type | UI Control | Editable | Notes |
|---|---|---|---|---|
| `TOKENIZER` | expression | `sl-input` (disabled) | Read-only | tiktoken object; show raw expression |
| `MAX_TOKENS_FOR_NODE` | `int` | `sl-input[type=number]` | Yes | |
| `CHUNK_SIZE_RANGE` | derived f-string | `sl-input` (disabled) | Read-only | Auto-derived from MAX_TOKENS_FOR_NODE |
| `CHUNK_MODEL` | `str` enum | `sl-select` | Yes | Options: "", "heading-safe", "greedy", "fixed" |
| `CODE_LENGTH` | `int` | `sl-input[type=number]` | Yes | min=1, max=50 |
| `KEYWORD_DENSITY` | `float` | `sl-input[type=number]` | Yes | step=0.01, min=0, max=1 |
| `ENABLE_CODE_EXTRACTION` | `bool` | `sl-radio-group` | Yes | True / False |
| `OUTPUT_NAME` | `str` | `sl-input` | Yes | |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Schema/config drift | Schema-coverage test fails if new keys appear in `.py` without schema entry |
| Minimal-diff writer corrupts file | Round-trip test asserts byte-identical output for unmodified config |
| Expression values can't be losslessly edited | Read-only fallback + tooltip; user edits `.py` directly for these |
| Cross-tab ID collisions | All chunk IDs prefixed `chunk-`; binder scopes queries to `#tab-content-chunk` |
| pywebview bridge not available in browser mode | AppBridge already has fetch fallback; tests run in jsdom (no bridge needed) |

---

## Future Phases (out of scope for this milestone)

- Repeat pattern for embed, crawl, clean, summary, vector tabs.
- Optional: build-time Python AST extractor to auto-generate/update schema JSON from config `.py` comments.
- pywebview parity pass once browser/Vite is stable.
- E2e Playwright smoke tests.
