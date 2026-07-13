/**
 * config-writer.js
 *
 * Minimal-diff writer: takes the original Python config text and a patch
 * object { key: newValue } and returns updated text with only the assignment
 * RHS replaced. Comments, blank lines, ordering, and spacing are untouched
 * for unpatched keys.
 *
 * Exported API (ES module):
 *   writeConfig(originalText, patch, schema) → string
 */

import { serializeValue, parseValue } from './config-parser.js';

/**
 * Apply a patch to the original config text, replacing only assignment values
 * for writable keys.
 *
 * @param {string} originalText - Full original Python config file text.
 * @param {Object} patch - { key: newValue } for keys that changed.
 * @param {Array} [schemaFields] - Optional schema fields array; read-only keys are skipped.
 * @returns {string} Updated file text.
 */
export function writeConfig(originalText, patch, schemaFields) {
  const readOnlyKeys = new Set();
  if (schemaFields) {
    for (const f of schemaFields) {
      if (f.readOnly) readOnlyKeys.add(f.key);
    }
  }

  const lines = originalText.replace(/\r\n/g, '\n').split('\n');
  const result = [];

  for (const line of lines) {
    const trimmed = line.trimStart();

    // Try to match an assignment line
    const assignMatch = trimmed.match(/^([A-Z_][A-Z0-9_]*)\s*=\s*/);

    if (assignMatch) {
      const key = assignMatch[1];

      // Only patch if key is in the patch and not read-only
      if (key in patch && !readOnlyKeys.has(key)) {
        const newVal = patch[key];
        const serialized = serializeValue(newVal);

        // Extract the original serialized value from the line so we can
        // compare: if nothing changed, keep the original line bit-for-bit
        // (preserving quirky whitespace / alignment).
        const rhsStart = line.indexOf('=') + 1;
        const rhsPart = line.slice(rhsStart);
        const commentIdx = findInlineCommentIndex(rhsPart);
        const origRawValue = (commentIdx >= 0 ? rhsPart.slice(0, commentIdx) : rhsPart).trim();

        if (serialized === origRawValue) {
          // Value unchanged — keep original line as-is
          result.push(line);
          continue;
        }

        // Expression guard: if the original RHS is a Python expression the
        // parser cannot represent (list, tuple, Path(...), f-string, ...),
        // re-serializing the model's string form would wrap it in quotes and
        // corrupt the config. Keep the original line instead.
        const origParsed = parseValue(origRawValue);
        if (origParsed.isExpression) {
          if (String(newVal) !== origRawValue) {
            console.warn(
              `[config-writer] "${key}" holds a Python expression (${origRawValue}); ` +
              'refusing to overwrite it with a quoted string. Edit the file directly.'
            );
          }
          result.push(line);
          continue;
        }

        // Preserve leading whitespace
        const leadingWS = line.match(/^(\s*)/)[1];

        // Preserve inline comment from original line
        const inlineComment = commentIdx >= 0 ? '  ' + rhsPart.slice(commentIdx).trim() : '';

        result.push(`${leadingWS}${key} = ${serialized}${inlineComment}`);
        continue;
      }
    }

    result.push(line);
  }

  return result.join('\n');
}

/**
 * Find the index of an inline comment '#' not inside a string.
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
