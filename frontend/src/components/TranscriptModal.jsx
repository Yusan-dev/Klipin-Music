import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { editSegments, pollJob, rerenderProject, saveTranscript } from '../api.js'
import { Button, Micro, Spinner } from './ui.jsx'
import Icon from './Icon.jsx'

/**
 * Transcript editor — Opus Clip parity: edit spoken text, delete segments,
 * reorder them, then re-render clips with the corrected captions + trimmed video.
 *
 * Flow:
 *   1. User edits text / deletes / reorders segments
 *   2. "Apply" saves remaining segments → editSegments() → keep_segments on clip
 *   3. "Re-render" rebuilds all clips using keep_segments (no re-transcribe)
 */
export default function TranscriptModal({ project, onClose, onRerendered }) {
  const [segs, setSegs] = useState(() =>
    (project.transcript?.segments || []).map((s) => ({
      ...s,
      _deleted: false,  // soft-delete flag
    })),
  )
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [rendering, setRendering] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [clipIdx, setClipIdx] = useState(null) // if editing a single clip

  // Determine which clips this applies to: all if none selected, or one
  const clips = project.clips || []

  const totalWords = useMemo(
    () => segs
      .filter((s) => !s._deleted)
      .reduce((a, s) => a + (s.text || '').trim().split(/\s+/).filter(Boolean).length, 0),
    [segs],
  )

  const activeCount = useMemo(
    () => segs.filter((s) => !s._deleted).length,
    [segs],
  )

  const deletedCount = segs.length - activeCount

  const edit = useCallback((i, text) => {
    setSegs((arr) => arr.map((s, j) => (j === i ? { ...s, text } : s)))
    setDirty(true)
  }, [])

  const toggleDelete = useCallback((i) => {
    setSegs((arr) => arr.map((s, j) =>
      j === i ? { ...s, _deleted: !s._deleted } : s,
    ))
    setDirty(true)
  }, [])

  const moveUp = useCallback((i) => {
    if (i <= 0) return
    setSegs((arr) => {
      const next = [...arr]
      ;[next[i - 1], next[i]] = [next[i], next[i - 1]]
      return next
    })
    setDirty(true)
  }, [])

  const moveDown = useCallback((i) => {
    setSegs((arr) => {
      if (i >= arr.length - 1) return arr
      const next = [...arr]
      ;[next[i], next[i + 1]] = [next[i + 1], next[i]]
      return next
    })
    setDirty(true)
  }, [])

  // Save remaining segments → editSegments → keep_segments on clip(s)
  const save = useCallback(async () => {
    setSaving(true)
    setErr('')
    try {
      // First, save any text edits to the transcript
      const allSegs = segs.map(({ _deleted, ...s }) => s)
      await saveTranscript(project.job_id, allSegs)

      // Then, build keep_segments from active (non-deleted) segments
      const activeSegs = segs
        .filter((s) => !s._deleted)
        .map(({ _deleted, ...s }) => s)

      // Apply to all clips (or a single clip if clipIdx is set)
      const targets = clipIdx !== null ? [clipIdx] : clips.map((_, i) => i)
      for (const idx of targets) {
        // Filter segments to this clip's time window
        const clip = clips[idx]
        if (!clip) continue
        const c0 = clip.start || 0
        const c1 = clip.end || c0 + 60
        const clipSegs = activeSegs.filter((s) => {
          const segEnd = s.end || s.start || 0
          const segStart = s.start || 0
          return segEnd > c0 && segStart < c1
        })
        if (clipSegs.length < activeSegs.length || clipSegs.length > 0) {
          await editSegments(project.job_id, idx, clipSegs)
        }
      }

      setDirty(false)
      setMsg(`${activeCount} segmen aktif · ${deletedCount} dihapus. Klik "Re-render" untuk menerapkan.`)
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }, [segs, project.job_id, clipIdx, clips, activeCount, deletedCount])

  const render = useCallback(async () => {
    if (dirty) await save()
    setRendering(true)
    setErr('')
    setMsg('')
    try {
      const { job_id } = await rerenderProject(project.job_id)
      const data = await pollJob(job_id, {
        interval: 1500,
        onStatus: (d) => setMsg(`Re-render… ${(d.logs || []).slice(-1)[0] || ''}`),
      })
      onRerendered?.(data.result?.clips || [])
      setMsg('✅ Semua klip di-render ulang dengan caption baru.')
    } catch (e) {
      setErr(e.message)
    } finally {
      setRendering(false)
    }
  }, [dirty, save, project.job_id, onRerendered])

  useEffect(() => {
    if (!msg) return
    const t = setTimeout(() => setMsg(''), 5000)
    return () => clearTimeout(t)
  }, [msg])

  // Keyboard shortcut: Ctrl+Enter to save
  const handleKeyDown = useCallback((e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      if (!saving && dirty) save()
    }
    if (e.key === 'Escape' && !rendering) onClose()
  }, [saving, dirty, save, rendering, onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="transcript editor"
      onClick={(e) => e.target === e.currentTarget && !rendering && onClose()}
      onKeyDown={handleKeyDown}
    >
      <div className="card-chrome flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">
            SYS.TEXT EDITOR — {activeCount} ACTIVE · {deletedCount > 0 && (
              <span className="text-danger">{deletedCount} DELETED · </span>
            )} {totalWords} WORDS
          </Micro>
          <button onClick={onClose} disabled={rendering} className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40" aria-label="close">
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        {/* Toolbar */}
        <div className="flex items-center gap-2 border-b border-cyan/10 px-4 py-2">
          <span className="flex-1 text-[11px] text-white/40">
            Edit teks, hapus segmen (🗑️), atau ubah urutan (↑↓) — lalu re-render.
          </span>
          {deletedCount > 0 && (
            <button
              onClick={() => setSegs((arr) => arr.map((s) => ({ ...s, _deleted: false })))}
              className="rounded border border-cyan/20 px-2 py-0.5 font-mono text-[10px] text-cyan-soft/60 hover:text-cyan-soft"
            >
              undelete all
            </button>
          )}
        </div>

        {/* Segment list */}
        <div className="flex-1 space-y-2 overflow-y-auto p-4">
          {segs.length === 0 && (
            <p className="text-sm text-white/50">Tidak ada segmen transkrip untuk proyek ini.</p>
          )}
          {segs.map((s, i) => (
            <div
              key={i}
              className={`rounded-md border p-2.5 transition ${
                s._deleted
                  ? 'border-danger/20 bg-danger/5 opacity-50'
                  : 'border-cyan/15 bg-navy-2/40'
              }`}
            >
              {/* Timestamp + controls row */}
              <div className="mb-1 flex items-center gap-2">
                <span className={`font-mono text-[9px] ${
                  s._deleted ? 'text-danger/60 line-through' : 'text-cyan-soft/60'
                }`}>
                  [{i + 1}] {s.start?.toFixed(1)}s – {s.end?.toFixed(1)}s
                </span>
                <div className="flex-1" />
                {/* Reorder buttons */}
                <button
                  onClick={() => moveUp(i)}
                  disabled={i === 0 || s._deleted}
                  className="rounded px-1 py-0.5 font-mono text-[9px] text-white/30 hover:text-white/60 disabled:opacity-20"
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  onClick={() => moveDown(i)}
                  disabled={i === segs.length - 1 || s._deleted}
                  className="rounded px-1 py-0.5 font-mono text-[9px] text-white/30 hover:text-white/60 disabled:opacity-20"
                  title="Move down"
                >
                  ↓
                </button>
                {/* Delete toggle */}
                <button
                  onClick={() => toggleDelete(i)}
                  className={`rounded px-1.5 py-0.5 font-mono text-[9px] transition ${
                    s._deleted
                      ? 'bg-danger/20 text-danger hover:bg-danger/30'
                      : 'text-white/30 hover:text-danger'
                  }`}
                  title={s._deleted ? 'Restore segment' : 'Delete segment'}
                >
                  {s._deleted ? '♻️' : '🗑️'}
                </button>
              </div>

              {/* Text area */}
              <textarea
                value={s.text || ''}
                onChange={(e) => edit(i, e.target.value)}
                disabled={s._deleted}
                rows={Math.min(3, Math.max(1, Math.ceil((s.text || '').length / 90)))}
                className={`w-full resize-y rounded border px-2.5 py-1.5 text-sm leading-relaxed outline-none transition ${
                  s._deleted
                    ? 'border-danger/10 bg-navy-2/20 text-white/30 line-through'
                    : 'border-cyan/20 bg-navy-2 text-white/85 focus:border-cyan'
                }`}
              />
            </div>
          ))}
        </div>

        {/* Footer */}
        <footer className="flex items-center gap-2 border-t border-cyan/15 px-4 py-3">
          {msg && <span className="flex-1 truncate text-xs text-cyan-soft">{msg}</span>}
          {err && <span className="flex-1 truncate text-xs text-danger">{err}</span>}
          <Button variant="ghost" size="sm" onClick={save} disabled={!dirty || saving}>
            {saving ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="check" className="h-3.5 w-3.5" />} Apply
          </Button>
          <Button variant="cyan" size="sm" onClick={render} disabled={rendering}>
            {rendering ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="refresh" className="h-3.5 w-3.5" />} Re-render
          </Button>
        </footer>
      </div>
    </div>
  )
}
