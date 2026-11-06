import { useCallback, useEffect, useRef, useState } from 'react'
import { claimAuthSession, devLogin, getGoogleAuthUrl, setAuthToken } from '../api.js'
import { Button, Micro, Spinner } from './ui.jsx'
import Icon from './Icon.jsx'

const IS_LOCAL_HOST =
  typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '[::1]')

/**
 * Google Sign-In modal. Opens a popup to Google's consent screen; the
 * backend handles the callback and stashes a fresh session token keyed by
 * the OAuth `state`. We poll /api/auth/claim?state=… (one-time) and store
 * the token in localStorage, then the caller refetches the user.
 */
export default function SignInModal({ open, onClose, onSuccess }) {
  const [phase, setPhase] = useState('idle') // idle | opening | waiting | done | error
  const [error, setError] = useState('')
  const [authUrl, setAuthUrl] = useState('')
  const [devLoading, setDevLoading] = useState(false)
  const timerRef = useRef(null)
  const popupRef = useRef(null)
  const stateRef = useRef('')

  const stop = useCallback(() => {
    clearInterval(timerRef.current)
    timerRef.current = null
  }, [])

  const finishWithToken = useCallback(
    (token) => {
      if (!token) return
      stop()
      setAuthToken(token)
      setPhase('done')
      onSuccess()
    },
    [onSuccess, stop],
  )

  const handleDevLogin = useCallback(async () => {
    setError('')
    setDevLoading(true)
    try {
      const { token } = await devLogin()
      finishWithToken(token)
    } catch (e) {
      setPhase('error')
      setError(e.message || 'Gagal masuk mode studio lokal.')
    } finally {
      setDevLoading(false)
    }
  }, [finishWithToken])

  const begin = useCallback(async () => {
    stop()
    setError('')
    setPhase('opening')
    try {
      const { url, state } = await getGoogleAuthUrl()
      setAuthUrl(url)
      stateRef.current = state
      popupRef.current = window.open(url, 'klipin_google', 'width=520,height=680')
      setPhase('waiting')

      // Do NOT abort when popupRef.current?.closed becomes true:
      // Google's accounts.google.com sends Cross-Origin-Opener-Policy headers
      // which sever WindowProxy in Firefox/Chrome and report .closed === true
      // immediately while the user is still completing sign-in in the popup!
      const deadline = Date.now() + 180_000 // give the user 3 min to consent
      timerRef.current = setInterval(async () => {
        if (Date.now() > deadline) {
          stop()
          setPhase('error')
          setError('Waktu tunggu otorisasi Google habis. Silakan coba lagi.')
          return
        }
        try {
          const { token } = await claimAuthSession(state)
          finishWithToken(token)
        } catch {
          /* claim not ready yet — keep polling */
        }
      }, 1000)
    } catch (e) {
      setPhase('error')
      setError(e.message)
    }
  }, [finishWithToken, stop])

  useEffect(() => {
    if (!open) return undefined
    const onMessage = (ev) => {
      if (ev?.data?.type === 'klipin:auth-done') {
        if (ev.data.token) {
          finishWithToken(ev.data.token)
        } else if (stateRef.current) {
          claimAuthSession(stateRef.current)
            .then(({ token }) => finishWithToken(token))
            .catch(() => {})
        }
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [open, finishWithToken])

  useEffect(() => {
    if (open) begin()
    else {
      stop()
      setPhase('idle')
      setError('')
    }
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const handleClose = () => {
    stop()
    setPhase('idle')
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="sign in with Google"
      onClick={(e) => e.target === e.currentTarget && handleClose()}
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-panel">
        <header className="flex items-center justify-between border-b border-border-soft px-4 py-2.5">
          <Micro className="text-white/60">SYS.AUTH — GOOGLE SIGN-IN</Micro>
          <button
            onClick={handleClose}
            className="font-mono text-xs text-white/40 hover:text-white"
            aria-label="close"
          >
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="space-y-4 p-5 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white/5">
            <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden>
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1Z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z" />
              <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84Z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15A11 11 0 0 0 2.18 7.06L5.84 9.9C6.71 7.31 9.14 5.38 12 5.38Z" />
            </svg>
          </div>

          <div>
            <h2 className="text-lg font-bold tracking-tight">Sign in with Google</h2>
            <p className="mt-1 text-sm text-white/45">
              Lanjutkan dengan Google untuk masuk ke Klipin Music Studio.
            </p>
          </div>

          {phase === 'waiting' && (
            <div className="space-y-2">
              <div className="flex items-center justify-center gap-2 text-sm text-white/55">
                <Spinner className="h-4 w-4" /> Menunggu login Google di jendela popup…
              </div>
              {authUrl && (
                <a
                  href={authUrl}
                  target="klipin_google"
                  rel="noopener noreferrer"
                  className="inline-block text-xs text-signal underline hover:text-signal/80"
                >
                  Popup terblokir browser? Klik di sini untuk membuka login Google
                </a>
              )}
            </div>
          )}
          {phase === 'opening' && (
            <div className="flex items-center justify-center gap-2 text-sm text-white/55">
              <Spinner className="h-4 w-4" /> Membuka jendela Google…
            </div>
          )}
          {phase === 'done' && (
            <div className="text-sm font-semibold text-signal">✓ Berhasil masuk — memuat studio…</div>
          )}
          {phase === 'error' && (
            <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
              {error}
            </p>
          )}

          {phase !== 'done' && (
            <div className="space-y-2.5 pt-1">
              {IS_LOCAL_HOST && (
                <button
                  type="button"
                  onClick={handleDevLogin}
                  disabled={devLoading}
                  className="w-full rounded-md border border-emerald-500/40 bg-emerald-500/15 px-4 py-2.5 text-xs font-bold tracking-wide text-emerald-300 transition hover:bg-emerald-500/25 disabled:opacity-50"
                >
                  {devLoading
                    ? 'Memuat sesi Studio Lokal…'
                    : '⚡ Masuk Instan (Akun Studio Lokal — Tanpa Popup)'}
                </button>
              )}
              <Button className="w-full" onClick={begin}>
                {phase === 'waiting' ? 'Buka Ulang Popup Google' : 'Masuk dengan Google'}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
