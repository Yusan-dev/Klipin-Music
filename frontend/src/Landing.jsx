import { useEffect, useState } from 'react'
import { getTrending, listProjects, resolveAsset, timeAgo } from './api.js'
import {
  Button, Logo, Micro, Mono, Panel, StatusDot, TextInput, VUMeter,
} from './components/ui.jsx'
import Icon from './components/Icon.jsx'

/**
 * Home = compact dashboard (KlipAja-style app layout, not a long landing page):
 * quick clip intake on the left, recent projects on the right, and module
 * shortcuts below. Marketing content lives in the Features/Pricing modules.
 */
export default function Landing({ onStart, onStartFile, onOpen, onOpenLibrary, onOpenFeatures, onOpenPricing }) {
  const [url, setUrl] = useState('')
  const [projects, setProjects] = useState(null)
  const [hintOpen, setHintOpen] = useState(() => {
    try { return !localStorage.getItem('klipin_seen_hint_v1') } catch { return false }
  })

  const dismissHint = () => {
    setHintOpen(false)
    try { localStorage.setItem('klipin_seen_hint_v1', '1') } catch { /* ignore */ }
  }

  const load = () => {
    listProjects()
      .then((d) => setProjects(d.projects || []))
      .catch(() => setProjects([]))
  }

  useEffect(() => { load() }, [])

  const recent = (projects || []).slice(0, 5)

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      {/* quick intake — hero stays short */}
      <Panel
        label="Buat klip baru"
        dot="brand"
        className="relative overflow-hidden shadow-neon-cyan"
        bodyClassName="relative px-5 py-6 sm:px-7"
      >
        {/* hero decorative texture — organic lines behind hero content */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.18]"
          style={{ backgroundImage: 'url(/brand/backgrounds/hero-lines-grid.svg)', backgroundSize: 'cover', backgroundPosition: 'center' }}
          aria-hidden="true"
        />
        <div className="flex flex-col gap-6 lg:flex-row lg:items-center">
          <div className="min-w-0 flex-1">
            <h1 className="text-chrome font-display text-2xl font-extrabold tracking-tight sm:text-3xl">
              Ubah video panjang jadi <span className="text-neon">klip viral 9:16</span>
            </h1>
            <p className="mt-2 text-sm text-white/50">
              AI pilih momen terbaik · subtitle karaoke Indonesia · siap upload ke TikTok, Reels &amp; Shorts.
            </p>
            <form
              className="mt-5 flex items-stretch gap-2"
              onSubmit={(e) => { e.preventDefault(); if (url.trim()) { dismissHint(); onStart(url.trim()) } }}
            >
              <TextInput
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="Tempel link YouTube / TikTok di sini…"
                className="flex-1 border-cyan/40 bg-navy-2/80 px-4 py-3 text-base font-medium placeholder:text-white/25"
                aria-label="video URL"
              />
              <Button type="submit" size="lg" variant="primary" className="px-6">
                Buat klip →
              </Button>
            </form>
            {hintOpen && projects !== null && projects.length === 0 && (
              <div className="mt-3 inline-flex flex-wrap items-center gap-2 rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-xs text-cyan-soft">
                <Icon name="ideas" className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>Tempel link video pertamamu di atas — AI pilih momen, kamu tinggal upload.</span>
                <button
                  type="button"
                  onClick={dismissHint}
                  aria-label="Tutup tips"
                  className="ml-auto rounded p-0.5 text-cyan-soft/70 transition hover:text-white"
                >
                  <Icon name="close" className="h-3 w-3" aria-hidden />
                </button>
              </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-[0.15em] text-white/45">
              <span className="flex items-center gap-1.5 rounded border border-border px-2 py-1">
                <StatusDot tone="ok" /> render server
              </span>
              <span className="rounded border border-border px-2 py-1">±Rp25 / video</span>
              <span className="rounded border border-border px-2 py-1">tanpa cookie akun</span>
            </div>

            {/* quick source actions — paste or upload, one tap */}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <QuickAction icon="upload" label="Upload file" hint="MP4 / MOV / MKV" onClick={() => document.getElementById('klipin-file-input')?.click()} />
              <QuickAction icon="link" label="Dari link" hint="YouTube / TikTok" onClick={() => document.querySelector('input[aria-label="video URL"]')?.focus()} />
              <QuickAction icon="stack" label="Batch" hint="banyak video" onClick={() => { dismissHint(); onStart('') }} />
            </div>
            <input
              id="klipin-file-input"
              type="file"
              accept="video/*,.mp4,.mov,.mkv,.webm"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onStartFile(f) }}
            />
          </div>

          {/* live product preview — 9:16 phone with karaoke caption demo */}
          <div className="shrink-0 lg:w-80">
            <PhonePreview />
            <div className="mt-4 grid grid-cols-2 gap-3">
              <ModuleCard icon="library" label="Library" desc="Semua project tersimpan" tone="cyan" onClick={onOpenLibrary} />
              <ModuleCard icon="features" label="Fitur" desc="Layout, karaoke, editor cut" tone="brand" onClick={onOpenFeatures} />
              <ModuleCard icon="pricing" label="Harga" desc="Kredit prabayar, tidak hangus" tone="gold" onClick={onOpenPricing} className="col-span-2" />
            </div>
          </div>
        </div>
      </Panel>

      {/* stats strip — personal numbers once the user has projects */}
      <StatsStrip projects={projects} />

      {/* trending channels — dynamic per category, 1-click to clip */}
      <TrendingChannels onClip={(videoUrl) => { dismissHint(); onStart(videoUrl) }} />

      {/* how-it-works onboarding strip */}
      <HowItWorks />

      {/* recent projects */}
      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <Micro className="flex items-center gap-2">
            <StatusDot tone="ok" /> Recent projects
          </Micro>
          <button
            type="button"
            onClick={onOpenLibrary}
            className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/50 transition hover:text-white"
          >
            lihat semua →
          </button>
        </div>

        {!projects ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="card-chrome overflow-hidden rounded-xl" aria-hidden>
                <div className="aspect-video animate-pulse-soft bg-white/5" />
                <div className="space-y-2 px-3 py-3">
                  <div className="h-3.5 w-2/3 animate-pulse-soft rounded bg-white/10" style={{ animationDelay: `${i * 0.2}s` }} />
                  <div className="h-2.5 w-1/3 animate-pulse-soft rounded bg-white/5" style={{ animationDelay: `${i * 0.2 + 0.1}s` }} />
                </div>
              </div>
            ))}
          </div>
        ) : recent.length === 0 ? (
          <Panel bodyClassName="p-10 text-center">
            <Icon name="film" className="mx-auto h-10 w-10 text-cyan/50" aria-hidden />
            <p className="mt-3 font-display text-sm font-bold text-white/80">Belum ada klip</p>
            <p className="mt-1 text-sm text-white/50">Tempel link video pertamamu — beberapa menit jadi klip siap upload.</p>
            <Button variant="primary" size="sm" className="mt-4" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
              Buat klip sekarang
            </Button>
          </Panel>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {recent.map((p) => (
              <ProjectCard key={p.job_id} p={p} onOpen={onOpen} />
            ))}
          </div>
        )}
      </div>

      {/* footer strip */}
      <footer className="mt-10 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 border-t border-border-soft pt-6 pb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-white/30">
        <span className="flex items-center gap-2">
          <Logo size={16} /> KLIP-IN v1.0
        </span>
        <span>DIPERCEPAT 100% OLEH AI · CEPAT · AKURAT</span>
        <span className="flex items-center gap-1.5">
          <Icon name="mail" className="h-3 w-3" />
          <a href="mailto:support@klip-in.id" className="transition hover:text-cyan-soft" title="Email dukungan Klip-in">
            support@klip-in.id
          </a>
        </span>
        <span>© 2026 · BUILT FOR INDONESIAN CREATORS 🇮🇩</span>
      </footer>
    </div>
  )
}

function ModuleCard({ icon, label, desc, tone, onClick, className = '' }) {
  const toneBorder = {
    cyan: 'border-cyan/40 hover:border-cyan/70 hover:shadow-neon-cyan',
    magenta: 'border-magenta/40 hover:border-magenta/70 hover:shadow-neon-magenta',
    brand: 'border-cyan/40 hover:border-cyan/70 hover:shadow-neon-cyan',
    gold: 'border-gold/40 hover:border-gold/70 hover:shadow-neon-gold',
  }[tone] || 'border-border hover:border-cyan/50'

  return (
    <button
      type="button"
      onClick={onClick}
      className={`card-chrome rounded-lg border p-3 text-left transition-all ${toneBorder} ${className}`}
    >
      <Icon name={icon} className="h-5 w-5" />
      <div className="mt-1 font-mono text-[10px] font-semibold uppercase tracking-widest text-white/80">{label}</div>
      <div className="mt-0.5 text-[11px] leading-snug text-white/40">{desc}</div>
    </button>
  )
}

/** Small labelled quick-action chip (upload / paste-link / batch). */
function QuickAction({ icon, label, hint, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint}
      className="group flex items-center gap-1.5 rounded-md border border-border bg-navy-2/60 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-widest text-white/50 transition hover:border-cyan/50 hover:text-cyan-soft"
    >
      <Icon name={icon} className="h-3.5 w-3.5 text-cyan/60 transition group-hover:text-cyan" />
      {label}
    </button>
  )
}

/** 4-step how-it-works strip (KlipAja/Opus style) — turns first-time visitors into users. */
const STEPS = [
  { icon: 'link', title: 'Tempel link', desc: 'YouTube / TikTok / file upload — sampai 2 GB' },
  { icon: 'sparkles', title: 'AI pilih momen', desc: 'Transkrip + hook score, 3–15 klip viral' },
  { icon: 'paint', title: 'Subtitle karaoke', desc: 'Bahasa Indonesia, timing per kata' },
  { icon: 'rocket', title: 'Upload langsung', desc: 'TikTok, Reels & Shorts — atau download ZIP' },
]

function HowItWorks() {
  return (
    <section className="mt-10">
      <div className="mb-4 flex items-center gap-2">
        <Micro className="flex items-center gap-2">
          <StatusDot tone="run" pulse /> Cara kerja — 4 langkah
        </Micro>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <div key={s.title} className="card-chrome group relative rounded-xl p-4">
            <div className="absolute right-3 top-3 font-display text-2xl font-extrabold text-cyan/20 transition group-hover:text-cyan/50">
              {i + 1}
            </div>
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan/30 bg-cyan/10 text-cyan-soft">
              <Icon name={s.icon} className="h-4 w-4" />
            </div>
            <h3 className="mt-3 font-display text-sm font-bold tracking-wide text-white">{s.title}</h3>
            <p className="mt-1 text-xs leading-relaxed text-white/45">{s.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

const KARAOKE_WORDS = ['Kliping', 'ini', 'bakal', 'viral', 'banget!']

/**
 * Decorative 9:16 phone preview with an animated karaoke caption demo —
 * shows what the product does without a video asset. Always-dark chrome,
 * so true white is safe here (.text-white-solid / literal rgba).
 */
function PhonePreview() {
  return (
    <div className="relative mx-auto w-40 sm:w-44 lg:w-56 lg:h-[320] lg:aspect-[9/16] md:aspect-[9/16] aspect-[4/3] md:aspect-[3/2] rounded-md bg-navy-2 overflow-hidden" aria-hidden>
      {/* ambient glow */}
      <div className="absolute -inset-5 rounded-[30px] bg-cyan/10 blur-2xl" />

      {/* subtle perspective-grid texture only on larger screens */}
      {typeof window !== 'undefined' && window.matchMedia('(min-width: 1024px)').matches && (
        <div
          className="pointer-events-none absolute inset-0 rounded-xl opacity-[0.08] mix-blend-multiply"
          style={{ backgroundImage: 'url(/brand/backgrounds/perspective-grid.svg)', backgroundSize: 'cover' }}
          aria-hidden="true"
        />
      )}

      <div className="porthole relative aspect-[9/16] overflow-hidden bg-navy-2">
              {/* faux video frame */}
              <div className="absolute inset-0 bg-gradient-to-b from-navy-3 via-panel-2 to-navy" />
        <div className="absolute inset-0 bg-grid opacity-60" />
        {/* speaker silhouette */}
        <div className="absolute inset-x-0 top-7 flex flex-col items-center">
          <div className="h-11 w-11 rounded-full bg-white-solid/15" />
          <div className="-mt-1 h-16 w-28 rounded-t-[999px] bg-white-solid/10" />
        </div>

        <span className="chip absolute left-2 top-2 !text-[9px]">9:16</span>
        <VUMeter bars={4} className="absolute right-2 top-2" />

        {/* karaoke caption strip */}
        <div className="absolute inset-x-2 bottom-7 rounded-lg bg-black/50 px-2 py-1.5 backdrop-blur-sm">
          <p className="text-center font-sans text-[13px] font-bold leading-snug">
            {KARAOKE_WORDS.map((w, i) => (
              <span
                key={i}
                className="animate-karaoke mr-1 inline-block"
                style={{ animationDelay: `${(i * 0.26).toFixed(2)}s` }}
              >
                {w}
              </span>
            ))}
          </p>
        </div>

        {/* playback progress */}
        <div className="absolute inset-x-3 bottom-3 h-1 overflow-hidden rounded-full bg-white-solid/20">
          <div className="h-full w-1/2 animate-sweep rounded-full bg-gold" />
        </div>
      </div>

      {/* floating badges */}
      <div className="absolute -right-5 top-9 animate-floaty rounded-lg border border-signal/40 bg-navy-2/95 px-2 py-1 shadow-neon-cyan">
        <span className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-signal">
          <Icon name="checkcircle" className="h-3 w-3" /> +3 klip
        </span>
      </div>
      <div
        className="absolute -left-6 bottom-16 animate-floaty rounded-lg border border-magenta/40 bg-navy-2/95 px-2 py-1.5"
        style={{ animationDelay: '1.3s' }}
      >
        <Icon name="scissors" className="h-4 w-4 animate-scissors text-magenta" />
      </div>
    </div>
  )
}

const TRENDING_TABS = [
  { id: 'berita', label: 'Berita' },
  { id: 'podcast', label: 'Podcast' },
  { id: 'olahraga', label: 'Olahraga' },
]

function fmtViews(n) {
  if (n == null) return null
  if (n >= 1e9) return `${(n / 1e9).toFixed(1).replace('.', ',')} M`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace('.', ',')} jt`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace('.', ',')} rb`
  return String(n)
}

/**
 * "Lagi ramai" — 5 channel YouTube yang sedang trending per kategori.
 * Dinamis: diambil live dari server (ytsearch + cache 6 jam), jadi daftarnya
 * berubah-ubah mengikuti YouTube. Klik kartu = langsung masuk intake klip.
 */
function TrendingChannels({ onClip }) {
  const [tab, setTab] = useState('berita')
  const [data, setData] = useState(null) // {channels, updated_at, stale}
  const [failed, setFailed] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const load = (force = false) => {
    let cancelled = false
    if (force) setRefreshing(true)
    else { setData(null); setFailed(false) }
    getTrending(tab, force)
      .then((d) => { if (!cancelled) { setData(d); setFailed(false) } })
      .catch(() => { if (!cancelled && !force) setFailed(true) })
      .finally(() => { if (!cancelled) setRefreshing(false) })
    return () => { cancelled = true }
  }

  useEffect(() => load(false), [tab])

  if (failed) return null // backend 503 — jangan tampilkan section rusak

  const channels = data?.channels || []

  return (
    <section className="mt-10">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Micro className="flex items-center gap-2">
          <StatusDot tone="run" pulse /> Lagi ramai di YouTube
        </Micro>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => load(true)}
            disabled={refreshing}
            title="Refresh trending sekarang (lewati cache 1 jam)"
            className="rounded-full border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-[10px] text-white/45 transition hover:border-cyan/40 hover:text-white/75 disabled:opacity-40"
          >
            {refreshing ? '⏳' : '⟳'}
          </button>
          {TRENDING_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              aria-pressed={tab === t.id}
              className={`rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-wider transition ${
                tab === t.id
                  ? 'border-cyan bg-cyan/15 text-cyan-soft'
                  : 'border-border bg-navy-2/60 text-white/45 hover:border-cyan/40 hover:text-white/75'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {!data ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="card-chrome overflow-hidden rounded-xl" aria-hidden>
              <div className="aspect-video animate-pulse-soft bg-white/5" />
              <div className="space-y-2 px-3 py-3">
                <div className="h-3.5 w-2/3 animate-pulse-soft rounded bg-white/10" style={{ animationDelay: `${i * 0.15}s` }} />
                <div className="h-2.5 w-1/3 animate-pulse-soft rounded bg-white/5" />
              </div>
            </div>
          ))}
        </div>
      ) : channels.length === 0 ? (
        <Panel bodyClassName="p-6 text-center">
          <p className="text-sm text-white/50">Belum ada data trending — coba tab lain.</p>
        </Panel>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {channels.map((c) => (
              <button
                key={c.channel + (c.top_video?.id || '')}
                type="button"
                onClick={() => c.top_video?.url && onClip(c.top_video.url)}
                title={`Klip video terbaru ${c.channel}`}
                className="card-chrome group overflow-hidden rounded-xl text-left transition-all hover:border-cyan/50 hover:shadow-neon-cyan"
              >
                <div className="relative aspect-video overflow-hidden bg-navy-2">
                  {c.top_video?.thumb && (
                    <img
                      src={c.top_video.thumb}
                      alt=""
                      loading="lazy"
                      className="absolute inset-0 h-full w-full object-cover"
                      onError={(e) => { e.currentTarget.style.display = 'none' }}
                    />
                  )}
                  <span className="absolute inset-0 flex items-center justify-center">
                    <span className="flex h-10 w-10 items-center justify-center rounded-full border border-cyan/60 bg-navy/60 backdrop-blur transition-transform group-hover:scale-110 group-hover:shadow-neon-cyan">
                      <Icon name="play" className="ml-0.5 h-4 w-4 text-white-solid" aria-hidden />
                    </span>
                  </span>
                  {c.top_video?.view_count != null && (
                    <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white-solid">
                      👁 {fmtViews(c.top_video.view_count)}
                    </span>
                  )}
                </div>
                <div className="px-3 py-2.5">
                  <div className="truncate text-sm font-semibold text-white/85">{c.channel}</div>
                  <div className="mt-0.5 line-clamp-2 min-h-[2em] text-[11px] leading-snug text-white/45">
                    {c.top_video?.title}
                  </div>
                  <div className="mt-1.5 font-mono text-[10px] uppercase tracking-widest text-cyan-soft/80 transition group-hover:text-cyan-soft">
                    Klip video ini →
                  </div>
                </div>
              </button>
            ))}
          </div>
          {data?.updated_at && (
            <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.15em] text-white/30">
              diperbarui {timeAgo(data.updated_at)}{data.stale ? ' · data cache' : ''}
            </p>
          )}
        </>
      )}
    </section>
  )
}

function fmtDur(s) {
  if (s == null) return null
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return `${m}:${String(r).padStart(2, '0')}`
}

function ProjectCard({ p, onOpen }) {
  const done = p.status === 'completed'
  const tone = done ? 'ok' : p.status === 'failed' ? 'err' : 'run'
  const dur = fmtDur(p.duration)
  const hue = [...(p.job_id || '')].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7)

  return (
    <button
      type="button"
      disabled={!done}
      onClick={() => onOpen(p.job_id)}
      title={done ? 'Buka project' : `Status: ${p.status}`}
      className={`card-chrome group overflow-hidden rounded-xl text-left transition-all ${
        done ? 'hover:border-cyan/50 hover:shadow-neon-cyan' : 'cursor-default opacity-75'
      }`}
    >
      {/* visual: real first-clip thumbnail when available, generated gradient otherwise */}
      <div
        className="relative aspect-video overflow-hidden"
        style={{ background: `linear-gradient(135deg, hsl(${hue} 65% 24%), hsl(${(hue + 45) % 360} 75% 12%))` }}
      >
        {p.thumb ? (
          <img
            src={resolveAsset(p.thumb)}
            alt=""
            loading="lazy"
            className="absolute inset-0 h-full w-full object-cover"
            onError={(e) => { e.currentTarget.style.display = 'none' }}
          />
        ) : (
          <>
            <div className="absolute inset-0 bg-grid opacity-50" aria-hidden />
            <Icon name="film" className="absolute left-3 top-3 h-5 w-5 text-white-solid opacity-60" aria-hidden />
          </>
        )}
        {done && (
          <span className="absolute inset-0 flex items-center justify-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-full border border-cyan/60 bg-navy/60 backdrop-blur transition-transform group-hover:scale-110 group-hover:shadow-neon-cyan">
              <Icon name="play" className="ml-0.5 h-5 w-5 text-white-solid" aria-hidden />
            </span>
          </span>
        )}
        {dur && (
          <span className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white-solid">
            {dur}
          </span>
        )}
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white-solid">
          {p.clip_count} klip
        </span>
      </div>

      {/* meta */}
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <StatusDot tone={tone} pulse={p.status === 'processing'} />
          <span title={p.source_title || p.source_label} className="line-clamp-2 text-sm font-semibold text-white/85">{p.source_title || p.source_label || 'Tanpa judul'}</span>
        </div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="truncate font-mono text-[10px] uppercase tracking-widest text-white/45">
            {timeAgo(p.created_at)}
          </span>
          {p.cost?.total_idr ? (
            <Mono className="shrink-0 text-xs text-warn">≈ Rp{p.cost.total_idr.toLocaleString('id-ID')}</Mono>
          ) : (
            <span className={`shrink-0 font-mono text-[10px] uppercase tracking-widest ${done ? 'text-signal' : 'text-gold'}`}>
              {done ? 'siap' : p.status}
            </span>
          )}
        </div>
      </div>
    </button>
  )
}

const STATS = [
  { value: '9:16', label: 'output siap viral' },
  { value: '±Rp25', label: 'biaya per video' },
  { value: '7', label: 'mode layout' },
  { value: '100%', label: 'render di server' },
]

function personalStats(projects) {
  let clips = 0
  let secs = 0
  let idr = 0
  for (const p of projects) {
    clips += p.clip_count || 0
    secs += p.duration || 0
    idr += p.cost?.total_idr || 0
  }
  const minutesLabel = secs >= 3600
    ? `${(secs / 3600).toFixed(1).replace('.', ',')} jam`
    : `${Math.round(secs / 60)} mnt`
  return {
    videos: projects.length,
    clips,
    minutesLabel,
    costLabel: idr > 0 ? `≈ Rp${Math.round(idr).toLocaleString('id-ID')}` : 'Rp0',
  }
}

/** Personal usage numbers (gold) once the user has projects; product facts (cyan) otherwise. */
function StatsStrip({ projects }) {
  if (!projects) {
    return (
      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card-chrome animate-pulse-soft rounded-lg px-3 py-4" style={{ animationDelay: `${(i * 0.15).toFixed(2)}s` }}>
            <div className="h-6 w-12 rounded bg-white/10" />
            <div className="mt-2 h-2.5 w-20 rounded bg-white/5" />
          </div>
        ))}
      </div>
    )
  }
  const hasData = projects.length > 0
  const s = hasData ? personalStats(projects) : null
  const items = hasData
    ? [
        { value: String(s.videos), label: 'video kamu proses' },
        { value: String(s.clips), label: 'klip viral dibuat' },
        { value: s.minutesLabel, label: 'footage diproses' },
        { value: s.costLabel, label: 'total biaya' },
      ]
    : STATS
  return (
    <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.map((st) => (
        <div key={st.label} className="card-chrome rounded-lg px-3 py-3">
          <div className={`font-display text-xl font-bold ${hasData ? 'text-gold-soft' : 'text-cyan-soft'}`}>{st.value}</div>
          <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-white/50">{st.label}</div>
        </div>
      ))}
    </div>
  )
}
