import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Icon from './components/Icon.jsx'
import VoiceDesigner, { PrivateVoicePlayer } from './components/VoiceDesigner.jsx'
import CoverCreate from './CoverCreate.jsx'
import MusicWaveform from './components/MusicWaveform.jsx'
import { Spinner } from './components/ui.jsx'
import {
  addMusicProjectRevision,
  composeMusicLyrics,
  coverSongMusic,
  createMusicLyricsDraft,
  createMusicProject,
  deleteMusicLyricsDraft,
  deleteMusicProject,
  remixMusicUrl,
  createMusicPersona,
  deleteMusicPersona,
  extendMusic,
  fetchMusicStems,
  generateMusic,
  getMusicCost,
  getMusicEngine,
  getMusicFeed,
  getMusicGenres,
  getMusicGroup,
  getMusicJobs,
  getMusicLyricsDraft,
  getMusicPersonas,
  getMusicProject,
  getMusicProjectRevision,
  getMusicStatus,
  listMusicLyricsDrafts,
  listMusicProjects,
  planMusicSong,
  realignMusic,
  refreshMusicCover,
  resolveMediaAsset,
  restoreMusicProjectRevision,
  shareMusic,
  streamMusicGroup,
  updateMusicLyricsDraft,
  uploadMusicVoicePersona,
} from './api.js'

const DURATIONS = [60, 90, 120, 150, 180, 210, 240, 300]
const VOCAL_GENDERS = [
  { id: 'auto', label: 'Auto (bebas)' },
  { id: 'female', label: '👩 Wanita' },
  { id: 'male', label: '👨 Pria' },
  { id: 'duet', label: '👥 Duet' },
]
// Engine + take controls. Values map 1:1 to MusicGenerateRequest so nothing is
// hidden from the API. `available` is filled from the engine preflight at runtime.
const PROVIDER_OPTIONS = [
  { id: 'heartmula', label: 'HeartMuLa — final (default)', hint: 'Engine asli Klipin Music (Apache 2.0) untuk lagu final.' },
  { id: 'diffrhythm', label: 'DiffRhythm — full song Apache 2.0', hint: 'Latent diffusion lagu penuh yang cepat & legal komersial.' },
  { id: 'auto', label: 'Auto A/B — draft + final', hint: 'Dua take: draft ACE-Step cepat lalu final HeartMuLa/DiffRhythm.' },
  { id: 'ace-step', label: 'ACE-Step — draft cepat', hint: 'Iterasi ide lebih murah, kualitas draft.' },
]
const TAKE_COUNTS = [
  { id: 1, label: '1 take — hemat' },
  { id: 2, label: '2 take — A/B bandingkan' },
]
const TAKE_MODES = [
  { id: 'final', label: 'final — kualitas penuh' },
  { id: 'draft', label: 'draft — iterasi cepat' },
  { id: 'both', label: 'both — draft lalu final' },
]
// Adaptasi Suno: penanda struktur di editor lirik + chip gaya instan.
const STRUCTURE_TAGS = ['[Intro]', '[Verse]', '[Pre-Chorus]', '[Chorus]', '[Bridge]', '[Outro]']
const STYLE_CHIPS = [
  'female vocal', 'male vocal', 'duet', 'acoustic', 'lo-fi', '80s synth',
  'orchestral', 'dangdut', 'keroncong', 'reggae', 'trap', 'ballad',
  'uplifting', 'melancholic', 'fast', 'slow', 'warm analog', 'dreamy reverb',
]
const NEGATIVE_CHIPS = ['male vocals', 'female vocals', 'rap', 'autotune', 'screaming', 'heavy metal', 'piano']
const RANDOM_IDEAS = [
  'lagu tentang pulang ke kampung halaman saat senja, kenangan masa kecil penuh kehangatan',
  'tentang seseorang yang menahan rindu lewat panggilan yang tak pernah tersambung',
  'cerita perjalanan naik kereta malam menuju kota baru, penuh harapan',
  'lagu pesta tentang teman lama yang berkumpul lagi setelah bertahun-tahun',
  'puisi tentang hujan di kota yang sibuk dan keheningan di dalam kamar',
  'kisah cinta yang tumbuh dari pesan singkat tengah malam',
  'lagu perjuangan wirausaha kecil yang tak pernah menyerah',
  'tentang melepas masa lalu di tepi pantai saat fajar',
  'lagu olahraga penyemangat untuk mengejar mimpi besar',
  'cerita ibu yang selalu menunggu kepulangan anaknya di depan pintu',
]
const MIN_PROMPT = 8
const MIN_LYRICS = 20
// Fallback poll only. Normal flow uses the realtime SSE stream, so this is just
// the safety net when streaming is unavailable.
const POLL_MS = 1500
// Estimasi jujur untuk progress bar. Basis + waktu per detik audio, dibedakan
// untuk worker cepat (draft ACE-Step/DiffRhythm) dan final (HeartMuLa) supaya bar
// tidak "menggantung" di satu angka sampai render benar-benar selesai.
const ESTIMATE_BASE_SEC = 45
const ESTIMATE_PER_AUDIO_SEC_FAST = 0.55
const ESTIMATE_PER_AUDIO_SEC_FINAL = 0.9
// Lantai progres per tahap riil backend: bar minimal maju saat tahap berganti,
// jadi tidak ada lagi bar yang diam sampai lagu selesai.
const STAGE_FLOOR = {
  writing: 8, generating: 18, mixing: 70, cover: 78,
  aligning: 84, mastering: 90, done: 100,
}
const GROUP_ORDER = ['Nusantara', 'Asia', 'Timur Tengah', 'Eropa & Global', 'Lainnya']
const STATUS_META = {
  queued: { label: 'Dalam antrean', className: 'border-warn/40 bg-warn/10 text-warn' },
  processing: { label: 'Menyusun lagu', className: 'border-cyan/40 bg-cyan/10 text-cyan-soft' },
  completed: { label: 'Selesai', className: 'border-signal/40 bg-signal/10 text-signal' },
  failed: { label: 'Gagal', className: 'border-danger/40 bg-danger/10 text-danger' },
}

// Estimasi durasi saat "Auto ikuti lirik": ~4.7 dtk per baris nyanyi + intro/outro.
const AUTO_SECONDS_PER_LINE = 4.7

// Grup accordion untuk form Create: hirarki jelas, bukan tumpukan dropdown.
const Group = ({ title, subtitle, defaultOpen = false, children }) => {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="rounded-xl border border-white/10 bg-black/20">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3.5 py-3 text-left">
        <span>
          <span className="block text-xs font-bold uppercase tracking-widest text-white/60">{title}</span>
          {subtitle && (
            <span className="mt-0.5 block text-[11px] font-normal normal-case tracking-normal text-white/40">{subtitle}</span>
          )}
        </span>
        <span className="shrink-0 text-white/40">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="space-y-4 border-t border-white/10 px-3.5 py-3.5">{children}</div>}
    </section>
  )
}

// A group may contain one successful take and one failed optional take. The
// player must show the usable completed result; the failed sibling remains
// visible in the take list with its worker error.
const preferredGroupJob = (items = []) => (
  items.find((item) => ['queued', 'processing'].includes(item.status))
    || items.find((item) => item.status === 'completed')
    || items.find((item) => item.status === 'failed')
    || items[0]
)

// Mirror of backend lyrics_writer.split_sections for the per-section editor.
const parseSections = (text) => {
  const sections = []
  let current = { header: '', lines: [] }
  for (const raw of String(text || '').replace(/\r\n?/g, '\n').split('\n')) {
    const t = raw.trim()
    if (t.startsWith('[') && t.endsWith(']') && t.length <= 40) {
      if (current.header || current.lines.length) sections.push(current)
      current = { header: t, lines: [] }
    } else if (t) {
      current.lines.push(raw)
    }
  }
  if (current.header || current.lines.length) sections.push(current)
  return sections
}


export default function Music({ user, authChecked = false, onSignIn }) {
  const [catalog, setCatalog] = useState({ genres: [], languages: [], providers: [] })
  const [engine, setEngine] = useState(null)
  const [engineBusy, setEngineBusy] = useState(false)
  const [catalogError, setCatalogError] = useState('')

  // Form kosong tanpa template: kreator mengisi ide/judul/lirik sendiri
  // atau memakai bantuan AI (saran judul, tulis & perbaiki lirik).
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
  const [lyrics, setLyrics] = useState('')
  const [genre, setGenre] = useState('pop-jawa')
  const [language, setLanguage] = useState('jv')
  const [languageSecondary, setLanguageSecondary] = useState('')
  const [languageMix, setLanguageMix] = useState('chorus')
  const [languageOverride, setLanguageOverride] = useState(false)
  const [vocalGender, setVocalGender] = useState('auto')
  // Engine, jumlah take, mode, seed, style tags, dan LoRA/regional adalah
  // kontrol lanjutan yang dipetakan langsung ke MusicGenerateRequest.
  const [provider, setProvider] = useState('heartmula')
  const [takeCount, setTakeCount] = useState(1)
  const [takeMode, setTakeMode] = useState('final')
  const [seed, setSeed] = useState('')
  const [styleTags, setStyleTags] = useState('')
  const [negativeTags, setNegativeTags] = useState('')
  const [loraId, setLoraId] = useState('')
  // Suno Simple/Custom: Simple menyembunyikan kontrol lanjutan (lirik, persona,
  // dual language, BPM/key). Default Custom agar semua opsi tetap terlihat.
  const [simpleMode, setSimpleMode] = useState(false)
  // Suno Creative Sliders + Inspire playlist + editor section.
  const [weirdness, setWeirdness] = useState(50)
  const [styleInfluence, setStyleInfluence] = useState(50)
  const [sectionIndex, setSectionIndex] = useState(0)
  const [sectionInstruction, setSectionInstruction] = useState('')
  const [sectionBusy, setSectionBusy] = useState(false)
  const [sectionInfo, setSectionInfo] = useState('')
  const [inspireIds, setInspireIds] = useState([])
  const [inspireInfo, setInspireInfo] = useState('')
  // Intelligence layer: rencana aransemen per seksi + hook (tanpa biaya Modal).
  const [plan, setPlan] = useState(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [planAi, setPlanAi] = useState(false)
  const [planInfo, setPlanInfo] = useState('')
  // Studio project: snapshot form + rencana aransemen, dengan revisi.
  const [projects, setProjects] = useState([])
  const [projectsBusy, setProjectsBusy] = useState(false)
  const [currentProject, setCurrentProject] = useState(null)
  const [projectName, setProjectName] = useState('')
  const [projectInfo, setProjectInfo] = useState('')
  const [projectRevisions, setProjectRevisions] = useState([])
  // Draft lirik: workspace terpisah dari proyek, autosave saat mengetik.
  const [lyricDrafts, setLyricDrafts] = useState([])
  const [currentDraft, setCurrentDraft] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftInfo, setDraftInfo] = useState('')
  const [draftBusy, setDraftBusy] = useState(false)
  const draftSyncRef = useRef('')
  const [realignBusy, setRealignBusy] = useState(false)
  const [realignInfo, setRealignInfo] = useState('')
  // Satu studio: Buat lagu | Cover | Riwayat (daftar lagu tak lagi di bawah form).
  const [studioTab, setStudioTab] = useState(() => {
    if (typeof window === 'undefined') return 'create'
    const seg = window.location.hash.replace(/^#\/?/, '').split(/[?/]/)[0]
    return seg === 'cover' ? 'cover' : seg === 'history' ? 'history' : 'create'
  })
  const [duration, setDuration] = useState(180)
  const [autoDuration, setAutoDuration] = useState(false)
  const [genreQuery, setGenreQuery] = useState('')
  const [genreOpen, setGenreOpen] = useState(false)
  const [titleIdeas, setTitleIdeas] = useState([])
  const [titleBusy, setTitleBusy] = useState(false)

  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [historyList, setHistoryList] = useState([])
  const [historyBusy, setHistoryBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  // Suno-style A/B: 1 Create = 1 grup berisi 2 takes (draft + final).
  const [groupId, setGroupId] = useState('')
  const [takes, setTakes] = useState([])
  const [lyricsAiBusy, setLyricsAiBusy] = useState(false)
  const [lyricsAiInfo, setLyricsAiInfo] = useState('')
  const [extendBusy, setExtendBusy] = useState(false)
  const [stemsBusy, setStemsBusy] = useState(false)
  const [stemsInfo, setStemsInfo] = useState('')
  // Fitur Suno-parity: persona, instrumental, BPM/key, share, feed, biaya.
  const [personas, setPersonas] = useState([])
  const [personaId, setPersonaId] = useState('')
  const [voiceName, setVoiceName] = useState('')
  const [voiceGender, setVoiceGender] = useState('auto')
  const [voiceFile, setVoiceFile] = useState(null)
  const [voiceConsent, setVoiceConsent] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [voiceCapability, setVoiceCapability] = useState(null)
  const [voiceRecording, setVoiceRecording] = useState(false)
  const [voiceRecordingSeconds, setVoiceRecordingSeconds] = useState(0)
  const [voicePreviewUrl, setVoicePreviewUrl] = useState('')
  const [personaMode, setPersonaMode] = useState('choose')
  const [personaListenId, setPersonaListenId] = useState('')
  const [personaFeedback, setPersonaFeedback] = useState('')
  const [instrumental, setInstrumental] = useState(false)
  const [bpm, setBpm] = useState(0)
  const [musicKey, setMusicKey] = useState('')
  const [costEstimate, setCostEstimate] = useState(null)
  const [costSummary, setCostSummary] = useState(null)
  const [feed, setFeed] = useState([])
  const [historyQuery, setHistoryQuery] = useState('')
  const [coverSongBusy, setCoverSongBusy] = useState(false)
  const [remixUrl, setRemixUrl] = useState('')
  const [remixGenre, setRemixGenre] = useState('reggae')
  const [remixGender, setRemixGender] = useState('female')
  const [remixEngine, setRemixEngine] = useState('mulacover')
  const [remixPitch, setRemixPitch] = useState('auto')
  const [remixRestyle, setRemixRestyle] = useState(true)
  const [remixVoiceMode, setRemixVoiceMode] = useState('manual')
  const [remixPrompt, setRemixPrompt] = useState('')
  const [remixLyrics, setRemixLyrics] = useState('')
  const [remixStrength, setRemixStrength] = useState(0.6)
  const [remixRightsConfirmed, setRemixRightsConfirmed] = useState(false)
  const [remixBusy, setRemixBusy] = useState(false)
  const [shareBusy, setShareBusy] = useState(false)

  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [songDuration, setSongDuration] = useState(0)
  const [coverBusy, setCoverBusy] = useState(false)
  const [coverUrl, setCoverUrl] = useState('')
  const [coverBust, setCoverBust] = useState(0)

  const audioRef = useRef(null)
  const identityPersona = personas.find(p => p.persona_id === personaId && p.has_voice_sample)
  const effectiveGender = identityPersona?.vocal_gender || vocalGender
  const voiceRecorderRef = useRef(null)
  const voiceStreamRef = useRef(null)
  const voiceChunksRef = useRef([])
  const activeLineRef = useRef(null)
  const startedAt = useRef(0)
  const promptInputRef = useRef(null)
  const titleInputRef = useRef(null)
  const lyricsRef = useRef(null)

  const genreInfo = useMemo(
    () => (catalog.genres || []).find((item) => item.id === genre) || null,
    [catalog.genres, genre],
  )
  const languageLocked = Boolean(genreInfo?.language_locked) && !languageOverride
  const languageLabel = useMemo(() => {
    const found = (catalog.languages || []).find((item) => item.id === language)
    return found?.label || language || '—'
  }, [catalog.languages, language])
  const languageSecondaryLabel = useMemo(() => {
    const found = (catalog.languages || []).find((item) => item.id === languageSecondary)
    return found?.label || languageSecondary || '—'
  }, [catalog.languages, languageSecondary])
  const bilingual = Boolean(languageSecondary && languageMix !== 'none')
  const running = Boolean(job && ['queued', 'processing'].includes(job.status))
  const working = busy || running || takes.some((t) => ['queued', 'processing'].includes(t.status))
  const jobError = job?.status === 'failed'
    ? (job.error || 'Job gagal tanpa pesan dari worker.')
    : ''

  // Validasi sisi klien: setiap pesan menyebut bagian yang harus diisi, dan
  // aturan yang sama ditegakkan lagi di backend (POST /api/music/generate).
  const issues = useMemo(() => {
    const found = {}
    if (!title.trim()) found.title = 'Judul lagu wajib diisi (dipakai untuk nama file & caption).'
    const promptOk = prompt.trim().length >= MIN_PROMPT
    const lyricsOk = lyrics.trim().length >= MIN_LYRICS
    if (!promptOk && !lyricsOk) {
      found.idea = `Isi salah satu: Ide lagu minimal ${MIN_PROMPT} karakter, atau Lirik minimal ${MIN_LYRICS} karakter.`
    }
    if (!(duration >= 15 && duration <= 600)) found.duration = 'Durasi harus 15–600 detik.'
    return found
  }, [title, prompt, lyrics, duration])
  const missing = useMemo(() => Object.values(issues), [issues])
  // Error merah hanya tampil setelah user mencoba Create / mengisi field —
  // form kosong saat dibuka tidak langsung "dimarahi".
  const [submitted, setSubmitted] = useState(false)
  const showErrors = submitted || title.trim() || prompt.trim() || lyrics.trim()
  const vis = (key) => (showErrors ? issues[key] : null)


  const languageGroups = useMemo(() => {
    const groups = new Map()
    for (const item of catalog.languages || []) {
      if (!groups.has(item.group)) groups.set(item.group, [])
      groups.get(item.group).push(item)
    }
    const rank = (name) => (GROUP_ORDER.includes(name) ? GROUP_ORDER.indexOf(name) : 99)
    return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b))
  }, [catalog.languages])

  const loadCatalog = useCallback(async (probe) => {
    setEngineBusy(true)
    try {
      const data = await getMusicGenres(Boolean(probe))
      setCatalog({
        genres: data.genres || [],
        languages: data.languages || [],
        providers: data.providers || [],
      })
      setEngine(data.engine || null)
      setCatalogError('')
    } catch (e) {
      setCatalogError(`Gagal memuat katalog musik: ${e.message}`)
    } finally {
      setEngineBusy(false)
    }
  }, [])

  const refreshEngine = useCallback(async () => {
    setEngineBusy(true)
    try {
      setEngine(await getMusicEngine())
      setCatalogError('')
    } catch (e) {
      setEngine({ ready: false, message: e.message })
    } finally {
      setEngineBusy(false)
    }
  }, [])

  const loadHistory = useCallback(async (query = '') => {
    if (!user) return
    setHistoryBusy(true)
    try {
      const data = await getMusicJobs(query)
      const jobs = data.jobs || []
      setHistoryList(jobs)

      // Resume ONLY an in-flight server job after a refresh/reopen so the form
      // does not look idle while the backend is still working. A finished
      // song is intentionally NOT auto-opened into the player: the last result
      // used to stick around forever as a permanent "Now creating" card, which
      // is confusing. Completed takes stay in Riwayat and open on click.
      if (!(query || '').trim()) {
        const active = jobs.find((item) => ['queued', 'processing'].includes(item.status))
        if (active) {
          setJob(active)
          setGroupId(active.group_id || active.metadata?.group_id || '')
          setTakes([active])
          setBusy(true)
          const started = Number(active.created_at || active.updated_at || 0) * 1000
          startedAt.current = started > 0 ? started : Date.now()
        }
      }
    } catch {
      // Riwayat hanya pelengkap — jangan memblokir panel karena ini.
    } finally {
      setHistoryBusy(false)
    }
  }, [user])

  const loadPersonas = useCallback(async () => {
    if (!user) return
    try {
      const data = await getMusicPersonas()
      setPersonas(data.personas || [])
      setVoiceCapability(data.voice_conversion || null)
    } catch {
      // Persona opsional — panel tetap jalan tanpanya.
    }
  }, [user])

  const loadCost = useCallback(async () => {
    if (!user) return
    try {
      setCostSummary(await getMusicCost())
    } catch {
      // Metering informatif saja.
    }
  }, [user])

  useEffect(() => { loadCatalog(true) }, [loadCatalog])
  useEffect(() => { loadHistory() }, [loadHistory])
  useEffect(() => {
    if (!user || busy || groupId) return undefined
    const id = setInterval(() => { loadHistory() }, 5000)
    return () => clearInterval(id)
  }, [user, busy, groupId, loadHistory])
  useEffect(() => { loadPersonas() }, [loadPersonas])
  useEffect(() => { loadCost() }, [loadCost])
  useEffect(() => {
    if (!voiceRecording) return undefined
    if (voiceRecordingSeconds >= 29) {
      voiceRecorderRef.current?.stop()
      return undefined
    }
    const timer = window.setTimeout(() => setVoiceRecordingSeconds((n) => n + 1), 1000)
    return () => window.clearTimeout(timer)
  }, [voiceRecording, voiceRecordingSeconds])
  useEffect(() => () => {
    const recorder = voiceRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null
      recorder.stop()
    }
    voiceStreamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])
  useEffect(() => {
    getMusicFeed(10).then((d) => setFeed(d.songs || [])).catch(() => {})
  }, [])

  // Genre daerah mengunci bahasa vokal (Pop Jawa → Jawa) selama override mati.
  useEffect(() => {
    if (genreInfo?.language_locked && !languageOverride && genreInfo.language) {
      setLanguage(genreInfo.language)
    }
  }, [genreInfo, languageOverride])
  useEffect(() => {
    if (languageSecondary === language) {
      const fallback = (catalog.languages || []).find((item) => item.id !== language)
      setLanguageSecondary(fallback?.id || '')
    }
  }, [language, languageSecondary, catalog.languages])

  const groupRunning = takes.some((t) => ['queued', 'processing'].includes(t.status))
  // Realtime monitor untuk grup A/B. Kita mencoba Server-Sent Events dulu:
  // backend mendorong setiap perubahan status/stage, jadi panel berhenti
  // "loading" tepat saat Modal selesai (tidak menunggu interval poll). Bila
  // stream tidak bisa dibuka (proxy/browser), otomatis jatuh ke polling.
  useEffect(() => {
    if (!groupId) return undefined
    let stopped = false
    let timer = null
    let controller = null

    const applyTakes = (next) => {
      setTakes(next)
      const preferred = preferredGroupJob(next)
      if (preferred) setJob(preferred)
      if (next.length && next.every((t) => ['completed', 'failed'].includes(t.status))) {
        setBusy(false)
        loadHistory()
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
        if (!stopped) setRequestError(`Gagal memantau grup ${groupId}: ${e.message}`)
      }
      if (!stopped) timer = setTimeout(poll, POLL_MS)
    }

    const run = async () => {
      let ended = false
      try {
        controller = typeof AbortController !== 'undefined' ? new AbortController() : null
        setRequestError('')
        await streamMusicGroup(groupId, {
          signal: controller?.signal,
          onGroup: (g) => {
            if (stopped) return
            if (applyTakes(g.takes || [])) ended = true
          },
          onEnd: () => { ended = true },
        })
        if (stopped || ended) return
      } catch {
        if (stopped || controller?.signal.aborted) return
        // Streaming tidak tersedia — lanjut lewat polling biasa.
      }
      if (!stopped) poll()
    }

    run()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      controller?.abort()
    }
  }, [groupId, loadHistory])
  // Fallback single-job (riwayat lama tanpa group_id).
  useEffect(() => {
    if (groupId || !job?.job_id || !running) return undefined
    const timer = setTimeout(() => {
      getMusicStatus(job.job_id)
        .then((next) => {
          setJob(next)
          if (next.status === 'completed' || next.status === 'failed') {
            setBusy(false)
            loadHistory()
          }
        })
        .catch((e) => {
          setRequestError(`Gagal memantau job ${job.job_id}: ${e.message}`)
          setJob((prev) => (prev ? { ...prev } : prev))
        })
    }, POLL_MS)
    return () => clearTimeout(timer)
  }, [job, running, groupId, loadHistory])

  useEffect(() => {
    if (!running) return undefined
    const timer = setInterval(() => {
      setElapsed(startedAt.current
        ? Math.max(0, Math.round((Date.now() - startedAt.current) / 1000)) : 0)
    }, 1000)
    return () => clearInterval(timer)
  }, [running])

  // Estimasi total render mengikuti durasi lagu yang diminta (dan provider +
  // jumlah take) supaya progress tidak mentok di 96% lalu diam sampai selesai.
  const estimateSec = useMemo(() => {
    const requested = Number(job?.duration) || Number(duration) || 180
    const name = String(job?.provider || provider || '').toLowerCase()
    const isFinal = name.includes('heartmula')
    const perSec = isFinal ? ESTIMATE_PER_AUDIO_SEC_FINAL : ESTIMATE_PER_AUDIO_SEC_FAST
    const takes = Math.max(1, Math.min(Number(takeCount) || 1, 2))
    return Math.max(60, ESTIMATE_BASE_SEC + requested * perSec + (takes - 1) * 30)
  }, [job, duration, provider, takeCount])

  // Progress + tahap: estimasi ikut durasi yang dipilih, lantai tahap menjaga
  // bar tetap bergerak, dan sengaja disisakan ruang sampai job benar-benar done.
  const progress = useMemo(() => {
    if (!job) return 0
    if (job.status === 'completed' || job.status === 'failed') return 100
    if (job.status === 'queued') return 6
    // Kurva asimtotik: bar terus merayap (tidak pernah beku) tapi melambat
    // mendekati akhir, sehingga tidak "mentok" bila render lebih lama dari ETA.
    const eased = 1 - Math.exp(-elapsed / estimateSec)
    const elapsedPct = 12 + Math.round(eased * 84)
    const floor = STAGE_FLOOR[job.metadata?.stage || ''] || 0
    return Math.min(97, Math.max(elapsedPct, floor))
  }, [job, elapsed, estimateSec])

  const stageText = useMemo(() => {
    if (!job) return ''
    // Stage riil dari backend (draft-render/final-render/done + tahap lokal
    // writing/generating/mixing/cover/aligning/mastering) — dipakai agar panel
    // memperlihatkan progres nyata, bukan sekadar "loading" yang beku.
    const stage = job.metadata?.stage || ''
    if (job.status === 'completed') {
      const al = job.metadata?.alignment === 'whisper' ? ' · karaoke presisi' : ''
      return `Lagu siap diputar.${al}`
    }
    if (job.status === 'queued') return 'Menunggu slot worker GPU…'
    if (stage === 'writing') return '✍️ Menyusun lirik & aransemen…'
    if (stage === 'generating') {
      if (job.provider?.includes('diffrhythm')) return '⚡ DiffRhythm menyusun lagu penuh…'
      if (job.take_mode === 'draft' || job.provider?.includes('ace-step')) return '⚡ Draft ACE-Step berjalan…'
      return '🎤 Worker Music L4 menyusun vokal…'
    }
    if (stage === 'mixing') return '🎚️ Menggabungkan vokal & musik…'
    if (stage === 'cover') return '🖼️ Menyiapkan cover art…'
    if (stage === 'aligning') return '🎤 Menyelaraskan lirik (Whisper)…'
    if (stage === 'mastering') return '🔊 Mastering audio (LUFS + WAV)…'
    if (stage === 'stale') return '⏱️ Worker tidak merespons — job dihentikan.'
    if (stage === 'draft-render') return '🎼 Menyusun draft…'
    if (stage === 'final-render') return '🎼 Menyusun lagu final…'
    if (job.status === 'processing') return 'Menghubungkan ke worker Music L4…'
    return stage || ''
  }, [job, elapsed])

  const providerOptions = useMemo(() => {
    const byId = new Map((catalog.providers || []).map((p) => [p.id, p]))
    return PROVIDER_OPTIONS.map((opt) => {
      const available = opt.id === 'diffrhythm'
        ? engine?.diffrhythm_modal_enabled !== false
        : opt.id === 'auto'
          ? engine?.ready !== false
          : opt.id === 'ace-step'
            ? Boolean(engine?.ace_step || engine?.ace_modal_enabled)
            : Boolean(engine?.modal_enabled || engine?.heartmula_local)
      return { ...opt, label: byId.get(opt.id)?.label || opt.label, available }
    })
  }, [catalog.providers, engine])

  const onProviderChange = (value) => {
    setProvider(value)
    if (value === 'auto') { setTakeCount(2); setTakeMode('both') }
    else if (value === 'ace-step') { setTakeCount(1); setTakeMode('draft') }
    else { setTakeCount(1); setTakeMode('final') }
  }

  // Adaptasi Suno: sisipkan penanda struktur di posisi kursor lirik.
  const insertStructureTag = (tag) => {
    const el = lyricsRef.current
    const text = lyrics
    const pos = el && typeof el.selectionStart === 'number' ? el.selectionStart : text.length
    const before = text.slice(0, pos)
    const after = text.slice(pos)
    const prefix = before && !before.endsWith('\n') ? '\n' : ''
    const insert = `${prefix}${tag}\n`
    setLyrics(before + insert + after)
    setRequestError('')
    requestAnimationFrame(() => {
      if (!el) return
      const caret = (before + insert).length
      el.focus()
      el.setSelectionRange(caret, caret)
    })
  }

  // Suno "Roll for a random prompt": pilih ide acak dari daftar kurasi.
  const rollIdea = () => {
    const pick = RANDOM_IDEAS[Math.floor(Math.random() * RANDOM_IDEAS.length)]
    setPrompt(pick)
    setTitleIdeas([])
    setRequestError('')
  }

  // Suno "Use Random Style": isi style tags dari chip gaya yang tersedia.
  const rollStyle = () => {
    const shuffled = [...STYLE_CHIPS].sort(() => Math.random() - 0.5)
    setStyleTags(shuffled.slice(0, 3).join(', '))
  }

  const toggleStyleChip = (chip) => {
    setStyleTags((prev) => {
      const items = prev.split(',').map((s) => s.trim()).filter(Boolean)
      const exists = items.some((s) => s.toLowerCase() === chip.toLowerCase())
      const next = exists ? items.filter((s) => s.toLowerCase() !== chip.toLowerCase()) : [...items, chip]
      return next.join(', ')
    })
  }

  const toggleNegativeChip = (chip) => {
    setNegativeTags((prev) => {
      const items = prev.split(',').map((s) => s.trim()).filter(Boolean)
      const exists = items.some((s) => s.toLowerCase() === chip.toLowerCase())
      const next = exists ? items.filter((s) => s.toLowerCase() !== chip.toLowerCase()) : [...items, chip]
      return next.join(', ')
    })
  }

  // Suno "My Taste": tarik gaya dari riwayat lagu yang sudah selesai.
  const styleFromHistory = () => {
    const seen = []
    const push = (value) => {
      const v = String(value || '').trim()
      if (v && !seen.some((s) => s.toLowerCase() === v.toLowerCase())) seen.push(v)
    }
    ;(historyList || []).forEach((item) => {
      push(item.genre_label || item.genre)
      String(item.style_tags || '').split(',').forEach(push)
    })
    const picked = (seen.length ? seen : STYLE_CHIPS).slice(0, 5)
    setStyleTags(picked.join(', '))
  }

  // Suno "section editing": rewrite ONE section with an instruction, keeping
  // every other section intact (backend preserves the section line count).
  const lyricSections = useMemo(() => parseSections(lyrics), [lyrics])

  const rewriteSection = async () => {
    if (!user) { onSignIn(); return }
    if (sectionBusy) return
    if (!lyricSections.length) { setRequestError('Belum ada bagian lirik untuk diedit.'); return }
    if (sectionInstruction.trim().length < 3) {
      setRequestError('Tulis instruksi perubahan, mis. "jadikan chorus lebih megah".')
      return
    }
    setSectionBusy(true)
    setRequestError('')
    setSectionInfo('')
    try {
      const res = await composeMusicLyrics({
        title: title.trim(), prompt: prompt.trim(), lyrics,
        genre, language, language_secondary: languageSecondary,
        language_mix: languageMix, language_override: languageOverride,
        vocal_gender: vocalGender, duration,
        mode: 'section', section_index: sectionIndex, instruction: sectionInstruction.trim(),
      })
      if (res?.lyrics) {
        setLyrics(res.lyrics)
        setSectionInfo(`✏️ Bagian [${res.section}] ditulis ulang (${res.lines} baris). Bagian lain tetap.`)
        setSectionInstruction('')
      }
    } catch (e) {
      setRequestError(`Edit bagian gagal: ${e.message}`)
    } finally {
      setSectionBusy(false)
    }
  }

  // Rencana aransemen (Intelligence layer): seksi, energi, hook, style tags.
  // Deterministik & murah; toggle "Saran AI" menambah style + varian hook.
  const loadPlan = async () => {
    if (planBusy) return
    setPlanBusy(true)
    setPlanInfo('')
    try {
      const res = await planMusicSong({
        title: title.trim(), prompt: prompt.trim(),
        lyrics: instrumental ? '' : lyrics,
        genre, language, language_secondary: languageSecondary,
        language_mix: languageMix, language_override: languageOverride,
        vocal_gender: vocalGender, instrumental, duration: Number(duration) || 180,
        bpm: Number(bpm) || 0, style_tags: styleTags.trim(), ai: planAi,
      })
      setPlan(res)
      setPlanInfo(`🧠 ${res?.sections?.length || 0} seksi · hook: ${res?.hook?.line || '—'}`)
      if (res?.style_tags && !styleTags.trim()) setStyleTags(res.style_tags)
    } catch (e) {
      setPlanInfo(`Gagal menyusun rencana: ${e.message}`)
    } finally {
      setPlanBusy(false)
    }
  }

  // ── Studio project (versi/revisi) ────────────────────────────────────
  const buildProjectSnapshot = () => ({
    version: 1,
    title, prompt, lyrics, genre, language,
    language_secondary: languageSecondary,
    language_mix: languageMix,
    language_override: languageOverride,
    vocal_gender: vocalGender,
    provider,
    duration,
    auto_duration: autoDuration,
    take_count: takeCount,
    take_mode: takeMode,
    seed,
    style_tags: styleTags,
    negative_tags: negativeTags,
    lora_id: loraId,
    instrumental,
    bpm,
    music_key: musicKey,
    persona_id: personaId,
    weirdness,
    style_influence: styleInfluence,
    plan,
    job_id: job?.job_id || '',
    group_id: groupId || '',
  })

  const applyProjectSnapshot = (snap) => {
    if (!snap || typeof snap !== 'object') return
    const setText = (value, setter) => {
      if (typeof value === 'string') setter(value)
    }
    setText(snap.title, setTitle)
    setText(snap.prompt, setPrompt)
    setText(snap.lyrics, setLyrics)
    setText(snap.genre, setGenre)
    setText(snap.language, setLanguage)
    setText(snap.language_secondary, setLanguageSecondary)
    setText(snap.language_mix, setLanguageMix)
    setText(snap.vocal_gender, setVocalGender)
    setText(snap.provider, setProvider)
    setText(snap.take_mode, setTakeMode)
    setText(snap.seed, setSeed)
    setText(snap.style_tags, setStyleTags)
    setText(snap.negative_tags, setNegativeTags)
    setText(snap.lora_id, setLoraId)
    setText(snap.music_key, setMusicKey)
    setText(snap.persona_id, setPersonaId)
    if (typeof snap.duration === 'number') setDuration(snap.duration)
    if (typeof snap.take_count === 'number') setTakeCount(snap.take_count)
    if (typeof snap.bpm === 'number') setBpm(snap.bpm)
    if (typeof snap.weirdness === 'number') setWeirdness(snap.weirdness)
    if (typeof snap.style_influence === 'number') setStyleInfluence(snap.style_influence)
    setAutoDuration(Boolean(snap.auto_duration))
    setInstrumental(Boolean(snap.instrumental))
    setLanguageOverride(Boolean(snap.language_override))
    setPlan(snap.plan && typeof snap.plan === 'object' ? snap.plan : null)
  }

  const loadProjects = useCallback(async () => {
    if (!user) return
    try {
      const data = await listMusicProjects()
      setProjects(data.projects || [])
    } catch {
      // Proyek opsional - panel tetap jalan tanpanya.
    }
  }, [user])

  useEffect(() => { loadProjects() }, [loadProjects])

  const saveProject = async () => {
    if (!user) { onSignIn(); return }
    if (projectsBusy) return
    setProjectsBusy(true)
    setProjectInfo('')
    try {
      const snapshot = buildProjectSnapshot()
      if (currentProject?.project_id) {
        const revision = await addMusicProjectRevision(currentProject.project_id, {
          snapshot,
          label: projectName.trim(),
          source_job_id: job?.job_id || '',
        })
        const refreshed = await getMusicProject(currentProject.project_id)
        setCurrentProject(refreshed)
        setProjectRevisions(refreshed.revisions || [])
        setProjectInfo(`Revisi ${revision.revision_index} tersimpan.`)
      } else {
        const created = await createMusicProject({
          name: projectName.trim(),
          snapshot,
          job_id: job?.job_id || '',
          group_id: groupId || '',
        })
        setCurrentProject(created)
        setProjectRevisions(created.revisions || [])
        setProjectName(created.name || '')
        setProjectInfo(`Proyek "${created.name}" dibuat (revisi 1).`)
      }
      await loadProjects()
    } catch (e) {
      setProjectInfo(`Gagal menyimpan proyek: ${e.message}`)
    } finally {
      setProjectsBusy(false)
    }
  }

  const openProject = async (entry) => {
    if (!entry?.project_id) return
    setProjectInfo('')
    try {
      const project = await getMusicProject(entry.project_id)
      applyProjectSnapshot(project.snapshot)
      setCurrentProject(project)
      setProjectRevisions(project.revisions || [])
      setProjectName(project.name || '')
      setProjectInfo(`Proyek "${project.name}" dimuat (${project.revision_count} revisi).`)
    } catch (e) {
      setProjectInfo(`Gagal membuka proyek: ${e.message}`)
    }
  }

  const loadProjectRevision = async (revision) => {
    if (!currentProject?.project_id || !revision?.revision_id) return
    try {
      const rev = await getMusicProjectRevision(
        currentProject.project_id, revision.revision_id)
      applyProjectSnapshot(rev.snapshot)
      setProjectInfo(`Revisi ${rev.revision_index} dimuat. Tekan "Simpan revisi" untuk menjadikannya versi terbaru.`)
    } catch (e) {
      setProjectInfo(`Gagal membuka revisi: ${e.message}`)
    }
  }

  const restoreProjectRevision = async (revision) => {
    if (!currentProject?.project_id || !revision?.revision_id) return
    setProjectsBusy(true)
    try {
      const rev = await restoreMusicProjectRevision(
        currentProject.project_id, revision.revision_id)
      const refreshed = await getMusicProject(currentProject.project_id)
      applyProjectSnapshot(rev.snapshot)
      setCurrentProject(refreshed)
      setProjectRevisions(refreshed.revisions || [])
      setProjectInfo(`Revisi ${rev.revision_index} dibuat dari restore revisi ${revision.revision_index}.`)
      await loadProjects()
    } catch (e) {
      setProjectInfo(`Gagal restore revisi: ${e.message}`)
    } finally {
      setProjectsBusy(false)
    }
  }

  const removeProject = async () => {
    if (!currentProject?.project_id) return
    setProjectsBusy(true)
    try {
      await deleteMusicProject(currentProject.project_id)
      setCurrentProject(null)
      setProjectRevisions([])
      setProjectInfo('Proyek dihapus.')
      await loadProjects()
    } catch (e) {
      setProjectInfo(`Gagal menghapus proyek: ${e.message}`)
    } finally {
      setProjectsBusy(false)
    }
  }

  const startNewProject = () => {
    setCurrentProject(null)
    setProjectRevisions([])
    setProjectName('')
    setProjectInfo('')
  }

  // ── Draft lirik (autosave) ───────────────────────────────────────────
  const loadDrafts = useCallback(async () => {
    if (!user) return
    try {
      const data = await listMusicLyricsDrafts()
      setLyricDrafts(data.drafts || [])
    } catch {
      // Draft opsional - editor lirik tetap jalan tanpanya.
    }
  }, [user])

  useEffect(() => { loadDrafts() }, [loadDrafts])

  const openLyricDraft = async (entry) => {
    if (!entry?.draft_id) return
    setDraftInfo('')
    try {
      const draft = await getMusicLyricsDraft(entry.draft_id)
      setCurrentDraft(draft)
      setDraftTitle(draft.title || '')
      setLyrics(draft.lyrics || '')
      setDraftSyncRef.current = `${draft.title || ''}\n${draft.lyrics || ''}`
      setDraftInfo('Draft dimuat.')
    } catch (e) {
      setDraftInfo(`Gagal membuka draft: ${e.message}`)
    }
  }

  const saveLyricDraft = async () => {
    if (!user) { onSignIn(); return }
    if (draftBusy) return
    setDraftBusy(true)
    const payload = {
      title: draftTitle.trim(),
      lyrics,
      notes: currentDraft?.notes || '',
      genre,
      language,
    }
    try {
      const isExisting = Boolean(currentDraft?.draft_id)
      const draft = isExisting
        ? await updateMusicLyricsDraft(currentDraft.draft_id, payload)
        : await createMusicLyricsDraft(payload)
      setCurrentDraft(draft)
      setDraftTitle(draft.title || '')
      setDraftSyncRef.current = `${draft.title || ''}\n${draft.lyrics || ''}`
      setDraftInfo(isExisting ? 'Draft tersimpan.' : 'Draft baru dibuat.')
      await loadDrafts()
    } catch (e) {
      setDraftInfo(`Gagal menyimpan draft: ${e.message}`)
    } finally {
      setDraftBusy(false)
    }
  }

  const removeLyricDraft = async () => {
    if (!currentDraft?.draft_id) return
    setDraftBusy(true)
    try {
      await deleteMusicLyricsDraft(currentDraft.draft_id)
      setCurrentDraft(null)
      setDraftTitle('')
      setDraftSyncRef.current = ''
      setDraftInfo('Draft dihapus.')
      await loadDrafts()
    } catch (e) {
      setDraftInfo(`Gagal menghapus draft: ${e.message}`)
    } finally {
      setDraftBusy(false)
    }
  }

  const newLyricDraft = () => {
    setCurrentDraft(null)
    setDraftTitle('')
    setDraftSyncRef.current = ''
    setDraftInfo('Draft baru - mengetik akan menyimpan otomatis setelah draft dibuat.')
  }

  useEffect(() => {
    if (!user || !currentDraft?.draft_id) return
    const signature = `${draftTitle}\n${lyrics}`
    if (signature === draftSyncRef.current) return
    const timer = setTimeout(async () => {
      try {
        const draft = await updateMusicLyricsDraft(currentDraft.draft_id, {
          title: draftTitle.trim(),
          lyrics,
          notes: currentDraft.notes || '',
          genre,
          language,
        })
        draftSyncRef.current = `${draft.title || ''}\n${draft.lyrics || ''}`
        setDraftInfo('Tersimpan otomatis.')
        setCurrentDraft((prev) => (prev ? { ...prev, ...draft } : prev))
        await loadDrafts()
      } catch (e) {
        setDraftInfo(`Autosave gagal: ${e.message}`)
      }
    }, 1500)
    return () => clearTimeout(timer)
  }, [draftTitle, lyrics, genre, language, currentDraft, user, loadDrafts])

  // Suno "Inspire": gabungkan beberapa lagu acuan jadi satu arahan gaya.
  const toggleInspire = (jobId) => {
    setInspireInfo('')
    setInspireIds((prev) => (prev.includes(jobId) ? prev.filter((id) => id !== jobId) : [...prev, jobId]))
  }

  const applyInspire = () => {
    const chosen = (historyList || []).filter((item) => inspireIds.includes(item.job_id))
    if (!chosen.length) { setInspireInfo('Pilih minimal satu lagu acuan.'); return }
    const styles = []
    const genres = {}
    const bpms = []
    const keys = {}
    chosen.forEach((item) => {
      String(item.style_tags || '').split(',').map((s) => s.trim()).filter(Boolean).forEach((s) => {
        if (!styles.some((x) => x.toLowerCase() === s.toLowerCase())) styles.push(s)
      })
      const g = item.genre_label || item.genre
      if (g) genres[g] = (genres[g] || 0) + 1
      if (item.metadata?.bpm_detected) bpms.push(Number(item.metadata.bpm_detected))
      const k = item.metadata?.key_detected || item.music_key
      if (k) keys[k] = (keys[k] || 0) + 1
    })
    if (styles.length) setStyleTags(styles.slice(0, 6).join(', '))
    const topGenre = Object.entries(genres).sort((a, b) => b[1] - a[1])[0]?.[0]
    if (topGenre) {
      const match = (catalog.genres || []).find((g) => (g.label || g.id) === topGenre || g.id === topGenre)
      if (match?.id) setGenre(match.id)
    }
    const validBpms = bpms.filter((n) => n >= 40 && n <= 220)
    if (validBpms.length) setBpm(Math.round(validBpms.reduce((a, b) => a + b, 0) / validBpms.length))
    const topKey = Object.entries(keys).sort((a, b) => b[1] - a[1])[0]?.[0]
    if (topKey) setMusicKey(String(topKey))
    const names = chosen.map((c) => c.title).filter(Boolean).slice(0, 3).join(', ')
    setPrompt((prev) => prev.trim() || `lagu baru terinspirasi oleh ${names}`)
    setInspireInfo(`✨ ${chosen.length} lagu acuan diterapkan${topGenre ? ` · genre ${topGenre}` : ''}${validBpms.length ? ` · ${Math.round(validBpms.reduce((a, b) => a + b, 0) / validBpms.length)} BPM` : ''}.`)
  }

  const create = async () => {
    if (!user) { onSignIn(); return }
    if (working) return
    setSubmitted(true)
    if (missing.length) {
      setRequestError(`Lengkapi dulu sebelum membuat lagu: ${missing.join(' ')}`)
      const target = !prompt.trim() && !lyrics.trim()
        ? promptInputRef.current
        : !title.trim() ? titleInputRef.current : null
      target?.focus()
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    if (engineReady === null) {
      setRequestError('Kesiapan worker belum diketahui. Tunggu sebentar lalu coba lagi.')
      return
    }
    if (engineReady === false) {
      setRequestError(engine?.message || 'Engine lagu belum siap. Tekan “Cek worker” sebelum mencoba lagi.')
      return
    }
    setRequestError('')
    const selectedPersona = personas.find((persona) => persona.persona_id === personaId)
    if (selectedPersona?.has_voice_sample && !selectedPersona.voice_conversion_ready) {
      setRequestError(voiceCapability?.message || 'Persona suara belum siap di server. Lagu belum dibuat dan kredit tidak terpakai.')
      return
    }
    setJob(null)
    setTakes([])
    setGroupId('')
    setStemsInfo('')
    setBusy(true)
    setElapsed(0)
    startedAt.current = Date.now()
    try {
       // Engine + take controls diteruskan apa adanya; backend memvalidasi dan
       // memetakan ke worker yang tersedia. Seed kosong = acak di server.
       const seedValue = seed.trim() === '' ? null : Number(seed)
       const next = await generateMusic({
        title: title.trim(),
        prompt: prompt.trim(),
        lyrics: instrumental ? '' : lyrics,
        genre,
        language,
        language_secondary: bilingual ? languageSecondary : '',
        language_mix: bilingual ? languageMix : 'none',
        language_override: languageOverride,
        vocal_gender: effectiveGender,
        provider,
        duration,
        auto_duration: autoDuration,
        full_song: true,
        take_count: Math.max(1, Math.min(Number(takeCount) || 1, 2)),
        take_mode: TAKE_MODES.some((m) => m.id === takeMode) ? takeMode : 'final',
        seed: Number.isFinite(seedValue) ? seedValue : null,
        style_tags: styleTags.trim(),
        negative_tags: negativeTags.trim(),
        weirdness: Number(weirdness),
        style_influence: Number(styleInfluence),
        lora_id: loraId.trim(),
        instrumental,
        bpm: Number(bpm) || 0,
        music_key: musicKey.trim(),
        persona_id: personaId,
      })
      if (next?.cost_estimate) setCostEstimate(next.cost_estimate)
      if (next?.language) setLanguage(next.language)
      if (next?.cached && next.jobs?.length) {
        setTakes(next.jobs)
        setGroupId(next.group_id || '')
        setJob(next.jobs.find((t) => t.status === 'completed') || next.jobs[0])
        setBusy(false)
        loadHistory()
        return
      }
      setGroupId(next.group_id || '')
      const initial = (next.jobs || []).map((t) => ({
        ...t, title: title.trim(), genre, vocal_gender: vocalGender,
        metadata: { stage: t.take_mode === 'draft' ? 'draft-render' : 'final-render', lyrics_timeline: [] },
      }))
      setTakes(initial)
      setJob({
        ...(initial[0] || next),
        title: title.trim(),
        genre,
        vocal_gender: vocalGender,
        metadata: initial[0]?.metadata || { lyrics_timeline: [] },
      })
    } catch (e) {
      setBusy(false)
      setRequestError(e.message)
    }
  }

  // Baris nyanyi (tanpa header [Section]) vs target durasi — panduan presisi.
  const { lyricSungCount, lyricTarget } = useMemo(() => {
    const sung = lyrics.split('\n').filter((ln) => {
      const t = ln.trim()
      return t && !(t.startsWith('[') && t.endsWith(']'))
    }).length
    const target = Math.max(6, Math.min(140, Math.round(duration / 4.7)))
    return { lyricSungCount: sung, lyricTarget: target }
  }, [lyrics, duration])

  const writeLyricsAi = async (mode) => {
    if (!user) { onSignIn(); return }
    if (lyricsAiBusy) return
    setLyricsAiBusy(true)
    setLyricsAiInfo('')
    try {
      const res = await composeMusicLyrics({
        title: title.trim(), prompt: prompt.trim(), lyrics,
        genre, language, language_secondary: languageSecondary,
        language_mix: languageMix, language_override: languageOverride,
        vocal_gender: vocalGender, duration, mode,
      })
      if (res?.lyrics) {
        setLyrics(res.lyrics)
        const label = mode === 'refine' ? '🪄 Refined'
          : mode === 'expand' ? '⤢ Expanded'
          : res.source === 'offline' ? 'Draft offline' : '✨ Lirik AI'
        setLyricsAiInfo(`${label}: ${res.lines}/${res.target || res.target_lines} baris untuk ${Math.round(duration)} dtk`)
      }
    } catch (e) {
      setRequestError(`Lirik AI gagal: ${e.message}`)
    } finally {
      setLyricsAiBusy(false)
    }
  }

  const suggestTitles = async () => {
    if (!user) { onSignIn(); return }
    if (titleBusy || (!prompt.trim() && !lyrics.trim())) return
    setTitleBusy(true)
    try {
      const res = await composeMusicLyrics({
        title: title.trim(), prompt: prompt.trim(), lyrics,
        genre, language, language_secondary: languageSecondary,
        language_mix: languageMix, language_override: languageOverride,
        vocal_gender: vocalGender, duration, mode: 'suggest',
      })
      if (res?.titles?.length) setTitleIdeas(res.titles)
    } catch (e) {
      setRequestError(`Saran judul gagal: ${e.message}`)
    } finally {
      setTitleBusy(false)
    }
  }

  const extendSong = async () => {
    if (!job?.job_id || job.status !== 'completed' || extendBusy) return
    setExtendBusy(true)
    try {
      const res = await extendMusic(job.job_id, { extra_lyrics: lyrics.slice(-500) || 'lanjutkan cerita', extra_duration: 60 })
      setRequestError('')
      setGroupId('')
      setTakes([])
      setJob({ job_id: res.job_id, status: 'queued', title: job.title, metadata: { stage: 'queued-extend', lyrics_timeline: [] } })
      setBusy(true)
      startedAt.current = Date.now()
    } catch (e) {
      setRequestError(`Extend gagal: ${e.message}`)
    } finally {
      setExtendBusy(false)
    }
  }

  const loadStems = async () => {
    if (!job?.job_id || job.status !== 'completed' || stemsBusy) return
    setStemsBusy(true)
    try {
      const res = await fetchMusicStems(job.job_id)
      const files = res?.stems?.files || []
      setStemsInfo(files.length ? `🎚️ Stems: ${files.join(', ')}` : 'Stems belum tersedia (butuh Demucs di server).')
    } catch (e) {
      setStemsInfo(`Stems gagal: ${e.message}`)
    } finally {
      setStemsBusy(false)
    }
  }

  const lyricsLines = job?.metadata?.lyrics_timeline || []
  const mediaBust = `${job?.job_id || ''}:${job?.updated_at || ''}`
  const audioUrl = useMemo(() => {
    if (job?.status !== 'completed' || !job.audio_path) return ''
    const url = resolveMediaAsset(job.audio_path)
    return mediaBust
      ? `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(mediaBust)}` : url
  }, [job, mediaBust])
  const coverSrc = useMemo(() => {
    const path = coverUrl || job?.cover_path || job?.metadata?.cover_path || ''
    if (!path) return ''
    const url = resolveMediaAsset(path)
    const bust = coverBust || mediaBust
    return bust ? `${url}${url.includes('?') ? '&' : '?'}cb=${encodeURIComponent(bust)}` : url
  }, [coverUrl, job, coverBust, mediaBust])
  const coverSource = job?.cover_source || job?.metadata?.cover_source || ''
  const wavUrl = useMemo(() => {
    const raw = String(job?.metadata?.wav_path || '').trim()
    if (!raw) return ''
    const path = raw.startsWith('/videos/')
      ? raw
      : `/videos/${job.job_id}/${raw.replace(/^\/+/, '')}`
    return resolveMediaAsset(path)
  }, [job])

  const activeIndex = useMemo(() => {
    const total = songDuration || Number(job?.duration) || 0
    if (!lyricsLines.length || !total) return -1
    let idx = -1
    for (let i = 0; i < lyricsLines.length; i += 1) {
      if (currentTime >= (Number(lyricsLines[i].start) || 0)) idx = i
    }
    return idx
  }, [lyricsLines, currentTime, songDuration, job])

  useEffect(() => {
    if (activeLineRef.current) {
      activeLineRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [activeIndex])

  const visibleGenres = useMemo(() => {
    const query = genreQuery.trim().toLowerCase()
    const list = catalog.genres || []
    if (!query) return list
    return list.filter((item) => `${item.label} ${item.id}`.toLowerCase().includes(query))
  }, [catalog.genres, genreQuery])

  const regenerateCover = async (overrideUrl = '') => {
    if (!user) { onSignIn(); return }
    if (!job?.job_id || job.status !== 'completed') return
    setCoverBusy(true)
    try {
      const res = await refreshMusicCover(job.job_id, {
        cover_url: overrideUrl,
        query: `${genreInfo?.label || ''} ${prompt}`.trim(),
      })
      setCoverUrl(res.cover_path || '')
      setCoverBust(Date.now())
    } catch (e) {
      setRequestError(`Ganti cover gagal: ${e.message}`)
    }
    setCoverBusy(false)
  }

  const pickCoverFile = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.size > 8 * 1024 * 1024) { setRequestError('Cover maksimal 8 MB.'); return }
    const reader = new FileReader()
    reader.onload = () => regenerateCover(String(reader.result || ''))
    reader.readAsDataURL(file)
  }

  const togglePlay = () => {
    if (!audioRef.current) return
    if (playing) audioRef.current.pause()
    else audioRef.current.play()
    setPlaying(!playing)
  }

  const onTimeUpdate = () => {
    if (audioRef.current) setCurrentTime(audioRef.current.currentTime || 0)
  }

  const onLoadedMetadata = () => {
    if (audioRef.current) {
      setSongDuration(audioRef.current.duration || 0)
      setCurrentTime(0)
    }
  }

  const seekTo = (seconds) => {
    if (!audioRef.current) return
    audioRef.current.currentTime = Math.max(0, Number(seconds) || 0)
    if (!playing) audioRef.current.play().then(() => setPlaying(true)).catch(() => {})
  }

  const realignTimeline = async () => {
    if (!job?.job_id || realignBusy) return
    setRealignBusy(true)
    setRealignInfo('')
    try {
      const updated = await realignMusic(job.job_id)
      setJob(updated)
      const words = updated.metadata?.word_alignment
      const bpm = updated.metadata?.beat_bpm
      setRealignInfo(
        `Timeline diperbarui (${words ? 'word-level' : updated.metadata?.alignment || 'even'})`
        + (bpm ? ` - ${Math.round(bpm)} BPM` : '') + '.')
    } catch (e) {
      setRealignInfo(`Gagal align ulang: ${e.message}`)
    } finally {
      setRealignBusy(false)
    }
  }

  const openHistoryJob = (entry) => {
    setRequestError('')
    setCoverUrl('')
    setCoverBust(Date.now())
    setStemsInfo('')
    const active = ['queued', 'processing'].includes(entry.status)
    setBusy(active)
    if (active) {
      const started = Number(entry.created_at || entry.updated_at || 0) * 1000
      startedAt.current = started > 0 ? started : Date.now()
    }
    // Show the clicked row immediately; group fetch may replace it with the
    // preferred take a moment later.
    setJob(entry)
    setTakes([entry])
    // Riwayat lama = single job; bila entry punya group_id, muat grup A/B-nya.
    const gid = entry.group_id || entry.metadata?.group_id || ''
    if (gid) {
      setGroupId(gid)
      getMusicGroup(gid).then((g) => {
        const next = g.takes || [entry]
        setTakes(next)
        const preferred = preferredGroupJob(next)
        setJob(preferred || entry)
        setBusy(Boolean(preferred && ['queued', 'processing'].includes(preferred.status)))
      }).catch(() => {
        setTakes([entry])
        setJob(entry)
      })
    } else {
      setGroupId('')
      setTakes([])
      setJob(entry)
    }
    if (entry.genre) setGenre(entry.genre)
    if (entry.language) setLanguage(entry.language)
  }

  const selectTake = (take) => {
    setCoverUrl('')
    setCoverBust(Date.now())
    setStemsInfo('')
    setPlaying(false)
    setCurrentTime(0)
    setJob(take)
  }

  const savePersona = async () => {
    if (!user) { onSignIn(); return }
    if (!job?.job_id || job.status !== 'completed') return
    try {
      const res = await createMusicPersona({
        name: `${job.title || 'Persona'} — ${genreInfo?.label || genre}`,
        job_id: job.job_id,
      })
      if (res?.persona) {
        setPersonas((prev) => [res.persona, ...prev])
        setPersonaId(res.persona.persona_id)
      }
    } catch (e) {
      setRequestError(`Simpan persona gagal: ${e.message}`)
    }
  }

  const saveVoicePersona = async () => {
    if (!user) { onSignIn(); return }
    if (!voiceFile || !voiceConsent || voiceBusy) return
    setVoiceBusy(true)
    setRequestError('')
    try {
      const res = await uploadMusicVoicePersona({
        name: voiceName.trim() || 'Suara saya', vocalGender: voiceGender,
        consent: voiceConsent, file: voiceFile,
      })
      if (res?.persona) {
        setPersonas((prev) => [res.persona, ...prev])
        setPersonaId(res.persona.persona_id)
        setVoiceCapability(res.voice_conversion || null)
        setVoiceName('')
        setVoiceFile(null)
        setVoiceConsent(false)
        setVoicePreviewUrl((oldUrl) => { if (oldUrl) URL.revokeObjectURL(oldUrl); return '' })
        const fileInput = document.getElementById('music-voice-sample')
        if (fileInput) fileInput.value = ''
        if (!res.voice_conversion?.ready) setRequestError(`${res.voice_conversion?.message || 'Konversi suara belum tersedia.'} Persona tersimpan, tetapi lagu tidak akan dibuat sampai layanan siap.`)
      }
    } catch (e) {
      setRequestError(`Simpan rekaman gagal: ${e.message}`)
    } finally {
      setVoiceBusy(false)
    }
  }

  const toggleVoiceRecording = async () => {
    if (voiceRecording) {
      voiceRecorderRef.current?.stop()
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setRequestError('Browser ini belum mendukung rekaman mikrofon. Silakan rekam di perangkat lalu unggah file audio.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      voiceStreamRef.current = stream
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus' : ''
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      voiceChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data?.size) voiceChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(voiceChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        if (blob.size) {
          const file = new File([blob], 'rekaman-persona.webm', { type: blob.type })
          setVoiceFile(file)
          setVoicePreviewUrl((oldUrl) => {
            if (oldUrl) URL.revokeObjectURL(oldUrl)
            return URL.createObjectURL(blob)
          })
        }
        voiceStreamRef.current?.getTracks().forEach((track) => track.stop())
        voiceStreamRef.current = null
        voiceRecorderRef.current = null
        setVoiceRecording(false)
      }
      recorder.start(250)
      voiceRecorderRef.current = recorder
      setVoiceRecordingSeconds(0)
      setVoiceRecording(true)
      setRequestError('')
    } catch (e) {
      voiceStreamRef.current?.getTracks().forEach((track) => track.stop())
      voiceStreamRef.current = null
      setRequestError(`Mikrofon tidak bisa digunakan: ${e.message || 'periksa izin browser lalu coba lagi.'}`)
    }
  }

  const removePersona = async (pid) => {
    try {
      await deleteMusicPersona(pid)
      setPersonas((prev) => prev.filter((p) => p.persona_id !== pid))
      if (personaId === pid) setPersonaId('')
    } catch (e) {
      setRequestError(`Hapus persona gagal: ${e.message}`)
    }
  }

  const coverSong = async () => {
    if (!user) { onSignIn(); return }
    if (!job?.job_id || job.status !== 'completed' || coverSongBusy) return
    setCoverSongBusy(true)
    setRequestError('')
    try {
      const res = await coverSongMusic(job.job_id, {
        prompt: prompt.trim(), genre, vocal_gender: effectiveGender,
        take_count: 2,
      })
      setGroupId(res.group_id || '')
      const initialTakes = (res.jobs || []).map((t) => ({
        ...t, title: `${job.title} (cover ${genreInfo?.label || genre})`,
        metadata: { stage: 'queued-cover', cover_mode: 'audio-to-audio',
          lyrics_locked: true, lyrics_timeline: [] },
      }))
      setTakes(initialTakes)
      setJob(initialTakes[0] || null)
      setBusy(true)
      startedAt.current = Date.now()
      setElapsed(0)
    } catch (e) {
      setRequestError(`Cover gagal: ${e.message}`)
    } finally {
      setCoverSongBusy(false)
    }
  }

  const remixFromUrl = async () => {
    if (!user) { onSignIn(); return }
    if (!remixUrl.trim() || remixBusy) return
    setRemixBusy(true)
    setRequestError('')
    try {
      const coverGender = remixGender === 'auto' ? effectiveGender : remixGender
      const res = await remixMusicUrl({ url: remixUrl.trim(), genre: remixGenre,
        prompt: remixPrompt.trim(), ref_strength: Number(remixStrength),
        lyrics: remixLyrics.trim(),
        vocal_gender: coverGender,
        persona_id: personaId, rights_confirmed: remixRightsConfirmed,
        cover_engine: remixEngine, pitch_shift: remixPitch,
        restyle: remixRestyle, voice_mode: remixVoiceMode })
      setGroupId(res.group_id || '')
      const initialTakes = (res.jobs || []).map((t) => ({
        ...t,
        title: res.remix_source_title || 'URL cover',
        metadata: {
          stage: 'queued-cover',
          cover_mode: true,
          cover_engine: res.cover_engine,
          lyrics_locked: true,
          lyrics_source: res.lyrics_source,
          lyrics_timeline: [],
        },
      }))
      setTakes(initialTakes)
      setJob(initialTakes[0] || null)
      setBusy(true)
      startedAt.current = Date.now()
      setElapsed(0)
    } catch (e) {
      setRequestError(`Remix link gagal: ${e.message}`)
    } finally {
      setRemixBusy(false)
    }
  }

  const toggleShare = async () => {
    if (!job?.job_id || job.status !== 'completed' || shareBusy) return
    setShareBusy(true)
    try {
      const res = await shareMusic(job.job_id, !job.is_public)
      setJob({ ...job, is_public: res.is_public ? 1 : 0 })
      setTakes((prev) => prev.map((t) => (t.job_id === job.job_id
        ? { ...t, is_public: res.is_public ? 1 : 0 } : t)))
    } catch (e) {
      setRequestError(`Share gagal: ${e.message}`)
    } finally {
      setShareBusy(false)
    }
  }

  const statusMeta = STATUS_META[job?.status] || null
  const engineReady = engine ? Boolean(engine.ready) : null
  const createBlocked = Boolean(working || engineBusy || engineReady === false)
  const readinessText = engineBusy
    ? 'Memeriksa kesiapan worker…'
    : engineReady === false
      ? 'Worker belum siap — cek ulang sebelum membuat lagu.'
      : missing.length
        ? `Lengkapi ${missing.length} bagian wajib untuk melanjutkan.`
        : 'Siap dibuat — worker akan memproses lagu di latar belakang.'

  return (
    <div className="mx-auto max-w-[1680px] px-3 pb-28 pt-5 sm:px-5 lg:px-7">
      {/* Suno AI Studio Top Header Bar */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-[#11131a]/90 px-4 py-3 shadow-xl backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-[#ff5a1f] via-[#ff2d78] to-[#b829ff] text-white shadow-lg">
              <Icon name="music" className="h-5 w-5" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-black tracking-tight text-white">Klipin Music</span>
                <span className="rounded-full border border-[#ff5a1f]/40 bg-[#ff5a1f]/15 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-[#ff9a62]">
                  Suno Studio v4.5
                </span>
              </div>
              <p className="text-[11px] text-white/45">
                Full-Song AI Studio · Nusantara & Global · Apache 2.0 Commercial Ready
              </p>
            </div>
          </div>

          <div className="hidden h-6 w-px bg-white/10 sm:block" />

          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Bagian studio musik">
            {[
              { id: 'create', label: '✨ Create Song' },
              { id: 'cover', label: '🎛️ Audio / Cover' },
              { id: 'history', label: '🗂️ Library (' + historyList.length + ')' },
            ].map((tab) => (
              <button key={tab.id} type="button" onClick={() => setStudioTab(tab.id)}
                aria-pressed={studioTab === tab.id}
                className={`rounded-full px-3.5 py-1.5 text-xs font-bold transition ${
                  studioTab === tab.id
                    ? 'bg-gradient-to-r from-[#ff5a1f] via-[#ff2d78] to-magenta text-white shadow-md'
                    : 'border border-white/10 bg-white/5 text-white/65 hover:border-white/25 hover:text-white'}`}>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-white/10 bg-black/30 px-3 py-1 font-mono text-[10px] text-white/70">
            {(providerOptions.find((item) => item.id === provider) || {}).label || 'HeartMuLa'} · L4
          </span>
          <div className={`rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-widest ${
            engineReady === null ? 'border-white/15 bg-white/5 text-white/50'
              : engineReady ? 'border-signal/25 bg-signal/10 text-signal'
                : 'border-warn/40 bg-warn/10 text-warn'}`}>
            {engineBusy ? '● memeriksa worker…' : engineReady === null
              ? '● status worker belum diketahui'
              : engineReady ? '● engine lagu siap' : '● engine lagu belum siap'}
          </div>
          <button type="button" onClick={refreshEngine} disabled={engineBusy}
            className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-[10px] font-bold text-white/70 transition hover:border-cyan/50 disabled:opacity-40">
            ↻ Cek worker
          </button>
        </div>
      </div>

      {(catalogError || (engine && !engine.ready)) && (
        <div className="mb-4 space-y-2">
          {catalogError && (
            <Notice tone="err" title="Katalog musik gagal dimuat" text={catalogError}
              action={{ label: 'Coba lagi', onClick: () => loadCatalog(true) }} />
          )}
          {engine && !engine.ready && (
            <Notice tone="warn" title="Engine lagu penuh belum siap" text={engine.message}
              action={{ label: 'Cek ulang', onClick: refreshEngine }} />
          )}
        </div>
      )}

      {authChecked && !user && (
        <div className="mb-4">
          <Notice tone="warn" title="Sesi login tidak aktif"
            text="Status lagu dan hasil Modal tidak bisa disinkronkan sebelum kamu masuk lagi. Jangan membuat job baru dulu; masuk untuk melanjutkan pemantauan job sebelumnya."
            action={{ label: 'Masuk untuk melanjutkan', onClick: onSignIn }} />
        </div>
      )}

      {/* Suno 3-Pane Studio Workspace: Left Create/Cover Dock | Center Song Feed | Right Player & Lyrics Inspector */}
      <div className="grid items-start gap-5 xl:grid-cols-[430px_minmax(0,1fr)_390px]">
        <section className="card-chrome overflow-hidden rounded-2xl border border-white/10 bg-[#12141d]">
          {/* Suno Left Dock Pill Header */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-black/25 px-4 py-3">
            <div className="flex items-center gap-1.5" role="group" aria-label="Mode pembuatan lagu">
              <button type="button" onClick={() => { setStudioTab('create'); setSimpleMode(false) }}
                className={`rounded-full px-3 py-1 text-[11px] font-bold transition ${studioTab !== 'cover' && !simpleMode ? 'bg-white text-navy shadow' : 'border border-white/10 bg-white/5 text-white/60 hover:text-white'}`}>
                ⚙ Custom
              </button>
              <button type="button" onClick={() => { setStudioTab('create'); setSimpleMode(true) }}
                className={`rounded-full px-3 py-1 text-[11px] font-bold transition ${studioTab !== 'cover' && simpleMode ? 'bg-white text-navy shadow' : 'border border-white/10 bg-white/5 text-white/60 hover:text-white'}`}>
                ✨ Simple
              </button>
              <button type="button" onClick={() => setStudioTab(studioTab === 'cover' ? 'create' : 'cover')}
                className={`rounded-full px-3 py-1 text-[11px] font-bold transition ${studioTab === 'cover' ? 'bg-gradient-to-r from-[#ff5a1f] to-[#ff2d78] text-white shadow' : 'border border-cyan/35 bg-cyan/10 text-cyan-soft hover:border-cyan'}`}>
                🎛️ Audio / Cover
              </button>
            </div>
            <button type="button" onClick={() => setInstrumental((v) => !v)}
              className={`rounded-full border px-2.5 py-1 font-mono text-[10px] font-bold transition ${
                instrumental ? 'border-magenta bg-magenta/20 text-white' : 'border-white/10 bg-white/5 text-white/50 hover:text-white'}`}>
              {instrumental ? '🎵 Instrumental ON' : '🎤 Vocal'}
            </button>
          </div>
          <div className="space-y-4 p-4">
            {showErrors && missing.length > 0 && studioTab === 'create' && (
              <Notice tone="warn" title={`${missing.length} bagian yang harus diisi`}
                text={missing.map((item) => `• ${item}`).join('\n')} />
            )}
            {requestError && (
              <Notice tone="err" title="Permintaan ditolak" text={requestError} />
            )}
            {simpleMode && studioTab === 'create' && (
              <p className="text-[11px] leading-5 text-white/45">
                Mode Simple: tulis ide lalu tekan <b className="text-white/70">Buat lagu</b>. Beralih ke
                <b className="text-white/70"> Custom</b> untuk lirik, persona, bahasa ganda, BPM/key, dan kontrol lanjutan.
              </p>
            )}

            {studioTab === 'cover' && <section className="rounded-xl border border-cyan/25 bg-cyan/5 p-4">
              <div className="mb-1 text-xs font-bold uppercase tracking-widest text-cyan-soft">Cover lagu dari link</div>
              <p className="mb-3 text-[11px] leading-5 text-white/50">Tempel link YouTube publik. Klipin mengambil audio, mengekstrak lirik sumber terlebih dahulu, lalu menyanyikannya dengan persona/gender terpilih dalam aransemen genre baru. Default memakai MuLaCover agar irama, melodi, dan chord asli tetap; bila worker itu mati, DiffRhythm/HeartMuLa dipakai sebagai fallback. ACE-Step hanya draft cepat, bukan cover faithful. Gunakan hanya materi yang kamu punya izinnya.</p>
              <input value={remixUrl} onChange={(e) => setRemixUrl(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" className="w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-sm text-white outline-none focus:border-cyan/60" />
              <label className="mt-3 block">
                <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">Lirik manual (opsional) — satu baris = satu kalimat/frasa vokal</span>
                <textarea value={remixLyrics} onChange={(e) => setRemixLyrics(e.target.value)} rows={6} placeholder={'[Verse]\nEntah sampai kapan aku harus menunggu\nSesuatu yang sangat sulit ku jalani\n…'} className="w-full resize-y rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-[12px] leading-5 text-white outline-none focus:border-cyan/60" />
                <span className="mt-1 block text-[10px] leading-4 text-white/40">Isi agar ketukan vokal mengikuti frasa asli. Kosongkan untuk ekstraksi lirik otomatis (ASR).</span>
              </label>
              <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr]">
                <select value={remixGenre} onChange={(e) => setRemixGenre(e.target.value)} className="rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                  {(catalog.genres || []).map((g) => {
                    const id = g.id || g
                    return <option key={id} value={id}>{g.label || id}</option>
                  })}
                </select>
                <input value={remixPrompt} onChange={(e) => setRemixPrompt(e.target.value)} placeholder="Arahan tambahan (opsional)" className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-cyan/60" />
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">Gender vokal cover</span>
                  <select value={remixGender} disabled={!!identityPersona} onChange={(e) => setRemixGender(e.target.value)} className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none disabled:opacity-60">
                    {VOCAL_GENDERS.map((g) => (
                      <option key={g.id} value={g.id}>{g.id === 'auto' ? 'Ikuti gender/persona' : g.label}</option>
                    ))}
                  </select>
                </label>
                <div className="flex items-end">
                  <p className="text-[11px] leading-5 text-white/50">
                    {identityPersona
                      ? `Persona "${identityPersona.name}" aktif — suara & gender mengikuti persona.`
                      : personaId
                        ? 'Persona gaya aktif — pilih gender di samping.'
                        : 'Pilih persona di bawah untuk memakai suara virtual/rekaman.'}
                  </p>
                </div>
              </div>
              <div className="mt-3">
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">Engine cover</span>
                  <select value={remixEngine} onChange={(e) => {
                    const next = e.target.value
                    setRemixEngine(next)
                    if (next === 'arrangement') setRemixPitch('0')
                    else if (next === 'voice' && remixPitch === '0') setRemixPitch('auto')
                  }} className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                    <option value="mulacover">⭐ MuLaCover — Melodi & Chord Asli + Aransemen Baru (Disarankan)</option>
                    <option value="arrangement">🎚️ Aransemen Baru (MuLaCover) + Timpa Vokal Asli (Demucs Stem)</option>
                    <option value="voice">🎤 Voice-conversion (Seed-VC) + Aransemen Baru (MuLaCover)</option>
                    <option value="diffrhythm">⚡ DiffRhythm — Reimagine Melodi Baru (Apache 2.0)</option>
                    <option value="heartmula">📝 HeartMuLa — Lirik Terkunci, Melodi Baru</option>
                    <option value="ace-step">Draft Cepat (ACE-Step — Kualitas Terbatas)</option>
                    <option value="">🤖 Otomatis — MuLaCover → Arrangement → DiffRhythm</option>
                  </select>
                </label>
                <p className="mt-1 text-[11px] leading-5 text-white/45">
                  {remixEngine === 'arrangement'
                    ? '⭐ Mode Cover YouTube (seperti Reggae Hits / Koplo Cover): vokal asli penyanyi dari sumber dipisahkan (Demucs Stem), dibersihkan, dan langsung ditimpa di atas aransemen musik baru sesuai genre pilihan (BPM & progresi chord terkunci).'
                    : remixEngine === 'voice'
                      ? 'Voice-conversion: vokal asli diisolasi lalu warna suaranya diganti (Seed-VC) memakai persona/gender terpilih — melodi, lirik, dan timing tetap utuh (bisa sekaligus ganti aransemen).'
                      : remixEngine === 'diffrhythm'
                        ? 'DiffRhythm (Apache 2.0) menyanyikan lirik sumber apa adanya dengan melodi & aransemen baru — hasil cepat, ber-vokal, dan durasi penuh.'
                        : remixEngine === ''
                          ? 'Otomatis memakai MuLaCover/Arrangement agar irama, melodi, dan chord asli tetap; DiffRhythm/HeartMuLa dipakai bila worker faithful tidak aktif.'
                          : remixEngine === 'mulacover'
                            ? 'MuLaCover menjaga melodi, chord, dan timeline asli lewat kondisi simbolik (MIDI + Chord) dan menyanyikannya ulang dengan AI.'
                            : remixEngine === 'ace-step'
                              ? 'ACE-Step draft cepat (25 step) dengan referensi audio.'
                              : 'HeartMuLa mengunci lirik tetapi menggubah melodi baru.'}
                </p>
                {(remixEngine === 'voice' || remixEngine === 'arrangement') && (
                  <label className="mt-2 block">
                    <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">Nada vokal (pitch)</span>
                    <select value={remixPitch} onChange={(e) => setRemixPitch(e.target.value)} className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                      <option value="0">0 — Pertahankan nada vokal asli (disarankan untuk Vokal Asli)</option>
                      <option value="auto">Otomatis — geser oktaf sesuai gender target</option>
                      <option value="2">+2 semitone — naikkan sedikit</option>
                      <option value="-2">-2 semitone — turunkan sedikit</option>
                      <option value="12">+12 — pria ke wanita</option>
                      <option value="-12">-12 — wanita ke pria</option>
                    </select>
                  </label>
                )}
                {remixEngine === 'voice' && (
                  <label className="mt-2 block">
                    <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">Sumber suara</span>
                    <select value={remixVoiceMode} onChange={(e) => setRemixVoiceMode(e.target.value)} className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                      <option value="manual">Saya pilih (persona / gender)</option>
                      <option value="auto">AI Klipin pilih otomatis</option>
                    </select>
                  </label>
                )}
                {remixEngine === 'voice' && (
                  <label className="mt-2 flex items-center gap-2 text-[11px] text-white/65">
                    <input type="checkbox" checked={remixRestyle} onChange={(e) => setRemixRestyle(e.target.checked)} className="accent-cyan" />
                    Ganti aransemen ke genre (restyle instrumental)
                  </label>
                )}
              </div>
              <div className="mt-3 flex items-center gap-3 text-[11px] text-white/55">
                <span>Ikuti lagu: {Math.round(remixStrength * 100)}%</span>
                <input type="range" min="0.1" max="0.9" step="0.1" value={remixStrength} onChange={(e) => setRemixStrength(Number(e.target.value))} className="flex-1 accent-cyan" />
              </div>
              <label className="mt-3 flex items-start gap-2 rounded-lg border border-white/10 bg-black/20 p-2.5 text-[11px] leading-5 text-white/60">
                <input type="checkbox" checked={remixRightsConfirmed} onChange={(e) => setRemixRightsConfirmed(e.target.checked)} className="mt-0.5 accent-cyan" />
                <span>Saya memiliki hak/izin memakai lagu sumber ini untuk membuat cover.</span>
              </label>
              <button type="button" onClick={remixFromUrl} disabled={!remixUrl.trim() || remixBusy || busy || !remixRightsConfirmed} className="mt-3 w-full rounded-lg border border-cyan/40 bg-cyan/10 px-3 py-2.5 text-sm font-black text-cyan-soft transition hover:border-cyan disabled:opacity-40">
                {remixBusy ? 'Ekstrak lirik & membuat cover…' : '🔁 Buat cover dari link'}
              </button>
            </section>}

            {studioTab === 'cover' && (
              <CoverCreate user={user} authChecked={authChecked} onSignIn={onSignIn} embedded />
            )}

            {studioTab !== 'cover' && (<>

            <Group title="1 · Ide & Judul" subtitle="Cerita/mood lagu dan judulnya" defaultOpen>
            <label className="block">
              <span className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-bold uppercase tracking-widest text-white/55">
                <span>Song idea <span className="text-danger">*</span></span>
                <button type="button" onClick={rollIdea}
                  title="Pilih ide acak ala “Roll for a random prompt” Suno"
                  className="rounded-full border border-cyan/40 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold normal-case text-white/85 transition hover:border-cyan">
                  🎲 Ide acak
                </button>
              </span>
              <textarea ref={promptInputRef} value={prompt} onChange={(e) => { setPrompt(e.target.value); setRequestError('') }} rows={3}
                aria-invalid={Boolean(vis("idea"))}
                className={`w-full resize-none rounded-xl border bg-black/20 p-4 text-sm text-white outline-none transition ${
                  vis("idea") ? 'border-danger/50' : 'border-white/10 focus:border-cyan/60'}`}
                placeholder="Describe the feeling, story, instruments…" />
              <span className="mt-1 block font-mono text-[10px] text-white/35">
                {prompt.trim().length}/{MIN_PROMPT} karakter — atau isi lirik di bawah.
              </span>
            </label>

            <div className="block">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-bold uppercase tracking-widest text-white/55">
                  Judul lagu <span className="text-danger">*</span>
                </span>
                <button type="button" onClick={suggestTitles} disabled={titleBusy || working || (!prompt.trim() && !lyrics.trim())}
                  title="Minta 3 saran judul dari ide/lirik (Gemini)"
                  className="rounded-full border border-magenta/40 bg-magenta/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-magenta disabled:opacity-40">
                  {titleBusy ? '✨ Memikir…' : '✨ Saran judul'}
                </button>
              </div>
              <input ref={titleInputRef} value={title} onChange={(e) => { setTitle(e.target.value); setTitleIdeas([]); setRequestError('') }}
                aria-invalid={Boolean(vis("title"))}
                className={`w-full rounded-lg border bg-black/20 px-3 py-2.5 text-sm text-white outline-none transition ${
                  vis("title") ? 'border-danger/50' : 'border-white/10 focus:border-cyan/60'}`}
                placeholder="Tulis judul sendiri atau minta saran AI…" />
              {titleIdeas.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {titleIdeas.map((t) => (
                    <button key={t} type="button" onClick={() => { setTitle(t); setTitleIdeas([]) }}
                      className="rounded-full border border-cyan/40 bg-cyan/10 px-2.5 py-1 text-[11px] font-semibold text-white/85 transition hover:border-cyan">
                      {t}
                    </button>
                  ))}
                </div>
              )}
              {vis("title") && (
                <span className="mt-1 block text-[11px] text-danger">{vis("title")}</span>
              )}
            </div>
            </Group>

            <Group title="2 · Gaya & Suara" subtitle="Genre, bahasa, durasi, dan vokal" defaultOpen>
            <div className="relative">
              <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-white/55">Genre</span>
              <button type="button" onClick={() => setGenreOpen((v) => !v)}
                aria-haspopup="listbox" aria-expanded={genreOpen}
                className="flex w-full items-center justify-between gap-2 rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-left text-sm text-white outline-none transition focus:border-cyan/60">
                <span className="truncate">
                  {(catalog.genres || []).find((g) => g.id === genre)?.label || genre}
                  {genreInfo?.language_locked && (
                    <span className="ml-2 font-mono text-[10px] text-cyan-soft/80">🔒{genreInfo.language_label}</span>
                  )}
                </span>
                <span className="shrink-0 text-white/40">{genreOpen ? '▲' : '▼'}</span>
              </button>
              {genreOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setGenreOpen(false)} />
                  <div className="absolute z-20 mt-1.5 w-full overflow-hidden rounded-xl border border-white/15 bg-navy-3 shadow-2xl">
                    <div className="border-b border-white/10 p-2">
                      <input value={genreQuery} onChange={(e) => setGenreQuery(e.target.value)}
                        placeholder="Ketik untuk mencari genre…" aria-label="Cari genre" autoFocus
                        className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-cyan/60" />
                    </div>
                    <div role="listbox" className="max-h-56 overflow-y-auto p-1.5">
                      {visibleGenres.map((item) => (
                        <button key={item.id} type="button" role="option" aria-selected={genre === item.id}
                          onClick={() => { setGenre(item.id); setGenreOpen(false); setGenreQuery('') }}
                          title={item.language_locked
                            ? `Bahasa vokal terkunci: ${item.language_label}` : 'Bahasa vokal bebas'}
                          className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                            genre === item.id
                              ? 'bg-magenta/25 font-bold text-white'
                              : 'text-white/70 hover:bg-white/5 hover:text-white'}`}>
                          <span className="truncate">{item.label}</span>
                          {item.language_locked && (
                            <span className="shrink-0 font-mono text-[10px] text-cyan-soft/70">🔒{item.language_label}</span>
                          )}
                        </button>
                      ))}
                      {visibleGenres.length === 0 && (
                        <p className="px-3 py-4 text-[12px] text-white/40">Tidak ada genre yang cocok.</p>
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-bold uppercase tracking-widest text-white/55">Bahasa vokal</span>
                  {languageLocked && (
                    <span className="rounded-full border border-cyan/30 bg-cyan/10 px-2 py-0.5 font-mono text-[9px] uppercase text-cyan-soft">
                      🔒 Terkunci
                    </span>
                  )}
                </div>
                <select value={language} onChange={(e) => setLanguage(e.target.value)}
                  disabled={languageLocked} aria-label="Bahasa vokal"
                  className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none disabled:cursor-not-allowed disabled:opacity-60">
                  {languageGroups.map(([group, items]) => (
                    <optgroup key={group} label={group}>
                      {items.map((item) => (
                        <option key={item.id} value={item.id}>{item.label}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <p className="mt-1.5 text-[11px] leading-4 text-white/45">
                  {languageLocked
                    ? `Genre ${genreInfo?.label || genre} selalu berbahasa ${genreInfo?.language_label || languageLabel}.`
                    : `Bahasa bebas dipilih. Aktif: ${languageLabel}.`}
                </p>
              </div>
              <div className="grid gap-4">
                <label className="block">
                  <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-white/55">Gender vokal</span>
                  <select value={effectiveGender} disabled={!!identityPersona} onChange={(e) => setVocalGender(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none">
                    {VOCAL_GENDERS.map((item) => (
                      <option key={item.id} value={item.id}>{item.label}</option>
                    ))}
                  </select>
                  {identityPersona && <span className="mt-1 block text-[11px] text-white/50">Mengikuti persona {identityPersona.name}. Pilih “Tanpa persona” untuk mengganti gender.</span>}
                </label>
                <label className="block">
                  <span className="mb-2 flex items-center justify-between gap-2 text-xs font-bold uppercase tracking-widest text-white/55">
                    <span>Durasi lagu</span>
                    <span className="flex items-center gap-1 text-[10px] font-semibold normal-case tracking-normal text-white/55">
                      <input type="checkbox" checked={autoDuration}
                        onChange={(e) => setAutoDuration(e.target.checked)}
                        className="accent-cyan" />
                      Auto ikuti lirik
                    </span>
                  </span>
                  <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}
                    disabled={autoDuration}
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none disabled:cursor-not-allowed disabled:opacity-50">
                    {DURATIONS.map((value) => (
                      <option key={value} value={value}>
                        {value < 60 ? `${value} detik` : `${value / 60} menit`}
                      </option>
                    ))}
                  </select>
                  {autoDuration && (
                    <span className="mt-1 block text-[11px] leading-4 text-cyan-soft">
                      {lyricSungCount > 0
                        ? `≈ ${Math.max(30, Math.min(600, Math.round(lyricSungCount * AUTO_SECONDS_PER_LINE + 6)))} dtk mengikuti ${lyricSungCount} baris lirik; panjang akhir = audio sebenarnya.`
                        : 'Panjang mengikuti lirik yang dibuat/diisi; durasi akhir = audio sebenarnya.'}
                    </span>
                  )}
                  {vis("duration") && !autoDuration && (
                    <span className="mt-1 block text-[11px] text-danger">{vis("duration")}</span>
                  )}
                </label>
              </div>
            </div>

              <label className="flex items-start gap-2 rounded-lg border border-white/10 bg-black/20 p-3 text-[11px] leading-5 text-white/55">
                <input type="checkbox" checked={instrumental}
                  onChange={(e) => setInstrumental(e.target.checked)}
                  className="mt-0.5 accent-magenta" />
                <span>
                  <b className="text-white/80">Instrumental</b> — tanpa vokal sama sekali
                  (lirik dilewati, deteksi BPM/key tetap jalan).
                </span>
              </label>
            </Group>

            {!simpleMode && (<Group title="3 · Persona & Bahasa" subtitle="Persona vokal, dual language, override bahasa">
            <label className="flex items-start gap-2 rounded-lg border border-white/10 bg-black/20 p-3 text-[11px] leading-5 text-white/55">
              <input type="checkbox" checked={languageOverride}
                onChange={(e) => setLanguageOverride(e.target.checked)}
                className="mt-0.5 accent-cyan" />
              <span>
                <b className="text-white/80">Timpa bahasa vokal</b> — pakai bahasa pilihan saya
                walaupun genrenya daerah (mis. Pop Jawa dengan vokal Indonesia).
              </span>
            </label>

            <div className="rounded-xl border border-magenta/25 bg-magenta/5 p-3.5">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <span className="block text-xs font-bold uppercase tracking-widest text-white/70">Dual language</span>
                  <span className="text-[11px] text-white/45">Verse Jawa → chorus Korea/Jepang atau kombinasi lain</span>
                </div>
                {bilingual && <span className="rounded-full border border-magenta/40 bg-magenta/15 px-2 py-0.5 font-mono text-[9px] text-magenta-soft">AKTIF</span>}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">Bahasa kedua</span>
                  <select value={languageSecondary} onChange={(e) => setLanguageSecondary(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none">
                    <option value="">Tanpa bahasa kedua</option>
                    {languageGroups.map(([group, items]) => (
                      <optgroup key={`secondary-${group}`} label={group}>
                        {items.filter((item) => item.id !== language).map((item) => (
                          <option key={`secondary-${item.id}`} value={item.id}>{item.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">Pola bahasa</span>
                  <select value={languageMix} onChange={(e) => setLanguageMix(e.target.value)}
                    disabled={!languageSecondary}
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none disabled:opacity-50">
                    <option value="chorus">Verse utama → Chorus bahasa kedua</option>
                    <option value="verse-chorus">Verse utama, chorus bergantian</option>
                    <option value="line">Bergantian per baris</option>
                    <option value="auto">Auto AI menentukan section</option>
                    <option value="none">Nonaktif</option>
                  </select>
                </label>
              </div>
              {bilingual && (
                <p className="mt-2 text-[11px] leading-4 text-warn">
                  ⚠ Pelafalan Jawa wajib direview penutur asli sebelum dipublikasikan. AI akan membuat lirik full sesuai pembagian section, tetapi lirik tetap bisa diedit manual di bawah.
                </p>
              )}
              {!bilingual && languageSecondary && (
                <p className="mt-2 text-[11px] text-white/40">Pilih pola selain Nonaktif untuk memakai {languageLabel} + {languageSecondaryLabel}.</p>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-white/55">
                  Persona <span className="font-mono normal-case text-white/30">(suara virtual / rekaman / gaya)</span>
                </span>
                <select value={personaId} onChange={(e) => { setPersonaId(e.target.value); setPersonaListenId('') }}
                  className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none">
                  <option value="">Tanpa persona — suara default</option>
                  {personas.map((p) => (
                    <option key={p.persona_id} value={p.persona_id}>
                      {p.has_voice_sample ? '🎙️ ' : p.persona_type === 'designed' ? '🎨 ' : ''}{p.name} · {p.genre || (p.vocal_gender || 'persona')}
                    </option>
                  ))}
                </select>
                {personaId && (
                  <button type="button" onClick={() => removePersona(personaId)}
                    className="mt-1 font-mono text-[10px] text-danger/80 hover:text-danger">
                    hapus persona ini
                  </button>
                )}
                {personas.find(p => p.persona_id === personaId)?.has_voice_sample && <button type="button"
                  onClick={() => setPersonaListenId(personaListenId === personaId ? '' : personaId)} className="mt-2 block text-xs text-cyan-soft">
                  {personaListenId === personaId ? 'Tutup contoh suara' : '▶ Dengarkan persona terpilih'}
                </button>}
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-white/55">BPM</span>
                  <input type="number" min={0} max={220} value={bpm || ''}
                    onChange={(e) => setBpm(Number(e.target.value) || 0)} placeholder="auto"
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none" />
                </label>
                <label className="block">
                  <span className="mb-2 block text-xs font-bold uppercase tracking-widest text-white/55">Key</span>
                  <input value={musicKey} onChange={(e) => setMusicKey(e.target.value)} placeholder="auto"
                    className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none" />
                </label>
              </div>
            </div>

            <div className="flex flex-wrap gap-2" role="group" aria-label="Cara membuat persona">
              <button type="button" onClick={() => setPersonaMode('choose')}
                className={`rounded-lg border px-3 py-2 text-xs font-semibold ${personaMode === 'choose' ? 'border-cyan/50 bg-cyan/10 text-cyan-soft' : 'border-white/10 bg-white/5 text-white/60'}`}>
                Gunakan pilihan
              </button>
              <button type="button" onClick={() => setPersonaMode(personaMode === 'record' ? 'choose' : 'record')}
                className={`rounded-lg border px-3 py-2 text-xs font-semibold ${personaMode === 'record' ? 'border-magenta/50 bg-magenta/10 text-white' : 'border-white/10 bg-white/5 text-white/60'}`}>
                🎙️ Rekam / unggah suara
              </button>
              <button type="button" onClick={() => setPersonaMode(personaMode === 'design' ? 'choose' : 'design')}
                className={`rounded-lg border px-3 py-2 text-xs font-semibold ${personaMode === 'design' ? 'border-magenta/50 bg-magenta/10 text-white' : 'border-white/10 bg-white/5 text-white/60'}`}>
                🎨 Desain karakter
              </button>
            </div>
            {personaFeedback && <p role="status" className="text-[11px] leading-5 text-signal">{personaFeedback}</p>}
            {personaListenId && <PrivateVoicePlayer id={personaListenId} savedPersona />}

            {personaMode === 'record' && <section className="rounded-xl border border-magenta/20 bg-magenta/5 p-4">
              <div className="mb-2 text-xs font-bold uppercase tracking-widest text-white/70">Persona dari rekaman</div>
              <p className="mb-3 text-[11px] leading-5 text-white/50">Rekam langsung atau unggah suara 3–30 detik yang bersih, tanpa musik latar. Rekaman dienkripsi dan hanya tersedia di akun ini. Konversi ini memakai model voice-conversion saat worker tersedia; bukan sekadar EQ.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <input value={voiceName} onChange={(e) => setVoiceName(e.target.value)} placeholder="Nama persona (opsional)"
                  className="rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                <select value={voiceGender} onChange={(e) => setVoiceGender(e.target.value)}
                  className="rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                  <option value="auto">Gender: otomatis</option><option value="male">Pria</option><option value="female">Wanita</option>
                </select>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" onClick={toggleVoiceRecording}
                  className={`rounded-lg border px-3 py-2 text-xs font-bold ${voiceRecording ? 'border-danger/50 bg-danger/10 text-danger' : 'border-magenta/40 bg-magenta/10 text-white'}`}>
                  {voiceRecording ? `■ Berhenti merekam · ${voiceRecordingSeconds} dtk` : '● Rekam dengan mikrofon'}
                </button>
                {voiceRecording && <span aria-live="polite" className="text-[11px] text-white/55">Maks. 30 detik. Bicara atau nyanyikan nada sederhana di ruangan tenang.</span>}
              </div>
              <input id="music-voice-sample" type="file" accept="audio/*" onChange={(e) => {
                setVoiceFile(e.target.files?.[0] || null)
                setVoicePreviewUrl((oldUrl) => { if (oldUrl) URL.revokeObjectURL(oldUrl); return '' })
              }}
                className="mt-3 block w-full text-xs text-white/60 file:mr-3 file:rounded-lg file:border-0 file:bg-white/10 file:px-3 file:py-2 file:text-white" />
              {voicePreviewUrl && <audio controls src={voicePreviewUrl} className="mt-3 h-9 max-w-full" aria-label="Pratinjau rekaman persona" />}
              <label className="mt-3 flex items-start gap-2 text-[11px] leading-5 text-white/55">
                <input type="checkbox" checked={voiceConsent} onChange={(e) => setVoiceConsent(e.target.checked)} className="mt-1" />
                Saya memiliki hak/izin atas suara ini dan menyetujui pemrosesan rekaman untuk konversi vokal lagu. Saya dapat menghapus persona kapan saja.
              </label>
              <button type="button" onClick={saveVoicePersona} disabled={!voiceFile || !voiceConsent || voiceBusy || voiceRecording}
                className="mt-3 rounded-lg border border-magenta/40 bg-magenta/10 px-3 py-2 text-xs font-bold text-white disabled:opacity-40">
                {voiceBusy ? 'Menyimpan rekaman…' : 'Simpan persona suara'}
              </button>
              {voiceCapability && !voiceCapability.ready && <p className="mt-2 text-[11px] text-warn">Belum aktif: {voiceCapability.message} Job lagu dengan persona akan dihentikan sebelum memakai kredit.</p>}
            </section>}

            {personaMode === 'design' && <VoiceDesigner key={user?.google_sub || user?.email || 'guest'}
              user={user} onSignIn={onSignIn} onSaved={(persona) => {
                setPersonas(prev => [persona, ...prev.filter(p => p.persona_id !== persona.persona_id)])
                setPersonaId(persona.persona_id)
                setPersonaFeedback('Persona suara dipilih untuk lagu berikutnya.')
              }} />}
            </Group>)}

            {!simpleMode && (<Group title="4 · Lirik" subtitle="Tulis, generate, atau edit per bagian" defaultOpen={Boolean(lyrics.trim())}>
            <div className="block">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-bold uppercase tracking-widest text-white/55">
                  Lirik <span className="font-mono normal-case text-white/30">({lyricSungCount}/{lyricTarget} baris)</span>
                </span>
                <div className="flex flex-wrap gap-1.5">
                  <button type="button" onClick={() => writeLyricsAi('compose')} disabled={lyricsAiBusy || working || instrumental}
                    title="Gemini menulis lirik full dari ide — sesuai durasi & bahasa genre"
                    className="rounded-full border border-magenta/40 bg-magenta/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-magenta disabled:opacity-40">
                    {lyricsAiBusy ? '✍️ Menulis…' : '✨ Generate'}
                  </button>
                  <button type="button" onClick={() => writeLyricsAi('expand')} disabled={lyricsAiBusy || working || instrumental || !lyrics.trim()}
                    title="Kembangkan coretanmu menjadi struktur lagu full"
                    className="rounded-full border border-cyan/40 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-cyan disabled:opacity-40">
                    ⤢ Expand
                  </button>
                  <button type="button" onClick={() => writeLyricsAi('refine')} disabled={lyricsAiBusy || working || instrumental || lyricSungCount < 2}
                    title="Perbaiki rima & keternyanyian tanpa mengubah cerita dan struktur"
                    className="rounded-full border border-signal/40 bg-signal/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-signal disabled:opacity-40">
                    🪄 Refine
                  </button>
                  <button type="button" onClick={() => { setLyrics(''); setLyricsAiInfo('') }} disabled={working || instrumental || !lyrics}
                    title="Kosongkan editor lirik"
                    className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/60 transition hover:border-danger/50 disabled:opacity-40">
                    🗑
                  </button>
                </div>
              </div>
              <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                <span className="text-[10px] font-bold uppercase tracking-widest text-white/35">Sisipkan struktur</span>
                {STRUCTURE_TAGS.map((tag) => (
                  <button key={tag} type="button" onClick={() => insertStructureTag(tag)} disabled={instrumental}
                    title={`Sisipkan ${tag} di posisi kursor`}
                    className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[10px] text-white/60 transition hover:border-cyan/50 hover:text-white disabled:opacity-40">
                    {tag}
                  </button>
                ))}
              </div>
              <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-lg border border-white/10 bg-black/15 px-2 py-1.5">
                <span className="font-mono text-[10px] uppercase tracking-widest text-white/35">Draft lirik</span>
                <input value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)}
                  placeholder="Judul draft" aria-label="Judul draft lirik"
                  className="w-40 rounded-md border border-white/10 bg-navy-3 px-2 py-1 text-[11px] text-white outline-none focus:border-cyan/60" />
                <button type="button" onClick={saveLyricDraft} disabled={draftBusy || !user}
                  className="rounded-md border border-cyan/40 bg-cyan/10 px-2 py-1 text-[10px] font-bold text-white/85 transition hover:border-cyan disabled:opacity-40">
                  {draftBusy ? 'Menyimpan.' : currentDraft ? 'Simpan' : 'Buat draft'}
                </button>
                <button type="button" onClick={newLyricDraft}
                  title="Mulai draft baru tanpa menghapus draft lama"
                  className="rounded-md border border-white/15 bg-white/5 px-2 py-1 text-[10px] font-bold text-white/60 transition hover:border-white/30">
                  Baru
                </button>
                {currentDraft && (
                  <button type="button" onClick={removeLyricDraft} disabled={draftBusy}
                    className="rounded-md border border-danger/30 bg-danger/10 px-2 py-1 text-[10px] font-bold text-danger transition hover:border-danger disabled:opacity-40">
                    Hapus
                  </button>
                )}
                {lyricDrafts.slice(0, 4).map((entry) => (
                  <button key={entry.draft_id} type="button" onClick={() => openLyricDraft(entry)}
                    title={`${entry.lyric_chars} karakter - buka draft`}
                    className={`max-w-[140px] truncate rounded-md border px-2 py-1 text-[10px] transition ${
                      currentDraft?.draft_id === entry.draft_id
                        ? 'border-magenta/60 bg-magenta/20 text-white'
                        : 'border-white/10 bg-white/5 text-white/60 hover:border-cyan/50'}`}>
                    {entry.title || 'Draft tanpa judul'}
                  </button>
                ))}
                {draftInfo && <span className="text-[10px] text-cyan-soft">{draftInfo}</span>}
              </div>
              <textarea ref={lyricsRef} value={instrumental ? '[Instrumental — lirik dilewati]' : lyrics} onChange={(e) => { setLyrics(e.target.value); setRequestError('') }} rows={10}
                aria-invalid={Boolean(vis("idea"))}
                disabled={instrumental}
                placeholder="[Verse]&#10;Tulis coretan lirikmu di sini…&#10;[Chorus]&#10;…atau tekan ✨ Generate dari ide di atas"
                className={`w-full resize-y rounded-xl border bg-black/20 p-4 font-mono text-xs leading-6 text-white/85 outline-none transition disabled:opacity-50 placeholder:text-white/25 ${
                  vis("idea") ? 'border-danger/40' : 'border-white/10 focus:border-cyan/60'}`} />
              <span className="mt-1 block font-mono text-[10px] text-white/35">
                {lyrics.trim().length} karakter · {lyricSungCount}/{lyricTarget} baris nyanyi · tag [Verse]/[Chorus]
                {lyricsAiInfo && <span className="text-cyan-soft"> · {lyricsAiInfo}</span>}
              </span>

              {lyricSections.length > 0 && (
                <div className="mt-3 rounded-xl border border-white/10 bg-black/20 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-white/45">Edit satu bagian (AI section editing)</span>
                    <span className="font-mono text-[10px] text-white/35">{lyricSections.length} bagian</span>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,180px)_1fr_auto]">
                    <select value={sectionIndex} onChange={(e) => { setSectionIndex(Number(e.target.value)); setSectionInfo('') }}
                      className="rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                      {lyricSections.map((sec, i) => (
                        <option key={`${sec.header}-${i}`} value={i}>
                          {sec.header || '[Pembuka]'} · {sec.lines.length} baris
                        </option>
                      ))}
                    </select>
                    <input value={sectionInstruction} onChange={(e) => setSectionInstruction(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') rewriteSection() }}
                      placeholder='mis. "jadikan chorus lebih megah dan gospel"'
                      className="rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                    <button type="button" onClick={rewriteSection} disabled={sectionBusy || instrumental || sectionInstruction.trim().length < 3}
                      className="rounded-lg border border-magenta/40 bg-magenta/10 px-3 py-2 text-xs font-bold text-white transition hover:border-magenta disabled:opacity-40">
                      {sectionBusy ? '✏️ Menulis…' : '✏️ Tulis ulang bagian'}
                    </button>
                  </div>
                  {sectionInfo && <p className="mt-2 text-[11px] leading-4 text-signal">{sectionInfo}</p>}
                </div>
              )}
            </div>
            </Group>)}

            <Group title="5 · Aransemen & Hook" subtitle="Rencana seksi, energi, hook">
            <div className="rounded-xl border border-cyan/25 bg-cyan/5 p-3.5">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-[10px] font-bold uppercase tracking-widest text-white/50">Rencana aransemen</span>
                <div className="flex items-center gap-1.5">
                  <label className="flex items-center gap-1 text-[10px] text-white/50">
                    <input type="checkbox" checked={planAi} onChange={(e) => setPlanAi(e.target.checked)} className="accent-cyan" />
                    Saran AI
                  </label>
                  <button type="button" onClick={loadPlan} disabled={planBusy}
                    title="Susun struktur seksi + hook sebelum render"
                    className="rounded-full border border-cyan/40 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-cyan disabled:opacity-40">
                    {planBusy ? '🧠 Menyusun…' : '🧠 Susun rencana'}
                  </button>
                </div>
              </div>

              {planInfo && <p className="mb-2 text-[11px] leading-4 text-cyan-soft">{planInfo}</p>}

              {plan ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-1.5">
                    {(plan.sections || []).map((sec) => (
                      <span key={`${sec.index}-${sec.name}`}
                        title={`${sec.start_sec}s–${sec.end_sec}s · ${sec.vocal} · ${sec.focus}`}
                        className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[10px] font-semibold ${
                          sec.hook ? 'border-magenta/50 bg-magenta/15 text-white' : 'border-white/10 bg-white/5 text-white/70'}`}>
                        <span className="font-mono">{sec.name}</span>
                        <span className="flex gap-[2px]">
                          {[1, 2, 3, 4, 5].map((n) => (
                            <span key={n} className={`h-2.5 w-1 rounded-sm ${n <= sec.energy ? 'bg-cyan' : 'bg-white/15'}`} />
                          ))}
                        </span>
                      </span>
                    ))}
                  </div>
                  {plan.hook?.line && (
                    <p className="text-[11px] leading-4 text-white/70">
                      <span className="mr-1 font-mono text-[10px] uppercase text-white/40">hook</span>
                      “{plan.hook.line}”
                    </p>
                  )}
                  {plan.style_tags && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[10px] uppercase text-white/40">style</span>
                      {plan.style_tags.split(',').map((t) => t.trim()).filter(Boolean).map((t) => (
                        <button key={t} type="button" onClick={() => toggleStyleChip(t)}
                          className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-white/70 transition hover:border-cyan">
                          {t}
                        </button>
                      ))}
                    </div>
                  )}
                  {(plan.hook?.variants || []).length > 0 && (
                    <div className="space-y-1">
                      <span className="font-mono text-[10px] uppercase text-white/40">varian hook</span>
                      {plan.hook.variants.map((v, i) => (
                        <div key={`${v.line}-${i}`} className="flex items-center justify-between gap-2 rounded-lg border border-white/10 bg-black/20 px-2 py-1">
                          <span className="truncate text-[11px] text-white/75">{v.line}</span>
                          <span className="shrink-0 font-mono text-[10px] text-white/45">{v.score}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {planAi && plan.source === 'offline' && (
                    <p className="text-[10px] leading-4 text-white/35">
                      Saran AI tidak tersedia (kuota teks) — memakai rencana offline.
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-[11px] leading-4 text-white/40">
                  Susun struktur (verse → chorus lift, energi per seksi, hook) sebelum render. Deterministik, tanpa GPU; tag-nya ikut dikirim ke engine.
                </p>
              )}
            </div>
            </Group>

            <Group title="Proyek & revisi" subtitle="Simpan form + rencana, buka versi sebelumnya">
            <div className="rounded-xl border border-cyan/25 bg-cyan/5 p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <input value={projectName} onChange={(e) => setProjectName(e.target.value)}
                  placeholder="Nama proyek" aria-label="Nama proyek musik"
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none focus:border-cyan/60" />
                <button type="button" onClick={saveProject} disabled={projectsBusy || !user}
                  className="rounded-lg border border-cyan/40 bg-cyan/10 px-3 py-2 text-xs font-bold text-white transition hover:border-cyan disabled:opacity-40">
                  {projectsBusy ? 'Menyimpan.' : currentProject ? 'Simpan revisi' : 'Simpan proyek'}
                </button>
                {currentProject && (
                  <button type="button" onClick={startNewProject}
                    className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs font-bold text-white/70 transition hover:border-white/30">
                    Proyek baru
                  </button>
                )}
                {currentProject && (
                  <button type="button" onClick={removeProject} disabled={projectsBusy}
                    className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs font-bold text-danger transition hover:border-danger disabled:opacity-40">
                    Hapus
                  </button>
                )}
              </div>

              {!user && (
                <p className="mt-2 text-[11px] leading-4 text-white/45">
                  Masuk untuk menyimpan proyek dan revisinya.
                </p>
              )}
              {projectInfo && <p className="mt-2 text-[11px] leading-4 text-cyan-soft">{projectInfo}</p>}

              {user && projects.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {projects.slice(0, 8).map((entry) => (
                    <button key={entry.project_id} type="button" onClick={() => openProject(entry)}
                      className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-semibold transition ${
                        currentProject?.project_id === entry.project_id
                          ? 'border-magenta/60 bg-magenta/20 text-white'
                          : 'border-white/10 bg-black/20 text-white/65 hover:border-cyan/50'}`}>
                      <span className="max-w-[160px] truncate">{entry.name}</span>
                      <span className="font-mono text-[9px] opacity-60">{entry.revision_count}x</span>
                    </button>
                  ))}
                </div>
              )}

              {currentProject && projectRevisions.length > 0 && (
                <div className="mt-3 space-y-1">
                  <span className="font-mono text-[10px] uppercase text-white/40">Revisi</span>
                  {projectRevisions.map((rev) => (
                    <div key={rev.revision_id}
                      className="flex items-center justify-between gap-2 rounded-lg border border-white/10 bg-black/20 px-2 py-1">
                      <button type="button" onClick={() => loadProjectRevision(rev)}
                        className="min-w-0 flex-1 truncate text-left text-[11px] text-white/75 transition hover:text-white">
                        <span className="font-mono text-white/40">#{rev.revision_index}</span>{' '}
                        {rev.label || 'revisi'}
                      </button>
                      <button type="button" onClick={() => restoreProjectRevision(rev)} disabled={projectsBusy}
                        className="shrink-0 rounded-full border border-white/15 px-2 py-0.5 text-[10px] font-semibold text-white/60 transition hover:border-cyan/50 disabled:opacity-40">
                        Restore
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            </Group>

            <div className="grid gap-3 sm:grid-cols-[1fr_150px]">
              <label className="block">
                <span className="mb-1.5 block text-xs font-bold uppercase tracking-widest text-white/55">Model / engine</span>
                <select value={provider} onChange={(e) => onProviderChange(e.target.value)}
                  className="w-full rounded-xl border border-white/10 bg-navy-3 px-3 py-2.5 text-sm text-white outline-none">
                  {providerOptions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}{item.available ? '' : ' · belum aktif di server'}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex items-end">
                <button type="button" onClick={create} disabled={createBlocked}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#ff5a1f] via-[#ff2d78] to-[#b829ff] px-4 py-2.5 text-sm font-black text-white shadow-lg transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
                  {working ? <><Spinner className="h-4 w-4" /> Membuat lagu…</> : '🎵 Create'}
                </button>
              </div>
            </div>
            <p className="mt-1 text-[11px] leading-5 text-white/45">
              {providerOptions.find((p) => p.id === provider)?.hint}
            </p>

            {!simpleMode && (<Group title="6 · Lanjutan" subtitle="Take A/B, seed, style tags, LoRA, kreativitas">
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className="block">
                      <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">Jumlah take</span>
                      <select value={takeCount} onChange={(e) => setTakeCount(Number(e.target.value))}
                        className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                        {TAKE_COUNTS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">Mode take</span>
                      <select value={takeMode} onChange={(e) => setTakeMode(e.target.value)}
                        className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none">
                        {TAKE_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">Seed</span>
                      <input value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" placeholder="acak"
                        className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                    </label>
                  </div>
                  <div className="block">
                    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-white/45">Style tags tambahan</span>
                      <div className="flex gap-1.5">
                        <button type="button" onClick={rollStyle} title="Gaya acak ala “Use Random Style” Suno"
                          className="rounded-full border border-cyan/40 bg-cyan/10 px-2 py-0.5 text-[10px] font-bold text-white/80 transition hover:border-cyan">
                          🎲 Gaya acak
                        </button>
                        <button type="button" onClick={styleFromHistory} title="Ambil gaya dari riwayat lagumu (ala “My Taste” Suno)"
                          className="rounded-full border border-magenta/40 bg-magenta/10 px-2 py-0.5 text-[10px] font-bold text-white/80 transition hover:border-magenta">
                          ✨ Dari riwayat
                        </button>
                      </div>
                    </div>
                    <input value={styleTags} onChange={(e) => setStyleTags(e.target.value)}
                      placeholder="mis. female vocal, dreamy reverb, 90 bpm, analog warmth"
                      className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {STYLE_CHIPS.map((chip) => {
                        const active = styleTags.split(',').map((s) => s.trim().toLowerCase()).includes(chip.toLowerCase())
                        return (
                          <button key={chip} type="button" onClick={() => toggleStyleChip(chip)}
                            className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold transition ${
                              active ? 'border-cyan/60 bg-cyan/20 text-white' : 'border-white/10 bg-white/5 text-white/55 hover:text-white'}`}>
                            {chip}
                          </button>
                        )
                      })}
                    </div>
                    <span className="mt-1 block text-[10px] leading-4 text-white/35">Ditambahkan ke tag genre. Bila dikosongkan, nilai dari persona akan dipakai.</span>
                  </div>
                  <div className="block">
                    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-white/45">Exclude styles — tag yang dihindari</span>
                      {negativeTags.trim() && <span className="font-mono text-[10px] text-danger/80">-{negativeTags.trim().replace(/,\s*/g, ' -')}</span>}
                    </div>
                    <input value={negativeTags} onChange={(e) => setNegativeTags(e.target.value)}
                      placeholder="mis. male vocals, piano, rap"
                      className="w-full rounded-lg border border-danger/25 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {NEGATIVE_CHIPS.map((chip) => {
                        const active = negativeTags.split(',').map((s) => s.trim().toLowerCase()).includes(chip.toLowerCase())
                        return (
                          <button key={chip} type="button" onClick={() => toggleNegativeChip(chip)}
                            className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold transition ${
                              active ? 'border-danger/60 bg-danger/20 text-white' : 'border-white/10 bg-white/5 text-white/55 hover:text-white'}`}>
                            {chip}
                          </button>
                        )
                      })}
                    </div>
                    <span className="mt-1 block text-[10px] leading-4 text-white/35">Diteruskan ke engine sebagai penanda negatif (-tag) dan arahan “avoid”. Hasil bergantung pada engine.</span>
                  </div>
                  <label className="block">
                    <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-widest text-white/45">LoRA / slot regional</span>
                    <input value={loraId} onChange={(e) => setLoraId(e.target.value)} placeholder="kosong = base model"
                      className="w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white outline-none" />
                    <span className="mt-1 block text-[10px] leading-4 text-white/35">Aktif di server: {engine?.lora_id || 'belum ada'}. Untuk moat regional (mis. Jawa).</span>
                  </label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1.5 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-white/45">
                        <span>Weirdness</span><span className="font-mono text-white/40">{weirdness}</span>
                      </span>
                      <input type="range" min={0} max={100} value={weirdness} onChange={(e) => setWeirdness(Number(e.target.value))} className="w-full accent-magenta" />
                      <span className="mt-1 block text-[10px] leading-4 text-white/35">0 = aman & konvensional · 100 = eksperimental.</span>
                    </label>
                    <label className="block">
                      <span className="mb-1.5 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-white/45">
                        <span>Style influence</span><span className="font-mono text-white/40">{styleInfluence}</span>
                      </span>
                      <input type="range" min={0} max={100} value={styleInfluence} onChange={(e) => setStyleInfluence(Number(e.target.value))} className="w-full accent-cyan" />
                      <span className="mt-1 block text-[10px] leading-4 text-white/35">Seberapa kuat genre/tag gaya diikuti.</span>
                    </label>
                  </div>
                  <p className="text-[11px] leading-5 text-white/40">
                    Seed sama + spesifikasi sama = hasil reproducible. Bila 2 take dipilih, server memakai seed berurutan untuk membandingkan A/B.
                  </p>
                </div>
            </Group>)}
            <p className={`text-center font-mono text-[10px] ${
              engineReady === false || missing.length ? 'text-warn' : 'text-white/35'}`}>
              {readinessText}
              {!missing.length && engineReady !== false && !engineBusy && !working && (
                <> {provider === 'heartmula'
                  ? '🎵 HeartMuLa final · engine asli Klipin Music (Apache 2.0)'
                  : provider === 'diffrhythm'
                    ? '⚡ DiffRhythm full song · latent diffusion cepat (Apache 2.0)'
                    : provider === 'auto'
                      ? '🅰️🅱️ Auto · draft cepat lalu final untuk dibandingkan'
                      : '⚡ ACE-Step draft cepat · untuk mencoba ide'}</>
              )}
            </p>
            {costEstimate && (
              <p className="text-center font-mono text-[10px] text-white/45">
                Estimasi: ±{costEstimate.gpu_sec}s GPU · ±${costEstimate.usd} · ±{costEstimate.credits} kredit
                (aktual dicatat per take)
              </p>
            )}
            {costSummary && costSummary.takes > 0 && (
              <p className="text-center font-mono text-[10px] text-white/30">
                Total kamu: {costSummary.takes} takes · {costSummary.gpu_sec}s GPU · ${costSummary.usd} · {costSummary.credits} kredit
              </p>
            )}
            </>)}
          </div>
        </section>

        {/* Pane 2 (Center): Suno Workspace Song List, Active Takes & Inspire */}
        <section className="space-y-4">
          {/* Active Render / Live Takes Card — always visible during Create OR Cover! */}
          {(running || working || takes.length > 1 || jobError) && (
            <div className="rounded-2xl border border-[#ff2d78]/40 bg-gradient-to-r from-[#26122c] via-[#161926] to-[#111c2d] p-4 shadow-lg">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-2.5 w-2.5 animate-pulse rounded-full bg-[#ff5a1f]" />
                  <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-white/80">
                    {running || working ? 'Live Render Queue' : 'Take Comparison'}
                  </span>
                  {job?.metadata?.cover_mode && (
                    <span className="rounded-full border border-cyan/40 bg-cyan/15 px-2 py-0.5 font-mono text-[9px] font-bold uppercase text-cyan-soft">
                      COVER · {job?.metadata?.cover_engine || job?.provider || 'mulacover'}
                    </span>
                  )}
                </div>
                <span className="font-mono text-[11px] text-white/60">
                  {formatClock(elapsed)} · {progress}%
                </span>
              </div>
              <p className="mt-1 text-xs font-semibold text-white/85">
                {stageText || 'Menghubungkan ke worker GPU L4…'}
              </p>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/50">
                <div className={`h-full rounded-full transition-all duration-500 ${
                  job?.status === 'failed' ? 'bg-danger'
                    : job?.status === 'completed' ? 'bg-signal' : 'bg-gradient-to-r from-[#ff5a1f] via-[#ff2d78] to-cyan'}`}
                  style={{ width: `${progress}%` }} />
              </div>
              {takes.length > 1 && (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {takes.map((t) => {
                    const active = job?.job_id === t.job_id
                    const meta = STATUS_META[t.status] || null
                    const label = t.provider?.includes('diffrhythm')
                      ? `⚡ DiffRhythm · Take ${Number(t.take_index || 0) + 1}`
                      : t.take_mode === 'draft' ? '⚡ A · Draft ACE-Step' : '🎤 B · Final HeartMuLa'
                    return (
                      <button key={t.job_id} type="button" onClick={() => selectTake(t)}
                        className={`rounded-xl border p-2.5 text-left transition ${active ? 'border-[#ff2d78] bg-[#ff2d78]/15' : 'border-white/10 bg-black/25 hover:border-[#ff2d78]/50'}`}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-bold text-white">{label}</span>
                          {meta && (
                            <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase ${meta.className}`}>{meta.label}</span>
                          )}
                        </div>
                        <div className="mt-1 font-mono text-[10px] text-white/40">
                          seed {t.seed} · {t.provider || '…'} · {t.metadata?.stage || t.status}
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
              {jobError && (
                <div className="mt-3">
                  <Notice tone="err" title="Job gagal" text={jobError}
                    action={{ label: 'Coba lagi', onClick: create }} />
                </div>
              )}
            </div>
          )}

          {/* Suno Workspace Song Feed Card */}
          <div className="card-chrome rounded-2xl border border-white/10 bg-[#12141d] p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-3">
              <div>
                <h3 className="text-sm font-black tracking-tight text-white">Riwayat lagu & Workspace</h3>
                <p className="text-[11px] text-white/45">
                  Klik lagu untuk membuka player, waveform, dan lirik karaoke di panel kanan.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <input value={historyQuery}
                  onChange={(e) => { setHistoryQuery(e.target.value); loadHistory(e.target.value) }}
                  placeholder="Cari judul/genre…" aria-label="Cari riwayat"
                  className="w-40 rounded-full border border-white/10 bg-black/30 px-3 py-1.5 text-[11px] text-white outline-none focus:border-cyan/60" />
                {historyBusy && <Spinner className="h-3.5 w-3.5" />}
                <button type="button" onClick={() => loadHistory(historyQuery)} disabled={historyBusy || !user}
                  className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/70 transition hover:border-cyan/50 disabled:opacity-40">
                  ↻ Segarkan
                </button>
              </div>
            </div>

            {!user ? (
              <p className="py-6 text-center text-sm text-white/40">
                Masuk untuk melihat riwayat lagu dan memutar ulang hasil sebelumnya.
              </p>
            ) : historyList.length === 0 ? (
              <p className="py-6 text-center text-sm text-white/40">
                Belum ada lagu. Take pertama yang dibuat akan tercatat di sini beserta
                status dan pesan error-nya bila gagal.
              </p>
            ) : (
              <div className="max-h-[680px] space-y-2 overflow-y-auto pr-1">
                {historyList.map((entry) => {
                  const meta = STATUS_META[entry.status] || null
                  const label = entry.metadata?.genre_label || entry.genre || '—'
                  const lang = entry.metadata?.language_label || entry.language || '—'
                  const thumb = entry.cover_path || entry.metadata?.cover_path || ''
                  const isSelected = job?.job_id === entry.job_id
                  const isCover = Boolean(entry.metadata?.cover_mode || String(entry.provider || '').includes('mulacover') || String(entry.provider || '').includes('voice'))
                  return (
                    <div key={entry.job_id} role="button" tabIndex={0}
                      onClick={() => openHistoryJob(entry)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          openHistoryJob(entry)
                        }
                      }}
                      title={entry.status === 'completed' ? 'Buka di player' : 'Lihat detail & error'}
                      className={`group flex items-center gap-3.5 rounded-xl border p-2.5 text-left transition ${
                        isSelected
                          ? 'border-[#ff2d78]/60 bg-gradient-to-r from-[#ff2d78]/15 to-cyan/10 shadow-md'
                          : 'border-white/10 bg-black/25 hover:border-white/25 hover:bg-white/[0.04]'}`}>
                      <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-lg border border-white/15 bg-gradient-to-br from-magenta/20 to-cyan/20">
                        {thumb ? (
                          <img src={resolveMediaAsset(thumb)} alt="" loading="lazy"
                            className="h-full w-full object-cover" />
                        ) : (
                          <span className="flex h-full w-full items-center justify-center">
                            <Icon name="music" className="h-5 w-5 text-white/50" />
                          </span>
                        )}
                        {isSelected && playing && (
                          <span className="absolute inset-0 flex items-center justify-center bg-black/55">
                            <Equalizer playing bars={4} className="h-5 w-6" />
                          </span>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-sm font-bold text-white">
                              {entry.title || entry.job_id}
                            </span>
                            {isCover && (
                              <span className="shrink-0 rounded border border-cyan/35 bg-cyan/10 px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase text-cyan-soft">
                                COVER
                              </span>
                            )}
                            {entry.provider && (
                              <span className="hidden shrink-0 rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[8px] text-white/55 sm:inline">
                                {entry.provider}
                              </span>
                            )}
                          </div>
                          {meta && (
                            <span className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase ${meta.className}`}>
                              {meta.label}
                            </span>
                          )}
                        </div>
                        <span className="mt-0.5 block font-mono text-[10px] text-white/45">
                          {label} · {lang} · {formatClock(entry.metadata?.duration || entry.duration || 0)}
                          {typeof entry.created_at === 'number' && (
                            <> · {formatHistoryStamp(entry.created_at)}</>
                          )}
                        </span>
                        {entry.status === 'completed' && entry.audio_path && (
                          <span className="mt-1.5 block" onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}>
                            <audio src={resolveMediaAsset(entry.audio_path)} controls
                              preload="none" className="h-8 w-full" />
                          </span>
                        )}
                        {entry.status === 'failed' && entry.error && (
                          <span className="mt-1.5 line-clamp-2 rounded bg-danger/10 p-1.5 text-[11px] leading-4 text-danger">
                            {entry.error}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Suno Inspire Bar */}
          <div className="card-chrome rounded-2xl border border-white/10 bg-[#12141d] p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-white">✨ Inspire — buat dari lagu acuan</h3>
              <button type="button" onClick={applyInspire} disabled={!inspireIds.length || !user}
                className="rounded-full border border-magenta/40 bg-magenta/10 px-3 py-1 text-[11px] font-bold text-white transition hover:border-magenta disabled:opacity-40">
                Terapkan ke form ({inspireIds.length})
              </button>
            </div>
            <p className="mb-2.5 text-[11px] leading-5 text-white/45">
              Pilih beberapa lagu yang kamu suka untuk menggabungkan gaya, genre dominan, BPM, dan key-nya.
            </p>
            {!user || historyList.length === 0 ? (
              <p className="text-xs text-white/40">Belum ada lagu acuan. Lagu selesai akan muncul di sini.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {historyList.filter((e) => e.status === 'completed').slice(0, 12).map((entry) => {
                  const on = inspireIds.includes(entry.job_id)
                  return (
                    <button key={entry.job_id} type="button" onClick={() => toggleInspire(entry.job_id)}
                      className={`rounded-full border px-3 py-1 text-[11px] font-semibold transition ${
                        on ? 'border-magenta/60 bg-magenta/20 text-white' : 'border-white/10 bg-white/5 text-white/55 hover:text-white'}`}>
                      {on ? '✓ ' : ''}{entry.title || entry.job_id}
                    </button>
                  )
                })}
              </div>
            )}
            {inspireInfo && <p className="mt-2 text-[11px] leading-4 text-signal">{inspireInfo}</p>}
          </div>

          {feed.length > 0 && (
            <div className="card-chrome rounded-2xl border border-white/10 bg-[#12141d] p-4">
              <h3 className="mb-1 text-sm font-bold text-white">🌐 Feed publik</h3>
              <p className="mb-3 text-xs text-white/40">
                Lagu yang dibagikan kreator lain — tekan Share pada lagumu untuk tampil di sini.
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {feed.map((song) => (
                  <div key={song.job_id} className="flex items-start gap-3 rounded-xl border border-white/10 bg-black/20 p-3">
                    {song.cover_path ? (
                      <img src={resolveMediaAsset(song.cover_path)} alt="" loading="lazy"
                        className="h-12 w-12 shrink-0 rounded-lg border border-white/15 object-cover" />
                    ) : (
                      <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-gradient-to-br from-magenta/20 to-cyan/20">
                        <Icon name="music" className="h-5 w-5 text-white/50" />
                      </span>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-bold text-white">{song.title}</div>
                      <div className="mt-0.5 font-mono text-[10px] text-white/40">
                        {song.genre} · {song.language} · {formatClock(song.duration || 0)}
                      </div>
                      {song.audio_path && (
                        <audio src={resolveMediaAsset(song.audio_path)} controls preload="none" className="mt-1.5 h-8 w-full" />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Pane 3 (Right): Suno Song Inspector, Cover Art Hero, Waveform & Karaoke Lyrics */}
        <section className="space-y-4">
          <div className="relative overflow-hidden rounded-2xl border border-magenta/30 bg-gradient-to-br from-[#28112f] via-navy-2 to-[#101e3d] p-5 shadow-neon-magenta/20">
            <div className="absolute -right-10 -top-10 h-48 w-48 rounded-full bg-magenta/20 blur-3xl" />
            <div className="absolute -bottom-16 left-10 h-48 w-48 rounded-full bg-cyan/20 blur-3xl" />

            <div className="relative space-y-4">
              <div className="flex items-start gap-4">
                <div className="relative h-32 w-32 shrink-0 overflow-hidden rounded-2xl border border-white/20 bg-black/40 shadow-neon-magenta/30">
                  {coverSrc
                    ? <img src={coverSrc} alt="Cover art" className="h-full w-full object-cover" />
                    : (
                      <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-magenta/30 to-cyan/30">
                        <Icon name="music" className="h-10 w-10 text-white/60" />
                      </div>
                    )}
                  {coverSource && (
                    <span className="absolute bottom-1.5 left-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[8px] text-white/70 shadow-sm">
                      {String(coverSource).startsWith('pexels') ? 'pexels' : coverSource}
                    </span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-[10px] uppercase tracking-[.22em] text-white/45">
                      {running ? 'Now creating'
                        : job?.status === 'completed' ? 'Hasil terakhir'
                          : job ? 'Status job' : 'Belum ada lagu'}
                    </span>
                    {statusMeta && (
                      <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase ${statusMeta.className}`}>
                        {statusMeta.label}
                      </span>
                    )}
                  </div>
                  <h2 className="mt-1.5 line-clamp-2 text-lg font-black leading-snug text-white">
                    {job?.title || title || 'Your next song'}
                  </h2>
                  <p className="mt-1 text-xs text-white/55">
                    {(job?.metadata?.genre_label || job?.genre_label || job?.genre
                      ? (job?.metadata?.genre_label || job?.genre_label || job?.genre)
                      : (genreInfo?.label || genre))}{' · '}
                    {(job?.metadata?.language_label || job?.language_label || job?.language
                      ? (job?.metadata?.language_label || job?.language_label || job?.language)
                      : languageLabel)}
                    {job?.metadata?.language_secondary_label ? ` + ${job.metadata.language_secondary_label}` : ''}
                    {job?.vocal_gender ? ` · ${(VOCAL_GENDERS.find((g) => g.id === job.vocal_gender) || {}).label || job.vocal_gender}` : ''}
                    {' · '}{job?.provider || 'belum ada provider'}
                  </p>
                  {job?.job_id && (
                    <p className="mt-0.5 font-mono text-[10px] text-white/30">job {job.job_id}</p>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-1.5">
                <button type="button" onClick={() => regenerateCover()}
                  disabled={coverBusy || job?.status !== 'completed'}
                  title="Ganti cover (cari foto profesional baru)"
                  className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/80 transition hover:border-cyan/50 disabled:opacity-40">
                  {coverBusy ? 'Mencari…' : '↻ Ganti cover'}
                </button>
                <label className="cursor-pointer rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/80 transition hover:border-cyan/50"
                  title="Upload gambar sendiri (max 8 MB)">
                  ⬆ Upload
                  <input type="file" accept="image/*" onChange={pickCoverFile} className="hidden" />
                </label>
                {job?.audio_path && (
                  <a href={audioUrl} download={`${job.title || 'klipin-music'}.mp3`}
                    className="rounded-full border border-cyan/30 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-cyan-soft transition hover:border-cyan/60">
                    ⬇ Unduh MP3
                  </a>
                )}
                {job?.lyrics_path && (
                  <a href={resolveMediaAsset(job.lyrics_path)} download="lyrics.lrc"
                    className="rounded-full border border-cyan/30 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-cyan-soft transition hover:border-cyan/60">
                    ⬇ .LRC
                  </a>
                )}
                {wavUrl && (
                  <a href={wavUrl} download="song.wav"
                    className="rounded-full border border-cyan/30 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-cyan-soft transition hover:border-cyan/60">
                    ⬇ WAV
                  </a>
                )}
                {job?.status === 'completed' && (
                  <>
                    <button type="button" onClick={extendSong} disabled={extendBusy}
                      title="Lanjutkan lagu +60 dtk dari take ini"
                      className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/80 transition hover:border-cyan/50 disabled:opacity-40">
                      {extendBusy ? '…' : '⤢ Extend +60s'}
                    </button>
                    <button type="button" onClick={loadStems} disabled={stemsBusy}
                      title="Pisahkan vokal & musik (Demucs)"
                      className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[10px] font-bold text-white/80 transition hover:border-cyan/50 disabled:opacity-40">
                      {stemsBusy ? '…' : '🎚️ Stems'}
                    </button>
                    <button type="button" onClick={savePersona}
                      title="Simpan genre, gender vokal, dan seed lagu ini sebagai preset gaya; bukan kloning suara"
                      className="rounded-full border border-magenta/40 bg-magenta/10 px-2.5 py-1 text-[10px] font-bold text-white/85 transition hover:border-magenta disabled:opacity-40">
                      🎭 Simpan persona gaya
                    </button>
                    <button type="button" onClick={coverSong} disabled={coverSongBusy}
                      title={`Pertahankan lirik dan melodi; ubah vokal serta aransemen ke ${genreInfo?.label || genre}`}
                      className="rounded-full border border-cyan/30 bg-cyan/10 px-2.5 py-1 text-[10px] font-bold text-cyan-soft transition hover:border-cyan/60 disabled:opacity-40">
                      {coverSongBusy ? '…' : '🔁 Cover faithful'}
                    </button>
                    <button type="button" onClick={toggleShare} disabled={shareBusy}
                      title="Bagikan ke feed publik / tarik kembali"
                      className={`rounded-full border px-2.5 py-1 text-[10px] font-bold transition disabled:opacity-40 ${
                        job.is_public
                          ? 'border-signal/50 bg-signal/10 text-signal'
                          : 'border-white/15 bg-white/5 text-white/80 hover:border-cyan/50'}`}>
                      {shareBusy ? '…' : job.is_public ? '🌐 Publik ✓' : '🔗 Share'}
                    </button>
                  </>
                )}
              </div>

              {stemsInfo && (
                <p className="font-mono text-[10px] text-white/45">{stemsInfo}</p>
              )}
              {job?.metadata?.voice_conversion?.status === 'applied' && (
                <p className="text-[11px] text-signal">✓ Persona suara diterapkan pada vokal.</p>
              )}
              {job?.metadata?.voice_conversion?.status === 'not_applied' && (
                <p role="status" className="rounded-lg border border-warn/30 bg-warn/10 p-2 text-[11px] leading-5 text-warn">
                  Lagu berhasil dibuat, tetapi persona suara tidak berhasil diterapkan; audio asli tetap tersedia. {job.metadata.voice_conversion.message || ''}
                </p>
              )}
              {job?.metadata?.lyrics_source && (
                <p className="font-mono text-[10px] text-white/30">
                  lirik: {job.metadata.lyrics_source}{job.metadata.alignment ? ` · align:${job.metadata.alignment}` : ''}{job.metadata.seed != null ? ` · seed:${job.metadata.seed}` : ''}
                  {job.metadata.bpm_detected ? ` · ${job.metadata.bpm_detected} BPM` : ''}
                  {job.metadata.key_detected ? ` · ${job.metadata.key_detected}` : ''}
                  {job.metadata.instrumental ? ' · instrumental' : ''}
                  {job.metadata.gpu_sec ? ` · ${job.metadata.gpu_sec}s GPU` : ''}
                </p>
              )}
              {job?.metadata?.language_secondary && (
                <p className="rounded-lg border border-warn/25 bg-warn/5 px-2 py-1.5 text-[11px] leading-4 text-warn">
                  Dual language: {job.metadata.language_label} → {job.metadata.language_secondary_label || job.metadata.language_secondary} · {job.metadata.language_mix_label || job.metadata.language_mix}. Review pelafalan Jawa oleh penutur asli.
                </p>
              )}

              {job?.status === 'completed' ? (
                <div className="space-y-3 pt-1">
                  <audio ref={audioRef} src={audioUrl} onEnded={() => setPlaying(false)}
                    onTimeUpdate={onTimeUpdate} onLoadedMetadata={onLoadedMetadata} className="hidden" />
                  <MusicWaveform
                    src={audioUrl}
                    audioRef={audioRef}
                    playing={playing}
                    currentTime={currentTime}
                    duration={songDuration || job?.duration || duration}
                  />
                  <div className="flex items-center gap-3">
                    <button type="button" onClick={togglePlay}
                      className="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-xs font-bold text-navy shadow-lg">
                      <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
                      {playing ? 'Pause preview' : 'Play preview'}
                    </button>
                    <span className="font-mono text-[11px] text-white/45">
                      {formatTime(currentTime)} / {formatTime(songDuration || job?.duration || duration)}
                    </span>
                  </div>
                  <input type="range" min={0}
                    max={Math.max(1, songDuration || job?.duration || duration)} step={0.1}
                    value={Math.min(currentTime, songDuration || job?.duration || duration)}
                    onChange={(e) => seekTo(Number(e.target.value))}
                    aria-label="Seek" className="w-full accent-cyan" />
                </div>
              ) : (
                <div>
                  <Equalizer playing={running} bars={36} className="h-12" />
                  {!job && (
                    <p className="mt-2 font-mono text-[11px] uppercase tracking-widest text-white/45">
                      Tekan Create atau pilih lagu di tengah untuk memutar
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Synchronized Karaoke Lyrics Timeline Card */}
          <div className="card-chrome rounded-2xl border border-white/10 bg-[#12141d] p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-sm font-bold text-white">
                Lyrics timeline
                <Equalizer playing={playing} bars={10} className="h-3.5 w-14" />
              </h3>
              <div className="flex items-center gap-1.5">
                {job?.metadata?.beat_bpm && (
                  <span title={job.metadata.beat_source === 'beat-this'
                    ? 'Beat grid + downbeat dari model Beat This!'
                    : 'Beat grid estimasi onset-autocorr'}
                    className="rounded-full border border-magenta/30 bg-magenta/10 px-2 py-0.5 font-mono text-[10px] text-white/70">
                    {Math.round(job.metadata.beat_bpm)} BPM
                  </span>
                )}
                {job?.status === 'completed' && lyricsLines.length > 0 && (
                  <button type="button" onClick={realignTimeline} disabled={realignBusy}
                    title="Hitung ulang word alignment + beat grid dari audio"
                    className="rounded-full border border-cyan/30 bg-cyan/10 px-2 py-0.5 font-mono text-[10px] text-cyan-soft transition hover:border-cyan disabled:opacity-40">
                    {realignBusy ? 'Aligning.' : 'Align ulang'}
                  </button>
                )}
                <span className="rounded-full bg-cyan/10 px-2 py-0.5 font-mono text-[10px] text-cyan-soft">
                  {lyricsLines.length || '-'} lines
                </span>
              </div>
            </div>
            {realignInfo && (
              <p className="mb-2 text-[11px] leading-4 text-cyan-soft">{realignInfo}</p>
            )}
            {lyricsLines.length ? (
              <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
                {lyricsLines.map((line, i) => {
                  const active = i === activeIndex
                  return (
                    <button key={`${line.start}-${i}`} type="button"
                      ref={active ? activeLineRef : undefined}
                      onClick={() => seekTo(line.start)}
                      title="Klik untuk lompat ke baris ini"
                      className={`flex w-full gap-2.5 rounded-lg border px-3 py-2 text-left transition ${
                        active
                          ? 'border-cyan/60 bg-cyan/15 shadow-neon-cyan/20'
                          : 'border-white/5 bg-black/15 hover:border-cyan/30'}`}>
                      <span className="w-11 shrink-0 font-mono text-[10px] text-cyan-soft/60">
                        {formatTime(line.start)}
                      </span>
                      {active && <Equalizer playing={playing} bars={4} className="h-4 w-5 shrink-0" />}
                      <span className={`text-xs leading-5 ${active ? 'font-bold text-white' : 'text-white/75'}`}>
                        {active && Array.isArray(line.words) && line.words.length ? (
                          line.words.map((word, wi) => {
                            const singing = currentTime >= word.start && currentTime < word.end
                            const sung = currentTime >= word.end
                            return (
                              <span key={`${word.start}-${wi}`}
                                className={singing ? 'text-cyan-soft' : sung ? 'text-white' : 'text-white/45'}>
                                {word.text}{wi < line.words.length - 1 ? ' ' : ''}
                              </span>
                            )
                          })
                        ) : line.text}
                        {line.language && job?.metadata?.language_secondary && (
                          <small className="ml-2 rounded border border-white/10 px-1 font-mono text-[9px] uppercase text-cyan-soft/70">{line.language}</small>
                        )}
                      </span>
                    </button>
                  )
                })}
              </div>
            ) : (
              <p className="text-xs leading-5 text-white/40">
                Lirik akan muncul di sini dengan timestamp karaoke setelah lagu selesai.
                Ekspor .LRC siap dipakai di editor video.
              </p>
            )}
          </div>
        </section>
      </div>

      <div className="mt-6 grid gap-3 text-xs text-white/45 sm:grid-cols-3">
        <InfoCard title="HeartMuLa & DiffRhythm"
          text="HeartMuLa & DiffRhythm (Apache 2.0) berjalan di worker Modal L4 terpisah dari Klipin Video; 100% legal untuk komersialisasi." />
        <InfoCard title="Genre → bahasa vokal"
          text="Genre daerah mengunci bahasa vokal (Pop Jawa → Jawa). Nyalakan “Timpa bahasa vokal” untuk memilih sendiri." />
        <InfoCard title="Cover art profesional"
          text="Cover otomatis dari Pexels (gratis) sesuai genre & mood, bisa diganti atau upload sendiri." />
      </div>

      {/* Sticky Bottom Suno Transport Bar */}
      {job?.status === 'completed' && audioUrl && (
        <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-white/15 bg-[#0c0e15]/95 px-4 py-2.5 shadow-2xl backdrop-blur-xl">
          <div className="mx-auto flex max-w-[1680px] flex-wrap items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="h-11 w-11 shrink-0 overflow-hidden rounded-lg border border-white/15 bg-black/40">
                {coverSrc ? (
                  <img src={coverSrc} alt="" className="h-full w-full object-cover" />
                ) : (
                  <span className="flex h-full w-full items-center justify-center">
                    <Icon name="music" className="h-5 w-5 text-white/50" />
                  </span>
                )}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-bold text-white">{job.title || 'Klipin Song'}</div>
                <div className="truncate font-mono text-[10px] text-white/45">
                  {job?.metadata?.genre_label || job?.genre} · {job?.provider || 'heartmula'}
                </div>
              </div>
            </div>

            <div className="flex min-w-[260px] flex-1 max-w-2xl items-center gap-3">
              <button type="button" onClick={togglePlay}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-r from-[#ff5a1f] to-[#ff2d78] text-white shadow-md transition hover:scale-105">
                <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
              </button>
              <span className="w-10 text-right font-mono text-[10px] text-white/55">{formatTime(currentTime)}</span>
              <input type="range" min={0}
                max={Math.max(1, songDuration || job?.duration || duration)} step={0.1}
                value={Math.min(currentTime, songDuration || job?.duration || duration)}
                onChange={(e) => seekTo(Number(e.target.value))}
                aria-label="Seek bottom player" className="flex-1 accent-[#ff2d78]" />
              <span className="w-10 font-mono text-[10px] text-white/55">{formatTime(songDuration || job?.duration || duration)}</span>
            </div>

            <div className="hidden items-center gap-1.5 sm:flex">
              <a href={audioUrl} download={`${job.title || 'klipin-music'}.mp3`}
                className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-[10px] font-bold text-white/80 hover:border-cyan/50">
                ⬇ MP3
              </a>
              {wavUrl && (
                <a href={wavUrl} download="song.wav"
                  className="rounded-full border border-cyan/30 bg-cyan/10 px-3 py-1 text-[10px] font-bold text-cyan-soft hover:border-cyan">
                  ⬇ WAV -14 LUFS
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}



function Notice({ tone, title, text, action }) {
  const styles = {
    err: 'border-danger/40 bg-danger/10 text-danger',
    warn: 'border-warn/40 bg-warn/10 text-warn',
    ok: 'border-signal/40 bg-signal/10 text-signal',
  }[tone || 'warn']
  return (
    <div className={`rounded-xl border p-3.5 ${styles}`}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <b className="text-xs uppercase tracking-widest">{title}</b>
        {action && (
          <button type="button" onClick={action.onClick}
            className="shrink-0 rounded-full border border-current px-2.5 py-0.5 text-[10px] font-bold opacity-80 transition hover:opacity-100">
            {action.label}
          </button>
        )}
      </div>
      <p className="whitespace-pre-wrap text-xs leading-5">{text}</p>
    </div>
  )
}

function formatTime(value) {
  const s = Math.max(0, Number(value) || 0)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

function formatClock(seconds) {
  return formatTime(seconds)
}

function formatHistoryStamp(seconds) {
  const date = new Date(Number(seconds) * 1000)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('id-ID', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function InfoCard({ title, text }) {
  return (
    <div className="rounded-xl border border-white/10 bg-navy-2/60 p-4">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-widest text-cyan-soft">{title}</div>
      <p>{text}</p>
    </div>
  )
}

function Equalizer({ playing = false, bars = 28, className = '' }) {
  return (
    <div className={`eq-bars ${playing ? 'eq-live' : ''} ${className}`} aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => (
        <span key={i} className="eq-bar"
          style={{
            animationDelay: `${(i % 8) * 70}ms`,
            animationDuration: `${680 + (i % 5) * 130}ms`,
            height: `${28 + ((i * 13) % 62)}%`,
          }} />
      ))}
    </div>
  )
}
