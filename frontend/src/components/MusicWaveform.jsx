import { useEffect, useRef, useState } from 'react'

/**
 * Modern music waveform visualizer (SoundCloud / Suno style).
 *
 * Decodes the finished song into amplitude peaks ONCE (cached per URL) and
 * draws a static waveform with a live playhead. It deliberately does NOT route
 * the <audio> element through the Web Audio graph: routing a cross-origin media
 * element without CORS silently mutes playback. Fetch/decode only feeds the
 * drawing, so if it fails the sound keeps working and we just show a playhead.
 *
 * Props: src, audioRef, playing, currentTime, duration, className
 */
const PEAK_CACHE = new Map()
const TARGET_BARS = 480

async function computePeaks(src) {
  if (PEAK_CACHE.has(src)) return PEAK_CACHE.get(src)
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) throw new Error('Web Audio API tidak tersedia')
  const res = await fetch(src)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const buffer = await res.arrayBuffer()
  const ctx = new Ctx()
  try {
    const audio = await ctx.decodeAudioData(buffer)
    const channel = audio.getChannelData(0)
    const buckets = Math.min(TARGET_BARS, Math.max(64, Math.floor(channel.length / 2048)))
    const size = Math.max(1, Math.floor(channel.length / buckets))
    const peaks = new Float32Array(buckets)
    let max = 0
    for (let b = 0; b < buckets; b += 1) {
      let m = 0
      const start = b * size
      const end = Math.min(channel.length, start + size)
      for (let i = start; i < end; i += 2) {
        const v = Math.abs(channel[i])
        if (v > m) m = v
      }
      peaks[b] = m
      if (m > max) max = m
    }
    const norm = max > 0 ? 1 / max : 1
    for (let i = 0; i < buckets; i += 1) peaks[i] *= norm
    PEAK_CACHE.set(src, peaks)
    return peaks
  } finally {
    try { ctx.close() } catch { /* noop */ }
  }
}

export default function MusicWaveform({
  src = '', audioRef, playing = false, currentTime = 0, duration = 0, className = '',
}) {
  const canvasRef = useRef(null)
  const peaksRef = useRef(null)
  const rafRef = useRef(0)
  const [status, setStatus] = useState('idle')

  useEffect(() => {
    if (!src) { peaksRef.current = null; setStatus('idle'); return undefined }
    if (PEAK_CACHE.has(src)) { peaksRef.current = PEAK_CACHE.get(src); setStatus('ready'); return undefined }
    let live = true
    setStatus('loading')
    computePeaks(src)
      .then((peaks) => { if (live) { peaksRef.current = peaks; setStatus('ready') } })
      .catch(() => { if (live) setStatus('failed') })
    return () => { live = false }
  }, [src])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const ctx2d = canvas.getContext('2d')
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const paint = () => {
      const el = audioRef?.current
      const total = (el && Number.isFinite(el.duration) && el.duration > 0 ? el.duration : duration) || 0
      const now = el ? el.currentTime : currentTime
      const rect = canvas.getBoundingClientRect()
      const w = Math.max(1, Math.round(rect.width))
      const h = Math.max(1, Math.round(rect.height))
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr
        canvas.height = h * dpr
      }
      ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx2d.clearRect(0, 0, w, h)

      const peaks = peaksRef.current
      const progress = total > 0 ? Math.max(0, Math.min(1, now / total)) : 0

      if (peaks && peaks.length) {
        const gap = 1.5
        const barW = Math.max(1, (w - gap * peaks.length) / peaks.length)
        for (let i = 0; i < peaks.length; i += 1) {
          const bh = Math.max(2, peaks[i] * h * 0.88)
          const x = i * (barW + gap)
          const played = (i + 1) / peaks.length <= progress
          if (played) {
            const g = ctx2d.createLinearGradient(0, h, 0, 0)
            g.addColorStop(0, 'rgba(0,240,255,0.9)')
            g.addColorStop(0.6, 'rgba(255,46,174,0.8)')
            g.addColorStop(1, 'rgba(255,212,0,0.7)')
            ctx2d.fillStyle = g
          } else {
            ctx2d.fillStyle = 'rgba(255,255,255,0.22)'
          }
          ctx2d.fillRect(x, (h - bh) / 2, barW, bh)
        }
      } else {
        ctx2d.fillStyle = 'rgba(255,255,255,0.12)'
        ctx2d.fillRect(0, h / 2 - 1, w, 2)
      }

      if (total > 0) {
        const px = progress * w
        ctx2d.strokeStyle = 'rgba(255,255,255,0.95)'
        ctx2d.lineWidth = 1.5
        ctx2d.beginPath()
        ctx2d.moveTo(px, 0)
        ctx2d.lineTo(px, h)
        ctx2d.stroke()
      }

      if (playing) rafRef.current = requestAnimationFrame(paint)
    }

    paint()
    return () => cancelAnimationFrame(rafRef.current)
  }, [status, playing, currentTime, duration, audioRef])

  return (
    <div className={`music-waveform-wrap ${playing ? 'playing' : ''} relative h-16 w-full overflow-hidden rounded-xl border border-white/10 bg-black/30 ${className}`}>
      <canvas ref={canvasRef} className="absolute inset-0 block h-full w-full" />
      {status === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center font-mono text-[10px] text-white/30">
          Menyiapkan waveform…
        </div>
      )}
    </div>
  )
}
