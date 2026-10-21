import { Field, Micro, Select, TextInput } from './ui.jsx'
import Icon from './Icon.jsx'

export const CAPTION_STYLES = [
  { id: 'solid', label: 'SOLID', desc: 'Outline + shadow' },
  { id: 'highlight', label: 'HIGHLIGHT', desc: 'Opaque highlight box' },
  { id: 'outline', label: 'OUTLINE', desc: 'Thick stroke only' },
  { id: 'gaming', label: 'GAMING', desc: 'Tebal + glow — ala KlipAja' },
  { id: 'box', label: 'BOX', desc: 'Box bersih tanpa stroke' },
]

export const COLORS = [
  { value: '#FFE500', label: 'amber' },
  { value: '#FFFFFF', label: 'white' },
  { value: '#5EEAD4', label: 'teal' },
  { value: '#7C8CF8', label: 'indigo' },
  { value: '#F472B6', label: 'pink' },
  { value: '#4ADE80', label: 'green' },
]

export const FONTS = [
  'Montserrat', 'Poppins', 'Anton', 'Bebas Neue', 'Roboto',
]
export const FONT_SIZE_MIN = 12
export const FONT_SIZE_MAX = 100

export const CAPTION_BGS = [
  { value: 'solid', label: 'Solid (box pekat)' },
  { value: 'glass', label: 'Glass (semi-transparan)' },
  { value: 'none', label: 'Tanpa background' },
]
export const ANIMATIONS = [
  { value: 'none', label: 'None' },
  { value: 'karaoke', label: 'Karaoke L→R (word sweep)' },
  { value: 'fade', label: 'Fade in' },
  { value: 'grow', label: 'Grow' },
  { value: 'drop', label: 'Drop' },
  { value: 'slide-left', label: 'Slide in' },
  { value: 'pop', label: 'Pop' },
  { value: 'bounce', label: 'Bounce' },
  { value: 'shake', label: 'Shake' },
  { value: 'glow', label: 'Glow' },
]

export const WORDS_PER_LINE = [
  { value: 1, label: '1 kata', desc: 'Satu kata besar per baris (podcast/motivasi)' },
  { value: 2, label: '2 kata', desc: 'Dua kata per baris' },
  { value: 3, label: '3 kata', desc: 'Frasa pendek (default)' },
  { value: 4, label: '4 kata', desc: 'Frasa sedang' },
  { value: 6, label: '6 kata', desc: 'Kalimat pendek' },
  { value: 8, label: '8 kata', desc: 'Kalimat penuh' },
]

export const DEFAULT_OPTIONS = {
  layout: 'auto',
  gameplay_face_position: 'bottom',
  gameplay_ratio: 0.6,
  aspect: '9:16',
  genre_detect: true,
  layout_picker: true,
  caption_style: 'solid',
  subtitles_enabled: true,
  caption_position_y: 73,
  color: '#FFFFFF',
  karaoke_color: '#FFE500',
  caption_font: 'Montserrat',
  caption_size: 84,
  animation: 'karaoke',
  words_per_line: 3,
  emoji: true,
  caption_bg: 'solid',
  watermark: '',
  show_klipin_watermark: true,
  hook_overlay: true,
    title_enabled: true,
    title_color: '#FFFFFF',
    title_position_y: 14,
    hook_duration: '5',
    hook_voiceover: false,
    hook_voice: 'id-ID-ArdiNeural',
    hook_tone: 'natural',
    hook_bg: 'color',
    hook_bg_color: '#FFE500',
    hook_text_color: '#0B0C0E',
    auto_zoom: false,
  zoom_strength: 1.15,
  bgm: 'none',
  bgm_volume: 0.15,
  clip_length: 'viral',
}

export const ZOOM_STRENGTHS = [
  { value: 1.05, label: 'Halus (1.05×)' },
  { value: 1.15, label: 'Sedang (1.15×)' },
  { value: 1.3, label: 'Kuat (1.3×)' },
]

export const BGMS = [
  { value: 'none', label: 'Tanpa musik' },
  { value: 'upbeat', label: 'Upbeat — energik' },
  { value: 'chill', label: 'Chill — santai' },
  { value: 'epic', label: 'Epic — sinematik' },
]

export const HOOK_VOICES = [
  { value: 'id-ID-ArdiNeural', label: 'Ardi (Indonesia, pria)' },
  { value: 'id-ID-GadisNeural', label: 'Gadis (Indonesia, wanita)' },
  { value: 'en-US-GuyNeural', label: 'Guy (English, male)' },
  { value: 'en-US-JennyNeural', label: 'Jenny (English, female)' },
]

// Hook background presets. 'color' uses hook_bg_color; the rest map to
// backend HOOK_STYLES (yellow/red/dark cards). Backend contract:
// options.hook_bg in {'color','yellow','red','dark'}.
export const HOOK_BGS = [
  { value: 'color', label: 'Warna pilihan', swatch: null, color: '#FFE500' },
  { value: 'yellow', label: 'Kuning TikTok', swatch: '#FFD600' },
  { value: 'red', label: 'Merah breaking', swatch: '#DC2626' },
  { value: 'dark', label: 'Gelap elegan', swatch: '#121214' },
]

export const CLIP_LENGTHS = [
  { value: 'viral', label: 'Viral — 90–120s', desc: 'Default: konteks dan payoff lengkap untuk free trial' },
  { value: 'padat', label: 'Padat — 30–45s', desc: 'Snappy, satu beat kuat, tetap utuh' },
  { value: 'cerita', label: 'Cerita — 45–90s', desc: 'Arc pendek: konteks + payoff' },
  { value: 'lengkap', label: 'Lengkap — 90–120s', desc: 'Segmen panjang dengan batas aman platform' },
]

/**
 * Ready-to-use subtitle presets. They intentionally contain ONLY subtitle
 * styling: choosing a text style must never reset FACE/SPLIT/PANEL.
 */
export const CAPTION_PRESETS = [
  {
    id: 'classic',
    name: 'Classic Amber',
    desc: 'Solid yellow — readable anywhere',
    sample: { text: 'WAIT FOR IT…', fg: '#FFE500', bg: null, font: 'Montserrat', stroke: '#000000' },
    options: {
      caption_style: 'solid', color: '#FFFFFF', karaoke_color: '#FFE500',
      caption_font: 'Montserrat', caption_size: 84, animation: 'none',
      emoji: true, watermark: '',
    },
  },
  {
    id: 'highlight',
    name: 'Highlight Pop',
    desc: 'Opaque box — loud & unmissable',
    sample: { text: 'THIS CHANGES EVERYTHING', fg: '#0B0C0E', bg: '#FFE500', font: 'Poppins', stroke: null },
    options: {
      caption_style: 'highlight', color: '#FFFFFF', karaoke_color: '#FFE500',
      caption_font: 'Poppins', caption_size: 96, animation: 'pop',
      emoji: true, watermark: '',
    },
  },
  {
    id: 'neon',
    name: 'Neon Outline',
    desc: 'Thick teal stroke — gaming vibe',
    sample: { text: 'NO WAY 🤯', fg: '#5EEAD4', bg: null, font: 'Anton', stroke: '#000000' },
    options: {
      caption_style: 'outline', color: '#FFFFFF', karaoke_color: '#5EEAD4',
      caption_font: 'Anton', caption_size: 96, animation: 'slide-left',
      emoji: false, watermark: '',
    },
  },
  {
    id: 'money',
    name: 'Money Motif',
    desc: 'Green cash theme + emojis',
    sample: { text: 'MONEY MONEY 💰', fg: '#0B0C0E', bg: '#4ADE80', font: 'Anton', stroke: null },
    options: {
      caption_style: 'highlight', color: '#FFFFFF', karaoke_color: '#4ADE80',
      caption_font: 'Anton', caption_size: 96, animation: 'fade',
      emoji: true, watermark: '',
    },
  },
  {
    id: 'minimal',
    name: 'Minimal White',
    desc: 'Clean & professional',
    sample: { text: 'Key insight', fg: '#FFFFFF', bg: null, font: 'Montserrat', stroke: '#000000' },
    options: {
      caption_style: 'solid', color: '#FFFFFF', karaoke_color: '#FFE500',
      caption_font: 'Montserrat', caption_size: 56, animation: 'none',
      emoji: false, watermark: '',
    },
  },
  {
    id: 'energy',
    name: 'Karaoke Energy',
    desc: 'Words grow in — high energy',
    sample: { text: 'LET\u2019S GO!!!', fg: '#F472B6', bg: null, font: 'Poppins', stroke: '#000000' },
    options: {
      caption_style: 'solid', color: '#FFFFFF', karaoke_color: '#F472B6',
      caption_font: 'Poppins', caption_size: 96, animation: 'grow',
      emoji: true, watermark: '',
    },
  },
  {
    id: 'gaming',
    name: 'Gaming HUD',
    desc: 'Anton tebal + neon — ala KlipAja gaming',
    sample: { text: 'HEADSHOT!', fg: '#00F0FF', bg: null, font: 'Anton', stroke: '#000000' },
    options: {
      caption_style: 'gaming', color: '#FFFFFF', karaoke_color: '#00F0FF',
      caption_font: 'Anton', caption_size: 96, animation: 'pop',
      emoji: true, watermark: '',
    },
  },
  {
    id: 'bebas',
    name: 'Bebas Bold',
    desc: 'Bebas Neue — titanic & modern',
    sample: { text: 'NO WAY', fg: '#FFFFFF', bg: null, font: 'Bebas Neue', stroke: '#FF2EAE' },
    options: {
      caption_style: 'solid', color: '#FFFFFF', karaoke_color: '#FF2EAE',
      caption_font: 'Bebas Neue', caption_size: 100, animation: 'slide-left',
      emoji: false, watermark: '', caption_bg: 'glass',
    },
  },
  {
    id: 'glass',
    name: 'Glass Modern',
    desc: 'Box semi-transparan, clean',
    sample: { text: 'Insight', fg: '#FFFFFF', bg: 'rgba(0,0,0,0.5)', font: 'Poppins', stroke: null },
    options: {
      caption_style: 'box', color: '#FFFFFF', karaoke_color: '#FFE500',
      caption_font: 'Poppins', caption_size: 96, animation: 'none',
      emoji: false, watermark: '', caption_bg: 'glass',
    },
  },
]

export function isPresetActive(o, preset) {
  return Object.entries(preset.options).every(([k, v]) => o[k] === v)
}

export function normalizeOptions(o = {}) {
  const normalized = { ...DEFAULT_OPTIONS, ...o }
  // `layout` is canonical, but older saved projects used `crop`. If both are
  // present, the legacy crop is the user's explicit selection and must win;
  // otherwise a stale `layout: auto` silently turns FACE into blurred AUTO.
  normalized.layout = o.crop || o.layout || DEFAULT_OPTIONS.layout
  delete normalized.crop
  if (!FONTS.includes(normalized.caption_font)) {
    normalized.caption_font = DEFAULT_OPTIONS.caption_font
  }
  return normalized
}

function PresetCard({ preset, active, onApply }) {
  const s = preset.sample
  const previewStyle = {
    fontFamily: `'${s.font}', sans-serif`,
    color: s.fg,
    ...(s.stroke
      ? { WebkitTextStroke: `2px ${s.stroke}`, textShadow: '0 2px 0 rgba(0,0,0,0.9)' }
      : {}),
  }
  return (
    <button
      type="button"
      onClick={onApply}
      title={`${preset.name} — ${preset.desc}`}
      className={`group rounded-md border p-2 text-left transition-colors ${
        active
          ? 'border-cyan bg-cyan/15'
          : 'border-border bg-navy-2 hover:border-white/30'
      }`}
    >
      <div className="flex h-9 items-center justify-center overflow-hidden rounded border border-border-soft bg-black/60 px-1">
        <span
          className={`truncate text-[10px] font-bold leading-none ${active ? '' : 'group-hover:scale-105'}`}
          style={{ ...previewStyle, ...(s.bg ? { background: s.bg, color: s.fg, padding: '2px 5px', WebkitTextStroke: 'none', textShadow: 'none' } : {}) }}
        >
          {s.text}
        </span>
      </div>
      <div className={`mt-1.5 font-mono text-[9px] uppercase tracking-wider ${active ? 'text-cyan-soft' : 'text-white/50'}`}>
        {preset.name}
      </div>
      <div className="font-mono text-[8px] leading-tight text-white/25">{preset.desc}</div>
    </button>
  )
}

export default function CaptionSettings({ options, onChange, title = 'MACHINE SETTINGS', editorMode = false }) {
  const o = normalizeOptions(options)
  const set = (patch) => onChange({ ...o, ...patch })
  const activePreset = CAPTION_PRESETS.find((p) => isPresetActive(o, p))?.id || null

  return (
    <div className="space-y-5">
      <div>
        <Micro>Preset gaya subtitle</Micro>
        <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
          {CAPTION_PRESETS.map((p) => (
            <PresetCard
              key={p.id}
              preset={p}
              active={activePreset === p.id}
              onApply={() => set(p.options)}
            />
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-white/35">
          Mengatur teks ucapan selama video, bukan caption pembuka dan bukan layout.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Clip length" hint="Durasi klip per momen (KlipAja-style)">
        <Select value={o.clip_length} onChange={(e) => set({ clip_length: e.target.value })}>
          {CLIP_LENGTHS.map((l) => (
            <option key={l.value} value={l.value} title={l.desc}>{l.label}</option>
          ))}
        </Select>
      </Field>

      <Field label="Auto-zoom" hint="Punch-in ke momen penting">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-white/70">
          <input
            type="checkbox"
            checked={!!o.auto_zoom}
            onChange={(e) => set({ auto_zoom: e.target.checked })}
            className="h-4 w-4 rounded accent-cyan"
          />
          Zoom ke momen penting
        </label>
        {o.auto_zoom && (
          <Select
            value={o.zoom_strength}
            onChange={(e) => set({ zoom_strength: Number(e.target.value) })}
            className="mt-2"
            aria-label="zoom strength"
          >
            {ZOOM_STRENGTHS.map((z) => (
              <option key={z.value} value={z.value}>{z.label}</option>
            ))}
          </Select>
        )}
      </Field>

      <Field label="Caption pembuka (hook)" hint="Judul singkat yang tampil hanya di awal video">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-white/70">
          <input
            type="checkbox"
            checked={!!o.hook_overlay}
            onChange={(e) => set({ hook_overlay: e.target.checked })}
            className="h-4 w-4 rounded accent-cyan"
          />
          Tampilkan caption di awal klip
        </label>
        {o.hook_overlay && (
                  <>
                    <Select value={o.hook_duration || '3'} onChange={(e) => set({ hook_duration: e.target.value })} aria-label="hook duration">
                      <option value="3">3 detik</option>
                      <option value="5">5 detik</option>
                      <option value="10">10 detik</option>
                    </Select>
                    <Select value={o.hook_style || 'simple'} onChange={(e) => set({ hook_style: e.target.value })} aria-label="hook style">
                      <option value="simple">Simple — teks polos</option>
                      <option value="bold">Bold — background kotak gelap</option>
                      <option value="dynamic">Dynamic — background blur + emoji</option>
                    </Select>
                    <label className="flex cursor-pointer items-center gap-2 rounded-md border border-cyan/20 bg-cyan/5 px-2.5 py-2 text-sm text-white/75">
                      <input
                        type="checkbox"
                        checked={!!o.hook_voiceover}
                        onChange={(e) => set({ hook_voiceover: e.target.checked })}
                        className="h-4 w-4 rounded accent-cyan"
                        aria-label="bacakan caption pembuka"
                      />
                      <span className="flex flex-col">
                        <span className="font-semibold text-cyan-soft">Bacakan caption pembuka dengan AI</span>
                        <span className="font-mono text-[9px] normal-case tracking-normal text-white/40">
                          caption selesai dibaca dulu, lalu video utama mulai
                        </span>
                      </span>
                    </label>
                    {o.hook_voiceover && (
                      <>
                        <Select value={o.hook_voice || 'id-ID-ArdiNeural'} onChange={(e) => set({ hook_voice: e.target.value })} aria-label="hook voice">
                          {HOOK_VOICES.map((v) => (
                            <option key={v.value} value={v.value}>{v.label}</option>
                          ))}
                        </Select>
                        <Select value={o.hook_tone || 'natural'} onChange={(e) => set({ hook_tone: e.target.value })} aria-label="hook tone">
                          <option value="natural">Natural — normal</option>
                          <option value="santai">Santai — lebih tenang</option>
                          <option value="energik">Energik — cepat & antusias</option>
                          <option value="serius">Serius — tegas & dalam</option>
                          <option value="berita">Berita — jelas & lugas</option>
                        </Select>
                      </>
                    )}
                    <div className="space-y-2.5 rounded-md border border-border-soft bg-navy-2/40 p-2.5">
                      <div>
                        <div className="mb-1.5 font-mono text-[9px] uppercase tracking-wider text-white/35">Background caption</div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {HOOK_BGS.map((c) => (
                            <button
                              key={c.value}
                              type="button"
                              title={c.label}
                              aria-label={`hook background ${c.label}`}
                              onClick={() => set({ hook_bg: c.value, hook_bg_color: c.color || o.hook_bg_color })}
                              className={`h-7 w-7 rounded-md border transition ${
                                (o.hook_bg || 'color') === c.value ? 'border-white ring-2 ring-brand/60' : 'border-border'
                              }`}
                              style={{ background: c.swatch }}
                            />
                          ))}
                        </div>
                      </div>
                      <div>
                        <div className="mb-1.5 font-mono text-[9px] uppercase tracking-wider text-white/35">Warna teks caption</div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {COLORS.map((c) => (
                            <button
                              key={c.value}
                              type="button"
                              title={c.label}
                              aria-label={`warna teks caption ${c.label}`}
                              onClick={() => set({ hook_text_color: c.value })}
                              className={`h-7 w-7 rounded-md border transition ${
                                o.hook_text_color === c.value ? 'border-white ring-2 ring-brand/60' : 'border-border'
                              }`}
                              style={{ background: c.value }}
                            />
                          ))}
                        </div>
                      </div>
                      <p className="text-[10px] leading-snug text-white/30">
                        Caption rata tengah dan otomatis turun baris — kalimat panjang tidak terpotong.
                      </p>
                    </div>
                  </>
                )}
              </Field>

      <Field label="Background music" hint="Musik latar sintetis (gratis, tanpa lisensi)">
        <Select value={o.bgm || 'none'} onChange={(e) => set({ bgm: e.target.value })}>
          {BGMS.map((b) => (
            <option key={b.value} value={b.value}>{b.label}</option>
          ))}
        </Select>
      </Field>

      <Field label="Layout video 9:16" hint="Cara isi video dipotong ke kanvas Shorts/Reels">
        <Select value={o.layout} onChange={(e) => set({ layout: e.target.value })}>
          <option value="auto">Auto — dynamic per-scene layout</option>
          <option value="general">General — blurred fitted frame</option>
          <option value="face">Face — active speaker, no blur</option>
          <option value="split">Split — 2 speakers stacked</option>
          <option value="panel">Panel — 2 speakers side by side</option>
          <option value="gameplay">Gameplay — content + presenter 60:40</option>
        </Select>
      </Field>

      <Field label="Caption style">
        <div className="grid grid-cols-3 gap-1.5">
          {CAPTION_STYLES.map((s) => (
            <button
              key={s.id}
              type="button"
              title={s.desc}
              onClick={() => set({ caption_style: s.id })}
              className={`rounded-md border px-2 py-2 font-mono text-[10px] tracking-wider transition-colors ${
                o.caption_style === s.id
                  ? 'border-cyan bg-cyan/15 text-cyan-soft'
                  : 'border-border bg-navy-2 text-white/50 hover:border-white/30'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </Field>

      {editorMode && <Field label="Subtitle" hint="Bisa dimatikan atau digeser langsung di preview editor">
        <div className="space-y-3 rounded-md border border-border-soft bg-navy-2/40 p-2.5">
          <label className="flex cursor-pointer items-center justify-between gap-3 text-sm text-white/75">
            <span>Tampilkan subtitle</span>
            <input
              type="checkbox"
              checked={!!o.subtitles_enabled}
              onChange={(e) => set({ subtitles_enabled: e.target.checked })}
              className="h-4 w-4 rounded accent-cyan"
              aria-label="aktifkan subtitle"
            />
          </label>
          {o.subtitles_enabled && (
            <div>
              <div className="mb-1.5 flex justify-between font-mono text-[9px] uppercase tracking-wider text-white/35">
                <span>Posisi vertikal</span>
                <span className="text-cyan-soft">{o.caption_position_y}%</span>
              </div>
              <input
                type="range"
                min={45}
                max={88}
                step={1}
                value={o.caption_position_y}
                onChange={(e) => set({ caption_position_y: Number(e.target.value) })}
                className="w-full accent-cyan"
                aria-label="posisi vertikal subtitle"
              />
              <div className="flex justify-between font-mono text-[8px] text-white/25"><span>Tengah</span><span>Bawah</span></div>
            </div>
          )}
        </div>
      </Field>}

      <Field label="Background subtitle" hint="Box di belakang teks">
        <Select value={o.caption_bg || 'solid'} onChange={(e) => set({ caption_bg: e.target.value })}>
          {CAPTION_BGS.map((b) => (
            <option key={b.value} value={b.value}>{b.label}</option>
          ))}
        </Select>
        <div className="flex flex-wrap items-center gap-2">
          {COLORS.map((c) => (
            <button
              key={c.value}
              type="button"
              title={`bg ${c.label}`}
              aria-label={`caption bg color ${c.label}`}
              onClick={() => set({ caption_bg_color: c.value })}
              className={`h-7 w-7 rounded-md border transition ${o.caption_bg_color === c.value ? 'border-white ring-2 ring-brand/60' : 'border-border'}`}
              style={{ background: c.value }}
            />
          ))}
        </div>
      </Field>

      <Field label="Caption color (dasar)" hint="Warna teks subtitle sebelum kata diucapkan">
        <div className="flex flex-wrap items-center gap-2">
          {COLORS.map((c) => (
            <button
              key={c.value}
              type="button"
              title={c.label}
              aria-label={`caption color ${c.label}`}
              onClick={() => set({ color: c.value })}
              className={`h-7 w-7 rounded-md border transition ${
                o.color === c.value ? 'border-white ring-2 ring-brand/60' : 'border-border'
              }`}
              style={{ background: c.value }}
            />
          ))}
        </div>
      </Field>

      <Field label="Karaoke highlight" hint="Warna kata saat sedang diucapkan (highlight karaoke)">
        <div className="flex flex-wrap items-center gap-2">
          {COLORS.map((c) => (
            <button
              key={c.value}
              type="button"
              title={c.label}
              aria-label={`karaoke highlight color ${c.label}`}
              onClick={() => set({ karaoke_color: c.value })}
              className={`h-7 w-7 rounded-md border transition ${
                o.karaoke_color === c.value ? 'border-white ring-2 ring-brand/60' : 'border-border'
              }`}
              style={{ background: c.value }}
            />
          ))}
        </div>
      </Field>

      <Field label="Font & size">
        <div className="space-y-2">
          <div className="grid grid-cols-[1fr_auto] items-center gap-2">
            <Select value={o.caption_font} onChange={(e) => set({ caption_font: e.target.value })}>
              {FONTS.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </Select>
            <span className="w-14 text-right font-mono text-sm text-cyan-soft">
              {o.caption_size}px
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-white/30">12</span>
            <input
              type="range"
              min={FONT_SIZE_MIN}
              max={FONT_SIZE_MAX}
              step={1}
              value={o.caption_size}
              onChange={(e) => set({ caption_size: Number(e.target.value) })}
              className="w-full accent-cyan"
              aria-label="caption size"
            />
            <span className="font-mono text-[10px] text-white/30">100</span>
          </div>
        </div>
      </Field>

      <Field label="Kata per baris" hint="Semua subtitle karaoke — beda gaya lewat jumlah kata & animasi">
        <Select value={o.words_per_line} onChange={(e) => set({ words_per_line: Number(e.target.value) })}>
          {WORDS_PER_LINE.map((w) => (
            <option key={w.value} value={w.value} title={w.desc}>{w.label}</option>
          ))}
        </Select>
      </Field>

      <Field label="Animasi subtitle karaoke" hint="Kata yang sedang diucapkan berubah ke warna highlight">
        <Select value={o.animation} onChange={(e) => set({ animation: e.target.value })}>
          {ANIMATIONS.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </Select>
      </Field>

      <Field label="Watermark text" hint="Burned bottom-right, subtle (optional)">
        <TextInput
          value={o.watermark}
          onChange={(e) => set({ watermark: e.target.value })}
          placeholder="@yourhandle"
        />
      </Field>

      <div className="flex cursor-pointer items-center gap-2.5 self-end pb-2">
        <input
          type="checkbox"
          checked={o.show_klipin_watermark !== false}
          onChange={(e) => set({ show_klipin_watermark: e.target.checked })}
          className="h-4 w-4 rounded border-border accent-cyan"
        />
        <span className="inline-flex items-center gap-1 rounded-md border border-cyan/25 bg-cyan/5 px-2.5 py-1.5 text-sm text-white/75" title="Tampilkan watermark 'Klip-in' di tengah video (gratis & Mulai)">
          Tampilkan watermark Klip-in
        </span>
      </div>

      <div className="flex cursor-pointer items-center gap-2.5 self-end pb-2">
        <input
          type="checkbox"
          checked={o.emoji}
          onChange={(e) => set({ emoji: e.target.checked })}
          className="h-4 w-4 rounded border-border accent-cyan"
        />
        <span className="inline-flex items-center gap-1 rounded-md border border-cyan/25 bg-cyan/5 px-2.5 py-1.5 text-sm text-white/75" title="Emoji otomatis dari kata kunci multi-bahasa (uang→💰, wow→🤯, menang→🏆)">
          Smart emojis <Icon name="sparkles" className="h-3.5 w-3.5" />
        </span>
      </div>
      </div>
    </div>
  )
}
