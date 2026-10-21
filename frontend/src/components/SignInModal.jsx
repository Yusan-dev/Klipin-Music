import { useCallback, useEffect, useRef, useState } from 'react'
import { claimAuthSession, getGoogleAuthUrl, setAuthToken } from '../api.js'
import { Button, Micro, Spinner } from './ui.jsx'
import Icon from './Icon.jsx'

/**
 * Google Sign-In modal. Opens a popup to Google's consent screen; the
 * backend handles the callback and stashes a fresh session token keyed by
 * the OAuth `state`. We poll /api/auth/claim?state=… (one-time) and store
 * the token in localStorage, then the caller refetches the user.
 */
export default function SignInModal({ open, onClose, onSuccess }) {
  const [phase, setPhase] = useState('idle') // idle | opening | waiting | done | error
  const [error, setError] = useState('')
  const timerRef = useRef(null)
  const popupRef = useRef(null)

  const stop = useCallback(() => {
    clearInterval(timerRef.current)
    timerRef.current = null
  }, [])

  const begin = useCallback(async () => {
    setError('')
    setPhase('opening')
    try {
      const { url, state } = await getGoogleAuthUrl()
      popupRef.current = window.open(url, 'klipin_google', 'width=520,height=680')
      setPhase('waiting')

      const deadline = Date.now() + 180_000 // give the user 3 min to consent
      timerRef.current = setInterval(async () => {
        // Popup closed without finishing → give up quietly.
        if (popupRef.current?.closed) {
          stop()
          setPhase('idle')
          onClose()
          return
        }
        if (Date.now() > deadline) {
          stop()
          setPhase('error')
          setError('Timed out waiting for Google authorization.')
          return
        }
        try {
          const { token } = await claimAuthSession(state)
          stop()
          setAuthToken(token)
          setPhase('done')
          onSuccess()
        } catch {
          /* claim not ready yet — keep polling */
        }
      }, 1000)
    } catch (e) {
      setPhase('error')
      setError(e.message)
    }
  }, [onClose, onSuccess, stop])

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

  const busy = phase === 'opening' || phase === 'waiting'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="sign in with Google"
      onClick={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-panel">
        <header className="flex items-center justify-between border-b border-border-soft px-4 py-2.5">
          <Micro className="text-white/60">SYS.AUTH — GOOGLE SIGN-IN</Micro>
          <button
            onClick={onClose}
            disabled={busy}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
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
              Continue to Google to authorize Klipin. No password ever touches this server —
              only Google's session token.
            </p>
          </div>

          {phase === 'waiting' && (
            <div className="flex items-center justify-center gap-2 text-sm text-white/55">
              <Spinner className="h-4 w-4" /> Waiting for Google… complete the popup to continue.
            </div>
          )}
          {phase === 'opening' && (
            <div className="flex items-center justify-center gap-2 text-sm text-white/55">
              <Spinner className="h-4 w-4" /> Opening Google…
            </div>
          )}
          {phase === 'done' && (
            <div className="text-sm font-semibold text-signal">✓ Signed in — continuing…</div>
          )}
          {phase === 'error' && (
            <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
              {error}
            </p>
          )}

          {!busy && phase !== 'done' && (
            <Button className="w-full" onClick={begin}>
              Retry sign-in
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
