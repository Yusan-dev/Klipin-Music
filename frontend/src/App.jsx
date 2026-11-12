import React, { Suspense, lazy, useEffect, useState } from 'react'
import AppShell from './components/AppShell.jsx'
import { Spinner } from './components/ui.jsx'
import SignInModal from './components/SignInModal.jsx'
import Toasts from './components/Toasts.jsx'
import { PlayerProvider } from './playerStore.jsx'
import { resumePendingRetries } from './criteriaStore.js'
import { getAuthToken, getMe, logout, setAuthToken } from './api.js'
import { parseHash, toPath } from './router.js'

// Route-level code splitting keeps mobile first load light & responsive.
const Landing = lazy(() => import('./Landing.jsx'))
const Features = lazy(() => import('./Features.jsx'))
const Pricing = lazy(() => import('./Pricing.jsx'))
const Billing = lazy(() => import('./Billing.jsx'))
const Workbench = lazy(() => import('./Workbench.jsx'))
const Library = lazy(() => import('./Library.jsx'))
const Ideas = lazy(() => import('./Ideas.jsx'))
const Favorites = lazy(() => import('./Favorites.jsx'))
const Settings = lazy(() => import('./Settings.jsx'))
const Music = lazy(() => import('./Music.jsx'))
const StudioWorkstation = lazy(() => import('./StudioWorkstation.jsx'))
const ShareSong = lazy(() => import('./ShareSong.jsx'))

function PageLoader() {
  return (
    <div className="flex min-h-[45vh] items-center justify-center gap-3 text-sm text-white/60">
      <Spinner className="h-5 w-5" />
      <span>Memuat studio…</span>
    </div>
  )
}

// Minimal error boundary — a failing module renders a visible message instead
// of unmounting the whole tree into a blank page (dev-mode silent crash).
class Boundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { err: null }
  }
  static getDerivedStateFromError(err) {
    return { err }
  }
  render() {
    if (this.state.err) {
      return (
        <div className="mx-auto max-w-2xl p-8 text-center font-mono text-sm text-danger">
          <p>Komponen gagal dimuat.</p>
          <pre className="mt-2 whitespace-pre-wrap bg-navy-2 p-3 text-left text-xs text-white/70">
            {String(this.state.err?.message || this.state.err)}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  // Workbench remounts on every navigation into it via a nonce.
  const [navNonce, setNavNonce] = useState(0)
  // A URL to prefill when arriving at #/clip from the landing hero.
  const [workbenchUrl, setWorkbenchUrl] = useState(null)
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [signInOpen, setSignInOpen] = useState(false)

  // Sync route from hash changes (browser back/forward, manual edits).
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Resume queued sync retries (backoff) left over from a previous session.
  useEffect(() => {
    resumePendingRetries()
  }, [])

  // Restore session on load; listen for 401s that drop the token.
  useEffect(() => {
    let alive = true

    const restore = () => {
      getMe()
        .then((d) => alive && setUser(d.user || null))
        .catch(() => {
          // Network error (backend restarting / tunnel down): DON'T show the
          // user as signed-out — keep the token, retry in a few seconds.
          if (getAuthToken()) {
            setTimeout(() => alive && restore(), 3000)
            return
          }
          alive && setUser(null)
        })
        .finally(() => alive && setAuthChecked(true))
    }
    restore()

    const onUnauthorized = () => setUser(null)
    window.addEventListener('klipin:unauthorized', onUnauthorized)
    return () => {
      alive = false
      window.removeEventListener('klipin:unauthorized', onUnauthorized)
    }
  }, [])

  const handleSignOut = async () => {
    try { await logout() } catch { /* token already invalid */ }
    setAuthToken(null)
    setUser(null)
  }

  const onAuthSuccess = () => {
    setSignInOpen(false)
    getMe()
      .then((d) => setUser(d.user || null))
      .catch(() => setUser(null))
      .finally(() => window.dispatchEvent(new CustomEvent('klipin:authenticated')))
  }

  // Navigate to a route. If the target already equals the current hash, we
  // still refresh the route state so the app reacts to repeated clicks.
  const navigate = (path) => {
    if (window.location.hash === path) {
      setRoute(parseHash(path))
    } else {
      window.location.hash = path
    }
  }

  // Wrap the raw `startNew` so a navigation triggered from a button inside a
  // route (e.g. Pricing "Mulai pakai") goes through the same hashchange flow
  // as a sidebar click. Without this, React unmounts InputStage mid-sync and
  // the guest localStorage access races the reload -> blank root.
  const startNewSafe = (url) => {
    if (window.location.hash === toPath('clip')) {
      setRoute({ name: 'clip', jobId: null })
    }
    startNew(url)
  }

  const openLibrary = () => navigate(toPath('library'))
  const openIdeas = () => navigate(toPath('ideas'))
  const openFeatures = () => navigate(toPath('features'))
  const openPricing = () => navigate(toPath('pricing'))
  const openSettings = () => navigate(toPath('settings'))
  const openProject = (jobId) => {
    setWorkbenchUrl(null)
    setNavNonce((n) => n + 1)
    navigate(toPath('clip', jobId))
  }
  const startNew = (url) => {
    setWorkbenchUrl(url || null)
    setNavNonce((n) => n + 1)
    navigate(toPath('clip'))
  }

  const workbenchKey = `${route.jobId || 'new'}-${navNonce}`

  return (
    <PlayerProvider>
      <AppShell
        route={route}
        navigate={navigate}
        onNewClip={() => startNew(null)}
        user={user}
        authChecked={authChecked}
        onSignIn={() => setSignInOpen(true)}
        onSignOut={handleSignOut}
      >
        <Boundary key={`${route.name}-${route.jobId || ''}-${navNonce}`}>
          <Suspense fallback={<PageLoader />}>
            {route.name === 'home' && (
              <Landing
                onStart={startNew}
                onStartFile={() => startNew(null)}
                onOpen={openProject}
                onOpenLibrary={openLibrary}
                onOpenFeatures={openFeatures}
                onOpenPricing={openPricing}
              />
            )}
            {route.name === 'features' && (
              <Features onStart={startNewSafe} />
            )}
            {route.name === 'pricing' && (
              <Pricing onStart={startNewSafe} />
            )}
            {route.name === 'billing' && (
              <Billing
                user={user}
                onSignIn={() => setSignInOpen(true)}
              />
            )}
            {route.name === 'clip' && (
              <Workbench
                key={workbenchKey}
                onOpenLibrary={openLibrary}
                onOpenProject={openProject}
                initialJobId={route.jobId}
                initialUrl={workbenchUrl}
                user={user}
                authChecked={authChecked}
                onSignIn={() => setSignInOpen(true)}
              />
            )}
            {route.name === 'library' && (
              <Library
                onHome={startNew}
                onOpen={openProject}
                user={user}
                onSignIn={() => setSignInOpen(true)}
              />
            )}
            {route.name === 'ideas' && (
              <Ideas
                onOpenProject={openProject}
                user={user}
                onSignIn={() => setSignInOpen(true)}
              />
            )}
            {route.name === 'favorites' && (
              <Favorites
                user={user}
                onSignIn={() => setSignInOpen(true)}
              />
            )}
            {route.name === 'settings' && (
              <Settings
                user={user}
                authChecked={authChecked}
                onSignIn={() => setSignInOpen(true)}
                onSignOut={handleSignOut}
              />
            )}
            {route.name === 'music' && (
              <Music user={user} authChecked={authChecked} onSignIn={() => setSignInOpen(true)} />
            )}
            {route.name === 'studio' && (
              <StudioWorkstation
                user={user}
                authChecked={authChecked}
                onSignIn={() => setSignInOpen(true)}
                initialJobId={route.jobId}
                navigate={navigate}
              />
            )}
            {route.name === 'song' && (
              <ShareSong jobId={route.jobId} navigate={navigate} />
            )}
          </Suspense>
        </Boundary>
        <SignInModal
          open={signInOpen}
          onClose={() => setSignInOpen(false)}
          onSuccess={onAuthSuccess}
        />
        <Toasts />
      </AppShell>
    </PlayerProvider>
  )
}
