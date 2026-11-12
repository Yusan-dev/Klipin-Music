import { useEffect, useRef, useState } from 'react'
import { hummingToSongMusic } from '../api.js'
import Icon from './Icon.jsx'
import { Spinner } from './ui.jsx'

export default function HummingRecorder({
  user,
  onSignIn,
  genres = [],
  onCreated,
}) {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [audioBlob, setAudioBlob] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [title, setTitle] = useState('Senandung Melodi Baru')
  const [genre, setGenre] = useState('pop')
  const [prompt, setPrompt] = useState('warm modern studio pop arrangement')
  const [lyrics, setLyrics] = useState('')
  const [instrumental, setInstrumental] = useState(false)
  const [restyleStrength, setRestyleStrength] = useState(0.55)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const mediaRecorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (mediaRecorderRef.current?.state === 'recording') {
        try { mediaRecorderRef.current.stop() } catch { /* ignore */ }
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const startRec = async () => {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Browser tidak mendukung rekaman mikrofon. Silakan upload file audio senandung.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
        setAudioBlob(blob)
        setPreviewUrl((old) => {
          if (old) URL.revokeObjectURL(old)
          return URL.createObjectURL(blob)
        })
      }
      mr.start()
      mediaRecorderRef.current = mr
      setSeconds(0)
      setRecording(true)
      timerRef.current = setInterval(() => {
        setSeconds((s) => {
          if (s + 1 >= 35) {
            stopRec()
            return 35
          }
          return s + 1
        })
      }, 1000)
    } catch (e) {
      setError(`Izin mikrofon ditolak atau gagal: ${e?.message || e}`)
    }
  }

  const stopRec = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    setRecording(false)
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
  }

  const submitHumming = async (e) => {
    e.preventDefault()
    if (!user) { onSignIn?.(); return }
    if (!audioBlob) {
      setError('Rekam senandung atau pilih file melodi terlebih dahulu.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const fd = new FormData()
      const fileObj = audioBlob instanceof File
        ? audioBlob
        : new File([audioBlob], 'senandung.webm', { type: audioBlob.type || 'audio/webm' })
      fd.append('file', fileObj)
      fd.append('title', title.trim() || 'Senandung Melodi')
      fd.append('genre', genre)
      fd.append('prompt', prompt.trim() || 'full studio arrangement')
      fd.append('lyrics', lyrics.trim())
      fd.append('instrumental', instrumental ? 'true' : 'false')
      fd.append('restyle_strength', String(restyleStrength))
      fd.append('duration', '90')
      fd.append('take_count', '1')
      const res = await hummingToSongMusic(fd)
      onCreated?.(res)
    } catch (err) {
      setError(err?.message || 'Gagal memproses audio senandung.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submitHumming} className="space-y-3 rounded-xl border border-border bg-navy-1 p-3.5">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-white">
          🎤 Senandung / Humming to Full Song
        </h3>
        <p className="mt-0.5 text-[11px] text-white/60">
          Rekam senandung nada/vokal atau petikan gitar 10–30 detik — AI akan menyusunnya menjadi lagu utuh beraransemen penuh.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border-soft bg-navy-2 p-2.5">
        {!recording ? (
          <button
            type="button"
            onClick={startRec}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-danger px-3 py-1.5 text-xs font-bold text-white-solid hover:brightness-110 disabled:opacity-40"
          >
            <Icon name="mic" className="h-3.5 w-3.5" /> Rekam Mikrofon
          </button>
        ) : (
          <button
            type="button"
            onClick={stopRec}
            className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3 py-1.5 text-xs font-bold text-white-solid animate-pulse"
          >
            ⏹ Stop ({seconds}s / 35s)
          </button>
        )}

        <label className="cursor-pointer rounded-lg border border-border bg-navy-1 px-2.5 py-1.5 text-[11px] font-semibold text-white/80 hover:border-cyan/50">
          <span>📁 Upload Audio</span>
          <input
            type="file"
            accept="audio/*"
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setAudioBlob(f)
              setPreviewUrl((old) => {
                if (old) URL.revokeObjectURL(old)
                return URL.createObjectURL(f)
              })
            }}
          />
        </label>

        {previewUrl && (
          <audio controls src={previewUrl} className="mt-1 h-8 w-full" />
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-[11px] text-white/75">
          Judul Lagu
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            className="mt-1 w-full rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
          />
        </label>
        <label className="block text-[11px] text-white/75">
          Genre Target
          <select
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            className="mt-1 w-full rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
          >
            {(genres.length ? genres : [{ id: 'pop', label: 'Pop' }, { id: 'reggae', label: 'Reggae' }]).map((g) => (
              <option key={g.id} value={g.id}>{g.label}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="block text-[11px] text-white/75">
        Arahan Aransemen &amp; Gaya
        <input
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="mis. acoustic indie pop, warm bass, drum santai…"
          className="mt-1 w-full rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
        />
      </label>

      {!instrumental && (
        <label className="block text-[11px] text-white/75">
          Lirik Lagu (opsional)
          <textarea
            rows={2}
            value={lyrics}
            onChange={(e) => setLyrics(e.target.value)}
            placeholder="[Verse]\nTulis lirik atau biarkan otomatis mengikuti senandung…"
            className="mt-1 w-full rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
          />
        </label>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-white/80">
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={instrumental}
            onChange={(e) => setInstrumental(e.target.checked)}
            style={{ accentColor: '#22d3ee', colorScheme: 'light dark' }}
          />
          <span>Mode Instrumental Saja</span>
        </label>
        <label className="flex items-center gap-2">
          <span>Kebebasan Aransemen: {Math.round(restyleStrength * 100)}%</span>
          <input
            type="range"
            min={0.25}
            max={0.85}
            step={0.05}
            value={restyleStrength}
            onChange={(e) => setRestyleStrength(Number(e.target.value))}
            style={{ accentColor: '#22d3ee', colorScheme: 'light dark' }}
          />
        </label>
      </div>

      {error && (
        <p className="rounded-lg border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy || recording || !audioBlob}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-signal py-2.5 text-xs font-bold text-white-solid shadow transition hover:brightness-110 disabled:opacity-40"
      >
        {busy ? <Spinner className="h-4 w-4" /> : '✨'} Jadikan Lagu Studio dari Senandung
      </button>
    </form>
  )
}

