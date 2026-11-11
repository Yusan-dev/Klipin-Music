import { useEffect, useRef } from 'react'
import { resolveDownloadAsset } from '../api.js'
import { usePlayer } from '../playerStore.jsx'
import Icon from './Icon.jsx'

function fmt(value) {
  const s = Math.max(0, Number(value) || 0)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

/**
 * Sticky desktop transport dock + mobile expandable bottom sheet.
 * Kept single-row and under 12% viewport height on desktop; on mobile, the
 * trigger lives in the bottom nav / topbar so bars never stack vertically.
 */
export default function MiniPlayer({ sidebarCollapsed = false, onNavigate }) {
  const {
    track,
    playing,
    currentTime,
    duration,
    sheetOpen,
    setSheetOpen,
    togglePlay,
    seek,
    closePlayer,
  } = usePlayer()

  const dialogRef = useRef(null)

  useEffect(() => {
    const dlg = dialogRef.current
    if (!dlg) return
    if (sheetOpen && !dlg.open && track?.audioUrl) {
      try {
        dlg.showModal()
      } catch {
        /* fallback cleanly if dialog already open */
      }
    } else if ((!sheetOpen || !track?.audioUrl) && dlg.open) {
      dlg.close()
    }
  }, [sheetOpen, track?.audioUrl])

  if (!track?.audioUrl) return null

  const total = Math.max(1, duration || track.duration || 1)
  const pct = Math.max(0, Math.min(100, (currentTime / total) * 100))
  const openStudioEq = () => {
    setSheetOpen(false)
    const path = track.jobId ? `#/studio/${track.jobId}` : '#/studio'
    if (onNavigate) onNavigate(path)
    else window.location.hash = path
  }
  const openInStudio = () => {
    setSheetOpen(false)
    if (onNavigate) onNavigate('#/music')
    else window.location.hash = '#/music'
  }

  return (
    <>
      {/* Desktop persistent bottom dock (single row, spans beside fixed Sidebar) */}
      <aside
        aria-label="Pemutar audio aktif"
        className={`fixed bottom-0 right-0 z-40 hidden border-t border-border bg-navy-1/95 backdrop-blur-xl transition-[left] lg:block ${
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
            onClick={openInStudio}
            className="flex min-w-0 max-w-[260px] items-center gap-3 text-left transition hover:opacity-90"
            title="Buka di Studio Lagu"
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
              <div className="truncate font-mono text-[10px] text-white/50">
                {track.genreLabel || 'Klip-in'} · {track.provider || 'studio'}
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
            {track.jobId && (
              <button
                type="button"
                onClick={openStudioEq}
                className="rounded-lg border border-signal/40 bg-signal/10 px-2.5 py-1.5 text-[11px] font-semibold text-signal transition hover:bg-signal/20"
              >
                🎚️ Studio EQ
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
            {track.wavUrl && (
              <a
                href={resolveDownloadAsset(track.wavUrl, `${track.title || 'klipin-music'}.wav`)}
                download={`${track.title || 'klipin-music'}.wav`}
                className="rounded-lg border border-cyan/35 bg-cyan/10 px-2.5 py-1.5 text-[11px] font-medium text-cyan-soft transition hover:border-cyan"
              >
                ⬇ WAV
              </a>
            )}
            <button
              type="button"
              onClick={closePlayer}
              title="Tutup pemutar"
              aria-label="Tutup pemutar mini"
              className="ml-1 flex h-8 w-8 items-center justify-center rounded-lg text-white/45 transition hover:bg-white/10 hover:text-white"
            >
              <Icon name="close" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Mobile full interactive player drawer (top-layer native dialog with backdrop light-dismiss) */}
      <dialog
        ref={dialogRef}
        onCancel={() => setSheetOpen(false)}
        onClick={(e) => {
          if (e.target === dialogRef.current) setSheetOpen(false)
        }}
        className="m-auto w-[calc(100%-1.5rem)] max-w-md rounded-2xl border border-border bg-navy-1 p-0 text-white shadow-2xl backdrop:bg-black/75 backdrop:backdrop-blur-sm lg:hidden"
      >
        <div className="space-y-4 p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="h-14 w-14 shrink-0 overflow-hidden rounded-xl border border-border bg-navy-2">
                {track.coverUrl ? (
                  <img src={track.coverUrl} alt="" className="h-full w-full object-cover" />
                ) : (
                  <span className="flex h-full w-full items-center justify-center text-white/45">
                    <Icon name="music" className="h-6 w-6" />
                  </span>
                )}
              </div>
              <div className="min-w-0">
                <span className="font-mono text-[10px] uppercase tracking-wider text-cyan">
                  Sedang Dimuat
                </span>
                <h3 className="truncate text-base font-bold text-white">
                  {track.title || 'Klipin Song'}
                </h3>
                <p className="truncate font-mono text-[11px] text-white/55">
                  {track.genreLabel || 'Klip-in'} · {track.provider || 'studio'}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setSheetOpen(false)}
              aria-label="Tutup panel pemutar"
              className="touch-hit flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-navy-2 text-white/70 hover:text-white"
            >
              <Icon name="close" className="h-4 w-4" />
            </button>
          </div>

          <div className="space-y-2">
            <input
              type="range"
              min={0}
              max={total}
              step={0.1}
              value={Math.min(currentTime, total)}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label="Posisi lagu mobile"
              className="w-full accent-cyan"
            />
            <div className="flex justify-between font-mono text-xs tabular-nums text-white/60">
              <span>{fmt(currentTime)}</span>
              <span>{fmt(total)}</span>
            </div>
          </div>

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={togglePlay}
              className="player-btn flex flex-1 items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-cyan to-magenta px-4 py-3 text-sm font-bold text-white-solid shadow-sm active:scale-[0.99]"
            >
              <Icon name={playing ? 'pause' : 'play'} className="h-4 w-4" />
              {playing ? 'Jeda Lagu' : 'Putar Lagu'}
            </button>
            {track.jobId && (
              <button
                type="button"
                onClick={openStudioEq}
                className="player-btn rounded-xl border border-signal/40 bg-signal/10 px-3.5 py-3 text-xs font-semibold text-signal"
              >
                🎚️ EQ
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-soft pt-3">
            <div className="flex gap-2">
              {track.audioPath && (
                <a
                  href={resolveDownloadAsset(track.audioPath, `${track.title || 'klipin-music'}.mp3`)}
                  download={`${track.title || 'klipin-music'}.mp3`}
                  className="touch-hit inline-flex items-center gap-1.5 rounded-lg border border-border bg-navy-2 px-3 py-2 text-xs font-medium text-white/85"
                >
                  ⬇ MP3
                </a>
              )}
              {track.wavUrl && (
                <a
                  href={resolveDownloadAsset(track.wavUrl, `${track.title || 'klipin-music'}.wav`)}
                  download={`${track.title || 'klipin-music'}.wav`}
                  className="touch-hit inline-flex items-center gap-1.5 rounded-lg border border-cyan/35 bg-cyan/10 px-3 py-2 text-xs font-medium text-cyan-soft"
                >
                  ⬇ WAV
                </a>
              )}
            </div>
            <button
              type="button"
              onClick={closePlayer}
              className="text-xs font-medium text-white/45 hover:text-danger"
            >
              Hentikan
            </button>
          </div>
        </div>
      </dialog>
    </>
  )
}
