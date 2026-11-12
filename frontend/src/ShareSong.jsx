import { useEffect, useMemo, useRef, useState } from 'react'
import { getPublicMusicShare, resolveMediaAsset } from './api.js'
import Icon from './components/Icon.jsx'
import { Spinner } from './components/ui.jsx'

function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec || 0))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function ShareSong({ jobId, navigate }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const [curTime, setCurTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [copied, setCopied] = useState(false)
  const audioRef = useRef(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    setPlaying(false)
    setCurTime(0)
    if (!jobId) {
      setError('ID lagu tidak ditemukan.')
      setLoading(false)
      return undefined
    }
    getPublicMusicShare(jobId)
      .then((res) => { if (alive) setData(res) })
      .catch((e) => { if (alive) setError(e?.message || 'Lagu tidak ditemukan atau bersifat privat.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [jobId])

  const song = data?.song || null
  const audioUrl = song?.audio_path ? resolveMediaAsset(song.audio_path) : ''
  const coverUrl = song?.cover_path ? resolveMediaAsset(song.cover_path) : ''

  const lyricsLines = useMemo(() => {
    const tl = song?.metadata?.lyrics_timeline
    if (Array.isArray(tl) && tl.length > 0) {
      return tl.map((x, idx) => ({
        id: idx,
        start: Number(x.start || 0),
        end: Number(x.end || 0),
        text: String(x.text || ''),
      }))
    }
    return String(song?.lyrics || '')
      .split(/\r?\n/)
      .filter((ln) => ln.trim())
      .map((text, id) => ({ id, start: -1, end: -1, text }))
  }, [song])

  const togglePlay = () => {
    const el = audioRef.current
    if (!el || !audioUrl) return
    if (el.paused) {
      el.play().then(() => setPlaying(true)).catch(() => {})
    } else {
      el.pause()
      setPlaying(false)
    }
  }

  const seek = (sec) => {
    const el = audioRef.current
    if (!el) return
    el.currentTime = Math.max(0, Number(sec) || 0)
    setCurTime(el.currentTime)
  }

  const copyShareUrl = () => {
    const url = window.location.href
    navigator.clipboard?.writeText(url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }

  if (loading) {
    return (
      <div className="mx-auto flex min-h-[60vh] max-w-4xl items-center justify-center gap-3 text-white/70">
        <Spinner className="h-5 w-5" />
        <span>Membuka halaman lagu…</span>
      </div>
    )
  }

  if (error || !song) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 text-center">
        <div className="rounded-2xl border border-border bg-navy-2 p-8">
          <Icon name="music" className="mx-auto h-10 w-10 text-cyan" />
          <h1 className="mt-4 text-lg font-bold text-white">Lagu Tidak Tersedia</h1>
          <p className="mt-2 text-sm text-white/65">{error || 'Halaman lagu ini privat atau sudah dihapus.'}</p>
          <button
            type="button"
            onClick={() => navigate('#/music')}
            className="mt-6 rounded-xl bg-signal px-5 py-2.5 text-xs font-bold text-white-solid shadow-lg transition hover:brightness-110"
          >
            Kembali ke Klipin Music
          </button>
        </div>
      </div>
    )
  }

  const effDur = duration || Number(song.duration || 0) || 1
  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => navigate('#/music')}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-navy-2 px-3 py-1.5 text-xs font-semibold text-white/80 transition hover:text-white"
        >
          ← Studio Musik
        </button>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={copyShareUrl}
            className="inline-flex items-center gap-1.5 rounded-lg border border-cyan/40 bg-cyan/15 px-3 py-1.5 text-xs font-semibold text-cyan-soft transition hover:bg-cyan/25"
          >
            <Icon name="share" className="h-3.5 w-3.5" />
            {copied ? 'Link Disalin!' : 'Salin Link Publik'}
          </button>
          <button
            type="button"
            onClick={() => navigate(`#/studio/${song.job_id}`)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-1.5 text-xs font-bold text-white-solid transition hover:brightness-110"
          >
            🎛️ Buka di Workstation
          </button>
        </div>
      </div>

      <section className="grid gap-6 lg:grid-cols-[340px_1fr]">
        <div className="rounded-2xl border border-border bg-navy-2 p-5 shadow-xl">
          <div className="aspect-square w-full overflow-hidden rounded-xl border border-border-soft bg-navy-1">
            {coverUrl ? (
              <img src={coverUrl} alt={song.title || 'Artwork'} className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-white/25">
                <Icon name="music" className="h-16 w-16" />
              </div>
            )}
          </div>

          <h1 className="mt-4 text-xl font-bold tracking-tight text-white">{song.title || song.job_id}</h1>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <span className="rounded-full border border-cyan/40 bg-cyan/10 px-2.5 py-0.5 font-mono text-[10px] font-bold text-cyan-soft">
              {song.metadata?.genre_label || song.genre || 'Music'}
            </span>
            {song.language && (
              <span className="rounded-full border border-border bg-navy-1 px-2.5 py-0.5 font-mono text-[10px] uppercase text-white/75">
                {song.language}
              </span>
            )}
            <span className="rounded-full border border-border bg-navy-1 px-2.5 py-0.5 font-mono text-[10px] text-white/75">
              {fmtTime(effDur)}
            </span>
          </div>

          <audio
            ref={audioRef}
            src={audioUrl}
            onTimeUpdate={(e) => setCurTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
            onEnded={() => setPlaying(false)}
          />

          <div className="mt-4 space-y-2 rounded-xl border border-border bg-navy-1 p-3">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={togglePlay}
                disabled={!audioUrl}
                aria-label={playing ? 'Jeda lagu' : 'Putar lagu'}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-signal text-white-solid shadow transition hover:brightness-110 disabled:opacity-40"
              >
                <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
              </button>
              <div className="min-w-0 flex-1">
                <input
                  type="range"
                  aria-label="Posisi pemutaran"
                  min={0}
                  max={effDur}
                  step={0.1}
                  value={Math.min(curTime, effDur)}
                  onChange={(e) => seek(Number(e.target.value))}
                  style={{ accentColor: '#22d3ee', colorScheme: 'light dark' }}
                  className="w-full"
                />
                <div className="flex justify-between font-mono text-[10px] text-white/60">
                  <span>{fmtTime(curTime)}</span>
                  <span>{fmtTime(effDur)}</span>
                </div>
              </div>
            </div>
            {audioUrl && (
              <a
                href={audioUrl}
                download={`${song.title || song.job_id}.mp3`}
                className="block w-full rounded-lg border border-border bg-navy-2 py-2 text-center text-xs font-semibold text-white/85 transition hover:border-cyan/50 hover:text-white"
              >
                ⬇ Unduh Audio Master MP3
              </a>
            )}
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-2xl border border-border bg-navy-2 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-bold uppercase tracking-wider text-white/90">Lirik &amp; Sinkronisasi</h2>
              <span className="font-mono text-[11px] text-white/55">{lyricsLines.length} baris</span>
            </div>
            <div className="max-h-[380px] space-y-1.5 overflow-y-auto pr-1">
              {lyricsLines.length === 0 ? (
                <p className="text-xs text-white/50">Lagu instrumental atau belum ada lirik.</p>
              ) : (
                lyricsLines.map((line) => {
                  const active = line.start >= 0 && curTime >= line.start && curTime <= (line.end || line.start + 3)
                  return (
                    <button
                      key={line.id}
                      type="button"
                      onClick={() => line.start >= 0 && seek(line.start)}
                      className={`block w-full rounded-lg px-3 py-1.5 text-left text-sm transition ${
                        active
                          ? 'bg-cyan/20 font-semibold text-cyan-soft ring-1 ring-cyan/50'
                          : 'text-white/80 hover:bg-navy-1'
                      }`}
                    >
                      {line.text}
                    </button>
                  )
                })
              )}
            </div>
          </div>

          {(data?.parent || (data?.remixes || []).length > 0) && (
            <div className="rounded-2xl border border-border bg-navy-2 p-5">
              <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-white/90">🌳 Pohon Remix &amp; Turunan</h2>
              {data?.parent && (
                <div className="mb-3">
                  <div className="mb-1 text-[11px] font-semibold text-white/60">Lagu Asal / Sumber:</div>
                  <button
                    type="button"
                    onClick={() => navigate(`#/song/${data.parent.job_id}`)}
                    className="flex w-full items-center justify-between rounded-xl border border-border bg-navy-1 px-3 py-2 text-left text-xs font-semibold text-white hover:border-cyan/50"
                  >
                    <span>{data.parent.title || data.parent.job_id}</span>
                    <span className="font-mono text-[10px] text-cyan">{data.parent.genre}</span>
                  </button>
                </div>
              )}
              {(data?.remixes || []).length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-[11px] font-semibold text-white/60">Remix / Versi Turunan ({data.remixes.length}):</div>
                  {data.remixes.map((rx) => (
                    <button
                      key={rx.job_id}
                      type="button"
                      onClick={() => navigate(`#/song/${rx.job_id}`)}
                      className="flex w-full items-center justify-between rounded-xl border border-border bg-navy-1 px-3 py-2 text-left text-xs font-semibold text-white/90 hover:border-cyan/50"
                    >
                      <span>{rx.title || rx.job_id}</span>
                      <span className="font-mono text-[10px] text-white/60">
                        {rx.genre} · {fmtTime(rx.duration)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

