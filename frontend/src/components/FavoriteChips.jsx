import { useEffect, useState } from 'react'
import {
  favoriteLabel, isSignedIn, isSynced, listGroups, pinFavorite,
  removeFavorite, renameFavorite, setFavoriteGroup, syncFavorites,
} from '../criteriaStore.js'
import { showToast } from '../toast.js'
import Icon from './Icon.jsx'

/**
 * Shared favorite-criteria chip row used by the Input stage and Trim modal.
 * Favorites render grouped by their `group` folder (ungrouped first); each
 * chip supports click-to-fill, inline rename, move-to-folder, and delete.
 *
 * Props:
 *  - favorites: [{prompt, name, group}]  (from criteriaStore)
 *  - value:     current criteria text (to highlight the active chip)
 *  - onUse:     (prompt) => void          — fill the criteria textarea
 *  - onChange:  (favorites) => void       — setFavorites (after rename/move/delete)
 *  - accent:    'cyan' (input) | 'magenta' (trim)
 */
export default function FavoriteChips({ favorites, value, onUse, onChange, accent = 'cyan' }) {
  const [editingFav, setEditingFav] = useState(null) // prompt being renamed
  const [editName, setEditName] = useState('')
  const [movingFav, setMovingFav] = useState(null) // prompt being moved
  const [newGroup, setNewGroup] = useState('')
  const [syncTick, setSyncTick] = useState(0) // re-render when sync status changes

  // Refresh badge state when a favorite gets confirmed as synced (or removed).
  useEffect(() => {
    const onSync = () => setSyncTick((t) => t + 1)
    window.addEventListener('klipin:criteria-synced', onSync)
    return () => window.removeEventListener('klipin:criteria-synced', onSync)
  }, [])
  void syncTick // sync status is read fresh from the store on every render

  const groups = listGroups()
  const active = (value || '').trim()
  const [syncing, setSyncing] = useState(false)

  // Signed in but some favorites are still local-only (server push pending
  // or failed) → warn and offer a manual re-sync.
  const pending = isSignedIn() && favorites.some((f) => !isSynced(f.prompt))
  const pendingCount = favorites.filter((f) => !isSynced(f.prompt)).length

  const syncNow = async () => {
    setSyncing(true)
    try {
      onChange(await syncFavorites())
      showToast('✓ Favorit tersinkron ke akun')
    } catch {
      /* failure toast already emitted by the store */
    } finally {
      setSyncing(false)
    }
  }

  const commitRename = (prompt) => {
    onChange(renameFavorite(prompt, editName))
    setEditingFav(null)
  }

  const commitMove = (prompt, group) => {
    onChange(setFavoriteGroup(prompt, group))
    setMovingFav(null)
    setNewGroup('')
  }

  // First chip of its group row → already pinned (button disabled).
  const isFirstInRow = (f) =>
    favorites.findIndex((x) => (x.group || '') === (f.group || '')) === favorites.indexOf(f)

  const chip = (f) => {
    const isActive = active === f.prompt
    const pinned = isFirstInRow(f)
    return (
      <span
        key={f.prompt}
        className={`group inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] transition ${
          isActive
            ? accent === 'cyan'
              ? 'border-cyan bg-cyan/15 text-cyan-soft'
              : 'border-magenta bg-magenta/15 text-signal'
            : 'border-border bg-navy-2/60 text-white/45 hover:border-cyan/40'
        }`}
      >
        {editingFav === f.prompt ? (
          <input
            autoFocus
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onBlur={() => commitRename(f.prompt)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename(f.prompt)
              if (e.key === 'Escape') setEditingFav(null)
            }}
            placeholder={favoriteLabel(f)}
            aria-label={`rename favorit ${favoriteLabel(f)}`}
            className={`w-32 bg-transparent outline-none placeholder:text-white/30 ${
              accent === 'cyan' ? 'text-cyan-soft' : 'text-signal'
            }`}
          />
        ) : (
          <button
            type="button"
            onClick={() => onUse(f.prompt)}
            title={f.prompt}
            className="max-w-[220px] truncate"
          >
            {favoriteLabel(f)}
          </button>
        )}

        {movingFav === f.prompt ? (
          <span className="flex items-center gap-1">
            <input
              autoFocus
              value={newGroup}
              onChange={(e) => setNewGroup(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitMove(f.prompt, newGroup)
                if (e.key === 'Escape') setMovingFav(null)
              }}
              onBlur={() => {
                if (newGroup.trim()) commitMove(f.prompt, newGroup)
                else setMovingFav(null)
              }}
              placeholder="folder baru…"
              aria-label={`folder baru untuk ${favoriteLabel(f)}`}
              className="w-24 bg-transparent outline-none placeholder:text-white/30"
            />
            {groups.map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => commitMove(f.prompt, g)}
                className={`rounded px-1 py-0.5 transition ${
                  f.group === g
                    ? 'bg-white/15 text-white'
                    : 'bg-white/5 text-white/60 hover:bg-white/15 hover:text-white'
                }`}
                title={g}
              >
                {g}
              </button>
            ))}
            <button
              type="button"
              onClick={() => commitMove(f.prompt, '')}
              className={`rounded px-1 py-0.5 transition ${
                !f.group ? 'bg-white/15 text-white' : 'bg-white/5 text-white/60 hover:bg-white/15'
              }`}
              title="Tanpa folder"
            >
              <Icon name="none" className="h-3 w-3" />
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => { setMovingFav(f.prompt); setNewGroup('') }}
            aria-label={`pindahkan ${favoriteLabel(f)} ke folder`}
            title={`Folder: ${f.group || 'tanpa folder'}`}
            className="text-white/25 transition hover:text-cyan-soft"
          >
            <Icon name="folder" className="h-3 w-3" />
          </button>
        )}

        {/* sync indicator: ☁️ = stored on the account, 💾 = local only */}
        <span
          title={isSynced(f.prompt) ? 'Tersimpan di akun (sinkron lintas perangkat)' : 'Tersimpan lokal di perangkat ini saja'}
          className={`inline-flex ${isSynced(f.prompt) ? 'text-cyan-soft/70' : 'text-white/20'}`}
        >
          <Icon name={isSynced(f.prompt) ? 'cloud' : 'upload'} className="h-3 w-3" />
        </span>

        <button
          type="button"
          onClick={() => onChange(pinFavorite(f.prompt))}
          disabled={pinned}
          aria-label={`pin ${favoriteLabel(f)} ke atas`}
          title={pinned ? 'Sudah paling depan di foldernya' : 'Pin ke atas (paling depan di foldernya)'}
          className={`transition ${pinned ? 'cursor-default text-cyan-soft/40' : 'text-white/25 hover:text-cyan-soft'}`}
        >
          <Icon name="pin" className="h-3 w-3" />
        </button>

        <button
          type="button"
          onClick={() => { setEditingFav(f.prompt); setEditName(f.name || '') }}
          aria-label={`rename favorit ${favoriteLabel(f)}`}
          title="Rename"
          className="text-white/25 transition hover:text-cyan-soft"
        >
          <Icon name="edit" className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={() => onChange(removeFavorite(f.prompt))}
          aria-label={`hapus favorit ${favoriteLabel(f)}`}
          className="text-white/30 transition hover:text-danger"
        >
          <Icon name="close" className="h-3 w-3" />
        </button>
      </span>
    )
  }

  const ungrouped = favorites.filter((f) => !f.group)
  const grouped = favorites.filter((f) => f.group)

  return (
    <div className="space-y-2">
      {pending && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 font-mono text-[10px] text-gold">
          <Icon name="warn" className="h-3 w-3" /> {pendingCount} favorit belum tersinkron ke akun
          <button
            type="button"
            onClick={syncNow}
            disabled={syncing}
            className="ml-auto inline-flex items-center gap-1 rounded border border-warn/40 px-1.5 py-0.5 uppercase tracking-wider transition hover:bg-warn/20 disabled:opacity-40"
          >
            {syncing ? 'menyinkronkan…' : <><Icon name="sync" className="h-3 w-3" /> Sync sekarang</>}
          </button>
        </div>
      )}
      {ungrouped.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[9px] uppercase tracking-wider text-white/25">Inbox</span>
          {ungrouped.map(chip)}
        </div>
      )}
      {groups.map((g) => {
        const items = grouped.filter((f) => f.group === g)
        if (!items.length) return null
        return (
          <div key={g} className="flex flex-wrap items-center gap-1.5">
            <span className="rounded border border-border-soft bg-navy-2/80 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-white/40">
              <Icon name="folder" className="h-2.5 w-2.5 inline" /> {g}
            </span>
            {items.map(chip)}
          </div>
        )
      })}
    </div>
  )
}
