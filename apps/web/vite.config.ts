/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The UI talks only to its own origin (`/api/v1`). In development the Vite server
// proxies that prefix to a control API (default: the Compose stack on :8080). Start
// the API with CQ_PUBLIC_ORIGINS including the Vite origin so state-changing
// requests pass the Origin check, e.g.
//   CQ_PUBLIC_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
const apiTarget = process.env.CQ_API_TARGET ?? 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@contracts': fileURLToPath(new URL('../../packages/contracts', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false, ws: false },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2022',
    // Keep the whole app same-origin and CSP-friendly: no inline scripts, no eval.
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-dom/client', 'react-router'],
          query: ['@tanstack/react-query', 'openapi-fetch'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'mock/**/*.test.ts'],
    css: false,
    restoreMocks: true,
  },
});
