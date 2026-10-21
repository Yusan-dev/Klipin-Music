// Minimal toast bus — dispatch an event; <Toasts/> (mounted once in App)
// renders the message and auto-dismisses it. Same event pattern as the
// klipin:* events used across the app, so any module can notify without
// prop drilling.
let seq = 0

// Default auto-dismiss per tone: errors stay longer so they're not missed.
const DEFAULT_DURATION = { ok: 4000, warn: 4000, err: 6000 }

// action: optional {label, path} (hash route to open) or {label, onClick}
// (callback). Rendered as a button inside the toast.
export function showToast(message, tone = 'ok', duration, action) {
  const id = ++seq
  window.dispatchEvent(new CustomEvent('klipin:toast', {
    detail: {
      id,
      message,
      tone,
      duration: duration || DEFAULT_DURATION[tone] || 4000,
      action,
    },
  }))
}
