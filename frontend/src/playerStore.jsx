import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { getMusicStatus, resolveMediaAsset } from './api.js'

const PlayerContext = createContext(null)

/**
 * Global persistent audio player state so songs keep playing seamlessly when
 * users switch between Studio, Cover, History, EQ, Pricing, or Settings.
 */
export function PlayerProvider({ children }) {
  const audioRef = useRef(null)
  const [track, setTrack] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState('')
  const [sheetOpen, setSheetOpen] = useState(false)

  const retryKeyRef = useRef('')
  const trackRef = useRef(null)
  trackRef.current = track

  const startPlayback = useCallback(() => {
    const el = audioRef.current
    if (!el || !el.getAttribute('src')) return
    const p = el.play()
    if (p && typeof p.catch === 'function') {
      p.catch((e) => {
        setPlaying(false)
        if (e?.name !== 'AbortError') {
          setError(`Lagu gagal diputar: ${e?.message || e}`)
        }
      })
    }
  }, [])

  const pause = useCallback(() => {
    const el = audioRef.current
    if (el && !el.paused) el.pause()
  }, [])

  const togglePlay = useCallback(() => {
    const el = audioRef.current
    if (!el || !el.getAttribute('src')) return
    if (!el.paused) el.pause()
    else startPlayback()
  }, [startPlayback])

  const seek = useCallback(
    (seconds) => {
      const el = audioRef.current
      if (!el) return
      const next = Math.max(0, Number(seconds) || 0)
      el.currentTime = next
      setCurrentTime(next)
      if (el.paused) startPlayback()
    },
    [startPlayback],
  )

  const loadTrack = useCallback(
    (nextTrack, { autoPlay = false } = {}) => {
      if (!nextTrack?.audioUrl) {
        setTrack(null)
        setPlaying(false)
        setCurrentTime(0)
        setDuration(0)
        setError('')
        return
      }
      setError('')
      const current = trackRef.current
      const sameSource = current?.audioUrl === nextTrack.audioUrl && current?.jobId === nextTrack.jobId
      setTrack(nextTrack)
      if (!sameSource) {
        setCurrentTime(0)
        setDuration(Number(nextTrack.duration) || 0)
      }
      if (autoPlay) {
        requestAnimationFrame(() => startPlayback())
      }
    },
    [startPlayback],
  )

  const updateTrackMeta = useCallback((partial) => {
    setTrack((prev) => (prev ? { ...prev, ...partial } : prev))
  }, [])

  const closePlayer = useCallback(() => {
    const el = audioRef.current
    if (el) {
      el.pause()
      el.removeAttribute('src')
      el.load()
    }
    setTrack(null)
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
    setError('')
    setSheetOpen(false)
  }, [])

  // Sync Media Session API so mobile lock screen & headset media keys work.
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return undefined
    try {
      if (track) {
        const artwork = track.coverUrl ? [{ src: track.coverUrl, sizes: '512x512', type: 'image/jpeg' }] : []
        navigator.mediaSession.metadata = new MediaMetadata({
          title: track.title || 'Klip-in Song',
          artist: track.genreLabel || 'Klip-in Studio',
          album: 'Klip-in Music',
          artwork,
        })
        navigator.mediaSession.setActionHandler('play', startPlayback)
        navigator.mediaSession.setActionHandler('pause', pause)
        navigator.mediaSession.setActionHandler('seekto', (details) => {
          if (typeof details?.seekTime === 'number') seek(details.seekTime)
        })
      } else {
        navigator.mediaSession.metadata = null
      }
    } catch {
      /* ignore unsupported MediaMetadata environment */
    }
    return undefined
  }, [track, startPlayback, pause, seek])

  const handleError = () => {
    setPlaying(false)
    const active = trackRef.current
    if (!active?.jobId) return
    const attempt = `${active.jobId}|${active.audioUrl}`
    if (retryKeyRef.current === attempt) {
      setError('Audio lagu gagal dimuat dari server. Coba muat ulang lagu dari Riwayat.')
      return
    }
    retryKeyRef.current = attempt
    getMusicStatus(active.jobId)
      .then((fresh) => {
        if (trackRef.current?.jobId !== active.jobId || !fresh?.audio_path) return
        const freshUrl = resolveMediaAsset(fresh.audio_path)
        setTrack((prev) => (prev ? { ...prev, audioUrl: freshUrl, audioPath: fresh.audio_path } : prev))
      })
      .catch((e) => setError(`Audio lagu gagal dimuat: ${e.message}`))
  }

  return (
    <PlayerContext.Provider
      value={{
        audioRef,
        track,
        playing,
        currentTime,
        duration,
        error,
        setError,
        sheetOpen,
        setSheetOpen,
        loadTrack,
        updateTrackMeta,
        play: startPlayback,
        pause,
        togglePlay,
        seek,
        closePlayer,
      }}
    >
      {children}
      <audio
        ref={audioRef}
        src={track?.audioUrl || undefined}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onEmptied={() => setPlaying(false)}
        onError={handleError}
        onTimeUpdate={() => {
          if (audioRef.current) setCurrentTime(audioRef.current.currentTime || 0)
        }}
        onLoadedMetadata={() => {
          if (audioRef.current) {
            setDuration(audioRef.current.duration || Number(trackRef.current?.duration) || 0)
          }
        }}
        className="hidden"
      />
    </PlayerContext.Provider>
  )
}

export function usePlayer() {
  const ctx = useContext(PlayerContext)
  if (!ctx) throw new Error('usePlayer must be used within PlayerProvider')
  return ctx
}
