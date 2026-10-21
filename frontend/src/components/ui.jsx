import { useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'

export function StatusDot({ tone = 'idle', pulse = false, className = '' }) {
  const tones = {
    idle: 'bg-white/20 text-white/20',      // slightly softer in light mode
    ok: 'bg-signal text-signal',
    run: 'bg-gold text-gold',
    err: 'bg-danger text-danger',
    brand: 'bg-cyan text-cyan',
    magenta: 'bg-magenta text-magenta',
  }
  return (
    <span
      aria-hidden
      className={`led inline-block h-1.5 w-1.5 shrink-0 rounded-full ${tones[tone] || tones.idle} ${pulse ? 'animate-pulse-soft' : ''} ${className}`}
    />
  )
}

export function Panel({ label, dot, right, children, className = '', bodyClassName = '' }) {
  return (
    <section className={`card-chrome overflow-hidden rounded-xl ${className}`}>
      {(label || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-border/50 bg-navy-2/60 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-soft/80">
            {dot && <StatusDot tone={dot} />}
            <span className="truncate">{label}</span>
          </div>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={bodyClassName || 'p-4'}>{children}</div>
    </section>
  )
}

export function Micro({ children, className = '' }) {
  return (
    <span className={`font-mono text-xs uppercase tracking-[0.18em] text-cyan-soft/80 ${className}`}>
      {children}
    </span>
  )
}

export function Mono({ children, className = '' }) {
  return <span className={`font-mono text-sm text-white/70 ${className}`}>{children}</span>
}

export function Spinner({ className = '' }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-cyan border-t-transparent ${className}`}
    />
  )
}

export function VUMeter({ className = '', bars = 5 }) {
  const anims = ['animate-vu1', 'animate-vu2', 'animate-vu3']
  return (
    <span aria-hidden className={`flex items-end gap-[3px] ${className}`}>
      {Array.from({ length: bars }).map((_, i) => (
        <span
          key={i}
          className={`vu-bar w-[3px] ${anims[i % anims.length]}`}
          style={{ animationDelay: `${(i * 0.14).toFixed(2)}s` }}
        />
      ))}
    </span>
  )
}

const VARIANTS = {
  primary:
    'bg-gold text-navy font-bold tracking-wide hover:bg-gold-soft shadow-neon-gold disabled:opacity-40 disabled:hover:bg-gold',
  cyan:
    'bg-cyan text-navy font-bold tracking-wide hover:bg-cyan-soft shadow-neon-cyan disabled:opacity-40 disabled:hover:bg-cyan',
  ghost:
    'border border-cyan/35 bg-transparent text-cyan-soft hover:border-cyan/70 hover:text-white hover:shadow-neon-cyan disabled:opacity-40',
  danger:
    'border border-danger/40 bg-danger/10 text-danger hover:bg-danger/20 disabled:opacity-40',
  magenta:
    'bg-magenta text-navy font-bold tracking-wide hover:bg-magenta-soft shadow-neon-magenta disabled:opacity-40 disabled:hover:bg-magenta',
}

export function Button({ variant = 'primary', size = 'md', className = '', ...props }) {
  const sizes = {
    sm: 'px-3 py-1.5 text-sm',
    md: 'px-4 py-2 text-base',
    lg: 'px-6 py-3 text-lg',
  }
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md uppercase font-mono tracking-wider transition-all ${sizes[size]} ${VARIANTS[variant]} ${className}`}
    />
  )
}

export function Logo({ size = 28, variant = 'mark' }) {
  // variant: 'mark' (icon-only, sidebar rail) | 'horizontal' (expanded sidebar)
  const src =
    variant === 'horizontal'
      ? '/brand/logos/logo-horizontal-dark.svg'
      : '/brand/logos/logo-mark.svg'
  const h = variant === 'horizontal' ? Math.max(28, size * 1.1) : size
  return (
    <img
      src={src}
      alt="Klip-in"
      height={h}
      width={variant === 'horizontal' ? 'auto' : h}
      className="block select-none"
      draggable={false}
    />
  )
}

export function Field({ label, children, hint }) {
  return (
    <label className="block">
      <Micro>{label}</Micro>
      <div className="mt-1.5">{children}</div>
      {hint && <p className="mt-1 text-sm text-white/50">{hint}</p>}
    </label>
  )
}

export function Select({ className = '', ...props }) {
  return (
    <select
      {...props}
      className={`w-full rounded-md border border-cyan/25 bg-navy-2 px-2.5 py-2 text-base text-white outline-none transition focus:border-cyan ${className}`}
    />
  )
}

export function TextInput({ className = '', ...props }) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-base text-white outline-none transition placeholder:text-white/30 focus:border-cyan ${className}`}
    />
  )
}

// YouTube-style video player: native controls + a fullscreen toggle button.
// Fullscreen is requested on the WRAPPER so the frame fills the screen with a
// black background and the video switches to object-contain (letterboxed).
export function FullscreenVideo({
  src, poster, controls = true, muted = false, autoPlay = false,
  playsInline = true, preload = 'metadata', videoRef, className = '',
  wrapClassName = 'aspect-[9/16] w-full bg-black', children,
}) {
  const wrapRef = useRef(null)
  const vidRef = useRef(null)
  const [fs, setFs] = useState(false)

  useEffect(() => {
    const on = () => setFs(document.fullscreenElement === wrapRef.current)
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [])

  useEffect(() => {
    if (vidRef.current) vidRef.current.muted = !!muted
  }, [muted])

  const setVid = (el) => {
    vidRef.current = el
    if (videoRef) videoRef.current = el
  }

  const toggle = () => {
    const el = wrapRef.current
    if (!el) return
    if (document.fullscreenElement) document.exitFullscreen?.()
    else el.requestFullscreen?.()
  }

  return (
    <div
      ref={wrapRef}
      className={`relative bg-black ${fs ? 'h-full w-full' : wrapClassName}`}
    >
      <video
        ref={setVid}
        src={src}
        controls={controls}
        muted={muted}
        autoPlay={autoPlay}
        playsInline={playsInline}
        preload={preload}
        poster={poster}
        className={`h-full w-full ${fs ? 'object-contain' : 'object-cover'} ${className}`}
      />
      {children}
      <button
        type="button"
        onClick={toggle}
        title={fs ? 'Keluar fullscreen' : 'Fullscreen'}
        aria-label={fs ? 'exit fullscreen' : 'fullscreen'}
        className="absolute bottom-2 right-2 z-10 rounded bg-black/60 px-1.5 py-1 font-mono text-[10px] text-white/70 text-white-solid transition hover:bg-black/80"
      >
        <Icon name="external" className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
