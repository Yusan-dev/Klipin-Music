import { useEffect, useMemo, useState } from 'react'
import { deleteProject, listProjects, resolveAsset, timeAgo } from './api.js'
import { Button, Micro, Mono, Panel, Select, Spinner, StatusDot, TextInput } from './components/ui.jsx'
import Icon from './components/Icon.jsx'
import Loader from './components/Loader.jsx'

const STATUS_META = {
  completed: { tone: 'ok', label: 'COMPLETED' },
  failed: { tone: 'err', label: 'FAILED' },
  processing: { tone: 'run', label: 'PROCESSING' },
  queued: { tone: 'idle', label: 'QUEUED' },
}

const STATUS_FILTERS = [
  { value: 'all', label: 'Semua status' },
  { value: 'completed', label: 'Selesai' },
  { value: 'failed', label: 'Gagal' },
  { value: 'processing', label: 'Proses' },
  { value: 'queued', label: 'Antre' },
]

export default function Library({ onHome, onOpen, user, onSignIn }) {
  const [projects, setProjects] = useState(null)
  const [err, setErr] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')

  const load = async () => {
    setErr('')
    try {
      const data = await listProjects()
      setProjects(data.projects || [])
    } catch (e) {
      setErr(e.message)
      setProjects([])
    }
  }

  useEffect(() => { load() }, [])

  const filtered = useMemo(() => {
    if (!projects) return []
    const q = query.trim().toLowerCase()
    return projects.filter((p) => {
      if (status !== 'all' && p.status !== status) return false
      if (!q) return true
      const hay = `${p.source_title || ''} ${p.source_label || ''} ${p.job_id || ''}`.toLowerCase()
      return hay.includes(q)
    })
  }, [projects, query, status])

  const hasActiveFilter = query.trim() !== '' || status !== 'all'

  const remove = async (id) => {
    if (!user) {
      onSignIn()
      return
    }
    if (!window.confirm('Hapus project ini beserta semua klipnya dari disk?')) return
    try {
      await deleteProject(id)
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <Micro className="block">SYS.LIBRARY — PROJECT INDEX</Micro>
          <h1 className="text-chrome mt-2 text-2xl font-bold tracking-tight">Riwayat project</h1>
        </div>
        <Button variant="ghost" size="sm" onClick={onHome}>+ Klip baru</Button>
      </div>
      <p className="mt-2 text-center text-sm text-white/45">
        Setiap video yang diproses tersimpan di sini — buka ulang, unduh lagi, atau hapus.
      </p>

      {/* search + filter bar */}
      {projects && projects.length > 0 && (
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <TextInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Cari judul video / sumber / job id…"
              className="w-full !py-2 pl-8"
              aria-label="search projects"
            />
            <span
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/30"
            >
              <Icon name="search" className="h-4 w-4" />
            </span>
          </div>
          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="w-40 !py-2"
            aria-label="filter by status"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </Select>
          <Mono className="text-white/35">
            {filtered.length}/{projects.length} project{projects.length === 1 ? '' : 's'}
          </Mono>
          {hasActiveFilter && (
            <button
              type="button"
              onClick={() => { setQuery(''); setStatus('all') }}
              className="font-mono text-[10px] uppercase tracking-widest text-white/40 transition hover:text-white"
            >
              clear <Icon name="close" className="h-3 w-3" />
            </button>
          )}
        </div>
      )}

      <div className="mt-8">
        {!projects && <Loader label="membaca index library…" />}
        {err && <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{err}</p>}

        {projects && projects.length === 0 && (
          <Panel className="mx-auto max-w-xl text-center" bodyClassName="p-10">
            <Icon name="film" className="mx-auto h-10 w-10 text-cyan/50" aria-hidden />
            <p className="mt-3 font-display text-sm font-bold text-white/80">Belum ada klip</p>
            <p className="mt-1 text-sm text-white/55">
              Klip video pertamamu untuk melihatnya di sini.
            </p>
            <Button variant="primary" size="sm" className="mt-4" onClick={onHome}>
              Buat klip sekarang
            </Button>
          </Panel>
        )}

        {projects && projects.length > 0 && filtered.length === 0 && (
          <Panel className="mx-auto max-w-xl text-center" bodyClassName="p-10">
            <div className="font-mono text-sm text-white/40">NO MATCH</div>
            <p className="mt-2 text-sm text-white/55">
              Tidak ada project yang cocok dengan pencarian / filter ini.
            </p>
            <Button variant="ghost" size="sm" className="mt-3"
                    onClick={() => { setQuery(''); setStatus('all') }}>
              hapus filter
            </Button>
          </Panel>
        )}

        {projects && projects.length > 0 && filtered.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((p) => {
              const meta = STATUS_META[p.status] || STATUS_META.queued
              const done = p.status === 'completed'
              const hue = [...(p.job_id || '')].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7)
              return (
                <div key={p.job_id} className={`card-chrome overflow-hidden rounded-xl transition-all ${done ? 'hover:border-cyan/50 hover:shadow-neon-cyan' : 'opacity-75'}`}>
                  {/* thumb */}
                  <button
                    type="button"
                    disabled={!done}
                    onClick={() => onOpen(p.job_id)}
                    title={done ? 'Buka project' : `Status: ${meta.label}`}
                    className={`relative block aspect-video w-full overflow-hidden ${done ? '' : 'cursor-default'}`}
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
                        <span className="flex h-11 w-11 items-center justify-center rounded-full border border-cyan/60 bg-navy/60 backdrop-blur transition-transform hover:scale-110 hover:shadow-neon-cyan">
                          <Icon name="play" className="ml-0.5 h-5 w-5 text-white-solid" aria-hidden />
                        </span>
                      </span>
                    )}
                    <span className={`absolute right-2 top-2 rounded border bg-black/60 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-white-solid ${
                      p.status === 'completed'
                        ? 'border-signal/50'
                        : p.status === 'failed'
                          ? 'border-danger/50'
                          : 'border-warn/50'
                    }`}>
                      {meta.label}
                    </span>
                  </button>

                  {/* meta */}
                  <div className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <StatusDot tone={meta.tone} pulse={p.status === 'processing'} />
                      <span title={p.source_title || p.source_label} className="line-clamp-2 text-sm font-semibold text-white/85">{p.source_title || p.source_label || 'Tanpa judul'}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[10px] uppercase tracking-widest text-white/45">
                      <span className="truncate">{timeAgo(p.created_at)}</span>
                      <span className="shrink-0 normal-case">{p.clip_count} klip · {(p.total_mb || 0).toFixed(1)} MB</span>
                    </div>
                    <div className="mt-2.5 flex items-center justify-between gap-2">
                      {p.cost?.total_idr ? (
                        <Mono className="text-xs text-warn">≈ Rp{p.cost.total_idr.toLocaleString('id-ID')}</Mono>
                      ) : <span />}
                      <div className="flex shrink-0 gap-2">
                        <Button variant="ghost" size="sm" disabled={!done} onClick={() => onOpen(p.job_id)}>
                          Buka
                        </Button>
                        <Button variant="danger" size="sm" onClick={() => remove(p.job_id)}>
                          Hapus
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
