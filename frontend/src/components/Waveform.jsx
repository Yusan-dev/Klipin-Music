import { useCallback, useMemo, useRef, useState } from 'react'
import { fmtTime } from '../api.js'

/**
 * Interactive amplitude waveform for precise trimming.
 *
 * Props:
 *  peaks     — array of 0..1 amplitudes (from /api/projects/{id}/waveform)
 *  duration  — source duration in seconds
 *  start/end — current trim bounds (seconds)
 *  segments  — [{start, end, text}] transcript regions to overlay
 *  onSeek    — (t) seek the preview video
 *  onChange  — (start, end) new trim bounds (clamped inside)
 *  minLen/maxLen — allowed clip length window (defaults 5 / 180)
 */
export default function Waveform({
  peaks = [], duration = 0, start = 0, end = 0, segments = [],
  onSeek, onChange, minLen = 5, maxLen = 180,
}) {
  const boxRef = useRef(null)
  const [hover, setHover] = useState(null) // { t, x }
  const [drag, setDrag] = useState(null)   // 'start' | 'end' | 'scrub'
  const [scrub, setScrub] = useState(false)

  const W = 1000
  const H = 96
  const M = 6 // vertical margin for peak bars

  const timeFromEvent = useCallback((e) => {
    const rect = boxRef.current.getBoundingClientRect()
    const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width)
    return { t: (x / rect.width) * duration, x }
  }, [duration])

  const clampStart = (t) => Math.max(0, Math.min(t, end - minLen, duration - minLen))
  const clampEnd = (t) => Math.max(start + minLen, Math.min(t, start + maxLen, duration))

  const barW = W / Math.max(1, peaks.length)

  const bars = useMemo(() => {
    const out = []
    for (let i = 0; i < peaks.length; i++) {
      const h = Math.max(1.5, (peaks[i] || 0) * (H - M * 2))
      out.push(
        <rect
          key={i}
          x={i * barW + 0.3}
          y={(H - h) / 2}
          width={Math.max(0.6, barW - 0.6)}
          height={h}
          rx={barW / 3}
        />,
      )
    }
    return out
  }, [peaks, barW])

  // Left/right positions (percent) for handles and selected range
  const startPct = duration ? (start / duration) * 100 : 0
  const endPct = duration ? (end / duration) * 100 : 100

  const beginDrag = (e, mode) => {
    e.preventDefault()
    e.stopPropagation()
    setDrag(mode)
    const move = (ev) => {
      const { t } = timeFromEvent(ev)
      if (mode === 'start') onChange(clampStart(t), end)
      else if (mode === 'end') onChange(start, clampEnd(t))
      else if (mode === 'scrub') { onSeek?.(Math.max(0, Math.min(t, duration))) }
    }
    const up = () => {
      setDrag(null)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    move(e)
  }

  const onMove = (e) => {
    const { t, x } = timeFromEvent(e)
    setHover({ t: Math.max(0, Math.min(t, duration)), x })
    if (drag === 'scrub') onSeek?.(Math.max(0, Math.min(t, duration)))
  }

  const onLeave = () => { setHover(null); setScrub(false) }

  const handlePos = (pct, isStart) => (
    <div
      className="absolute top-0 bottom-0 z-20 w-4 -translate-x-1/2 cursor-ew-resize touch-none"
      style={{ left: `${pct}%` }}
      onPointerDown={(e) => beginDrag(e, isStart ? 'start' : 'end')}
      role="slider"
      aria-label={isStart ? 'trim start' : 'trim end'}
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(isStart ? start : end)}
      tabIndex={0}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 5 : 0.5
        if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
          e.preventDefault()
          isStart ? onChange(clampStart(start - step), end) : onChange(start, clampEnd(end - step))
        } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
          e.preventDefault()
          isStart ? onChange(clampStart(start + step), end) : onChange(start, clampEnd(end + step))
        }
      }}
    >
      <div className="absolute inset-y-0 left-1/2 w-[2px] -translate-x-1/2 bg-cyan" />
      <div className="absolute left-1/2 top-1/2 flex h-6 w-3.5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-sm bg-cyan shadow-md">
        <span className="text-[9px] font-bold leading-none text-[#0B0C0E]">{isStart ? '◀' : '▶'}</span>
      </div>
    </div>
  )

  return (
    <div>
      <div
        ref={boxRef}
        className="relative h-28 touch-none select-none overflow-hidden rounded-md border border-border bg-navy-2"
        onPointerDown={(e) => { setDrag('scrub'); setScrub(true); beginDrag(e, 'scrub') }}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
        onPointerUp={() => { setDrag(null); setScrub(false) }}
      >
        {/* bars */}
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className={`absolute inset-0 h-full w-full ${drag === 'scrub' || scrub ? 'cursor-crosshair' : 'cursor-pointer'}`}
          aria-hidden
        >
          <g fill={drag === 'scrub' ? 'rgba(124,140,248,0.55)' : 'rgba(124,140,248,0.35)'}>
            {bars}
          </g>
        </svg>

        {/* transcript segments (speech regions) */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2.5">
          {segments.map((s, i) => (
            <div
              key={i}
              className="absolute inset-y-0 rounded-[1px] bg-gold/25"
              style={{
                left: `${(s.start / duration) * 100}%`,
                width: `${Math.max(0.4, ((s.end - s.start) / duration) * 100)}%`,
              }}
              title={`${fmtTime(s.start)} – ${fmtTime(s.end)} ${s.text}`}
            />
          ))}
        </div>

        {/* selected range tint */}
        <div
          className="pointer-events-none absolute inset-y-0 z-10 bg-cyan/10"
          style={{ left: `${startPct}%`, width: `${endPct - startPct}%` }}
        >
          <div className="absolute inset-0 border-x border-cyan/50" />
        </div>

        {/* handles */}
        {handlePos(startPct, true)}
        {handlePos(endPct, false)}

        {/* hover tooltip */}
        {hover && (
          <div
            className="pointer-events-none absolute top-1.5 z-30 -translate-x-1/2 rounded border border-border bg-panel px-1.5 py-0.5 font-mono text-[10px] text-white/80"
            style={{ left: `${(hover.t / duration) * 100}%` }}
          >
            {fmtTime(hover.t)}
            <span className="absolute left-1/2 top-full h-1.5 w-px -translate-x-1/2 bg-white/40" />
          </div>
        )}
      </div>

      {/* time ruler */}
      <div className="mt-1 flex justify-between font-mono text-[9px] text-white/25">
        <span>0:00</span>
        <span>{fmtTime(duration / 4)}</span>
        <span>{fmtTime(duration / 2)}</span>
        <span>{fmtTime((duration * 3) / 4)}</span>
        <span>{fmtTime(duration)}</span>
      </div>

      <div className="mt-1.5 flex gap-2 font-mono text-[10px] text-white/35">
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-[1px] bg-gold/40" /> speech</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-[1px] bg-cyan/50" /> selected</span>
        <span className="ml-auto hidden sm:inline">drag handles · click to preview</span>
      </div>
    </div>
  )
}
