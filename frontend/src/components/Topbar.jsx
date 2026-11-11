import { useEffect, useState } from 'react'
import { timeAgo } from '../api.js'
import { isSynced, lastSyncAt, loadFavorites, syncFavorites } from '../criteriaStore.js'
import { Button } from './ui.jsx'
import Icon from './Icon.jsx'
import ThemeToggle from './ThemeToggle.jsx'
import { IS_MUSIC_STANDALONE } from '../router.js'

const PAGE_TITLES = {
  home: 'Dashboard',
  clip: 'Video Workbench',
  library: 'Library',
  ideas: 'Ideas Board',
  favorites: 'Favorit',
  features: 'Fitur',
  music: 'Klip-in Music',
  cover: 'Cover Studio',
  studio: 'Studio EQ',
  pricing: 'Paket & Kredit',
  billing: 'Tagihan',
  settings: 'Pengaturan',
}

export default function Topbar({ route, user, authChecked, onSignIn, onSignOut, onMenu, navigate, onOpenPalette, onOpenHelp }) {
  const hashSeg = typeof window !== 'undefined'
    ? window.location.hash.replace(/^#\/?/, '').split(/[?/]/)[0]
    : ''
  const titleKey = route.name === 'music' && hashSeg === 'cover' ? 'cover' : route.name
  const pageTitle = PAGE_TITLES[titleKey] || 'Klip-in Studio'

  return (
    <header className="sticky top-0 z-30 flex h-[54px] items-center gap-3 border-b border-border-soft bg-navy/90 px-3.5 backdrop-blur-md sm:px-6">
      {/* hamburger (mobile only) */}
      <button
        onClick={onMenu}
        className="touch-hit inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-navy-2/80 text-white/80 transition hover:border-cyan/50 hover:text-white lg:hidden"
        aria-label="Buka navigasi"
      >
        <Icon name="menu" className="h-5 w-5" />
      </button>

      <div className="flex min-w-0 items-center gap-2">
        <span className="font-display truncate text-sm font-bold tracking-tight text-white sm:text-[15px]">
          {pageTitle}
        </span>
        {route.name === 'clip' && route.jobId && (
          <span className="hidden rounded-md bg-navy-2 px-1.5 py-0.5 font-mono text-[10px] text-white/55 sm:inline">
            {route.jobId.slice(0, 8)}
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2 sm:gap-2.5">
        {onOpenPalette && (
          <button
            type="button"
            onClick={onOpenPalette}
            title="Navigasi cepat (Ctrl+K)"
            aria-label="buka command palette"
            className="hidden items-center gap-1.5 rounded-lg border border-border bg-navy-2/70 px-2.5 py-1.5 font-mono text-[11px] text-white/55 transition hover:border-cyan/45 hover:text-white sm:flex"
          >
            <Icon name="search" className="h-3.5 w-3.5" aria-hidden />
            <span>Ctrl+K</span>
          </button>
        )}
        {/* quick-access icon buttons — one tap to the most-used actions */}
        {!IS_MUSIC_STANDALONE && (
        <button
          type="button"
          onClick={() => navigate('#/library')}
          title="Library — semua project"
          aria-label="buka library"
          className="hidden rounded-lg border border-border bg-navy-2/70 p-2 text-white/65 transition hover:border-cyan/50 hover:text-cyan sm:block"
        >
          <Icon name="library" className="h-4 w-4" />
        </button>
        )}
        <button
          type="button"
          onClick={() => navigate('#/pricing')}
          title="Harga & kredit"
          aria-label="lihat harga"
          className="hidden rounded-lg border border-border bg-navy-2/70 p-2 text-gold/85 transition hover:border-gold/50 hover:text-gold md:block"
        >
          <Icon name="pricing" className="h-4 w-4" />
        </button>
        {onOpenHelp && (
          <button
            type="button"
            onClick={onOpenHelp}
            title="Panduan cepat"
            aria-label="buka panduan cepat"
            className="touch-hit hidden rounded-lg border border-border bg-navy-2/70 p-2 text-white/55 transition hover:border-cyan/50 hover:text-cyan-soft sm:inline-flex"
          >
            <Icon name="help" className="h-4 w-4" />
          </button>
        )}
        <ThemeToggle compact />
        <span className="hidden sm:inline-flex">
          <SyncBadge user={user} onOpen={() => navigate('#/settings')} />
        </span>
        {authChecked && user ? (
          <>
            {user.picture && (
              <img
                src={user.picture}
                alt=""
                referrerPolicy="no-referrer"
                className="h-8 w-8 rounded-full border border-cyan/40"
              />
            )}
            <span className="hidden max-w-[140px] truncate text-xs font-medium text-white/75 md:inline">
              {user.name || user.email}
            </span>
            <Button variant="ghost" size="sm" onClick={onSignOut}>Keluar</Button>
          </>
        ) : authChecked ? (
          <Button variant="ghost" size="sm" onClick={onSignIn}>Masuk</Button>
        ) : null}
      </div>
    </header>
  )
}

/**
 * Compact global sync indicator: cloud icon when everything is on the account,
 * cloud n · upload m when some favorites are still local-only, upload when signed out.
 * Live-updates via the klipin:criteria-synced event; click → Settings sync panel.
 */
function SyncBadge({ user, onOpen }) {
  const [favorites, setFavorites] = useState(loadFavorites)
  const [syncTick, setSyncTick] = useState(0)

  useEffect(() => {
    let alive = true
    syncFavorites().then((l) => { if (alive) setFavorites(l) })
    const onSync = () => setSyncTick((t) => t + 1)
    window.addEventListener('klipin:criteria-synced', onSync)
    return () => { alive = false; window.removeEventListener('klipin:criteria-synced', onSync) }
  }, [])
  void syncTick // favorites + badge state are read fresh from the store each render

  const synced = favorites.filter((f) => isSynced(f.prompt)).length
  const localOnly = favorites.length - synced
  const allSynced = user && favorites.length > 0 && synced === favorites.length
  const mixed = user && favorites.length > 0 && !allSynced
  const last = lastSyncAt()

  const title = !user
    ? 'Belum masuk — data tersimpan lokal di perangkat ini'
    : favorites.length === 0
      ? 'Terhubung ke akun — belum ada favorit'
      : allSynced
        ? `Semua favorit tersinkron ke akun · terakhir ${timeAgo(last / 1000)}`
        : `${localOnly} favorit belum tersinkron ke akun (${synced} di akun) — klik untuk detail`

  return (
    <button
      type="button"
      onClick={onOpen}
      title={title}
      aria-label="status sinkronisasi — klik untuk detail"
      className={`flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-[10px] transition ${
        allSynced
          ? 'border-cyan/40 bg-cyan/10 text-cyan-soft'
          : mixed
            ? 'border-warn/40 bg-warn/10 text-gold animate-pulse-soft'
            : 'border-border-soft bg-navy-2/60 text-white/45 hover:border-cyan/40 hover:text-white'
      }`}
    >
      {!user ? (
        <Icon name="upload" className="h-3.5 w-3.5" />
      ) : allSynced ? (
        <Icon name="cloud" className="h-3.5 w-3.5" />
      ) : mixed ? (
        <>
          <Icon name="warn" className="h-3.5 w-3.5" />
          <Icon name="cloud" className="h-3.5 w-3.5" />
          <span>{synced}</span>
          <Icon name="upload" className="h-3.5 w-3.5" />
          <span>{localOnly}</span>
        </>
      ) : (
        <Icon name="cloud" className="h-3.5 w-3.5" />
      )}
    </button>
  )
}
