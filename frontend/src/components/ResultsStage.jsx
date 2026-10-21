import { useEffect, useMemo, useRef, useState } from 'react'
import {
  brollClip, cleanClip, autofixCaption, customCut, dubClip, editCaption,
  enhanceClip, exportProject, faceswapClip, fmtCount, fmtTime,
  generateThumbnail, getClipAnalytics, getClipCaptions, getDubVoices,
  getWaveform, overlayMedia, overlayText, pollJob, repickProject,
  getProjectSourceUrl,
  resolveAsset, resolveMediaAsset, retrimClip, putClipLayoutPlan, sfxClip,
  slugify, timeAgo, updateClipMeta, dl, generateHookVariants, selectHookVariant,
  setClipApproval,
  voiceoverClip, withCacheBust, previewClipLayout,
} from '../api.js'
import { CRITERIA_PRESETS, LAYOUTS, ASPECTS, LANGUAGES } from './InputStage.jsx'
import { loadFavorites, saveFavorite, syncFavorites } from '../criteriaStore.js'
import FavoriteChips from './FavoriteChips.jsx'
import CaptionSettings, { normalizeOptions } from './CaptionSettings.jsx'
import PublishModal from './PublishModal.jsx'
import VideoFxModal from './VideoFxModal.jsx'
import RepurposeModal from './RepurposeModal.jsx'
import TranscriptModal from './TranscriptModal.jsx'
import StudioDrawer from './StudioDrawer.jsx'
import ClipStudioSheet from './ClipStudioSheet.jsx'
import Icon from './Icon.jsx'
import Waveform from './Waveform.jsx'
import {
  Button, Field, FullscreenVideo, Micro, Mono, Panel, Select, Spinner, StatusDot, TextInput, VUMeter,
} from './ui.jsx'

const RESOLUTIONS = [
  { value: 'source', label: 'Source' },
  { value: '1080', label: '1080×1920' },
  { value: '720', label: '720×1280' },
]

const EXPORT_ASPECTS = [
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
]

function scoreTone(s) {
  if (s >= 85) return 'text-signal'
  if (s >= 65) return 'text-warn'
  return 'text-white/50'
}

function scoreBar(s) {
  if (s >= 85) return 'bg-gradient-to-t from-magenta via-cyan to-cyan-soft shadow-neon-cyan'
  if (s >= 65) return 'bg-gradient-to-t from-magenta/60 via-cyan to-cyan'
  return 'bg-white/40'
}

// Precise seconds timestamp for subtitle lines: 65.3 -> "1:05.30".
function fmtTs(t) {
  const v = Number(t) || 0
  const m = Math.floor(v / 60)
  return `${m}:${(v - m * 60).toFixed(2).padStart(5, '0')}`
}

// Short-lived signed URL for a project's source.mp4. Editor modals preview the
// raw source before any clip exists; we fetch a signature from the API rather
// than embedding the session token in the media URL (see resolveMediaAsset).
function useSourcePreviewUrl(jobId) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!jobId) return undefined
    let alive = true
    getProjectSourceUrl(jobId)
      .then((d) => { if (alive && d && d.url) setUrl(d.url) })
      .catch(() => {})
    return () => { alive = false }
  }, [jobId])
  return url
}

export default function ResultsStage({ project, onNewVideo, onOpenLibrary }) {
  const initial = useMemo(() => project.clips || [], [project.job_id])
  const [clips, setClips] = useState(initial)
  const [titles, setTitles] = useState({})
  const [selected, setSelected] = useState(() => new Set(initial.map((c) => c.index)))
  const [res, setRes] = useState('source')
  const [exportAspect, setExportAspect] = useState(project.options?.aspect || '9:16')
  const [onlyApproved, setOnlyApproved] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportErr, setExportErr] = useState('')
  const [exportRes, setExportRes] = useState(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [trimIdx, setTrimIdx] = useState(null)
  const [pubIdx, setPubIdx] = useState(null)
  const [thumbIdx, setThumbIdx] = useState(null)
  const [swapIdx, setSwapIdx] = useState(null)
  const [dubIdx, setDubIdx] = useState(null)
  const [brollIdx, setBrollIdx] = useState(null)
  const [voiceoverIdx, setVoiceoverIdx] = useState(null)
  const [enhanceIdx, setEnhanceIdx] = useState(null)
  const [insertIdx, setInsertIdx] = useState(null)
  const [effect, setEffect] = useState(null) // {kind:'clean'|'sfx', idx}
  const [fxIdx, setFxIdx] = useState(null)
  const [repurposeOpen, setRepurposeOpen] = useState(false)
  const [repurposeIdx, setRepurposeIdx] = useState(null)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [clipQuery, setClipQuery] = useState('')
  const [cutOpen, setCutOpen] = useState(false)
  const [editIdx, setEditIdx] = useState(null)
  const [studioOpen, setStudioOpen] = useState(false)

  useEffect(() => setClips(project.clips || []), [project.job_id])

  const totalMb = clips.reduce((a, c) => a + (c.size_mb || 0), 0)
  const rawCostIdr = project.cost?.total_idr == null ? NaN : Number(project.cost.total_idr)
  const rawCostUsd = project.cost?.total_usd == null ? NaN : Number(project.cost.total_usd)
  const costIdr = Number.isFinite(rawCostIdr)
    ? rawCostIdr
    : Number.isFinite(rawCostUsd)
      ? Math.round(rawCostUsd * 16500)
      : null

  // SYS.SUMMARY — viral-clip overview (how many viral/hook clips, avg & top score).
  const scores = clips.map((c) => c.hook_score || 0)
  const viralCount = scores.filter((s) => s >= 85).length
  const strongCount = scores.filter((s) => s >= 65 && s < 85).length
  const avgScore = scores.length
    ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
    : 0
  const topScore = scores.length ? Math.max(...scores) : 0
  const totalDur = clips.reduce((a, c) => a + ((c.end || 0) - (c.start || 0)), 0)
  const batchQuality = useMemo(() => {
    const keys = ['hook', 'context', 'payoff', 'emotion', 'technical']
    return Object.fromEntries(keys.map((key) => {
      const values = clips.map((c) => Number(c.quality_breakdown?.[key])).filter(Number.isFinite)
      return [key, values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : 0]
    }))
  }, [clips])

  // Search/filter clips by title, hook or duration (many clips = needs search).
  const shownClips = useMemo(() => {
    const q = clipQuery.trim().toLowerCase()
    if (!q) return clips
    return clips.filter((c) => {
      const hay = `${c.title || ''} ${c.hook || ''} ${c.start || ''} ${c.end || ''}`.toLowerCase()
      return hay.includes(q)
    })
  }, [clips, clipQuery])

  const toggleAll = (on) =>
    setSelected(on ? new Set(clips.map((c) => c.index)) : new Set())

  const toggle = (idx) => {
    const next = new Set(selected)
    next.has(idx) ? next.delete(idx) : next.add(idx)
    setSelected(next)
  }

  const persistTitle = async (idx) => {
    const t = titles[idx] || {}
    if (!t.title && !t.hook) return
    try {
      await updateClipMeta(project.job_id, idx, t)
      setClips((cs) =>
        cs.map((c) => (c.index === idx ? { ...c, ...t } : c)),
      )
    } catch {
      /* silent — local state still reflects edits */
    }
  }

  const runExport = async () => {
    setExporting(true)
    setExportErr('')
    setExportRes(null)
    try {
      const { job_id } = await exportProject(project.job_id, {
        indices: [...selected].sort((a, b) => a - b),
        resolution: res === 'source' ? null : res,
        aspect: exportAspect,
        only_approved: onlyApproved,
      })
      const data = await pollJob(job_id, { interval: 1200 })
      setExportRes(data.result)
    } catch (e) {
      setExportErr(e.message)
    } finally {
      setExporting(false)
    }
  }

  const onRendered = (updated) => {
    setClips((cs) => cs.map((c) => (c.index === updated.index ? { ...c, ...updated } : c)))
    setTrimIdx(null)
  }

  const trimClip = clips.find((c) => c.index === trimIdx)
  const renderElapsedSec = Number(
    project.completion_elapsed_sec || project.metrics?.total_sec || 0,
  )

  return (
    <div>
      {renderElapsedSec > 0 ? (
        <div
          className="mb-5 flex items-center gap-3 rounded-md border border-signal/35 bg-signal/10 px-4 py-3 text-signal"
          role="status"
          aria-live="polite"
        >
          <Icon name="checkcircle" className="h-5 w-5 shrink-0" />
          <p className="text-sm font-medium">
            Klip viral Anda berhasil dibuat dalam {Math.max(1, Math.ceil(renderElapsedSec / 60))} menit. Terima kasih sudah menunggu.
          </p>
        </div>
      ) : null}
      {/* meta header */}
      <Panel label="Project" dot="ok" className="mb-5" bodyClassName="px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 font-mono text-xs text-white/55">
          <span className="truncate text-white/75">{project.source_label || '—'}</span>
          <span className="text-white/30">ID {project.job_id}</span>
          <span>{timeAgo(project.created_at)}</span>
          <span>{clips.length} clips</span>
          <span>{totalMb.toFixed(1)} MB</span>
          {project.duration ? <span>src {fmtTime(project.duration)}</span> : null}
          {costIdr !== null && (
            <span className="text-warn" title={`≈ $${project.cost.total_usd} (transkripsi $${project.cost.asr_usd} · analisis AI $${project.cost.gemini_usd} · render $${project.cost.render_usd})`}>
              ≈ Rp{costIdr.toLocaleString('id-ID')}
            </span>
          )}
          <div className="ml-auto flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => setRepurposeOpen(true)} title="Ubah transkrip jadi X/LinkedIn/blog/IG"><Icon name="repurpose" className="h-3.5 w-3.5" /> Repurpose</Button>
            <Button variant="ghost" size="sm" onClick={() => setTranscriptOpen(true)} title="Edit teks transkrip → re-render caption"><Icon name="text" className="h-3.5 w-3.5" /> Transkrip</Button>
            <Button variant="ghost" size="sm" onClick={onOpenLibrary}>Library</Button>
            <Button size="sm" onClick={onNewVideo}>+ New video</Button>
          </div>
        </div>
      </Panel>

      {clips.length === 0 ? (
        <Panel className="mx-auto max-w-xl text-center" bodyClassName="p-10">
          <div className="font-mono text-sm text-white/40">
            {project.status === 'failed' ? 'RENDER FAILED' : '0 CLIPS PRODUCED'}
          </div>
          <p className="mt-2 text-sm text-white/55">
            {project.error
              ? `Render gagal: ${project.error}`
              : 'The model did not find viral moments. Try a different video or loosen the selection criteria.'}
          </p>
        </Panel>
      ) : (
        <>
          {/* SYS.SUMMARY — hasil klip viral: berapa, avg/top score, durasi */}
          <Panel label="Summary" dot="brand" className="mb-5" bodyClassName="px-4 py-3">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <div>
                <div className="font-mono text-2xl font-bold leading-none text-white">
                  {clips.length}
                </div>
                <div className="mt-1 font-mono text-[10px] uppercase tracking-widest text-white/40">
                  VIRAL CLIPS FOUND
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-signal/40 bg-signal/10 px-2.5 py-1 font-mono text-xs text-signal">
                  <Icon name="fire" className="h-3 w-3" /> viral ≥85 · {viralCount}
                </span>
                <span className="rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1 font-mono text-xs text-warn">
                  <Icon name="bolt" className="h-3 w-3" /> strong 65–84 · {strongCount}
                </span>
                <span className="rounded-md border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-xs text-white/60">
                  avg {avgScore}
                </span>
                <span className="rounded-md border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-xs text-white/60">
                  top {topScore}
                </span>
                <span className="rounded-md border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-xs text-white/60">
                  {fmtTime(totalDur)} total
                </span>
                <span className="rounded-md border border-border bg-navy-2/60 px-2.5 py-1 font-mono text-xs text-white/60">
                  {totalMb.toFixed(1)} MB
                </span>
              </div>
              <Button
                className="ml-auto"
                onClick={() => setRepurposeOpen(true)}
                title="Ubah tiap klip jadi blog / X / LinkedIn / IG caption"
              >
                <Icon name="repurpose" className="h-3.5 w-3.5" /> Blog & Repurpose
              </Button>
            </div>
            {Object.values(batchQuality).some(Boolean) && (
              <div className="mt-3 grid grid-cols-5 gap-2 border-t border-border-soft pt-3" aria-label="batch quality report">
                {Object.entries(batchQuality).map(([key, value]) => (
                  <div key={key}>
                    <div className="flex justify-between font-mono text-[8px] uppercase text-white/35">
                      <span>{key}</span><span className={scoreTone(value)}>{value}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded bg-white/10">
                      <div className={scoreBar(value)} style={{ width: `${value}%`, height: '100%' }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* batch export menu (sticky above grid) */}
          <div className="sticky top-16 z-20 mb-5">
            <div className="flex items-center gap-2 rounded-xl border border-border bg-navy-1/90 px-3 py-2 shadow-neon-cyan backdrop-blur">
              <button
                type="button"
                onClick={() => setStudioOpen(true)}
                className="flex items-center gap-2 rounded-md bg-brand/15 px-3 py-1.5 text-xs font-semibold text-cyan-soft transition hover:bg-brand/25"
                title="Klipin Studio: pilih warna subtitle, edit caption, render ulang"
              >
                <Icon name="paintbrush" className="h-3.5 w-3.5" /> Studio
              </button>
              <button
                type="button"
                onClick={() => setExportOpen((v) => !v)}
                className="flex items-center gap-2 rounded-md bg-brand/15 px-3 py-1.5 text-xs font-semibold text-cyan-soft transition hover:bg-brand/25"
                aria-expanded={exportOpen}
              >
                <Icon name="package" className="h-3.5 w-3.5" /> Batch export
                <span className="rounded bg-cyan/20 px-1.5 py-0.5 font-mono text-[9px]">
                  {selected.size}/{clips.length}
                </span>
                <span className="font-mono text-[10px]">{exportOpen ? '▲' : '▼'}</span>
              </button>
              {exportRes && (
                <a
                  href={dl(resolveAsset(exportRes.zip_url))}
                  className="rounded-md border border-signal/40 bg-signal/10 px-3 py-1.5 text-xs font-semibold text-signal transition hover:bg-signal/20"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> ZIP {exportRes.count} klip · {exportRes.size_mb} MB
                </a>
              )}
              <div className="ml-auto flex items-center gap-2">
                {exportErr && <span className="text-xs text-danger">{exportErr}</span>}
                <Button variant="ghost" size="sm" onClick={() => setCutOpen(true)} title="Pilih banyak bagian source → gabung jadi 1 klip">
                  <Icon name="trim" className="h-3.5 w-3.5" /> Cut editor
                </Button>
              </div>
            </div>

            {exportOpen && (
              <div className="mt-2 rounded-xl border border-border bg-navy-1 p-3 shadow-neon-cyan">
                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-white/70">
                    <input
                      type="checkbox"
                      checked={selected.size === clips.length}
                      onChange={(e) => toggleAll(e.target.checked)}
                      className="h-4 w-4 rounded accent-cyan"
                    />
                    Select all
                  </label>
                  <Mono className="text-white/40">{selected.size}/{clips.length} selected</Mono>
                  <Select
                    value={exportAspect}
                    onChange={(e) => setExportAspect(e.target.value)}
                    className="w-24"
                    aria-label="export aspect"
                  >
                    {EXPORT_ASPECTS.map((a) => (
                      <option key={a.value} value={a.value}>{a.label}</option>
                    ))}
                  </Select>
                  <Select
                    value={res}
                    onChange={(e) => setRes(e.target.value)}
                    className="w-36"
                    aria-label="export resolution"
                  >
                    {RESOLUTIONS.map((r) => (
                      <option key={r.value} value={r.value}>{r.label}</option>
                    ))}
                  </Select>
                  <label
                    className="flex cursor-pointer items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-white/55"
                    title="Ekspor hanya klip yang sudah di-approve reviewer"
                  >
                    <input
                      type="checkbox"
                      checked={onlyApproved}
                      onChange={(e) => setOnlyApproved(e.target.checked)}
                      className="h-3.5 w-3.5 rounded accent-cyan"
                    />
                    Approved only
                  </label>
                  <Button
                    onClick={runExport}
                    disabled={selected.size === 0 || exporting}
                  >
                    {exporting ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="package" className="h-3.5 w-3.5" />} Export ZIP
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* clip search + grid */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative min-w-0 flex-1">
              <TextInput
                value={clipQuery}
                onChange={(e) => setClipQuery(e.target.value)}
                placeholder="Cari klip — judul / hook / durasi…"
                className="!py-2 pl-8"
                aria-label="search clips"
              />
              <span
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/30"
              >
                <Icon name="search" className="h-4 w-4" />
              </span>
            </div>
            <Mono className="text-white/35">{shownClips.length}/{clips.length} klip</Mono>
            {clipQuery.trim() && (
              <button
                type="button"
                onClick={() => setClipQuery('')}
                className="font-mono text-[10px] uppercase tracking-widest text-white/40 transition hover:text-white"
              >
                clear <Icon name="close" className="h-3 w-3" />
              </button>
            )}
          </div>
          {shownClips.length === 0 && (
            <Panel className="mb-4 text-center" bodyClassName="p-8">
              <div className="font-mono text-sm text-white/40">NO CLIP MATCH</div>
              <p className="mt-2 text-sm text-white/55">
                Tidak ada klip yang cocok dengan "{clipQuery}".
              </p>
              <Button variant="ghost" size="sm" className="mt-3" onClick={() => setClipQuery('')}>
                clear <Icon name="close" className="h-3 w-3" />
              </Button>
            </Panel>
          )}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {shownClips.map((c) => (
              <ClipCard
                key={`${c.index}-${c.rev || 0}`}
                clip={c}
                jobId={project.job_id}
                options={project.options}
                transcript={project.transcript}
                checked={selected.has(c.index)}
                onToggle={() => toggle(c.index)}
                titles={titles[c.index] || {}}
                onTitles={(t) => setTitles((m) => ({ ...m, [c.index]: t }))}
                onBlur={() => persistTitle(c.index)}
                onTrim={() => setTrimIdx(c.index)}
                onPublish={() => setPubIdx(c.index)}
                onThumbnail={() => setThumbIdx(c.index)}
                onSwap={() => setSwapIdx(c.index)}
                onDub={() => setDubIdx(c.index)}
                onBroll={() => setBrollIdx(c.index)}
                onVoiceover={() => setVoiceoverIdx(c.index)}
                onEnhance={() => setEnhanceIdx(c.index)}
                onInsert={() => setInsertIdx(c.index)}
                onEffect={(kind) => setEffect({ kind, idx: c.index })}
                onFx={() => setFxIdx(c.index)}
                onRepurpose={() => setRepurposeIdx(c.index)}
                onEdit={() => setEditIdx(c.index)}
                onCaptionEdit={(clip) =>
                  setClips((cs) => cs.map((x) =>
                    x.index === clip.index ? { ...x, ...clip } : x))}
                onClipUpdate={(clip) =>
                  setClips((cs) => cs.map((x) =>
                    x.index === clip.index ? { ...x, ...clip } : x))}
              />
            ))}
          </div>
        </>
      )}

      {trimClip && (
        <TrimModal
          clip={trimClip}
          jobId={project.job_id}
          duration={project.duration}
          baseOptions={project.options}
          onClose={() => setTrimIdx(null)}
          onRendered={onRendered}
          onRepick={({ clips }) => {
            setClips(clips)
            // Selection must track the new clip set (indices may have changed).
            setSelected(new Set((clips || []).map((c) => c.index)))
            setTrimIdx(null)
          }}
        />
      )}
      {pubIdx !== null && clips.some((c) => c.index === pubIdx) && (
        <PublishModal
          clip={clips.find((c) => c.index === pubIdx)}
          jobId={project.job_id}
          onClose={() => setPubIdx(null)}
        />
      )}
      {thumbIdx !== null && clips.some((c) => c.index === thumbIdx) && (
        <ThumbnailModal
          clip={clips.find((c) => c.index === thumbIdx)}
          jobId={project.job_id}
          onClose={() => setThumbIdx(null)}
        />
      )}
      {swapIdx !== null && clips.some((c) => c.index === swapIdx) && (
        <FaceSwapModal
          clip={clips.find((c) => c.index === swapIdx)}
          jobId={project.job_id}
          onClose={() => setSwapIdx(null)}
        />
      )}
      {dubIdx !== null && clips.some((c) => c.index === dubIdx) && (
        <DubbingModal
          clip={clips.find((c) => c.index === dubIdx)}
          jobId={project.job_id}
          onClose={() => setDubIdx(null)}
        />
      )}
      {brollIdx !== null && clips.some((c) => c.index === brollIdx) && (
        <BrollModal
          clip={clips.find((c) => c.index === brollIdx)}
          jobId={project.job_id}
          onClose={() => setBrollIdx(null)}
        />
      )}
      {voiceoverIdx !== null && clips.some((c) => c.index === voiceoverIdx) && (
        <VoiceoverModal
          clip={clips.find((c) => c.index === voiceoverIdx)}
          jobId={project.job_id}
          onClose={() => setVoiceoverIdx(null)}
        />
      )}
      {enhanceIdx !== null && clips.some((c) => c.index === enhanceIdx) && (
        <EnhanceModal
          clip={clips.find((c) => c.index === enhanceIdx)}
          jobId={project.job_id}
          onClose={() => setEnhanceIdx(null)}
        />
      )}
      {insertIdx !== null && clips.some((c) => c.index === insertIdx) && (
        <InsertModal
          clip={clips.find((c) => c.index === insertIdx)}
          jobId={project.job_id}
          onClose={() => setInsertIdx(null)}
        />
      )}
      {effect && clips.some((c) => c.index === effect.idx) && (
        <EffectModal
          kind={effect.kind}
          clip={clips.find((c) => c.index === effect.idx)}
          jobId={project.job_id}
          onClose={() => setEffect(null)}
        />
      )}
      {fxIdx !== null && clips.some((c) => c.index === fxIdx) && (
        <VideoFxModal
          clip={clips.find((c) => c.index === fxIdx)}
          jobId={project.job_id}
          onClose={() => setFxIdx(null)}
        />
      )}
      {repurposeOpen && (
        <RepurposeModal project={project} onClose={() => setRepurposeOpen(false)} />
      )}
      {repurposeIdx !== null && clips.some((c) => c.index === repurposeIdx) && (
        <RepurposeModal
          project={project}
          initialClipIndex={repurposeIdx}
          onClose={() => setRepurposeIdx(null)}
        />
      )}
      {transcriptOpen && (
        <TranscriptModal
          project={project}
          onClose={() => setTranscriptOpen(false)}
          onRerendered={(clips) => {
            setClips(clips)
            setSelected(new Set((clips || []).map((c) => c.index)))
          }}
        />
      )}
      <StudioDrawer
        project={project}
        open={studioOpen}
        onClose={() => setStudioOpen(false)}
        onUpdated={() => setStudioOpen(false)}
      />
      {cutOpen && (
        <CutModal
          jobId={project.job_id}
          duration={project.duration}
          baseOptions={project.options}
          onClose={() => setCutOpen(false)}
          onRendered={(clip) => {
            setClips((cs) => (cs.some((c) => c.index === clip.index)
              ? cs.map((c) => (c.index === clip.index ? { ...c, ...clip } : c))
              : [...cs, clip]))
            setCutOpen(false)
          }}
        />
      )}
      {editIdx !== null && clips.some((c) => c.index === editIdx) && (
        <EditModal
          clip={clips.find((c) => c.index === editIdx)}
          jobId={project.job_id}
          baseOptions={project.options}
          onClose={() => setEditIdx(null)}
          onRendered={onRendered}
        />
      )}
    </div>
  )
}

const METRIC_ICONS = {
  views: 'views',
  likes: 'thumb',
  comments: 'comments',
  shares: 'repurpose',
  reach: 'radio',
}

const PLATFORM_DOTS = {
  youtube: '#FF0033',
  tiktok: '#69C9D0',
  instagram: '#E1306C',
}

// Approval workflow row on a clip card: status pill + quick review actions.
function ApprovalRow({ clip, jobId, onClipUpdate }) {
  const [status, setStatus] = useState(clip.approval_status || '')
  const [note, setNote] = useState(clip.approval_note || '')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setStatus(clip.approval_status || '')
    setNote(clip.approval_note || '')
  }, [clip.approval_status, clip.approval_note])

  const apply = async (next, nextNote = note) => {
    setBusy(true)
    try {
      const res = await setClipApproval(jobId, clip.index, next, nextNote)
      setStatus(res.approval_status)
      setNote(res.note || '')
      onClipUpdate?.({ index: clip.index, approval_status: res.approval_status, approval_note: res.note || '' })
    } catch {
      /* keep previous state on failure */
    } finally {
      setBusy(false)
    }
  }

  const pill = status === 'approved'
    ? { label: 'APPROVED', cls: 'border-signal/50 bg-signal/15 text-signal' }
    : status === 'changes_requested'
      ? { label: 'REVISI', cls: 'border-warn/50 bg-warn/15 text-warn' }
      : status === 'pending'
        ? { label: 'PENDING', cls: 'border-border bg-navy-2 text-white/50' }
        : null

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5" aria-label="approval workflow">
      {pill && (
        <span className={`rounded border px-1.5 py-0.5 font-mono text-[8px] uppercase ${pill.cls}`}>
          {pill.label}
        </span>
      )}
      <button
        type="button"
        onClick={() => apply('approved')}
        disabled={busy || status === 'approved'}
        title="Setujui klip"
        aria-label={`approve clip ${clip.index + 1}`}
        className="rounded border border-border bg-navy-2 px-1.5 py-0.5 font-mono text-[9px] text-white/50 transition hover:border-signal/60 hover:text-signal disabled:opacity-40"
      >
        ✓ Setujui
      </button>
      <button
        type="button"
        onClick={() => {
          const n = window.prompt('Catatan revisi (opsional):', note || '')
          if (n !== null) apply('changes_requested', n)
        }}
        disabled={busy || status === 'changes_requested'}
        title="Minta revisi klip"
        aria-label={`request changes for clip ${clip.index + 1}`}
        className="rounded border border-border bg-navy-2 px-1.5 py-0.5 font-mono text-[9px] text-white/50 transition hover:border-warn/60 hover:text-warn disabled:opacity-40"
      >
        ✕ Revisi
      </button>
      {status && (
        <button
          type="button"
          onClick={() => apply('')}
          disabled={busy}
          title="Hapus status review"
          aria-label={`clear approval for clip ${clip.index + 1}`}
          className="rounded border border-border bg-navy-2 px-1.5 py-0.5 font-mono text-[9px] text-white/40 transition hover:text-white/75 disabled:opacity-40"
        >
          ↺
        </button>
      )}
      {note && status === 'changes_requested' && (
        <span className="font-mono text-[9px] italic text-warn/80" title={note}>
          “{note.slice(0, 40)}{note.length > 40 ? '…' : ''}”
        </span>
      )}
    </div>
  )
}

function ClipCard({ clip, jobId, options, checked, onToggle, titles, onTitles, onBlur, onTrim, onPublish, onThumbnail, onSwap, onDub, onBroll, onVoiceover, onEnhance, onInsert, onEffect, onFx, onRepurpose, onCaptionEdit, onClipUpdate, onEdit, transcript }) {
  const [muted, setMuted] = useState(false)
  const [stats, setStats] = useState(null)   // null = not loaded, [] = loaded but none
  const [statsOpen, setStatsOpen] = useState(false)
  const [statsBusy, setStatsBusy] = useState(false)
  const [statsErr, setStatsErr] = useState('')
  const [thumbOpen, setThumbOpen] = useState(false)
  const [dlOpen, setDlOpen] = useState(false)
  const [capOpen, setCapOpen] = useState(false)
  const [studioOpen, setStudioOpen] = useState(false)
  const [copiedCap, setCopiedCap] = useState(false)
  const [capEdit, setCapEdit] = useState(false)
  const [capLines, setCapLines] = useState([])
  const [capBusy, setCapBusy] = useState(false)
  const [capErr, setCapErr] = useState('')

  // The Studio timeline must use the exact phrase blocks burned into the
  // rendered .ass file. Raw transcript segments are much longer and can make
  // the whole clip look like a single, inaccurate caption block.
  const loadCaptionLines = async () => {
    if (capLines.length > 0 || capBusy) return
    setCapBusy(true)
    setCapErr('')
    try {
      const data = await getClipCaptions(jobId, clip.index)
      setCapLines(data.blocks || [])
    } catch (e) {
      setCapErr(e.message)
    } finally {
      setCapBusy(false)
    }
  }

  const copyCaption = () => {
    const txt = [clip.hook, ...(clip.hashtags || [])].filter(Boolean).join('\n')
    navigator.clipboard?.writeText(txt)
    setCopiedCap(true)
    setTimeout(() => setCopiedCap(false), 1600)
  }

  const openCapEditor = async () => {
    setCapOpen(true)
    setCapEdit(true)
    await loadCaptionLines()
  }

  const openStudio = () => {
    setStudioOpen(true)
    void loadCaptionLines()
  }

  const saveCapEditor = async () => {
    setCapBusy(true)
    setCapErr('')
    try {
      const { job_id } = await editCaption(jobId, clip.index,
        capLines.map((l) => l.text))
      const data = await pollJob(job_id, { interval: 1200 })
      const updated = data.result?.clip
      if (updated) onCaptionEdit?.(updated)
      setCapEdit(false)
      setCapLines([])
    } catch (e) {
      setCapErr(e.message)
    } finally {
      setCapBusy(false)
    }
  }

  const autofixCapEditor = async () => {
    setCapBusy(true)
    setCapErr('')
    try {
      const { lines } = await autofixCaption(jobId, clip.index,
        capLines.map((l) => l.text))
      setCapLines((cur) => cur.map((b, i) =>
        i < lines.length ? { ...b, text: lines[i] } : b))
    } catch (e) {
      setCapErr(e.message)
    } finally {
      setCapBusy(false)
    }
  }

  const toggleStats = async () => {
    if (statsOpen) { setStatsOpen(false); return }
    if (stats === null && !statsBusy) {
      setStatsBusy(true)
      setStatsErr('')
      try {
        const data = await getClipAnalytics(jobId, clip.index)
        setStats(data.analytics || [])
      } catch (e) {
        setStatsErr(e.message)
        setStats([])
      } finally {
        setStatsBusy(false)
      }
    }
    setStatsOpen(true)
  }

  return (
    <article className="card-chrome overflow-hidden rounded-xl">
      <div className="relative aspect-[9/16] w-full bg-black">
        <FullscreenVideo
          src={`${resolveAsset(clip.video_url)}&v=${clip.rev || 0}`}
          muted={muted}
        >
          {options?.watermark && (
            <span className="absolute bottom-9 right-3 z-10 font-mono text-[10px] text-white/35 drop-shadow-[0_1px_2px_rgba(0,0,0,0.4)]">
              {options.watermark}
            </span>
          )}
          {options?.show_klipin_watermark !== false && (
            <span className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 font-mono text-sm text-white/20 drop-shadow-[0_1px_3px_rgba(0,0,0,0.5)]">
              Klip-in
            </span>
          )}
          <button
            type="button"
            onClick={() => setMuted((m) => !m)}
            title={muted ? 'Unmute preview' : 'Mute preview (navigasi senyap)'}
            aria-label={muted ? 'unmute' : 'mute'}
            className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-1 font-mono text-[10px] text-white/70 text-white-solid transition hover:bg-black/80"
          >
            <Icon name={muted ? 'mute' : 'speaker'} className="h-3.5 w-3.5" />
          </button>
          <span className="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white/70 text-white-solid">
            CLIP {String(clip.index + 1).padStart(2, '0')}
          </span>
          {clip.rev > 0 && (
            <span className="absolute right-2 top-2 rounded bg-signal/90 px-1.5 py-0.5 font-mono text-[10px] text-black">
              RE-RENDERED
            </span>
          )}
        </FullscreenVideo>
      </div>
      <div className="p-3.5">
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            aria-label={`select clip ${clip.index + 1}`}
            className="h-4 w-4 rounded accent-cyan"
          />
          <input
            value={titles.title ?? clip.title ?? ''}
            onChange={(e) => onTitles({ title: e.target.value })}
            onBlur={onBlur}
            placeholder="Clip title"
            className="w-full bg-transparent text-sm font-semibold text-white outline-none placeholder:text-white/25"
            aria-label={`clip ${clip.index + 1} title`}
          />
        </div>
        <input
          value={titles.hook ?? clip.hook ?? ''}
          onChange={(e) => onTitles({ hook: e.target.value })}
          onBlur={onBlur}
          placeholder="Hook line…"
          className="mt-1 w-full bg-transparent text-xs text-white/50 outline-none placeholder:text-white/20"
          aria-label={`clip ${clip.index + 1} hook`}
        />
        {clip.reason && (
          <p className="mt-1.5 border-l-2 border-warn/50 pl-2 font-mono text-[10px] italic leading-relaxed text-warn/80">
            {clip.reason}
          </p>
        )}
        {clip.topic && (
          <div className="mt-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-cyan-soft/70">
            <span className="text-white/30">Topik:</span>
            <span className="text-cyan-soft">{clip.topic}</span>
            {clip.speech_density > 0 && (
              <span className="ml-auto text-white/30">padat {clip.speech_density}</span>
            )}
          </div>
        )}
        {clip.layout_plan?.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1" aria-label="layout intelligence plan">
            {clip.layout_plan_edited ? (
              <span className="rounded border border-signal/40 bg-signal/10 px-1.5 py-0.5 font-mono text-[8px] uppercase text-signal">
                layout edited
              </span>
            ) : null}
            {clip.layout_plan.map((scene, i) => (
              <span key={`${scene.start}-${i}`} className="rounded border border-cyan/20 bg-cyan/5 px-1.5 py-0.5 font-mono text-[8px] uppercase text-cyan-soft/70">
                {fmtTime(scene.start)}–{fmtTime(scene.end)} · {scene.layout}
              </span>
            ))}
          </div>
        )}
        <ApprovalRow clip={clip} jobId={jobId} onClipUpdate={onClipUpdate} />
        <div className="mt-3 flex items-center justify-between font-mono text-[11px] text-white/40">
          <span>{fmtTime(clip.start)} – {fmtTime(clip.end)}</span>
          <span>{clip.size_mb} MB</span>
        </div>
        {clip.hook_score > 0 && (
          <div className="mt-2">
            <div className="flex items-center gap-2">
            <span
              className={`rounded-md border px-2 py-1 font-mono text-sm font-bold ${
                clip.hook_score >= 85
                  ? 'border-signal/50 bg-signal/15 text-signal'
                  : clip.hook_score >= 65
                    ? 'border-warn/50 bg-warn/10 text-warn'
                    : 'border-border bg-navy-2/60 text-white/50'
              }`}
              title="Hook / viral score"
            >
              {clip.hook_score >= 85 ? <Icon name="fire" className="h-3.5 w-3.5" /> : <Icon name="star" className="h-3.5 w-3.5" />} {clip.hook_score}
            </span>
            <div className="flex-1">
              <div className="mb-0.5 flex justify-between font-mono text-[9px] uppercase tracking-wider text-white/30">
                <span>Viral score</span>
                <span className={scoreTone(clip.hook_score)}>{clip.hook_score}/100</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded bg-white/10">
                <div
                  className={`h-full rounded ${scoreBar(clip.hook_score)}`}
                  style={{ width: `${clip.hook_score}%` }}
                />
              </div>
            </div>
            </div>
            {clip.quality_breakdown && Object.keys(clip.quality_breakdown).length > 0 && (
              <div className="mt-2 grid grid-cols-5 gap-1" aria-label="clip quality breakdown">
                {[
                  ['Hook', 'hook'], ['Context', 'context'], ['Payoff', 'payoff'],
                  ['Emotion', 'emotion'], ['Technical', 'technical'],
                ].map(([label, key]) => {
                  const value = Math.max(0, Math.min(100, Number(clip.quality_breakdown[key]) || 0))
                  return (
                    <div key={key} className="rounded border border-border-soft bg-navy-2/50 px-1.5 py-1" title={`${label}: ${value}/100`}>
                      <div className="truncate font-mono text-[7px] uppercase tracking-wider text-white/35">{label}</div>
                      <div className="mt-0.5 h-1 overflow-hidden rounded bg-white/10">
                        <div className={scoreBar(value)} style={{ width: `${value}%`, height: '100%' }} />
                      </div>
                      <div className={`mt-0.5 font-mono text-[9px] ${scoreTone(value)}`}>{value}</div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
        {/* thumbnail — auto-generated per clip (free ffmpeg), download link */}
        {clip.thumbnail_url && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setThumbOpen((o) => !o)}
              className="flex w-full items-center justify-between rounded-md border border-border bg-navy-2/60 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider text-white/50 transition hover:border-cyan/40"
              aria-expanded={thumbOpen}
            >
              <span><Icon name="thumbnail" className="h-3.5 w-3.5" /> Thumbnail</span>
              <span className="font-mono text-[10px] text-white/35">{thumbOpen ? 'hide ▲' : 'show ▼'}</span>
            </button>
            {thumbOpen && (
              <div className="mt-1.5 overflow-hidden rounded-md border border-border">
                <img
                  src={resolveAsset(clip.thumbnail_url)}
                  alt={`thumbnail clip ${clip.index + 1}`}
                  loading="lazy"
                  className="aspect-[9/16] max-h-[32rem] w-full bg-black object-contain"
                />
                <a
                  href={dl(resolveAsset(clip.thumbnail_url))}
                  download={`${slugify(clip.title)}_thumb.jpg`}
                  className="block bg-cyan px-3 py-1.5 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download thumbnail
                </a>
              </div>
            )}
          </div>
        )}
        {/* caption & subtitle — timestamped lines + .ass download */}
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setCapOpen((o) => !o)}
            className="flex w-full items-center justify-between rounded-md border border-border bg-navy-2/60 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider text-white/50 transition hover:border-cyan/40"
            aria-expanded={capOpen}
          >
            <span><Icon name="comments" className="h-3.5 w-3.5" /> Caption & Subtitle</span>
            <span className="font-mono text-[10px] text-white/35">{capOpen ? 'hide ▲' : 'show ▼'}</span>
          </button>
          {capOpen && (
            <div className="mt-1.5 space-y-1 rounded-md border border-border bg-navy-2/40 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[9px] uppercase tracking-wider text-white/35">
                  {capEdit ? <><Icon name="edit" className="h-3 w-3" /> Edit subtitle</> : `${captionLines.length} baris`}
                </span>
                {!capBusy && (
                  <button
                    type="button"
                    onClick={() => {
                      if (capEdit) { setCapEdit(false); setCapErr(''); }
                      else openCapEditor()
                    }}
                    className="rounded bg-cyan/10 px-1.5 py-0.5 font-mono text-[9px] text-cyan-soft transition hover:bg-cyan/20"
                  >
                    {capEdit ? <Icon name="close" className="h-3 w-3" /> : <Icon name="edit" className="h-3 w-3" />} {capEdit ? 'batal' : 'Edit'}
                  </button>
                )}
              </div>
              {capBusy && (
                <div className="flex items-center gap-1.5 font-mono text-[10px] text-white/50">
                  <Spinner className="h-3 w-3" /> {capEdit ? 'memuat & menyimpan…' : '…'}
                </div>
              )}
              {capErr && (
                <div className="font-mono text-[10px] text-danger">{capErr}</div>
              )}
              {!capBusy && capEdit && (
                <div className="space-y-1.5">
                  {capLines.length === 0 && !capErr && (
                    <div className="font-mono text-[10px] text-white/35">tidak ada blok subtitle</div>
                  )}
                  {capLines.map((b, i) => (
                    <div key={i} className="space-y-0.5">
                      <div className="font-mono text-[9px] text-white/35">
                        [{fmtTs(b.start)} → {fmtTs(b.end)}]
                      </div>
                      <input
                        value={b.text}
                        onChange={(e) => {
                          const next = capLines.map((x, j) =>
                            j === i ? { ...x, text: e.target.value } : x)
                          setCapLines(next)
                        }}
                        className="w-full rounded border border-border bg-navy-2 px-1.5 py-1 font-mono text-[10px] text-white outline-none focus:border-cyan"
                      />
                    </div>
                  ))}
                  {capLines.length > 0 && (
                    <>
                      <button
                        type="button"
                        onClick={autofixCapEditor}
                        className="w-full rounded border border-cyan/40 bg-cyan/5 px-2 py-1 font-mono text-[9px] font-semibold text-cyan-soft transition hover:bg-cyan/20"
                      >
                        <Icon name="sparkles" className="h-3 w-3" /> Auto-fix AI (typo)
                      </button>
                      <button
                        type="button"
                        onClick={saveCapEditor}
                        className="w-full rounded bg-cyan px-2 py-1 font-mono text-[9px] font-semibold text-black transition hover:bg-cyan-soft"
                      >
                        <Icon name="check" className="h-3 w-3" /> Simpan & render ulang
                      </button>
                    </>
                  )}
                </div>
              )}
              {!capBusy && !capEdit && captionLines.length === 0 && (
                <div className="font-mono text-[10px] text-white/35">tidak ada teks dalam rentang klip</div>
              )}
              {!capBusy && !capEdit && captionLines.map((s, i) => (
                <div key={i} className="font-mono text-[10px] leading-relaxed text-white/65">
                  <span className="text-cyan-soft">[{fmtTs(s.start)} → {fmtTs(s.end)}]</span> {(s.text || '').trim()}
                </div>
              ))}
              {!capEdit && clip.subtitle_url && (
                <a
                  href={`/api/projects/${jobId}/subtitle/${clip.index + 1}`}
                  download={`${slugify(clip.title)}.ass`}
                  className="mt-1 block rounded bg-cyan/10 px-2 py-1 text-center font-mono text-[9px] text-cyan-soft transition hover:bg-cyan/20"
                >
                  ⬇ subtitle .ass
                </a>
              )}
            </div>
          )}
        </div>
        {/* hashtags — auto (batched) per clip */}
        {clip.hashtags?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {clip.hashtags.map((h, i) => (
              <span
                key={i}
                className="rounded-full border border-cyan/25 bg-cyan/5 px-2 py-0.5 font-mono text-[9px] text-cyan-soft"
              >
                {h}
              </span>
            ))}
          </div>
        )}
        {/* analytics — live metrics per platform, no modal needed */}
        <button
          type="button"
          onClick={toggleStats}
          className="mt-2 flex w-full items-center justify-between gap-2 rounded-md border border-border bg-navy-2/60 px-2.5 py-1.5 text-left transition hover:border-cyan/40"
          aria-expanded={statsOpen}
        >
          <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-white/50">
            <Icon name="analytics" className="h-3.5 w-3.5" /> Analytics
            {stats && stats.length > 0 && (
              <span className="rounded bg-signal/15 px-1 py-0.5 text-[9px] text-signal">
                {stats.length} platform
              </span>
            )}
          </span>
          <span className="font-mono text-[10px] text-white/35">
            {statsBusy ? '…' : statsOpen ? 'hide ▲' : 'show ▼'}
          </span>
        </button>
        {statsOpen && (
          <div className="mt-1.5 space-y-1.5">
            {statsBusy && (
              <div className="flex items-center gap-2 rounded-md border border-border bg-navy-2/40 px-2.5 py-2 font-mono text-[10px] text-white/40">
                <Spinner className="h-3 w-3" /> fetching live metrics…
              </div>
            )}
            {!statsBusy && statsErr && (
              <p className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-[10px] text-danger">
                {statsErr}
              </p>
            )}
            {!statsBusy && !statsErr && stats.length === 0 && (
              <p className="rounded-md border border-border bg-navy-2/40 px-2.5 py-1.5 font-mono text-[10px] text-white/35">
                belum ada post ter-publish untuk klip ini
              </p>
            )}
            {!statsBusy && !statsErr && stats.map((a) => (
              <div key={`${a.platform}-${a.post_id}`} className="rounded-md border border-border-soft bg-navy-2/40 px-2.5 py-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: PLATFORM_DOTS[a.platform] || '#888' }} />
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-white/75">{a.platform}</span>
                  {a.post_url && (
                    <a
                      href={a.post_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-auto font-mono text-[9px] text-cyan-soft/70 transition hover:text-cyan-soft"
                    >
                      open ↗
                    </a>
                  )}
                </div>
                {a.metrics ? (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {Object.entries(a.metrics).map(([k, v]) => (
                      <span key={k} className="flex items-center gap-1 font-mono text-[10px] text-white/60">
                        <span className="text-white/30">{METRIC_ICONS[k] ? <Icon name={METRIC_ICONS[k]} className="h-3 w-3" /> : '•'}</span>
                        <span className="text-white/85">{fmtCount(v)}</span>
                        <span className="uppercase tracking-wider text-white/30">{k}</span>
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="mt-0.5 font-mono text-[9px] text-warn"><Icon name="warn" className="h-3 w-3" /> {a.error || 'unavailable'}</div>
                )}
              </div>
            ))}
          </div>
        )}
        {/* Primary action row — one clean button opens the full editor studio. */}
        <div className="mt-2.5 flex gap-1.5">
          <Button
            variant="primary"
            size="sm"
            className="flex-1 py-2"
            onClick={openStudio}
            title="Buka Klip-in Studio — semua alat edit dalam satu panel"
          >
            <Icon name="fx" className="h-3.5 w-3.5" /> Klip-in Studio
            <span className="ml-auto rounded bg-navy/70 px-1.5 py-0.5 font-mono text-[9px] normal-case tracking-normal text-cyan-soft">edit</span>
          </Button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setDlOpen((v) => !v)}
              title="Unduh video (720p / 1080p / 4K)"
              className="inline-flex h-full items-center justify-center gap-1 rounded-md bg-cyan px-3 py-1.5 text-xs font-semibold text-[#0B0C0E] transition hover:bg-cyan-soft"
            >
              <Icon name="download" className="h-4 w-4" />
              <span className="hidden font-mono text-[10px] sm:inline">{dlOpen ? '▲' : '▼'}</span>
            </button>
            {dlOpen && (
              <div className="absolute bottom-full right-0 z-30 mb-1.5 w-44 overflow-hidden rounded-lg border border-border bg-navy-1 shadow-neon-cyan">
                {[
                  { label: '720p', res: 720 },
                  { label: '1080p (Full HD)', res: 1080 },
                  { label: '4K (Ultra HD)', res: 2160 },
                ].map((o) => (
                  <a
                    key={o.res}
                    href={dl(`${resolveAsset(clip.video_url)}&v=${clip.rev || 0}&res=${o.res}`)}
                    download={`${slugify(clip.title)}_${o.res}.mp4`}
                    className="flex items-center justify-between px-3 py-2 text-xs text-white/75 transition hover:bg-cyan/10 hover:text-cyan-soft"
                  >
                    {o.label}
                    <span className="font-mono text-[9px] text-white/30">{o.res === 1080 ? '1080×1920' : o.res === 2160 ? '2160×3840' : '720×1280'}</span>
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
        {/* /end primary actions */}
      </div>

      {/* Klip-in Studio — bottom-sheet editor with all tools in one place. */}
      <ClipStudioSheet
        open={studioOpen}
        onClose={() => setStudioOpen(false)}
        clip={clip}
        jobId={jobId}
        copiedCap={copiedCap}
        captionLines={capLines}
        stats={stats}
        statsOpen={statsOpen}
        statsBusy={statsBusy}
        statsErr={statsErr}
        onCopyCaption={copyCaption}
        onToggleStats={toggleStats}
        onThumbToggle={() => setThumbOpen((o) => !o)}
        thumbOpen={thumbOpen}
        onCapToggle={() => setCapOpen((o) => !o)}
        capOpen={capOpen}
        onRepurpose={onRepurpose}
        onTrim={onTrim}
        onPublish={onPublish}
        onThumbnail={onThumbnail}
        onSwap={onSwap}
        onDub={onDub}
        onBroll={onBroll}
        onVoiceover={onVoiceover}
        onEnhance={onEnhance}
        onInsert={onInsert}
        onFx={onFx}
        onEdit={onEdit}
        onClean={() => onEffect('clean')}
        onSfx={() => onEffect('sfx')}
        onClipUpdate={onClipUpdate}
      />
    </article>
  )
}

function EditModal({ clip, jobId, baseOptions, onClose, onRendered }) {
  const [opts, setOpts] = useState(() => normalizeOptions(baseOptions))
  const [state, setState] = useState('idle') // idle|running|error
  const [err, setErr] = useState('')
  const [logs, setLogs] = useState([])

  const set = (patch) => setOpts((o) => ({ ...o, ...patch }))

  const apply = async () => {
    setState('running')
    setErr('')
    setLogs([])
    try {
      const { job_id } = await retrimClip(jobId, clip.index, {
        start: clip.start,
        end: clip.end,
        options: opts,
      })
      const data = await pollJob(job_id, {
        interval: 1200,
        onStatus: (d) => setLogs((d.logs || []).slice(-2)),
      })
      const updated = data.result?.clip
      if (updated) onRendered?.({ ...updated, index: clip.index })
      onClose()
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-navy-1 shadow-neon-cyan"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <Mono><Icon name="edit" className="h-4 w-4" /> Edit gaya — {clip.title || `Klip ${clip.index + 1}`}</Mono>
          <button type="button" onClick={onClose} className="text-white/50 transition hover:text-white"><Icon name="close" className="h-4 w-4" /></button>
        </div>
        <div className="space-y-4 p-4">
          <RealLayoutPreview clip={clip} jobId={jobId} options={opts} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Layout mode" hint="How the source fills the frame">
              <Select
                value={opts.layout || opts.crop || 'auto'}
                onChange={(e) => set({ layout: e.target.value })}
                aria-label="layout mode"
              >
                {LAYOUTS.map((l) => (
                  <option key={l.id} value={l.id} title={l.desc}>
                    {l.label} — {l.desc}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Aspect ratio" hint="Output frame shape">
              <Select
                value={opts.aspect || '9:16'}
                onChange={(e) => set({ aspect: e.target.value })}
                aria-label="aspect ratio"
              >
                {ASPECTS.map((a) => (
                  <option key={a.id} value={a.id}>{a.label}</option>
                ))}
              </Select>
            </Field>

            <Field label="Subtitle language" hint="Terjemahkan subtitle otomatis oleh AI (opsional)">
              <Select
                value={opts.translate_to || 'original'}
                onChange={(e) => set({ translate_to: e.target.value })}
                aria-label="subtitle language"
              >
                {LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
                ))}
              </Select>
            </Field>

            <Field label="Judul video" hint="Judul pembuka bisa dimatikan, diwarnai, dan digeser">
              <div className="space-y-2.5 rounded-md border border-border-soft bg-navy-2/40 p-2.5">
                <label className="flex cursor-pointer items-center justify-between gap-3 text-sm text-white/75">
                  <span>Tampilkan judul</span>
                  <input
                    type="checkbox"
                    checked={!!opts.title_enabled}
                    onChange={(e) => set({ title_enabled: e.target.checked })}
                    className="h-4 w-4 rounded accent-cyan"
                    aria-label="aktifkan judul video"
                  />
                </label>
                {opts.title_enabled && (
                  <>
                    <TextInput
                      value={opts.title_overlay || ''}
                      onChange={(e) => set({ title_overlay: e.target.value })}
                      placeholder="cth: INI BUKAN CERITA BIASA…"
                      aria-label="title overlay"
                    />
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={opts.title_color || '#FFFFFF'}
                        onChange={(e) => set({ title_color: e.target.value })}
                        className="h-8 w-12 cursor-pointer rounded border border-border bg-transparent p-0.5"
                        aria-label="warna judul"
                      />
                      <span className="font-mono text-[9px] text-white/40">warna judul</span>
                    </div>
                    <div>
                      <div className="mb-1 flex justify-between font-mono text-[9px] uppercase text-white/35">
                        <span>Posisi vertikal</span><span className="text-cyan-soft">{opts.title_position_y}%</span>
                      </div>
                      <input
                        type="range" min={8} max={45} step={1}
                        value={opts.title_position_y}
                        onChange={(e) => set({ title_position_y: Number(e.target.value) })}
                        className="w-full accent-cyan"
                        aria-label="posisi vertikal judul"
                      />
                    </div>
                  </>
                )}
              </div>
            </Field>
          </div>

          <CaptionSettings options={opts} onChange={setOpts} title="MACHINE SETTINGS" editorMode />

          {logs.length > 0 && (
            <div className="space-y-0.5 rounded-md border border-border bg-navy-2/40 p-2 font-mono text-[10px] text-white/60">
              {logs.map((l, i) => <div key={i}>{l}</div>)}
            </div>
          )}
          {err && <p className="text-xs text-danger">{err}</p>}
          <div className="flex gap-2">
            <Button onClick={apply} disabled={state === 'running'}>
              {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="edit" className="h-3.5 w-3.5" />} Render ulang gaya
            </Button>
            <Button variant="ghost" onClick={onClose} disabled={state === 'running'}>Batal</Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function RealLayoutPreview({ clip, jobId, options }) {
  const layout = options.layout || options.crop || 'auto'
  const [proof, setProof] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestId = useRef(0)
  const key = JSON.stringify([jobId, clip.index, clip.rev, layout, clip.layout_plan,
    clip.layout_plan_edited, clip.subject_lock, options.gameplay_face_position,
    options.gameplay_ratio])
  useEffect(() => () => { requestId.current += 1 }, [])
  const current = proof?.key === key
  const renderPreview = async () => {
    const id = ++requestId.current
    setBusy(true)
    setError('')
    try {
      const data = await previewClipLayout(jobId, clip.index, { ...options, layout, crop: layout })
      if (id === requestId.current) setProof({ ...data, key })
    } catch (e) {
      if (id === requestId.current) setError(e.message)
    } finally {
      if (id === requestId.current) setBusy(false)
    }
  }
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between font-mono text-[9px] uppercase tracking-wider text-white/40">
        <span>{current ? 'Preview render layout' : 'Hasil render tersimpan'}</span>
        <span className="text-cyan-soft">9:16 · {layout}</span>
      </div>
      <div className="relative mx-auto aspect-[9/16] max-h-72 overflow-hidden rounded-lg border border-cyan/30 bg-black">
        <video
          key={current ? proof.video_url : clip.video_url}
          src={resolveAsset(current ? proof.video_url : clip.video_url)}
          muted
          playsInline
          controls
          preload="metadata"
          className="h-full w-full object-contain"
        />
        {current && options.title_enabled && options.title_overlay && (
          <div
            className="pointer-events-none absolute left-[7%] right-[7%] -translate-y-1/2 rounded-md bg-black/70 px-2 py-1.5 text-center text-[9px] font-black leading-tight shadow-lg"
            style={{ top: `${options.title_position_y}%`, color: options.title_color || '#FFFFFF' }}
          >
            {options.title_overlay}
          </div>
        )}
        {current && options.subtitles_enabled && (
          <div
            className="pointer-events-none absolute left-[8%] right-[8%] -translate-y-1/2 text-center text-[9px] font-black uppercase leading-tight"
            style={{
              top: `${options.caption_position_y}%`,
              color: options.color || '#FFFFFF',
              textShadow: '0 1px 2px #000, 0 0 2px #000',
            }}
          >
            <span style={{ color: options.karaoke_color || '#FFE500' }}>PREVIEW</span> SUBTITLE
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-[7%] top-[8%] bottom-[16%] rounded border border-dashed border-signal/70" />
        <span className="pointer-events-none absolute left-[8%] top-[9%] rounded bg-black/65 px-1 py-0.5 font-mono text-[7px] uppercase text-signal">panduan platform</span>
        {Array.isArray(clip.subject_lock) && clip.subject_lock.length === 2 && (
          // NOTE: the lock point lives in SOURCE-frame coordinates, so it
          // cannot be drawn accurately on this rendered 9:16 preview —
          // show a text badge only (position is editable in TrimModal).
          <span className="pointer-events-none absolute right-[8%] bottom-[3%] rounded bg-black/65 px-1 py-0.5 font-mono text-[7px] uppercase text-signal">🔒 subject lock</span>
        )}
      </div>
      <p className="mt-1.5 text-center font-mono text-[8px] text-white/30">
        {current ? 'Crop dirender dari sumber asli. Teks di sini hanya panduan posisi.'
          : 'Perubahan layout belum terlihat. Buat preview untuk memeriksa crop baru.'}
        {' '}Garis putus-putus adalah panduan platform, bukan deteksi wajah.
      </p>
      {proof?.edited_plan && current && <p className="mt-1 text-xs text-cyan-soft">Mengikuti layout per-scene yang tersimpan.</p>}
      <Button className="mt-2 w-full" onClick={renderPreview} disabled={busy}>
        {busy ? <><Spinner className="h-3.5 w-3.5" /> Membuat preview layout…</> : 'Perbarui preview layout'}
      </Button>
      {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  )
}

const EFFECT_META = {
  clean: {
    title: 'SYS.CLEAN — SPEECH CLEANUP',
    accent: 'text-cyan-soft',
    run: <><Icon name="clean" className="h-3.5 w-3.5" /> Bersihkan klip</>,
    desc: 'Potong kata pengisi, jeda panjang, dan bad takes (AI) — ala editor profesional. Audio tetap selaras dengan video.',
  },
  sfx: {
    title: 'SYS.SFX — AUTO SOUND EFFECTS',
    accent: 'text-gold',
    run: <><Icon name="sfx" className="h-3.5 w-3.5" /> Tambah SFX</>,
    desc: 'Mendeteksi momen penekanan (angka → ting, tanda seru → pop, jeda transisi → swoosh) lalu menyisipkan efek suara otomatis.',
  },
}

const CLEAN_OPTIONS = [
  { key: 'remove_fillers', label: 'Kata pengisi', desc: 'um, eh, anu, gitu, kan…', default: true },
  { key: 'remove_silences', label: 'Jeda panjang', desc: 'pause ≥1 detik antar-segmen', default: true },
  { key: 'remove_bad_takes', label: 'Bad takes (AI)', desc: 'stumble, false start, pengulangan', default: false },
]

function EffectModal({ kind, clip, jobId, onClose }) {
  const meta = EFFECT_META[kind]
  const [state, setState] = useState('idle') // idle|running|error
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])
  const [opts, setOpts] = useState(() =>
    Object.fromEntries(CLEAN_OPTIONS.map(o => [o.key, o.default]))
  )

  const run = async () => {
    setState('running')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = kind === 'clean'
        ? await cleanClip(jobId, clip.index, opts)
        : await sfxClip(jobId, clip.index)
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

  const suffix = kind === 'clean' ? '_clean' : '_sfx'
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={kind}
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">{meta.title} — CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
              <p className="text-sm leading-relaxed text-white/55">{meta.desc}</p>

              {kind === 'clean' && (
                <div className="space-y-2">
                  <div className="font-mono text-[10px] uppercase tracking-wider text-white/35">Opsi pembersihan:</div>
                  {CLEAN_OPTIONS.map(o => (
                    <label
                      key={o.key}
                      className="flex items-center gap-3 rounded-md border border-cyan/15 bg-navy-2/40 px-3 py-2 transition hover:border-cyan/30"
                    >
                      <input
                        type="checkbox"
                        checked={opts[o.key]}
                        onChange={(e) => setOpts({ ...opts, [o.key]: e.target.checked })}
                        className="h-4 w-4 rounded accent-cyan"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-white/80">{o.label}</div>
                        <div className="font-mono text-[10px] text-white/40">{o.desc}</div>
                      </div>
                      {o.key === 'remove_bad_takes' && (
                        <span className="shrink-0 rounded-full border border-magenta/40 bg-magenta/10 px-2 py-0.5 font-mono text-[9px] text-magenta-soft">AI</span>
                      )}
                    </label>
                  ))}
                </div>
              )}

              <div className="whitespace-pre-wrap rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/40">
                {kind === 'clean'
                  ? `INPUT: transcript → ${opts.remove_fillers ? 'kata pengisi ' : ''}${opts.remove_silences ? 'jeda ' : ''}${opts.remove_bad_takes ? 'bad takes(AI) ' : ''}\n→ AI bersihkan audio · hasil: clip_N_clean.mp4`
                  : 'INPUT: transcript (angka, tanda seru, jeda >0.8s)\n→ SFX sintesis AI + timing · hasil: clip_N_sfx.mp4'}
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
              <Button variant="cyan" onClick={run} disabled={state === 'running' || (kind === 'clean' && !Object.values(opts).some(Boolean))} className="w-full">
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : meta.run}
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}${suffix}.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function EnhanceModal({ clip, jobId, onClose }) {
  const [strength, setStrength] = useState(2)
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
      const { job_id } = await enhanceClip(jobId, clip.index, { strength })
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
      aria-label="ai enhance"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.ENHANCE — AI UPSCALE & CLEANUP · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
                Upscale ke 1080p + noise removal + sharpening + vibrance. Bikin klip
                dari sumber resolusi rendah tampak Full HD tanpa API berbayar.
              </p>
              <Field label="Intensitas" hint="1 = halus · 2 = balance · 3 = agresif">
                <div className="flex gap-1.5">
                  {[1, 2, 3].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setStrength(n)}
                      className={`flex-1 rounded-md border px-3 py-2 font-mono text-xs transition ${
                        strength === n
                          ? 'border-cyan bg-cyan/15 text-cyan-soft'
                          : 'border-border bg-navy-2/60 text-white/50 hover:border-cyan/40'
                      }`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </Field>
              <div className="whitespace-pre-wrap rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/40">
                {`INPUT: clip_N.mp4 (segala resolusi)\n→ lanczos upscale 1080×1920 + unsharp + hqdn3d + vibrance\n→ hasil: clip_N.mp4 (diperbarui di tempat)`}
              </div>
              {state === 'running' && (
                <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                  <Spinner className="h-3.5 w-3.5" />
                  {logs[logs.length - 1] || 'Meningkatkan kualitas…'}
                </div>
              )}
              {state === 'error' && (
                <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
              )}
              <Button variant="cyan" onClick={run} disabled={state === 'running'} className="w-full">
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="sparkles" className="h-3.5 w-3.5" />} Enhance clip
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_enhance.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function InsertModal({ clip, jobId, onClose }) {
  const [kind, setKind] = useState('text') // text | image | audio
  const [text, setText] = useState('')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(2)
  const [position, setPosition] = useState('bottom')
  const [fontSize, setFontSize] = useState(64)
  const [color, setColor] = useState('#FFE500')
  const [widthPct, setWidthPct] = useState(60)
  const [volume, setVolume] = useState(0.3)
  const [keepOriginal, setKeepOriginal] = useState(true)
  const [file, setFile] = useState(null)
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
      let data
      if (kind === 'text') {
        if (!text.trim()) throw new Error('Tulis teks yang ingin disisipkan')
        data = await overlayText(jobId, clip.index, {
          kind, text, start: Number(start), end: Number(end),
          position, font_size: fontSize, color,
        })
      } else {
        if (!file) throw new Error('Pilih file terlebih dahulu')
        data = await overlayMedia(jobId, clip.index, file, {
          kind, start: Number(start), end: Number(end), position,
          width_pct: widthPct, volume, keep_original: keepOriginal,
        })
      }
      const polled = await pollJob(data.job_id, {
        interval: 1500,
        onStatus: (d) => setLogs((d.logs || []).slice(-2)),
      })
      setResult(polled.result)
      setState('idle')
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  const dur = Math.max(2, (clip.end || 30) - (clip.start || 0))
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="insert media"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.INSERT — CUSTOMIZE MEDIA · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
              <div className="grid grid-cols-3 gap-1.5">
                {[
                  { id: 'text', label: <><Icon name="text" className="h-3 w-3" /> TEKS</> },
                  { id: 'image', label: <><Icon name="thumbnail" className="h-3 w-3" /> GAMBAR</> },
                  { id: 'audio', label: <><Icon name="speaker" className="h-3 w-3" /> AUDIO</> },
                ].map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setKind(t.id)}
                    className={`rounded-md border px-3 py-2 font-mono text-[10px] uppercase tracking-wider transition ${
                      kind === t.id
                        ? 'border-cyan bg-cyan/15 text-cyan-soft'
                        : 'border-border bg-navy-2/60 text-white/45 hover:border-cyan/40'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Mulai (detik)">
                  <TextInput
                    type="number" min="0" max={dur} step="0.1" value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </Field>
                <Field label="Selesai (detik)">
                  <TextInput
                    type="number" min="0.5" max={dur + 2} step="0.1" value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </Field>
              </div>

              <Field label="Posisi">
                <Select value={position} onChange={(e) => setPosition(e.target.value)}>
                  <option value="top">Atas</option>
                  <option value="center">Tengah</option>
                  <option value="bottom">Bawah</option>
                </Select>
              </Field>

              {kind === 'text' && (
                <>
                  <Field label="Teks overlay">
                    <TextInput
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="cth: TONTON SAMPAI HABIS 🔥"
                    />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Ukuran font">
                      <TextInput
                        type="number" min="24" max="160" value={fontSize}
                        onChange={(e) => setFontSize(e.target.value)}
                      />
                    </Field>
                    <Field label="Warna">
                      <input
                        type="color"
                        value={color}
                        onChange={(e) => setColor(e.target.value)}
                        className="h-10 w-full cursor-pointer rounded-md border border-cyan/25 bg-navy-2"
                      />
                    </Field>
                  </div>
                </>
              )}

              {kind === 'image' && (
                <>
                  <Field label="File gambar (PNG/JPG)">
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(e) => setFile(e.target.files?.[0] || null)}
                      className="w-full rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-sm text-white/70 file:mr-2 file:rounded file:border-0 file:bg-cyan file:px-3 file:py-1 file:text-xs file:font-semibold file:text-navy"
                    />
                  </Field>
                  <Field label="Lebar (% frame)" hint="10–100% dari lebar video">
                    <TextInput
                      type="number" min="10" max="100" value={widthPct}
                      onChange={(e) => setWidthPct(e.target.value)}
                    />
                  </Field>
                </>
              )}

              {kind === 'audio' && (
                <>
                  <Field label="File audio (MP3/M4A/WAV)">
                    <input
                      type="file"
                      accept="audio/*"
                      onChange={(e) => setFile(e.target.files?.[0] || null)}
                      className="w-full rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-sm text-white/70 file:mr-2 file:rounded file:border-0 file:bg-cyan file:px-3 file:py-1 file:text-xs file:font-semibold file:text-navy"
                    />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Volume" hint="0.0–1.5">
                      <TextInput
                        type="number" min="0" max="1.5" step="0.05" value={volume}
                        onChange={(e) => setVolume(e.target.value)}
                      />
                    </Field>
                    <Field label="Audio asli">
                      <Select
                        value={keepOriginal ? 'yes' : 'no'}
                        onChange={(e) => setKeepOriginal(e.target.value === 'yes')}
                      >
                        <option value="yes">Tetap (mix)</option>
                        <option value="no">Ganti (insert only)</option>
                      </Select>
                    </Field>
                  </div>
                </>
              )}

              {state === 'running' && (
                <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                  <Spinner className="h-3.5 w-3.5" />
                  {logs[logs.length - 1] || 'Menyisipkan media…'}
                </div>
              )}
              {state === 'error' && (
                <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
              )}
              <Button variant="cyan" onClick={run} disabled={state === 'running'} className="w-full">
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="plus" className="h-3.5 w-3.5" />} Sisipkan & render
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_insert.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BrollModal({ clip, jobId, onClose }) {
  const [count, setCount] = useState(2)
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
      const { job_id } = await brollClip(jobId, clip.index, { count })
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
      aria-label="broll"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.BROLL — AUTO STOCK FOOTAGE · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
                Deteksi momen <em>talking-head</em> (bicara terus tanpa jeda) lalu ganti
                visualnya dengan stock footage relevan — audio pembicara tetap jalan
                di bawahnya (gaya b-roll klasik). Footage dari pustaka stok video (gratis).
              </p>

              <div>
                <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">Jumlah b-roll</div>
                <Select
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  className="w-full"
                  aria-label="broll count"
                >
                  <option value={1}>1 — ringan</option>
                  <option value={2}>2 — standar</option>
                  <option value={3}>3 — agresif</option>
                  <option value={4}>4 — maksimal</option>
                </Select>
              </div>

              <div className="rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/40">
                INPUT: transcript (stretch bicara ≥4s)
                → query stock (AI, fallback keyword lokal)
                → cari stok video · download mp4
                → overlay full-frame + fade in/out · hasil: clip_N_broll.mp4
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
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="broll" className="h-3.5 w-3.5" />} Tambah B-roll
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <p className="rounded-md border border-cyan/15 bg-navy-2/40 px-3 py-2 font-mono text-[11px] text-white/45">
                <Icon name="checkcircle" className="h-3.5 w-3.5" /> {result.windows.length} window · query “{result.query}” · {result.size_mb} MB
              </p>
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_broll.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function VoiceoverModal({ clip, jobId, onClose }) {
  const [catalog, setCatalog] = useState(null)
  const [voice, setVoice] = useState('')
  const [mixRatio, setMixRatio] = useState(0.15)
  const [state, setState] = useState('idle')
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  useEffect(() => {
    ;(async () => {
      try {
        const data = await getDubVoices()
        setCatalog(data)
      } catch { /* katalog gagal — dropdown tetap tampil kosong */ }
    })()
  }, [])

  const run = async () => {
    setState('running')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = await voiceoverClip(jobId, clip.index, {
        voice, mix_ratio: mixRatio,
      })
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
      aria-label="voiceover"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.VOICEOVER — AI TTS RE-VOICE · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
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
                Regenerate speech dari transcript menggunakan AI text-to-speech (gratis).
                Suara baru di-mix dengan audio original (15% original + 85% TTS).
              </p>

              <div>
                <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">AI Voice</div>
                <select
                  value={voice}
                  onChange={(e) => setVoice(e.target.value)}
                  className="w-full rounded-md border border-cyan/20 bg-navy-2 px-2.5 py-2 text-sm text-white"
                  aria-label="voiceover voice"
                >
                  <option value="">Auto (ikuti bahasa video)</option>
                  {Object.entries(catalog?.voices || {}).flatMap(([lang, voices]) =>
                    (voices || []).map((v) => (
                      <option key={v} value={v}>{v}</option>
                    )),
                  )}
                </select>
              </div>

              <div>
                <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">Original audio volume</div>
                <input
                  type="range" min="0" max="1" step="0.05"
                  value={mixRatio}
                  onChange={(e) => setMixRatio(Number(e.target.value))}
                  className="w-full accent-cyan"
                />
                <div className="flex justify-between font-mono text-[9px] text-white/30">
                  <span>Full replace</span>
                  <span>{Math.round(mixRatio * 100)}% original</span>
                  <span>No change</span>
                </div>
              </div>

              <div className="rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/40">
                INPUT: transcript segments → AI TTS audio
                → time-stretch to match duration → amix with original
                · hasil: clip_N_voiceover.mp4
              </div>

              {state === 'running' && (
                <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                  <Spinner className="h-3.5 w-3.5" />
                  {logs[logs.length - 1] || 'Generating TTS...'}
                </div>
              )}
              {state === 'error' && (
                <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
              )}
              <Button variant="cyan" onClick={run} disabled={state === 'running'} className="w-full">
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="dub" className="h-3.5 w-3.5" />} Generate Voiceover
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <p className="rounded-md border border-cyan/15 bg-navy-2/40 px-3 py-2 font-mono text-[11px] text-white/45">
                <Icon name="checkcircle" className="h-3.5 w-3.5" /> voice={result.voice} · {result.duration?.toFixed(1)}s · {result.size_mb} MB
              </p>
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_voiceover.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Retry</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DubbingModal({ clip, jobId, onClose }) {
  const [catalog, setCatalog] = useState(null) // {languages, voices}
  const [lang, setLang] = useState('en')
  const [voice, setVoice] = useState('')
  const [state, setState] = useState('idle') // idle|loading|running|error
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  useEffect(() => {
    ;(async () => {
      setState('loading')
      try {
        const data = await getDubVoices()
        setCatalog(data)
        setState('idle')
      } catch (e) {
        setState('error')
        setErr(e.message)
      }
    })()
  }, [])

  const pickLang = (code) => {
    setLang(code)
    const voices = catalog?.voices?.[code] || []
    setVoice(voices[0] || '')
  }

  const run = async () => {
    if (!lang) return
    setState('running')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = await dubClip(jobId, clip.index, {
        target_lang: lang,
        voice: voice || undefined,
      })
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
      aria-label="dubbing"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.DUB — AI DUBBING · CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
                Terjemahkan ucapan klip ke bahasa lain dan suarakan ulang dengan suara AI —
                kalimat tetap sinkron dengan timing asli (dubbing, bukan sekadar subtitle).
              </p>

              {state === 'loading' && (
                <div className="flex items-center gap-2 rounded-md border border-cyan/25 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
                  <Spinner className="h-3.5 w-3.5" /> memuat bahasa & suara…
                </div>
              )}

              {catalog && (
                <div className="space-y-3">
                  <div>
                    <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">Bahasa tujuan</div>
                    <Select
                      value={lang}
                      onChange={(e) => pickLang(e.target.value)}
                      className="w-full"
                      aria-label="target language"
                    >
                      {Object.entries(catalog.languages).map(([code, name]) => (
                        <option key={code} value={code}>{name} ({code})</option>
                      ))}
                    </Select>
                  </div>
                  <div>
                    <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/40">Suara</div>
                    <Select
                      value={voice}
                      onChange={(e) => setVoice(e.target.value)}
                      className="w-full"
                      aria-label="voice"
                    >
                      {(catalog.voices?.[lang] || []).map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </Select>
                  </div>
                </div>
              )}

              <div className="rounded-md border border-cyan/15 bg-navy-2/40 p-3 font-mono text-[11px] leading-relaxed text-white/40">
                INPUT: transcript (segmen dalam rentang klip)
                → AI translate → text-to-speech per segmen → sync
                → amix dengan audio asli (15%) · hasil: clip_N_dub_&lt;lang&gt;.mp4
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
              <Button
                variant="cyan"
                onClick={run}
                disabled={state === 'running' || state === 'loading'}
                className="w-full"
              >
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="dub" className="h-3.5 w-3.5" />} Dub ke {catalog?.languages?.[lang] || lang || '…'}
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-cyan/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <p className="rounded-md border border-cyan/15 bg-navy-2/40 px-3 py-2 font-mono text-[11px] text-white/45">
                <Icon name="checkcircle" className="h-3.5 w-3.5" /> {catalog?.languages?.[result.lang] || result.lang} · {result.voice} · {result.size_mb} MB
              </p>
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_dub_${result.lang}.mp4`}
                  className="flex-1 rounded-md bg-cyan px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-cyan-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FaceSwapModal({ clip, jobId, onClose }) {
  const [faceFile, setFaceFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [state, setState] = useState('idle') // idle|running|error
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  const pick = (file) => {
    setFaceFile(file)
    setPreview(file ? URL.createObjectURL(file) : '')
  }

  const run = async () => {
    if (!faceFile) return
    setState('running')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = await faceswapClip(jobId, clip.index, faceFile)
      const data = await pollJob(job_id, {
        interval: 2000,
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
      aria-label="face swap"
      onClick={(e) => e.target === e.currentTarget && state !== 'running' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.FACESWAP — CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'running'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
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
              <div
                role="button"
                tabIndex={0}
                onClick={() => document.getElementById('swap-face-input')?.click()}
                onKeyDown={(e) => e.key === 'Enter' && document.getElementById('swap-face-input')?.click()}
                className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-8 text-center transition-all ${
                  faceFile ? 'border-magenta/60 bg-magenta/5' : 'border-magenta/30 hover:border-magenta/60'
                }`}
              >
                {preview ? (
                  <img src={preview} alt="face preview" className="mx-auto h-32 w-32 rounded-full border-2 border-magenta/50 object-cover" />
                ) : (
                  <div className="font-mono text-xs uppercase tracking-widest text-magenta-soft">[ FOTO WAJAH ]</div>
                )}
                <p className="mt-3 text-sm text-white/70">
                  {faceFile ? faceFile.name : 'Upload foto wajah yang akan ditukar'}
                </p>
                <p className="mt-1 font-mono text-[11px] text-white/35">
                  JPG/PNG · wajah jelas menghadap depan
                </p>
                <input
                  id="swap-face-input"
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => pick(e.target.files?.[0])}
                />
              </div>

              <div className="rounded-md border border-warn/25 bg-warn/5 p-3 text-xs leading-relaxed text-white/55">
                <Icon name="bolt" className="h-3.5 w-3.5" /> <b className="text-warn">Aman & gratis</b> — pemrosesan berjalan di server kami, foto tidak
                dikirim ke pihak ketiga. Klip 50 detik butuh ±15–20 menit.
              </div>

              {state === 'running' && (
                <div className="flex items-center gap-2 rounded-md border border-magenta/30 bg-magenta/5 px-3 py-2 font-mono text-xs text-magenta-soft">
                  <Spinner className="h-3.5 w-3.5 border-magenta border-t-transparent" />
                  {logs[logs.length - 1] || 'AI sedang menyiapkan face-swap…'}
                </div>
              )}
              {state === 'error' && (
                <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
                  {err}
                </p>
              )}

              <Button
                variant="cyan"
                onClick={run}
                disabled={!faceFile || state === 'running'}
                className="w-full"
              >
                {state === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="swap" className="h-3.5 w-3.5" />} Ganti wajah
              </Button>
            </>
          ) : (
            <div className="space-y-3">
              <FullscreenVideo
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded-lg border border-magenta/30 bg-black"
                src={`${resolveAsset(result.video_url)}&t=${Date.now()}`}
              />
              <div className="flex gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download={`${slugify(clip.title)}_swap.mp4`}
                  className="flex-1 rounded-md bg-magenta px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-magenta-soft"
                >
                  <Icon name="download" className="h-3.5 w-3.5" /> Download hasil ({result.size_mb} MB)
                </a>
                <Button variant="ghost" onClick={() => setResult(null)}>Ulangi</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ThumbnailModal({ clip, jobId, onClose }) {
  const [count, setCount] = useState(3)
  const [state, setState] = useState('idle') // idle|generating|error
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  const run = async () => {
    setState('generating')
    setErr('')
    setResult(null)
    setLogs([])
    try {
      const { job_id } = await generateThumbnail(jobId, clip.index, {
        count,
        thumbnail_format: 'shorts',
      })
      const data = await pollJob(job_id, {
        interval: 1500,
        onStatus: (d) => setLogs((d.logs || []).slice(-3)),
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
      aria-label="thumbnail studio"
      onClick={(e) => e.target === e.currentTarget && state !== 'generating' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-cyan/15 px-4 py-2.5">
          <Micro className="text-white/60">SYS.THUMBNAIL — CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          <button
            onClick={onClose}
            disabled={state === 'generating'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="space-y-4 p-4">
          <div className="rounded-md border border-cyan/15 bg-navy-2/50 p-3">
            <div className="truncate text-sm font-semibold text-white/85">{clip.title}</div>
            {clip.hook && <div className="mt-0.5 truncate text-xs text-white/50">“{clip.hook}”</div>}
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <Field label="Jumlah thumbnail">
              <Select
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                className="w-24"
                aria-label="thumbnail count"
              >
                {[1, 2, 3, 4].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </Select>
            </Field>
            <Field label="Jenis thumbnail">
              <div className="rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 text-sm text-cyan-soft">Shorts — 9:16</div>
            </Field>
            <Button variant="cyan" onClick={run} disabled={state === 'generating'}>
              {state === 'generating' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="thumbnail" className="h-3.5 w-3.5" />} Generate
            </Button>
          </div>

          {state === 'generating' && (
            <div className="flex items-center gap-2 rounded-md border border-cyan/20 bg-cyan/5 px-3 py-2 font-mono text-xs text-cyan-soft">
              <VUMeter bars={4} />
              {logs[logs.length - 1] || 'AI sedang membuat thumbnail…'}
            </div>
          )}
          {state === 'error' && (
            <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
              {err}
            </p>
          )}

          {result && result.thumbnails?.length > 0 && (
            <>
              {result.auto_hook && (
                <div className="rounded-md border border-gold/25 bg-gold/5 px-3 py-2">
                  <Micro className="text-gold">AI AUTO-HOOK</Micro>
                  <div className="mt-1 text-sm font-semibold text-white/85">{result.auto_hook}</div>
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                {result.thumbnails.map((url, i) => (
                  <div key={url} className="overflow-hidden rounded-lg border border-cyan/25">
                    <img
                      src={resolveAsset(url)}
                      alt={`thumbnail ${i + 1}`}
                      className="aspect-[9/16] max-h-[32rem] w-full bg-black object-contain"
                    />
                    <a
                      href={dl(resolveAsset(url))}
                      download={`thumb_${clip.index + 1}_${i + 1}.jpg`}
                      className="flex items-center justify-center gap-1 border-t border-cyan/15 bg-navy-2/60 px-3 py-1.5 text-xs font-semibold text-cyan-soft transition hover:bg-cyan/10"
                    >
                      ⬇ Download {i + 1}
                    </a>
                  </div>
                ))}
              </div>
              {result.titles?.length > 0 && (
                <div className="rounded-md border border-gold/25 bg-gold/5 p-3">
                  <Micro className="text-gold"><Icon name="lightbulb" className="h-3.5 w-3.5" /> Judul rekomendasi</Micro>
                  <ul className="mt-2 space-y-1.5">
                    {result.titles.map((t, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-white/75">
                        <span className="mt-0.5 font-mono text-[10px] text-gold">{String(i + 1).padStart(2, '0')}</span>
                        {t}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {result.hashtags?.length > 0 && (
                <div className="rounded-md border border-magenta/25 bg-magenta/5 p-3">
                  <Micro className="text-magenta"><Icon name="tag" className="h-3.5 w-3.5" /> Hashtag viral</Micro>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {result.hashtags.map((h) => (
                      <span
                        key={h}
                        className="rounded-full border border-magenta/30 bg-magenta/10 px-2.5 py-1 font-mono text-[11px] text-magenta-soft"
                      >
                        {String(h).startsWith('#') ? h : `#${h}`}
                      </span>
                    ))}
                  </div>
                  <button
                    onClick={() =>
                      navigator.clipboard?.writeText(
                        result.hashtags.map((h) => (String(h).startsWith('#') ? h : `#${h}`)).join(' ')
                      )
                    }
                    className="mt-2 font-mono text-[10px] uppercase tracking-widest text-white/40 transition hover:text-white"
                  >
                    ⧉ Copy semua hashtag
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function CutModal({ jobId, duration, baseOptions, onClose, onRendered }) {
  const sourcePreviewUrl = useSourcePreviewUrl(jobId)
  // Free-form cut editor: pick MANY keep-ranges from the source timeline,
  // they get stitched into one clip (captions stay in sync).
  const max = duration && duration > 0 ? duration : 600
  const [ranges, setRanges] = useState([]) // [{start, end}, ...] ascending
  const [pending, setPending] = useState(null) // start of a range being marked
  const [opts, setOpts] = useState(() => normalizeOptions(baseOptions))
  const [wave, setWave] = useState(null)
  const [waveErr, setWaveErr] = useState('')
  const [state, setState] = useState('idle') // idle|rendering|error|done
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const videoRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    getWaveform(jobId)
      .then((d) => { if (!cancelled) setWave(d) })
      .catch((e) => { if (!cancelled) setWaveErr(e.message) })
    return () => { cancelled = true }
  }, [jobId])

  const total = ranges.reduce((a, r) => a + (r.end - r.start), 0)

  // Click timeline: first click marks start, second click closes the range.
  const mark = (t) => {
    if (state !== 'idle') return
    if (pending === null) {
      setPending(Math.max(0, Math.min(t, max)))
    } else {
      const a = Math.min(pending, t)
      const b = Math.max(pending, t)
      if (b - a >= 3) {
        setRanges((rs) =>
          [...rs, { start: Math.round(a), end: Math.round(b) }]
            .sort((x, y) => x.start - y.start),
        )
      }
      setPending(null)
    }
  }

  const removeRange = (i) => setRanges((rs) => rs.filter((_, j) => j !== i))

  const seekPreview = (t) => {
    if (videoRef.current && videoRef.current.readyState >= 1) {
      videoRef.current.currentTime = Math.max(0, Math.min(t, max))
    }
  }

  const apply = async () => {
    setState('rendering')
    setErr('')
    setResult(null)
    try {
      const { job_id } = await customCut(jobId, {
        ranges: ranges.map((r) => [r.start, r.end]),
        options: opts,
      })
      const data = await pollJob(job_id, { interval: 1200 })
      setResult(data.result)
      setState('done')
      onRendered?.({
        index: 999,
        title: 'Custom cut',
        video_url: data.result.video_url,
        size_mb: data.result.size_mb,
        duration: data.result.duration,
        rev: Date.now(),
      })
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  const peaks = wave?.peaks || []
  const barW = peaks.length ? 100 / peaks.length : 1

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="free-form cut editor"
      onClick={(e) => e.target === e.currentTarget && state !== 'rendering' && onClose()}
    >
      <div className="card-chrome max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-magenta/20 px-4 py-2.5">
          <Micro className="text-magenta-soft">SYS.CUT — FREE-FORM EDITOR</Micro>
          <button
            onClick={onClose}
            disabled={state === 'rendering'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="grid gap-4 p-4 sm:grid-cols-[220px_1fr]">
          {/* preview column */}
          <div>
            <FullscreenVideo
              videoRef={videoRef}
              wrapClassName="aspect-[9/16] w-full overflow-hidden rounded border border-magenta/30 bg-black"
              src={result ? withCacheBust(`${resolveAsset(result.video_url)}`) : withCacheBust(`${sourcePreviewUrl || resolveMediaAsset(`/videos/${jobId}/source.mp4`)}`)}
            />
            <div className="mt-2 space-y-1.5 font-mono text-[11px] text-white/45">
              <div className="flex justify-between"><span>KEEP RANGES</span><span>{ranges.length}</span></div>
              <div className="flex justify-between"><span>TOTAL LEN</span><span>{total.toFixed(0)}s</span></div>
              <div className="flex justify-between"><span>LIMIT</span><span>3:00</span></div>
            </div>
          </div>

          {/* timeline + ranges */}
          <div className="space-y-4">
            {/* click-to-mark timeline */}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <Micro>
                  {pending === null
                    ? 'Click timeline to mark a start point'
                    : `Marking range from ${fmtTime(pending)} — click again to finish`}
                </Micro>
                <button
                  type="button"
                  onClick={() => setPending(null)}
                  className="font-mono text-[10px] uppercase tracking-widest text-white/40 hover:text-white"
                >
                  cancel <Icon name="close" className="h-3 w-3" />
                </button>
              </div>
              <div
                className="relative h-28 touch-none select-none overflow-hidden rounded-md border border-border bg-navy-2"
                onPointerDown={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect()
                  const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width)
                  mark((x / rect.width) * max)
                }}
                role="slider"
                aria-label="cut timeline"
                aria-valuemin={0}
                aria-valuemax={Math.round(max)}
              >
                {/* amplitude bars */}
                <svg
                  viewBox={`0 0 ${peaks.length || 1} 100`}
                  preserveAspectRatio="none"
                  className="absolute inset-0 h-full w-full"
                  aria-hidden
                >
                  {peaks.map((p, i) => (
                    <rect
                      key={i}
                      x={i + 0.1}
                      y={(100 - Math.max(2, p * 100)) / 2}
                      width={0.8}
                      height={Math.max(2, p * 100)}
                      fill="rgba(124,140,248,0.35)"
                    />
                  ))}
                </svg>
                {/* selected ranges tint */}
                {ranges.map((r, i) => (
                  <div
                    key={i}
                    className="pointer-events-none absolute inset-y-0 z-10 border-x border-cyan/60 bg-cyan/15"
                    style={{
                      left: `${(r.start / max) * 100}%`,
                      width: `${((r.end - r.start) / max) * 100}%`,
                    }}
                  />
                ))}
                {/* pending marker */}
                {pending !== null && (
                  <div
                    className="pointer-events-none absolute inset-y-0 z-20 w-0.5 bg-magenta shadow-neon-cyan"
                    style={{ left: `${(pending / max) * 100}%` }}
                  />
                )}
              </div>
              <div className="mt-1 flex justify-between font-mono text-[9px] text-white/25">
                <span>0:00</span>
                <span>{fmtTime(max / 2)}</span>
                <span>{fmtTime(max)}</span>
              </div>
            </div>

            {/* selected ranges list */}
            <div className="space-y-1.5">
              {ranges.length === 0 ? (
                <p className="rounded-md border border-dashed border-border px-3 py-2 font-mono text-[11px] text-white/30">
                  no ranges yet — click the timeline to mark keep-sections
                </p>
              ) : (
                ranges.map((r, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 rounded-md border border-cyan/20 bg-cyan/5 px-2.5 py-1.5"
                  >
                    <span className="font-mono text-[10px] text-cyan-soft">{String(i + 1).padStart(2, '0')}</span>
                    <Mono className="text-white/70">
                      {fmtTime(r.start)} – {fmtTime(r.end)}
                    </Mono>
                    <Mono className="text-white/30">{(r.end - r.start).toFixed(0)}s</Mono>
                    <button
                      type="button"
                      onClick={() => { removeRange(i); setPending(null) }}
                      className="ml-auto p-1 text-white/40 transition hover:text-danger"
                      aria-label={`remove range ${i + 1}`}
                    >
                      <Icon name="close" className="h-3 w-3" />
                    </button>
                  </div>
                ))
              )}
              <div className="flex items-center gap-2 pt-1">
                <Mono className="text-white/30">
                  total {total.toFixed(0)}s / 180s
                </Mono>
                {ranges.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setRanges([])}
                    className="ml-auto font-mono text-[10px] uppercase tracking-widest text-white/40 hover:text-danger"
                  >
                    clear all
                  </button>
                )}
              </div>
            </div>

            <details className="rounded-md border border-border-soft bg-navy-2/40 p-3">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.18em] text-white/45 hover:text-white">
                Output settings (layout · aspect · captions)
              </summary>
              <div className="mt-3">
                <CaptionSettings options={opts} onChange={setOpts} editorMode />
              </div>
            </details>

            {state === 'error' && (
              <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
                {err}
              </p>
            )}

            {state === 'done' && result && (
              <div className="flex items-center gap-2">
                <a
                  href={dl(`${resolveAsset(result.video_url)}&t=${Date.now()}`)}
                  download="cut_clip.mp4"
                  className="flex-1 rounded-md bg-magenta px-3 py-2 text-center text-xs font-semibold text-navy transition hover:bg-magenta-soft"
                >
                  ⬇ Download cut ({result.size_mb} MB · {result.duration}s)
                </a>
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button
                onClick={apply}
                disabled={state === 'rendering' || ranges.length === 0 || total < 3 || total > 180}
                variant="magenta"
              >
                {state === 'rendering' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="trim" className="h-3.5 w-3.5" />} Stitch into one clip
              </Button>
              {state === 'rendering' && (
                <Mono className="text-white/40">stitching {ranges.length} range(s)…</Mono>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function TranscriptPanel({ segments, start, end, onUseStart, onUseEnd, onSeek }) {
  // Transcript-native trimming: click a segment's start/end to snap the trim
  // bounds to a spoken sentence boundary — no slider scrubbing needed.
  const list = segments || []
  if (list.length === 0) return null
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <Micro>Transcript trim — set start/end dari kalimat</Micro>
        <Mono className="text-white/30">{list.length} segmen</Mono>
      </div>
      <div className="max-h-48 space-y-1 overflow-y-auto pr-1">
        {list.map((s, i) => {
          const overlaps = s.end > start && s.start < end
          return (
            <div
              key={i}
              className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 transition-colors ${
                overlaps
                  ? 'border-cyan/40 bg-cyan/10'
                  : 'border-border-soft bg-navy-2/40 hover:border-white/20'
              }`}
            >
              <button
                type="button"
                onClick={() => onUseStart(s.start)}
                title={`Set start ke ${fmtTime(s.start)}`}
                className="shrink-0 rounded border border-cyan/40 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-cyan-soft transition hover:bg-cyan/15"
              >
                ▶ start
              </button>
              <button
                type="button"
                onClick={() => onSeek((s.start + s.end) / 2)}
                title={`Preview: ${fmtTime(s.start)} – ${fmtTime(s.end)}`}
                className="min-w-0 flex-1 truncate text-left text-xs text-white/70 transition hover:text-white"
              >
                <span className="font-mono text-[10px] text-white/35">{fmtTime(s.start)}</span>
                {' '}
                <span className="font-mono text-[10px] text-white/35">{fmtTime(s.end)}</span>
                {' · '}
                {s.text}
              </button>
              <button
                type="button"
                onClick={() => onUseEnd(s.end)}
                title={`Set end ke ${fmtTime(s.end)}`}
                className="shrink-0 rounded border border-cyan/40 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-cyan-soft transition hover:bg-cyan/15"
              >
                end ▶
              </button>
            </div>
          )
        })}
      </div>
      <p className="mt-1.5 font-mono text-[10px] text-white/30">
        klik teks = preview · ▶ start / end ▶ = snap trim ke batas kalimat
      </p>
    </div>
  )
}

const LAYOUT_SCENE_OPTIONS = [
  { v: 'track', label: 'Talking head (follow)' },
  { v: 'general', label: 'Group / full-width' },
  { v: 'split', label: 'Dua speaker (stack)' },
  { v: 'panel', label: 'Dua speaker (side-by-side)' },
  { v: 'center', label: 'Center (tanpa follow)' },
  { v: 'gameplay', label: 'Gameplay / screencast' },
]

function TrimModal({ clip, jobId, duration, baseOptions, onClose, onRendered, onRepick }) {
  const sourcePreviewUrl = useSourcePreviewUrl(jobId)
  const max = duration && duration > 0 ? duration : clip.end + 30
  const [start, setStart] = useState(clip.start)
  const [end, setEnd] = useState(clip.end)
  const [opts, setOpts] = useState(normalizeOptions(baseOptions))
  const [state, setState] = useState('idle') // idle|rendering|error
  const [err, setErr] = useState('')
  const [repickPrompt, setRepickPrompt] = useState(baseOptions?.custom_prompt || '')
  const [repickState, setRepickState] = useState('idle') // idle|running|error
  const [repickErr, setRepickErr] = useState('')
  const [repickLogs, setRepickLogs] = useState([])
  const [favorites, setFavorites] = useState(loadFavorites)
  const [wave, setWave] = useState(null) // {peaks, duration, segments}
  const [waveErr, setWaveErr] = useState('')
  // Manual subject lock: [x, y] normalized on the SOURCE frame; click the face
  // the tracker must follow. null = follow automatically (largest/active
  // speaker). While armed, the preview swaps to the SOURCE video so the click
  // point matches the coordinates the tracker uses on the source frame.
  const [lock, setLock] = useState(
    () => (Array.isArray(clip.subject_lock) && clip.subject_lock.length === 2
      ? clip.subject_lock : null)
  )
  const [lockArmed, setLockArmed] = useState(false) // overlay armed for click
  const videoRef = useRef(null)
  const previewWrapRef = useRef(null)
  // Per-scene layout editor: editable copy of clip.layout_plan (clip-local s).
  const [scenes, setScenes] = useState(() => {
    const plan = Array.isArray(clip.layout_plan) ? clip.layout_plan : []
    return plan.length
      ? plan.map((s) => ({ start: Number(s.start) || 0, end: Number(s.end) || 0,
                           layout: s.layout || s.strategy || 'track' }))
      : []
  })
  const [planDirty, setPlanDirty] = useState(false)
  const [planMsg, setPlanMsg] = useState('')
  // A/B hook generator: 3 scored variants stored on the clip.
  const [hookVariants, setHookVariants] = useState(
    () => Array.isArray(clip.hook_variants) ? clip.hook_variants : [])
  const [activeHook, setActiveHook] = useState(() => clip.hook || '')
  const [hookState, setHookState] = useState('idle') // idle|generating|applying
  const [hookMsg, setHookMsg] = useState('')

  // Sync local editor state when a different clip is opened (avoid stale plan/lock/hooks).
  useEffect(() => {
    setStart(clip.start)
    setEnd(clip.end)
    setLock(Array.isArray(clip.subject_lock) && clip.subject_lock.length === 2 ? clip.subject_lock : null)
    setLockArmed(false)
    const plan = Array.isArray(clip.layout_plan) ? clip.layout_plan : []
    setScenes(plan.length ? plan.map((s) => ({ start: Number(s.start) || 0, end: Number(s.end) || 0, layout: s.layout || s.strategy || 'track' })) : [])
    setPlanDirty(false)
    setPlanMsg('')
    setHookVariants(Array.isArray(clip.hook_variants) ? clip.hook_variants : [])
    setActiveHook(clip.hook || '')
    setHookMsg('')
  }, [clip.index])

  useEffect(() => {
    let cancelled = false
    getWaveform(jobId)
      .then((d) => { if (!cancelled) setWave(d) })
      .catch((e) => { if (!cancelled) setWaveErr(e.message) })
    return () => { cancelled = true }
  }, [jobId])

  // Pull account-synced favorites (also catches post-login events).
  useEffect(() => {
    let cancelled = false
    syncFavorites().then((list) => { if (!cancelled) setFavorites(list) })
    const onAuth = () => syncFavorites().then((list) => setFavorites(list))
    window.addEventListener('klipin:authenticated', onAuth)
    return () => {
      cancelled = true
      window.removeEventListener('klipin:authenticated', onAuth)
    }
  }, [])

  const clampStart = (v) => Math.max(0, Math.min(v, max - 5))
  const clampEnd = (v) => Math.max(start + 5, Math.min(v, start + 180, max))

  const changeBounds = (s, e) => { setStart(s); setEnd(e) }

  const seekPreview = (t) => {
    if (videoRef.current && videoRef.current.readyState >= 1) {
      videoRef.current.currentTime = Math.max(0, t - 0.05)
    }
  }

  // Snap trim bounds to a transcript segment boundary (transcript-native).
  const useStart = (t) => {
    const s = clampStart(t)
    setStart(s)
    setEnd((ev) => Math.max(ev, s + 5))
    seekPreview(s)
  }
  const useEnd = (t) => {
    const e = clampEnd(t)
    setEnd(e)
    seekPreview(e)
  }

  const apply = async () => {
    setState('rendering')
    setErr('')
    try {
      const { job_id } = await retrimClip(jobId, clip.index, {
        start,
        end,
        options: opts,
        subject_lock: lock ? [lock[0], lock[1]] : [],
      })
      const data = await pollJob(job_id, { interval: 1200 })
      onRendered({ ...data.result.clip, index: clip.index })
    } catch (e) {
      setState('error')
      setErr(e.message)
    }
  }

  // Manual subject lock: arm the overlay (preview swaps to the SOURCE video,
  // seeked to the clip start), then a click stores the normalized point of
  // the face that must be followed on the source frame.
  useEffect(() => {
    if (lockArmed && videoRef.current) {
      try { videoRef.current.currentTime = clip.start } catch { /* not ready */ }
    }
  }, [lockArmed, clip.start])
  const handleLockClick = (e) => {
    const el = previewWrapRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))
    setLock([Number(x.toFixed(4)), Number(y.toFixed(4))])
  }

  // ── Per-scene layout editor ─────────────────────────────────────────────
  // Times are clip-local seconds of the ORIGINAL clip render. On save they
  // are shifted into the current trim window (start may have moved).
  const splitSceneAtPlayhead = () => {
    const t = videoRef.current?.currentTime || 0
    setScenes((list) => {
      const sorted = [...(list.length ? list
        : [{ start: 0, end: Math.max(1, clip.end - clip.start), layout: 'track' }])]
        .sort((a, b) => a.start - b.start)
      const i = sorted.findIndex((s) => t > s.start + 0.5 && t < s.end - 0.5)
      if (i < 0) return sorted
      const s = sorted[i]
      return [...sorted.slice(0, i),
        { ...s, end: Number(t.toFixed(2)) },
        { ...s, start: Number(t.toFixed(2)) },
        ...sorted.slice(i + 1)]
    })
    setPlanDirty(true)
    setPlanMsg('')
  }
  const savePlan = async () => {
    setPlanMsg('')
    // Validate before PUT: end>start, no overlap, inside current trim window.
    const dur = Math.max(1, end - start)
    const sorted = [...scenes].sort((a, b) => a.start - b.start)
    for (let i = 0; i < sorted.length; i++) {
      const s = sorted[i]
      if (!(s.end > s.start)) { setPlanMsg(`⚠️ Scene ${i + 1}: end harus > start`); return }
      if (s.start < 0 || s.end > dur + 0.5) { setPlanMsg(`⚠️ Scene ${i + 1} di luar durasi trim (0–${dur.toFixed(0)}s)`); return }
      if (i > 0 && s.start < sorted[i - 1].end - 0.01) { setPlanMsg(`⚠️ Scene ${i + 1} overlap dengan scene ${i}`); return }
    }
    try {
      const shift = start - clip.start
      const payload = [...scenes]
        .sort((a, b) => a.start - b.start)
        .map((s) => ({
          start: Math.max(0, s.start - shift),
          end: Math.max(0, s.end - shift),
          layout: s.layout,
        }))
      const res = await putClipLayoutPlan(jobId, clip.index, payload)
      if (res.layout_plan?.length) {
        setScenes(res.layout_plan.map((s) => ({
          start: Number(s.start) || 0, end: Number(s.end) || 0,
          layout: s.layout || s.strategy || 'track',
        })))
      }
      setPlanDirty(false)
      setPlanMsg('✅ Layout disimpan — tekan "Re-render clip" untuk menerapkan')
    } catch (e) {
      setPlanMsg(`⚠️ ${e.message}`)
    }
  }
  const resetPlan = async () => {
    setPlanMsg('')
    try {
      const res = await putClipLayoutPlan(jobId, clip.index, [])
      const plan = Array.isArray(res.layout_plan) ? res.layout_plan : []
      setScenes(plan.length ? plan.map((s) => ({
        start: Number(s.start) || 0, end: Number(s.end) || 0,
        layout: s.layout || s.strategy || 'track',
      })) : [])
      setPlanDirty(false)
      setPlanMsg('↩︎ Kembali ke layout AI')
    } catch (e) {
      setPlanMsg(`⚠️ ${e.message}`)
    }
  }

  // ── A/B hook generator ──────────────────────────────────────────────────
  const genHooks = async () => {
    setHookState('generating')
    setHookMsg('')
    try {
      const res = await generateHookVariants(jobId, clip.index)
      setHookVariants(res.variants || [])
      setHookMsg('')
    } catch (e) {
      setHookMsg(`⚠️ ${e.message}`)
    } finally {
      setHookState('idle')
    }
  }
  const useHook = async (text) => {
    setHookState('applying')
    setHookMsg('')
    try {
      await selectHookVariant(jobId, clip.index, text)
      setActiveHook(text)
      setHookVariants((list) => list.map((v) => ({ ...v, active: v.text === text })))
      setHookMsg('✅ Hook aktif — tekan "Re-render clip" untuk membakar overlay')
    } catch (e) {
      setHookMsg(`⚠️ ${e.message}`)
    } finally {
      setHookState('idle')
    }
  }

  // Re-pick momen klip: re-run AI selection with new criteria, replacing the
  // whole clip list (source + transcript are reused server-side, no re-transcribe).
  const runRepick = async () => {
    if (!repickPrompt.trim()) return
    setRepickState('running')
    setRepickErr('')
    setRepickLogs([])
    try {
      const { job_id } = await repickProject(jobId, { custom_prompt: repickPrompt.trim() })
      const data = await pollJob(job_id, {
        interval: 1500,
        onStatus: (d) => setRepickLogs((d.logs || []).slice(-2)),
      })
      onRepick?.(data.result)
    } catch (e) {
      setRepickState('error')
      setRepickErr(e.message)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="trim clip"
      onClick={(e) => e.target === e.currentTarget && state !== 'rendering' && onClose()}
    >
      <div className="card-chrome max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl">
        <header className="flex items-center justify-between border-b border-border-soft px-4 py-2.5">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={state === 'rendering'}
              className="inline-flex items-center gap-1 font-mono text-xs text-white/40 transition hover:text-white disabled:opacity-40"
              aria-label="Kembali ke hasil"
              title="Kembali ke hasil tanpa me-render ulang"
            >
              ← Kembali
            </button>
            <Micro className="text-white/60">SYS.TRIM — CLIP {String(clip.index + 1).padStart(2, '0')}</Micro>
          </div>
          <button
            onClick={onClose}
            disabled={state === 'rendering'}
            className="font-mono text-xs text-white/40 hover:text-white disabled:opacity-40"
            aria-label="close"
          >
            ESC <Icon name="close" className="h-3 w-3" />
          </button>
        </header>

        <div className="grid gap-4 p-4 sm:grid-cols-[220px_1fr]">
          <div>
            <div ref={previewWrapRef} className="relative">
              <FullscreenVideo
                videoRef={videoRef}
                wrapClassName="aspect-[9/16] w-full overflow-hidden rounded border border-border bg-black"
                src={lockArmed
                  ? (sourcePreviewUrl || resolveMediaAsset(`/videos/${jobId}/source.mp4`))
                  : `${resolveAsset(clip.video_url)}&v=${clip.rev || 0}`}
              />
              {lockArmed && lock && (
                <span
                  className="pointer-events-none absolute z-10 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-signal bg-signal/25 shadow-neon-cyan"
                  style={{ left: `${lock[0] * 100}%`, top: `${lock[1] * 100}%` }}
                  title="Subject lock aktif"
                />
              )}
              {/* Safe-zone overlay: hook (top), subtitle (bottom) — lock buta tanpa ini */}
              <div className="pointer-events-none absolute inset-x-[7%] top-[7%] z-10 h-[12%] rounded border border-dashed border-amber-300/70" title="Safe zone hook — jangan taruh wajah di sini" />
              <div className="pointer-events-none absolute inset-x-[7%] bottom-[14%] z-10 h-[18%] rounded border border-dashed border-cyan-300/70" title="Safe zone subtitle — jangan taruh wajah di sini" />
              {lockArmed && (
                <button
                  type="button"
                  onClick={handleLockClick}
                  aria-label="pilih wajah untuk subject lock"
                  className="absolute inset-0 z-20 h-full w-full cursor-crosshair rounded border-2 border-dashed border-cyan/70 bg-cyan/5"
                  title="Klik wajah yang harus diikuti"
                />
              )}
            </div>
            {lockArmed && (
              <p className="mt-1 font-mono text-[9px] text-cyan-soft">
                Preview SOURCE — klik wajah di frame asli agar koordinat cocok dengan tracker.
              </p>
            )}
            <div className="mt-2 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  const v = videoRef.current
                  if (!v) return
                  if (v.paused) v.play().catch(() => {})
                  else v.pause()
                }}
                className="rounded border border-border bg-navy-2 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white/45 transition hover:border-cyan/50 hover:text-white/75"
                title="Putar/jeda preview untuk cari frame wajah"
              >
                ▶ Putar
              </button>
              <button
                type="button"
                onClick={() => setLockArmed((v) => !v)}
                aria-pressed={lockArmed}
                className={`rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition ${
                  lockArmed
                    ? 'border-cyan bg-cyan/15 text-cyan'
                    : 'border-border bg-navy-2 text-white/45 hover:border-cyan/50 hover:text-white/75'
                }`}
                title="Kunci wajah yang harus diikuti tracker"
              >
                {lockArmed ? '✓ Selesai' : '⌖ Lock wajah'}
              </button>
              {lock && (
                <button
                  type="button"
                  onClick={() => { setLock(null); setLockArmed(false) }}
                  className="rounded border border-border bg-navy-2 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white/45 transition hover:border-danger/50 hover:text-danger"
                  title="Hapus subject lock"
                >
                  ✕ Hapus lock
                </button>
              )}
            </div>
            {lock && (
              <p className="mt-1 font-mono text-[9px] text-signal/80">
                🔒 Tracker mengikuti wajah terdekat titik ini setelah re-render.
              </p>
            )}
            <div className="mt-2 space-y-2 font-mono text-[11px] text-white/45">
              <div className="flex justify-between"><span>START</span><span>{fmtTime(start)}</span></div>
              <div className="flex justify-between"><span>END</span><span>{fmtTime(end)}</span></div>
              <div className="flex justify-between"><span>LEN</span><span>{(end - start).toFixed(0)}s</span></div>
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <div className="mb-1 flex justify-between">
                <Micro>Start</Micro>
                <Mono>{fmtTime(start)}</Mono>
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(1, max - 5)}
                step={0.5}
                value={start}
                onChange={(e) => { const v = Number(e.target.value); setStart(v); setEnd((ev) => Math.max(ev, v + 5)); seekPreview(v) }}
                className="w-full accent-cyan"
                aria-label="clip start"
              />
              <div className="mb-1 mt-4 flex justify-between">
                <Micro>End</Micro>
                <Mono>{fmtTime(end)}</Mono>
              </div>
              <input
                type="range"
                min={Math.min(start + 5, max)}
                max={max}
                step={0.5}
                value={end}
                onChange={(e) => setEnd(Number(e.target.value))}
                className="w-full accent-cyan"
                aria-label="clip end"
              />
              <div className="mt-1 flex justify-between font-mono text-[10px] text-white/25">
                <span>0:00</span><span>{fmtTime(max)}</span>
              </div>
              <div className="mt-3 flex gap-2 font-mono text-[11px] text-white/35">
                <span className="rounded border border-border bg-navy-2 px-2 py-1">MIN 5s</span>
                <span className="rounded border border-border bg-navy-2 px-2 py-1">MAX 180s</span>
              </div>
            </div>

            <details className="rounded-md border border-border-soft bg-navy-2/40 p-3" open>
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.18em] text-white/45 hover:text-white">
                Layout per scene {clip.layout_plan_edited ? '· EDITED' : ''} {planDirty ? '· BELUM DISIMPAN' : ''}
              </summary>
              <div className="mt-3 space-y-2">
                <p className="text-[11px] leading-relaxed text-white/40">
                  Ubah layout tiap scene sebelum re-render. Putar preview lalu
                  tekan <span className="text-cyan-soft">+ Scene di playhead</span> untuk
                  memecah scene di posisi saat ini.
                </p>
                {scenes.length === 0 && (
                  <p className="font-mono text-[10px] text-white/35">
                    Belum ada layout plan AI — satu scene penuh akan dipakai.
                  </p>
                )}
                {scenes.map((s, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="w-24 shrink-0 font-mono text-[10px] text-white/40">
                      {fmtTime(s.start)}–{fmtTime(s.end)}
                    </span>
                    <select
                      value={s.layout}
                      onChange={(e) => {
                        const v = e.target.value
                        setScenes((list) => list.map((x, j) => j === i ? { ...x, layout: v } : x))
                        setPlanDirty(true)
                        setPlanMsg('')
                      }}
                      aria-label={`layout scene ${i + 1}`}
                      className="min-w-0 flex-1 rounded border border-border bg-navy-2 px-2 py-1 font-mono text-[11px] text-white outline-none focus:border-cyan"
                    >
                      {LAYOUT_SCENE_OPTIONS.map((o) => (
                        <option key={o.v} value={o.v}>{o.label}</option>
                      ))}
                    </select>
                    {scenes.length > 1 && (
                      <button
                        type="button"
                        onClick={() => {
                          // Gap-fill: previous scene absorbs the removed range.
                          setScenes((list) => {
                            const removed = list[i]
                            const rest = list.filter((_, j) => j !== i)
                            if (removed && i > 0 && rest[i - 1]) {
                              rest[i - 1] = { ...rest[i - 1], end: Math.max(rest[i - 1].end, removed.end) }
                            }
                            return rest
                          })
                          setPlanDirty(true)
                          setPlanMsg('')
                        }}
                        aria-label={`hapus scene ${i + 1}`}
                        className="rounded border border-border bg-navy-2 px-1.5 py-1 font-mono text-[10px] text-white/40 transition hover:border-danger/50 hover:text-danger"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                ))}
                <div className="flex flex-wrap gap-2 pt-1">
                  <button
                    type="button"
                    onClick={splitSceneAtPlayhead}
                    className="rounded border border-cyan/40 bg-cyan/5 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-cyan-soft transition hover:border-cyan hover:text-cyan"
                  >
                    + Scene di playhead
                  </button>
                  <button
                    type="button"
                    onClick={savePlan}
                    disabled={!planDirty}
                    className="rounded border border-signal/40 bg-signal/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-signal transition hover:border-signal disabled:opacity-40"
                  >
                    💾 Simpan layout
                  </button>
                  {clip.layout_plan_edited && (
                    <button
                      type="button"
                      onClick={resetPlan}
                      className="rounded border border-border bg-navy-2 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white/45 transition hover:border-border-soft hover:text-white/75"
                    >
                      ↩︎ Reset ke AI
                    </button>
                  )}
                </div>
                {planMsg && (
                  <p className="font-mono text-[10px] text-white/60">{planMsg}</p>
                )}
              </div>
            </details>

            <details className="rounded-md border border-border-soft bg-navy-2/40 p-3">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.18em] text-white/45 hover:text-white">
                A/B Hook generator {hookVariants.length ? `· ${hookVariants.length} variasi` : ''}
              </summary>
              <div className="mt-3 space-y-2">
                <p className="text-[11px] leading-relaxed text-white/40">
                  3 variasi hook dengan gaya psikologis berbeda + skor prediksi
                  retensi. Pilih yang terbaik, lalu re-render untuk membakarnya.
                  Hook aktif saat ini: <span className="text-white/70">“{(activeHook || '').slice(0, 80)}”</span>
                </p>
                <button
                  type="button"
                  onClick={genHooks}
                  disabled={hookState !== 'idle'}
                  className="rounded border border-magenta/40 bg-magenta/5 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-signal transition hover:border-magenta disabled:opacity-40"
                >
                  {hookState === 'generating'
                    ? '⏳ Generating…'
                    : `🅰️ ${hookVariants.length ? 'Generate ulang 3 variasi' : 'Generate 3 variasi hook'}`}
                </button>
                {hookVariants.map((v, i) => (
                  <div key={i} className="rounded border border-border bg-navy-2/60 p-2">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-cyan-soft">
                        {v.style}
                      </span>
                      <span className="font-mono text-[10px] text-signal">
                        {v.predicted_score}/100
                      </span>
                    </div>
                    <div className="mb-1 h-1 w-full overflow-hidden rounded bg-white/10">
                      <div
                        className="h-full rounded bg-gradient-to-r from-cyan to-signal"
                        style={{ width: `${Math.max(4, Math.min(100, v.predicted_score))}%` }}
                      />
                    </div>
                    <p className="text-[12px] font-bold leading-snug text-white/85">“{v.text}”</p>
                    {v.why && (
                      <p className="mt-0.5 text-[10px] leading-relaxed text-white/40">{v.why}</p>
                    )}
                    <button
                      type="button"
                      onClick={() => useHook(v.text)}
                      disabled={hookState !== 'idle'}
                      className="mt-1.5 rounded border border-cyan/40 bg-cyan/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-cyan transition hover:border-cyan disabled:opacity-40"
                    >
                      {hookState === 'applying' ? '⏳ …' : '✓ Pakai hook ini'}
                    </button>
                  </div>
                ))}
                {hookMsg && (
                  <p className="font-mono text-[10px] text-white/60">{hookMsg}</p>
                )}
              </div>
            </details>

            <details className="rounded-md border border-border-soft bg-navy-2/40 p-3">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.18em] text-white/45 hover:text-white">
                Caption overrides
              </summary>
              <div className="mt-3">
                <CaptionSettings options={opts} onChange={setOpts} editorMode />
              </div>
            </details>

            <div className="rounded-md border border-magenta/25 bg-navy-2/40 p-3">
              <div className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-white/45">
                Custom AI criteria — re-pick momen
              </div>
              <p className="mb-2 text-[11px] leading-relaxed text-white/40">
                Pilih preset atau tulis kriteria sendiri, lalu jalankan ulang seleksi AI —
                seluruh daftar klip diganti sesuai kriteria baru (tanpa transcribe ulang).
              </p>
              <div className="flex flex-wrap gap-1.5">
                {CRITERIA_PRESETS.map((p) => {
                  const active = repickPrompt.trim() === p.prompt
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setRepickPrompt(active ? '' : p.prompt)}
                      aria-pressed={active}
                      title={p.prompt}
                      className={`rounded-full border px-2.5 py-1 font-mono text-[10px] transition ${
                        active
                          ? 'border-magenta bg-magenta/15 text-signal'
                          : 'border-border bg-navy-2/60 text-white/45 hover:border-magenta/40 hover:text-white/75'
                      }`}
                    >
                      {active ? <Icon name="check" className="h-3 w-3" /> : ''}{p.label}
                    </button>
                  )
                })}
              </div>
              <textarea
                value={repickPrompt}
                onChange={(e) => setRepickPrompt(e.target.value)}
                placeholder="cth: fokus pada momen product demo dan cara pakainya…"
                aria-label="re-pick criteria"
                rows={2}
                className="mt-2 w-full resize-y rounded-md border border-magenta/25 bg-navy-2 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/25 focus:border-magenta"
              />
              {(repickPrompt.trim() && !favorites.some((f) => f.prompt === repickPrompt.trim())) && (
                <button
                  type="button"
                  onClick={() => setFavorites(saveFavorite(repickPrompt))}
                  className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-magenta/30 bg-magenta/5 px-2.5 py-1 font-mono text-[10px] text-signal transition hover:border-magenta/70"
                  title="Simpan kriteria ini sebagai favorit"
                >
                  <Icon name="star" className="h-3.5 w-3.5" /> Simpan sebagai favorit
                </button>
              )}
              {favorites.length > 0 && (
                <div className="mt-2.5 border-t border-border-soft pt-2">
                  <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-white/35">
                    Favorit ({favorites.length}) — klik label untuk isi, <Icon name="folder" className="h-2.5 w-2.5 inline" /> folder, <Icon name="edit" className="h-2.5 w-2.5 inline" /> rename, <Icon name="close" className="h-2.5 w-2.5 inline" /> hapus
                  </div>
                  <FavoriteChips
                    favorites={favorites}
                    value={repickPrompt}
                    onUse={setRepickPrompt}
                    onChange={setFavorites}
                    accent="magenta"
                  />
                </div>
              )}
              {repickState === 'running' && (
                <div className="mt-2 flex items-center gap-2 rounded-md border border-magenta/25 bg-magenta/5 px-3 py-2 font-mono text-xs text-signal">
                  <Spinner className="h-3.5 w-3.5" />
                  {repickLogs[repickLogs.length - 1] || 'Re-picking momen…'}
                </div>
              )}
              {repickState === 'error' && (
                <p className="mt-2 rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
                  {repickErr}
                </p>
              )}
              <Button
                variant="magenta"
                onClick={runRepick}
                disabled={repickState === 'running' || !repickPrompt.trim()}
                className="mt-2 w-full"
              >
                {repickState === 'running' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="repurpose" className="h-3.5 w-3.5" />} Re-pick momen klip
              </Button>
            </div>

            {state === 'error' && (
              <p className="rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">
                {err}
              </p>
            )}

            <div className="flex items-center gap-2">
              <Button onClick={apply} disabled={state === 'rendering' || end - start < 5}>
                {state === 'rendering' ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="refresh" className="h-3.5 w-3.5" />} Re-render clip
              </Button>
              {state === 'rendering' && (
                <Mono className="text-white/40">rendering {fmtTime(start)}–{fmtTime(end)}…</Mono>
              )}
            </div>
          </div>
        </div>          {/* transcript-native trim — set start/end dari kalimat */}
        <div className="border-t border-border-soft px-4 pb-4 pt-3">
          <TranscriptPanel
            segments={wave?.segments || []}
            start={start}
            end={end}
            onUseStart={useStart}
            onUseEnd={useEnd}
            onSeek={seekPreview}
          />
        </div>

        {/* waveform timeline — full-width for precise cut points */}
        <div className="border-t border-border-soft px-4 pb-4 pt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <Micro>Source waveform</Micro>
            <Mono className="text-white/30">
              {wave
                ? `${wave.peaks?.length || 0} samples · ${fmtTime(wave.duration || 0)}`
                : waveErr ? 'unavailable' : 'decoding…'}
            </Mono>
          </div>
          {wave ? (
            <Waveform
              peaks={wave.peaks || []}
              duration={wave.duration || max}
              start={start}
              end={end}
              segments={wave.segments || []}
              onSeek={seekPreview}
              onChange={changeBounds}
            />
          ) : (
            <div className="flex h-28 items-center justify-center gap-2 rounded-md border border-border bg-navy-2 font-mono text-[10px] uppercase tracking-[0.18em] text-white/30">
              {waveErr ? (
                `waveform error: ${waveErr}`
              ) : (
                <><Spinner className="h-3.5 w-3.5" /> decoding audio…</>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
