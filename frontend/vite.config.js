import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Host port that docker-compose publishes for this web container (5175 ->
// container 5173). Must NOT be 5174: that is the separate Klipin video
// clipper dev server, and pointing HMR there made this page reload whenever
// that other project changed or restarted.
const HMR_CLIENT_PORT = process.env.VITE_HMR_CLIENT_PORT ? Number(process.env.VITE_HMR_CLIENT_PORT) : undefined
// Compose service name of the music API in Docker (VITE_PROXY_TARGET=http://music-api:8000),
// falling back to local uvicorn (http://127.0.0.1:8000) when running outside Docker.
const PROXY_TARGET = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

// The Windows bind mount occasionally returns EIO for a stat (Docker Desktop
// file-sharing hiccup). Without an 'error' listener chokidar's error event is
// unhandled and kills the dev server -> every open tab reloads. Log instead.
const survivePollingErrors = () => ({
  name: 'klipin-survive-watch-errors',
  configureServer(server) {
    server.watcher.on('error', (err) => {
      server.config.logger.warn(`[watch] ignored: ${err?.code || ''} ${err?.message || err}`)
    })
  },
})

export default defineConfig({
  plugins: [react(), survivePollingErrors()],
  server: {
    port: 5173,
    // Docker on Windows mounts the repo through a bind mount that does NOT
    // forward filesystem change events to the container, so chokidar never
    // sees edits and Vite keeps serving a stale module graph (source edits
    // silently never appear — HMR and even a browser reload show old code).
    // Polling makes Vite pick up host edits reliably.
    watch: {
      usePolling: true,
      // 300ms stat-polling over the Windows mount is heavy and raised the
      // chance of EIO errors; 1s is still fast enough for HMR.
      interval: 1000,
      binaryInterval: 3000,
      // Never part of the module graph — don't poll them.
      ignored: ['**/dist/**', '**/.pytest_cache/**', '**/*.log'],
    },
    // Docker publishes container port 5173 as localhost:5175. Tell Vite's
    // browser client to reconnect through the published port instead of the
    // container-only port, otherwise HMR repeatedly fails in local Firefox.
    hmr: HMR_CLIENT_PORT ? { clientPort: HMR_CLIENT_PORT } : true,
    // allow access from tunnels (trycloudflare / ngrok) & the public domain — phone testing
    allowedHosts: ['.trycloudflare.com', '.ngrok-free.app', '.ngrok.io', '.ngrok-free.dev', '.klip-in.id'],
    // In Docker the API lives in another container (service `music-api`), so
    // the proxy must use the compose hostname — `localhost:8002` here would hit
    // the web container itself. Same-origin via tunnel => cookies survive.
    // changeOrigin:false keeps the browser's Host (localhost:5175 or
    // klip-in.id) so the backend can pick the right OAuth redirect_uri.
    proxy: {
      '/api': { target: PROXY_TARGET, changeOrigin: false },
      '/videos': { target: PROXY_TARGET, changeOrigin: false },
    },
  },
})
