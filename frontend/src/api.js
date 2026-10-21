// Base origin for the backend. In Docker the frontend runs on a different
// host port, so compose sets VITE_API_URL=http://localhost:8001. When unset
// (plain `npm run dev`) we fall back to same-origin `/api` via vite proxy.
export const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '') + '/api'

/**
 * Resolve a backend-relative asset path (e.g. "/videos/…") to an absolute URL
 * when the frontend is served from a different origin (Docker).
 */
export function resolveAsset(path = '') {
  const base = import.meta.env.VITE_API_URL || ''
  // Media URLs come from API payloads already signed by the backend
  // (?exp=…&sig=…), so no session token is ever embedded in a URL — a shared
  // clip link stops working after the signature expires.
  return base ? `${base}${path.startsWith('/') ? path : `/${path}`}` : path
}

/**
 * Resolve a media asset URL. Backend payloads already sign /videos links
 * (?exp=…&sig=…), so we return them as-is. We deliberately do NOT append the
 * session token here: a bearer token in a media URL leaks through referrers,
 * browser history and proxy logs. Callers that hold an un-signed path (e.g. a
 * client-built `/videos/<id>/source.mp4` preview) fetch a short-lived signed
 * URL from the API (see getProjectSourceUrl) instead.
 */
export function resolveMediaAsset(path = '') {
  return resolveAsset(path)
}

/**
 * Append a cache-busting param to an already-signed media URL without
 * breaking the existing `?exp=&sig=` query. Uses `&` when the URL already
 * carries a query string, otherwise `?` — a stray second `?` makes the
 * backend's HMAC signature invalid (→ 401, video fails to load).
 */
export function withCacheBust(url = '', bust = Date.now()) {
  if (!url) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}t=${bust}`
}

const TOKEN_KEY = 'klipin_token'

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAuthToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(extra = {}) {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}`, ...extra } : extra
}

async function handle(res) {
  const data = await res.json().catch(() => ({}))
  if (res.status === 401) {
    // Session invalid/expired — drop the token so the app treats the user
    // as signed out and the UI shows the sign-in prompt again.
    setAuthToken(null)
    window.dispatchEvent(new CustomEvent('klipin:unauthorized'))
  }
  if (!res.ok) {
    const error = new Error(data.detail || `Request failed (${res.status})`)
    error.status = res.status
    error.retryAfter = Number(res.headers.get('Retry-After') || 0)
    throw error
  }
  return data
}

export function postJSON(path, body) {
  return fetch(`${API}${path}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  }).then(handle)
}

export function processVideo(url, options, platform = '') {
  return postJSON('/process', { url, options, platform })
}

export function uploadVideo(file, options) {
  const form = new FormData()
  form.append('file', file)
  form.append('options', JSON.stringify(options))
  return fetch(`${API}/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(handle)
}

export function getStatus(jobId, { signal } = {}) {
  return fetch(`${API}/status/${jobId}`, { headers: authHeaders(), signal }).then(handle)
}

// Short-lived signed URL for a project's original source.mp4 — lets editor
// modals preview the raw source without embedding the session token in a URL.
export function getProjectSourceUrl(jobId) {
  return fetch(`${API}/projects/${jobId}/source-url`, { headers: authHeaders() }).then(handle)
}

export function listProjects() {
  return fetch(`${API}/projects`, { headers: authHeaders() }).then(handle)
}

// Channel YouTube yang sedang ramai per kategori (publik, dinamis —
// live ytsearch + cache server 1 jam). Tanpa auth agar tampil di beranda.
// force=true melewati cache (tombol refresh manual).
export function getTrending(category = 'berita', force = false) {
  return fetch(`${API}/trending?category=${encodeURIComponent(category)}${force ? '&force=1' : ''}`).then(handle)
}

export function getProject(jobId) {
  return fetch(`${API}/projects/${jobId}`, { headers: authHeaders() }).then(handle)
}

export function deleteProject(jobId) {
  return fetch(`${API}/projects/${jobId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(handle)
}

export function updateClipMeta(jobId, idx, meta) {
  return postJSON(`/projects/${jobId}/clips/${idx}/meta`, meta)
}

export function retrimClip(jobId, idx, payload) {
  return postJSON(`/projects/${jobId}/clips/${idx}/retrim`, payload)
}

export function getClipCaptions(jobId, idx) {
  return fetch(`${API}/projects/${jobId}/clips/${idx}/captions`, {
    headers: authHeaders(),
  }).then(handle)
}

// Append the server-side "force download" flag to a /videos URL. Cross-origin
// browsers ignore the <a download> attribute and would open the file instead;
// the /videos endpoint responds with Content-Disposition: attachment when this
// flag is present, so downloads always work (Docker flow is cross-origin).
export function dl(url) {
  if (!url) return url
  return url.includes('?') ? `${url}&download=1` : `${url}?download=1`
}

export function editCaption(jobId, idx, lines) {
  return postJSON(`/projects/${jobId}/clips/${idx}/caption`, { lines })
}

export function autofixCaption(jobId, idx, lines) {
  return postJSON(`/projects/${jobId}/clips/${idx}/caption/autofix`, { lines })
}

export function repickProject(jobId, payload) {
  return postJSON(`/projects/${jobId}/repick`, payload)
}

export function putProjectOptions(jobId, options) {
  return fetch(`${API}/projects/${jobId}/options`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ options }),
  }).then(handle)
}

// Per-scene layout editor: save a user-edited layout plan for one clip.
// plan = [{start, end, layout}] in clip-local seconds; [] resets to AI plan.
export function putClipLayoutPlan(jobId, idx, plan) {
  return fetch(`${API}/projects/${jobId}/clips/${idx}/layout-plan`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ plan }),
  }).then(handle)
}

export function previewClipLayout(jobId, clipIndex, options) {
  return postJSON(`/projects/${jobId}/clips/${clipIndex}/layout-preview`, { options })
}

export function getClipLayerPlan(jobId, idx) {
  return fetch(`${API}/projects/${jobId}/clips/${idx}/layer-plan`, {
    headers: authHeaders(),
  }).then(handle)
}

export function putClipLayerPlan(jobId, idx, layers) {
  return fetch(`${API}/projects/${jobId}/clips/${idx}/layer-plan`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ layers }),
  }).then(handle)
}

export function renderClipLayerPlan(jobId, idx) {
  return postJSON(`/projects/${jobId}/clips/${idx}/layer-plan/render`, {})
}

export function uploadClipLayerMedia(jobId, idx, file, { kind, start = 0, end = 4 } = {}) {
  const body = new FormData()
  body.append('file', file); body.append('kind', kind)
  body.append('start', String(start)); body.append('end', String(end))
  return fetch(`${API}/projects/${jobId}/clips/${idx}/layer-plan/media`, {
    method: 'POST', headers: authHeaders(), body,
  }).then(handle)
}

export function createClipProxy(jobId, idx) {
  return postJSON(`/projects/${jobId}/clips/${idx}/proxy`, {})
}

// A/B hook generator: 3 scored hook variants for one clip.
export function generateHookVariants(jobId, idx) {
  return postJSON(`/projects/${jobId}/clips/${idx}/hook-variants`, {})
}

// Apply one hook variant as the clip's active hook (burned on next re-render).
export function selectHookVariant(jobId, idx, text) {
  return postJSON(`/projects/${jobId}/clips/${idx}/hook-variants/select`, { text })
}

// Approval workflow: set the review decision for one clip.
// status: 'approved' | 'changes_requested' | 'pending' | '' (clear).
export function setClipApproval(jobId, idx, status, note = '') {
  return postJSON(`/projects/${jobId}/clips/${idx}/approval`, { status, note })
}

// Public multi-currency pricing (no auth) for the global Pricing page.
export function getPricing(currency = 'USD') {
  return fetch(`${API}/pricing?currency=${encodeURIComponent(currency)}`).then(handle)
}

export function getMusicGenres(probe = false) {
  return fetch(`${API}/music/genres${probe ? '?probe=1' : ''}`,
    { headers: authHeaders() }).then(handle)
}

// Preflight: is a full-song engine (HeartMuLa worker / ACE-Step) reachable now?
export function getMusicEngine() {
  return fetch(`${API}/music/engine`, { headers: authHeaders() }).then(handle)
}

export function generateMusic(payload) {
  return postJSON('/music/generate', { take_count: 2, take_mode: 'both', ...payload })
}

export function composeMusicLyrics(payload) {
  return postJSON('/music/lyrics', payload)
}

export function planMusicSong(payload) {
  // Rencana aransemen per seksi + hook. Tanpa render GPU (tanpa biaya Modal).
  return postJSON('/music/plan', payload)
}

export function getMusicGroup(groupId) {
  // Status berubah di background setelah Modal selesai. Prevent the browser
  // from reusing the initial queued/processing response during polling.
  return fetch(`${API}/music/group/${groupId}?_=${Date.now()}`, {
    headers: authHeaders(),
    cache: 'no-store',
  }).then(handle)
}

/**
 * Real-time Server-Sent Events stream for a music group. Uses fetch (not
 * EventSource) so the bearer token stays in the Authorization header instead
 * of leaking into the URL. Calls onGroup(payload) on every change and onEnd()
 * once all takes are terminal. Resolves when the stream closes.
 */
export async function streamMusicGroup(groupId, { onGroup, onEnd, signal } = {}) {
  const res = await fetch(`${API}/music/group/${groupId}/stream`, {
    headers: authHeaders(),
    cache: 'no-store',
    signal,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `Stream failed (${res.status})`)
  }
  if (!res.body) throw new Error('Streaming tidak didukung browser ini')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (event === 'group' && data) {
        try { onGroup?.(JSON.parse(data)) } catch { /* ignore malformed frame */ }
      } else if (event === 'end') {
        onEnd?.()
        return
      } else if (event === 'error') {
        throw new Error('Music group not found')
      }
    }
  }
  onEnd?.()
}

export function extendMusic(jobId, payload) {
  return postJSON(`/music/extend/${jobId}`, payload)
}

export function fetchMusicStems(jobId) {
  return postJSON(`/music/stems/${jobId}`, {})
}

export function getMusicStatus(jobId) {
  return fetch(`${API}/music/status/${jobId}?_=${Date.now()}`, {
    headers: authHeaders(),
    cache: 'no-store',
  }).then(handle)
}

// Full job history for the Music panel (status + error text per attempt).
export function getMusicJobs(query = '') {
  const q = query ? `?q=${encodeURIComponent(query)}` : ''
  return fetch(`${API}/music/jobs${q}`, { headers: authHeaders() }).then(handle)
}

// Recompute word-level lyric timing + beat/downbeat grid for an old song.
export function realignMusic(jobId) {
  return postJSON(`/music/align/${encodeURIComponent(jobId)}`, {})
}

// Studio projects: versioned form + arrangement plan snapshots per song.
export function listMusicProjects() {
  return fetch(`${API}/music/projects`, { headers: authHeaders() }).then(handle)
}

export function createMusicProject(payload) {
  return postJSON('/music/projects', payload)
}

export function getMusicProject(projectId) {
  return fetch(`${API}/music/projects/${encodeURIComponent(projectId)}`,
    { headers: authHeaders() }).then(handle)
}

export function addMusicProjectRevision(projectId, payload) {
  return postJSON(`/music/projects/${encodeURIComponent(projectId)}/revisions`, payload)
}

export function getMusicProjectRevision(projectId, revisionId) {
  return fetch(
    `${API}/music/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}`,
    { headers: authHeaders() }).then(handle)
}

export function restoreMusicProjectRevision(projectId, revisionId) {
  return postJSON(
    `/music/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}/restore`, {})
}

export function deleteMusicProject(projectId) {
  return fetch(`${API}/music/projects/${encodeURIComponent(projectId)}`,
    { method: 'DELETE', headers: authHeaders() }).then(handle)
}

// Lyrics workspace: lightweight drafts with autosave (PUT).
export function listMusicLyricsDrafts() {
  return fetch(`${API}/music/lyrics-drafts`, { headers: authHeaders() }).then(handle)
}

export function createMusicLyricsDraft(payload) {
  return postJSON('/music/lyrics-drafts', payload)
}

export function getMusicLyricsDraft(draftId) {
  return fetch(`${API}/music/lyrics-drafts/${encodeURIComponent(draftId)}`,
    { headers: authHeaders() }).then(handle)
}

export function updateMusicLyricsDraft(draftId, payload) {
  return fetch(`${API}/music/lyrics-drafts/${encodeURIComponent(draftId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  }).then(handle)
}

export function deleteMusicLyricsDraft(draftId) {
  return fetch(`${API}/music/lyrics-drafts/${encodeURIComponent(draftId)}`,
    { method: 'DELETE', headers: authHeaders() }).then(handle)
}

export function getMusicPersonas() {
  return fetch(`${API}/music/personas`, { headers: authHeaders() }).then(handle)
}

export function createMusicPersona(payload) {
  return postJSON('/music/personas', payload)
}

export function createDesignedMusicPersona(payload) {
  return postJSON('/music/personas/design', payload)
}

export function listVoicePreviews() {
  return fetch(`${API}/music/voice-previews`, { headers: authHeaders() }).then(handle)
}
export function createVoicePreview(payload) {
  return postJSON('/music/voice-previews', payload)
}
export function getVoicePreview(id) {
  return fetch(`${API}/music/voice-previews/${encodeURIComponent(id)}`, { headers: authHeaders() }).then(handle)
}
export function saveVoicePreview(id, name) {
  return postJSON(`/music/voice-previews/${encodeURIComponent(id)}/save`, { name })
}
export async function fetchVoiceAudio(id, savedPersona = false) {
  const route = savedPersona ? 'personas' : 'voice-previews'
  const res = await fetch(`${API}/music/${route}/${encodeURIComponent(id)}/audio`, { headers: authHeaders() })
  if (!res.ok) await handle(res)
  return res.blob()
}

export function uploadMusicVoicePersona({ name, vocalGender, consent, file }) {
  const body = new FormData()
  body.append('name', name || '')
  body.append('vocal_gender', vocalGender || 'auto')
  body.append('consent', consent ? 'true' : 'false')
  body.append('file', file)
  return fetch(`${API}/music/personas/voice`, {
    method: 'POST', headers: authHeaders(), body,
  }).then(handle)
}

export function deleteMusicPersona(personaId) {
  return fetch(`${API}/music/personas/${personaId}`, {
    method: 'DELETE', headers: authHeaders(),
  }).then(handle)
}

export function coverSongMusic(jobId, payload) {
  return postJSON(`/music/cover-song/${jobId}`, payload)
}

export function remixMusicUrl(payload) {
  return postJSON('/music/remix-url', payload)
}

// Cover Create: faithful cover from an uploaded song file (multipart).
export function coverUploadMusic(formData) {
  return fetch(`${API}/music/cover-upload`, {
    method: 'POST', headers: authHeaders(), body: formData,
  }).then(handle)
}

export function shareMusic(jobId, isPublic) {
  return postJSON(`/music/share/${jobId}`, { public: isPublic })
}

export function getMusicFeed(limit = 20) {
  return fetch(`${API}/music/feed?limit=${limit}`).then(handle)
}

export function getMusicCost() {
  return fetch(`${API}/music/cost`, { headers: authHeaders() }).then(handle)
}

export function refreshMusicCover(jobId, payload) {
  return postJSON(`/music/cover/${jobId}`, payload)
}

export function getBillingStatus() {
  return fetch(`${API}/billing/status`, { headers: authHeaders() }).then(handle)
}

// Prediction calibration: predicted hook score vs real post performance.
export function getCalibration(jobId) {
  return fetch(`${API}/projects/${jobId}/calibration`, { headers: authHeaders() }).then(handle)
}

export function exportProject(jobId, payload) {
  return postJSON(`/projects/${jobId}/export`, payload)
}

export function getPublishConfig() {
  return fetch(`${API}/publish/config`, { headers: authHeaders() }).then(handle)
}

export function getPublishAuthUrl(platform) {
  return fetch(`${API}/publish/${platform}/auth-url`, { headers: authHeaders() }).then(handle)
}

export function getPublishStatus(platform) {
  return fetch(`${API}/publish/${platform}/status`, { headers: authHeaders() }).then(handle)
}

export function disconnectPlatform(platform) {
  return fetch(`${API}/publish/${platform}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(handle)
}

export function getWaveform(jobId) {
  return fetch(`${API}/projects/${jobId}/waveform`, { headers: authHeaders() }).then(handle)
}

export function getClipPublishes(jobId, clipIdx) {
  return fetch(`${API}/projects/${jobId}/clips/${clipIdx}/publishes`, {
    headers: authHeaders(),
  }).then(handle)
}

export function getClipAnalytics(jobId, clipIdx) {
  return fetch(`${API}/projects/${jobId}/clips/${clipIdx}/analytics`, {
    headers: authHeaders(),
  }).then(handle)
}

export function publishClip(jobId, clipIdx, payload) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/publish`, payload)
}

export function getPublishIdeas(jobId, clipIdx, platform) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/publish-ideas`, { platform })
}

export function generateThumbnail(jobId, clipIdx, payload) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/thumbnail`, payload)
}

export function faceswapClip(jobId, clipIdx, file) {
  const form = new FormData()
  form.append('face', file)
  return fetch(`${API}/projects/${jobId}/clips/${clipIdx}/faceswap`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(handle)
}

export function cleanClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/clean`, payload)
}

export function sfxClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/sfx`, payload)
}

export function brollClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/broll`, payload)
}

export function voiceoverClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/voiceover`, payload)
}

export function enhanceClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/enhance`, payload)
}

export function overlayText(jobId, clipIdx, payload) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/overlay`, payload)
}

export function overlayMedia(jobId, clipIdx, file, payload) {
  const form = new FormData()
  form.append('file', file)
  Object.entries(payload).forEach(([k, v]) => {
    if (v !== undefined && v !== null) form.append(k, v)
  })
  return fetch(`${API}/projects/${jobId}/clips/${clipIdx}/overlay/media`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  }).then(handle)
}

export function dubClip(jobId, clipIdx, payload) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/dub`, payload)
}

export function getDubVoices() {
  return fetch(`${API}/dub/voices`).then(handle)
}

export function customCut(jobId, payload) {
  return postJSON(`/projects/${jobId}/cut`, payload)
}

// ─── repurpose / transcript editor / effects ──────────────────────────

export function repurposeProject(jobId, format, clipIdx = null) {
  return postJSON(`/projects/${jobId}/repurpose`, { format, clip_idx: clipIdx })
}

export function effectClip(jobId, clipIdx, payload = {}) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/effects`, payload)
}

export function saveTranscript(jobId, segments) {
  return fetch(`${API}/projects/${jobId}/transcript`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ segments }),
  }).then(handle)
}

export function rerenderProject(jobId) {
  return postJSON(`/projects/${jobId}/rerender`, {})
}

export function editSegments(jobId, clipIdx, segments) {
  return postJSON(`/projects/${jobId}/clips/${clipIdx}/edit-segments`, { segments })
}

export function batchProcess(urls, options) {
  return postJSON('/batch', { urls, options })
}

// ─── Google Sign-In ──────────────────────────────────────────────────

export function getAuthConfig() {
  return fetch(`${API}/auth/config`).then(handle)
}

// ── Mock Payment Gateway (dev/testing only) ──────────────────────────
// Simulasi checkout QRIS/Stripe tanpa payment provider asli. Cocok buat
// staging/dev atau demo produk. Aktif otomatis bila billing DISABLED di server.
const _isMock = (typeof window !== 'undefined') && window.location.search.includes('mock_payments=1')
export function mockCheckout(packId = 'mulai', method = 'qris') {
  return postJSON('/payment/mock/checkout', { pack_id: packId, method })
}
export function mockCheckStatus(txId) {
  return fetch(`${API}/payment/mock/status/${txId}`, { headers: authHeaders() }).then(handle)
}
export function mockApprove(txId) {
  return fetch(`${API}/payment/mock/approve/${txId}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
  }).then(handle)
}
export function mockPayEnabled() { return _isMock }

export function getGoogleAuthUrl() {
  return fetch(`${API}/auth/google/url`).then(handle)
}

export function claimAuthSession(state) {
  return fetch(`${API}/auth/claim?state=${encodeURIComponent(state)}`).then(handle)
}

export function getMe() {
  return fetch(`${API}/auth/me`, { headers: authHeaders() }).then((res) =>
    res.json().then((data) => {
      if (res.ok && data && data.user === null && getAuthToken()) {
        // Token invalid/expired (endpoint returns 200 {user:null}, not 401) —
        // drop it so the UI shows the sign-in prompt instead of a dead session.
        setAuthToken(null)
      }
      if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
      return data
    }),
  )
}

export function logout() {
  return fetch(`${API}/auth/logout`, {
    method: 'POST',
    headers: authHeaders(),
  }).then(handle)
}

export function getFavorites() {
  return fetch(`${API}/criteria/favorites`, { headers: authHeaders() }).then(handle)
}

export function getFavoriteGroups() {
  return fetch(`${API}/criteria/favorites/groups`, { headers: authHeaders() }).then(handle)
}

export function addFavorite(prompt, name, group) {
  return fetch(`${API}/criteria/favorites`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ prompt, name, group }),
  }).then(handle)
}

export function renameFavorite(prompt, name, group, newPrompt) {
  return fetch(`${API}/criteria/favorites/rename`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ prompt, name, group, new_prompt: newPrompt }),
  }).then(handle)
}

export function renameGroup(oldGroup, newGroup) {
  return fetch(`${API}/criteria/favorites/groups/rename`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ old: oldGroup, new: newGroup }),
  }).then(handle)
}

export function clearGroup(group) {
  return fetch(`${API}/criteria/favorites/groups?group=${encodeURIComponent(group)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(handle)
}

export function deleteFavorite(prompt) {
  return fetch(`${API}/criteria/favorites?prompt=${encodeURIComponent(prompt)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(handle)
}

export function pinFavorite(prompt) {
  return fetch(`${API}/criteria/favorites/pin`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ prompt }),
  }).then(handle)
}

export function getBrandKit() {
  return fetch(`${API}/brand-kit`, { headers: authHeaders() }).then(handle)
}

export function putBrandKit(kit) {
  return fetch(`${API}/brand-kit`, {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ kit }),
  }).then(handle)
}

export function listIdeas() {
  return fetch(`${API}/ideas`, { headers: authHeaders() }).then(handle)
}

export function createIdea(payload) {
  return fetch(`${API}/ideas`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  }).then(handle)
}

export function updateIdea(id, payload) {
  return fetch(`${API}/ideas/${id}`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  }).then(handle)
}

export function deleteIdea(id) {
  return fetch(`${API}/ideas/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  }).then(handle)
}

export function pollJob(jobId, { onStatus, onDone, onFail, interval = 1500, signal } = {}) {
  return new Promise((resolve, reject) => {
    // Phone screens dim -> the OS freezes the tab and kills in-flight fetches.
    // A single failed poll must NOT kill the job view: tolerate a burst of
    // errors and only surface failure after ~20s of consecutive misses.
    let misses = 0
    let timer = null
    let settled = false

    const stop = () => {
      settled = true
      if (timer) clearTimeout(timer)
    }

    const schedule = (delay = interval) => {
      if (!settled && !signal?.aborted) timer = setTimeout(tick, delay)
    }

    const tick = async () => {
      try {
        const data = await getStatus(jobId, { signal })
        misses = 0
        onStatus?.(data)
        if (data.status === 'completed') {
          settled = true
          onDone?.(data)
          resolve(data)
        } else if (data.status === 'failed') {
          settled = true
          onFail?.(data)
          reject(new Error(data.error || 'Job failed'))
        } else {
          schedule()
        }
      } catch (e) {
        if (e?.name === 'AbortError' || signal?.aborted) {
          stop()
          resolve(null)
          return
        }
        // A 429 only means the status endpoint asked us to slow down. It says
        // nothing about the render job and must never turn into "job failed".
        if (e?.status === 429) {
          const retryMs = Math.max(interval, (e.retryAfter || 3) * 1000)
          schedule(retryMs)
          return
        }
        // A 404 means the job id exists nowhere (API restarted mid-render and
        // edit jobs live only in memory, or the project was deleted). Polling
        // forever just spins — fail fast with an actionable message instead
        // of burning 12 misses on a job that will never exist.
        if (e?.status === 404) {
          settled = true
          const gone = new Error(
            'Job tidak ditemukan di server (server restart atau proyek dihapus). Silakan kirim ulang.',
          )
          gone.status = 404
          onFail?.(gone)
          reject(gone)
          return
        }
        misses += 1
        if (misses >= 12) {
          settled = true
          onFail?.(e)
          reject(e)
        } else {
          schedule()
        }
      }
    }

    // Recursive timeout guarantees one in-flight status request per poller;
    // setInterval could overlap requests on a slow connection.
    signal?.addEventListener('abort', () => {
      stop()
      resolve(null)
    }, { once: true })
    schedule(0)
  })
}

export function fmtTime(t) {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** Compact numbers: 1234 -> "1,2 rb", 1200000 -> "1,2 jt". */
export function fmtCount(n) {
  if (n === null || n === undefined) return '—'
  return new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

export function timeAgo(ts) {
  if (!ts) return '—'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export function slugify(s) {
  return (s || 'clip')
    .toLowerCase()
    .replace(/[^a-z0-9 _-]/g, '-')
    .replace(/ +/g, '-')
    .slice(0, 40)
}
