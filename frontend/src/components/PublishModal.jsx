import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fmtCount, getClipAnalytics, getClipPublishes, getPublishAuthUrl,
  getPublishConfig, getPublishIdeas, getPublishStatus, pollJob, publishClip,
  disconnectPlatform,
} from '../api.js'
import { Button, Micro, Mono, Panel, Select, Spinner, StatusDot } from './ui.jsx'
import Icon from './Icon.jsx'

const PLATFORMS = [
  { id: 'youtube', label: 'YouTube Shorts', dot: '#FF0033' },
  { id: 'tiktok', label: 'TikTok', dot: '#69C9D0' },
  { id: 'instagram', label: 'Instagram Reels', dot: '#E1306C' },
]

const PRIVACY = {
  youtube: [
    { value: 'public', label: 'Public' },
    { value: 'unlisted', label: 'Unlisted' },
    { value: 'private', label: 'Private' },
  ],
  tiktok: [
    { value: 'PUBLIC_TO_EVERYONE', label: 'Public' },
    { value: 'FOLLOWER_OF_CREATOR', label: 'Followers' },
    { value: 'MUTUAL_FOLLOW_FRIENDS', label: 'Mutual follows' },
    { value: 'SELF_ONLY', label: 'Only me' },
  ],
  instagram: [
    { value: 'public', label: 'Public' },
  ],
}

const CONNECT_TEXTS = {
  youtube: 'Open Google authorization',
  tiktok: 'Open TikTok authorization',
  instagram: 'Open Meta authorization',
}

const WHEN_OPTIONS = [
  { id: 'now', label: 'POST NOW' },
  { id: 'schedule', label: 'SCHEDULE' },
]

/** datetime-local value (local time) for `ts` epoch seconds. */
function toLocalInput(ts) {
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Human-friendly "15 Agu 2026, 14:30" from epoch seconds. */
function fmtScheduled(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('id-ID', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

const METRIC_ICONS = {
  views: 'views',
  likes: 'thumb',
  comments: 'comments',
  shares: 'repurpose',
  reach: 'radio',
}

export default function PublishModal({ clip, jobId, onClose }) {
  const [config, setConfig] = useState(null)
  const [statuses, setStatuses] = useState({})
  const [history, setHistory] = useState([])
  const [connectState, setConnectState] = useState('') // '' | platform being connected
  const [form, setForm] = useState({
    title: clip.title || clip.hook || '',
    privacy: 'public',
    when: 'now', // 'now' | 'schedule'
    scheduledAt: toLocalInput(Date.now() / 1000 + 60 * 60), // default: +1 hour
  })
  const [publishing, setPublishing] = useState('') // '' | platform being published to
  const [pubErr, setPubErr] = useState('')
  const [pubResults, setPubResults] = useState({})
  const [analytics, setAnalytics] = useState([])
  const [analyticsBusy, setAnalyticsBusy] = useState(false)
  const [analyticsErr, setAnalyticsErr] = useState('')
  const [ideas, setIdeas] = useState({}) // {platform: {title, caption, hashtags}}
  const [ideasBusy, setIdeasBusy] = useState('') // '' | platform being generated

  const refresh = useCallback(async () => {
    const [cfg, hist] = await Promise.all([
      getPublishConfig(),
      getClipPublishes(jobId, clip.index),
    ])
    setConfig(cfg)
    setHistory(hist.publishes || [])
    const st = {}
    for (const p of PLATFORMS) {
      if (cfg[p.id]?.configured) {
        st[p.id] = await getPublishStatus(p.id).catch(() => null)
      }
    }
    setStatuses(st)
  }, [jobId, clip.index])

  useEffect(() => { refresh() }, [refresh])

  const loadAnalytics = useCallback(async () => {
    setAnalyticsBusy(true)
    setAnalyticsErr('')
    try {
      const data = await getClipAnalytics(jobId, clip.index)
      setAnalytics(data.analytics || [])
    } catch (e) {
      setAnalyticsErr(e.message)
    } finally {
      setAnalyticsBusy(false)
    }
  }, [jobId, clip.index])

  useEffect(() => { loadAnalytics() }, [loadAnalytics])

  // While any scheduled/processing publish exists, poll history so the row
  // flips to "published" on its own once the scheduler fires it.
  useEffect(() => {
    const pending = history.some((h) => h.status === 'scheduled' || h.status === 'processing')
    if (!pending) return
    const t = setInterval(refresh, 10000)
    return () => clearInterval(t)
  }, [history, refresh])

  const connectTimer = useRef(null)

  // Cleanup OAuth polling timer on unmount.
  useEffect(() => {
    return () => { if (connectTimer.current) clearInterval(connectTimer.current) }
  }, [])

  const connect = async (platform) => {
    setConnectState(platform)
    setPubErr('')
    try {
      const { url } = await getPublishAuthUrl(platform)
      const win = window.open(url, 'klipin_oauth', 'width=600,height=720')
      // Poll until the popup finishes (it closes itself on success) or user closes it.
      const deadline = Date.now() + 180_000
      if (connectTimer.current) clearInterval(connectTimer.current)
      connectTimer.current = setInterval(async () => {
        if (Date.now() > deadline || (win && win.closed)) {
          clearInterval(connectTimer.current)
          connectTimer.current = null
          setConnectState('')
          await refresh()
          return
        }
        try {
          const st = await getPublishStatus(platform)
          if (st.connected) {
            clearInterval(connectTimer.current)
            connectTimer.current = null
            setConnectState('')
            setStatuses((s) => ({ ...s, [platform]: st }))
          }
        } catch { /* transient */ }
      }, 1200)
    } catch (e) {
      setConnectState('')
      setPubErr(e.message)
    }
  }

  const disconnect = async (platform) => {
    await disconnectPlatform(platform)
    await refresh()
  }

  const doPublish = async (platform) => {
    setPublishing(platform)
    setPubErr('')
    try {
      let scheduledAt = null
      if (form.when === 'schedule') {
        const epoch = new Date(form.scheduledAt).getTime() / 1000
        if (!form.scheduledAt || !Number.isFinite(epoch) || epoch <= Date.now() / 1000) {
          setPubErr('Pilih tanggal & jam di masa depan untuk jadwal.')
          setPublishing('')
          return
        }
        scheduledAt = Math.floor(epoch)
      }
      const payload = {
        platform,
        title: form.title,
        description: clip.hook || '',
        privacy: PRIVACY[platform].some((p) => p.value === form.privacy)
          ? form.privacy
          : PRIVACY[platform][0].value,
      }
      if (scheduledAt) payload.scheduled_at = scheduledAt

      const resp = await publishClip(jobId, clip.index, payload)
      if (resp.scheduled) {
        // Stored — the scheduler will post it later.
        refresh()
      } else {
        const data = await pollJob(resp.job_id, { interval: 1500 })
        setPubResults((r) => ({ ...r, [platform]: data.result }))
        refresh()
        loadAnalytics()
      }
    } catch (e) {
      setPubErr(e.message)
    } finally {
      setPublishing('')
    }
  }

  const platformResult = (p) => pubResults[p] || history.find((h) => h.platform === p && h.status === 'published')

  // Each platform has its own privacy vocabulary; a value chosen for one
  // platform must never leak into another platform's dropdown.
  const privacyFor = (platform) =>
    PRIVACY[platform].some((o) => o.value === form.privacy) ? form.privacy : PRIVACY[platform][0].value

  const genIdeas = async (platform) => {
    setIdeasBusy(platform)
    setPubErr('')
    try {
      const { ideas: res } = await getPublishIdeas(jobId, clip.index, platform)
      setIdeas((m) => ({ ...m, [platform]: res }))
    } catch (e) {
      setPubErr(e.message)
    } finally {
      setIdeasBusy('')
    }
  }

  const applyIdea = (platform) => {
    const idea = ideas[platform]
    if (!idea) return
    const tags = (idea.hashtags || []).join(' ')
    const full = [idea.caption, tags].filter(Boolean).join('\n\n')
    setForm((f) => ({ ...f, title: full }))
  }

  const appendHashtag = (platform, tag) => {
    setForm((f) => (f.title.includes(tag) ? f : { ...f, title: `${f.title} ${tag}`.trim() }))
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`publish clip ${clip.index + 1}`}
      onClick={(e) => e.target === e.currentTarget && !publishing && onClose()}
    >
      <div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-lg border border-border bg-panel">
        <header className="flex items-center justify-between border-b border-border-soft px-4 py-2.5">
          <Micro className="text-white/60">SYS.PUBLISH — CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={loadAnalytics}
              disabled={analyticsBusy}
              className="font-mono text-[10px] uppercase tracking-wider text-white/40 transition hover:text-cyan-soft disabled:opacity-40"
              aria-label="refresh analytics"
            >
              {analyticsBusy ? <Spinner className="h-3 w-3" /> : <Icon name="refresh" className="h-3 w-3" />} analytics
            </button>
            <button
              onClick={onClose}
              disabled={!!publishing}
              className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
              aria-label="close"
            >
              ESC <Icon name="close" className="h-3 w-3" />
            </button>
          </div>
        </header>

        <div className="space-y-4 p-4">
          {/* caption */}
          <div>
            <Micro>Caption</Micro>
            <textarea
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              rows={2}
              className="mt-1.5 w-full resize-none rounded-md border border-border bg-navy-2 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/25 focus:border-cyan"
              placeholder="Caption / title for the post"
            />
            <div className="mt-1 flex gap-3 font-mono text-[10px] text-white/25">
              <span>{form.title.length} chars</span>
              <span>per-platform limits apply</span>
            </div>
          </div>

          {/* when: post now vs schedule */}
          <div>
            <Micro>When</Micro>
            <div className="mt-1.5 flex items-center gap-2">
              <div
                className="grid grid-cols-2 gap-1 rounded-md border border-border-soft bg-navy-2/60 p-0.5"
                role="radiogroup"
                aria-label="publish timing"
              >
                {WHEN_OPTIONS.map((w) => (
                  <button
                    key={w.id}
                    type="button"
                    role="radio"
                    aria-checked={form.when === w.id}
                    onClick={() => setForm((f) => ({ ...f, when: w.id }))}
                    className={`rounded px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.18em] transition-all ${
                      form.when === w.id
                        ? 'bg-cyan/15 text-cyan-soft shadow-neon-cyan'
                        : 'text-white/40 hover:text-white/70'
                    }`}
                  >
                    {w.label}
                  </button>
                ))}
              </div>
              {form.when === 'schedule' && (
                <input
                  type="datetime-local"
                  value={form.scheduledAt}
                  onChange={(e) => setForm((f) => ({ ...f, scheduledAt: e.target.value }))}
                  className="flex-1 rounded-md border border-border bg-navy-2 px-3 py-1.5 font-mono text-xs text-white outline-none transition focus:border-cyan"
                  aria-label="schedule date and time"
                />
              )}
            </div>
            {form.when === 'schedule' && (
              <p className="mt-1.5 font-mono text-[10px] text-white/30">
                <Icon name="clock" className="h-3 w-3 inline" /> akan di-post otomatis oleh scheduler server — jadwal tersimpan meski server restart.
              </p>
            )}
          </div>

          {/* platform cards */}
          <div className="space-y-2.5">
            {PLATFORMS.map((p) => {
              const cfg = config?.[p.id]
              const st = statuses[p.id]
              const busy = connectState === p.id || publishing === p.id
              const result = platformResult(p.id)
              const historyRows = history.filter((h) => h.platform === p.id)
              const latest = historyRows[0]
              return (
                <div key={p.id} className="rounded-md border border-border-soft bg-navy-2/40 p-3">
                  <div className="flex items-center gap-2.5">
                    <span className="h-2 w-2 rounded-full" style={{ background: p.dot }} />
                    <span className="text-sm font-semibold text-white/85">{p.label}</span>
                    {st?.connected ? (
                      <span className="ml-auto flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-signal">
                        <StatusDot tone="ok" /> {st.account_name || 'connected'}
                      </span>
                    ) : (
                      <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-white/30">
                        {cfg?.configured ? 'not connected' : 'not configured'}
                      </span>
                    )}
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {!cfg?.configured ? (
                      <Mono className="text-white/30">
                        missing env credentials — see backend/.env
                      </Mono>
                    ) : !cfg?.base_url_ok ? (
                      <Mono className="text-warn">
                        PUBLIC_BASE_URL not set — needed for OAuth
                      </Mono>
                    ) : !st?.connected ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => connect(p.id)}
                        disabled={!!connectState}
                      >
                        {connectState === p.id ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="link" className="h-3.5 w-3.5" />} Connect
                      </Button>
                    ) : (
                      <>
                        <label className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-white/35">
                          privacy
                          <Select
                            value={privacyFor(p.id)}
                            onChange={(e) => setForm((f) => ({ ...f, privacy: e.target.value }))}
                            className="w-32 !py-1 text-[11px]"
                            aria-label={`${p.label} privacy`}
                          >
                            {PRIVACY[p.id].map((o) => (
                              <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                          </Select>
                        </label>
                        <Button size="sm" onClick={() => doPublish(p.id)} disabled={busy || !form.title.trim()}>
                          {publishing === p.id ? <Spinner className="h-3.5 w-3.5" /> : form.when === 'schedule' ? <Icon name="clock" className="h-3.5 w-3.5" /> : <Icon name="publish" className="h-3.5 w-3.5" />}
                          {' '}{form.when === 'schedule' ? 'Schedule' : 'Publish'}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => genIdeas(p.id)}
                          disabled={ideasBusy !== ''}
                          title="Generate caption + hashtags AI untuk platform ini"
                        >
                          {ideasBusy === p.id ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="sparkles" className="h-3.5 w-3.5" />} AI
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => disconnect(p.id)}
                          className="ml-auto !px-2 !py-1 text-[10px] text-white/30"
                        >
                          disconnect
                        </Button>
                      </>
                    )}
                  </div>

                  {result && (
                    <a
                      href={result.post_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2.5 inline-flex items-center gap-1.5 rounded border border-signal/40 bg-signal/10 px-2.5 py-1.5 text-xs font-semibold text-signal transition hover:bg-signal/20"
                    >
                      <Icon name="check" className="h-3.5 w-3.5" /> Published · open post <Icon name="external" className="h-3 w-3" />
                    </a>
                  )}
                  {ideas[p.id] && (
                    <div className="mt-2.5 rounded-md border border-magenta/25 bg-magenta/5 p-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <Micro className="text-magenta-soft"><Icon name="sparkles" className="h-3.5 w-3.5" /> AI caption</Micro>
                        <button
                          type="button"
                          onClick={() => applyIdea(p.id)}
                          className="font-mono text-[10px] uppercase tracking-wider text-cyan-soft/80 transition hover:text-cyan-soft"
                        >
                          pakai semua →
                        </button>
                      </div>
                      <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-white/70">
                        {ideas[p.id].caption || '(caption kosong)'}
                      </p>
                      {ideas[p.id].hashtags?.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {ideas[p.id].hashtags.map((h) => (
                            <button
                              key={h}
                              type="button"
                              onClick={() => appendHashtag(p.id, h)}
                              title="Klik untuk tambah ke caption"
                              className="rounded-full border border-magenta/30 bg-magenta/10 px-2 py-0.5 font-mono text-[10px] text-magenta-soft transition hover:bg-magenta/20"
                            >
                              {h}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {latest?.status === 'processing' && (
                    <div className="mt-2.5 inline-flex items-center gap-1.5 rounded border border-warn/40 bg-warn/10 px-2.5 py-1.5 font-mono text-[10px] text-warn">
                      <Spinner className="h-3 w-3" /> publishing… ({p.label})
                    </div>
                  )}
                  {historyRows.map((h) => (
                    <div
                      key={h.id}
                      className="mt-1.5 font-mono text-[10px] text-white/30"
                    >
                      {h.status === 'published' && <><Icon name="check" className="h-3 w-3 inline" /> {h.platform} — {h.post_url || h.post_id}</>}
                      {h.status === 'failed' && <><Icon name="close" className="h-3 w-3 inline" /> {h.platform} failed — {h.error}</>}
                      {h.status === 'scheduled' && (
                        <><Icon name="clock" className="h-3 w-3 inline" /> {h.platform} — scheduled {fmtScheduled(h.scheduled_at)}</>
                      )}
                      {h.status === 'processing' && (
                        <><Icon name="clock" className="h-3 w-3 inline" /> {h.platform} — publishing…</>
                      )}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>

          {/* analytics: live metrics per platform */}
          {analytics.length > 0 && (
            <div>
              <Micro><Icon name="analytics" className="h-3 w-3" /> Analytics — live</Micro>
              <div className="mt-1.5 space-y-1.5">
                {analytics.map((a) => {
                  const pl = PLATFORMS.find((p) => p.id === a.platform)
                  return (
                    <div key={`${a.platform}-${a.post_id}`} className="rounded-md border border-border-soft bg-navy-2/40 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="h-2 w-2 rounded-full" style={{ background: pl?.dot || '#888' }} />
                        <span className="text-xs font-semibold text-white/85">{pl?.label || a.platform}</span>
                        {a.post_url && (
                          <a
                            href={a.post_url}
                            target="_blank"
                            rel="noreferrer"
                            className="ml-auto font-mono text-[10px] text-cyan-soft/70 transition hover:text-cyan-soft"
                          >
                            open post ↗
                          </a>
                        )}
                      </div>
                      {a.metrics ? (
                        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                          {Object.entries(a.metrics).map(([k, v]) => (
                            <span key={k} className="flex items-center gap-1 font-mono text-[11px] text-white/60">
                              <span className="text-white/30">{METRIC_ICONS[k] ? <Icon name={METRIC_ICONS[k]} className="h-3 w-3" /> : '•'}</span>
                              <span className="text-white/85">{fmtCount(v)}</span>
                              <span className="uppercase tracking-wider text-white/30">{k}</span>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="mt-1 font-mono text-[10px] text-warn">
                          <Icon name="warn" className="h-3 w-3 inline" /> {a.error || 'analytics unavailable'}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
              {analyticsErr && (
                <p className="mt-1.5 font-mono text-[10px] text-white/30">
                  refresh failed — {analyticsErr}
                </p>
              )}
            </div>
          )}

          {pubErr && (
            <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
              {pubErr}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
