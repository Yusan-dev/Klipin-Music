import { useState } from 'react'
import {
  exportMusicVideo,
  getMusicStemsZip,
  mixMusicStems,
  replaceMusicSection,
  resolveDownloadAsset,
  shareMusic,
} from '../api.js'
import { Spinner } from './ui.jsx'

const STEMS = [
  { key: 'vocals', label: '🎤 Vokal', color: '#22d3ee' },
  { key: 'drums', label: '🥁 Drum', color: '#f59e0b' },
  { key: 'bass', label: '🎸 Bass', color: '#10b981' },
  { key: 'other', label: '🎹 Instrumen', color: '#a855f7' },
]

export default function WorkstationProTools({
  job,
  duration = 30,
  onJobUpdated,
  navigate,
}) {
  // Feature #3: Multi-track 4-stem mixer + pitch + tempo
  const [gains, setGains] = useState({ vocals: 1.0, drums: 1.0, bass: 1.0, other: 1.0 })
  const [mutes, setMutes] = useState({ vocals: false, drums: false, bass: false, other: false })
  const [solos, setSolos] = useState([])
  const [pitchSemitones, setPitchSemitones] = useState(0)
  const [tempo, setTempo] = useState(1.0)
  const [mixBusy, setMixBusy] = useState(false)
  const [zipBusy, setZipBusy] = useState(false)

  // Feature #2: Replace section
  const [repStart, setRepStart] = useState(0)
  const [repEnd, setRepEnd] = useState(() => Math.min(12, Math.max(4, Math.round((duration || 30) * 0.3))))
  const [repLyrics, setRepLyrics] = useState('')
  const [repPrompt, setRepPrompt] = useState('')
  const [repBusy, setRepBusy] = useState(false)

  // Feature #5 & #7: Lyric Video MP4 & Share Page
  const [vidRatio, setVidRatio] = useState('9:16')
  const [vidBusy, setVidBusy] = useState(false)
  const [vidResult, setVidResult] = useState(null)
  const [shareBusy, setShareBusy] = useState(false)

  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  if (!job?.job_id) return null

  const toggleSolo = (key) => {
    setSolos((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
  }

  const handleMixStems = async () => {
    setMixBusy(true)
    setError('')
    setNotice('')
    try {
      const updated = await mixMusicStems(job.job_id, {
        gains,
        mutes,
        solos,
        pitch_semitones: pitchSemitones,
        tempo,
        target_lufs: job?.metadata?.studio_eq?.target_lufs ?? -14.0,
      })
      onJobUpdated?.(updated)
      setNotice('✅ Mixdown 4-Stem + Pitch/Tempo berhasil dirender ke master lagu.')
    } catch (e) {
      setError(e?.message || 'Gagal merender mixdown stems.')
    } finally {
      setMixBusy(false)
    }
  }

  const handleDownloadZip = async () => {
    setZipBusy(true)
    setError('')
    try {
      const res = await getMusicStemsZip(job.job_id)
      const url = resolveDownloadAsset(res.zip_url, res.filename || 'stems.zip')
      const a = document.createElement('a')
      a.href = url
      a.download = res.filename || 'stems.zip'
      document.body.appendChild(a)
      a.click()
      a.remove()
      setNotice('📦 Paket 4-Stem ZIP siap diunduh.')
    } catch (e) {
      setError(e?.message || 'Gagal menyiapkan ZIP 4-stem.')
    } finally {
      setZipBusy(false)
    }
  }

  const handleReplaceSection = async (e) => {
    e.preventDefault()
    if (repEnd <= repStart + 0.8) {
      setError('Waktu akhir harus minimal 1 detik setelah waktu mulai.')
      return
    }
    setRepBusy(true)
    setError('')
    setNotice('')
    try {
      const updated = await replaceMusicSection(job.job_id, {
        start_sec: Number(repStart),
        end_sec: Number(repEnd),
        lyrics: repLyrics,
        prompt: repPrompt,
        crossfade_sec: 0.25,
      })
      onJobUpdated?.(updated)
      setNotice(`✂️ Bagian [${repStart}s – ${repEnd}s] berhasil diganti & disambung halus.`)
    } catch (err) {
      setError(err?.message || 'Gagal mengganti bagian lagu.')
    } finally {
      setRepBusy(false)
    }
  }

  const handleExportVideo = async () => {
    setVidBusy(true)
    setError('')
    setNotice('')
    try {
      const res = await exportMusicVideo(job.job_id, { ratio: vidRatio, show_waveform: true })
      setVidResult(res)
      setNotice(`🎬 Video Lirik MP4 (${res.ratio}) siap diunduh!`)
    } catch (e) {
      setError(e?.message || 'Gagal mengekspor video lirik MP4.')
    } finally {
      setVidBusy(false)
    }
  }

  const handleOpenShare = async () => {
    setShareBusy(true)
    setError('')
    try {
      if (!job.is_public) {
        const res = await shareMusic(job.job_id, true)
        onJobUpdated?.({ ...job, is_public: Boolean(res?.is_public ?? true) })
      }
      navigate?.(`#/song/${job.job_id}`)
    } catch (e) {
      setError(e?.message || 'Gagal membuka halaman share lagu.')
    } finally {
      setShareBusy(false)
    }
  }

  const maxDur = Math.max(10, Math.round(duration || job.duration || 120))

  return (
    <div className="mt-4 space-y-4">
      {(notice || error) && (
        <div
          className={`rounded-xl border px-3.5 py-2 text-xs ${
            error
              ? 'border-danger/40 bg-danger/10 text-danger'
              : 'border-signal/40 bg-signal/10 text-signal'
          }`}
        >
          {error || notice}
        </div>
      )}

      {/* Feature #3: 4-Stem Interactive Mixer + Pitch/Tempo + ZIP */}
      <section className="rounded-xl border border-border bg-navy-2 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider text-white">
              🎛️ Interactive 4-Stem Mixer, Pitch &amp; Tempo
            </h3>
            <p className="text-[11px] text-white/60">
              Atur level Vokal/Drum/Bass/Instrumen, transpose nada, lalu render ke master atau unduh bundle ZIP.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleDownloadZip}
              disabled={zipBusy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-navy-1 px-3 py-1.5 text-[11px] font-semibold text-white/85 hover:border-cyan/50 disabled:opacity-40"
            >
              {zipBusy ? <Spinner className="h-3 w-3" /> : '📦'} Unduh ZIP 4-Stem
            </button>
            <button
              type="button"
              onClick={handleMixStems}
              disabled={mixBusy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-1.5 text-[11px] font-bold text-white-solid hover:brightness-110 disabled:opacity-40"
            >
              {mixBusy ? <Spinner className="h-3 w-3" /> : '💾'} Render Mixdown ke Master
            </button>
          </div>
        </div>

        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {STEMS.map((st) => {
            const gain = gains[st.key] ?? 1.0
            const isMuted = Boolean(mutes[st.key])
            const isSolo = solos.includes(st.key)
            return (
              <div key={st.key} className="rounded-lg border border-border-soft bg-navy-1 p-2.5">
                <div className="mb-1.5 flex items-center justify-between gap-1">
                  <span className="text-xs font-semibold text-white">{st.label}</span>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => setMutes((m) => ({ ...m, [st.key]: !m[st.key] }))}
                      aria-pressed={isMuted}
                      className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                        isMuted ? 'bg-danger/30 text-danger ring-1 ring-danger/50' : 'bg-navy-2 text-white/60'
                      }`}
                    >
                      M
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleSolo(st.key)}
                      aria-pressed={isSolo}
                      className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                        isSolo ? 'bg-cyan/30 text-cyan-soft ring-1 ring-cyan/50' : 'bg-navy-2 text-white/60'
                      }`}
                    >
                      S
                    </button>
                  </div>
                </div>
                <input
                  type="range"
                  aria-label={`Volume ${st.label}`}
                  min={0}
                  max={1.8}
                  step={0.05}
                  value={gain}
                  onChange={(e) => setGains((g) => ({ ...g, [st.key]: Number(e.target.value) }))}
                  style={{ accentColor: st.color, colorScheme: 'light dark' }}
                  className="w-full"
                />
                <div className="text-right font-mono text-[10px] text-white/65">
                  {Math.round(gain * 100)}%
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="flex items-center justify-between gap-3 rounded-lg border border-border-soft bg-navy-1 px-3 py-2 text-xs text-white/80">
            <span>Pitch Transpose (Semitones)</span>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={-12}
                max={12}
                step={1}
                value={pitchSemitones}
                onChange={(e) => setPitchSemitones(Number(e.target.value))}
                style={{ accentColor: '#22d3ee', colorScheme: 'light dark' }}
              />
              <span className="w-9 text-right font-mono text-[11px] font-bold text-cyan">
                {pitchSemitones > 0 ? `+${pitchSemitones}` : pitchSemitones}
              </span>
            </div>
          </label>
          <label className="flex items-center justify-between gap-3 rounded-lg border border-border-soft bg-navy-1 px-3 py-2 text-xs text-white/80">
            <span>Kecepatan / Tempo</span>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0.75}
                max={1.35}
                step={0.05}
                value={tempo}
                onChange={(e) => setTempo(Number(e.target.value))}
                style={{ accentColor: '#a855f7', colorScheme: 'light dark' }}
              />
              <span className="w-10 text-right font-mono text-[11px] font-bold text-white">
                {tempo.toFixed(2)}x
              </span>
            </div>
          </label>
        </div>
      </section>

      {/* Feature #2 & Feature #5/#7 Row */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Replace Section (Inpainting) */}
        <form onSubmit={handleReplaceSection} className="space-y-2.5 rounded-xl border border-border bg-navy-2 p-4">
          <h3 className="text-xs font-bold uppercase tracking-wider text-white">
            ✂️ Replace Section (Inpainting / Ganti Bagian Lagu)
          </h3>
          <div className="grid grid-cols-2 gap-2.5">
            <label className="text-[11px] text-white/75">
              Mulai (detik)
              <input
                type="number"
                min={0}
                max={maxDur - 1}
                step={0.5}
                value={repStart}
                onChange={(e) => setRepStart(Number(e.target.value))}
                className="mt-1 w-full rounded-lg border border-border bg-navy-1 px-2.5 py-1.5 font-mono text-xs text-white outline-none focus:border-cyan"
              />
            </label>
            <label className="text-[11px] text-white/75">
              Akhir (detik)
              <input
                type="number"
                min={1}
                max={maxDur}
                step={0.5}
                value={repEnd}
                onChange={(e) => setRepEnd(Number(e.target.value))}
                className="mt-1 w-full rounded-lg border border-border bg-navy-1 px-2.5 py-1.5 font-mono text-xs text-white outline-none focus:border-cyan"
              />
            </label>
          </div>
          <label className="block text-[11px] text-white/75">
            Lirik Baru untuk Bagian Ini
            <textarea
              rows={2}
              value={repLyrics}
              onChange={(e) => setRepLyrics(e.target.value)}
              placeholder="Tulis lirik pengganti di rentang waktu di atas…"
              className="mt-1 w-full rounded-lg border border-border bg-navy-1 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
            />
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={repPrompt}
              onChange={(e) => setRepPrompt(e.target.value)}
              placeholder="Arahan gaya transisi (opsional)…"
              className="flex-1 rounded-lg border border-border bg-navy-1 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
            />
            <button
              type="submit"
              disabled={repBusy}
              className="rounded-lg bg-cyan/20 px-3.5 py-1.5 text-xs font-bold text-cyan-soft ring-1 ring-cyan/40 transition hover:bg-cyan/30 disabled:opacity-40"
            >
              {repBusy ? 'Merender…' : 'Ganti Bagian'}
            </button>
          </div>
        </form>

        {/* Export Lyric Video MP4 + Public Share */}
        <section className="flex flex-col justify-between space-y-3 rounded-xl border border-border bg-navy-2 p-4">
          <div className="space-y-2.5">
            <h3 className="text-xs font-bold uppercase tracking-wider text-white">
              🎬 Export Video Lirik MP4 &amp; Publikasi Link
            </h3>
            <div className="flex flex-wrap items-center gap-2">
              {[
                { id: '9:16', label: '📱 9:16 TikTok / Reels / Shorts' },
                { id: '16:9', label: '📺 16:9 YouTube Widescreen' },
              ].map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setVidRatio(opt.id)}
                  className={`rounded-lg border px-3 py-1.5 text-[11px] font-semibold transition ${
                    vidRatio === opt.id
                      ? 'border-cyan bg-cyan/15 text-cyan-soft'
                      : 'border-border bg-navy-1 text-white/75 hover:text-white'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleExportVideo}
                disabled={vidBusy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-2 text-xs font-bold text-white-solid hover:brightness-110 disabled:opacity-40"
              >
                {vidBusy ? <Spinner className="h-3.5 w-3.5" /> : '🎬'} Render Video Lirik ({vidRatio})
              </button>
              <button
                type="button"
                onClick={handleOpenShare}
                disabled={shareBusy}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-navy-1 px-3.5 py-2 text-xs font-semibold text-white/85 hover:border-cyan/50 disabled:opacity-40"
              >
                🔗 {job.is_public ? 'Buka Halaman Share' : 'Publikasikan & Share Link'}
              </button>
            </div>
            {vidResult?.path && (
              <a
                href={resolveDownloadAsset(vidResult.path, vidResult.filename || 'lyric_video.mp4')}
                download={vidResult.filename || 'lyric_video.mp4'}
                className="inline-flex items-center gap-2 rounded-lg border border-cyan/40 bg-cyan/10 px-3 py-1.5 text-xs font-semibold text-cyan-soft hover:bg-cyan/20"
              >
                ⬇ Unduh {vidResult.filename} ({vidResult.width}×{vidResult.height})
              </a>
            )}
          </div>
          <p className="font-mono text-[10px] text-white/50">
            Link publik: #/song/{job.job_id}
          </p>
        </section>
      </div>
    </div>
  )
}

