// vite + plugin-electron 단독 dev (electron auto spawn 비활성).
// Playwright 가 별도로 Electron 띄움 — 충돌 회피.

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: 'localhost',
    watch: {
      ignored: ['**/python/**', '**/release/**', '**/dist-electron/**', '**/node_modules/**', '**/tmp-e2e*/**'],
    },
  },
  resolve: {
    alias: {
      '@': './src',
    },
  },
})
