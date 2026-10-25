/**
 * source-init.js - Source tab lifecycle controller for run_settings.py.
 */

const RUN_SETTINGS_HTTP_URL = '/api/run-settings';
const RUN_SETTINGS_DEFAULT_HTTP_URL = '/config/defaults/run_settings.py';

const METADATA_KEYS = [
  'ID',
  'PARSER',
  'CRAWL_URL',
  'BASE_DIR',
  'METADATA_TITLE',
  'METADATA_AUTHOR',
  'METADATA_CATEGORY',
  'METADATA_DESCRIPTION',
  'TAGS',
  'METADATA_DATE',
  'PAGES',
];

const FIELD_MAP = {
  CWD: '#source-cwd',
  LOG_DIR: '#source-log-dir',
  ID: '#source-id',
  PARSER: '#source-parser',
  CRAWL_URL: '#source-crawl-url',
  BASE_DIR: '#source-base-dir',
  METADATA_TITLE: '#source-metadata-title',
  METADATA_AUTHOR: '#source-metadata-author',
  METADATA_CATEGORY: '#source-metadata-category',
  METADATA_DESCRIPTION: '#source-metadata-description',
  TAGS: '#source-tags',
  METADATA_DATE: '#source-metadata-date',
  PAGES: '#source-pages',
};

function readField(container, selector) {
  const el = container.querySelector(selector);
  return el ? String(el.value ?? '').trim() : '';
}

function writeField(container, selector, value) {
  const el = container.querySelector(selector);
  if (el) el.value = value ?? '';
}

function extractAssignment(text, key) {
  const re = new RegExp(`^\\s*${key}\\s*=\\s*(.+)$`, 'm');
  const m = text.match(re);
  return m ? m[1].trim() : '';
}

function parsePathAssignment(rawExpr) {
  if (!rawExpr) return '';

  let m = rawExpr.match(/^Path\(\s*r?"([\s\S]*?)"\s*\)$/);
  if (m) return normalizePathLiteral(m[1]);

  m = rawExpr.match(/^Path\(\s*r?'([\s\S]*?)'\s*\)$/);
  if (m) return normalizePathLiteral(m[1]);

  m = rawExpr.match(/^r?"([\s\S]*?)"$/);
  if (m) return normalizePathLiteral(m[1]);

  m = rawExpr.match(/^r?'([\s\S]*?)'$/);
  if (m) return normalizePathLiteral(m[1]);

  return normalizePathLiteral(rawExpr);
}

function normalizePathLiteral(value) {
  return String(value || '').replace(/\\{2,}/g, '\\');
}

function parseMetadata(text) {
  const metadata = {};
  const blockMatch = text.match(/METADATA\s*=\s*\{([\s\S]*?)^\s*\}/m);
  if (!blockMatch) return metadata;

  const block = blockMatch[1];
  const lines = block.split(/\r?\n/);
  for (const line of lines) {
    const m = line.match(/^\s*['\"]([^'\"]+)['\"]\s*:\s*(.+?)\s*,?\s*$/);
    if (!m) continue;
    const key = m[1];
    let value = m[2].trim();

    const dq = value.match(/^"([\s\S]*)"$/);
    const sq = value.match(/^'([\s\S]*)'$/);
    if (dq) value = dq[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\');
    else if (sq) value = sq[1].replace(/\\'/g, "'").replace(/\\\\/g, '\\');

    metadata[key] = value;
  }

  return metadata;
}

function parseRunSettings(text) {
  const parsed = {
    CWD: parsePathAssignment(extractAssignment(text, 'CWD')),
    LOG_DIR: parsePathAssignment(extractAssignment(text, 'LOG_DIR')),
    METADATA: parseMetadata(text),
  };

  return parsed;
}

function escapePyString(value) {
  return String(value ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function escapePyRawPath(value) {
  return String(value ?? '').replace(/"/g, '\\"');
}

function buildMetadataBlock(metadata) {
  const lines = ['METADATA = {'];
  for (const key of METADATA_KEYS) {
    const value = metadata[key] ?? '';
    lines.push(`    \"${key}\": \"${escapePyString(value)}\",`);
  }
  lines.push('}');
  return lines.join('\n');
}

function updateRunSettingsText(originalText, values) {
  let updated = originalText;

  const cwdLine = `CWD = Path(r\"${escapePyRawPath(values.CWD)}\")`;
  const logLine = `LOG_DIR = Path(r\"${escapePyRawPath(values.LOG_DIR)}\")`;

  if (/^\s*CWD\s*=.*$/m.test(updated)) {
    updated = updated.replace(/^\s*CWD\s*=.*$/m, cwdLine);
  }

  if (/^\s*LOG_DIR\s*=.*$/m.test(updated)) {
    updated = updated.replace(/^\s*LOG_DIR\s*=.*$/m, logLine);
  }

  const metadataBlock = buildMetadataBlock(values.METADATA);
  if (/METADATA\s*=\s*\{[\s\S]*?^\s*\}/m.test(updated)) {
    updated = updated.replace(/METADATA\s*=\s*\{[\s\S]*?^\s*\}/m, metadataBlock);
  } else {
    updated = `${updated.trimEnd()}\n\n${metadataBlock}\n`;
  }

  return updated;
}

function collectValues(container) {
  const values = {
    CWD: readField(container, FIELD_MAP.CWD),
    LOG_DIR: readField(container, FIELD_MAP.LOG_DIR),
    METADATA: {},
  };

  for (const key of METADATA_KEYS) {
    values.METADATA[key] = readField(container, FIELD_MAP[key]);
  }

  return values;
}

function applyValuesToUi(container, parsed) {
  writeField(container, FIELD_MAP.CWD, parsed.CWD || '');
  writeField(container, FIELD_MAP.LOG_DIR, parsed.LOG_DIR || '');

  for (const key of METADATA_KEYS) {
    writeField(container, FIELD_MAP[key], parsed.METADATA[key] || '');
  }
}

async function fetchRunSettingsText() {
  const resp = await fetch(RUN_SETTINGS_HTTP_URL, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.text();
}

async function fetchRunSettingsDefaultsText() {
  const resp = await fetch(RUN_SETTINGS_DEFAULT_HTTP_URL, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.text();
}

async function saveRunSettingsText(text) {
  const resp = await fetch(RUN_SETTINGS_HTTP_URL, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: text,
  });

  if (!resp.ok) {
    const detail = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}${detail ? ` - ${detail}` : ''}`);
  }

  const result = await resp.json().catch(() => null);
  if (result && result.success === false) {
    throw new Error(result.error || 'Save failed');
  }
}

export async function initSourceTab(container) {
  const statusEl = container.querySelector('#source-status-display');
  const rawViewer = container.querySelector('#source-config-viewer');
  const saveBtn = container.querySelector('#source-save-btn');
  const reloadBtn = container.querySelector('#source-reload-btn');
  const defaultsBtn = ensureDefaultsButton(container);

  let originalText = '';

  const isDirty = () => {
    try {
      const values = collectValues(container);
      const candidate = updateRunSettingsText(originalText, values);
      return candidate !== originalText;
    } catch (_) {
      return false;
    }
  };

  const refresh = async () => {
    try {
      const text = await fetchRunSettingsText();
      originalText = text;
      if (rawViewer) rawViewer.value = text;
      const parsed = parseRunSettings(text);
      applyValuesToUi(container, parsed);
      if (statusEl) statusEl.value = 'Run settings loaded.';
    } catch (err) {
      if (statusEl) statusEl.value = `Load failed: ${err.message}`;
    }
  };

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      try {
        const values = collectValues(container);
        const nextText = updateRunSettingsText(originalText, values);
        await saveRunSettingsText(nextText);
        originalText = nextText;
        if (rawViewer) rawViewer.value = nextText;
        if (statusEl) statusEl.value = 'Run settings saved successfully.';
      } catch (err) {
        if (statusEl) statusEl.value = `Save failed: ${err.message}`;
      }
    });
  }

  if (reloadBtn) {
    reloadBtn.title = 'Revert unsaved changes';
    reloadBtn.addEventListener('click', async () => {
      if (isDirty() && !window.confirm('Discard unsaved changes and revert from disk?')) {
        return;
      }
      await refresh();
      if (statusEl) statusEl.value = 'Changes reverted from disk.';
    });
  }

  if (defaultsBtn) {
    defaultsBtn.addEventListener('click', async () => {
      if (!window.confirm('Reset Source settings to saved defaults? This will overwrite current settings.')) {
        return;
      }
      try {
        const defaultText = await fetchRunSettingsDefaultsText();
        await saveRunSettingsText(defaultText);
        originalText = defaultText;
        if (rawViewer) rawViewer.value = defaultText;

        const parsed = parseRunSettings(defaultText);
        applyValuesToUi(container, parsed);

        if (statusEl) statusEl.value = 'Run settings reset to defaults.';
      } catch (err) {
        if (statusEl) statusEl.value = `Reset to defaults failed: ${err.message}`;
      }
    });
  }

  await refresh();
}

function ensureDefaultsButton(container) {
  let btn = container.querySelector('#source-defaults-btn');
  if (btn) return btn;

  const actions = container.querySelector('.config-action-buttons');
  if (!actions) return null;

  btn = document.createElement('sl-button');
  btn.variant = 'primary';
  btn.id = 'source-defaults-btn';
  btn.title = 'Reset configuration to saved defaults';
  const icon = document.createElement('sl-icon');
  icon.name = 'arrow-counterclockwise';
  btn.appendChild(icon);
  actions.appendChild(btn);
  return btn;
}
