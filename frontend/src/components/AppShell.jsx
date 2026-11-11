import { useEffect, useState } from 'react'
import { usePlayer } from '../playerStore.jsx'
import Sidebar from './Sidebar.jsx'
import Topbar from './Topbar.jsx'
import MiniPlayer from './MiniPlayer.jsx'
import MobileBottomNav from './MobileBottomNav.jsx'
import CommandPalette from './CommandPalette.jsx'
import HelpModal from './HelpModal.jsx'

export default function AppShell({ route, navigate, onNewClip, user, authChecked, onSignIn, onSignOut, children }) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  // Desktop sidebar collapse (icon-only rail). Persisted across reloads.
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('klipin_sidebar_collapsed') === '1' } catch { return false }
  })

  // Ctrl/Cmd+K toggles the command palette (global).
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const close = () => setDrawerOpen(false)

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c
      try { localStorage.setItem('klipin_sidebar_collapsed', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }

  const go = (path) => {
    close()
    setPaletteOpen(false)
    if (path === '#/clip') onNewClip()
    else navigate(path)
  }

  const { track } = usePlayer()
  const hasTrack = Boolean(track?.audioUrl)
  const isMusicStudio = route?.name === 'music' || route?.name === 'studio'
  const effectiveCollapsed = isMusicStudio ? true : collapsed

  return (
    <div className="relative isolate min-h-screen overflow-x-hidden bg-navy text-white">
      {/* Ambient Obsidian Vinyl studio backdrop */}
      <div className="klip-bg-lines" aria-hidden="true" />
      <div className="klip-bg-circuit" aria-hidden="true" />
      <div className="klip-bg-vignette" aria-hidden="true" />

      <Sidebar
        route={route}
        collapsed={effectiveCollapsed}
        open={drawerOpen}
        onClose={close}
        onToggle={toggleCollapsed}
        onNavigate={go}
      />

      {/* main column (offset by sidebar rail on desktop) */}
      <div className={`relative z-10 flex min-h-screen flex-col transition-[padding] ${effectiveCollapsed ? 'lg:pl-16' : 'lg:pl-60'}`}>
        <Topbar
          route={route}
          user={user}
          authChecked={authChecked}
          onSignIn={onSignIn}
          onSignOut={onSignOut}
          onMenu={() => setDrawerOpen(true)}
          navigate={navigate}
          onOpenPalette={() => setPaletteOpen(true)}
          onOpenHelp={() => setHelpOpen(true)}
        />
        <main
          className={`flex-1 pb-[calc(4.5rem+env(safe-area-inset-bottom,0px))] ${
            hasTrack ? 'lg:pb-20' : 'lg:pb-6'
          }`}
        >
          {children}
        </main>
      </div>

      <MiniPlayer sidebarCollapsed={effectiveCollapsed} onNavigate={go} />
      <MobileBottomNav route={route} onNavigate={go} onOpenMenu={() => setDrawerOpen(true)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={go} />
      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  )
}
