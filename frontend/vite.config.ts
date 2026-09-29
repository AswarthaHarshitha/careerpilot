/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The browser always calls /api on its own origin: in development Vite proxies it to VITE_API_URL, in production
// the host rewrites it to the API. No secret is ever exposed through VITE_ variables.
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env }
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: { '/api': { target: env.VITE_API_URL || env.API_URL || 'http://127.0.0.1:8010', changeOrigin: true } },
    },
    build: { sourcemap: false, chunkSizeWarningLimit: 700 },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
