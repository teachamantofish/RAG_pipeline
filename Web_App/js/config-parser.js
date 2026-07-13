/**
 * config-parser.js
 * 
 * Parses a Python config file (simple KEY = VALUE assignments) into a typed
 * JavaScript object. Preserves original line text for minimal-diff write-back.
 *
 * Exported API (ES module):
 *   parseConfig(text)   → { values: { key: typedValue }, lines: [ { raw, key, value, comment } ] }
 *   serializeValue(val) → Python-safe string representation
 */

/**
 * Parse a single Python assignment value string into a typed JS value.
 * @param {string} raw - The right-hand side of an assignment, trimmed.
 * @returns {{ value: *, isExpression: boolean }}
 */
export function parseValue(raw) {
  // Remove inline comment (but not inside strings)
  const stripped = stripInlineComment(raw);

  // None → null
  if (stripped === 'None') return { value: null, isExpression: false };

  // Boolean
  if (stripped === 'True') return { value: true, isExpression: false };
  if (stripped === 'False') return { value: false, isExpression: false };

  // Quoted string (single or double, including triple-quoted)
  const strMatch = stripped.match(/^("""|'''|"|')([\s\S]*?)\1$/);
  if (strMatch) return { value: strMatch[2], isExpression: false };

  // Integer
  if (/^-?\d+$/.test(stripped)) return { value: parseInt(stripped, 10), isExpression: false };

  // Float
  if (/^-?\d+\.\d*$/.test(stripped) || /^-?\.\d+$/.test(stripped)) {
    return { value: parseFloat(stripped), isExpression: false };
  }

  // Underscore-separated int literal (e.g. 8_192)
  if (/^-?\d[\d_]*\d$/.test(stripped) && stripped.includes('_')) {
    return { value: parseInt(stripped.replace(/_/g, ''), 10), isExpression: false };
  }

  // Everything else is an expression (f-strings, function calls, etc.)
  return { value: stripped, isExpression: true };
}

/**
 * Strip an inline comment from a value string, respecting quoted strings.
 * @param {string} s
 * @returns {string}
 */
function stripInlineComment(s) {
  let inStr = null;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (ch === '\\') { i++; continue; }
      if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === '"' || ch === "'") { inStr = ch; continue; }
    if (ch === '#') return s.slice(0, i).trimEnd();
  }
  return s.trimEnd();
}

/**
 * Parse full Python config text.
 * @param {string} text - Full file contents.
 * @returns {{ values: Object, lines: Array<{ raw: string, key: string|null, value: *, comment: string|null, isExpression: boolean }> }}
 */
export function parseConfig(text) {
  const values = {};
  const lines = [];

  const rawLines = text.replace(/\r\n/g, '\n').split('\n');

  for (const raw of rawLines) {
    const trimmed = raw.trimStart();

    // Full-line comment or blank
    if (trimmed === '' || trimmed.startsWith('#')) {
      lines.push({ raw, key: null, value: null, comment: trimmed.startsWith('#') ? trimmed : null, isExpression: false });
      continue;
    }

    // Assignment: KEY = value
    const assignMatch = trimmed.match(/^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$/);
    if (assignMatch) {
      const key = assignMatch[1];
      const rhsFull = assignMatch[2];

      // Extract inline comment
      const commentIdx = findInlineCommentIndex(rhsFull);
      const rhs = commentIdx >= 0 ? rhsFull.slice(0, commentIdx).trimEnd() : rhsFull.trimEnd();
      const comment = commentIdx >= 0 ? rhsFull.slice(commentIdx).trim() : null;

      const parsed = parseValue(rhs);
      values[key] = parsed.value;
      lines.push({ raw, key, value: parsed.value, comment, isExpression: parsed.isExpression });
      continue;
    }

    // Anything else (import statements, etc.)
    lines.push({ raw, key: null, value: null, comment: null, isExpression: false });
  }

  return { values, lines };
}

/**
 * Find the index of an inline comment '#' that is not inside a string.
 * @param {string} s
 * @returns {number} index or -1
 */
function findInlineCommentIndex(s) {
  let inStr = null;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (ch === '\\') { i++; continue; }
      if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === '"' || ch === "'") { inStr = ch; continue; }
    if (ch === '#') return i;
  }
  return -1;
}

/**
 * Serialize a JS value back to a Python-safe literal string.
 * @param {*} val
 * @returns {string}
 */
export function serializeValue(val) {
  if (val === null || val === undefined) return 'None';
  if (val === true) return 'True';
  if (val === false) return 'False';
  if (typeof val === 'number') {
    return Number.isInteger(val) ? String(val) : String(val);
  }
  if (typeof val === 'string') return `"${val.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
  return String(val);
}
