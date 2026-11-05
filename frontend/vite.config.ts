import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { portOffset } from './src/worktreePort'

// The API sits behind the dev server's proxy, so the album is same-origin and the
// backend carries no CORS policy. Compose points this at the `app` service.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8007'
const basePort = Number(process.env.VITE_PORT ?? 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    port: process.env.VITE_PORT ? basePort : basePort + portOffset(process.cwd()),
    // Without it a collision slides onto another port and serves the wrong branch's app.
    strictPort: true,
    proxy: { '/api': apiTarget },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/testSetup.ts',
    exclude: ['node_modules', 'dist'],
  },
})
