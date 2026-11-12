import { useEffect, useState } from 'react'
import {
  addMusicPlaylistItem,
  createMusicPlaylist,
  deleteMusicPlaylist,
  listMusicPlaylists,
  removeMusicPlaylistItem,
} from '../api.js'

export default function PlaylistStudioBar({
  user,
  selectedJob,
  onSelectSongJob,
}) {
  const [playlists, setPlaylists] = useState([])
  const [activeId, setActiveId] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const refresh = () => {
    if (!user) return
    listMusicPlaylists()
      .then((res) => {
        const list = res?.playlists || []
        setPlaylists(list)
        if (list.length && !activeId) setActiveId(list[0].playlist_id)
      })
      .catch(() => {})
  }

  useEffect(() => {
    refresh()
  }, [user])

  if (!user) return null

  const activePlaylist = playlists.find((p) => p.playlist_id === activeId) || null

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!newTitle.trim() || busy) return
    setBusy(true)
    setMsg('')
    try {
      const created = await createMusicPlaylist({ title: newTitle.trim(), is_public: true })
      setNewTitle('')
      setPlaylists((prev) => [created, ...prev])
      setActiveId(created.playlist_id)
      setMsg(`Playlist "${created.title}" dibuat.`)
    } catch (err) {
      setMsg(err?.message || 'Gagal membuat playlist.')
    } finally {
      setBusy(false)
    }
  }

  const handleAddCurrent = async () => {
    if (!activeId || !selectedJob?.job_id || selectedJob.status !== 'completed' || busy) return
    setBusy(true)
    setMsg('')
    try {
      await addMusicPlaylistItem(activeId, selectedJob.job_id)
      refresh()
      setMsg(`"${selectedJob.title || selectedJob.job_id}" ditambahkan ke playlist.`)
    } catch (err) {
      setMsg(err?.message || 'Gagal menambahkan lagu.')
    } finally {
      setBusy(false)
    }
  }

  const handleDeletePlaylist = async (pid) => {
    if (!pid || busy) return
    setBusy(true)
    try {
      await deleteMusicPlaylist(pid)
      setPlaylists((prev) => prev.filter((p) => p.playlist_id !== pid))
      if (activeId === pid) setActiveId('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-border bg-navy-1 p-3.5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-white/90">
          📂 Playlist &amp; Album Studio
        </h3>
        {selectedJob?.status === 'completed' && activePlaylist && (
          <button
            type="button"
            disabled={busy}
            onClick={handleAddCurrent}
            className="rounded-md border border-cyan/40 bg-cyan/15 px-2.5 py-1 text-[11px] font-semibold text-cyan-soft hover:bg-cyan/25 disabled:opacity-40"
          >
            + Masukkan Lagu Aktif ke "{activePlaylist.title}"
          </button>
        )}
      </div>

      <form onSubmit={handleCreate} className="mb-2.5 flex gap-2">
        <input
          type="text"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="Nama playlist / album baru…"
          aria-label="Nama playlist baru"
          className="min-w-0 flex-1 rounded-lg border border-border bg-navy-2 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan"
        />
        <button
          type="submit"
          disabled={busy || !newTitle.trim()}
          className="rounded-lg bg-signal px-3 py-1.5 text-xs font-bold text-white-solid hover:brightness-110 disabled:opacity-40"
        >
          + Buat
        </button>
      </form>

      {playlists.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {playlists.map((pl) => (
            <div key={pl.playlist_id} className="inline-flex items-center rounded-md border border-border bg-navy-2">
              <button
                type="button"
                onClick={() => setActiveId(pl.playlist_id)}
                className={`px-2.5 py-1 text-[11px] font-medium ${
                  activeId === pl.playlist_id ? 'text-cyan-soft font-bold' : 'text-white/75'
                }`}
              >
                {pl.title} ({pl.item_count || 0})
              </button>
              <button
                type="button"
                aria-label={`Hapus playlist ${pl.title}`}
                onClick={() => handleDeletePlaylist(pl.playlist_id)}
                className="px-1.5 py-1 text-[10px] text-white/45 hover:text-danger"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {activePlaylist && (activePlaylist.items || []).length > 0 && (
        <div className="space-y-1">
          {activePlaylist.items.map((item) => (
            <div
              key={item.job_id}
              className="flex items-center justify-between gap-2 rounded-lg border border-border-soft bg-navy-2 px-2.5 py-1.5 text-[11px]"
            >
              <button
                type="button"
                onClick={() => onSelectSongJob?.(item.job_id)}
                className="truncate text-left font-medium text-white hover:text-cyan"
              >
                ▶ {item.title || item.job_id}
              </button>
              <button
                type="button"
                onClick={async () => {
                  await removeMusicPlaylistItem(activePlaylist.playlist_id, item.job_id)
                  refresh()
                }}
                className="text-[10px] text-white/45 hover:text-danger"
              >
                Keluarkan
              </button>
            </div>
          ))}
        </div>
      )}

      {msg && <p className="mt-1.5 text-[11px] text-signal">{msg}</p>}
    </div>
  )
}

