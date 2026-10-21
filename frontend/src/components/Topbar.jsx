import { useEffect, useState } from 'react'
import { timeAgo } from '../api.js'
import { isSynced, lastSyncAt, loadFavorites, syncFavorites } from '../criteriaStore.js'
import { Button } from './ui.jsx'
import Icon from './Icon.jsx'
import ThemeToggle from './ThemeToggle.jsx'

const CRUMBS = {
  home: 'SYS.DASHBOARD',
  clip: 'SYS.WORKBENCH',
  library: 'SYS.LIBRARY',
  ideas: 'SYS.IDEAS',
  favorites: 'SYS.FAVORITES',
  features: 'SYS.FEATURES',
  music: 'SYS.MUSIC',
  cover: 'SYS.COVER.CREATE',
  pricing: 'SYS.PRICING',
  billing: 'SYS.BILLING',
  settings: 'SYS.SETTINGS',
}

export default function Topbar({ route, user, authChecked, onSignIn, onSignOut, onMenu, navigate, onOpenPalette, onOpenHelp }) {
  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border-soft bg-navy/85 px-4 py-3 backdrop-blur sm:px-6 lg:px-8">
      {/* hamburger (mobile only) */}
      <button
        onClick={onMenu}
        className="rounded-md border border-cyan/30 p-1.5 text-cyan-soft transition hover:border-cyan/60 lg:hidden"
        aria-label="Open navigation"
      >
        <Icon name="menu" className="h-4 w-4" />
      </button>

      <span className="truncate font-mono text-sm uppercase tracking-[0.16em] text-cyan-soft">
        {route.name === 'clip' && route.jobId ? route.jobId.slice(0, 8) : ''}
      </span>

      <div className="ml-auto flex items-center gap-3">
        {onOpenPalette && (
          <button
            type="button"
            onClick={onOpenPalette}
            title="Navigasi cepat (Ctrl+K)"
            aria-label="buka command palette"
            className="hidden items-center gap-1.5 rounded-md border border-border px-2 py-1.5 font-mono text-[10px] uppercase tracking-widest text-white/50 transition hover:border-cyan/40 hover:text-white sm:flex"
          >
            <Icon name="search" className="h-3.5 w-3.5" aria-hidden />
            <span>ctrl k</span>
          </button>
        )}
        {/* quick-access icon buttons — one tap to the most-used actions */}
        <button
          type="button"
          onClick={() => navigate('#/library')}
          title="Library — semua project"
          aria-label="buka library"
          className="hidden rounded-md border border-cyan/25 p-1.5 text-cyan/70 transition hover:border-cyan/60 hover:text-cyan hover:shadow-neon-cyan sm:block"
        >
          <Icon name="library" className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => navigate('#/pricing')}
          title="Harga & kredit"
          aria-label="lihat harga"
          className="hidden rounded-md border border-gold/25 p-1.5 text-gold/80 transition hover:border-gold/60 hover:text-gold hover:shadow-neon-gold md:block"
        >
          <Icon name="pricing" className="h-4 w-4" />
        </button>
        {onOpenHelp && (
          <button
            type="button"
            onClick={onOpenHelp}
            title="Panduan cepat"
            aria-label="buka panduan cepat"
            className="rounded-md border border-border p-1.5 text-white/50 transition hover:border-cyan/50 hover:text-cyan-soft"
          >
            <Icon name="help" className="h-4 w-4" />
          </button>
        )}
        <ThemeToggle />
        <SyncBadge user={user} onOpen={() => navigate('#/settings')} />
        {authChecked && user ? (
          <>
            {user.picture && (
              <img
                src={user.picture}
                alt=""
                referrerPolicy="no-referrer"
                className="h-7 w-7 rounded-full border border-cyan/40"
              />
            )}
            <span className="hidden max-w-[150px] truncate text-sm text-white/60 md:inline">
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
