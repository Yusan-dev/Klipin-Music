import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Icon from './components/Icon.jsx'
import StudioEqualizer, { STUDIO_EQ_PRESETS } from './components/StudioEqualizer.jsx'
import { Button, Field, Panel, Select, Spinner, TextInput } from './components/ui.jsx'
import {
  coverUploadMusic,
  equalizeMusicJob,
  getMusicGenres,
  getMusicGroup,
  getMusicPersonas,
  resolveDownloadAsset,
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

const VOCAL_STYLE_OPTIONS = [
  { id: 'auto', label: 'Otomatis (Sesuai Genre)' },
  { id: 'warm-smooth', label: 'Warm & Smooth Studio' },
  { id: 'laidback-chill', label: 'Laid-Back Island Chill (Reggae/Akustik)' },
  { id: 'soulful-raspy', label: 'Soulful & Slight Rasp' },
  { id: 'emotional-ballad', label: 'Emotional Expressive Ballad' },
  { id: 'bright-pop', label: 'Bright Radio Pop' },
  { id: 'traditional-cengkok', label: 'Cengkok Nusantara (Jawa/Sunda/Koplo)' },
]

const ACCEPT = '.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,audio/*'

function labels() {
  const en = {
    eyebrow: 'COVER CREATE — FAITHFUL SONG COVER',
    title: 'Ubah lagu jadi cover, melodi aslinya tetap.',
    sub: 'Unggah lagu, pilih gaya, dan atur parameter aransemen. Engine klipin-music mengunci notasi melodi vokal & lirik lagu aslinya dengan presisi tinggi.',
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
    sub: 'Unggah lagu, pilih gaya, lalu atur parameter aransemen. Engine klipin-music mengunci notasi melodi vokal & lirik lagu aslinya dengan presisi tinggi.',
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

export default function CoverCreate({
  user,
  authChecked = false,
  onSignIn,
  embedded = false,
  personaId: parentPersonaId = '',
  selectedPersona: parentSelectedPersona = null,
  personaSlot = null,
}) {
  const L = labels()
  const [genres, setGenres] = useState([])
  const [coverEngines, setCoverEngines] = useState([])
  const [personas, setPersonas] = useState([])
  const [localPersonaId, setLocalPersonaId] = useState('')
  const [file, setFile] = useState(null)
  const [genre, setGenre] = useState('dangdut-koplo')
  const [voice, setVoice] = useState('auto')
  const [vocalStyle, setVocalStyle] = useState('auto')
  const activePersonaId = embedded ? parentPersonaId : localPersonaId
  const activePersona = useMemo(
    () => (embedded ? parentSelectedPersona : personas.find((p) => p.persona_id === localPersonaId) || null),
    [embedded, parentSelectedPersona, personas, localPersonaId],
  )
  const femalePersonas = useMemo(
    () => personas.filter((p) => String(p.vocal_gender || '').toLowerCase() === 'female'),
    [personas],
  )
  const malePersonas = useMemo(
    () => personas.filter((p) => String(p.vocal_gender || '').toLowerCase() === 'male'),
    [personas],
  )
  // Persona tanpa kategori (auto) tetap harus bisa dipilih untuk menyanyi.
  const otherPersonas = useMemo(
    () => personas.filter((p) => !['female', 'male'].includes(String(p.vocal_gender || '').toLowerCase())),
    [personas],
  )
  const [engine] = useState('yue2_comfy')
  const [directVoiceFile, setDirectVoiceFile] = useState(null)
  const [directVoicePreviewUrl, setDirectVoicePreviewUrl] = useState('')
  const [pitchShift, setPitchShift] = useState('auto')
  const [restyleStrength, setRestyleStrength] = useState(0.55)
  const [weirdness, setWeirdness] = useState(45)
  const [styleInfluence, setStyleInfluence] = useState(70)
  const [lyrics, setLyrics] = useState('')
  const [stylePrompt, setStylePrompt] = useState('')
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
  const [showStudioEq, setShowStudioEq] = useState(false)
  const [mediaNonce, setMediaNonce] = useState(0)
  const [eqConfig, setEqConfig] = useState(() => ({
    preset: 'flat',
    bands: [0, 0, 0, 0, 0, 0, 0, 0],
    preamp_db: 0,
    compressor: 0,
    stereo_width: 1.0,
    reverb: 0,
    normalize_lufs: true,
  }))
  const fileRef = useRef(null)
  const previewAudioRef = useRef(null)

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
      })
      .catch(() => {})
    return () => { live = false }
  }, [])

  useEffect(() => {
    if (!user || embedded) return
    getMusicPersonas()
      .then((d) => setPersonas(d.personas || []))
      .catch(() => {})
  }, [user, embedded])

  useEffect(() => {
    if (activePersona?.vocal_gender === 'female' || activePersona?.vocal_gender === 'male') {
      setVoice(activePersona.vocal_gender)
    }
  }, [activePersona])

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
      let errStatus = null
      let retryAfter = 0
      try {
        const g = await getMusicGroup(groupId)
        if (stopped) return
        if (applyTakes(g.takes || [])) return
      } catch (e) {
        errStatus = e?.status
        retryAfter = e?.retryAfter || 0
        if (!stopped && errStatus !== 429) setError(`Gagal memantau render: ${e.message}`)
      }
      if (!stopped) {
        const delay = errStatus === 429 ? Math.min(10000, Math.max(3000, retryAfter * 1000 || 5000)) : POLL_MS
        timer = setTimeout(poll, delay)
      }
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
  const isOriginalGenre = genre === 'original'
  const effectiveEngine = isOriginalGenre ? 'voice' : engine
  const engineStatus = useMemo(() => {
    if (!coverEngines.length) return null
    return coverEngines.find((e) => e.id === effectiveEngine) || null
  }, [coverEngines, effectiveEngine])
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
      const effectiveVoice = (activePersona?.vocal_gender === 'female' || activePersona?.vocal_gender === 'male')
        ? activePersona.vocal_gender
        : voice
      const fd = new FormData()
      fd.append('file', file)
      fd.append('genre', genre)
      fd.append('vocal_gender', effectiveVoice)
      if (activePersonaId) fd.append('persona_id', activePersonaId)
      if (directVoiceFile) fd.append('voice_file', directVoiceFile)
      fd.append('vocal_style', vocalStyle)
      fd.append('cover_engine', isOriginalGenre ? 'voice' : 'yue2_comfy')
      fd.append('pitch_shift', pitchShift)
      fd.append('restyle_strength', String(restyleStrength))
      fd.append('weirdness', String(weirdness))
      fd.append('style_influence', String(styleInfluence))
      fd.append('lyrics', lyrics)
      if (stylePrompt.trim()) fd.append('prompt', stylePrompt.trim())
      fd.append('seed', seed ? String(parseInt(seed, 10) || 0) : '0')
      fd.append('take_count', String(takes))
      fd.append('rights_confirmed', 'true')
      if (eqConfig?.preset) fd.append('eq_preset', eqConfig.preset)
      if (Array.isArray(eqConfig?.bands)) fd.append('eq_bands', JSON.stringify(eqConfig.bands))
      if (eqConfig?.preamp_db !== undefined) fd.append('eq_preamp_db', String(eqConfig.preamp_db))
      if (eqConfig?.compressor !== undefined) fd.append('eq_compressor', String(eqConfig.compressor))
      if (eqConfig?.stereo_width !== undefined) fd.append('eq_stereo_width', String(eqConfig.stereo_width))
      if (eqConfig?.reverb !== undefined) fd.append('eq_reverb', String(eqConfig.reverb))
      const res = await coverUploadMusic(fd)
      setGroupId(res.group_id || '')
    } catch (e) {
      setError(e.message || 'Gagal memulai cover.')
      setBusy(false)
    }
  }

  const handleServerEq = useCallback(async (targetJobId, payload) => {
    const updated = await equalizeMusicJob(targetJobId, payload)
    if (updated?.job_id) {
      setGroupTakes((prev) => prev.map((t) => (t.job_id === updated.job_id ? updated : t)))
      setMediaNonce((n) => n + 1)
    }
    return updated
  }, [])

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

  if (embedded) {
    return (
      <div className="space-y-3.5">
        {/* Langkah 1: Lagu Sumber & Lirik */}
        <div className="space-y-2.5 rounded-xl border border-border bg-navy-1 p-3">
          <div className="text-[11px] font-bold uppercase tracking-wider text-white/80">
            1 · Lagu Sumber &amp; Lirik
          </div>
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
            className={`flex w-full flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-4 text-center transition ${
              drag ? 'border-cyan bg-cyan/10' : 'border-border bg-navy-2 hover:border-cyan/50 hover:bg-navy-3'
            }`}
          >
            <Icon name="music" className="h-5 w-5 text-white/60" aria-hidden />
            <span className="text-xs font-medium text-white">{L.drop}</span>
            <span className="font-mono text-[10px] text-white/55">{L.dropHint}</span>
            {file && (
              <span className="mt-1 rounded-md border border-cyan/40 bg-cyan/10 px-2.5 py-0.5 text-[11px] font-medium text-cyan-soft">
                {file.name} · {(file.size / 1_048_576).toFixed(1)} MB
              </span>
            )}
          </button>

          <div>
            <label className="mb-1 block text-[11px] font-medium text-white/70">{L.lyrics}</label>
            <textarea
              value={lyrics}
              onChange={(e) => setLyrics(e.target.value)}
              rows={3}
              placeholder={L.lyricsHint}
              className="w-full resize-y rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white placeholder:text-white/40 outline-none focus:border-cyan"
            />
          </div>
        </div>

        {/* Langkah 2: Gaya Musik Cover */}
        <div className="space-y-2.5 rounded-xl border border-border bg-navy-1 p-3">
          <div className="text-[11px] font-bold uppercase tracking-wider text-white/80">
            2 · Gaya Musik Cover (Genre &amp; Aransemen)
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-white/70">{L.genre}</label>
            <select value={genre} onChange={(e) => setGenre(e.target.value)}
              className="w-full rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan">
              <option value="original">🎵 Lagu Original — Pertahankan Musik Asli (Ganti Vokal Saja)</option>
              <optgroup label="Ubah Genre / Aransemen Baru">
                {genres.map((g) => (
                  <option key={g.id} value={g.id}>{g.label}</option>
                ))}
              </optgroup>
            </select>
          </div>

          {isOriginalGenre ? (
            <div className="rounded-lg border border-signal/35 bg-signal/10 p-2.5 text-[11px] leading-relaxed text-signal">
              ✨ <b>Mode Lagu Original:</b> Musik &amp; instrumen lagu sumber dipertahankan 100%. Hanya vokal penyanyinya yang diganti menggunakan <b>Persona</b> atau <b>File Suara</b> di langkah 3 (cocok untuk membawakan lagu A dengan suara artis/sosok pilihan Anda).
            </div>
          ) : (
            <div>
              <label className="mb-1 block text-[11px] font-medium text-white/70">Preset Vibe / Arahan Aransemen (opsional)</label>
              {genre === 'reggae' && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {[
                    {
                      id: 'boenter',
                      label: '🌴 Reggae Santai (BOENTER)',
                      prompt: 'warm island roots pop-reggae cover (BOENTER chill reggae vibe), laid-back 68 BPM half-time one-drop drum groove, muted clean offbeat guitar skank, warm Hammond B3 organ bubble, deep round sub-dub bassline, melodic brass intro hook, relaxed soulful vocal with warm reverb and subtle dub echo, silky analog highs',
                    },
                    {
                      id: 'ska',
                      label: '🎺 Reggae Ska & Brass',
                      prompt: 'upbeat Indonesian ska-reggae cover, lively brass section (trumpet, trombone, saxophone hooks), crisp offbeat guitar skank, bouncy walking dub bassline, tight one-drop snare and rimshot',
                    },
                    {
                      id: 'acoustic',
                      label: '🌊 Reggae Akustik',
                      prompt: 'acoustic island reggae cover, nylon and steel acoustic guitar offbeat skank, warm cajon and rimshot one-drop, deep upright dub bass, relaxed intimate vocal',
                    },
                    {
                      id: 'dub',
                      label: '🔊 Reggae Dub Bass',
                      prompt: 'deep roots dub reggae cover, heavy warm sub-bass pocket (60-140 Hz), spacious spring reverb and analog tape echo, laid-back one-drop rimshot, warm Hammond organ bubble',
                    },
                  ].map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => setStylePrompt(preset.prompt)}
                      className={`rounded-md border px-2 py-1 text-[11px] font-medium transition ${
                        stylePrompt === preset.prompt
                          ? 'border-cyan bg-cyan/20 text-cyan-soft'
                          : 'border-border bg-navy-2 text-white/70 hover:border-cyan/40 hover:text-white'
                      }`}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              )}
              <input
                value={stylePrompt}
                onChange={(e) => setStylePrompt(e.target.value)}
                placeholder="Contoh: warm roots reggae, brass intro, dub bassline, santai…"
                className="w-full rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white placeholder:text-white/40 outline-none focus:border-cyan"
              />
            </div>
          )}
        </div>

        {/* Langkah 3: Penyanyi & Persona Suara */}
        <div className="space-y-2.5 rounded-xl border border-border bg-navy-1 p-3">
          <div className="text-[11px] font-bold uppercase tracking-wider text-white/80">
            3 · Penyanyi &amp; Persona Suara (👩 Cewek / 👨 Cowok)
          </div>
          {personaSlot}

          <div className="rounded-lg border border-dashed border-border bg-navy-2 p-2.5">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] font-semibold text-white/85">
                🎙️ Atau Upload Suara Target Langsung (Opsional — mis. suara artis/tokoh)
              </span>
              {directVoiceFile && (
                <button
                  type="button"
                  onClick={() => {
                    setDirectVoiceFile(null)
                    setDirectVoicePreviewUrl((u) => { if (u) URL.revokeObjectURL(u); return '' })
                  }}
                  className="text-[10px] font-semibold text-danger hover:underline"
                >
                  ✕ Hapus
                </button>
              )}
            </div>
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => {
                const picked = e.target.files?.[0] || null
                setDirectVoiceFile(picked)
                setDirectVoicePreviewUrl((old) => {
                  if (old) URL.revokeObjectURL(old)
                  return picked ? URL.createObjectURL(picked) : ''
                })
              }}
              className="block w-full text-[11px] text-white/70 file:mr-2 file:rounded-md file:border-0 file:bg-cyan/20 file:px-2.5 file:py-1 file:text-[11px] file:font-semibold file:text-cyan-soft"
            />
            {directVoiceFile && (
              <div className="mt-1.5 space-y-1">
                <p className="text-[10.5px] text-signal">
                  ✓ Memakai file suara: <b>{directVoiceFile.name}</b> ({(directVoiceFile.size / 1_048_576).toFixed(1)} MB). Musik latar otomatis dibersihkan bila ada.
                </p>
                {directVoicePreviewUrl && (
                  <audio controls src={directVoicePreviewUrl} className="h-7 max-w-full" />
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-white/70">{L.voice}</label>
              <select
                value={voice}
                disabled={Boolean(activePersona)}
                onChange={(e) => setVoice(e.target.value)}
                className="w-full rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan disabled:opacity-60"
              >
                {VOICE_OPTIONS.map((v) => (
                  <option key={v.id} value={v.id}>{v.id === 'auto' && activePersona ? 'Ikuti Persona' : v.label}</option>
                ))}
              </select>
            </div>
            {isOriginalGenre ? (
              <div>
                <label className="mb-1 block text-[11px] font-medium text-white/70">Nada Vokal (Pitch)</label>
                <select
                  value={pitchShift}
                  onChange={(e) => setPitchShift(e.target.value)}
                  className="w-full rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan"
                >
                  <option value="auto">Auto (Ikuti Suara Target)</option>
                  <option value="0">Asli (0 semitone)</option>
                  <option value="12">+12 (+1 Oktaf: Cowok → Cewek)</option>
                  <option value="-12">-12 (-1 Oktaf: Cewek → Cowok)</option>
                  <option value="5">+5 semitone</option>
                  <option value="-5">-5 semitone</option>
                  <option value="3">+3 semitone</option>
                  <option value="-3">-3 semitone</option>
                </select>
              </div>
            ) : (
              <div>
                <label className="mb-1 block text-[11px] font-medium text-white/70">Karakter vokal</label>
                <select value={vocalStyle} onChange={(e) => setVocalStyle(e.target.value)}
                  className="w-full rounded-lg border border-border bg-navy-2 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan">
                  {VOCAL_STYLE_OPTIONS.map((vs) => (
                    <option key={vs.id} value={vs.id}>{vs.label}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </div>

        {/* Pengaturan Lanjutan & Studio EQ (Opsional - Lipat) */}
        <details className="group rounded-xl border border-border bg-navy-1 p-3">
          <summary className="flex cursor-pointer list-none items-center justify-between text-[11px] font-semibold text-white/75 hover:text-white">
            <span>⚙️ Pengaturan Lanjutan &amp; Studio EQ (Opsional)</span>
            <span className="font-mono text-[10px] text-white/50 group-open:rotate-180">▼</span>
          </summary>
          <div className="mt-3 space-y-3 border-t border-border-soft pt-3">
            {!isOriginalGenre && (
              <>
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="text-white/70">Kunci Melodi &amp; Ketukan</span>
                    <span className="font-mono text-cyan-soft">{Math.round(restyleStrength * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min={0.2}
                    max={0.85}
                    step={0.05}
                    value={restyleStrength}
                    onChange={(e) => setRestyleStrength(parseFloat(e.target.value))}
                    className="w-full accent-cyan"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <div className="mb-1 flex items-center justify-between text-[11px]">
                      <span className="text-white/70">Kreativitas Aransemen</span>
                      <span className="font-mono text-white/90">{weirdness}%</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={weirdness}
                      onChange={(e) => setWeirdness(Number(e.target.value))}
                      className="w-full accent-cyan"
                    />
                  </div>
                  <div>
                    <div className="mb-1 flex items-center justify-between text-[11px]">
                      <span className="text-white/70">Kepatuhan Genre</span>
                      <span className="font-mono text-white/90">{styleInfluence}%</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={styleInfluence}
                      onChange={(e) => setStyleInfluence(Number(e.target.value))}
                      className="w-full accent-cyan"
                    />
                  </div>
                </div>
              </>
            )}

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[11px] font-medium text-white/70">Pre-Master Studio Equalizer</span>
                <button
                  type="button"
                  onClick={() => setShowStudioEq((v) => !v)}
                  className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold transition ${
                    showStudioEq || eqConfig.preset !== 'flat'
                      ? 'border-signal/60 bg-signal/15 text-signal'
                      : 'border-border bg-navy-2 text-white/75 hover:text-white'
                  }`}
                >
                  🎚️ Studio EQ ({eqConfig.preset === 'custom' ? 'Custom' : (STUDIO_EQ_PRESETS.find((p) => p.id === eqConfig.preset)?.label || 'Flat')})
                </button>
              </div>
              {showStudioEq && (
                <StudioEqualizer
                  audioRef={previewAudioRef}
                  genre={genre}
                  initialEq={eqConfig}
                  onChange={setEqConfig}
                  compact
                  title="PRE-MASTER STUDIO EQUALIZER"
                />
              )}
            </div>
          </div>
        </details>

        <label className="flex items-start gap-2 text-xs text-white/70">
          <input
            type="checkbox"
            checked={rights}
            onChange={(e) => setRights(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 accent-cyan"
          />
          <span>{L.rights}</span>
        </label>

        {error && <p className="text-xs text-danger">{error}</p>}

        <button
          type="button"
          onClick={create}
          disabled={busy || engineBlocked}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-cyan px-4 py-3 text-[13px] font-bold text-navy shadow-md transition hover:brightness-110 disabled:opacity-45"
        >
          {busy ? (<><Spinner className="h-3.5 w-3.5" /> {L.creating}</>) : (isOriginalGenre ? '🎤 Buat Cover Ganti Vokal (Musik Asli)' : '🎵 Buat Cover Lagu')}
        </button>

        {groupId && (
          <div className="space-y-2 rounded-lg border border-border bg-navy-1 p-3">
            <div className="flex items-center justify-between text-[11px] text-white/70">
              <span>{result?.status === 'completed' ? L.done : result?.status === 'failed' ? L.failed : L.waiting}</span>
              <span className="font-mono tabular-nums">{fmtSecs(elapsed)}</span>
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-navy-3">
              <div className="h-full rounded-full bg-cyan transition-all" style={{ width: `${progress}%` }} />
            </div>
            {result?.status === 'completed' && (
              <div className="space-y-2 pt-1">
                <audio
                  ref={previewAudioRef}
                  src={`${resolveMediaAsset(result.audio_path)}${String(result.audio_path || '').includes('?') ? '&' : '?'}v=${mediaNonce}`}
                  controls
                  preload="metadata"
                  className="h-8 w-full"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <a
                    href={resolveDownloadAsset(result.audio_path, `${result.title || 'klipin-cover'}.mp3`)}
                    download={`${result.title || 'klipin-cover'}.mp3`}
                    className="inline-flex items-center gap-1.5 text-[11px] font-medium text-cyan hover:underline"
                  >
                    <Icon name="download" className="h-3.5 w-3.5" aria-hidden /> {L.download}
                  </a>
                  <button
                    type="button"
                    onClick={() => {
                      window.location.hash = `#/studio/${result.job_id}`
                    }}
                    className="inline-flex items-center gap-1.5 rounded-md border border-signal/50 bg-signal/15 px-2.5 py-1 text-[11px] font-semibold text-signal transition hover:bg-signal/25"
                  >
                    🎚️ Buka di Studio Equalizer
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
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

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label={L.genre}>
                <Select value={genre} onChange={(e) => setGenre(e.target.value)}>
                  <option value="original">🎵 Lagu Original — Pertahankan Musik Asli (Ganti Vokal Saja)</option>
                  {genres.map((g) => (
                    <option key={g.id} value={g.id}>{g.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Persona Suara (👩 Cewek / 👨 Cowok)">
                <Select
                  value={localPersonaId}
                  onChange={(e) => {
                    const nextId = e.target.value
                    setLocalPersonaId(nextId)
                    const found = personas.find((p) => p.persona_id === nextId)
                    if (found?.vocal_gender === 'female' || found?.vocal_gender === 'male') {
                      setVoice(found.vocal_gender)
                    }
                  }}
                >
                  <option value="">Tanpa persona — suara default</option>
                  {femalePersonas.length > 0 && (
                    <optgroup label={`👩 Suara Cewek (${femalePersonas.length})`}>
                      {femalePersonas.map((p) => (
                        <option key={p.persona_id} value={p.persona_id}>
                          👩 {p.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {malePersonas.length > 0 && (
                    <optgroup label={`👨 Suara Cowok (${malePersonas.length})`}>
                      {malePersonas.map((p) => (
                        <option key={p.persona_id} value={p.persona_id}>
                          👨 {p.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {otherPersonas.length > 0 && (
                    <optgroup label={`🎭 Persona Lainnya (${otherPersonas.length})`}>
                      {otherPersonas.map((p) => (
                        <option key={p.persona_id} value={p.persona_id}>
                          🎭 {p.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </Select>
              </Field>
              <Field label={L.voice}>
                <Select value={voice} disabled={Boolean(activePersona)} onChange={(e) => setVoice(e.target.value)}>
                  {VOICE_OPTIONS.map((v) => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="flex items-center justify-between rounded-xl border border-cyan/30 bg-cyan/5 px-3.5 py-2.5 text-xs">
              <span className="font-medium text-white">✨ Engine Cover: klipin-music</span>
              <span className="rounded bg-cyan/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-cyan-soft">
                Fast Keep-Warm · Lirik &amp; Melodi Terkunci
              </span>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Karakter penyampaian vokal">
                <Select value={vocalStyle} onChange={(e) => setVocalStyle(e.target.value)}>
                  {VOCAL_STYLE_OPTIONS.map((vs) => (
                    <option key={vs.id} value={vs.id}>{vs.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label={`Kunci Melodi & Ketukan (${Math.round(restyleStrength * 100)}%)`}>
                <input
                  type="range"
                  min={0.2}
                  max={0.85}
                  step={0.05}
                  value={restyleStrength}
                  onChange={(e) => setRestyleStrength(parseFloat(e.target.value))}
                  className="mt-2 w-full accent-cyan"
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={`Kreativitas Aransemen (${weirdness}%)`}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={weirdness}
                  onChange={(e) => setWeirdness(Number(e.target.value))}
                  className="mt-2 w-full accent-cyan"
                />
              </Field>
              <Field label={`Kepatuhan Gaya Genre (${styleInfluence}%)`}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={styleInfluence}
                  onChange={(e) => setStyleInfluence(Number(e.target.value))}
                  className="mt-2 w-full accent-cyan"
                />
              </Field>
            </div>

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
                      take {t.take_index + 1} · klipin-music
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
                <div className="space-y-3">
                  <audio
                    ref={previewAudioRef}
                    src={`${resolveMediaAsset(result.audio_path)}${String(result.audio_path || '').includes('?') ? '&' : '?'}v=${mediaNonce}`}
                    controls
                    preload="metadata"
                    className="w-full"
                  />
                  <div className="flex flex-wrap items-center gap-3">
                    <a
                      href={resolveDownloadAsset(result.audio_path, `${result.title || 'klipin-cover'}.mp3`)}
                      download={`${result.title || 'klipin-cover'}.mp3`}
                      className="inline-flex items-center gap-2 text-xs text-cyan-soft hover:text-cyan"
                    >
                      <Icon name="download" className="h-4 w-4" aria-hidden /> {L.download}
                    </a>
                    <button
                      type="button"
                      onClick={() => {
                        window.location.hash = `#/studio/${result.job_id}`
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-signal/50 bg-signal/15 px-3 py-1.5 text-xs font-semibold text-signal transition hover:bg-signal/25"
                    >
                      🎚️ Buka di Studio Equalizer
                    </button>
                  </div>
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
