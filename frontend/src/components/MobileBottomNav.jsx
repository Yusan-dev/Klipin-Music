import { IS_MUSIC_STANDALONE, toPath } from '../router.js'
import { usePlayer } from '../playerStore.jsx'
import Icon from './Icon.jsx'

/**
 * Single-row, one-handed bottom navigation bar for mobile viewports (< lg).
 * Safe-area padded for notched phones and kept comfortably compact (< 12% vh).
 */
export default function MobileBottomNav({ route, onNavigate, onOpenMenu }) {
  const { track, playing, currentTime, duration, togglePlay, setSheetOpen } = usePlayer()
  const hashSeg = typeof window !== 'undefined'
    ? window.location.hash.replace(/^#\/?/, '').split(/[?/]/)[0]
    : ''

  const items = IS_MUSIC_STANDALONE
    ? [
        {
          key: 'music',
          label: 'Buat',
          icon: 'music',
          path: '#/music',
          active: route.name === 'music' && hashSeg !== 'cover' && hashSeg !== 'history',
        },
        {
          key: 'cover',
          label: 'Cover',
          icon: 'mic',
          path: '#/cover',
          active: route.name === 'music' && hashSeg === 'cover',
        },
        {
          key: 'history',
          label: 'Riwayat',
          icon: 'folder',
          path: '#/history',
          active: route.name === 'music' && hashSeg === 'history',
        },
        {
          key: 'studio',
          label: 'Studio EQ',
          icon: 'sliders',
          path: '#/studio',
          active: route.name === 'studio',
        },
      ]
    : [
        { key: 'home', label: 'Beranda', icon: 'home', path: toPath('home'), active: route.name === 'home' },
        { key: 'music', label: 'Musik', icon: 'music', path: toPath('music'), active: route.name === 'music' },
        { key: 'clip', label: 'Buat Klip', icon: 'scissors', path: toPath('clip'), active: route.name === 'clip' },
        { key: 'studio', label: 'Studio EQ', icon: 'sliders', path: '#/studio', active: route.name === 'studio' },
      ]

  const hasTrack = Boolean(track?.audioUrl)
  const total = Math.max(1, duration || track?.duration || 1)
  const pct = hasTrack ? Math.max(0, Math.min(100, (currentTime / total) * 100)) : 0

  return (
    <nav
      aria-label="Navigasi bawah seluler"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-navy-1/95 backdrop-blur-xl lg:hidden pb-[max(0.35rem,env(safe-area-inset-bottom,0px))]"
    >
      {hasTrack && (
        <div className="h-[2px] w-full bg-border-soft">
          <div
            className="h-full bg-gradient-to-r from-cyan via-gold to-magenta transition-[width] duration-150"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      <div className="mx-auto grid max-w-lg grid-cols-5 items-center px-1.5 pt-1">
        {items.map((item) => (
          <a
            key={item.key}
            href={item.path}
            onClick={(e) => {
              e.preventDefault()
              onNavigate(item.path)
            }}
            aria-current={item.active ? 'page' : undefined}
            className={`mobile-nav-btn flex flex-col items-center justify-center gap-0.5 rounded-xl px-1 py-1 text-[10px] font-medium transition ${
              item.active
                ? 'text-cyan'
                : 'text-white/55 hover:text-white'
            }`}
          >
            <Icon name={item.icon} className="h-5 w-5" />
            <span className="truncate leading-none">{item.label}</span>
          </a>
        ))}

        {/* 5th slot: Quick Play/Now-Playing sheet when a song is active, else Menu drawer */}
        {hasTrack ? (
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            onDoubleClick={togglePlay}
            aria-label="Buka pemutar lagu"
            className="mobile-nav-btn flex flex-col items-center justify-center gap-0.5 rounded-xl px-1 py-1 text-[10px] font-semibold text-cyan transition hover:bg-cyan/10"
          >
            <span className={`relative flex h-5 w-5 items-center justify-center rounded-full border border-cyan/50 bg-cyan/15 ${playing ? 'animate-pulse-soft' : ''}`}>
              <Icon name={playing ? 'pause' : 'play'} className="h-3 w-3" />
            </span>
            <span className="max-w-[58px] truncate leading-none">{playing ? 'Putar' : 'Audio'}</span>
          </button>
        ) : (
          <button
            type="button"
            onClick={onOpenMenu}
            aria-label="Menu lainnya"
            className="mobile-nav-btn flex flex-col items-center justify-center gap-0.5 rounded-xl px-1 py-1 text-[10px] font-medium text-white/55 transition hover:text-white"
          >
            <Icon name="menu" className="h-5 w-5" />
            <span className="leading-none">Menu</span>
          </button>
        )}
      </div>
    </nav>
  )
}
