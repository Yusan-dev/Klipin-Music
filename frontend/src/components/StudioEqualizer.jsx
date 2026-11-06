import { useState, useEffect, useRef, useCallback } from 'react'

export const STUDIO_EQ_FREQS = [
  { freq: 60, label: '60', sub: 'Sub' },
  { freq: 150, label: '150', sub: 'Bass' },
  { freq: 400, label: '400', sub: 'Low-Mid' },
  { freq: 1000, label: '1k', sub: 'Vocal' },
  { freq: 2400, label: '2.4k', sub: 'Presence' },
  { freq: 5000, label: '5k', sub: 'Clarity' },
  { freq: 10000, label: '10k', sub: 'Brilliance' },
  { freq: 16000, label: '16k', sub: 'Air' },
]

export const STUDIO_EQ_PRESETS = [
  {
    id: 'flat',
    label: '🎚️ Studio Flat',
    desc: 'Respons frekuensi netral 0 dB tanpa pewarnaan tambahan.',
    bands: [0, 0, 0, 0, 0, 0, 0, 0],
    preamp_db: 0,
    compressor: 0,
    stereo_width: 1.0,
    reverb: 0,
  },
  {
    id: 'reggae-dub',
    label: '🌴 Reggae Dub Bass (BOENTER)',
    desc: 'Sub-bass bulat hangat (60-150Hz), anti-mud 400Hz, vokal santai, dan treble analog lembut.',
    bands: [4.5, 3.2, -2.0, -0.5, 1.2, 1.5, -1.8, -2.5],
    preamp_db: 0.5,
    compressor: 0.35,
    stereo_width: 1.24,
    reverb: 0.15,
  },
  {
    id: 'vocal-clarity',
    label: '🎤 Vocal Clarity & Presence',
    desc: 'Angkat artikulasi vokal (1k-5kHz) dan pangkas frekuensi dengung low-mid.',
    bands: [-2.5, -1.5, -2.0, 1.5, 3.5, 3.0, 2.0, 1.5],
    preamp_db: 0,
    compressor: 0.45,
    stereo_width: 1.1,
    reverb: 0.08,
  },
  {
    id: 'koplo-punch',
    label: '🥁 Koplo Sub Punch',
    desc: 'Tendangan kendang & sub-bass nendang (60-150Hz) dengan simbal renyah.',
    bands: [5.0, 3.5, -2.5, 0.5, 2.5, 3.2, 2.5, 2.0],
    preamp_db: 0.5,
    compressor: 0.5,
    stereo_width: 1.18,
    reverb: 0.05,
  },
  {
    id: 'warm-analog',
    label: '📼 Warm Analog Tape',
    desc: 'Karakter pita analog hangat: low-mid tebal dan roll-off frekuensi tinggi yang tajam.',
    bands: [2.5, 2.8, 1.0, 0.5, -0.8, -1.5, -2.2, -3.5],
    preamp_db: 0.5,
    compressor: 0.3,
    stereo_width: 1.12,
    reverb: 0.1,
  },
  {
    id: 'hifi-master',
    label: '✨ Hi-Fi Loudness Master',
    desc: 'Kurva mastering modern: sub ketat, mid bersih, dan air 10k-16kHz berkilau.',
    bands: [3.0, 1.5, -1.5, 0.0, 1.8, 2.5, 3.2, 3.5],
    preamp_db: 0,
    compressor: 0.4,
    stereo_width: 1.2,
    reverb: 0.05,
  },
  {
    id: 'acoustic-intimate',
    label: '🎸 Acoustic Intimate',
    desc: 'Petikan gitar & vokal akustik dekat di telinga dengan ruang stereo alami.',
    bands: [-1.0, 1.5, -1.2, 1.2, 2.2, 2.0, 2.5, 1.8],
    preamp_db: 0,
    compressor: 0.25,
    stereo_width: 1.15,
    reverb: 0.12,
  },
]

// Cache Web Audio graph per HTMLAudioElement so createMediaElementSource is never called twice
const AUDIO_GRAPH_MAP = new WeakMap()

function getOrCreateAudioGraph(audioEl) {
  if (!audioEl || typeof window === 'undefined') return null
  if (AUDIO_GRAPH_MAP.has(audioEl)) {
    return AUDIO_GRAPH_MAP.get(audioEl)
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext
  if (!AudioCtx) return null
  try {
    const ctx = new AudioCtx()
    const source = ctx.createMediaElementSource(audioEl)
    const preGain = ctx.createGain()

    const filters = STUDIO_EQ_FREQS.map((item, idx) => {
      const f = ctx.createBiquadFilter()
      if (idx === 0) {
        f.type = 'lowshelf'
      } else if (idx === STUDIO_EQ_FREQS.length - 1) {
        f.type = 'highshelf'
      } else {
        f.type = 'peaking'
        f.Q.value = 1.25
      }
      f.frequency.value = item.freq
      f.gain.value = 0
      return f
    })

    const compressor = ctx.createDynamicsCompressor()
    compressor.threshold.value = -12
    compressor.knee.value = 18
    compressor.ratio.value = 1
    compressor.attack.value = 0.015
    compressor.release.value = 0.18

    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    analyser.smoothingTimeConstant = 0.82

    // Connect chain: source -> preGain -> f0..f7 -> compressor -> analyser -> destination
    source.connect(preGain)
    let prev = preGain
    for (const f of filters) {
      prev.connect(f)
      prev = f
    }
    prev.connect(compressor)
    compressor.connect(analyser)
    analyser.connect(ctx.destination)

    const graph = { ctx, source, preGain, filters, compressor, analyser }
    AUDIO_GRAPH_MAP.set(audioEl, graph)
    return graph
  } catch (err) {
    console.warn('[StudioEqualizer] Web Audio API fallback:', err?.message || err)
    return null
  }
}

export default function StudioEqualizer({
  audioRef,
  jobId = '',
  genre = '',
  initialEq = null,
  onChange = null,
  onApplyServerEq = null,
  compact = false,
  title = 'STUDIO EQUALIZER & MASTERING RACK',
}) {
  const [enabled, setEnabled] = useState(true)
  const [preset, setPreset] = useState(() => initialEq?.preset || 'flat')
  const [bands, setBands] = useState(() =>
    Array.isArray(initialEq?.bands) && initialEq.bands.length === 8
      ? initialEq.bands.map((n) => Number(n) || 0)
      : [0, 0, 0, 0, 0, 0, 0, 0]
  )
  const [preampDb, setPreampDb] = useState(() => Number(initialEq?.preamp_db) || 0)
  const [compressor, setCompressor] = useState(() => Number(initialEq?.compressor) || 0)
  const [stereoWidth, setStereoWidth] = useState(() => Number(initialEq?.stereo_width) || 1.0)
  const [reverb, setReverb] = useState(() => Number(initialEq?.reverb) || 0)
  const [normalizeLufs, setNormalizeLufs] = useState(true)
  const [applying, setApplying] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [vuLevel, setVuLevel] = useState({ peak: -60, rms: -60 })

  const canvasRef = useRef(null)
  const animRef = useRef(null)

  // Sync when song changes and carries saved metadata.studio_eq
  useEffect(() => {
    if (initialEq && typeof initialEq === 'object') {
      if (Array.isArray(initialEq.bands) && initialEq.bands.length === 8) {
        setBands(initialEq.bands.map((n) => Number(n) || 0))
      }
      if (initialEq.preset) setPreset(initialEq.preset)
      if (initialEq.preamp_db !== undefined) setPreampDb(Number(initialEq.preamp_db) || 0)
      if (initialEq.compressor !== undefined) setCompressor(Number(initialEq.compressor) || 0)
      if (initialEq.stereo_width !== undefined) setStereoWidth(Number(initialEq.stereo_width) || 1.0)
      if (initialEq.reverb !== undefined) setReverb(Number(initialEq.reverb) || 0)
    }
    setStatusMsg('')
    setErrorMsg('')
  }, [jobId])

  // Notify parent form when EQ changes (e.g. CoverCreate / Music form)
  const emitChange = useCallback(
    (nextState) => {
      if (typeof onChange === 'function') {
        onChange(nextState)
      }
    },
    [onChange]
  )

  // Apply real-time Web Audio API filter gains whenever bands / preamp / compressor / enabled change
  useEffect(() => {
    const audioEl = audioRef?.current
    if (!audioEl) return

    const syncWebAudio = () => {
      const graph = getOrCreateAudioGraph(audioEl)
      if (!graph) return
      if (graph.ctx.state === 'suspended' && !audioEl.paused) {
        graph.ctx.resume().catch(() => {})
      }
      const now = graph.ctx.currentTime || 0
      const linearGain = enabled ? Math.pow(10, preampDb / 20) : 1.0
      graph.preGain.gain.setTargetAtTime(linearGain, now, 0.02)

      graph.filters.forEach((f, idx) => {
        const targetGain = enabled ? Number(bands[idx] || 0) : 0
        f.gain.setTargetAtTime(targetGain, now, 0.02)
      })

      const compAmt = enabled ? Math.max(0, Math.min(1, compressor)) : 0
      graph.compressor.threshold.setTargetAtTime(-10 - compAmt * 18, now, 0.03)
      graph.compressor.ratio.setTargetAtTime(1 + compAmt * 5, now, 0.03)
    }

    syncWebAudio()
    const onPlay = () => {
      const graph = getOrCreateAudioGraph(audioEl)
      if (graph && graph.ctx.state === 'suspended') {
        graph.ctx.resume().catch(() => {})
      }
      syncWebAudio()
    }
    audioEl.addEventListener('play', onPlay)
    return () => {
      audioEl.removeEventListener('play', onPlay)
    }
  }, [audioRef, bands, preampDb, compressor, enabled, jobId])

  // Draw live FFT spectrum + EQ Frequency Response Curve on Canvas
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx2d = canvas.getContext('2d')
    if (!ctx2d) return

    const freqData = new Uint8Array(256)
    const timeData = new Uint8Array(256)

    const renderFrame = () => {
      const w = canvas.width
      const h = canvas.height
      ctx2d.clearRect(0, 0, w, h)

      // Background grid
      ctx2d.fillStyle = '#090b10'
      ctx2d.fillRect(0, 0, w, h)

      ctx2d.strokeStyle = 'rgba(255, 255, 255, 0.06)'
      ctx2d.lineWidth = 1
      for (const frac of [0.2, 0.5, 0.8]) {
        const y = Math.round(h * frac) + 0.5
        ctx2d.beginPath()
        ctx2d.moveTo(0, y)
        ctx2d.lineTo(w, y)
        ctx2d.stroke()
      }
      // 0 dB center line
      ctx2d.strokeStyle = 'rgba(124, 92, 255, 0.28)'
      ctx2d.setLineDash([4, 4])
      ctx2d.beginPath()
      ctx2d.moveTo(0, h / 2)
      ctx2d.lineTo(w, h / 2)
      ctx2d.stroke()
      ctx2d.setLineDash([])

      // Live FFT Spectrum if audio is playing
      const audioEl = audioRef?.current
      const graph = audioEl ? AUDIO_GRAPH_MAP.get(audioEl) : null
      if (graph?.analyser && audioEl && !audioEl.paused) {
        graph.analyser.getByteFrequencyData(freqData)
        graph.analyser.getByteTimeDomainData(timeData)

        // Compute RMS & Peak for VU meter
        let sumSq = 0
        let maxAbs = 0
        for (let i = 0; i < timeData.length; i++) {
          const sample = (timeData[i] - 128) / 128
          const abs = Math.abs(sample)
          sumSq += sample * sample
          if (abs > maxAbs) maxAbs = abs
        }
        const rms = Math.sqrt(sumSq / timeData.length)
        const rmsDb = Math.max(-60, Math.min(0, 20 * Math.log10(rms + 1e-5)))
        const peakDb = Math.max(-60, Math.min(0, 20 * Math.log10(maxAbs + 1e-5)))
        setVuLevel({ peak: Math.round(peakDb), rms: Math.round(rmsDb) })

        const barCount = 48
        const barW = w / barCount
        for (let i = 0; i < barCount; i++) {
          const binIdx = Math.min(freqData.length - 1, Math.floor(Math.pow(i / barCount, 1.55) * (freqData.length * 0.78)))
          const val = freqData[binIdx] / 255
          const barH = val * (h * 0.86)
          const grad = ctx2d.createLinearGradient(0, h, 0, h - barH)
          grad.addColorStop(0, 'rgba(16, 185, 129, 0.22)')
          grad.addColorStop(0.6, 'rgba(124, 92, 255, 0.38)')
          grad.addColorStop(1, 'rgba(244, 63, 94, 0.55)')
          ctx2d.fillStyle = grad
          ctx2d.fillRect(i * barW + 1, h - barH, Math.max(2, barW - 2), barH)
        }
      }

      // Draw 8-Band EQ Response Curve
      const pts = STUDIO_EQ_FREQS.map((_, idx) => {
        const x = ((idx + 0.5) / STUDIO_EQ_FREQS.length) * w
        const db = enabled ? Number(bands[idx] || 0) : 0
        const y = h / 2 - (db / 14) * (h / 2)
        return { x, y, db }
      })

      // Fill under curve
      ctx2d.beginPath()
      ctx2d.moveTo(0, h / 2)
      ctx2d.lineTo(pts[0].x, pts[0].y)
      for (let i = 0; i < pts.length - 1; i++) {
        const xc = (pts[i].x + pts[i + 1].x) / 2
        const yc = (pts[i].y + pts[i + 1].y) / 2
        ctx2d.quadraticCurveTo(pts[i].x, pts[i].y, xc, yc)
      }
      ctx2d.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y)
      ctx2d.lineTo(w, h / 2)
      ctx2d.closePath()
      const curveFill = ctx2d.createLinearGradient(0, 0, 0, h)
      curveFill.addColorStop(0, enabled ? 'rgba(16, 185, 129, 0.26)' : 'rgba(148, 163, 184, 0.08)')
      curveFill.addColorStop(0.5, 'rgba(124, 92, 255, 0.05)')
      curveFill.addColorStop(1, enabled ? 'rgba(244, 63, 94, 0.22)' : 'rgba(148, 163, 184, 0.08)')
      ctx2d.fillStyle = curveFill
      ctx2d.fill()

      // Stroke EQ curve
      ctx2d.beginPath()
      ctx2d.moveTo(0, pts[0].y)
      ctx2d.lineTo(pts[0].x, pts[0].y)
      for (let i = 0; i < pts.length - 1; i++) {
        const xc = (pts[i].x + pts[i + 1].x) / 2
        const yc = (pts[i].y + pts[i + 1].y) / 2
        ctx2d.quadraticCurveTo(pts[i].x, pts[i].y, xc, yc)
      }
      ctx2d.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y)
      ctx2d.lineTo(w, pts[pts.length - 1].y)
      ctx2d.strokeStyle = enabled ? '#10b981' : '#64748b'
      ctx2d.lineWidth = 2.2
      ctx2d.stroke()

      // Draw nodes
      for (const p of pts) {
        ctx2d.beginPath()
        ctx2d.arc(p.x, p.y, 4, 0, Math.PI * 2)
        ctx2d.fillStyle = enabled ? (p.db > 0 ? '#10b981' : p.db < 0 ? '#f59e0b' : '#a78bfa') : '#64748b'
        ctx2d.fill()
        ctx2d.strokeStyle = '#090b10'
        ctx2d.lineWidth = 1.5
        ctx2d.stroke()
      }

      animRef.current = requestAnimationFrame(renderFrame)
    }

    animRef.current = requestAnimationFrame(renderFrame)
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current)
    }
  }, [audioRef, bands, enabled])

  const selectPreset = (presetId) => {
    const found = STUDIO_EQ_PRESETS.find((p) => p.id === presetId)
    if (!found) return
    setPreset(found.id)
    const nextBands = [...found.bands]
    setBands(nextBands)
    setPreampDb(found.preamp_db)
    setCompressor(found.compressor)
    setStereoWidth(found.stereo_width)
    setReverb(found.reverb)
    setStatusMsg('')
    setErrorMsg('')
    emitChange({
      preset: found.id,
      bands: nextBands,
      preamp_db: found.preamp_db,
      compressor: found.compressor,
      stereo_width: found.stereo_width,
      reverb: found.reverb,
      normalize_lufs: normalizeLufs,
    })
  }

  const updateBand = (index, rawVal) => {
    const val = Math.max(-12, Math.min(12, Math.round(Number(rawVal) * 2) / 2))
    const next = bands.map((b, i) => (i === index ? val : b))
    setBands(next)
    setPreset('custom')
    setStatusMsg('')
    emitChange({
      preset: 'custom',
      bands: next,
      preamp_db: preampDb,
      compressor,
      stereo_width: stereoWidth,
      reverb,
      normalize_lufs: normalizeLufs,
    })
  }

  const handleApplyToServer = async (reset = false) => {
    if (!jobId || typeof onApplyServerEq !== 'function') return
    setApplying(true)
    setErrorMsg('')
    setStatusMsg('')
    try {
      const payload = reset
        ? {
            preset: 'flat',
            bands: [0, 0, 0, 0, 0, 0, 0, 0],
            preamp_db: 0,
            compressor: 0,
            stereo_width: 1.0,
            reverb: 0,
            normalize_lufs: true,
            reset: true,
          }
        : {
            preset,
            bands,
            preamp_db: preampDb,
            compressor,
            stereo_width: stereoWidth,
            reverb,
            normalize_lufs: normalizeLufs,
            reset: false,
          }
      const res = await onApplyServerEq(jobId, payload)
      if (reset) {
        selectPreset('flat')
        setStatusMsg('✅ Audio lagu dikembalikan ke master asli (Studio Flat).')
      } else {
        setStatusMsg(
          `✅ Master EQ (${preset === 'custom' ? 'Custom 8-Band' : preset}) berhasil dirender permanen ke file MP3 & WAV!`
        )
      }
      return res
    } catch (err) {
      setErrorMsg(err?.message || 'Gagal menerapkan Equalizer ke server.')
    } finally {
      setApplying(false)
    }
  }

  const activePresetInfo = STUDIO_EQ_PRESETS.find((p) => p.id === preset)
  const vuPct = Math.max(0, Math.min(100, ((vuLevel.rms + 60) / 60) * 100))

  return (
    <div
      className={`studio-eq-rack ${compact ? 'is-compact' : ''} ${enabled ? 'is-enabled' : 'is-bypassed'}`}
      style={{
        background: 'linear-gradient(180deg, rgba(15, 18, 28, 0.96) 0%, rgba(10, 12, 20, 0.98) 100%)',
        border: '1px solid rgba(124, 92, 255, 0.28)',
        borderRadius: 14,
        padding: compact ? '12px' : '14px 16px',
        marginTop: 12,
        boxShadow: '0 10px 30px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.06)',
        color: '#e2e8f0',
      }}
    >
      {/* Top Rack Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          flexWrap: 'wrap',
          marginBottom: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: enabled ? '#10b981' : '#64748b',
              boxShadow: enabled ? '0 0 10px #10b981' : 'none',
              display: 'inline-block',
            }}
          />
          <strong style={{ fontSize: 12, letterSpacing: '0.08em', color: '#f8fafc' }}>{title}</strong>
          <span
            style={{
              fontSize: 10,
              padding: '2px 7px',
              borderRadius: 999,
              background: 'rgba(16, 185, 129, 0.14)',
              border: '1px solid rgba(16, 185, 129, 0.35)',
              color: '#34d399',
              fontWeight: 600,
            }}
          >
            8-Band Parametric + Mastering
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            type="button"
            onClick={() => setEnabled((v) => !v)}
            style={{
              fontSize: 11,
              padding: '4px 10px',
              borderRadius: 999,
              border: enabled ? '1px solid #10b981' : '1px solid rgba(148,163,184,0.3)',
              background: enabled ? 'rgba(16, 185, 129, 0.18)' : 'rgba(30, 41, 59, 0.6)',
              color: enabled ? '#6ee7b7' : '#94a3b8',
              cursor: 'pointer',
              fontWeight: 600,
            }}
            title="Aktifkan atau Bypass Equalizer secara real-time (A/B compare)"
          >
            {enabled ? '● EQ ON' : '○ BYPASS'}
          </button>
          <button
            type="button"
            onClick={() => selectPreset('flat')}
            style={{
              fontSize: 11,
              padding: '4px 9px',
              borderRadius: 999,
              border: '1px solid rgba(148, 163, 184, 0.25)',
              background: 'rgba(30, 41, 59, 0.5)',
              color: '#cbd5e1',
              cursor: 'pointer',
            }}
            title="Reset semua band ke 0 dB"
          >
            Reset 0 dB
          </button>
        </div>
      </div>

      {/* Preset Pill Selector */}
      <div
        style={{
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          marginBottom: 10,
        }}
      >
        {STUDIO_EQ_PRESETS.map((p) => {
          const active = preset === p.id
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => selectPreset(p.id)}
              style={{
                fontSize: 11,
                padding: '5px 10px',
                borderRadius: 8,
                border: active ? '1px solid #8b5cf6' : '1px solid rgba(255,255,255,0.1)',
                background: active
                  ? 'linear-gradient(135deg, rgba(124, 92, 255, 0.32), rgba(16, 185, 129, 0.22))'
                  : 'rgba(255, 255, 255, 0.04)',
                color: active ? '#ffffff' : '#cbd5e1',
                cursor: 'pointer',
                fontWeight: active ? 600 : 400,
                transition: 'all 0.15s ease',
              }}
            >
              {p.label}
            </button>
          )
        })}
      </div>

      {activePresetInfo && (
        <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 8 }}>{activePresetInfo.desc}</div>
      )}

      {/* Real-time Spectrum + Frequency Response Curve Canvas */}
      <div
        style={{
          position: 'relative',
          borderRadius: 10,
          overflow: 'hidden',
          border: '1px solid rgba(255, 255, 255, 0.09)',
          marginBottom: 12,
          background: '#090b10',
        }}
      >
        <canvas
          ref={canvasRef}
          width={520}
          height={compact ? 84 : 108}
          style={{ width: '100%', height: compact ? 84 : 108, display: 'block' }}
        />
        <div
          style={{
            position: 'absolute',
            top: 6,
            left: 8,
            right: 8,
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 10,
            color: 'rgba(226, 232, 240, 0.65)',
            pointerEvents: 'none',
            fontFamily: 'monospace',
          }}
        >
          <span>+12 dB</span>
          <span>SPECTRUM &amp; EQ CURVE</span>
          <span>
            RMS {vuLevel.rms} dB | PEAK {vuLevel.peak} dB
          </span>
        </div>
        {/* VU Meter Bar at bottom of analyzer */}
        <div
          style={{
            height: 4,
            width: '100%',
            background: 'rgba(255,255,255,0.06)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              width: `${vuPct}%`,
              background: 'linear-gradient(90deg, #10b981 0%, #eab308 75%, #ef4444 100%)',
              transition: 'width 0.08s linear',
            }}
          />
        </div>
      </div>

      {/* 8-Band Vertical Faders */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(8, minmax(0, 1fr))',
          gap: compact ? 4 : 6,
          background: 'rgba(0, 0, 0, 0.28)',
          padding: compact ? '8px 6px' : '10px 8px',
          borderRadius: 10,
          border: '1px solid rgba(255, 255, 255, 0.06)',
          marginBottom: 12,
        }}
      >
        {STUDIO_EQ_FREQS.map((item, idx) => {
          const val = Number(bands[idx] || 0)
          return (
            <div
              key={item.freq}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontFamily: 'monospace',
                  color: val > 0 ? '#34d399' : val < 0 ? '#fbbf24' : '#94a3b8',
                  fontWeight: 600,
                }}
              >
                {val > 0 ? `+${val.toFixed(1)}` : val.toFixed(1)}
              </span>

              <div
                style={{
                  height: compact ? 88 : 108,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <input
                  type="range"
                  min="-12"
                  max="12"
                  step="0.5"
                  value={val}
                  disabled={!enabled}
                  onDoubleClick={() => updateBand(idx, 0)}
                  onChange={(e) => updateBand(idx, e.target.value)}
                  title={`${item.label} Hz (${item.sub}): ${val > 0 ? '+' : ''}${val} dB — klik 2x untuk reset 0 dB`}
                  style={{
                    writingMode: 'vertical-lr',
                    direction: 'rtl',
                    width: 22,
                    height: compact ? 82 : 100,
                    accentColor: val > 0 ? '#10b981' : val < 0 ? '#f59e0b' : '#8b5cf6',
                    cursor: enabled ? 'ns-resize' : 'not-allowed',
                  }}
                />
              </div>

              <strong style={{ fontSize: 10, color: '#e2e8f0' }}>{item.label}</strong>
              <span style={{ fontSize: 9, color: '#64748b' }}>{item.sub}</span>
            </div>
          )
        })}
      </div>

      {/* Studio Mastering Dynamics Row: Preamp, Compressor, Stereo Width, Room Echo */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: compact ? 'repeat(2, 1fr)' : 'repeat(4, 1fr)',
          gap: 8,
          marginBottom: jobId && onApplyServerEq ? 12 : 4,
        }}
      >
        <label
          style={{
            background: 'rgba(255,255,255,0.03)',
            padding: '7px 9px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.06)',
            fontSize: 11,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#94a3b8' }}>Preamp Gain</span>
            <strong style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>
              {preampDb > 0 ? `+${preampDb.toFixed(1)}` : preampDb.toFixed(1)} dB
            </strong>
          </div>
          <input
            type="range"
            min="-6"
            max="6"
            step="0.5"
            value={preampDb}
            disabled={!enabled}
            onChange={(e) => {
              const v = Number(e.target.value)
              setPreampDb(v)
              emitChange({
                preset,
                bands,
                preamp_db: v,
                compressor,
                stereo_width: stereoWidth,
                reverb,
                normalize_lufs: normalizeLufs,
              })
            }}
            style={{ width: '100%', accentColor: '#8b5cf6' }}
          />
        </label>

        <label
          style={{
            background: 'rgba(255,255,255,0.03)',
            padding: '7px 9px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.06)',
            fontSize: 11,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#94a3b8' }}>Bus Compressor</span>
            <strong style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>
              {Math.round(compressor * 100)}%
            </strong>
          </div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={compressor}
            disabled={!enabled}
            onChange={(e) => {
              const v = Number(e.target.value)
              setCompressor(v)
              emitChange({
                preset,
                bands,
                preamp_db: preampDb,
                compressor: v,
                stereo_width: stereoWidth,
                reverb,
                normalize_lufs: normalizeLufs,
              })
            }}
            style={{ width: '100%', accentColor: '#10b981' }}
          />
        </label>

        <label
          style={{
            background: 'rgba(255,255,255,0.03)',
            padding: '7px 9px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.06)',
            fontSize: 11,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#94a3b8' }}>Stereo Width</span>
            <strong style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>
              {Math.round(stereoWidth * 100)}%
            </strong>
          </div>
          <input
            type="range"
            min="0.6"
            max="1.6"
            step="0.04"
            value={stereoWidth}
            disabled={!enabled}
            onChange={(e) => {
              const v = Number(e.target.value)
              setStereoWidth(v)
              emitChange({
                preset,
                bands,
                preamp_db: preampDb,
                compressor,
                stereo_width: v,
                reverb,
                normalize_lufs: normalizeLufs,
              })
            }}
            style={{ width: '100%', accentColor: '#38bdf8' }}
          />
        </label>

        <label
          style={{
            background: 'rgba(255,255,255,0.03)',
            padding: '7px 9px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.06)',
            fontSize: 11,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#94a3b8' }}>Studio Dub Space</span>
            <strong style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>
              {Math.round(reverb * 100)}%
            </strong>
          </div>
          <input
            type="range"
            min="0"
            max="0.6"
            step="0.03"
            value={reverb}
            disabled={!enabled}
            onChange={(e) => {
              const v = Number(e.target.value)
              setReverb(v)
              emitChange({
                preset,
                bands,
                preamp_db: preampDb,
                compressor,
                stereo_width: stereoWidth,
                reverb: v,
                normalize_lufs: normalizeLufs,
              })
            }}
            style={{ width: '100%', accentColor: '#f59e0b' }}
          />
        </label>
      </div>

      {/* Server-Side Permanent Bake Controls when inspecting a completed song */}
      {jobId && typeof onApplyServerEq === 'function' && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 8,
            paddingTop: 8,
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
          }}
        >
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              color: '#cbd5e1',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={normalizeLufs}
              onChange={(e) => setNormalizeLufs(e.target.checked)}
            />
            <span>Kunci standar streaming (-14 LUFS True Peak)</span>
          </label>

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button
              type="button"
              disabled={applying}
              onClick={() => handleApplyToServer(true)}
              style={{
                fontSize: 11,
                padding: '6px 11px',
                borderRadius: 8,
                border: '1px solid rgba(148, 163, 184, 0.3)',
                background: 'rgba(30, 41, 59, 0.65)',
                color: '#cbd5e1',
                cursor: applying ? 'wait' : 'pointer',
              }}
              title="Kembalikan file lagu ke hasil master awal sebelum di-EQ"
            >
              ↺ Restore Master Asli
            </button>
            <button
              type="button"
              disabled={applying}
              onClick={() => handleApplyToServer(false)}
              style={{
                fontSize: 11,
                padding: '6px 13px',
                borderRadius: 8,
                border: '1px solid rgba(16, 185, 129, 0.5)',
                background: 'linear-gradient(135deg, #10b981, #7c5cff)',
                color: '#ffffff',
                fontWeight: 700,
                cursor: applying ? 'wait' : 'pointer',
              }}
              title="Render dan simpan permanen kurva Equalizer & Compressor ini ke file MP3 & WAV di server"
            >
              {applying ? '⏳ Merender Master EQ…' : '💾 Simpan & Render Master EQ ke Lagu'}
            </button>
          </div>
        </div>
      )}

      {statusMsg && (
        <div
          style={{
            marginTop: 8,
            padding: '6px 10px',
            borderRadius: 8,
            background: 'rgba(16, 185, 129, 0.14)',
            border: '1px solid rgba(16, 185, 129, 0.35)',
            color: '#6ee7b7',
            fontSize: 11,
          }}
        >
          {statusMsg}
        </div>
      )}
      {errorMsg && (
        <div
          style={{
            marginTop: 8,
            padding: '6px 10px',
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.14)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#fca5a5',
            fontSize: 11,
          }}
        >
          ⚠️ {errorMsg}
        </div>
      )}
    </div>
  )
}

