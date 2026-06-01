#!/usr/bin/env node
/* eslint-disable */
/**
 * scripts/obfuscate.cjs
 *
 * Applies javascript-obfuscator to Electron build outputs:
 *   - dist-electron/main/index.mjs    (ESM, Node/Electron main)
 *   - dist-electron/preload/index.js  (CJS, Electron preload)
 *   - dist/assets/index-*.js          (Browser ES module, renderer)
 *
 * Designed to run AFTER `tsc && vite build` and BEFORE `electron-builder`.
 *
 * Conservative defaults — controlFlowFlattening / deadCodeInjection at moderate
 * thresholds, no selfDefending (which has historically broken Electron preload
 * via contextBridge), no debugProtection. Native module identifiers and core
 * Electron API names are preserved via reservedStrings/reservedNames so that
 * the obfuscator does NOT rewrite e.g. require('better-sqlite3') or
 * contextBridge.exposeInMainWorld.
 */

const fs = require('fs');
const path = require('path');
const JavaScriptObfuscator = require('javascript-obfuscator');

const ROOT = path.resolve(__dirname, '..');

// -----------------------------------------------------------------------------
// Identifier / string names that MUST NOT be renamed or rewritten.
// Anything that needs to remain string-equal at runtime (module IDs passed to
// require(), or Electron IPC well-known channel names that look like
// identifiers) goes here.
// -----------------------------------------------------------------------------
const RESERVED_NAMES = [
  // Native / built-in modules called by name
  'better-sqlite3',
  'electron',
  'electron-updater',
  'electronAPI',
  'electronSplash',
  'contextBridge',
  'ipcRenderer',
  'ipcMain',
  'webUtils',
  'BrowserWindow',
  'app',
  'shell',
  'dialog',
  'session',
  'Menu',
  'Tray',
  'nativeImage',
  // Node modules referenced via dynamic import or string require
  'better-sqlite3.node',
];

// Common base options. Tuned to the spec in the task brief but with
// selfDefending=false (Electron compat) and renameGlobals=false.
const BASE_OPTIONS = {
  compact: true,
  stringArray: true,
  stringArrayThreshold: 0.75,
  stringArrayEncoding: ['base64'],
  stringArrayIndexShift: true,
  stringArrayRotate: true,
  stringArrayShuffle: true,
  splitStrings: true,
  splitStringsChunkLength: 8,
  controlFlowFlattening: true,
  controlFlowFlatteningThreshold: 0.4,
  deadCodeInjection: true,
  deadCodeInjectionThreshold: 0.2,
  identifierNamesGenerator: 'mangled-shuffled',
  renameGlobals: false,
  selfDefending: false, // Electron-safe
  debugProtection: false,
  disableConsoleOutput: false,
  transformObjectKeys: true,
  unicodeEscapeSequence: false,
  numbersToExpressions: true,
  simplify: true,
  reservedNames: RESERVED_NAMES,
};

// -----------------------------------------------------------------------------
// Per-target overrides. The main and preload processes load *before* anything
// else and historically have the lowest tolerance for control-flow tricks
// against ESM/CJS interop, so we keep options a touch more conservative there.
// -----------------------------------------------------------------------------
const TARGETS = [
  {
    label: 'main (ESM)',
    file: 'dist-electron/main/index.mjs',
    options: {
      ...BASE_OPTIONS,
      // ESM target — keep top-level imports/exports untouched.
      target: 'node',
      // Slightly lower flattening to reduce risk on dynamic native module loads.
      controlFlowFlatteningThreshold: 0.35,
      deadCodeInjectionThreshold: 0.15,
    },
  },
  {
    label: 'preload (CJS)',
    file: 'dist-electron/preload/index.js',
    options: {
      ...BASE_OPTIONS,
      target: 'node',
      // Preload must keep contextBridge wiring intact — be conservative.
      controlFlowFlatteningThreshold: 0.3,
      deadCodeInjectionThreshold: 0.1,
      transformObjectKeys: false, // keep exposed API object keys readable
    },
  },
  {
    label: 'renderer (browser)',
    file: null, // resolved via glob below
    glob: 'dist/assets/index-*.js',
    options: {
      ...BASE_OPTIONS,
      target: 'browser',
    },
  },
];

function findRendererBundle() {
  const assetsDir = path.join(ROOT, 'dist', 'assets');
  if (!fs.existsSync(assetsDir)) return [];
  return fs
    .readdirSync(assetsDir)
    .filter((f) => /^index-.*\.js$/.test(f))
    .map((f) => path.join('dist', 'assets', f));
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function obfuscateFile(relPath, options, label) {
  const absPath = path.join(ROOT, relPath);
  if (!fs.existsSync(absPath)) {
    console.warn(`[obfuscate] SKIP (missing): ${relPath}`);
    return null;
  }
  const before = fs.readFileSync(absPath, 'utf8');
  const beforeBytes = Buffer.byteLength(before, 'utf8');
  const start = Date.now();
  let result;
  try {
    result = JavaScriptObfuscator.obfuscate(before, options).getObfuscatedCode();
  } catch (err) {
    console.error(`[obfuscate] FAILED on ${relPath}: ${err.message}`);
    throw err;
  }
  fs.writeFileSync(absPath, result, 'utf8');
  const afterBytes = Buffer.byteLength(result, 'utf8');
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);
  const ratio = (afterBytes / beforeBytes).toFixed(2);
  console.log(
    `[obfuscate] ${label}: ${relPath}\n` +
      `             ${formatBytes(beforeBytes)} -> ${formatBytes(afterBytes)} ` +
      `(x${ratio}, ${elapsed}s)`,
  );
  return { before: beforeBytes, after: afterBytes };
}

function main() {
  console.log('[obfuscate] Starting javascript-obfuscator pass on build outputs');
  const totals = { before: 0, after: 0, files: 0 };
  for (const target of TARGETS) {
    const files = target.glob ? findRendererBundle() : [target.file];
    for (const rel of files) {
      const stats = obfuscateFile(rel, target.options, target.label);
      if (stats) {
        totals.before += stats.before;
        totals.after += stats.after;
        totals.files += 1;
      }
    }
  }
  console.log(
    `[obfuscate] Done. ${totals.files} file(s). ` +
      `Total: ${formatBytes(totals.before)} -> ${formatBytes(totals.after)} ` +
      `(x${(totals.after / Math.max(1, totals.before)).toFixed(2)})`,
  );
}

main();
