import { useEffect, useRef, useState } from 'react'
import { createIdea, deleteIdea, listIdeas, timeAgo, updateIdea } from './api.js'
import { Button, Micro, Mono, Select, Spinner, StatusDot, TextInput } from './components/ui.jsx'
import Icon from './components/Icon.jsx'

const COLUMNS = [
  { status: 'idea', label: 'IDEA', tone: 'idle', hint: 'Masih gagasan — belum dikerjakan' },
  { status: 'draft', label: 'DRAFT', tone: 'run', hint: 'Klip sedang dibuat / menunggu render' },
  { status: 'published', label: 'PUBLISHED', tone: 'ok', hint: 'Sudah tayang atau terjadwal' },
]

const PLATFORMS = [
  { value: '', label: 'Tanpa platform' },
  { value: 'youtube', label: 'YouTube Shorts' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'instagram', label: 'Instagram Reels' },
]

const PLATFORM_DOTS = { youtube: '#FF0033', tiktok: '#69C9D0', instagram: '#E1306C' }

function toLocalInput(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function Ideas({ onOpenProject, user, onSignIn }) {
  const [ideas, setIdeas] = useState(null)
  const [err, setErr] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', platform: '', scheduled_at: '' })
  const [saving, setSaving] = useState(false)
  // Debounce the scheduled-at input: the picker fires onChange for every step,
  // so we coalesce those into a single PATCH instead of one request per tick.
  const scheduleTimer = useRef(null)

  const load = async () => {
    setErr('')
    try {
      const data = await listIdeas()
      setIdeas(data.ideas || [])
    } catch (e) {
      setErr(e.message)
      setIdeas([])
    }
  }

  useEffect(() => { load() }, [])

  const add = async () => {
    if (!form.title.trim()) return
    setSaving(true)
    setErr('')
    try {
      const ts = form.scheduled_at ? new Date(form.scheduled_at).getTime() / 1000 : null
      await createIdea({
        title: form.title.trim(),
        description: form.description.trim(),
        platform: form.platform,
        scheduled_at: ts,
      })
      setForm({ title: '', description: '', platform: '', scheduled_at: '' })
      setShowForm(false)
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  const move = async (idea, status) => {
    if (idea.status === status) return
    try {
      setIdeas((is) => is.map((i) => (i.id === idea.id ? { ...i, status } : i)))
      await updateIdea(idea.id, { status })
      await load()
    } catch (e) {
      setErr(e.message)
      await load()
    }
  }

  const patch = async (idea, fields) => {
    try {
      await updateIdea(idea.id, fields)
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const patchScheduledAt = (idea, value) => {
    clearTimeout(scheduleTimer.current)
    scheduleTimer.current = setTimeout(() => {
      patch(idea, { scheduled_at: value ? new Date(value).getTime() / 1000 : null })
    }, 500)
  }

  const remove = async (idea) => {
    if (!window.confirm(`Hapus ide "${idea.title}"?`)) return
    try {
      await deleteIdea(idea.id)
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const requireUser = () => {
    if (!user) {
      onSignIn()
      return false
    }
    return true
  }

  const card = (idea) => {
    const dt = idea.scheduled_at ? new Date(idea.scheduled_at * 1000) : null
    const overdue = dt && dt.getTime() < Date.now() && idea.status !== 'published'
    return (
      <div
        key={idea.id}
        className="card-chrome group rounded-lg p-3 transition hover:border-cyan/40"
      >
        {/* inline title (committed on blur — no PATCH per keystroke) */}
        <input
          key={`t-${idea.updated_at}`}
          defaultValue={idea.title}
          onBlur={(e) => {
            const v = e.target.value.trim()
            if (v && v !== idea.title) patch(idea, { title: v })
          }}
          className="w-full bg-transparent text-sm font-semibold text-white outline-none placeholder:text-white/25"
          aria-label="idea title"
        />
        {idea.description && (
          <textarea
            key={`d-${idea.updated_at}`}
            defaultValue={idea.description}
            onBlur={(e) => {
              const v = e.target.value.trim()
              if (v !== idea.description) patch(idea, { description: v })
            }}
            rows={2}
            className="mt-1 w-full resize-y bg-transparent text-xs text-white/55 outline-none"
            aria-label="idea description"
          />
        )}
        {idea.job_id && (
          <button
            type="button"
            onClick={() => onOpenProject(idea.job_id)}
            className="mt-2 block w-full truncate rounded border border-border-soft bg-navy-2/60 px-2 py-1 text-left font-mono text-[10px] text-cyan-soft/70 transition hover:border-cyan/40 hover:text-cyan-soft"
            title="Buka project di Library"
          >
            ⧉ project {idea.job_id.slice(0, 12)}…
          </button>
        )}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-white/40">
          {idea.platform && (
            <span className="flex items-center gap-1 rounded border border-border-soft bg-navy-2/60 px-1.5 py-0.5">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: PLATFORM_DOTS[idea.platform] || '#888' }} />
              {PLATFORMS.find((p) => p.value === idea.platform)?.label.replace(/^.* /, '') || idea.platform}
            </span>
          )}
          {dt && (
            <span className={`rounded border px-1.5 py-0.5 ${overdue ? 'border-danger/40 text-danger' : 'border-border-soft'}`}>
              <Icon name="calendar" className="h-3 w-3 inline" /> {dt.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' })}
            </span>
          )}
          <span className="ml-auto text-white/25">{timeAgo(idea.created_at)}</span>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <Select
            value={idea.platform}
            onChange={(e) => patch(idea, { platform: e.target.value })}
            aria-label="platform"
            className="w-32 !py-1 !text-[10px]"
          >
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </Select>
          <input
            type="datetime-local"
            value={toLocalInput(idea.scheduled_at)}
            onChange={(e) => patchScheduledAt(idea, e.target.value)}
            className="rounded border border-border bg-navy-2 px-1.5 py-1 text-[10px] text-white/60 outline-none focus:border-cyan [color-scheme:dark]"
            aria-label="jadwal publish"
          />
          <button
            type="button"
            onClick={() => remove(idea)}
            aria-label="hapus ide"
            className="p-1 text-white/25 transition hover:text-danger"
          >
            <Icon name="close" className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Micro className="block">SYS.IDEAS — CONTENT CALENDAR</Micro>
          <h1 className="text-chrome mt-2 text-2xl font-bold tracking-tight">Ideas board</h1>
          <p className="mt-1 text-sm text-white/45">
            Rencanakan konten: ide → draft → published. Jadwalkan tanggal tayang per platform.
          </p>
        </div>
        <Button variant="cyan" size="sm" onClick={() => { if (requireUser()) setShowForm((v) => !v) }}>
          {showForm ? <><Icon name="close" className="h-3.5 w-3.5" /> Tutup</> : <><Icon name="plus" className="h-3.5 w-3.5" /> Ide baru</>}
        </Button>
      </div>

      {err && (
        <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
          {err}
        </p>
      )}

      {showForm && (
        <div className="card-chrome mb-6 rounded-xl p-4">
          <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-soft/70">
            NEW IDEA
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextInput
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="Judul ide — cth: Podcast #12 cara jualan online"
              aria-label="judul ide"
              className="sm:col-span-2"
            />
            <TextInput
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="Deskripsi / angle singkat (opsional)"
              aria-label="deskripsi ide"
              className="sm:col-span-2"
            />
            <Select value={form.platform} onChange={(e) => setForm((f) => ({ ...f, platform: e.target.value }))} aria-label="platform">
              {PLATFORMS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </Select>
            <input
              type="datetime-local"
              value={form.scheduled_at}
              onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))}
              className="rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-sm text-white outline-none transition focus:border-cyan [color-scheme:dark]"
              aria-label="jadwal tayang"
            />
          </div>
          <Button variant="cyan" onClick={add} disabled={saving || !form.title.trim()} className="mt-3">
            {saving ? <Spinner className="h-3.5 w-3.5" /> : 'Simpan ide'}
          </Button>
        </div>
      )}

      {ideas === null ? (
        <div className="flex items-center justify-center gap-2 py-20 font-mono text-xs text-white/30">
          <Spinner className="h-4 w-4" /> memuat ideas…
        </div>
      ) : ideas.length === 0 && !showForm ? (
        <div className="card-chrome rounded-xl border-dashed py-16 text-center">
          <div className="text-3xl">🗓️</div>
          <p className="mt-2 text-sm text-white/50">Belum ada ide. Klik <span className="text-cyan-soft">+ Ide baru</span> untuk mulai.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          {COLUMNS.map((col) => {
            const items = (ideas || []).filter((i) => i.status === col.status)
            return (
              <div key={col.status} className="rounded-xl border border-border-soft bg-navy-2/30 p-3">
                <div className="mb-3 flex items-center gap-2">
                  <StatusDot tone={col.tone} />
                  <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/60">
                    {col.label}
                  </span>
                  <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/50">
                    {items.length}
                  </span>
                  <span className="hidden font-mono text-[9px] text-white/25 xl:inline">{col.hint}</span>
                </div>
                <div className="space-y-2">
                  {items.map((idea) => (
                    <div key={idea.id}>
                      {card(idea)}
                      {/* quick move buttons */}
                      <div className="mt-1 flex gap-1 px-1">
                        {COLUMNS.filter((c) => c.status !== idea.status).map((c) => (
                          <button
                            key={c.status}
                            type="button"
                            onClick={() => move(idea, c.status)}
                            className="flex-1 rounded border border-border bg-navy-2/60 px-1 py-0.5 font-mono text-[9px] uppercase tracking-wider text-white/35 transition hover:border-cyan/40 hover:text-cyan-soft"
                          >
                            → {c.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                  {items.length === 0 && (
                    <p className="rounded border border-dashed border-border-soft py-6 text-center font-mono text-[10px] text-white/20">
                      kosong
                    </p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
