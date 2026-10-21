import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'

const TONES = {
  ok: 'border-signal/40 bg-signal/10 text-signal',
  warn: 'border-warn/40 bg-warn/10 text-gold',
  err: 'border-danger/40 bg-danger/10 text-danger',
}

const DURATION = 4000 // fallback when the event carries no explicit duration
const LEAVE_MS = 200 // matches the toast-out animation length
const MAX_VISIBLE = 4
const EXTEND_MS = 5000 // how long a click on the progress bar adds

export default function Toasts() {
  const [toasts, setToasts] = useState([])
  const [leaving, setLeaving] = useState({})
  const [tick, setTick] = useState(0) // drives the countdown progress bars
  const timers = useRef({}) // id -> timeout handle
  const deadlines = useRef({}) // id -> epoch ms the toast auto-dismisses
  const paused = useRef({}) // id -> remaining ms while hover-paused

  // Re-render every 100ms while toasts are visible so the progress bar
  // shrinks smoothly with the remaining time.
  useEffect(() => {
    if (!toasts.length) return
    const iv = setInterval(() => setTick((t) => t + 1), 100)
    return () => clearInterval(iv)
  }, [toasts.length])

  // ms still left (frozen while paused).
  const remainingOf = (t) => {
    const p = paused.current[t.id]
    return p !== undefined ? p : Math.max((deadlines.current[t.id] || 0) - Date.now(), 0)
  }

  // 0..1 fraction of the toast's duration still left (frozen while paused).
  const progressOf = (t) => {
    const total = t.duration || DURATION
    return total > 0 ? Math.min(remainingOf(t) / total, 1) : 0
  }

  const clearTimer = useCallback((id) => {
    if (timers.current[id]) {
      clearTimeout(timers.current[id])
      delete timers.current[id]
    }
  }, [])

  // Two-phase dismiss: play the exit animation, then remove from the stack.
  const dismiss = useCallback((id) => {
    clearTimer(id)
    delete paused.current[id]
    delete deadlines.current[id]
    setLeaving((l) => ({ ...l, [id]: true }))
    setTimeout(() => {
      setLeaving((l) => { const n = { ...l }; delete n[id]; return n })
      setToasts((ts) => ts.filter((x) => x.id !== id))
    }, LEAVE_MS)
  }, [clearTimer])

  // Arm (or re-arm) the auto-dismiss timer for a toast with `ms` left.
  const arm = useCallback((id, ms) => {
    clearTimer(id)
    deadlines.current[id] = Date.now() + ms
    timers.current[id] = setTimeout(() => {
      delete timers.current[id]
      dismiss(id)
    }, ms)
  }, [clearTimer, dismiss])

  // Pause on hover: remember how much time is left, stop the countdown.
  const pause = useCallback((id) => {
    if (paused.current[id] !== undefined) return
    const dl = deadlines.current[id]
    if (dl === undefined) return
    paused.current[id] = Math.max(dl - Date.now(), 0)
    clearTimer(id)
  }, [clearTimer])

  // Resume on mouse-leave: re-arm with the remaining time.
  const resume = useCallback((id) => {
    const remaining = paused.current[id]
    if (remaining === undefined) return
    delete paused.current[id]
    arm(id, remaining)
  }, [arm])

  // Extend on progress-bar click: add `ms` to whatever is left. While hover-
  // paused the frozen remaining time grows; otherwise re-arm from the live
  // remaining time.
  const extend = useCallback((id, ms) => {
    const remaining = remainingOf({ id })
    if (paused.current[id] !== undefined) paused.current[id] = remaining + ms
    else arm(id, remaining + ms)
  }, [arm])

  useEffect(() => {
    const onToast = (e) => {
      const t = e.detail
      setToasts((ts) => [...ts.slice(-(MAX_VISIBLE - 1)), t])
      arm(t.id, t.duration || DURATION)
    }
    window.addEventListener('klipin:toast', onToast)
    return () => window.removeEventListener('klipin:toast', onToast)
  }, [arm])

  void tick // progress is computed from refs on every render
  if (!toasts.length) return null

  return (
    <div className="fixed bottom-4 right-4 z-[60] flex w-72 flex-col gap-2" role="status" aria-live="polite">
      {toasts.map((t) => {
        const isPaused = paused.current[t.id] !== undefined
        return (
          <div
            key={t.id}
            onMouseEnter={() => pause(t.id)}
            onMouseLeave={() => resume(t.id)}
            className={`relative overflow-hidden rounded-md border px-3 py-2 font-mono text-xs shadow-neon-cyan backdrop-blur transition ${
              leaving[t.id] ? 'animate-toast-out' : 'animate-toast-in'
            } ${TONES[t.tone] || TONES.ok}`}
          >
            <div className="flex items-start gap-2">
              <span className="mt-0.5 shrink-0" aria-hidden>
                <Icon
                  name={t.tone === 'ok' ? 'checkcircle' : t.tone === 'err' ? 'nosymbol' : 'warn'}
                  className="h-4 w-4"
                />
              </span>
              <span className="min-w-0 flex-1 leading-snug">{t.message}</span>
              <span
                aria-hidden
                className="shrink-0 self-center font-mono text-[9px] tabular-nums opacity-60"
                title="detik tersisa sebelum auto-dismiss"
              >
                {Math.ceil(remainingOf(t) / 1000)}s
              </span>
              {isPaused && (
                <span
                  className="shrink-0 rounded border border-white/25 bg-white/5 p-0.5 leading-none"
                  title="Countdown dijeda — gerakkan kursor keluar untuk melanjutkan"
                >
                  <Icon name="pause" className="h-3 w-3" />
                </span>
              )}
              {t.action && (
                <button
                  type="button"
                  onClick={() => {
                    dismiss(t.id)
                    if (t.action.onClick) t.action.onClick()
                    else if (t.action.path) window.location.hash = t.action.path
                  }}
                  className="shrink-0 rounded border border-cyan/30 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-cyan-soft transition hover:bg-cyan/10"
                >
                  {t.action.label}
                </button>
              )}
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                aria-label="tutup notifikasi"
                className="shrink-0 p-0.5 text-white/40 transition hover:text-white"
              >
                <Icon name="close" className="h-3 w-3" />
              </button>
            </div>
            {/* countdown progress bar — shrinks with the remaining time; click to extend */}
            <button
              type="button"
              onClick={() => extend(t.id, EXTEND_MS)}
              aria-label="perpanjang waktu notifikasi"
              title={`Klik untuk memperpanjang tampilan (+${EXTEND_MS / 1000} detik)`}
              className="absolute inset-x-0 bottom-0 block h-2 w-full cursor-pointer bg-white/10"
            >
              <span
                aria-hidden
                className={`block h-full bg-current transition-[width] duration-100 ease-linear ${
                  isPaused ? 'opacity-60 animate-pulse-soft' : 'opacity-70'
                }`}
                style={{
                  width: `${progressOf(t) * 100}%`,
                  ...(isPaused
                    ? { backgroundImage: 'repeating-linear-gradient(45deg, currentColor 0 4px, transparent 4px 8px)' }
                    : {}),
                }}
              />
            </button>
          </div>
        )
      })}
    </div>
  )
}
