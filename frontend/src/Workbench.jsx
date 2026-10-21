import { useEffect, useRef, useState } from 'react'
import { batchProcess, getProject, getStatus, pollJob, processVideo, uploadVideo } from './api.js'
import { showToast } from './toast.js'
import InputStage from './components/InputStage.jsx'
import ProcessingStage from './components/ProcessingStage.jsx'
import ResultsStage from './components/ResultsStage.jsx'
import { Button, Spinner } from './components/ui.jsx'
import Icon from './components/Icon.jsx'
import Loader from './components/Loader.jsx'

export default function Workbench({ onOpenLibrary, onOpenProject, initialJobId, initialUrl, user, authChecked, onSignIn }) {
  const [stage, setStage] = useState(initialJobId ? 'loading' : 'input')
  const [jobId, setJobId] = useState(initialJobId || null)
  const [logs, setLogs] = useState([])
  const [error, setError] = useState('')
  const [project, setProject] = useState(null)
  const [sourceLabel, setSourceLabel] = useState('')
  const [jobFailed, setJobFailed] = useState(false)
  const [batch, setBatch] = useState(null) // {ids, statuses}
  const [sourceMeta, setSourceMeta] = useState(null)
  const startedAt = useRef(null)
  const pollAbort = useRef(null)
  // Options submitted with the job — re-attached to the project when polling
  // finishes so trim/cut/export keep the chosen layout, aspect & captions.
  const submittedOptions = useRef({})
  // Pending action the user tried while signed out — run it after login.
  const pendingAction = useRef(null)
  const prevUser = useRef(user)

  // Cleanup batch polling timer on unmount.
  useEffect(() => {
    return () => {
      pollAbort.current?.abort()
      batchUnmounted.current = true
      if (batchTimer.current) clearTimeout(batchTimer.current)
    }
  }, [])

  // Once the user signs in (guest -> authed), resume the blocked action.
  useEffect(() => {
    if (user && !prevUser.current && pendingAction.current) {
      const fn = pendingAction.current
      pendingAction.current = null
      fn()
    }
    prevUser.current = user
  }, [user])

  const requireAuth = (fn) => {
    if (user) return fn()
    pendingAction.current = fn
    onSignIn()
  }

  // Re-open an existing project (library → Open)
  useEffect(() => {
    if (!initialJobId) return
    ;(async () => {
      try {
        const p = await getProject(initialJobId)
        if (p.status === 'completed' && (p.clips || []).length > 0) {
          setProject(p)
          setStage('results')
        } else if (p.status === 'completed' || p.status === 'failed') {
          setProject(p)
          setStage('results')
        } else {
          // In-flight job — keep the stored options so the finished project
          // retains the layout/aspect/caption settings the job was queued with.
          submittedOptions.current = p.options || {}
          await startPolling(initialJobId)
        }
      } catch (e) {
        if (e?.status === 404) {
          showToast(
            'Proyek tidak ditemukan di akun ini. Membuka library akun aktif.',
            'warn',
          )
          onOpenLibrary()
          return
        }
        setError(e.message)
        setStage('input')
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialJobId])

  const startPolling = async (id) => {
    // Deep-link navigation and React effects can both request polling for the
    // same job. Cancel the previous loop so old projects do not keep polling
    // forever and exhaust the API status budget.
    pollAbort.current?.abort()
    const controller = new AbortController()
    pollAbort.current = controller
    setJobId(id)
    startedAt.current = Date.now()
    setJobFailed(false)
    setSourceMeta(null)
    setStage('processing')
    try {
      const data = await pollJob(id, {
        interval: 1500,
        signal: controller.signal,
        onStatus: (d) => {
          setLogs(d.logs || [])
          // Live source metadata (video preview card) streams from the
          // backend during processing — not only on completion.
          if (d.source_meta) setSourceMeta(d.source_meta)
        },
      })
      if (!data || controller.signal.aborted) return
      const result = data.result || {}
      const completionElapsedSec = Math.max(
        1,
        Math.round((Date.now() - (startedAt.current || Date.now())) / 1000),
      )
      setProject({
        ...result,
        job_id: id,
        source_label: sourceLabel,
        status: 'completed',
        created_at: data.created_at || Date.now() / 1000,
        duration: result.duration,
        options: submittedOptions.current || {},
        clips: result.clips || [],
        completion_elapsed_sec: completionElapsedSec,
      })
      setStage('results')
      // micro-celebration: the moment of completion deserves a beat
      const n = (result.clips || []).length
      if (n > 0) {
        const minutes = Math.max(1, Math.ceil(completionElapsedSec / 60))
        showToast(
          `Video selesai dalam ${minutes} menit. Terima kasih sudah menunggu.`,
          'ok',
          8000,
        )
      }
    } catch (e) {
      if (e?.name === 'AbortError' || controller.signal.aborted) return
      setLogs((l) => [...l, `❌ ${e.message}`])
      setError(e.message)
      setJobFailed(true)
      // Stay on the pipeline view so the user sees the halted state + logs;
      // ProcessingStage offers a way back to the input.
    }
  }

  const startUrl = async (url, options) => {
    requireAuth(async () => {
      setSourceLabel(url)
      setError('')
      submittedOptions.current = options || {}
      try {
        const data = await processVideo(url, options)
        // Deep-link the URL to this job BEFORE polling: if the phone screen
        // dims and the tab reloads, #/clip/{id} resumes polling on load
        // instead of dropping back to the input form.
        if (onOpenProject && data.job_id) onOpenProject(data.job_id)
        await startPolling(data.job_id)
      } catch (e) {
        setError(e.message)
        setStage('input')
      }
    })
  }

  const startBatch = async (urls, options) => {
    requireAuth(async () => {
      setError('')
      submittedOptions.current = options || {}
      try {
        const data = await batchProcess(urls, options)
        setBatch({ ids: data.job_ids, statuses: {} })
        batchToastShown.current = false
        pollBatch(data.job_ids)
      } catch (e) {
        setError(e.message)
        setStage('input')
      }
    })
  }

  const batchTimer = useRef(null)
  const batchUnmounted = useRef(false)
  const batchToastShown = useRef(false)

  const pollBatch = (ids) => {
    batchUnmounted.current = false
    const tick = async () => {
      if (batchUnmounted.current) return
      // Poll all pipelines together. The old serial loop made an 8-pipeline
      // batch wait for every request before the UI got any update, and then
      // discarded the logs that already contain the live stage information.
      const entries = await Promise.all(ids.map(async (id) => {
        try {
          const d = await getStatus(id)
          return [id, d]
        } catch {
          return [id, { status: 'error', logs: [], error: 'Status unavailable' }]
        }
      }))
      const details = Object.fromEntries(entries)
      if (batchUnmounted.current) return
      setBatch((b) => (b ? { ...b, details } : b))
      const statuses = Object.fromEntries(ids.map((id) => [id, details[id]?.status || 'queued']))
      const finished = Object.values(statuses).filter(
        (s) => ['completed', 'failed', 'error'].includes(s)).length
      if (finished === ids.length && !batchToastShown.current) {
        batchToastShown.current = true
        const okCount = Object.values(statuses).filter((s) => s === 'completed').length
        showToast(
          okCount === ids.length
            ? `🎉 Semua ${ids.length} video selesai — hasil ada di Library!`
            : `Batch selesai — ${okCount}/${ids.length} sukses`,
          okCount === ids.length ? 'ok' : 'warn',
          6000,
        )
      }
      if (finished < ids.length) batchTimer.current = setTimeout(tick, 2500)
    }
    tick()
  }

  const startFile = async (file, options) => {
    requireAuth(async () => {
      setSourceLabel(file.name)
      setError('')
      submittedOptions.current = options || {}
      try {
        const data = await uploadVideo(file, options)
        // Same deep-link as startUrl: survive a phone screen-dim reload.
        if (onOpenProject && data.job_id) onOpenProject(data.job_id)
        await startPolling(data.job_id)
      } catch (e) {
        setError(e.message)
        setStage('input')
      }
    })
  }

  const reset = () => {
    pollAbort.current?.abort()
    pollAbort.current = null
    setStage('input')
    setJobId(null)
    setProject(null)
    setLogs([])
    setError('')
    setJobFailed(false)
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      {error && stage === 'input' && (
        <div className="mx-auto mb-6 max-w-3xl rounded-md border border-danger/30 bg-danger/10 p-3 text-center text-sm text-danger">
          {error}
        </div>
      )}
      {stage === 'input' && (
        <>
          {authChecked && !user && !batch && (
            <div className="mx-auto mb-6 max-w-3xl rounded-md border border-warn/30 bg-warn/10 p-3 text-center text-xs text-warn">
              Kamu sedang menjelajah sebagai tamu — masuk dengan Google untuk membuat klip.
            </div>
          )}
          {batch ? (
            <BatchProgress
              batch={batch}
              onReset={() => setBatch(null)}
              onOpen={onOpenProject}
            />
          ) : (
            <InputStage
              onUrlSubmit={startUrl}
              onFile={startFile}
              onBatch={startBatch}
              initialUrl={initialUrl}
              user={user}
            />
          )}
        </>
      )}
      {stage === 'loading' && <Loader label="memuat project…" />}
      {stage === 'processing' && (
        <ProcessingStage
          logs={logs}
          status={jobFailed ? 'failed' : 'running'}
          jobId={jobId}
          label={sourceLabel}
          elapsed={startedAt.current}
          onBack={reset}
          liveSourceMeta={sourceMeta}
        />
      )}
      {stage === 'results' && project && (
        <>
          <CelebrateBurst key={project.job_id} />
          <ResultsStage project={project} onNewVideo={reset} onOpenLibrary={onOpenLibrary} />
        </>
      )}
    </div>
  )
}

/** One-shot celebratory glow when a project lands on the results view. */
function CelebrateBurst() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center">
      <div className="animate-celebrate h-56 w-56 rounded-full bg-signal/25 blur-3xl" />
    </div>
  )
}

function BatchProgress({ batch, onReset, onOpen }) {
  const ids = batch.ids || []
  const details = batch.details || {}
  const statuses = Object.fromEntries(ids.map((id) => [id, details[id]?.status || 'queued']))
  const done = ids.filter((id) => ['completed', 'failed', 'error'].includes(statuses[id])).length
  const pct = ids.length ? Math.round(ids.reduce((sum, id) => {
    const d = details[id] || {}
    if (['completed', 'failed', 'error'].includes(d.status)) return sum + 1
    return sum + stageProgress(d.logs || []) / 100
  }, 0) / ids.length * 100) : 0
  const STATUS_LABEL = {
    queued: 'antre',
    processing: 'proses',
    completed: 'selesai',
    failed: 'gagal',
    error: 'error',
  }
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
      <div className="card-chrome overflow-hidden rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 bg-navy-2/60 px-4 py-2.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-soft/80">
            SYS.BATCH — {done}/{ids.length} SELESAI
          </span>
          <button onClick={onReset} className="inline-flex items-center gap-1 font-mono text-xs text-white/40 hover:text-white"><Icon name="close" className="h-3 w-3" /> batal</button>
        </header>
        <div className="space-y-3 p-4">
          <div className="h-2 overflow-hidden rounded-full bg-navy-2">
            <div
              className="h-full bg-gradient-to-r from-cyan to-magenta transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="space-y-1.5">
            {ids.map((id) => {
              const st = statuses[id] || 'queued'
              const detail = details[id] || {}
              const stage = batchStage(detail.logs || [])
              const rowPct = ['completed', 'failed', 'error'].includes(st)
                ? 100
                : stageProgress(detail.logs || [])
              return (
                <div key={id} className="rounded-md border border-cyan/15 bg-navy-2/40 px-3 py-2">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-[10px] text-cyan-soft/60">{id.slice(0, 8)}</span>
                    <span className="flex-1 font-mono text-[11px] text-white/55">
                      {STATUS_LABEL[st] || st} · {stage}
                    </span>
                    <span className="font-mono text-[10px] text-cyan-soft/70">{rowPct}%</span>
                    {st === 'completed' && (
                      <Button variant="ghost" size="sm" onClick={() => onOpen(id)}>Buka →</Button>
                    )}
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-navy-2">
                    <div className="h-full bg-cyan/70 transition-all duration-500" style={{ width: `${rowPct}%` }} />
                  </div>
                  {detail.logs?.length > 0 && (
                    <div className="mt-1 truncate font-mono text-[10px] text-white/30" title={detail.logs.at(-1)}>
                      {detail.logs.at(-1)}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          {done === ids.length && (
            <p className="flex items-center gap-1.5 font-mono text-xs text-cyan-soft">
              <Icon name="checkcircle" className="h-3.5 w-3.5" /> Batch selesai — buka setiap hasil di Library.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

const BATCH_STAGES = [
  ['QUEUE', /queued/i],
  ['TRANSCRIPT', /caption|whisper|transcrib/i],
  ['ANALYZE', /transcript-first|gemini|viral analysis|viral moments/i],
  ['FETCH', /fetching source|downloading .*source/i],
  ['VISION', /dynamic layout|layout intelligence|auto layout/i],
  ['RENDER', /render/i],
  ['BURN', /subtitle|watermark|burn/i],
  ['DONE', /done:/i],
]

function batchStage(logs) {
  let current = 'INITIALIZING'
  for (const log of logs) {
    for (const [label, match] of BATCH_STAGES) if (match.test(log)) current = label
  }
  return current
}

function stageProgress(logs) {
  let current = -1
  for (const log of logs) {
    for (let i = BATCH_STAGES.length - 1; i > current; i--) {
      if (BATCH_STAGES[i][1].test(log)) current = i
    }
  }
  return Math.max(4, Math.round(((current + 1) / BATCH_STAGES.length) * 100))
}
