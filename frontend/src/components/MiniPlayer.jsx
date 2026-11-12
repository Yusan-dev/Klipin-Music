import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { resolveDownloadAsset } from '../api.js'
import { usePlayer } from '../playerStore.jsx'
import Icon from './Icon.jsx'
import MusicWaveform from './MusicWaveform.jsx'

function fmt(value) {
  const s = Math.max(0, Number(value) || 0)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

function defaultPosition(elWidth = 390, elHeight = 520) {
  if (typeof window === 'undefined') return { x: 16, y: 80 }
  const vw = window.innerWidth
  const vh = window.innerHeight
  if (vw < 768) {
    return {
      x: Math.max(8, Math.round((vw - Math.min(elWidth, vw - 16)) / 2)),
      y: Math.max(64, Math.round(vh - Math.min(elHeight, vh - 120) - 72)),
    }
  }
  return {
    x: Math.max(12, vw - elWidth - 24),
    y: Math.max(64, vh - Math.min(elHeight, vh - 104) - 76),
  }
}

/**
 * Sticky bottom transport bar (desktop) + draggable floating non-modal pop-up
 * player (`<dialog popover="manual">`) for both desktop and mobile.
 *
 * Keeps the underlying workspace completely interactive while users drag the
 * pop-up anywhere across the screen or collapse it back to the compact bar.
 */
export default function MiniPlayer({ sidebarCollapsed = false, onNavigate }) {
  const {
    audioRef,
    track,
    playing,
    currentTime,
    duration,
    sheetOpen,
    setSheetOpen,
    trackActions,
    togglePlay,
    seek,
    closePlayer,
  } = usePlayer()

  const dialogRef = useRef(null)
  const activeLineRef = useRef(null)
  const dragStateRef = useRef(null)

  const [pos, setPos] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [showLyrics, setShowLyrics] = useState(true)

  const clampPos = useCallback((rawX, rawY) => {
    const dlg = dialogRef.current
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
    const vh = typeof window !== 'undefined' ? window.innerHeight : 768
    const w = dlg?.offsetWidth || Math.min(390, vw - 16)
    const maxX = Math.max(8, vw - w - 8)
    const maxY = Math.max(8, vh - 72)
    return {
      x: Math.max(8, Math.min(maxX, Math.round(rawX))),
      y: Math.max(8, Math.min(maxY, Math.round(rawY))),
    }
  }, [])

  // Show/hide top-layer non-modal dialog via native Popover API (with show() fallback).
  useEffect(() => {
    const dlg = dialogRef.current
    if (!dlg) return
    const supportsPopover = typeof dlg.showPopover === 'function'
    const isOpen = supportsPopover ? dlg.matches(':popover-open') : dlg.open

    if (sheetOpen && track?.audioUrl) {
      if (!pos) {
        setPos(defaultPosition(Math.min(390, (typeof window !== 'undefined' ? window.innerWidth : 1024) - 16)))
      }
      if (!isOpen) {
        try {
          if (supportsPopover) dlg.showPopover()
          else dlg.show()
        } catch {
          /* ignore already-open errors */
        }
      }
    } else if (isOpen) {
      try {
        if (supportsPopover) dlg.hidePopover()
        else dlg.close()
      } catch {
        /* ignore already-closed errors */
      }
    }
  }, [sheetOpen, track?.audioUrl, pos])

  // Re-clamp floating window cleanly if viewport resizes.
  useEffect(() => {
    if (!sheetOpen || !pos) return undefined
    const onResize = () => setPos((prev) => (prev ? clampPos(prev.x, prev.y) : prev))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [sheetOpen, pos, clampPos])

  const onDragStart = (e) => {
    if (e.button !== undefined && e.button !== 0) return
    if (e.target.closest('button, a, input, select')) return
    const dlg = dialogRef.current
    if (!dlg) return
    const rect = dlg.getBoundingClientRect()
    dragStateRef.current = {
      pointerId: e.pointerId,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    }
    setDragging(true)
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      /* ignore capture failure */
    }
  }

  const onDragMove = (e) => {
    const st = dragStateRef.current
    if (!st || st.pointerId !== e.pointerId) return
    setPos(clampPos(e.clientX - st.offsetX, e.clientY - st.offsetY))
  }

  const onDragEnd = (e) => {
    const st = dragStateRef.current
    if (!st || st.pointerId !== e.pointerId) return
    dragStateRef.current = null
    setDragging(false)
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
  }

  const lyricsLines = track?.lyricsLines || []
  const total = Math.max(1, duration || track?.duration || 1)
  const pct = Math.max(0, Math.min(100, (currentTime / total) * 100))

  const activeIndex = useMemo(() => {
    if (!lyricsLines.length) return -1
    let idx = -1
    for (let i = 0; i < lyricsLines.length; i += 1) {
      if (currentTime >= (Number(lyricsLines[i].start) || 0)) idx = i
    }
    return idx
  }, [lyricsLines, currentTime])

  useEffect(() => {
    if (sheetOpen && showLyrics && activeLineRef.current) {
      activeLineRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [activeIndex, sheetOpen, showLyrics])

  if (!track?.audioUrl) return null

  const actions = trackActions && trackActions.jobId === track.jobId ? trackActions : null
  const openStudioEq = () => {
    setSheetOpen(false)
    const path = track.jobId ? `#/studio/${track.jobId}` : '#/studio'
    if (onNavigate) onNavigate(path)
    else window.location.hash = path
  }
  const openInStudio = () => {
    if (onNavigate) onNavigate('#/music')
    else window.location.hash = '#/music'
  }

  const resolvedPos = pos || defaultPosition(390)

  return (
    <>
      {/* Desktop persistent compact bottom dock (single row, opens draggable pop-up on click) */}
      <aside
        aria-label="Pemutar audio aktif"
        className={`fixed bottom-0 right-0 z-30 hidden border-t border-border bg-navy-1/95 backdrop-blur-xl transition-[left] lg:block ${
          sidebarCollapsed ? 'lg:left-16' : 'lg:left-60'
        }`}
      >
        <div className="h-[2px] w-full bg-border-soft">
          <div
            className="h-full bg-gradient-to-r from-cyan via-gold to-magenta transition-[width] duration-150"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="mx-auto flex h-[56px] max-w-[1680px] items-center justify-between gap-4 px-5">
          <button
            type="button"
            onClick={() => setSheetOpen((o) => !o)}
            className="flex min-w-0 max-w-[270px] items-center gap-3 rounded-lg px-1.5 py-1 text-left transition hover:bg-white/5"
            title={sheetOpen ? 'Sembunyikan pop-up pemutar' : 'Buka pop-up pemutar yang bisa digeser'}
          >
            <div className="relative h-9 w-9 shrink-0 overflow-hidden rounded-lg border border-border bg-navy-2">
              {track.coverUrl ? (
                <img src={track.coverUrl} alt="" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-white/45">
                  <Icon name="music" className="h-4 w-4" />
                </span>
              )}
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-white">
                {track.title || 'Klipin Song'}
              </div>
              <div className="truncate font-mono text-[10px] text-white/60">
                {track.genreLabel || 'Klipin Music'} · klipin-music
              </div>
            </div>
          </button>

          <div className="flex min-w-[260px] max-w-xl flex-1 items-center gap-3">
            <button
              type="button"
              onClick={togglePlay}
              aria-label={playing ? 'Jeda lagu' : 'Putar lagu'}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-cyan to-magenta text-white-solid shadow-sm transition hover:brightness-110 active:scale-95"
            >
              <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
            </button>
            <span className="w-10 text-right font-mono text-[11px] tabular-nums text-white/60">
              {fmt(currentTime)}
            </span>
            <input
              type="range"
              min={0}
              max={total}
              step={0.1}
              value={Math.min(currentTime, total)}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label="Posisi lagu"
              className="flex-1 cursor-pointer accent-cyan"
            />
            <span className="w-10 font-mono text-[11px] tabular-nums text-white/60">
              {fmt(total)}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setSheetOpen((o) => !o)}
              className={`rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition ${
                sheetOpen
                  ? 'border-cyan bg-cyan/15 text-cyan'
                  : 'border-border bg-navy-2 text-white/80 hover:border-cyan/50 hover:text-white'
              }`}
              title="Buka atau tutup jendela pop-up pemutar"
            >
              {sheetOpen ? 'Tutup Pop-up' : 'Pop-up Player'}
            </button>
            {track.jobId && (
              <button
                type="button"
                onClick={openStudioEq}
                className="rounded-lg border border-signal/40 bg-signal/10 px-2.5 py-1.5 text-[11px] font-semibold text-signal transition hover:bg-signal/20"
              >
                🎚️ EQ
              </button>
            )}
            {track.audioPath && (
              <a
                href={resolveDownloadAsset(track.audioPath, `${track.title || 'klipin-music'}.mp3`)}
                download={`${track.title || 'klipin-music'}.mp3`}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-[11px] font-medium text-white/80 transition hover:border-cyan/50 hover:text-white"
              >
                ⬇ MP3
              </a>
            )}
            <button
              type="button"
              onClick={closePlayer}
              title="Hentikan dan tutup pemutar"
              aria-label="Hentikan pemutar"
              className="ml-0.5 flex h-8 w-8 items-center justify-center rounded-lg text-white/45 transition hover:bg-white/10 hover:text-white"
            >
              <Icon name="close" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Non-modal Draggable Floating Player Pop-up (desktop + mobile) */}
      <dialog
        ref={dialogRef}
        popover="manual"
        aria-labelledby="floating-player-title"
        style={{ left: `${resolvedPos.x}px`, top: `${resolvedPos.y}px` }}
        className={`floating-player w-[min(390px,calc(100vw-16px))] overflow-hidden rounded-2xl border border-border bg-navy-1/95 p-0 text-white shadow-2xl backdrop-blur-xl ${
          dragging ? 'ring-2 ring-cyan/50 select-none' : ''
        }`}
      >
        {/* Drag Handle Header Bar */}
        <div
          onPointerDown={onDragStart}
          onPointerMove={onDragMove}
          onPointerUp={onDragEnd}
          onPointerCancel={onDragEnd}
          onDoubleClick={() => setPos(defaultPosition(390))}
          title="Seret untuk menggeser posisi pemutar (klik ganda untuk reset posisi)"
          className="touch-none flex cursor-grab items-center justify-between gap-2 border-b border-border-soft bg-navy-2/90 px-3.5 py-2 active:cursor-grabbing select-none"
        >
          <div className="flex min-w-0 items-center gap-2">
            <span aria-hidden="true" className="font-mono text-xs tracking-tighter text-cyan">
              ⋮⋮
            </span>
            <span className="truncate font-mono text-[10px] font-semibold uppercase tracking-wider text-white/70">
              Pemutar Lagu · Geser
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setSheetOpen(false)}
              title="Minimalkan ke bilah bawah"
              aria-label="Minimalkan pop-up pemutar"
              className="touch-hit flex h-7 w-7 items-center justify-center rounded-lg text-white/60 transition hover:bg-white/10 hover:text-white"
            >
              <span className="block h-0.5 w-3 rounded-full bg-current" />
            </button>
            <button
              type="button"
              onClick={() => setSheetOpen(false)}
              title="Tutup pop-up (audio tetap bisa diputar di bilah bawah)"
              aria-label="Tutup pop-up pemutar"
              className="touch-hit flex h-7 w-7 items-center justify-center rounded-lg text-white/60 transition hover:bg-white/10 hover:text-white"
            >
              <Icon name="close" className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Scrollable Pop-up Body */}
        <div className="max-h-[calc(100dvh-96px)] space-y-3 overflow-y-auto p-4">
          {/* Song Identity + Cover */}
          <div className="flex items-start gap-3">
            <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-border bg-navy-2">
              {track.coverUrl ? (
                <img src={track.coverUrl} alt="Cover art" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-white/40">
                  <Icon name="music" className="h-6 w-6" />
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="rounded bg-cyan/15 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase text-cyan">
                  Aktif
                </span>
                <span className="truncate font-mono text-[10px] text-white/60">
                  klipin-music
                </span>
              </div>
              <h3 id="floating-player-title" className="mt-1 truncate text-sm font-bold text-white">
                {track.title || 'Klipin Song'}
              </h3>
              <p className="truncate text-[11px] text-white/70">
                {track.genreLabel || 'Klipin Music'}
              </p>
              {actions?.lyricsSource && (
                <p className="mt-0.5 font-mono text-[10px] text-signal">
                  Lirik: {actions.lyricsSource}
                </p>
              )}
            </div>
          </div>

          {/* Live Waveform & Transport */}
          <div className="space-y-2 rounded-xl border border-border-soft bg-navy-2/60 p-3">
            <MusicWaveform
              src={track.audioUrl}
              audioRef={audioRef}
              playing={playing}
              currentTime={currentTime}
              duration={total}
              className="!h-12"
            />
            <input
              type="range"
              min={0}
              max={total}
              step={0.1}
              value={Math.min(currentTime, total)}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label="Posisi lagu pop-up"
              className="w-full cursor-pointer accent-cyan"
            />
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={togglePlay}
                className="player-btn inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-cyan to-magenta px-4 py-2 text-xs font-bold text-white-solid shadow-sm transition hover:brightness-110 active:scale-[0.98]"
              >
                <Icon name={playing ? 'pause' : 'play'} className="h-3.5 w-3.5" />
                {playing ? 'Jeda' : 'Putar'}
              </button>
              <span className="font-mono text-[11px] tabular-nums text-white/65">
                {fmt(currentTime)} / {fmt(total)}
              </span>
            </div>
          </div>

          {/* Action Chips (Downloads, Studio EQ, +60s, Stems, Cover, Persona) */}
          <div className="flex flex-wrap gap-1.5">
            {track.jobId && (
              <button
                type="button"
                onClick={openStudioEq}
                className="rounded-lg border border-signal/45 bg-signal/15 px-2.5 py-1 text-[11px] font-semibold text-signal transition hover:bg-signal/25"
              >
                🎚️ Studio EQ
              </button>
            )}
            {track.jobId && (
              <button
                type="button"
                onClick={() => {
                  setSheetOpen(false)
                  const path = `#/song/${track.jobId}`
                  if (onNavigate) onNavigate(path)
                  else window.location.hash = path
                }}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/85 transition hover:border-cyan/50"
              >
                🔗 Share
              </button>
            )}
            {track.audioPath && (
              <a
                href={resolveDownloadAsset(track.audioPath, `${track.title || 'klipin-music'}.mp3`)}
                download={`${track.title || 'klipin-music'}.mp3`}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/85 transition hover:border-cyan/50"
              >
                ⬇ MP3
              </a>
            )}
            {track.wavUrl && (
              <a
                href={resolveDownloadAsset(track.wavUrl, `${track.title || 'klipin-music'}.wav`)}
                download={`${track.title || 'klipin-music'}.wav`}
                className="rounded-lg border border-cyan/35 bg-cyan/10 px-2.5 py-1 text-[11px] font-medium text-cyan-soft transition hover:border-cyan"
              >
                ⬇ WAV
              </a>
            )}
            {actions?.lyricsPath && (
              <a
                href={resolveDownloadAsset(actions.lyricsPath, `${track.title || 'lyrics'}.lrc`)}
                download={`${track.title || 'lyrics'}.lrc`}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50"
              >
                .LRC
              </a>
            )}
            {actions?.onRefreshCover && (
              <button
                type="button"
                onClick={actions.onRefreshCover}
                disabled={actions.coverBusy}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50 disabled:opacity-40"
              >
                {actions.coverBusy ? '…' : 'Ganti cover'}
              </button>
            )}
            {actions?.onExtend && (
              <button
                type="button"
                onClick={actions.onExtend}
                disabled={actions.extendBusy}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50 disabled:opacity-40"
              >
                {actions.extendBusy ? '…' : '+60s'}
              </button>
            )}
            {actions?.onLoadStems && (
              <button
                type="button"
                onClick={actions.onLoadStems}
                disabled={actions.stemsBusy}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50 disabled:opacity-40"
              >
                {actions.stemsBusy ? '…' : 'Stems'}
              </button>
            )}
            {actions?.onToggleShare && (
              <button
                type="button"
                onClick={actions.onToggleShare}
                disabled={actions.shareBusy}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50 disabled:opacity-40"
              >
                {actions.shareBusy ? '…' : (actions.isPublic ? 'Shared' : 'Share')}
              </button>
            )}
            {actions?.onSavePersona && (
              <button
                type="button"
                onClick={actions.onSavePersona}
                className="rounded-lg border border-border bg-navy-2 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:border-cyan/50"
              >
                Simpan persona
              </button>
            )}
          </div>

          {actions?.stemsInfo && (
            <p className="font-mono text-[10px] text-white/60">{actions.stemsInfo}</p>
          )}

          {/* Collapsible Karaoke Lyrics Timeline */}
          <div className="rounded-xl border border-border-soft bg-navy-2/50 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => setShowLyrics((v) => !v)}
                className="text-left text-[11px] font-semibold uppercase tracking-wider text-white/80 hover:text-white"
              >
                Lirik Timeline {lyricsLines.length ? `(${lyricsLines.length})` : ''} {showLyrics ? '▾' : '▸'}
              </button>
              <div className="flex items-center gap-2">
                {actions?.onRealignLyrics && lyricsLines.length > 0 && (
                  <button
                    type="button"
                    onClick={actions.onRealignLyrics}
                    disabled={actions.realignBusy}
                    className="font-mono text-[10px] text-cyan hover:underline disabled:opacity-40"
                  >
                    {actions.realignBusy ? 'Aligning…' : 'Align ulang'}
                  </button>
                )}
              </div>
            </div>
            {showLyrics && (
              lyricsLines.length ? (
                <div className="mt-2 max-h-44 space-y-1 overflow-y-auto pr-1">
                  {lyricsLines.map((line, i) => {
                    const active = i === activeIndex
                    return (
                      <button
                        key={`${line.start}-${i}`}
                        type="button"
                        ref={active ? activeLineRef : undefined}
                        onClick={() => seek(line.start)}
                        className={`flex w-full gap-2 rounded-lg px-2 py-1 text-left text-xs transition ${
                          active
                            ? 'bg-cyan/15 font-semibold text-cyan'
                            : 'text-white/65 hover:bg-white/5 hover:text-white'
                        }`}
                      >
                        <span className="w-9 shrink-0 font-mono text-[10px] text-white/45">
                          {fmt(line.start)}
                        </span>
                        <span className="flex-1 leading-5">{line.text}</span>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <p className="mt-1.5 text-[11px] text-white/45">
                  Lirik karaoke akan tampil di sini jika lagu memiliki timeline lirik.
                </p>
              )
            )}
          </div>

          <div className="flex items-center justify-between border-t border-border-soft pt-2 text-[11px]">
            <button
              type="button"
              onClick={openInStudio}
              className="text-white/55 hover:text-white"
            >
              Ke Studio Lagu →
            </button>
            <button
              type="button"
              onClick={closePlayer}
              className="font-medium text-white/50 hover:text-danger"
            >
              Hentikan audio
            </button>
          </div>
        </div>
      </dialog>
    </>
  )
}
