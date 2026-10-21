import { useState } from 'react'
import { effectClip, fmtTime, pollJob, resolveAsset, slugify, dl, withCacheBust } from '../api.js'
import { Button, FullscreenVideo, Micro, Spinner } from './ui.jsx'
import Icon from './Icon.jsx'

const EFFECTS = [
  { id: '', label: <><Icon name="sparkles" className="h-3.5 w-3.5" /> Auto (AI pilih)</>, desc: 'AI memilih efek yang paling cocok untuk klip ini.' },
  { id: 'glitch', label: 'Glitch', desc: 'RGB split + noise burst di momen penekanan.' },
  { id: 'zoom', label: 'Punch-in zoom', desc: 'Zoom pulse di beat penting.' },
  { id: 'pulse', label: 'Brightness pulse', desc: 'Kilatan terang di momen emosi.' },
  { id: 'shake', label: 'Camera shake', desc: 'Getaran singkat di momen energik.' },
  { id: 'vignette', label: 'Vignette sinematik', desc: 'Sudut gelap + kontras, look film.' },
  { id: 'color_grade', label: 'Teal & orange', desc: 'Grade warna Hollywood.' },
  { id: 'b_w', label: 'Black & white', desc: 'Hitam-putih kontras tinggi.' },
]

export default function VideoFxModal({ clip, jobId, onClose }) {
  const [effect, setEffect] = useState('')
  const [state, setState] = useState('idle') // idle|running|error
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  const run = async () => {
    setState('running')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = await effectClip(jobId, clip.index, effect ? { effect } : {})
      const data = await pollJob(job_id, {
        interval: 1500,
        onStatus: (d) => setLogs((d.logs || []).slice(-2)),
      })
      setResult(data.result)
      setState('idle')
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="video effects"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.FX — AI VIDEO EFFECTS · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button onClick={onClose} disabled={state === 'running'} className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40" aria-label="close">
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="space-y-4 p-4">
          <div className="rounded-md border border-cyan/15 bg-navy-2/50 p-3">
            <div className="truncate text-sm font-semibold text-white/85">{clip.title}</div>
            <div className="mt-0.5 truncate text-xs text-white/50">
              {fmtTime(clip.start)} – {fmtTime(clip.end)} · {clip.size_mb} MB
            </div>
          </div>

          {!result ? (
            <>
              <p className="text-sm leading-relaxed text-white/55">
                Efek dipilih AI dari transcript, dirender otomatis di server (gratis).
              </p>
              <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="effect">
                {EFFECTS.map((e) => (
                  <label
                    key={e.id || 'auto'}
                    className={`cursor-pointer rounded-md border p-3 transition ${
                      effect === e.id
                        ? 'border-cyan bg-cyan/10 shadow-neon-cyan'
                        : 'border-cyan/20 bg-navy-2/40 hover:border-cyan/50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="fx"
                      value={e.id}
                      checked={effect === e.id}
                      onChange={() => setEffect(e.id)}
                      className="sr-only"
                    />
                    <div className="text-sm font-semibold text-white/85">{e.label}</div>
                    <div className="mt-0.5 text-[11px] leading-snug text-white/45">{e.desc}</div>
                  </label>
                ))}
              </div>
              {state === 'running' && (
                <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                  <Spinner className="h-3.5 w-3.5" />
                  {logs[logs.length - 1] || 'Memproses…'}
                </div>
              )}
              {state === 'error' && (
                <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
              )}
              <Button variant="cyan" onClick={run} disabled={state === 'running'} className="w-full">
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="sparkles" className="h-3.5 w-3.5" />} Terapkan efek
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <div className="rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                <Icon name="checkcircle" className="h-3.5 w-3.5" /> Efek {result.effect} (intensity {result.intensity}) diterapkan
              </div>
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={withCacheBust(`${resolveAsset(clip.video_url)}`)}
              />
              <div className="flex gap-2">
                <a
                  href={dl(withCacheBust(`${resolveAsset(clip.video_url)}`))}
                  download={`${slugify(clip.title)}_fx.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ganti efek</Button>
                <Button variant="ghost" onClick={onClose}>Selesai</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
