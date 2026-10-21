import { useEffect, useRef, useState } from 'react'
import CaptionSettings, { normalizeOptions } from './CaptionSettings.jsx'
import LayoutMockup from './LayoutMockup.jsx'
import { loadFavorites, saveFavorite, syncFavorites } from '../criteriaStore.js'
import { loadBrandKit } from '../Settings.jsx'
import FavoriteChips from './FavoriteChips.jsx'
import { Button, Field, Micro, Panel, Select, TextInput } from './ui.jsx'
import Icon from './Icon.jsx'
import { getBillingStatus } from '../api.js'

export const LAYOUTS = [
  { id: 'auto', label: 'AUTO', desc: 'Scene-aware face tracking (best)' },
  { id: 'general', label: 'GENERAL', desc: 'Full-frame fit, everyone visible' },
  { id: 'face', label: 'FACE', desc: 'Sharp face crop + speaker cuts' },
  { id: 'split', label: 'SPLIT', desc: '2 people stacked, no blur' },
  { id: 'panel', label: 'PANEL', desc: '2 people side by side, no blur' },
  { id: 'gameplay', label: 'GAMEPLAY', desc: 'Game + presenter 60:40, no blur' },
]

export const ASPECTS = [
  { id: '9:16', label: '9:16 — Shorts/Reels/TikTok' },
]

export const LANGUAGES = [
  { id: 'original', label: 'Original language' },
  { id: 'id', label: 'Bahasa Indonesia' },
  { id: 'en', label: 'English' },
  { id: 'ms', label: 'Melayu' },
  { id: 'zh', label: '中文 (Mandarin)' },
  { id: 'ja', label: '日本語 (Japanese)' },
  { id: 'ko', label: '한국어 (Korean)' },
  { id: 'es', label: 'Español' },
  { id: 'ar', label: 'العربية (Arabic)' },
  { id: 'hi', label: 'हिन्दी (Hindi)' },
  { id: 'th', label: 'ไทย (Thai)' },
  { id: 'vi', label: 'Tiếng Việt' },
]

// R8 — custom AI criteria presets: natural-language instructions that steer
// AI's moment selection (sent as options.custom_prompt). Click to fill.
export const CRITERIA_PRESETS = [
  {
    id: 'product',
    label: '📦 Product demo',
    prompt: 'Fokus pada momen product demo: tunjukkan fitur unggulan, cara pakai, dan hasilnya. Pilih bagian yang paling meyakinkan untuk konversi.',
  },
  {
    id: 'hot-take',
    label: '🔥 Opini kontroversial',
    prompt: 'Highlight opini kontroversial, pernyataan tegas, atau klaim yang memancing perdebatan. Semakin memecah belah pendapat, semakin baik.',
  },
  {
    id: 'emotional',
    label: '😭 Momen emosional',
    prompt: 'Pilih momen paling emosional: kisah haru, kegagalan lalu bangkit, atau pengakuan jujur. Utamakan bagian yang membuat penonton tersentuh.',
  },
  {
    id: 'tutorial',
    label: '🎓 Tutorial step',
    prompt: 'Pilih langkah tutorial yang paling penting dan actionable — bagian dengan instruksi konkret yang bisa langsung dipraktikkan penonton.',
  },
  {
    id: 'mind-blown',
    label: '🤯 Fakta mengejutkan',
    prompt: 'Cari fakta, angka, atau plot twist yang mengejutkan — momen yang membuat penonton berhenti scroll karena terkejut.',
  },
  {
    id: 'story',
    label: '📖 Cerita utuh',
    prompt: 'Pilih momen yang memiliki alur cerita lengkap: pembuka → konflik → resolusi. Bagian yang bisa berdiri sendiri sebagai video pendek.',
  },
  {
    id: 'monetize',
    label: '💰 Niche & CTA',
    prompt: 'Fokus pada momen yang menyebut harga, diskon, keuntungan, atau ajakan bertindak — bagian paling bernilai untuk penjualan.',
  },
  {
    id: 'qa',
    label: '🙋 Q&A menarik',
    prompt: 'Pilih pertanyaan paling menarik dan jawabannya yang paling memuaskan dari sesi tanya jawab.',
  },
]

const STARTERS = [
  { label: '🎙️ Podcast — uji cepat', url: 'https://www.youtube.com/watch?v=aqz-KE-bpKQ', desc: 'Video yang sudah teruji end-to-end' },
]

export default function InputStage({ onUrlSubmit, onFile, onBatch, initialUrl, user }) {
  const [url, setUrl] = useState(initialUrl ? String(initialUrl) : '')
  const [batchText, setBatchText] = useState('')
  const [options, setOptions] = useState(() => ({ ...normalizeOptions(), ...loadBrandKit() }))
  const [dragging, setDragging] = useState(false)
  const [open, setOpen] = useState(false)
  const [inputTab, setInputTab] = useState('url') // 'url' | 'upload' | 'batch'
  const [favorites, setFavorites] = useState(loadFavorites)
  const [maxOutputClips, setMaxOutputClips] = useState(3)
  const fileRef = useRef(null)

  // Pull account-synced favorites when signed in (also catches the login
  // event that fires after the Google popup completes).
  useEffect(() => {
    syncFavorites().then(setFavorites)
    const onAuth = () => syncFavorites().then(setFavorites)
    window.addEventListener('klipin:authenticated', onAuth)
    return () => window.removeEventListener('klipin:authenticated', onAuth)
  }, [])

  useEffect(() => {
    let alive = true
    if (!user) {
      setMaxOutputClips(3)
      setOptions((current) => ({ ...current, render_count: 3 }))
      return () => { alive = false }
    }
    getBillingStatus()
      .then((status) => {
        if (!alive) return
        const maximum = Number(status.max_output_clips) === 6 ? 6 : 3
        setMaxOutputClips(maximum)
        setOptions((current) => {
          const requested = Number(current.render_count)
          return {
            ...current,
            render_count: requested === 3 || requested === 6
              ? Math.min(requested, maximum)
              : maximum,
          }
        })
      })
      .catch(() => {
        if (!alive) return
        setMaxOutputClips(3)
        setOptions((current) => ({ ...current, render_count: 3 }))
      })
    return () => { alive = false }
  }, [user])

  const submit = (e) => {
    e.preventDefault()
    if (!url.trim()) return
    onUrlSubmit(url.trim(), { ...options })
  }

  const submitManual = (u) => {
    onUrlSubmit(u.trim(), { ...options })
  }

  const submitBatch = (e) => {
    e.preventDefault()
    const urls = batchText
      .split(/[\n,]+/)
      .map((u) => u.trim())
      .filter(Boolean)
    if (!urls.length) return
    onBatch(urls, { ...options })
  }

  const pickFile = (file) => {
    if (file) onFile(file, { ...options })
  }

  const drop = (e) => {
    e.preventDefault()
    setDragging(false)
    pickFile(e.dataTransfer.files?.[0])
  }

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
      <Micro className="block text-center">SYS.INPUT — SOURCE INTAKE</Micro>
      <h1 className="text-chrome mt-2 text-center font-display text-2xl font-extrabold tracking-wide sm:text-3xl">
        Paste a link, or drop a video
      </h1>
      <p className="mt-2 text-center text-sm text-white/45">
        YouTube · TikTok · Instagram · MP4/MOV/MKV · up to 2 GB · guest browsing, sign-in to render
      </p>
      <div className="mx-auto mt-3 flex max-w-2xl flex-wrap items-center justify-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em]">
        <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-white/55">
          Free · hingga 3 klip/video
        </span>
        <span className="rounded-full border border-cyan/35 bg-cyan/10 px-3 py-1 text-cyan-soft">
          Berbayar · hingga 6 klip/video
        </span>
        <span className="rounded-full border border-gold/35 bg-gold/10 px-3 py-1 text-gold">
          Prioritas kualitas · AI score &gt;90
        </span>
      </div>

      <div className="mx-auto mt-4 max-w-md rounded-lg border border-cyan/20 bg-navy-2/70 p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/45">
              Jumlah video hasil
            </div>
            <div className="mt-0.5 text-xs text-white/65">
              Kandidat &gt;90 diprioritaskan; sisa slot diisi kandidat unik terbaik
            </div>
          </div>
          <div className="grid w-full grid-cols-2 gap-1 rounded-md border border-white/10 bg-black/20 p-1 sm:w-auto">
            {[3, 6].map((count) => {
              const locked = count > maxOutputClips
              const active = Number(options.render_count || maxOutputClips) === count
              return (
                <button
                  key={count}
                  type="button"
                  disabled={locked}
                  onClick={() => setOptions({ ...options, render_count: count })}
                  aria-pressed={active}
                  title={locked ? 'Tersedia setelah membeli paket berbayar' : `${count} video hasil`}
                  className={`min-w-0 rounded px-2 py-2 font-mono text-xs font-bold transition sm:min-w-20 sm:px-3 ${
                    active
                      ? 'bg-cyan/20 text-cyan-soft shadow-neon-cyan'
                      : locked
                        ? 'cursor-not-allowed text-white/20'
                        : 'text-white/50 hover:bg-white/5 hover:text-white'
                  }`}
                >
                  {count} VIDEO {locked ? '🔒' : ''}
                </button>
              )
            })}
          </div>
        </div>
        {maxOutputClips === 3 && (
          <p className="mt-2 text-center font-mono text-[9px] uppercase tracking-wider text-gold/70 sm:text-right">
            Free tier · upgrade untuk 6 video
          </p>
        )}
      </div>

      {/* source input: URL / upload as parallel tabs */}
      <div className="mt-6">
        <div
          className="grid grid-cols-3 gap-1.5 rounded-md border border-border-soft bg-navy-2/60 p-1"
          role="tablist"
          aria-label="source type"
        >
          {[
            { id: 'url', label: 'URL' },
            { id: 'upload', label: 'UPLOAD' },
            { id: 'batch', label: 'BATCH' },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={inputTab === t.id}
              onClick={() => setInputTab(t.id)}
              className={`rounded px-3 py-2 font-mono text-[10px] uppercase tracking-[0.2em] transition-all ${
                inputTab === t.id
                  ? 'bg-cyan/15 text-cyan-soft shadow-neon-cyan'
                  : 'text-white/40 hover:text-white/70'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {inputTab === 'url' ? (
          <>
            <form onSubmit={submit} className="mt-3 flex gap-2">
              <TextInput
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.youtube.com / tiktok.com / instagram.com/reel/..."
                className="flex-1 px-4 py-3"
                aria-label="video URL"
              />
              <Button type="submit" disabled={!url.trim()} className="px-5 py-3">
                Clip it →
              </Button>
            </form>
            {/* video starter suggestions (KlipAja-style) */}
            <div className="mt-3">
              <div className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-white/30">
                Butuh ide? Coba video starter:
              </div>
              <div className="flex flex-wrap gap-1.5">
                {STARTERS.map((s) => (
                  <button
                    key={s.url}
                    type="button"
                    onClick={() => { setUrl(s.url); submitManual(s.url) }}
                    title={s.desc}
                    className="rounded-full border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-[10px] text-white/50 transition hover:border-cyan/50 hover:text-cyan-soft"
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : inputTab === 'batch' ? (
          <form onSubmit={submitBatch} className="mt-3">
            <textarea
              value={batchText}
              onChange={(e) => setBatchText(e.target.value)}
              placeholder={'https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/watch?v=...\n(satu URL per baris — maks 20)'}
              rows={5}
              className="w-full resize-y rounded-md border border-cyan/25 bg-navy-2 px-3 py-2.5 font-mono text-sm text-white outline-none transition placeholder:text-white/25 focus:border-cyan"
              aria-label="batch URLs"
            />
            <div className="mt-3 flex items-center gap-3">
              <Button type="submit" disabled={!batchText.trim()} className="px-5 py-3">
                Batch clip →
              </Button>
              <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/35">
                {batchText.split(/[\n,]+/).filter((u) => u.trim()).length || 0} URL(s) · diantrekan paralel
              </span>
            </div>
          </form>
        ) : (
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => e.key === 'Enter' && fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
            className={`mt-3 cursor-pointer rounded-xl border-2 border-dashed px-8 py-10 text-center transition-all ${
              dragging ? 'border-cyan bg-cyan/10 shadow-neon-cyan' : 'card-chrome border-cyan/30 hover:border-cyan/60'
            }`}
          >
            <div className="font-mono text-xs tracking-widest text-white/40">[ DROP ZONE ]</div>
            <p className="mt-3 text-sm text-white/70">Drop a video here or click to browse</p>
            <p className="mt-1 font-mono text-[11px] text-white/30">VIDEO/* · ≤ 2 GB</p>
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0])}
            />
          </div>
        )}
      </div>

      <Panel
        label="Machine settings"
        dot="brand"
        className="mt-8"
        bodyClassName="p-5"
        right={
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/50 transition hover:text-white"
          >
            {open ? 'hide' : 'show'} ▾
          </button>
        }
      >
        {open ? (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Layout mode" hint="How the source fills the frame">
                <Select
                  value={options.layout || options.crop || 'auto'}
                  onChange={(e) => setOptions({ ...options, layout: e.target.value })}
                  aria-label="layout mode"
                >
                  {LAYOUTS.map((l) => (
                    <option key={l.id} value={l.id} title={l.desc}>
                      {l.label} — {l.desc}
                    </option>
                  ))}
                </Select>
                <div className="mt-2.5">
                  <LayoutMockup
                    value={options.layout || options.crop || 'auto'}
                    onChange={(id) => setOptions({ ...options, layout: id })}
                  />
                </div>
                <p className="mt-2 text-[10px] leading-snug text-white/40">
                  AUTO berubah per-scene. FACE mengikuti pembicara aktif tanpa blur.
                  SPLIT menumpuk dua pembicara atas–bawah secara tetap.
                </p>
                {(options.layout || options.crop) === 'gameplay' && (
                  <div className="mt-3">
                    <Field label="Posisi presenter" hint="Panel presenter 40%; konten utama 60%">
                      <Select
                        value={options.gameplay_face_position || 'bottom'}
                        onChange={(e) => setOptions({
                          ...options,
                          gameplay_face_position: e.target.value,
                        })}
                      >
                        <option value="bottom">Bawah — konten 60% di atas</option>
                        <option value="top">Atas — konten 60% di bawah</option>
                      </Select>
                    </Field>
                    <Field label="Proporsi panel" hint="Atur tinggi konten utama dan facecam">
                      <div className="mt-2 grid grid-cols-3 gap-2">
                        {[0.5, 0.6, 0.7].map((ratio) => (
                          <button
                            key={ratio}
                            type="button"
                            className={`rounded-lg border px-2 py-2 text-xs transition ${
                              Number(options.gameplay_ratio || 0.6) === ratio
                                ? 'border-amber-400 bg-amber-400/15 text-amber-200'
                                : 'border-white/10 bg-black/20 text-white/60 hover:border-white/25'
                            }`}
                            onClick={() => setOptions({ ...options, gameplay_ratio: ratio })}
                          >
                            {Math.round(ratio * 100)}:{Math.round((1 - ratio) * 100)}
                          </button>
                        ))}
                      </div>
                    </Field>
                  </div>
                )}
              </Field>

              <Field label="Aspect ratio" hint="Output frame shape">
                <Select
                  value={options.aspect || '9:16'}
                  onChange={(e) => setOptions({ ...options, aspect: e.target.value })}
                  aria-label="aspect ratio"
                >
                  {ASPECTS.map((a) => (
                    <option key={a.id} value={a.id}>{a.label}</option>
                  ))}
                </Select>
              </Field>

              <Field label="Subtitle language" hint="Terjemahkan subtitle otomatis oleh AI (opsional)">
                <Select
                  value={options.translate_to || 'original'}
                  onChange={(e) => setOptions({ ...options, translate_to: e.target.value })}
                  aria-label="subtitle language"
                >
                  {LANGUAGES.map((l) => (
                    <option key={l.id} value={l.id}>{l.label}</option>
                  ))}
                </Select>
              </Field>

              <Field label="Title overlay" hint="Judul muncul di awal klip lalu memudar">
                <TextInput
                  value={options.title_overlay || ''}
                  onChange={(e) => setOptions({ ...options, title_overlay: e.target.value })}
                  placeholder="cth: INI BUKAN CERITA BIASA…"
                  aria-label="title overlay"
                />
              </Field>

              <div className="col-span-2 flex items-center gap-3 rounded-md border border-magenta/25 bg-magenta/5 px-3 py-2.5">
                <input
                  type="checkbox"
                  checked={!!options.genre_detect}
                  onChange={(e) => setOptions({ ...options, genre_detect: e.target.checked })}
                  className="h-4 w-4 rounded accent-magenta"
                  id="genre-detect"
                />
                <label htmlFor="genre-detect" className="cursor-pointer">
                  <span className="text-xs font-semibold text-white/80">🧠 Genre-aware reframe</span>
                  <span className="ml-2 rounded-full border border-magenta/40 bg-magenta/10 px-2 py-0.5 font-mono text-[9px] text-magenta-soft">AI</span>
                  <p className="mt-0.5 text-[11px] text-white/40">Auto-detect genre (sports, gaming, vlog…) → crop adapts (wider for action, tighter for talk)</p>
                </label>
              </div>

              <div className="col-span-2">
                <Field label="Custom AI criteria" hint="Preset siap pakai atau instruksi bebas — arahkan AI saat memilih momen (opsional)">
                  <div className="flex flex-wrap gap-1.5">
                    {CRITERIA_PRESETS.map((p) => {
                      const active = (options.custom_prompt || '').trim() === p.prompt
                      return (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() =>
                            setOptions({
                              ...options,
                              custom_prompt: active ? '' : p.prompt,
                            })
                          }
                          aria-pressed={active}
                          title={p.prompt}
                          className={`rounded-full border px-2.5 py-1 font-mono text-[10px] transition ${
                            active
                              ? 'border-cyan bg-cyan/15 text-cyan-soft'
                              : 'border-border bg-navy-2/60 text-white/45 hover:border-cyan/40 hover:text-white/75'
                          }`}
                        >
                          {active ? <Icon name="check" className="h-3 w-3" /> : ''}{p.label}
                        </button>
                      )
                    })}
                  </div>
                  <textarea
                    value={options.custom_prompt || ''}
                    onChange={(e) => setOptions({ ...options, custom_prompt: e.target.value })}
                    placeholder="cth: pilih momen yang paling emosional, minimal teriakan…"
                    aria-label="custom ai criteria"
                    rows={2}
                    className="mt-2 w-full resize-y rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/25 focus:border-cyan"
                  />
                  {(options.custom_prompt || '').trim() && (
                    <button
                      type="button"
                      onClick={() => setFavorites(saveFavorite(options.custom_prompt))}
                      disabled={favorites.some((f) => f.prompt === (options.custom_prompt || '').trim())}
                      className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-cyan/30 bg-cyan/5 px-2.5 py-1 font-mono text-[10px] text-cyan-soft transition hover:border-cyan/70 disabled:opacity-40"
                      title="Simpan kriteria ini sebagai favorit"
                    >
                      <Icon name="star" className="h-3 w-3" /> Simpan sebagai favorit
                    </button>
                  )}
                  {favorites.length > 0 && (
                    <div className="mt-2.5 border-t border-border-soft pt-2">
                      <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/35">
                        Favorit ({favorites.length}) — klik label untuk isi, <Icon name="folder" className="h-2.5 w-2.5 inline" /> folder, <Icon name="edit" className="h-2.5 w-2.5 inline" /> rename, <Icon name="close" className="h-2.5 w-2.5 inline" /> hapus
                      </div>
                      <FavoriteChips
                        favorites={favorites}
                        value={options.custom_prompt || ''}
                        onUse={(prompt) => setOptions({ ...options, custom_prompt: prompt })}
                        onChange={setFavorites}
                        accent="cyan"
                      />
                    </div>
                  )}
                </Field>
              </div>
            </div>
            <CaptionSettings options={options} onChange={setOptions} title="MACHINE SETTINGS" />
          </div>
        ) : (
          <p className="text-xs text-white/40">
            Layout · aspect · caption style · color · font · animation · watermark — applied to
            every clip. Defaults are fine for a first run.
          </p>
        )}
      </Panel>
    </div>
  )
}
