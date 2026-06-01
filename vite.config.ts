import { rmSync } from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron/simple'
import commonjs from '@rollup/plugin-commonjs'
import pkg from './package.json'

// vite-plugin-electron forces 'electron' into Rollup externals via withExternalBuiltins(),
// which causes `import { app } from "electron"` in the ESM bundle — but Node.js v20 ESM
// cannot analyze the Electron built-in's CJS exports (cjsPreparseModuleExports fails).
// Fix: post-process each entry chunk to replace the ESM import with a createRequire call.
const electronEsmFix = {
  name: 'electron-esm-fix',
  renderChunk(code: string, chunk: { isEntry: boolean }, options: { format: string }) {
    if (!chunk.isEntry || options.format !== 'es') return null

    const match = code.match(
      /^(import\s*(\{[^}]+\})\s*from\s*["']electron["'];?\n?)/m
    )
    if (!match) return null

    const namedImports = match[2].trim()

    // Remove the electron import line
    let result = code.replace(match[0], '')

    // Find the index of the last import line and insert shim after it
    const lines = result.split('\n')
    let lastImportIdx = -1
    for (let i = 0; i < lines.length; i++) {
      if (/^import[\s{*]/.test(lines[i])) lastImportIdx = i
    }

    const shimLines = [
      `import { createRequire as __crEl__ } from 'node:module';`,
      `const __electronMod__ = __crEl__(import.meta.url)('electron');`,
      `const ${namedImports} = __electronMod__;`,
    ]

    const insertAt = lastImportIdx >= 0 ? lastImportIdx + 1 : 0
    lines.splice(insertAt, 0, ...shimLines)

    return { code: lines.join('\n'), map: null }
  }
}

export default defineConfig(({ command, mode }) => {
  rmSync('dist-electron', { recursive: true, force: true })

  const isServe = command === 'serve'
  const isBuild = command === 'build'
  const sourcemap = isServe || !!process.env.VSCODE_DEBUG

  const env = loadEnv(mode, process.cwd(), '')

  const mainEnvDefine: Record<string, string> = {
    'process.env.UPDATE_FEED_URL': JSON.stringify(env.UPDATE_FEED_URL ?? ''),
    'process.env.INSERTYAI_SUPABASE_ANON_KEY': JSON.stringify(env.INSERTYAI_SUPABASE_ANON_KEY ?? ''),
    // ★ INSERTYAI_UPDATE_TOKEN inject 영구 제거 (보안). auto-update 는 Worker proxy 사용.
    // Worker: inserty-beta-admin/worker/src/auto-update.ts
    // Client config: electron/services/auto-update-service.ts (UPDATE_FEED_URL 상수)
    //                + electron-builder.json (publish.url)
  }

  return {
    resolve: {
      alias: {
        '@': path.join(__dirname, 'src')
      },
    },
    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        'zustand',
        'lucide-react',
        'tailwind-merge',
        'clsx',
        'date-fns'
      ],
      force: false
    },
    cacheDir: 'node_modules/.vite',
    plugins: [
      react(),
      electron({
        main: {
          entry: 'electron/main/index.ts',
          onstart(args) {
            if (process.env.VSCODE_DEBUG) {
              console.log(/* For `.vscode/.debug.script.mjs` */'[startup] Electron App')
            } else {
              // Remove ELECTRON_RUN_AS_NODE so Electron initializes the full main-process
              // context (process.type, built-in 'electron' module, etc.).
              // Claude Code and some CI environments set this to 1, which makes Electron
              // behave like plain Node.js and breaks require('electron').
              const { ELECTRON_RUN_AS_NODE: _removed, ...spawnEnv } = process.env as Record<string, string>
              args.startup(['.',  '--no-sandbox'], { env: spawnEnv })
            }
          },
          vite: {
            build: {
              sourcemap,
              minify: isBuild,
              outDir: 'dist-electron/main',
              rollupOptions: {
                plugins: [
                  electronEsmFix,
                  commonjs()
                ],
                external: [
                  'electron',
                  ...Object.keys('dependencies' in pkg ? pkg.dependencies : {})
                ],
                output: {
                  format: 'es',
                  entryFileNames: '[name].mjs',
                  // __dirname / __filename are not defined in ESM; inject via URL API.
                  intro: [
                    `const __filename = new URL(import.meta.url).pathname.replace(/^\\/([A-Za-z]:)/, '$1').replace(/\\//g, '\\\\');`,
                    `const __dirname = __filename.slice(0, __filename.lastIndexOf('\\\\'));`,
                  ].join('\n'),
                }
              },
            },
            define: mainEnvDefine,
          },
        },
        preload: {
          input: 'electron/preload/index.ts',
          vite: {
            build: {
              sourcemap: sourcemap ? 'inline' : undefined,
              minify: isBuild,
              outDir: 'dist-electron/preload',
              rollupOptions: {
                external: [
                  'electron',
                  'electron-updater',
                  ...Object.keys('dependencies' in pkg ? pkg.dependencies : {})
                ],
                output: {
                  format: 'cjs',
                  entryFileNames: '[name].js'
                }
              },
            },
          },
        },
        renderer: {},
      }),
    ],
    server: process.env.VSCODE_DEBUG ? (() => {
      const url = new URL(pkg.debug.env.VITE_DEV_SERVER_URL)
      return {
        host: url.hostname,
        port: +url.port,
        watch: {
          ignored: ['**/python/.venv/**', '**/python/dist/**', '**/release/**', '**/dist-electron/**', '**/node_modules/**'],
        },
      }
    })() : {
      watch: {
        // Python venv 안 수백 개 파일 watch 시 무한 reload — 제외 필수.
        ignored: ['**/python/.venv/**', '**/python/dist/**', '**/release/**', '**/dist-electron/**', '**/node_modules/**'],
      },
    },
    clearScreen: false,
  }
})
