import { useState } from 'react'
import { fmtTime, repurposeProject } from '../api.js'
import { Button, Micro, Select, Spinner } from './ui.jsx'
import Icon from './Icon.jsx'

const FORMATS = [
  { id: 'x_thread', label: <><Icon name="share" className="h-3.5 w-3.5" /> X Thread</>, desc: '6–10 postingan bernomor dengan hook pembuka' },
  { id: 'linkedin', label: <><Icon name="briefcase" className="h-3.5 w-3.5" /> LinkedIn</>, desc: 'Post profesional 150–250 kata + CTA' },
  { id: 'blog', label: <><Icon name="text" className="h-3.5 w-3.5" /> Blog</>, desc: 'Artikel dengan heading, 250–450 kata' },
  { id: 'ig_long', label: <><Icon name="photo" className="h-3.5 w-3.5" /> IG Caption</>, desc: 'Caption panjang + emoji + 5–8 hashtag' },
]

export default function RepurposeModal({ project, initialClipIndex = null, onClose }) {
  const clips = project.clips || []
  // Default: per-clip (first clip) when clips exist, else whole transcript.
  const [clipIdx, setClipIdx] = useState(
    initialClipIndex ?? (clips.length ? clips[0].index : null),
  )
  const [format, setFormat] = useState('x_thread')
  const [state, setState] = useState('idle') // idle|running|error
  const [err, setErr] = useState('')
  const [content, setContent] = useState(null)
  const [copied, setCopied] = useState(false)

  const scopeLabel = clipIdx === null ? 'SEMUA KLIP' : `CLIP ${String(clipIdx + 1).padStart(2, '0')}`

  const run = async () => {
    setState('running')
    setErr('')
    setContent(null)
    try {
      const d = await repurposeProject(project.job_id, format, clipIdx)
      setContent(d.content)
      setState('idle')
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  const text = () => {
    if (!content) return ''
    if (content.thread) return content.thread.map((p, i) => `${i + 1}/${content.thread.length} ${p}`).join('\n\n')
    if (content.post) return content.post
    if (content.caption) return content.caption
    if (content.sections) {
      return `# ${content.title || ''}\n\n` + content.sections
        .map((s) => `## ${s.heading}\n${s.body}`).join('\n\n')
    }
    return JSON.stringify(content, null, 2)
  }

  const copy = () => {
    navigator.clipboard?.writeText(text())
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="repurpose"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.REPURPOSE — {scopeLabel} → KONTEN</Micro>
          <button onClick={onClose} disabled={state === 'running'} className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40" aria-label="close">
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="space-y-4 p-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">
                Sumber konten
              </div>
              <Select
                value={clipIdx ?? 'all'}
                onChange={(e) => setClipIdx(e.target.value === 'all' ? null : Number(e.target.value))}
                className="w-full"
                aria-label="repurpose source clip"
              >
                {clips.map((c) => (
                  <option key={c.index} value={c.index}>
                    Clip {String(c.index + 1).padStart(2, '0')} · {fmtTime(c.start)}–{fmtTime(c.end)} · {(c.title || '').slice(0, 40)}
                  </option>
                ))}
                {clips.length > 1 && <option value="all">Semua klip (seluruh video)</option>}
              </Select>
              <p className="mt-1 font-mono text-[9px] text-white/30">
                Per-klip = dari window transkrip klip itu + judul/hook-nya. Paling akurat untuk konten spesifik.
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setContent(null)}>Ganti format</Button>
          </div>

          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="format">
            {FORMATS.map((f) => (
              <label
                key={f.id}
                className={`cursor-pointer rounded-md border p-3 transition ${
                  format === f.id ? 'border-cyan bg-cyan/10 shadow-neon-cyan' : 'border-cyan/20 bg-navy-2/40 hover:border-cyan/50'
                }`}
              >
                <input type="radio" name="fmt" value={f.id} checked={format === f.id} onChange={() => setFormat(f.id)} className="sr-only" />
                <div className="text-sm font-semibold text-white/85">{f.label}</div>
                <div className="mt-0.5 text-[11px] text-white/45">{f.desc}</div>
              </label>
            ))}
          </div>

          {state === 'running' && (
            <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
              <Spinner className="h-3.5 w-3.5" /> AI menulis konten dari {scopeLabel}…
            </div>
          )}
          {state === 'error' && (
            <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
          )}

          {!content ? (
            <Button variant="cyan" onClick={run} disabled={state === 'running'} className="w-full">
              {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="sparkles" className="h-3.5 w-3.5" />} Generate konten
            </Button>
          ) : (
            <div className="space-y-3">
              <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/75">
                {text()}
              </pre>
              <div className="flex gap-2">
                <Button variant="cyan" onClick={copy} className="flex-1">
                  {copied ? <Icon name="check" className="h-3.5 w-3.5" /> : <Icon name="copy" className="h-3.5 w-3.5" />} {copied ? 'Tersalin' : 'Salin'}
                </Button>
                <Button variant="ghost" onClick={onClose}>Selesai</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
