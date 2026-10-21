// Tiny hash router. Routes:
//   #/               -> home (dashboard)
//   #/clip           -> workbench (new job)
//   #/clip/{jobId}   -> workbench (existing job, deep-link)
//   #/library        -> library
//   #/ideas          -> ideas board / content calendar
//   #/favorites      -> favorites manager (criteria library)
//   #/features       -> features module
//   #/pricing        -> pricing module
//   #/billing        -> billing & subscription page
//   #/settings       -> settings
//   #/music          -> Klipin Music (Buat lagu | Cover | Riwayat)
//   #/cover          -> legacy deep-link, now redirects to Music (Cover tab)
export const IS_MUSIC_STANDALONE = (
  typeof import.meta !== 'undefined'
  && import.meta.env
  && String(import.meta.env.VITE_APP_MODE || '').toLowerCase() === 'music'
)

const MUSIC_ALLOWED = new Set(['music', 'cover', 'pricing', 'billing', 'settings'])

export function parseHash(hash = window.location.hash) {
  const raw = hash.replace(/^#\/?/, '')
  // Klipin Music standalone: no video/clipper routes at all. Anything that is
  // not a music page (dashboard, clip, library, ideas, favorites, features)
  // lands on the Music Studio.
  if (IS_MUSIC_STANDALONE) {
    const seg = raw.split(/[?/]/)[0]
    if (!MUSIC_ALLOWED.has(seg)) return { name: 'music' }
    if (seg === 'cover') return { name: 'music' }
    return { name: seg }
  }
  if (raw === '') return { name: 'home' }
  if (raw === 'clip') return { name: 'clip', jobId: null }
  if (raw.startsWith('clip/')) return { name: 'clip', jobId: raw.slice('clip/'.length) }
  if (raw === 'library') return { name: 'library' }
  if (raw === 'ideas') return { name: 'ideas' }
  if (raw === 'favorites') return { name: 'favorites' }
  if (raw === 'features') return { name: 'features' }
  if (raw === 'pricing') return { name: 'pricing' }
  if (raw === 'billing') return { name: 'billing' }
  if (raw === 'settings') return { name: 'settings' }
  if (raw === 'music') return { name: 'music' }
  if (raw === 'cover') return { name: 'music' }
  return { name: 'home' }
}

export function toPath(name, jobId) {
  if (IS_MUSIC_STANDALONE && !MUSIC_ALLOWED.has(name)) return '#/music'
  if (name === 'home') return '#/'
  if (name === 'clip') return jobId ? `#/clip/${jobId}` : '#/clip'
  if (name === 'library') return '#/library'
  if (name === 'ideas') return '#/ideas'
  if (name === 'favorites') return '#/favorites'
  if (name === 'features') return '#/features'
  if (name === 'pricing') return '#/pricing'
  if (name === 'billing') return '#/billing'
  if (name === 'settings') return '#/settings'
  if (name === 'music') return '#/music'
  if (name === 'cover') return '#/cover'
  return '#/'
}
