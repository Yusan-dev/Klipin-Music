import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePlayer } from './playerStore.jsx'
import {
  equalizeMusicJob,
  getMusicStatus,
  listMusicJobs,
  resolveDownloadAsset,
  resolveMediaAsset,
  splitMusicStems,
} from './api.js'
import Icon from './components/Icon.jsx'
import MusicWaveform from './components/MusicWaveform.jsx'
import StudioEqualizer from './components/StudioEqualizer.jsx'
import { Spinner } from './components/ui.jsx'

function formatTime(value) {
  const s = Math.max(0, Number(value) || 0)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

function parseLrcLines(raw = '') {
  if (!raw) return []
  const lines = []
  for (const row of String(raw).split(/\r?\n/)) {
    const m = row.match(/^\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)$/)
    if (m) {
      const min = Number(m[1]) || 0
      const sec = Number(m[2]) || 0
      const ms = m[3] ? Number(m[3].padEnd(3, '0')) / 1000 : 0
      const text = m[4].trim()
      if (text) lines.push({ start: min * 60 + sec + ms, text })
    } else if (row.trim() && !row.trim().startsWith('[')) {
      lines.push({ start: lines.length * 4, text: row.trim() })
    }
  }
  return lines
}

export default function StudioWorkstation({
  user,
  authChecked,
  onSignIn,
  initialJobId = null,
  navigate,
}) {
  const [jobs, setJobs] = useState([])
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedJob, setSelectedJob] = useState(null)
  const [activeStemKey, setActiveStemKey] = useState('master') // master | vocals | backing | bass | drums | other
  const [stemsBusy, setStemsBusy] = useState(false)
  const [stemsFeedback, setStemsFeedback] = useState('')
  const [localFile, setLocalFile] = useState(null) // { name, url, size }
  const [mediaBust, setMediaBust] = useState(() => Date.now())

  const [playing, setPlaying] = useState(false)
  const [looping, setLooping] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [audioError, setAudioError] = useState('')
  const { pause: pauseGlobalPlayer } = usePlayer()

  const audioRef = useRef(null)
  const fileInputRef = useRef(null)

  const loadLibrary = useCallback(async () => {
    if (!user) return
    setLoadingJobs(true)
    try {
      const res = await listMusicJobs()
      const completed = (res?.jobs || []).filter((j) => j.status === 'completed')
      setJobs(completed)
      if (!selectedJob && !localFile) {
        const match =
          (initialJobId && completed.find((j) => j.job_id === initialJobId)) ||
          completed[0] ||
          null
        if (match) setSelectedJob(match)
      } else if (initialJobId && selectedJob?.job_id !== initialJobId) {
        const match = completed.find((j) => j.job_id === initialJobId)
        if (match) {
          setLocalFile(null)
          setSelectedJob(match)
        }
      }
    } catch (err) {
      setAudioError(err?.message || 'Gagal memuat daftar lagu studio.')
    } finally {
      setLoadingJobs(false)
    }
  }, [user, initialJobId, selectedJob, localFile])

  useEffect(() => {
    loadLibrary()
  }, [loadLibrary])

  // Refresh selected job details when initialJobId changes
  useEffect(() => {
    if (!initialJobId || !user) return
    getMusicStatus(initialJobId)
      .then((fresh) => {
        if (fresh && fresh.job_id) {
          setLocalFile(null)
          setSelectedJob(fresh)
          setActiveStemKey('master')
          setAudioError('')
        }
      })
      .catch(() => {})
  }, [initialJobId, user])

  // Clean up object URL for local audio file
  useEffect(() => {
    return () => {
      if (localFile?.url) URL.revokeObjectURL(localFile.url)
    }
  }, [localFile])

  const filteredJobs = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return jobs
    return jobs.filter(
      (j) =>
        String(j.title || '').toLowerCase().includes(q) ||
        String(j.genre || '').toLowerCase().includes(q) ||
        String(j.job_id || '').toLowerCase().includes(q),
    )
  }, [jobs, searchQuery])

  // Available stems for the selected job
  const stemTracks = useMemo(() => {
    if (localFile) {
      return [{ key: 'master', label: '🎵 File Audio Lokal', path: localFile.url, isLocal: true }]
    }
    if (!selectedJob) return []
    const jid = selectedJob.job_id
    const baseMaster = selectedJob.audio_path || `/videos/${jid}/song.mp3`
    const metaStems = selectedJob.metadata?.stems || {}
    return [
      { key: 'master', label: '🎵 Full Master Mix', path: baseMaster },
      {
        key: 'vocals',
        label: '🎤 Vokal Utama',
        path: metaStems.vocals || `/videos/${jid}/cover_vocals.mp3`,
      },
      {
        key: 'backing',
        label: '🎸 Aransemen / Backing',
        path: metaStems.instrumental || metaStems.backing || `/videos/${jid}/cover_backing_restyled.mp3`,
      },
      {
        key: 'bass',
        label: '🔊 Sub / Dub Bass',
        path: metaStems.bass || `/videos/${jid}/cover_bass_source.mp3`,
      },
      {
        key: 'drums',
        label: '🥁 Drums / One-Drop',
        path: metaStems.drums || `/videos/${jid}/cover_drums_source.mp3`,
      },
      {
        key: 'other',
        label: '🎹 Harmoni / Other',
        path: metaStems.other || `/videos/${jid}/cover_other_source.mp3`,
      },
    ]
  }, [selectedJob, localFile])

  const activeStem = useMemo(
    () => stemTracks.find((t) => t.key === activeStemKey) || stemTracks[0] || null,
    [stemTracks, activeStemKey],
  )

  const activeAudioUrl = useMemo(() => {
    if (!activeStem?.path) return ''
    if (activeStem.isLocal) return activeStem.path
    const resolved = resolveMediaAsset(activeStem.path)
    const sep = resolved.includes('?') ? '&' : '?'
    return `${resolved}${sep}v=${mediaBust}`
  }, [activeStem, mediaBust])

  const coverSrc = useMemo(() => {
    if (localFile) return ''
    const raw = selectedJob?.cover_path || selectedJob?.metadata?.cover_path || ''
    if (!raw) return ''
    return resolveMediaAsset(raw)
  }, [selectedJob, localFile])

  const lyricsLines = useMemo(() => {
    if (localFile || !selectedJob) return []
    const aligned = selectedJob.metadata?.aligned_segments || selectedJob.metadata?.segments
    if (Array.isArray(aligned) && aligned.length > 0) {
      return aligned
        .map((seg) => ({
          start: Number(seg.start) || 0,
          text: String(seg.text || '').trim(),
        }))
        .filter((s) => s.text)
    }
    return parseLrcLines(selectedJob.lyrics || '')
  }, [selectedJob, localFile])

  const activeLyricIdx = useMemo(() => {
    if (!lyricsLines.length) return -1
    let idx = -1
    for (let i = 0; i < lyricsLines.length; i += 1) {
      if (currentTime >= lyricsLines[i].start) idx = i
      else break
    }
    return idx
  }, [lyricsLines, currentTime])

  const selectJob = (jobItem) => {
    if (audioRef.current && !audioRef.current.paused) {
      audioRef.current.pause()
    }
    setPlaying(false)
    setAudioError('')
    setLocalFile(null)
    setActiveStemKey('master')
    setSelectedJob(jobItem)
    setCurrentTime(0)
    setDuration(jobItem?.duration || jobItem?.metadata?.duration || 0)
    setMediaBust(Date.now())
  }

  const handleUploadLocalAudio = (ev) => {
    const file = ev.target.files?.[0]
    ev.target.value = ''
    if (!file) return
    if (audioRef.current && !audioRef.current.paused) {
      audioRef.current.pause()
    }
    if (localFile?.url) URL.revokeObjectURL(localFile.url)
    const url = URL.createObjectURL(file)
    setPlaying(false)
    setAudioError('')
    setActiveStemKey('master')
    setLocalFile({
      name: file.name.replace(/\.[^.]+$/, ''),
      fileName: file.name,
      sizeMb: (file.size / (1024 * 1024)).toFixed(2),
      url,
    })
    setCurrentTime(0)
    setDuration(0)
  }

  const togglePlay = () => {
    const el = audioRef.current
    if (!el || !activeAudioUrl) return
    setAudioError('')
    if (!el.paused) {
      el.pause()
    } else {
      pauseGlobalPlayer()
      const p = el.play()
      if (p && typeof p.catch === 'function') {
        p.catch((err) => {
          setPlaying(false)
          if (err?.name !== 'AbortError') {
            setAudioError(`Gagal memutar track (${activeStem?.label || 'audio'}): ${err?.message || err}`)
          }
        })
      }
    }
  }

  const seekTo = (sec) => {
    const el = audioRef.current
    const target = Math.max(0, Number(sec) || 0)
    setCurrentTime(target)
    if (el) {
      try {
        el.currentTime = target
      } catch {
        /* ignore seek before metadata */
      }
    }
  }

  const handleAudioError = () => {
    setPlaying(false)
    if (activeStemKey !== 'master') {
      setAudioError(
        `Stem "${activeStem?.label}" belum tersedia untuk lagu ini. Klik "Pisahkan Stems" atau gunakan Full Master Mix.`,
      )
      setActiveStemKey('master')
      return
    }
    if (selectedJob?.job_id && !localFile) {
      getMusicStatus(selectedJob.job_id)
        .then((fresh) => {
          if (fresh?.job_id === selectedJob.job_id) {
            setSelectedJob(fresh)
            setMediaBust(Date.now())
          }
        })
        .catch(() => {
          setAudioError('File audio tidak ditemukan di server. Silakan pilih lagu lain atau unggah file MP3/WAV lokal.')
        })
    }
  }

  const handleServerEq = async (eqPayload) => {
    if (!selectedJob?.job_id || localFile) {
      throw new Error('Simpan permanen server hanya untuk lagu di Library.')
    }
    const updated = await equalizeMusicJob(selectedJob.job_id, eqPayload)
    if (updated && updated.job_id) {
      setSelectedJob(updated)
      setJobs((prev) => prev.map((item) => (item.job_id === updated.job_id ? updated : item)))
      setMediaBust(Date.now())
    }
    return updated
  }

  const handleExtractStems = async () => {
    if (!selectedJob?.job_id || stemsBusy) return
    setStemsBusy(true)
    setStemsFeedback('')
    setAudioError('')
    try {
      const res = await splitMusicStems(selectedJob.job_id)
      const fresh = await getMusicStatus(selectedJob.job_id)
      if (fresh?.job_id) setSelectedJob(fresh)
      setMediaBust(Date.now())
      setStemsFeedback(
        res?.provider
          ? `✓ Stems siap (${res.provider}) — silakan pilih stem di bawah.`
          : '✓ Stems berhasil dipisahkan.',
      )
    } catch (err) {
      setAudioError(`Pemisahan stem gagal: ${err?.message || err}`)
    } finally {
      setStemsBusy(false)
    }
  }

  const activeTitle = localFile
    ? localFile.name
    : selectedJob?.title || 'Pilih Lagu atau Unggah Audio'
  const activeGenre = localFile
    ? 'Local Audio Master'
    : selectedJob?.metadata?.genre_label || selectedJob?.genre || 'reggae'
  const effectiveDuration = duration || selectedJob?.duration || selectedJob?.metadata?.duration || 0

  return (
    <div className="min-h-[calc(100vh-54px)] bg-navy text-white">
      {/* Studio Workstation toolbar — non-sticky so it never stacks under Topbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft bg-navy-1/75 px-3.5 py-2 sm:px-5">
        <nav className="flex items-center gap-1 overflow-x-auto" aria-label="Bagian studio musik">
          {[
            { id: 'create', label: 'Create', path: '#/music' },
            { id: 'cover', label: 'Cover', path: '#/cover' },
            { id: 'studio', label: '🎚️ Studio EQ', path: '#/studio', active: true },
            { id: 'history', label: `Library${jobs.length ? ` · ${jobs.length}` : ''}`, path: '#/history' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => navigate?.(tab.path)}
              aria-pressed={Boolean(tab.active)}
              className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition ${
                tab.active
                  ? 'border border-[#10b981]/40 bg-[#10b981]/15 text-[#6ee7b7]'
                  : 'text-[#8e8e99] hover:text-[#f4f4f6]'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a"
            onChange={handleUploadLocalAudio}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#26262e] bg-[#18181d] px-3 py-1.5 text-[11px] font-semibold text-[#d4d4d8] transition hover:border-[#10b981]/50 hover:text-white"
          >
            📂 Buka Audio Lokal (MP3/WAV)
          </button>
          {activeAudioUrl && (
            <a
              href={localFile ? activeAudioUrl : resolveDownloadAsset(activeStem?.path || activeAudioUrl, `${activeTitle || 'klipin-master'}.mp3`)}
              download={`${activeTitle || 'klipin-master'}.mp3`}
              className="hidden items-center gap-1 rounded-lg border border-[#10b981]/40 bg-[#10b981]/15 px-3 py-1.5 text-[11px] font-semibold text-[#6ee7b7] transition hover:bg-[#10b981]/25 sm:inline-flex"
            >
              ⬇ Unduh Track
            </a>
          )}
        </div>
      </div>

      {authChecked && !user && (
        <div className="border-b border-[#26262e] bg-[#141418] px-5 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
            <span>
              Masuk ke akun Anda untuk memuat riwayat lagu dari Library dan menyimpan hasil mastering EQ ke server.
              (Anda tetap bisa langsung mencoba Equalizer dengan tombol <b>📂 Buka Audio Lokal</b> di kanan atas).
            </span>
            <button
              type="button"
              onClick={onSignIn}
              className="rounded-lg bg-amber-400 px-3 py-1.5 font-bold text-black transition hover:bg-amber-300"
            >
              Masuk Sekarang
            </button>
          </div>
        </div>
      )}

      {/* Main 3-Pane Suno Studio Workstation Layout */}
      <div className="flex flex-col xl:flex-row">
        {/* Left Pane: Song Library & Local File Selector */}
        <aside className="w-full shrink-0 border-b border-border-soft bg-navy-1/50 p-4 xl:w-[320px] xl:border-b-0 xl:border-r">
          <div className="space-y-3 xl:sticky xl:top-[62px] xl:max-h-[calc(100vh-136px)] xl:overflow-y-auto">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xs font-bold uppercase tracking-wider text-[#e4e4e7]">
                  Track Library
                </h2>
                <p className="text-[11px] text-[#71717a]">
                  Pilih lagu hasil render atau file lokal untuk di-mastering.
                </p>
              </div>
              <button
                type="button"
                onClick={loadLibrary}
                disabled={loadingJobs || !user}
                className="rounded-md border border-[#26262e] bg-[#18181d] px-2 py-1 text-[10px] font-medium text-[#a1a1aa] hover:text-white disabled:opacity-40"
              >
                {loadingJobs ? '…' : 'Segarkan'}
              </button>
            </div>

            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Cari judul lagu / genre…"
              className="w-full rounded-lg border border-[#26262e] bg-[#18181d] px-3 py-2 text-xs text-[#f4f4f6] outline-none placeholder:text-[#5c5c66] focus:border-[#10b981]/60"
            />

            {localFile && (
              <div className="rounded-xl border border-[#10b981]/60 bg-[#10b981]/10 p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="rounded bg-[#10b981]/20 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-[#6ee7b7]">
                    FILE LOKAL AKTIF
                  </span>
                  <span className="font-mono text-[10px] text-[#a1a1aa]">{localFile.sizeMb} MB</span>
                </div>
                <div className="mt-1 truncate text-xs font-semibold text-white">{localFile.fileName}</div>
              </div>
            )}

            <div className="max-h-[540px] space-y-1.5 overflow-y-auto pr-1">
              {filteredJobs.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#26262e] p-5 text-center text-xs text-[#71717a]">
                  {user
                    ? 'Belum ada lagu selesai di Library. Unggah file MP3/WAV lokal atau buat lagu di tab Create/Cover.'
                    : 'Klik "Masuk" atau gunakan tombol "📂 Buka Audio Lokal" untuk mulai menggunakan Studio Equalizer.'}
                </div>
              ) : (
                filteredJobs.map((item) => {
                  const isSelected = !localFile && selectedJob?.job_id === item.job_id
                  const thumb = item.cover_path || item.metadata?.cover_path || ''
                  const hasEq = Boolean(item.metadata?.studio_eq && !item.metadata?.studio_eq?.bypass)
                  return (
                    <button
                      key={item.job_id}
                      type="button"
                      onClick={() => selectJob(item)}
                      className={`library-row-deferred flex w-full items-center gap-2.5 rounded-xl border p-2 text-left transition ${
                        isSelected
                          ? 'border-[#10b981]/60 bg-navy-2 ring-1 ring-[#10b981]/25'
                          : 'border-transparent bg-navy-2/70 hover:border-border hover:bg-navy-2'
                      }`}
                    >
                      <div className="relative h-11 w-11 shrink-0 overflow-hidden rounded-lg bg-[#26262e]">
                        {thumb ? (
                          <img
                            src={resolveMediaAsset(thumb)}
                            alt=""
                            loading="lazy"
                            onError={(e) => {
                              e.currentTarget.style.display = 'none'
                            }}
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <span className="flex h-full w-full items-center justify-center text-[#71717a]">
                            <Icon name="music" className="h-4 w-4" />
                          </span>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1.5">
                          <span className="truncate text-xs font-semibold text-[#f4f4f6]">
                            {item.title || item.job_id}
                          </span>
                          {hasEq && (
                            <span className="shrink-0 rounded bg-[#10b981]/20 px-1 py-0.5 font-mono text-[8px] font-bold text-[#34d399]">
                              EQ
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[10px] text-[#71717a]">
                          {item.metadata?.genre_label || item.genre || 'music'} ·{' '}
                          {formatTime(item.duration || item.metadata?.duration || 0)}
                        </div>
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          </div>
        </aside>

        {/* Center Pane: Suno Studio DAW Transport, Stem Switcher & Full-Size Equalizer Rack */}
        <section className="min-w-0 flex-1 space-y-5 p-4 sm:p-6">
          {/* Hidden Studio Audio Element */}
          <audio
            ref={audioRef}
            src={activeAudioUrl || undefined}
            crossOrigin="anonymous"
            loop={looping}
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onTimeUpdate={() => {
              if (audioRef.current) setCurrentTime(audioRef.current.currentTime || 0)
            }}
            onLoadedMetadata={() => {
              if (audioRef.current) {
                setDuration(audioRef.current.duration || 0)
              }
            }}
            onError={handleAudioError}
            className="hidden"
          />

          {audioError && (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
              <span>⚠️ {audioError}</span>
              <button
                type="button"
                onClick={() => {
                  setAudioError('')
                  setMediaBust(Date.now())
                }}
                className="shrink-0 rounded-md border border-rose-400/40 px-2.5 py-1 font-mono text-[10px] font-bold text-rose-200 hover:bg-rose-500/20"
              >
                Muat Ulang Track
              </button>
            </div>
          )}

          {/* Track Transport & Multitrack Timeline Card */}
          <div className="rounded-2xl border border-[#26262e] bg-[#141419] p-4 shadow-xl sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#26262e] pb-4">
              <div className="flex min-w-0 items-center gap-3.5">
                <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-white/10 bg-[#26262e]">
                  {coverSrc ? (
                    <img
                      src={coverSrc}
                      alt=""
                      onError={(e) => {
                        e.currentTarget.style.display = 'none'
                      }}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center text-[#71717a]">
                      <Icon name="music" className="h-6 w-6" />
                    </div>
                  )}
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-md border border-[#10b981]/40 bg-[#10b981]/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-[#6ee7b7]">
                      SUNO STUDIO WORKSTATION
                    </span>
                    <span className="rounded-md bg-[#26262e] px-2 py-0.5 font-mono text-[10px] uppercase text-[#a1a1aa]">
                      {activeGenre}
                    </span>
                    {selectedJob?.metadata?.beat_bpm && (
                      <span className="rounded-md bg-[#26262e] px-2 py-0.5 font-mono text-[10px] text-[#f59e0b]">
                        {Math.round(selectedJob.metadata.beat_bpm)} BPM
                      </span>
                    )}
                  </div>
                  <h1 className="mt-1.5 truncate text-lg font-bold tracking-tight text-white">
                    {activeTitle}
                  </h1>
                  <p className="mt-0.5 font-mono text-[11px] text-[#71717a]">
                    Track Aktif: <span className="text-[#d4d4d8]">{activeStem?.label || 'Tidak ada'}</span>
                  </p>
                </div>
              </div>

              {/* Transport Controls */}
              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  onClick={togglePlay}
                  disabled={!activeAudioUrl}
                  className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#10b981] to-[#059669] px-5 py-2.5 text-xs font-bold text-[#06130e] shadow-lg transition hover:brightness-110 disabled:opacity-40"
                >
                  <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
                  {playing ? 'Pause Studio' : 'Play Studio'}
                </button>
                <button
                  type="button"
                  onClick={() => seekTo(0)}
                  disabled={!activeAudioUrl}
                  title="Kembali ke awal (00:00)"
                  className="rounded-xl border border-[#26262e] bg-[#1c1c22] px-3 py-2.5 font-mono text-xs text-[#a1a1aa] transition hover:text-white disabled:opacity-40"
                >
                  ⏮ 00:00
                </button>
                <button
                  type="button"
                  onClick={() => setLooping((v) => !v)}
                  className={`rounded-xl border px-3 py-2.5 font-mono text-xs font-semibold transition ${
                    looping
                      ? 'border-[#f59e0b]/60 bg-[#f59e0b]/15 text-[#fbbf24]'
                      : 'border-[#26262e] bg-[#1c1c22] text-[#a1a1aa] hover:text-white'
                  }`}
                >
                  🔁 Loop {looping ? 'ON' : 'OFF'}
                </button>
              </div>
            </div>

            {/* Waveform & Scrub Timeline */}
            <div className="mt-4 space-y-2.5">
              <MusicWaveform
                src={activeAudioUrl}
                audioRef={audioRef}
                playing={playing}
                currentTime={currentTime}
                duration={effectiveDuration}
              />
              <div className="flex items-center gap-3">
                <span className="w-12 font-mono text-xs tabular-nums text-[#10b981]">
                  {formatTime(currentTime)}
                </span>
                <input
                  type="range"
                  min={0}
                  max={Math.max(1, effectiveDuration)}
                  step={0.1}
                  value={Math.min(currentTime, Math.max(1, effectiveDuration))}
                  onChange={(e) => seekTo(Number(e.target.value))}
                  aria-label="Timeline scrub"
                  className="flex-1 accent-[#10b981]"
                />
                <span className="w-12 text-right font-mono text-xs tabular-nums text-[#71717a]">
                  {formatTime(effectiveDuration)}
                </span>
              </div>
            </div>

            {/* Suno Studio Multitrack Stem Selector */}
            {!localFile && selectedJob && (
              <div className="mt-4 border-t border-[#26262e] pt-3.5">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-[#8e8e99]">
                    🎛️ Multitrack Stems &amp; Bus Monitoring
                  </span>
                  <button
                    type="button"
                    onClick={handleExtractStems}
                    disabled={stemsBusy}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-[#26262e] bg-[#1c1c22] px-2.5 py-1 font-mono text-[10px] font-semibold text-[#d4d4d8] transition hover:border-[#f59e0b]/50 hover:text-white disabled:opacity-40"
                  >
                    {stemsBusy ? (
                      <>
                        <Spinner className="h-3 w-3" /> Memisahkan Stems…
                      </>
                    ) : (
                      '🔀 Pisahkan / Segarkan AI Stems (Demucs)'
                    )}
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {stemTracks.map((track) => {
                    const on = activeStemKey === track.key
                    return (
                      <button
                        key={track.key}
                        type="button"
                        onClick={() => {
                          setPlaying(false)
                          setAudioError('')
                          setActiveStemKey(track.key)
                        }}
                        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                          on
                            ? 'border-[#10b981]/60 bg-[#10b981]/20 text-[#6ee7b7]'
                            : 'border-[#26262e] bg-[#18181d] text-[#a1a1aa] hover:border-[#3f3f46] hover:text-white'
                        }`}
                      >
                        {track.label}
                      </button>
                    )
                  })}
                </div>
                {stemsFeedback && (
                  <p className="mt-2 font-mono text-[11px] text-[#34d399]">{stemsFeedback}</p>
                )}
              </div>
            )}
          </div>

          {/* Dedicated Full-Size Studio Equalizer & Mastering Rack */}
          <StudioEqualizer
            audioRef={audioRef}
            jobId={localFile ? null : selectedJob?.job_id}
            genre={selectedJob?.genre || 'reggae'}
            initialEq={selectedJob?.metadata?.studio_eq}
            onApplyServerEq={localFile ? null : handleServerEq}
            compact={false}
          />
        </section>

        {/* Right Pane: Synchronized Lyrics & Studio Mastering Specs */}
        <aside className="w-full shrink-0 border-t border-[#26262e] bg-[#121216] p-4 xl:w-[320px] xl:border-l xl:border-t-0">
          <div className="space-y-4 xl:sticky xl:top-[105px] xl:max-h-[calc(100vh-120px)] xl:overflow-y-auto">
            {/* Synchronized Lyrics Monitor */}
            <div className="rounded-xl border border-[#26262e] bg-[#16161b] p-3.5">
              <div className="mb-2.5 flex items-center justify-between">
                <h3 className="text-xs font-bold uppercase tracking-wider text-[#d4d4d8]">
                  Karaoke Lyrics Monitor
                </h3>
                <span className="font-mono text-[10px] text-[#71717a]">
                  {lyricsLines.length ? `${lyricsLines.length} baris` : '—'}
                </span>
              </div>
              {lyricsLines.length > 0 ? (
                <div className="max-h-[380px] space-y-1 overflow-y-auto pr-1">
                  {lyricsLines.map((line, idx) => {
                    const active = idx === activeLyricIdx
                    return (
                      <button
                        key={`${line.start}-${idx}`}
                        type="button"
                        onClick={() => seekTo(line.start)}
                        className={`flex w-full gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition ${
                          active
                            ? 'border border-[#10b981]/40 bg-[#10b981]/15 font-semibold text-[#6ee7b7]'
                            : 'text-[#a1a1aa] hover:bg-[#1c1c22] hover:text-white'
                        }`}
                      >
                        <span className="w-10 shrink-0 font-mono text-[10px] text-[#71717a]">
                          {formatTime(line.start)}
                        </span>
                        <span className="flex-1 leading-5">{line.text}</span>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <p className="py-6 text-center text-xs text-[#71717a]">
                  Pilih lagu dengan lirik dari Library untuk memantau artikulasi vokal sambil mengatur frekuensi Presence (1kHz–6kHz).
                </p>
              )}
            </div>

            {/* Studio Engineering Reference Guide */}
            <div className="rounded-xl border border-[#26262e] bg-[#16161b] p-3.5">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[#d4d4d8]">
                Panduan Frekuensi Studio
              </h3>
              <ul className="space-y-2 text-[11px] leading-5 text-[#a1a1aa]">
                <li>
                  <strong className="text-[#6ee7b7]">60Hz – 150Hz (Sub &amp; Dub Bass):</strong> Mengatur dentuman kick one-drop dan ketebalan bassline reggae.
                </li>
                <li>
                  <strong className="text-[#6ee7b7]">400Hz (Low-Mid Mud):</strong> Turunkan <code className="text-white">-2dB</code> bila vokal atau gitar terdengar mendem/boxy.
                </li>
                <li>
                  <strong className="text-[#6ee7b7]">1kHz – 2.4kHz (Vocal &amp; Skank):</strong> Frekuensi utama artikulasi vokal dan petikan gitar skank off-beat.
                </li>
                <li>
                  <strong className="text-[#6ee7b7]">6kHz – 16kHz (Clarity &amp; Air):</strong> Memberikan kejernihan vokal modern dan desis hi-hat yang renyah.
                </li>
              </ul>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}

