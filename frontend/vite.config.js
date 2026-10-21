import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Docker on Windows mounts the repo through a bind mount that does NOT
    // forward filesystem change events to the container, so chokidar never
    // sees edits and Vite keeps serving a stale module graph (source edits
    // silently never appear — HMR and even a browser reload show old code).
    // Polling makes Vite pick up host edits reliably.
    watch: {
      usePolling: true,
      interval: 300,
    },
    // Docker publishes container port 5173 as localhost:5174. Tell Vite's
    // browser client to reconnect through the published port instead of the
    // container-only port, otherwise HMR repeatedly fails in local Firefox.
    hmr: {
      clientPort: 5174,
    },
    // allow access from tunnels (trycloudflare / ngrok) & the public domain — phone testing
    allowedHosts: ['.trycloudflare.com', '.ngrok-free.app', '.ngrok.io', '.ngrok-free.dev', '.klip-in.id'],
    // In Docker the API lives in another container (service `api`), so the
    // proxy must use the compose hostname — `localhost:8001` here would hit
    // the web container itself. Same-origin via tunnel => cookies survive.
    // changeOrigin:false keeps the browser's Host (localhost:5174 or
    // klip-in.id) so the backend can pick the right OAuth redirect_uri.
    proxy: {
      '/api': { target: 'http://api:8000', changeOrigin: false },
      '/videos': { target: 'http://api:8000', changeOrigin: false },
    },
  },
})
