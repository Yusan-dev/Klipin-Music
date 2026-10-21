import { useEffect, useReducer } from 'react'
import { Logo, StatusDot } from './ui.jsx'
import Icon from './Icon.jsx'
import { getLocale } from '../i18n.js'
import { IS_MUSIC_STANDALONE } from '../router.js'

// Labels follow the active locale (EN default for the global market, ID for
// Indonesian users) — mirroring the Pricing page language toggle.
function menuLabels() {
  const en = {
    grp_workspace: 'Workspace', grp_explore: 'Explore', grp_system: 'System',
    home: 'Dashboard', clip: 'Create Clip', music: 'Music', cover: 'Cover Create',
    library: 'Library', ideas: 'Ideas',
    favorites: 'Favorites', features: 'Features', pricing: 'Pricing',
    billing: 'Billing', settings: 'Settings',
    server: 'server online', per_video: 'per video', per_cost: '±$0.03',
    expand: 'Expand menu', collapse: 'Collapse menu',
  }
  const id = {
    grp_workspace: 'Workspace', grp_explore: 'Jelajah', grp_system: 'Sistem',
    home: 'Dashboard', clip: 'Buat Klip', music: 'Music', cover: 'Buat Cover',
    library: 'Library', ideas: 'Ideas',
    favorites: 'Favorit', features: 'Fitur', pricing: 'Harga',
    billing: 'Tagihan', settings: 'Pengaturan',
    server: 'server online', per_video: 'per video', per_cost: '±Rp25',
    expand: 'Perluas menu', collapse: 'Ciutkan menu',
  }
  return getLocale() === 'id' ? id : en
}

function SidebarBody({ route, collapsed, onNavigate, onToggle }) {
  const [, force] = useReducer((x) => x + 1, 0)
  useEffect(() => {
    const rerender = () => force()
    window.addEventListener('klipin:locale', rerender)
    window.addEventListener('storage', rerender)
    return () => {
      window.removeEventListener('klipin:locale', rerender)
      window.removeEventListener('storage', rerender)
    }
  }, [])
  const L = menuLabels()
  const GROUPS = IS_MUSIC_STANDALONE ? [
    {
      label: L.grp_workspace,
      items: [
        { key: 'music', route: 'music', label: 'Suno Studio', path: '#/music', icon: 'music' },
        { key: 'cover', route: 'music', label: L.cover, path: '#/cover', icon: 'features' },
      ],
    },
    {
      label: L.grp_explore,
      items: [
        { key: 'pricing', route: 'pricing', label: L.pricing, path: '#/pricing', icon: 'pricing' },
        { key: 'billing', route: 'billing', label: L.billing, path: '#/billing', icon: 'billing' },
      ],
    },
    {
      label: L.grp_system,
      items: [
        { key: 'settings', route: 'settings', label: L.settings, path: '#/settings', icon: 'settings' },
      ],
    },
  ] : [
    {
      label: L.grp_workspace,
      items: [
        { key: 'home', route: 'home', label: L.home, path: '#/', icon: 'dashboard' },
        { key: 'clip', route: 'clip', label: L.clip, path: '#/clip', icon: 'plus' },
        { key: 'music', route: 'music', label: L.music, path: '#/music', icon: 'music' },
        { key: 'library', route: 'library', label: L.library, path: '#/library', icon: 'library' },
        { key: 'ideas', route: 'ideas', label: L.ideas, path: '#/ideas', icon: 'ideas' },
        { key: 'favorites', route: 'favorites', label: L.favorites, path: '#/favorites', icon: 'favorites' },
      ],
    },
    {
      label: L.grp_explore,
      items: [
        { key: 'features', route: 'features', label: L.features, path: '#/features', icon: 'features' },
        { key: 'pricing', route: 'pricing', label: L.pricing, path: '#/pricing', icon: 'pricing' },
        { key: 'billing', route: 'billing', label: L.billing, path: '#/billing', icon: 'billing' },
      ],
    },
    {
      label: L.grp_system,
      items: [
        { key: 'settings', route: 'settings', label: L.settings, path: '#/settings', icon: 'settings' },
      ],
    },
  ]
  return (
    <div className="flex h-full flex-col">
      {/* brand — acts as the collapse/expand trigger */}
      <button
        onClick={onToggle}
        className={`relative flex items-center border-b border-border-soft text-left transition hover:bg-white/5 ${
          collapsed ? 'justify-center px-0 py-4' : 'gap-2.5 px-5 py-4'
        }`}
        aria-label={collapsed ? L.expand : L.collapse}
        title={collapsed ? L.expand : L.collapse}
      >
        <Logo size={28} variant={collapsed ? 'mark' : 'horizontal'} />
      </button>

      {/* module nav */}
      <nav className="mt-1 flex-1 overflow-y-auto px-2.5 pb-4" aria-label="Primary">
        {GROUPS.map((g) => (
          <div key={g.label} className="mt-4 first:mt-1">
            {!collapsed && (
              <div className="px-3 pb-1.5 font-mono text-[10px] uppercase tracking-[0.22em] text-white/35">
                {g.label}
              </div>
            )}
            <div className="space-y-1">
              {g.items.map((it) => {
                const active = route.name === it.route
                return (
                  <a
                    key={it.key}
                    href={it.path}
                    onClick={(e) => {
                      e.preventDefault()
                      onNavigate(it.path)
                    }}
                    aria-current={active ? 'page' : undefined}
                    title={collapsed ? it.label : undefined}
                    className={`group relative flex items-center rounded-lg border-l-2 transition-all ${
                      collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5'
                    } ${
                      active
                        ? 'border-cyan bg-cyan/10 text-cyan-soft shadow-neon-cyan'
                        : 'border-transparent text-white/60 hover:bg-white/5 hover:text-white'
                    }`}
                  >
                    <span className={`shrink-0 ${active ? 'text-cyan' : 'text-white/45 group-hover:text-white'}`}>
                      <Icon name={it.icon} className="h-5 w-5" />
                    </span>
                    {!collapsed && <span className="flex-1 text-sm font-medium tracking-wide">{it.label}</span>}
                    {!collapsed && active && <StatusDot tone="brand" pulse className="!m-0" />}
                  </a>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* system status */}
      <div className="space-y-3 border-t border-border-soft px-5 py-4">
        {!collapsed && (
          <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.16em] text-white/40">
            <div className="flex items-center gap-2">
              <StatusDot tone="ok" />
              <span>{L.server}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="chip !px-1.5 !text-[10px]">{L.per_cost}</span>
              <span>{L.per_video}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function Sidebar({ route, collapsed, open, onClose, onToggle, onNavigate }) {
  return (
    <>
      {/* desktop: fixed rail — collapses to icon-only */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 hidden border-r border-border-soft bg-navy-2/70 backdrop-blur transition-[width] lg:block ${
          collapsed ? 'w-16' : 'w-60'
        }`}
      >
        <SidebarBody route={route} collapsed={collapsed} onNavigate={onNavigate} onToggle={onToggle} />
      </aside>

      {/* mobile: drawer + overlay */}
      <div className={`fixed inset-0 z-50 lg:hidden ${open ? '' : 'pointer-events-none'}`} aria-hidden={!open}>
        <div
          className={`absolute inset-0 bg-black/70 transition-opacity ${open ? 'opacity-100' : 'opacity-0'}`}
          onClick={onClose}
        />
        <aside
          className={`absolute inset-y-0 left-0 w-72 border-r border-border-soft bg-navy shadow-neon-cyan transition-transform ${open ? 'translate-x-0' : '-translate-x-full'}`}
        >
          <SidebarBody route={route} collapsed={false} onNavigate={onNavigate} onToggle={onClose} />
        </aside>
      </div>
    </>
  )
}
