// Favorite custom AI criteria — localStorage (same pattern as the brand kit)
// for instant local access, PLUS server sync when signed in so favorites
// follow the user across devices.
//
// Stored shape: array of {prompt, name, group} where `name` is an optional
// editable label and `group` an optional folder label (per niche, e.g.
// "finansial", "komedi"). The prompt stays the identity; name/group are
// display/organization only. Old string[] and {prompt,name} entries are
// migrated on load.
//
// API shape: getFavorites/getFavoriteGroups/addFavorite/deleteFavorite hit
// /api/criteria/favorites and 401 silently when there's no session (handle()
// drops the token) — so callers can just try and fall back to local-only.
import {
  addFavorite, clearGroup as clearGroupRemote, deleteFavorite,
  getFavoriteGroups, getFavorites, getAuthToken,
  pinFavorite as pinFavoriteRemote, renameFavorite as renameFavoriteRemote,
  renameGroup as renameGroupRemote,
} from './api.js'
import { showToast } from './toast.js'

const KEY = 'klipin_criteria_favorites'
const SYNC_KEY = 'klipin_criteria_synced' // prompts confirmed stored on the server
const LAST_SYNC_KEY = 'klipin_criteria_last_sync' // ms epoch of last server-confirmed sync
const MAX = 12

function loadSyncedSet() {
  try {
    const arr = JSON.parse(localStorage.getItem(SYNC_KEY))
    return new Set(Array.isArray(arr) ? arr : [])
  } catch {
    return new Set()
  }
}

function persistSyncedSet(set) {
  localStorage.setItem(SYNC_KEY, JSON.stringify([...set]))
  // Every caller reaches this point after the server confirmed a write, so
  // stamp the last-sync time here (only meaningful while signed in).
  if (getAuthToken()) localStorage.setItem(LAST_SYNC_KEY, String(Date.now()))
  window.dispatchEvent(new CustomEvent('klipin:criteria-synced'))
}

// Failure toast with a short debounce — several components pull on mount,
// and a single offline moment shouldn't stack error toasts.
let lastFailToast = 0
function failToast() {
  const now = Date.now()
  if (now - lastFailToast < 8000) return
  lastFailToast = now
  showToast('✗ Gagal menyinkronkan ke akun — cek koneksi', 'err', undefined, {
    label: 'Coba lagi',
    onClick: retryPendingNow,
  })
}

// ── automatic retry with exponential backoff ─────────────────────────────
// A failed server write is enqueued (localStorage survives reloads) and
// replayed later with growing delays until it succeeds or gives up (the
// amber "belum tersinkron" banners + manual Sync now are the fallback).
const PENDING_KEY = 'klipin_criteria_pending'
const BACKOFF = [5000, 15000, 30000, 60000, 120000, 300000] // 5s → 5min
const MAX_ATTEMPTS = BACKOFF.length

function loadPending() {
  try {
    const arr = JSON.parse(localStorage.getItem(PENDING_KEY))
    return Array.isArray(arr) ? arr : []
  } catch {
    return []
  }
}

function savePending(queue) {
  localStorage.setItem(PENDING_KEY, JSON.stringify(queue))
}

// op -> raw api call. `rename` covers label/move-prompt/rename-prompt (the
// server treats missing fields as untouched, so a partial entry is fine).
const OPS = {
  save: (e) => addFavorite(e.prompt, e.name || '', e.group || ''),
  remove: (e) => deleteFavorite(e.prompt),
  rename: (e) => renameFavoriteRemote(e.prompt, e.name || '', e.group, e.newPrompt),
  pin: (e) => pinFavoriteRemote(e.prompt),
  renameGroup: (e) => renameGroupRemote(e.old, e.new),
  clearGroup: (e) => clearGroupRemote(e.group),
}

function enqueuePending(entry) {
  const q = loadPending()
  if (q.some((e) => e.key === entry.key)) return // keep earliest attempt count
  q.push({ attempts: 0, nextAt: Date.now(), ...entry })
  savePending(q)
  ensureScheduler()
}

function dropPendingByKey(key) {
  savePending(loadPending().filter((e) => e.key !== key))
}

function dropPendingByPrompt(prompt) {
  const text = (prompt || '').trim()
  savePending(loadPending().filter((e) => !e.prompt || e.prompt !== text))
}

function dropPendingWhere(pred) {
  savePending(loadPending().filter((e) => !pred(e)))
}

function bumpAttempts(key) {
  const q = loadPending()
  const i = q.findIndex((e) => e.key === key)
  if (i < 0) return
  const e = q[i]
  if (e.attempts + 1 >= MAX_ATTEMPTS) {
    q.splice(i, 1) // gave up — banners + manual Sync now remain
  } else {
    e.attempts += 1
    e.nextAt = Date.now() + BACKOFF[e.attempts - 1]
  }
  savePending(q)
}

// Replay one queued write. Resolves true on success, false on failure.
function replay(entry) {
  const fn = OPS[entry.op]
  if (!fn) {
    dropPendingByKey(entry.key)
    return Promise.resolve(true)
  }
  return fn(entry)
    .then((d) => {
      persistSyncedSet(new Set((d.favorites || []).map((f) => f.prompt)))
      persist((d.favorites || []).map(asFav).slice(0, MAX))
      dropPendingByKey(entry.key)
      return true
    })
    .catch(() => {
      bumpAttempts(entry.key)
      return false
    })
}

let retryTimer = null

function ensureScheduler() {
  if (retryTimer || !loadPending().length) return
  retryTimer = setTimeout(runRetries, 1000)
}

async function runRetries() {
  retryTimer = null
  const q = loadPending()
  if (!q.length) return
  const now = Date.now()
  const due = q.filter((e) => e.nextAt <= now)
  let recovered = 0
  for (const e of due) {
    if (await replay(e)) recovered += 1
  }
  if (recovered > 0) {
    showToast(`✓ Sinkronisasi pulih — ${recovered} perubahan tersinkron ke akun`)
  }
  const remaining = loadPending()
  if (remaining.length) {
    const soonest = Math.min(...remaining.map((e) => e.nextAt - now))
    retryTimer = setTimeout(runRetries, Math.max(soonest, 1000))
  }
}

let onlineHandler = null

// Fire-and-forget wrapper used by every .catch: toast (debounced) + enqueue.
function failSync(entry) {
  failToast()
  enqueuePending(entry)
}

// Start (or resume) the retry loop after a reload, and retry immediately
// when the browser reports the connection is back.
export function resumePendingRetries() {
  if (!onlineHandler) {
    onlineHandler = () => {
      if (!loadPending().length) return
      savePending(loadPending().map((e) => ({ ...e, nextAt: 0 })))
      ensureScheduler()
    }
    window.addEventListener('online', onlineHandler)
  }
  ensureScheduler()
}

// Manual retry: make every queued write due right now and kick the
// scheduler (used by the failure toast's "Coba lagi" action).
export function retryPendingNow() {
  if (!loadPending().length) return false
  savePending(loadPending().map((e) => ({ ...e, nextAt: 0 })))
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  ensureScheduler()
  return true
}

// Epoch ms of the last server-confirmed sync (null = never synced).
export function lastSyncAt() {
  const v = Number(localStorage.getItem(LAST_SYNC_KEY) || 0)
  return v > 0 ? v : null
}

// Is this prompt confirmed as stored on the server (synced to the account)?
export function isSynced(prompt) {
  const text = (prompt || '').trim()
  return text ? loadSyncedSet().has(text) : false
}

// True when a session token is present (favorites sync to the account).
export function isSignedIn() {
  return !!getAuthToken()
}

function asFav(item) {
  if (typeof item === 'string') return { prompt: item.trim(), name: '', group: '' } // legacy
  return {
    prompt: String(item?.prompt || '').trim(),
    name: String(item?.name || '').trim(),
    group: String(item?.group || item?.grp || '').trim(),
  }
}

export function loadFavorites() {
  try {
    const arr = JSON.parse(localStorage.getItem(KEY))
    if (!Array.isArray(arr)) return []
    return arr.map(asFav).filter((f) => f.prompt)
  } catch {
    return []
  }
}

function persist(list) {
  localStorage.setItem(KEY, JSON.stringify(list))
  return list
}

// Distinct group labels currently in use (local, newest-appearing first).
export function listGroups() {
  const seen = []
  for (const f of loadFavorites()) {
    if (f.group && !seen.includes(f.group)) seen.push(f.group)
  }
  return seen
}

export function saveFavorite(prompt, name = '', group = '') {
  const text = (prompt || '').trim()
  const local = loadFavorites().filter((f) => f.prompt !== text)
  const existing = local.find((f) => f.prompt === text)
  const entry = {
    prompt: text,
    name: (name || '').trim() || existing?.name || '',
    group: (group || '').trim() || existing?.group || '',
  }
  const list = text ? [entry, ...local].slice(0, MAX) : local
  persist(list)
  // Best-effort server sync — never blocks the UI.
  if (text && getAuthToken()) {
    // A later save supersedes a queued remove for the same prompt.
    dropPendingWhere((e) => e.op === 'remove' && e.prompt === text)
    addFavorite(text, entry.name, entry.group)
      .then((d) => {
        const set = loadSyncedSet()
        set.add(text)
        persistSyncedSet(set)
        persist((d.favorites || []).map(asFav).slice(0, MAX))
        showToast('✓ Tersimpan & tersinkron ke akun')
      })
      .catch(() => failSync({ op: 'save', key: `save:${text}`, prompt: text, name: entry.name, group: entry.group }))
  }
  return list
}

export function renameFavorite(prompt, name) {
  const text = (prompt || '').trim()
  const list = persist(loadFavorites().map((f) =>
    f.prompt === text ? { ...f, name: (name || '').trim() } : f))
  if (text && getAuthToken()) {
    renameFavoriteRemote(text, (name || '').trim())
      .then((d) => persist((d.favorites || []).map(asFav).slice(0, MAX)))
      .catch(() => failSync({ op: 'rename', key: `rename:${text}`, prompt: text, name: (name || '').trim() }))
  }
  return list
}

// Pin a favorite to the front of its own group row (folder rows keep their
// order). No-op when it's already the first item of its row. Mirrors
// db.pin_favorite so local (logged-out) and synced results match.
export function pinFavorite(prompt) {
  const text = (prompt || '').trim()
  const local = loadFavorites()
  const idx = local.findIndex((f) => f.prompt === text)
  if (idx < 0) return local
  const target = local[idx]
  const rest = local.filter((f) => f.prompt !== text)
  const firstSame = rest.findIndex((f) => (f.group || '') === (target.group || ''))
  if (firstSame < 0) return local // only item in its row — already pinned
  const list = [...rest.slice(0, firstSame), target, ...rest.slice(firstSame)]
  persist(list)
  if (text && getAuthToken()) {
    pinFavoriteRemote(text)
      .then((d) => persist((d.favorites || []).map(asFav).slice(0, MAX)))
      .catch(() => failSync({ op: 'pin', key: `pin:${text}`, prompt: text }))
  }
  return list
}

// Move a favorite into a group (or out of any group with ''). Order kept.
export function setFavoriteGroup(prompt, group) {
  const text = (prompt || '').trim()
  const entry = loadFavorites().find((f) => f.prompt === text)
  const list = persist(loadFavorites().map((f) =>
    f.prompt === text ? { ...f, group: (group || '').trim() } : f))
  if (text && getAuthToken()) {
    // Send the current name too — the server treats missing name as
    // untouched, but '' would wipe it.
    renameFavoriteRemote(text, entry?.name || '', (group || '').trim())
      .then((d) => persist((d.favorites || []).map(asFav).slice(0, MAX)))
      .catch(() => failSync({ op: 'rename', key: `rename:${text}`, prompt: text, name: entry?.name || '', group: (group || '').trim() }))
  }
  return list
}

// Change the prompt text itself (identity). Name/group kept; duplicates are
// merged (the renamed entry survives at its own position).
export function renamePrompt(prompt, newPrompt) {
  const text = (prompt || '').trim()
  const next = (newPrompt || '').trim()
  const local = loadFavorites()
  const idx = local.findIndex((f) => f.prompt === text)
  if (!next || idx < 0) return local
  const entry = { ...local[idx], prompt: next }
  const others = local.filter((f) => f.prompt !== text && f.prompt !== next)
  const list = persist([...others.slice(0, idx), entry, ...others.slice(idx)])
  // move the sync marker old -> new
  const set = loadSyncedSet()
  if (set.has(text)) {
    set.delete(text)
    set.add(next)
    persistSyncedSet(set)
  }
  // Queued writes for the old identity are stale now.
  dropPendingByPrompt(text)
  if (text && getAuthToken()) {
    // Replay must re-run against the server's old prompt (text -> next).
    renameFavoriteRemote(text, entry.name, entry.group, next)
      .then((d) => {
        persistSyncedSet(new Set((d.favorites || []).map((f) => f.prompt)))
        persist((d.favorites || []).map(asFav).slice(0, MAX))
      })
      .catch(() => failSync({ op: 'rename', key: `rename:${next}`, prompt: text, name: entry.name, group: entry.group, newPrompt: next }))
  }
  return list
}

// Rename a folder: all favorites in it move to the new label ('' = ungroup).
export function renameGroup(oldGroup, newGroup) {
  const oldG = (oldGroup || '').trim()
  const newG = (newGroup || '').trim()
  const list = persist(loadFavorites().map((f) =>
    f.group === oldG ? { ...f, group: newG } : f))
  if (oldG && getAuthToken()) {
    renameGroupRemote(oldG, newG)
      .then((d) => persist((d.favorites || []).map(asFav).slice(0, MAX)))
      .catch(() => failSync({ op: 'renameGroup', key: `renameGroup:${oldG}`, old: oldG, new: newG }))
  }
  return list
}

// Remove a folder (ungroup its favorites back to Inbox).
export function clearGroup(group) {
  const g = (group || '').trim()
  const list = persist(loadFavorites().map((f) =>
    f.group === g ? { ...f, group: '' } : f))
  if (g && getAuthToken()) {
    clearGroupRemote(g)
      .then((d) => persist((d.favorites || []).map(asFav).slice(0, MAX)))
      .catch(() => failSync({ op: 'clearGroup', key: `clearGroup:${g}`, group: g }))
  }
  return list
}

export function removeFavorite(prompt) {
  const text = (prompt || '').trim()
  const list = persist(loadFavorites().filter((f) => f.prompt !== text))
  // Local removal supersedes any queued writes for this prompt.
  dropPendingByPrompt(text)
  if (text && getAuthToken()) {
    deleteFavorite(text)
      .then((d) => {
        const set = loadSyncedSet()
        set.delete(text)
        persistSyncedSet(set)
        persist((d.favorites || []).map(asFav).slice(0, MAX))
      })
      .catch(() => failSync({ op: 'remove', key: `remove:${text}`, prompt: text }))
  }
  return list
}

// Pull the user's server favorites (when signed in) and merge with local
// ones, preferring the server's name/group labels. Local-only favorites
// (failed writes, guest saves) are pushed up, so a manual Sync now is a
// complete two-way sync. Call on mount so another device's saves appear
// immediately.
export function syncFavorites() {
  if (!getAuthToken()) return Promise.resolve(loadFavorites())
  return getFavorites()
    .then((d) => {
      const server = (d.favorites || []).map(asFav)
      const localOnly = loadFavorites().filter((f) => !server.some((s) => s.prompt === f.prompt))
      if (!localOnly.length) {
        persistSyncedSet(new Set(server.map((f) => f.prompt)))
        return persist(mergeLists(server))
      }
      return Promise.all(localOnly.map((f) => addFavorite(f.prompt, f.name, f.group)
        .then(() => ({ ok: true, prompt: f.prompt }))
        .catch(() => ({ ok: false, prompt: f.prompt }))))
        .then((results) => {
          const pushed = results.filter((r) => r.ok).map((r) => r.prompt)
          return getFavorites().then((d2) => {
            const all = (d2.favorites || []).map(asFav)
            persistSyncedSet(new Set(all.map((f) => f.prompt)))
            // Queued writes for prompts that just reached the server are done.
            dropPendingWhere((e) => pushed.includes(e.prompt))
            return persist(mergeLists(all))
          })
        })
    })
    .catch((e) => {
      failToast()
      return loadFavorites()
    })
}

// Server list first, then any local extras that aren't on the server yet.
function mergeLists(server) {
  const merged = [...server]
  for (const f of loadFavorites()) {
    if (!merged.some((m) => m.prompt === f.prompt)) merged.push(f)
  }
  return merged.slice(0, MAX)
}

// Display label: custom name if set, else first ~5 words of the prompt.
export function favoriteLabel(fav) {
  const name = typeof fav === 'string' ? '' : String(fav?.name || '').trim()
  if (name) return name.length > 40 ? `${name.slice(0, 40)}…` : name
  const prompt = typeof fav === 'string' ? fav : String(fav?.prompt || '')
  const words = prompt.trim().split(/\s+/)
  const label = words.slice(0, 5).join(' ')
  return label.length > 40 ? `${label.slice(0, 40)}…` : label
}
