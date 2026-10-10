import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API is proxied to the local cluster; in production nginx serves the built files
// and the ingress sends /api and /mcp to the gateway on the same host, so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://gateway.localtest.me:8080' } },
  test: { environment: 'jsdom', globals: true, setupFiles: './src/test-setup.js', css: false },
})
