// Brand kit (caption defaults) — localStorage-first for instant reads, PLUS
// account sync so the kit follows the user across devices. Same pattern as
// criteriaStore: local write immediately, server push best-effort, merge on
// sync with last-write-wins via timestamps.
//
// Stored shape: {color, caption_font, caption_size, watermark, emoji}.
import { getAuthToken, getBrandKit, putBrandKit } from './api.js'
import { showToast } from './toast.js'

const KEY = 'klipin_brand_kit'
const UPDATED_KEY = 'klipin_brand_kit_updated' // ms epoch of last local edit
const SYNCED_KEY = 'klipin_brand_kit_synced' // ms epoch of last server-confirmed sync

export function loadKit() {
  try {
    const kit = JSON.parse(localStorage.getItem(KEY))
    return kit && typeof kit === 'object' ? kit : {}
  } catch {
    return {}
  }
}

// Epoch ms of the last server-confirmed kit state (null = never synced).
export function lastKitSyncAt() {
  const v = Number(localStorage.getItem(SYNCED_KEY) || 0)
  return v > 0 ? v : null
}

function markSynced() {
  localStorage.setItem(SYNCED_KEY, String(Date.now()))
  window.dispatchEvent(new CustomEvent('klipin:brand-kit-synced'))
}

function markUnsynced() {
  localStorage.removeItem(SYNCED_KEY)
  window.dispatchEvent(new CustomEvent('klipin:brand-kit-synced'))
}

// Save locally + push to the account (best-effort). Never blocks the UI.
export function saveKit(kit) {
  const clean = kit && typeof kit === 'object' ? kit : {}
  localStorage.setItem(KEY, JSON.stringify(clean))
  localStorage.setItem(UPDATED_KEY, String(Date.now()))
  if (getAuthToken()) {
    putBrandKit(clean)
      .then(() => markSynced())
      .catch(() => {
        markUnsynced()
        showToast('✗ Gagal menyinkronkan brand kit — cek koneksi', 'err')
      })
  } else {
    markUnsynced()
  }
  return clean
}

// Two-way sync with the account: last write wins by timestamp. The server
// returns updated_at (seconds); local edits are tracked in ms.
export function syncBrandKit() {
  if (!getAuthToken()) return Promise.resolve(loadKit())
  return getBrandKit()
    .then((d) => {
      const server = d.kit && typeof d.kit === 'object' ? d.kit : {}
      const serverAt = (d.updated_at || 0) * 1000
      const localAt = Number(localStorage.getItem(UPDATED_KEY) || 0)
      if (Object.keys(server).length && serverAt >= localAt) {
        localStorage.setItem(KEY, JSON.stringify(server))
        markSynced()
        return server
      }
      // Local is newer (or server empty) → push local up.
      const local = loadKit()
      if (!Object.keys(local).length) {
        markSynced()
        return server
      }
      return putBrandKit(local)
        .then((d2) => {
          markSynced()
          return (d2.kit && typeof d2.kit === 'object') ? d2.kit : local
        })
    })
    .catch(() => {
      markUnsynced()
      return loadKit()
    })
}

// Sync once after login so another device's kit applies immediately.
if (typeof window !== 'undefined') {
  window.addEventListener('klipin:authenticated', () => { syncBrandKit() })
}
