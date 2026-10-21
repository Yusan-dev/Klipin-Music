import { useCallback, useEffect, useRef, useState } from 'react'
import { createVoicePreview, fetchVoiceAudio, getVoicePreview, listVoicePreviews, saveVoicePreview } from '../api.js'

const nextSeed = () => window.crypto.getRandomValues(new Uint32Array(1))[0] % 2147483648
const options = {
  vocal_gender: [['auto', 'Bebas'], ['male', 'Laki-laki'], ['female', 'Perempuan']],
  texture: [['smooth', 'Jernih & halus'], ['breathy', 'Lembut & berembus'], ['raspy', 'Sedikit serak']],
  energy: [['balanced', 'Seimbang'], ['soft', 'Lembut'], ['powerful', 'Bertenaga']],
  register: [['mid', 'Sedang'], ['low', 'Rendah'], ['high', 'Tinggi']],
}
const labels = { vocal_gender: 'Karakter suara', texture: 'Tekstur', energy: 'Energi', register: 'Rentang vokal' }
const initial = () => ({ vocal_gender: 'auto', warmth: 0, brightness: 0,
  texture: 'smooth', energy: 'balanced', register: 'mid', seed: nextSeed() })
const signature = (s) => JSON.stringify(['vocal_gender', 'warmth', 'brightness', 'texture', 'energy', 'register', 'seed'].map(k => s?.[k]))
const summary = (s) => ['vocal_gender', 'texture', 'register'].map(k => options[k].find(([v]) => v === s?.[k])?.[1]).filter(Boolean).join(' · ')

export function PrivateVoicePlayer({ id, savedPersona = false, onHeard }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    let blobUrl = ''
    setUrl(''); setError('')
    fetchVoiceAudio(id, savedPersona).then(blob => {
      if (!alive) return
      blobUrl = URL.createObjectURL(blob)
      setUrl(blobUrl)
    }).catch(e => { if (alive) setError(e.message) })
    return () => { alive = false; if (blobUrl) URL.revokeObjectURL(blobUrl) }
  }, [id, savedPersona])
  if (error) return <p role="alert" className="text-xs text-danger">Audio belum bisa diputar: {error}</p>
  if (!url) return <p role="status" className="text-xs text-white/55">Memuat contoh suara…</p>
  return <audio controls preload="metadata" src={url} onPlay={onHeard} className="h-10 w-full" aria-label="Dengarkan contoh suara persona" />
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
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const selected = items.find(p => p.preview_id === selectedId)
  const active = items.find(p => ['queued', 'processing'].includes(p.status))
  const existing = items.find(p => signature(p.spec) === signature(spec) && p.status === 'completed')
  const upsert = useCallback(row => setItems(prev => [row, ...prev.filter(p => p.preview_id !== row.preview_id)].slice(0, 6)), [])

  const refresh = useCallback(async () => {
    if (!user) return
    try {
      const data = await listVoicePreviews()
      setCapability(data.capability)
      setItems(data.previews || [])
      if (!mounted.current && data.previews?.length) {
        setSelectedId(data.previews[0].preview_id)
        setSpec(data.previews[0].spec)
      }
      mounted.current = true
    } catch (e) { setError(e.message) }
  }, [user])
  useEffect(() => { refresh() }, [refresh])
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
          setNotice('Preview siap. Dengarkan sebelum menyimpan suara ini.')
          return
        }
        if (row.status === 'failed') { setError(row.error); return }
      } catch (e) { if (alive) setError(`Status belum terhubung: ${e.message}. Pemeriksaan akan dicoba kembali.`) }
      if (alive) timer = window.setTimeout(poll, 3000)
    }
    timer = window.setTimeout(poll, 1500)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [active?.preview_id, upsert])

  const generate = async (variation = false) => {
    if (!user) { onSignIn(); return }
    if (active || busy) return
    if (existing && !variation) { setSelectedId(existing.preview_id); return }
    const draft = variation ? { ...spec, seed: nextSeed() } : spec
    setSpec(draft); setBusy(true); setError(''); setNotice('')
    try {
      const row = await createVoicePreview(draft)
      upsert(row); setSelectedId(row.preview_id)
      if (row.status === 'failed') setError(row.error)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  const save = async () => {
    if (!selected || (!selected.saved_persona_id && !heard[selected.preview_id]) || saving) return
    setSaving(true); setError('')
    try {
      const data = await saveVoicePreview(selected.preview_id, name.trim() || 'Penyanyi saya')
      upsert({ ...selected, saved_persona_id: data.persona.persona_id })
      setNotice('Persona tersimpan. Suara ini sekarang dipilih untuk lagu berikutnya.')
      onSaved(data.persona)
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  return <section className="rounded-xl border border-cyan/25 bg-cyan/5 p-4" aria-label="Buat persona suara">
    <h3 className="text-sm font-bold text-white">Buat penyanyi virtualmu</h3>
    <p className="mt-1 text-xs leading-5 text-white/60">1. Atur karakter · 2. Dengarkan preview · 3. Simpan suara pilihanmu</p>
    <p className="mt-2 text-[11px] leading-5 text-white/45">Titik tengah adalah netral 0. Geser halus ke kiri/kanan untuk membentuk warna suara; setiap variasi juga memakai seed berbeda sehingga kombinasi suaranya sangat luas.</p>
    {!user && <button type="button" onClick={onSignIn} className="mt-3 text-xs text-cyan-soft">Masuk untuk membuat dan menyimpan suara</button>}
    <fieldset disabled={busy || Boolean(active)} className="mt-4 grid gap-3 sm:grid-cols-2 disabled:opacity-60">
      {Object.keys(options).map(key => <label key={key} className="text-xs text-white/65">{labels[key]}
        <select value={spec[key]} onChange={e => setSpec(prev => ({ ...prev, [key]: e.target.value }))}
          className="mt-1 block w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white">
          {options[key].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>)}
      <label className="text-xs text-white/65">Warna suara: {spec.warmth > 0 ? `Hangat +${spec.warmth}` : spec.warmth < 0 ? `Sejuk ${spec.warmth}` : 'Netral 0'}
        <input aria-label="Warna suara" type="range" min="-100" max="100" step="1" value={spec.warmth} onChange={e => setSpec(prev => ({ ...prev, warmth: Number(e.target.value) }))} className="mt-2 block w-full accent-cyan" />
        <span className="mt-1 flex justify-between text-[10px] text-white/35"><span>Sejuk −100</span><span>Netral 0</span><span>Hangat +100</span></span>
      </label>
      <label className="text-xs text-white/65">Kecerahan: {spec.brightness > 0 ? `Cerah +${spec.brightness}` : spec.brightness < 0 ? `Gelap ${spec.brightness}` : 'Netral 0'}
        <input aria-label="Kecerahan suara" type="range" min="-100" max="100" step="1" value={spec.brightness} onChange={e => setSpec(prev => ({ ...prev, brightness: Number(e.target.value) }))} className="mt-2 block w-full accent-cyan" />
        <span className="mt-1 flex justify-between text-[10px] text-white/35"><span>Gelap −100</span><span>Netral 0</span><span>Cerah +100</span></span>
      </label>
    </fieldset>
    <div className="mt-4 flex flex-wrap gap-2">
      <button type="button" disabled={busy || Boolean(active) || (Boolean(user) && !capability?.ready)} onClick={() => generate(false)}
        className="rounded-lg border border-cyan/40 bg-cyan/15 px-4 py-2 text-xs font-bold text-cyan-soft disabled:opacity-40">
        {busy ? 'Memulai preview…' : active ? 'Membuat contoh suara…' : existing ? 'Dengarkan hasil tersimpan' : 'Buat preview suara'}
      </button>
      {items.length > 0 && <button type="button" disabled={busy || Boolean(active) || !capability?.ready} onClick={() => generate(true)}
        className="rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70 disabled:opacity-40">Buat variasi lain</button>}
    </div>
    <p className="mt-2 text-[10px] leading-4 text-white/45">Preview baru memakai komputasi; maksimal 6 per 24 jam. Memutar dan menyimpan preview tidak membuat audio ulang.</p>
    {user && capability && !capability.ready && <p role="status" className="mt-2 text-xs text-warn">{capability.message} <button type="button" onClick={refresh} className="underline">Periksa lagi</button></p>}
    {active && <p role="status" className="mt-3 text-xs leading-5 text-cyan-soft">Suara sedang dibuat. Proses awal bisa memerlukan beberapa menit. Kamu boleh meninggalkan halaman dan kembali ke preview ini.</p>}
    {error && <p role="alert" className="mt-3 text-xs leading-5 text-danger">{error}</p>}
    {notice && <p role="status" className="mt-3 text-xs text-signal">{notice}</p>}
    {items.length > 0 && <div className="mt-4 border-t border-white/10 pt-3">
      <h4 className="text-xs font-semibold text-white/75">Bandingkan contoh suaramu</h4>
      <div className="mt-2 flex flex-wrap gap-2">{items.map((p, index) => <button key={p.preview_id} type="button" onClick={() => setSelectedId(p.preview_id)}
        aria-pressed={selectedId === p.preview_id} className={`rounded-lg border px-3 py-2 text-left text-[11px] ${selectedId === p.preview_id ? 'border-cyan/50 bg-cyan/10 text-white' : 'border-white/10 text-white/55'}`}>
        {p.saved_persona_id ? '✓ Tersimpan' : `Contoh ${items.length - index}`} · {p.status === 'completed' ? `${Math.round(p.duration)} dtk` : p.status === 'failed' ? 'Gagal' : 'Diproses'}
        <span className="mt-1 block text-[10px] text-white/40">{summary(p.spec)}</span>
      </button>)}</div>
      {selected?.status === 'failed' && <p className="mt-3 text-xs text-danger">{selected.error}</p>}
      {selected?.status === 'completed' && <div className="mt-3 space-y-3">
        <p className="text-[11px] text-white/50">Suara pilihan: {summary(selected.spec)}. Simpan mengunci contoh yang sedang kamu dengarkan.</p>
        <PrivateVoicePlayer key={selected.preview_id} id={selected.preview_id} onHeard={() => setHeard(prev => ({ ...prev, [selected.preview_id]: true }))} />
        {!selected.saved_persona_id && <label className="block text-xs text-white/65">Nama persona
          <input maxLength={80} value={name} onChange={e => setName(e.target.value)} placeholder="Contoh: Aruna — pop hangat" className="mt-1 block w-full rounded-lg border border-white/10 bg-navy-3 px-3 py-2 text-sm text-white" />
        </label>}
        <button type="button" onClick={save} disabled={(!selected.saved_persona_id && !heard[selected.preview_id]) || saving}
          className="rounded-lg border border-signal/40 bg-signal/10 px-3 py-2 text-xs font-bold text-signal disabled:opacity-40">
          {saving ? 'Menyiapkan persona…' : selected.saved_persona_id ? 'Gunakan persona tersimpan' : 'Simpan suara ini & gunakan'}
        </button>
        {!selected.saved_persona_id && !heard[selected.preview_id] && <p className="text-[10px] text-white/45">Putar contoh suara dahulu untuk mengaktifkan tombol simpan.</p>}
      </div>}
    </div>}
  </section>
}
