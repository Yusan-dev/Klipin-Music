import { useEffect, useRef, useState, useMemo } from 'react'
import { Button, Micro, Mono, Panel, Spinner, StatusDot } from './ui.jsx'
import Icon from './Icon.jsx'

const STAGES = [
  { id: 'queue', label: 'QUEUE', match: /queued/i },
  { id: 'transcript', label: 'TRANSCRIPT', match: /resolving validated transcript|caption|whisper|transcrib/i },
  { id: 'analyze', label: 'ANALYZE', match: /transcript-first|gemini|viral analysis|viral moments/i },
  { id: 'fetch', label: 'FETCH', match: /fetching source|downloading .*source/i },
  { id: 'vision', label: 'VISION', match: /dynamic layout|layout intelligence|auto layout/i },
  // The Modal RPC is blocking on the API host, so this is the last live
  // signal available while the remote worker is rendering. Treat it as an
  // active render stage instead of leaving the UI stuck on QUEUE.
  { id: 'render', label: 'RENDER · MODAL L4', match: /Modal L4 job started|hosted ASR \+ GPU visual|✂️|re-render|rendering selected|rendering clip/i },
  { id: 'burn', label: 'BURN', match: /subtitle|watermark|burn/i },
  { id: 'done', label: 'DONE', match: /done:/i },
]

function currentStage(logs) {
  let cur = -1
  for (let i = 0; i < logs.length; i++) {
    for (let s = STAGES.length - 1; s > cur; s--) {
      if (STAGES[s].match.test(logs[i])) cur = s
    }
  }
  return cur
}

function fmtDuration(seconds) {
  if (!seconds || seconds < 0) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return h ? `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}` : `${m}:${s.toString().padStart(2, '0')}`
}

function fmtCount(n) {
  if (!n) return '—'
  return new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

export default function ProcessingStage({ logs, status, jobId, label, elapsed, onBack, result, liveSourceMeta }) {
  const [now, setNow] = useState(Date.now())
  const [thumbnailIndex, setThumbnailIndex] = useState(0)
  const scrollRef = useRef(null)
  const cur = currentStage(logs)
  const remoteRenderActive = logs.some((log) => /Modal L4 job started|hosted ASR \+ GPU visual/i.test(log))
  const failed = status === 'failed'
  
  // Extract source metadata: prefer the live `liveSourceMeta` prop streamed
  // during polling, then fall back to the completed `result`.
  const sourceMeta = useMemo(() => {
    if (liveSourceMeta) return liveSourceMeta
    if (result?.source_meta) return result.source_meta
    if (result?.clips?.[0]?.source_meta) return result.clips[0].source_meta
    return null
  }, [liveSourceMeta, result])

  // YouTube's maxres poster is optional and commonly returns 404. Keep the
  // provider URL for the first attempt, then fall back to guaranteed-sized
  // posters instead of leaving a broken image in the live processing card.
  const thumbnailSources = useMemo(() => {
    // maxresdefault is optional on YouTube. Do not request a known-bad
    // provider URL first; otherwise every refresh logs a visible 404 before
    // the fallback can be used.
    const providerThumb = String(sourceMeta?.thumbnail || '')
    const sources = providerThumb && !/maxresdefault\.(?:webp|jpg|jpeg)$/i.test(providerThumb)
      ? [providerThumb]
      : []
    const match = String(sourceMeta?.url || '').match(
      /(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{6,})/i,
    )
    if (match) {
      sources.push(`https://i.ytimg.com/vi/${match[1]}/hqdefault.jpg`)
      sources.push(`https://i.ytimg.com/vi/${match[1]}/default.jpg`)
    }
    return [...new Set(sources)]
  }, [sourceMeta])

  useEffect(() => {
    setThumbnailIndex(0)
  }, [sourceMeta?.thumbnail, sourceMeta?.url])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [logs])

  const secs = Math.floor((now - (elapsed || now)) / 1000)

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
      <Micro className="block text-center">SYS.PIPELINE — RUNNING</Micro>
      <h2 className="mt-2 text-center text-xl font-bold tracking-tight">
        {failed ? 'Pipeline halted' : 'Processing…'}
      </h2>
      <p className="mt-1 text-center font-mono text-xs text-white/40">
        JOB {jobId} · {label || '—'}
      </p>

      {/* Video Preview Card - shows YouTube info while processing */}
      {!failed && sourceMeta && (
        <div className="mt-6" aria-live="polite">
          <div className="relative bg-navy-2/60 rounded-2xl border border-cyan/20 overflow-hidden">
            {/* Thumbnail */}
            <div className="relative aspect-video w-full">
              {thumbnailSources[thumbnailIndex] ? (
                <img
                  src={thumbnailSources[thumbnailIndex]}
                  alt={sourceMeta.title}
                  className="absolute inset-0 w-full h-full object-cover opacity-80"
                  loading="lazy"
                  onError={() => setThumbnailIndex((index) => (
                    index + 1 < thumbnailSources.length ? index + 1 : index
                  ))}
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center bg-navy-3">
                  <Icon name="film" className="h-16 w-16 text-white/20" />
                </div>
              )}
              {/* Live indicator overlay */}
              <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 backdrop-blur px-2.5 py-1 rounded-full border border-white/10">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inset-0 animate-ping rounded-full bg-cyan/60" />
                  <span className="relative rounded-full bg-cyan h-2 w-2" />
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-cyan">LIVE ANALYSIS</span>
              </div>
              {/* Duration badge */}
              <div className="absolute bottom-3 right-3 bg-black/60 backdrop-blur px-2.5 py-1 rounded-full border border-white/10 font-mono text-xs text-white/80">
                {fmtDuration(sourceMeta.duration)}
              </div>
              {/* Platform badge */}
              <div className="absolute top-3 right-3 bg-black/60 backdrop-blur px-2.5 py-1 rounded-full border border-white/10 font-mono text-[9px] uppercase tracking-[0.1em] text-white/60">
                {sourceMeta.platform?.toUpperCase() || 'URL'}
              </div>
            </div>
            
            {/* Video Info Overlay */}
            <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent p-4 pointer-events-none">
              <div className="absolute bottom-0 left-0 right-0">
                <h3 className="font-display text-lg font-bold text-white truncate pr-8">
                  {sourceMeta.title}
                </h3>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-white/60 text-sm">
                  <span className="flex items-center gap-1">
                    <Icon name="user" className="h-3.5 w-3.5" />
                    {sourceMeta.channel}
                  </span>
                  {sourceMeta.view_count && (
                    <span className="flex items-center gap-1">
                      <Icon name="eye" className="h-3.5 w-3.5" />
                      {fmtCount(sourceMeta.view_count)}
                    </span>
                  )}
                  <span className="flex items-center gap-1">
                    <Icon name="clock" className="h-3.5 w-3.5" />
                    {fmtDuration(sourceMeta.duration)}
                  </span>
                </div>
                <div className="mt-2 h-1.5 bg-white/10 rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-gradient-to-r from-cyan to-cyan/40 animate-pulse"
                    style={{ width: `${Math.min(100, Math.max(5, (cur + 1) / STAGES.length * 100))}%` }}
                    aria-hidden
                  />
                </div>
              </div>
            </div>
          </div>
          
          {/* Analysis status text */}
          <Mono className="mt-3 text-xs uppercase tracking-[0.22em] text-cyan/70 text-center">
            <span className="animate-pulse">{remoteRenderActive ? 'Modal L4 sedang merender' : 'analyzing video content'}</span>
            {' • '}{STAGES[cur]?.label || 'INITIALIZING'}
          </Mono>
          
          {/* Equalizer bars - represent audio analysis */}
          <div className="mt-3 flex justify-center items-end gap-1.5 h-8">
            {[0.9, 0.4, 0.7, 1, 0.5, 0.8, 0.3, 0.6, 0.95].map((h, i) => (
              <span
                key={i}
                className="w-1.5 animate-pulse rounded-sm bg-cyan/60"
                style={{ height: `${h * 100}%`, animationDelay: `${i * 0.12}s`, animationDuration: '0.9s' }}
                aria-hidden
              />
            ))}
          </div>
        </div>
      )}

      {failed && (
        <div className="mt-6 flex flex-col items-center gap-3">
          <div className="flex h-16 w-16 items-center justify-center rounded-full border border-danger/40 text-danger">
            <Icon name="nosymbol" className="h-7 w-7" />
          </div>
          {onBack && <Button variant="ghost" size="sm" onClick={onBack}>← Kembali ke input</Button>}
        </div>
      )}

      {/* stage strip */}
            <Panel label="Pipeline telemetry" dot={failed ? 'err' : 'run'} className="mt-6">
              <ol className="grid grid-cols-4 gap-x-3 gap-y-3 sm:grid-cols-8">
                {STAGES.map((s, i) => {
                  const state = failed && i < cur ? 'err' : i < cur ? 'ok' : i === cur ? 'run' : 'idle'
                  const isCurrent = i === cur && !failed
                  return (
                    <li key={s.id} className="flex flex-col items-start gap-1.5">
                      <StatusDot tone={state} pulse={isCurrent && status !== 'failed'} />
                      <span
                        className={`font-mono text-[9px] uppercase tracking-[0.14em] ${
                          state === 'idle' ? 'text-white/25' : 'text-white/60'
                        }`}
                      >
                        {s.label}
                      </span>
                      <span className="font-mono text-[9px] text-white/20">
                        {String(i + 1).padStart(2, '0')}
                      </span>
                    </li>
                  )
                })}
              </ol>
              <div className="mt-4 flex items-center gap-2 border-t border-border-soft pt-3 font-mono text-[10px] uppercase tracking-[0.18em] text-white/35">
                {!failed && <Spinner className="h-3 w-3" />}
                elapsed {String(Math.floor(secs / 60)).padStart(2, '0')}:{String(secs % 60).padStart(2, '0')}
                <span className="ml-auto text-white/25">8 STAGES · GPU ON DEMAND</span>
              </div>
            </Panel>

      {/* log console - only show on hover/focus or keep minimal */}
      <Panel label="Log console" dot="idle" className="mt-4" bodyClassName="p-0">
        <div
          ref={scrollRef}
          aria-live="polite"
          className="max-h-64 overflow-y-auto bg-navy-2/60 p-4 font-mono text-xs leading-relaxed text-white/55"
          onMouseEnter={() => {}} // Keep scrollable
        >
          {logs.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap">
              <span className="mr-2 select-none text-white/20">{String(i + 1).padStart(2, '0')}</span>
              {l}
            </div>
          ))}
          {!failed && (
            <div className="mt-1">
              <span className="inline-block h-3.5 w-2 animate-blink bg-cyan/80 align-middle" aria-hidden />
            </div>
          )}
          {failed && <div className="mt-1 text-danger">process terminated with errors</div>}
        </div>
      </Panel>
    </div>
  )
}
