import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Icon from './components/Icon.jsx'
import { Button, Field, Panel, Select, Spinner, TextInput } from './components/ui.jsx'
import {
  coverUploadMusic,
  getMusicGenres,
  getMusicGroup,
  resolveMediaAsset,
  streamMusicGroup,
} from './api.js'
import { getLocale } from './i18n.js'

// Fallback poll only; the live SSE stream drives normal updates.
const POLL_MS = 1500
const MAX_BYTES = 15_000_000

// Persona vokal: pengguna memilih sendiri, tidak perlu ikut preset gaya.
const VOICE_OPTIONS = [
  { id: 'auto', label: 'Auto (bebas)' },
  { id: 'female', label: 'Wanita' },
  { id: 'male', label: 'Pria' },
  { id: 'duet', label: 'Duet (pria + wanita)' },
]

const ENGINES = [
  { id: 'arrangement', label: '⭐ Aransemen Baru + Vokal Asli (YouTube Cover Style)' },
  { id: 'mulacover', label: 'MuLaCover - melodi & chord asli (AI nyanyi ulang)' },
  { id: 'diffrhythm', label: 'DiffRhythm - lagu ulang penuh Apache 2.0 (melodi baru)' },
  { id: 'ace-step', label: 'ACE-Step - draft cepat, kualitas terbatas' },
]

const ACCEPT = '.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,audio/*'

function labels() {
  const en = {
    eyebrow: 'COVER CREATE — FAITHFUL SONG COVER',
    title: 'Ubah lagu jadi cover, melodi aslinya tetap.',
    sub: 'Unggah lagu, pilih gaya, dan pilih siapa yang menyanyi. MuLaCover mempertahankan melodi serta progresi chord aslinya; lirik boleh kamu tempel atau biarkan Klipin menuliskannya lewat ASR.',
    source: 'Lagu sumber', drop: 'Klik untuk memilih file audio', dropHint: 'MP3, WAV, M4A, FLAC — maks. 15 MB',
    genre: 'Gaya / genre', voice: 'Persona vokal', engine: 'Engine cover',
    lyrics: 'Lirik (opsional)', lyricsHint: 'Kosongkan untuk menuliskan lirik otomatis dari audio (ASR).',
    seed: 'Seed', seedHint: 'Kosong = acak.',
    takes: 'Jumlah take', rights: 'Saya berhak memakai lagu sumber ini.',
    create: 'Buat cover', creating: 'Menyiapkan…',
    needFile: 'Pilih file audio lagu dulu.', needRights: 'Centang pernyataan hak pakai lagu dulu.',
    status: 'Status render', idle: 'Belum ada render.',
    waiting: 'Menunggu slot worker GPU…', done: 'Cover siap diputar.', failed: 'Cover gagal diproses.',
    download: 'Unduh', singin: 'Masuk dulu untuk membuat cover.',
    restart: 'Buat cover lagi',
  }
  const id = {
    eyebrow: 'COVER CREATE — COVER LAGU SETIA',
    title: 'Ubah lagu jadi cover, melodi aslinya tetap.',
    sub: 'Unggah lagu, pilih gaya, lalu pilih siapa yang menyanyi. MuLaCover mempertahankan melodi dan progresi chord aslinya; lirik boleh kamu tempel sendiri atau biarkan Klipin menuliskannya lewat ASR.',
    source: 'Lagu sumber', drop: 'Klik untuk memilih file audio', dropHint: 'MP3, WAV, M4A, FLAC — maks. 15 MB',
    genre: 'Gaya / genre', voice: 'Persona vokal', engine: 'Engine cover',
    lyrics: 'Lirik (opsional)', lyricsHint: 'Kosongkan untuk menuliskan lirik otomatis dari audio (ASR).',
    seed: 'Seed', seedHint: 'Kosong = acak.',
    takes: 'Jumlah take', rights: 'Saya berhak memakai lagu sumber ini.',
    create: 'Buat cover', creating: 'Menyiapkan…',
    needFile: 'Pilih file audio lagu dulu.', needRights: 'Centang pernyataan hak pakai lagu dulu.',
    status: 'Status render', idle: 'Belum ada render.',
    waiting: 'Menunggu slot worker GPU…', done: 'Cover siap diputar.', failed: 'Cover gagal diproses.',
    download: 'Unduh', singin: 'Masuk dulu untuk membuat cover.',
    restart: 'Buat cover lagi',
  }
  return getLocale() === 'id' ? id : en
}

function fmtSecs(s) {
  const n = Math.max(0, Math.round(s || 0))
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

const preferredTake = (items = []) => (
  items.find((t) => ['queued', 'processing'].includes(t.status))
    || items.find((t) => t.status === 'completed')
    || items.find((t) => t.status === 'failed')
    || items[0]
)

export default function CoverCreate({ user, authChecked = false, onSignIn, embedded = false }) {
  const L = labels()
  const [genres, setGenres] = useState([])
  const [coverEngines, setCoverEngines] = useState([])
  const [file, setFile] = useState(null)
  const [genre, setGenre] = useState('dangdut-koplo')
  const [voice, setVoice] = useState('auto')
  const [engine, setEngine] = useState('mulacover')
  const [lyrics, setLyrics] = useState('')
  const [seed, setSeed] = useState('')
  const [takes, setTakes] = useState(1)
  const [rights, setRights] = useState(false)
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [groupId, setGroupId] = useState('')
  const [groupTakes, setGroupTakes] = useState([])
  const [error, setError] = useState('')
  const [startedAt, setStartedAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const fileRef = useRef(null)

  useEffect(() => {
    let live = true
    getMusicGenres()
      .then((data) => {
        if (!live) return
        const list = data.genres || []
        setGenres(list)
        const koplo = list.find((g) => g.id === 'dangdut-koplo')
        if (koplo) setGenre(koplo.id)
        else if (list[0]) setGenre(list[0].id)
        const engines = Array.isArray(data.cover_engines) ? data.cover_engines : []
        setCoverEngines(engines)
        const available = engines.filter((e) => e.available).map((e) => e.id)
        setEngine((current) => (
          available.length && !available.includes(current) ? available[0] : current
        ))
      })
      .catch(() => {})
    return () => { live = false }
  }, [])

  useEffect(() => {
    if (!groupId) return undefined
    let stopped = false
    let timer = null
    let controller = null
    const applyTakes = (next) => {
      setGroupTakes(next)
      if (next.length && next.every((t) => ['completed', 'failed'].includes(t.status))) {
        setBusy(false)
        if (timer) clearTimeout(timer)
        return true
      }
      return false
    }
    const poll = async () => {
      try {
        const g = await getMusicGroup(groupId)
        if (stopped) return
        if (applyTakes(g.takes || [])) return
      } catch (e) {
        if (!stopped) setError(`Gagal memantau render: ${e.message}`)
      }
      if (!stopped) timer = setTimeout(poll, POLL_MS)
    }
    const run = async () => {
      let ended = false
      try {
        controller = typeof AbortController !== 'undefined' ? new AbortController() : null
        await streamMusicGroup(groupId, {
          signal: controller?.signal,
          onGroup: (g) => { if (!stopped && applyTakes(g.takes || [])) ended = true },
          onEnd: () => { ended = true },
        })
        if (stopped || ended) return
      } catch {
        if (stopped || controller?.signal.aborted) return
      }
      if (!stopped) poll()
    }
    run()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      controller?.abort()
    }
  }, [groupId])

  useEffect(() => {
    if (!busy) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [busy])

  const elapsed = startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : 0
  const result = useMemo(() => preferredTake(groupTakes), [groupTakes])
  const running = groupTakes.some((t) => ['queued', 'processing'].includes(t.status))
  const engineStatus = useMemo(() => {
    if (!coverEngines.length) return null
    return coverEngines.find((e) => e.id === engine) || null
  }, [coverEngines, engine])
  const engineBlocked = Boolean(engineStatus && !engineStatus.available)
  const progress = useMemo(() => {
    if (!result) return 0
    if (result.status === 'completed' || result.status === 'failed') return 100
    if (result.status === 'queued') return 6
    return Math.min(96, 12 + Math.round((elapsed / 240) * 84))
  }, [result, elapsed])

  const pickFile = (f) => {
    if (!f) return
    if (f.size > MAX_BYTES) {
      setError('File audio terlalu besar (maks. 15 MB).')
      return
    }
    setError('')
    setFile(f)
  }

  const create = async () => {
    if (!user) { onSignIn?.(); return }
    if (busy) return
    if (!file) { setError(L.needFile); return }
    if (!rights) { setError(L.needRights); return }
    if (engineBlocked) { setError('Engine cover yang dipilih belum aktif di server.'); return }
    setError('')
    setBusy(true)
    setGroupId('')
    setGroupTakes([])
    setStartedAt(Date.now())
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('genre', genre)
      fd.append('vocal_gender', voice)
      fd.append('cover_engine', engine)
      fd.append('lyrics', lyrics)
      fd.append('seed', seed ? String(parseInt(seed, 10) || 0) : '0')
      fd.append('take_count', String(takes))
      fd.append('rights_confirmed', 'true')
      const res = await coverUploadMusic(fd)
      setGroupId(res.group_id || '')
    } catch (e) {
      setError(e.message || 'Gagal memulai cover.')
      setBusy(false)
    }
  }

  const reset = () => {
    setGroupId('')
    setGroupTakes([])
    setStartedAt(0)
    setError('')
    setBusy(false)
  }

  if (authChecked && !user) {
    return (
      <div className="mx-auto max-w-lg py-16">
        <Panel label={L.status}>
          <p className="mb-4 text-sm text-white/70">{L.singin}</p>
          <Button onClick={() => onSignIn?.()}>Masuk</Button>
        </Panel>
      </div>
    )
  }

  return (
    <div className={embedded ? '' : 'mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8'}>
      {!embedded && (
        <header className="mb-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-cyan-soft">{L.eyebrow}</p>
          <h1 className="mt-2 text-2xl font-semibold text-white sm:text-3xl">{L.title}</h1>
          <p className="mt-2 max-w-3xl text-sm text-white/55">{L.sub}</p>
        </header>
      )}

      <div className={embedded ? 'grid gap-4' : 'grid gap-6 lg:grid-cols-2'}>
        <Panel label={L.source}>
          <div className="space-y-4">
            <div>
              <input
                ref={fileRef}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => pickFile(e.target.files?.[0])}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDrag(false)
                  pickFile(e.dataTransfer?.files?.[0])
                }}
                className={`flex w-full flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition ${
                  drag ? 'border-cyan bg-cyan/10' : 'border-cyan/30 hover:border-cyan/60 hover:bg-white/5'
                }`}
              >
                <Icon name="music" className="h-7 w-7 text-cyan" aria-hidden />
                <span className="text-sm font-medium text-white">{L.drop}</span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-white/35">{L.dropHint}</span>
                {file && (
                  <span className="mt-1 rounded-full border border-cyan/40 bg-cyan/10 px-3 py-1 text-xs text-cyan-soft">
                    {file.name} · {(file.size / 1_048_576).toFixed(1)} MB
                  </span>
                )}
              </button>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={L.genre}>
                <Select value={genre} onChange={(e) => setGenre(e.target.value)}>
                  {genres.map((g) => (
                    <option key={g.id} value={g.id}>{g.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label={L.voice}>
                <Select value={voice} onChange={(e) => setVoice(e.target.value)}>
                  {VOICE_OPTIONS.map((v) => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </Select>
              </Field>
            </div>

            <Field label={L.engine}>
              <Select value={engine} onChange={(e) => setEngine(e.target.value)}>
                {ENGINES.map((e2) => {
                  const status = coverEngines.find((item) => item.id === e2.id)
                  const off = Boolean(status && !status.available)
                  return (
                    <option key={e2.id} value={e2.id} disabled={off}>
                      {e2.label}{off ? ' — belum aktif di server' : ''}
                    </option>
                  )
                })}
              </Select>
              {engineBlocked && (
                <p className="mt-1.5 text-[11px] leading-4 text-warn">
                  Engine ini belum aktif. Pilih engine lain yang tersedia.
                </p>
              )}
            </Field>

            <Field label={L.lyrics} hint={L.lyricsHint}>
              <textarea
                value={lyrics}
                onChange={(e) => setLyrics(e.target.value)}
                rows={6}
                placeholder="[Verse]&#10;..."
                className="w-full resize-y rounded-lg border border-border-soft bg-navy-2/50 px-3 py-2 text-sm text-white outline-none focus:border-cyan/60"
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={L.seed} hint={L.seedHint}>
                <TextInput
                  value={seed}
                  onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ''))}
                  placeholder="acak"
                  inputMode="numeric"
                />
              </Field>
              <Field label={L.takes}>
                <Select value={String(takes)} onChange={(e) => setTakes(Number(e.target.value))}>
                  <option value="1">1</option>
                  <option value="2">2</option>
                </Select>
              </Field>
            </div>

            <label className="flex items-start gap-2 text-xs text-white/65">
              <input
                type="checkbox"
                checked={rights}
                onChange={(e) => setRights(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-cyan"
              />
              <span>{L.rights}</span>
            </label>

            {error && <p className="text-sm text-danger">{error}</p>}

            <div className="flex items-center gap-3">
              <Button onClick={create} disabled={busy || engineBlocked}>
                {busy ? (<span className="flex items-center gap-2"><Spinner className="h-4 w-4" /> {L.creating}</span>) : L.create}
              </Button>
              {groupId && !running && (
                <Button variant="ghost" onClick={reset}>{L.restart}</Button>
              )}
            </div>
          </div>
        </Panel>

        <Panel label={L.status} right={busy ? <span className="font-mono text-[10px] text-cyan-soft">{fmtSecs(elapsed)}</span> : null}>
          {!groupId && !groupTakes.length && (
            <p className="py-8 text-center text-sm text-white/40">{L.idle}</p>
          )}

          {groupId && (
            <div className="space-y-4">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-cyan transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="font-mono text-[10px] uppercase tracking-widest text-white/45">
                {result?.status === 'completed' ? L.done
                  : result?.status === 'failed' ? L.failed
                  : result?.status === 'queued' ? L.waiting
                  : (result?.metadata?.stage || '')}
              </p>

              <ul className="space-y-2">
                {groupTakes.map((t) => (
                  <li key={t.job_id} className="flex items-center justify-between rounded-lg border border-border-soft bg-navy-2/40 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-xs text-white/70">
                      take {t.take_index + 1} · {t.provider}
                    </span>
                    <span className={`ml-2 shrink-0 font-mono text-[10px] uppercase tracking-widest ${
                      t.status === 'completed' ? 'text-signal'
                        : t.status === 'failed' ? 'text-danger' : 'text-cyan-soft'
                    }`}>
                      {t.status}
                    </span>
                  </li>
                ))}
              </ul>

              {result?.status === 'completed' && (
                <div className="space-y-2">
                  <audio
                    src={resolveMediaAsset(result.audio_path)}
                    controls
                    preload="none"
                    className="w-full"
                  />
                  <a
                    href={resolveMediaAsset(result.audio_path)}
                    download
                    className="inline-flex items-center gap-2 text-xs text-cyan-soft hover:text-cyan"
                  >
                    <Icon name="download" className="h-4 w-4" aria-hidden /> {L.download}
                  </a>
                </div>
              )}

              {result?.status === 'failed' && result.error && (
                <p className="text-xs text-danger">{result.error}</p>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
