import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createDesignedMusicPersona,
  createVoicePreview,
  fetchVoiceAudio,
  getPersonaAudioUrl,
  getVoicePreview,
  listVoicePreviews,
  saveVoicePreview,
} from '../api.js'

const nextSeed = () => window.crypto.getRandomValues(new Uint32Array(1))[0] % 2147483648
const options = {
  vocal_gender: [['female', '👩 Suara Cewek (Perempuan)'], ['male', '👨 Suara Cowok (Laki-laki)']],
  texture: [['smooth', 'Jernih & halus (Smooth)'], ['breathy', 'Lembut & berembus (Breathy)'], ['raspy', 'Serak tipis berkarakter (Raspy)']],
  energy: [['balanced', 'Seimbang (Balanced)'], ['soft', 'Lembut & intim (Soft)'], ['powerful', 'Bertenaga & lantang (Powerful)']],
  register: [['mid', 'Sedang (Mid)'], ['low', 'Rendah / Dalam (Low)'], ['high', 'Tinggi / Melengking (High)']],
}
const labels = {
  vocal_gender: 'Kategori suara (Cewek / Cowok)',
  texture: 'Tekstur vokal',
  energy: 'Energi nyanyian',
  register: 'Rentang nada (Register)',
}
const initial = () => ({
  vocal_gender: 'female',
  warmth: 20,
  brightness: 15,
  texture: 'smooth',
  energy: 'balanced',
  register: 'mid',
  seed: nextSeed(),
})
const signature = (s) => JSON.stringify(['vocal_gender', 'warmth', 'brightness', 'texture', 'energy', 'register', 'seed'].map((k) => s?.[k]))
const summary = (s) => ['vocal_gender', 'texture', 'register'].map((k) => options[k]?.find(([v]) => v === s?.[k])?.[1] || s?.[k]).filter(Boolean).join(' · ')

export function PrivateVoicePlayer({ id, savedPersona = false, onHeard, autoPlay = true, title = '' }) {
  const streamUrl = useMemo(() => (id ? getPersonaAudioUrl(id, savedPersona) : ''), [id, savedPersona])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const audioRef = useRef(null)

  useEffect(() => {
    setError('')
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [id, savedPersona])

  useEffect(() => {
    if (!audioRef.current || !streamUrl || !autoPlay) return
    const a = audioRef.current
    const p = a.play()
    if (p !== undefined) {
      p.then(() => {
        setIsPlaying(true)
        onHeard?.()
      }).catch(() => {
        // Autoplay policy may require manual click on some browsers
        setIsPlaying(false)
      })
    }
  }, [streamUrl, autoPlay, onHeard])

  const togglePlay = () => {
    if (!audioRef.current) return
    if (audioRef.current.paused) {
      audioRef.current.play()
        .then(() => {
          setIsPlaying(true)
          onHeard?.()
        })
        .catch((err) => {
          console.warn('Audio play error:', err)
          setError('Klik untuk memutar audio.')
        })
    } else {
      audioRef.current.pause()
      setIsPlaying(false)
    }
  }

  const formatSec = (s) => {
    if (!Number.isFinite(s) || s <= 0) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-emerald-500/35 bg-emerald-950/30 p-2.5 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <button
            type="button"
            disabled={loading || !streamUrl}
            onClick={togglePlay}
            aria-label={isPlaying ? 'Jeda suara' : 'Putar suara'}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-400 text-black font-bold shadow hover:bg-emerald-300 transition disabled:opacity-50"
          >
            {loading ? (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-black border-t-transparent" />
            ) : isPlaying ? (
              <span className="text-xs">⏸</span>
            ) : (
              <span className="text-xs ml-0.5">▶</span>
            )}
          </button>
          <div className="min-w-0">
            <div className="truncate text-xs font-semibold text-emerald-300">
              {title || (savedPersona ? '🎙️ Contoh Suara Persona' : '🎧 Audisi Karakter Suara')}
            </div>
            <div className="font-mono text-[10px] text-emerald-400/75">
              {loading ? 'Menyiapkan audio…' : `${formatSec(currentTime)} / ${formatSec(duration || 0)}`}
            </div>
          </div>
        </div>
        {isPlaying && (
          <div className="flex items-end gap-0.5 h-4 px-2" aria-hidden>
            <span className="w-1 bg-emerald-400 animate-pulse h-2 rounded-full" />
            <span className="w-1 bg-emerald-400 animate-pulse h-4 rounded-full delay-75" />
            <span className="w-1 bg-emerald-400 animate-pulse h-3 rounded-full delay-150" />
            <span className="w-1 bg-emerald-400 animate-pulse h-4 rounded-full delay-100" />
          </div>
        )}
      </div>

      {streamUrl && (
        <audio
          ref={audioRef}
          src={streamUrl}
          preload="auto"
          onPlay={() => { setIsPlaying(true); onHeard?.() }}
          onPause={() => setIsPlaying(false)}
          onEnded={() => { setIsPlaying(false); setCurrentTime(0) }}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => {
            setDuration(e.currentTarget.duration)
            setLoading(false)
          }}
          onCanPlay={() => setLoading(false)}
          onError={() => {
            setError('Audio belum dapat dimuat.')
            setLoading(false)
          }}
          className="hidden"
        />
      )}

      {duration > 0 && (
        <input
          type="range"
          min={0}
          max={duration || 1}
          step={0.1}
          value={currentTime}
          onChange={(e) => {
            const t = Number(e.target.value)
            setCurrentTime(t)
            if (audioRef.current) audioRef.current.currentTime = t
          }}
          className="h-1 w-full accent-emerald-400 cursor-pointer"
        />
      )}

      {error && <p role="alert" className="text-[11px] text-danger">{error}</p>}
    </div>
  )
}

export default function VoiceDesigner({ user, onSignIn, onSaved }) {
  const [spec, setSpec] = useState(initial)
  const [items, setItems] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [heard, setHeard] = useState({})
  const [name, setName] = useState('')
  const [capability, setCapability] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [instantSaving, setInstantSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const selected = items.find((p) => p.preview_id === selectedId)
  const active = items.find((p) => ['queued', 'processing'].includes(p.status))
  const existing = items.find((p) => signature(p.spec) === signature(spec) && p.status === 'completed')
  const upsert = useCallback((row) => setItems((prev) => [row, ...prev.filter((p) => p.preview_id !== row.preview_id)]), [])

  const refresh = useCallback(async () => {
    if (!user) return
    try {
      const data = await listVoicePreviews()
      setCapability(data.capability)
      setItems(data.previews || [])
      if (!mounted.current && data.previews?.length) {
        setSelectedId(data.previews[0].preview_id)
        setSpec((prev) => ({
          ...prev,
          ...(data.previews[0].spec || {}),
          vocal_gender: data.previews[0].spec?.vocal_gender === 'male' ? 'male' : 'female',
        }))
      }
      mounted.current = true
    } catch {
      // Jangan tampilkan error "Not Found" saat inisialisasi; simpan persona karakter tetap berjalan instan.
      setCapability({ ready: false })
    }
  }, [user])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    if (!active) return undefined
    let alive = true
    let timer
    const poll = async () => {
      try {
        const row = await getVoicePreview(active.preview_id)
        if (!alive) return
        upsert(row)
        if (row.status === 'completed') {
          setSelectedId(row.preview_id)
          setNotice('Preview suara siap. Dengarkan lalu simpan dengan nama pilihanmu.')
          return
        }
        if (row.status === 'failed') {
          setError(row.error)
          return
        }
      } catch (e) {
        if (alive) setError(`Status belum terhubung: ${e.message}. Pemeriksaan akan dicoba kembali.`)
      }
      if (alive) timer = window.setTimeout(poll, 3000)
    }
    timer = window.setTimeout(poll, 1500)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [active?.preview_id, upsert])

  const saveInstantDesign = async () => {
    if (!user) {
      onSignIn?.()
      return
    }
    if (instantSaving) return
    setInstantSaving(true)
    setError('')
    setNotice('')
    try {
      const defaultLabel = spec.vocal_gender === 'male' ? 'Penyanyi Cowok' : 'Penyanyi Cewek'
      const activePreviewId = selected?.status === 'completed' ? selected.preview_id : undefined
      const res = await createDesignedMusicPersona({
        name: name.trim() || `${defaultLabel} (${spec.texture})`,
        vocal_gender: spec.vocal_gender === 'male' ? 'male' : 'female',
        warmth: Number(spec.warmth) || 0,
        brightness: Number(spec.brightness) || 0,
        texture: spec.texture || 'smooth',
        energy: spec.energy || 'balanced',
        register: spec.register || 'mid',
        preview_id: activePreviewId,
      })
      if (res?.persona) {
        setNotice(`✓ Persona "${res.persona.name}" (${res.persona.vocal_gender === 'male' ? '👨 Cowok' : '👩 Cewek'}) berhasil disimpan! Kamu bisa membuat persona baru sebanyak mungkin tanpa batas.`)
        setName('')
        setSpec((prev) => ({ ...prev, seed: nextSeed() }))
        onSaved?.(res.persona)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setInstantSaving(false)
    }
  }

  const generate = async (variation = false) => {
    if (!user) {
      onSignIn?.()
      return
    }
    if (active || busy) return
    if (existing && !variation) {
      setSelectedId(existing.preview_id)
      return
    }
    const draft = variation ? { ...spec, seed: nextSeed() } : spec
    setSpec(draft)
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const row = await createVoicePreview(draft)
      upsert(row)
      setSelectedId(row.preview_id)
      if (row.status === 'failed') setError(row.error)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const savePreviewPersona = async () => {
    if (!selected || saving) return
    setSaving(true)
    setError('')
    try {
      const defaultLabel = selected.spec?.vocal_gender === 'male' ? 'Penyanyi Cowok' : 'Penyanyi Cewek'
      const data = await saveVoicePreview(selected.preview_id, name.trim() || defaultLabel)
      upsert({ ...selected, saved_persona_id: data.persona.persona_id })
      setNotice(`✓ Persona suara "${data.persona.name}" tersimpan dan langsung dipilih!`)
      setName('')
      onSaved?.(data.persona)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="rounded-xl border border-cyan/25 bg-cyan/5 p-4" aria-label="Buat persona suara">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-white">🎨 Buat Persona Suara Sendiri (Cewek / Cowok · Tak Terbatas)</h3>
        <span className="rounded-full border border-cyan/40 bg-cyan/15 px-2.5 py-0.5 font-mono text-[10px] text-cyan-soft">
          ∞ Unlimited Persona
        </span>
      </div>
      <p className="mt-1 text-xs leading-5 text-white/60">
        Beri nama penyanyi virtualmu, pilih kategori <b>👩 Suara Cewek</b> atau <b>👨 Suara Cowok</b>, lalu atur tekstur &amp; warna suaranya.
      </p>
      {!user && (
        <button type="button" onClick={onSignIn} className="mt-3 text-xs text-cyan-soft underline">
          Masuk untuk membuat dan menyimpan persona suara
        </button>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-white/75">
          Nama Persona Baru
          <input
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={spec.vocal_gender === 'male' ? 'Contoh: Dimas — Bariton Reggae' : 'Contoh: Sinta — Pop Jawa Halus'}
            className="mt-1 block w-full rounded-lg border border-white/15 bg-navy-3 px-3 py-2 text-sm text-white outline-none focus:border-cyan"
          />
        </label>

        <div className="text-xs text-white/75">
          <span>Kategori Suara (Dipisahkan Cewek &amp; Cowok)</span>
          <div className="mt-1 grid grid-cols-2 gap-2">
            {[
              { id: 'female', label: '👩 Suara Cewek' },
              { id: 'male', label: '👨 Suara Cowok' },
            ].map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => setSpec((prev) => ({ ...prev, vocal_gender: g.id }))}
                className={`rounded-lg border px-3 py-2 text-xs font-bold transition ${spec.vocal_gender === g.id
                  ? g.id === 'female'
                    ? 'border-magenta/60 bg-magenta/20 text-white'
                    : 'border-cyan/60 bg-cyan/20 text-white'
                  : 'border-white/10 bg-navy-3 text-white/60 hover:text-white'
                  }`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <fieldset disabled={busy || Boolean(active)} className="mt-3 grid gap-3 sm:grid-cols-3 disabled:opacity-60">
        {['texture', 'energy', 'register'].map((key) => (
          <label key={key} className="text-xs text-white/65">
            {labels[key]}
            <select
              value={spec[key]}
              onChange={(e) => setSpec((prev) => ({ ...prev, [key]: e.target.value }))}
              className="mt-1 block w-full rounded-lg border border-white/10 bg-navy-3 px-2.5 py-2 text-xs text-white"
            >
              {options[key].map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        ))}
      </fieldset>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-white/65">
          Warna suara: {spec.warmth > 0 ? `Hangat +${spec.warmth}` : spec.warmth < 0 ? `Sejuk ${spec.warmth}` : 'Netral 0'}
          <input
            aria-label="Warna suara"
            type="range"
            min="-100"
            max="100"
            step="1"
            value={spec.warmth}
            onChange={(e) => setSpec((prev) => ({ ...prev, warmth: Number(e.target.value) }))}
            className="mt-2 block w-full accent-cyan"
          />
          <span className="mt-1 flex justify-between text-[10px] text-white/35">
            <span>Sejuk −100</span>
            <span>Netral 0</span>
            <span>Hangat +100</span>
          </span>
        </label>
        <label className="text-xs text-white/65">
          Kecerahan: {spec.brightness > 0 ? `Cerah +${spec.brightness}` : spec.brightness < 0 ? `Gelap ${spec.brightness}` : 'Netral 0'}
          <input
            aria-label="Kecerahan suara"
            type="range"
            min="-100"
            max="100"
            step="1"
            value={spec.brightness}
            onChange={(e) => setSpec((prev) => ({ ...prev, brightness: Number(e.target.value) }))}
            className="mt-2 block w-full accent-cyan"
          />
          <span className="mt-1 flex justify-between text-[10px] text-white/35">
            <span>Gelap −100</span>
            <span>Netral 0</span>
            <span>Cerah +100</span>
          </span>
        </label>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={instantSaving}
          onClick={saveInstantDesign}
          className="rounded-lg border border-signal/50 bg-signal/15 px-4 py-2 text-xs font-bold text-signal transition hover:bg-signal/25 disabled:opacity-40"
        >
          {instantSaving ? 'Menyimpan persona…' : `✨ Simpan Persona ${spec.vocal_gender === 'male' ? 'Cowok' : 'Cewek'} Ini Sekarang`}
        </button>

        <button
          type="button"
          disabled={busy || Boolean(active) || (Boolean(user) && !capability?.ready)}
          onClick={() => generate(false)}
          className="rounded-lg border border-cyan/40 bg-cyan/15 px-3.5 py-2 text-xs font-bold text-cyan-soft disabled:opacity-40"
        >
          {busy ? 'Memulai preview…' : active ? 'Membuat contoh suara…' : existing ? '🎧 Putar Preview Audio' : '🎧 Buat Preview Audio (GPU)'}
        </button>

        {items.length > 0 && (
          <button
            type="button"
            disabled={busy || Boolean(active) || !capability?.ready}
            onClick={() => generate(true)}
            className="rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70 disabled:opacity-40"
          >
            🎲 Variasi Baru
          </button>
        )}
      </div>

      {error && <p role="alert" className="mt-3 text-xs leading-5 text-danger">{error}</p>}
      {notice && <p role="status" className="mt-3 text-xs text-signal">{notice}</p>}

      {items.length > 0 && (
        <div className="mt-4 border-t border-white/10 pt-3">
          <h4 className="text-xs font-semibold text-white/75">Riwayat Preview Audio Suara ({items.length})</h4>
          <div className="mt-2 flex max-h-40 flex-wrap gap-2 overflow-y-auto pr-1">
            {items.map((p, index) => (
              <button
                key={p.preview_id}
                type="button"
                onClick={() => setSelectedId(p.preview_id)}
                aria-pressed={selectedId === p.preview_id}
                className={`rounded-lg border px-3 py-2 text-left text-[11px] ${selectedId === p.preview_id ? 'border-cyan/50 bg-cyan/10 text-white' : 'border-white/10 text-white/55'
                  }`}
              >
                {p.saved_persona_id ? '✓ Tersimpan' : `Preview #${items.length - index}`} ·{' '}
                {p.status === 'completed' ? `${Math.round(p.duration)} dtk` : p.status === 'failed' ? 'Gagal' : 'Diproses'}
                <span className="mt-1 block text-[10px] text-white/40">{summary(p.spec)}</span>
              </button>
            ))}
          </div>
          {selected?.status === 'failed' && <p className="mt-3 text-xs text-danger">{selected.error}</p>}
          {selected?.status === 'completed' && (
            <div className="mt-3 space-y-2.5">
              <p className="text-[11px] text-white/50">Preview terpilih: {summary(selected.spec)}</p>
              <PrivateVoicePlayer
                key={selected.preview_id}
                id={selected.preview_id}
                onHeard={() => setHeard((prev) => ({ ...prev, [selected.preview_id]: true }))}
              />
              <button
                type="button"
                onClick={savePreviewPersona}
                disabled={saving}
                className="rounded-lg border border-signal/40 bg-signal/10 px-3 py-2 text-xs font-bold text-signal disabled:opacity-40"
              >
                {saving ? 'Menyimpan…' : '💾 Simpan Audio Preview Ini Sebagai Persona'}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
