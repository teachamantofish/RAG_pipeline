/**
 * chunk-init.js — Chunk tab lifecycle controller.
 *
 * Uses the shared createTabInit factory (this file was previously a 335-line
 * fork of tab-init.js that had already drifted — it lacked Stop support).
 */
import { createTabInit } from '../tab-init.js';

export const initChunkTab = createTabInit({
  phase: 'chunk',
  schemaUrl: './config/chunk.schema.json',
  configUrl: './Get_and_Chunk/config/chunkerconfig.py',
  prefix: 'chunk',
});
