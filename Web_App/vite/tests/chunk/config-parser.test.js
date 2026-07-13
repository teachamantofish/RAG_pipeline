/**
 * config-parser.test.js
 *
 * Unit tests for Web_App/js/config-parser.js
 * Validates parsing of Python config files into typed JS objects and
 * serialization back to Python-safe strings.
 */

import { describe, it, expect } from 'vitest';
import { parseConfig, serializeValue } from '../../../js/config-parser.js';

// ---------- Sample config text (mirrors chunkerconfig.py) ----------
const SAMPLE_CONFIG = `import tiktoken

TOKENIZER = tiktoken.get_encoding("cl100k_base")  # For OpenAI models: Counts the tokens of all chunk types
MAX_TOKENS_FOR_NODE = 500
CHUNK_SIZE_RANGE = f"max={MAX_TOKENS_FOR_NODE}"
CHUNK_MODEL = "heading-safe"  # Custom identifier: "heading-safe" | "greedy" | "fixed"
CODE_LENGTH = 3  # The number of lines that makes the code chunks "meaningful".
KEYWORD_DENSITY = 0.2  # Keyword density threshold for chunking
ENABLE_CODE_EXTRACTION = True  # Set to False to skip component extraction during testing
OUTPUT_NAME = "a_chunks.json"
`;

describe('parseConfig', () => {
  it('parses the sample config without throwing', () => {
    const result = parseConfig(SAMPLE_CONFIG);
    expect(result).toBeDefined();
    expect(result.values).toBeDefined();
    expect(result.lines).toBeDefined();
  });

  it('parses boolean True', () => {
    const { values } = parseConfig('ENABLE_CODE_EXTRACTION = True');
    expect(values.ENABLE_CODE_EXTRACTION).toBe(true);
  });

  it('parses boolean False', () => {
    const { values } = parseConfig('ENABLE_CODE_EXTRACTION = False');
    expect(values.ENABLE_CODE_EXTRACTION).toBe(false);
  });

  it('parses integer', () => {
    const { values } = parseConfig('CODE_LENGTH = 10');
    expect(values.CODE_LENGTH).toBe(10);
  });

  it('parses float', () => {
    const { values } = parseConfig('KEYWORD_DENSITY = 0.15');
    expect(values.KEYWORD_DENSITY).toBeCloseTo(0.15);
  });

  it('parses quoted string', () => {
    const { values } = parseConfig('OUTPUT_NAME = "a_chunks.json"');
    expect(values.OUTPUT_NAME).toBe('a_chunks.json');
  });

  it('parses single-quoted string', () => {
    const { values } = parseConfig("OUTPUT_NAME = 'a_chunks.json'");
    expect(values.OUTPUT_NAME).toBe('a_chunks.json');
  });

  it('parses None as null', () => {
    const { values } = parseConfig('MAX_EMBED_CHUNKS = None');
    expect(values.MAX_EMBED_CHUNKS).toBeNull();
  });

  it('parses expression as raw string (function call)', () => {
    const { values } = parseConfig('TOKENIZER = tiktoken.get_encoding("cl100k_base")');
    expect(typeof values.TOKENIZER).toBe('string');
    expect(values.TOKENIZER).toContain('tiktoken');
  });

  it('preserves inline comments in line data', () => {
    const { lines } = parseConfig('CODE_LENGTH = 3  # The number of lines');
    const codeLine = lines.find(l => l.key === 'CODE_LENGTH');
    expect(codeLine).toBeDefined();
    expect(codeLine.comment).toContain('The number of lines');
  });

  it('ignores comment-only lines', () => {
    const { values } = parseConfig('# This is a comment\nCODE_LENGTH = 5');
    expect(Object.keys(values)).toEqual(['CODE_LENGTH']);
    expect(values.CODE_LENGTH).toBe(5);
  });

  it('ignores blank lines', () => {
    const { values } = parseConfig('\n\nCODE_LENGTH = 5\n\n');
    expect(values.CODE_LENGTH).toBe(5);
  });

  it('parses full sample config with correct key count', () => {
    const { values } = parseConfig(SAMPLE_CONFIG);
    // 8 assignment keys expected
    const keys = Object.keys(values);
    expect(keys.length).toBe(8);
    expect(keys).toContain('TOKENIZER');
    expect(keys).toContain('MAX_TOKENS_FOR_NODE');
    expect(keys).toContain('CHUNK_SIZE_RANGE');
    expect(keys).toContain('CHUNK_MODEL');
    expect(keys).toContain('CODE_LENGTH');
    expect(keys).toContain('KEYWORD_DENSITY');
    expect(keys).toContain('ENABLE_CODE_EXTRACTION');
    expect(keys).toContain('OUTPUT_NAME');
  });

  it('parses values with correct types from full sample', () => {
    const { values } = parseConfig(SAMPLE_CONFIG);
    expect(typeof values.TOKENIZER).toBe('string'); // expression
    expect(values.MAX_TOKENS_FOR_NODE).toBe(500);
    expect(typeof values.CHUNK_SIZE_RANGE).toBe('string'); // f-string expression
    expect(values.CHUNK_MODEL).toBe('heading-safe');
    expect(values.CODE_LENGTH).toBe(3);
    expect(values.KEYWORD_DENSITY).toBeCloseTo(0.2);
    expect(values.ENABLE_CODE_EXTRACTION).toBe(true);
    expect(values.OUTPUT_NAME).toBe('a_chunks.json');
  });

  it('parses underscore-separated integer', () => {
    const { values } = parseConfig('PARQUET_ROW_GROUP_SIZE = 8_192');
    expect(values.PARQUET_ROW_GROUP_SIZE).toBe(8192);
  });
});

describe('serializeValue', () => {
  it('serializes null to None', () => {
    expect(serializeValue(null)).toBe('None');
  });

  it('serializes undefined to None', () => {
    expect(serializeValue(undefined)).toBe('None');
  });

  it('serializes true to True', () => {
    expect(serializeValue(true)).toBe('True');
  });

  it('serializes false to False', () => {
    expect(serializeValue(false)).toBe('False');
  });

  it('serializes integer', () => {
    expect(serializeValue(42)).toBe('42');
  });

  it('serializes float', () => {
    expect(serializeValue(0.15)).toBe('0.15');
  });

  it('serializes string with double quotes', () => {
    expect(serializeValue('hello')).toBe('"hello"');
  });

  it('escapes quotes in strings', () => {
    expect(serializeValue('say "hi"')).toBe('"say \\"hi\\""');
  });
});

describe('writeConfig expression guard', () => {
  it('refuses to quote a Python expression value', async () => {
    const { writeConfig } = await import('../../../js/config-writer.js');
    const original = 'EXCLUDED_TAGS = ["form", "script"]  # tags to drop\n';
    // Model round-trips expressions as strings; writing must not corrupt them.
    const result = writeConfig(original, { EXCLUDED_TAGS: '["form", "script"]' }, [
      { key: 'EXCLUDED_TAGS', type: 'text' },
    ]);
    expect(result).toBe(original);
  });

  it('still writes plain scalar changes', async () => {
    const { writeConfig } = await import('../../../js/config-writer.js');
    const original = 'MAX_URLS = 500  # cap\n';
    const result = writeConfig(original, { MAX_URLS: 250 }, [{ key: 'MAX_URLS', type: 'number' }]);
    expect(result).toBe('MAX_URLS = 250  # cap\n');
  });

  it('serializes null as None for nullable numbers', async () => {
    const { writeConfig } = await import('../../../js/config-writer.js');
    const original = 'TESTINGMODE = 5\n';
    const result = writeConfig(original, { TESTINGMODE: null }, [{ key: 'TESTINGMODE', type: 'number' }]);
    expect(result).toBe('TESTINGMODE = None\n');
  });
});
