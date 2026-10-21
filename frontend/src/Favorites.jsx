import { useEffect, useMemo, useState } from 'react'
import {
  clearGroup, favoriteLabel, isSignedIn, isSynced, listGroups, loadFavorites,
  pinFavorite, removeFavorite, renameFavorite, renameGroup, renamePrompt,
  saveFavorite, setFavoriteGroup, syncFavorites,
} from './criteriaStore.js'
import { Button, Micro, Select, Spinner, StatusDot, TextInput } from './components/ui.jsx'
import Icon from './components/Icon.jsx'
import { showToast } from './toast.js'

/**
 * Central favorites manager (CRUD + folders in one place). Reads/writes via
 * criteriaStore, so everything syncs to the account when signed in and
 * falls back to localStorage otherwise.
 */
function GroupPicker({ group, onGroup, newGroup, onNewGroup, groups, className = '' }) {
  const isNew = group === '__new__'
  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      <Select
        value={isNew ? '__new__' : group}
        onChange={(e) => onGroup(e.target.value)}
        aria-label="folder"
        className="w-44 !py-1.5 !text-xs"
      >
        <option value="">Inbox</option>
        {groups.map((g) => (
          <option key={g} value={g}>{g}</option>
        ))}
        <option value="__new__">+ Folder baru…</option>
      </Select>
      {isNew && (
        <TextInput
          autoFocus
          value={newGroup}
          onChange={(e) => onNewGroup(e.target.value)}
          placeholder="nama folder baru…"
          aria-label="nama folder baru"
          className="w-40 !py-1.5 !text-xs"
        />
      )}
    </div>
  )
}

export default function Favorites({ user, onSignIn }) {
  const [favorites, setFavorites] = useState(() => loadFavorites())
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ prompt: '', name: '', group: '', newGroup: '' })
  const [editing, setEditing] = useState(null) // prompt being edited
  const [edit, setEdit] = useState({ name: '', prompt: '', group: '', newGroup: '' })
  const [renamingGroup, setRenamingGroup] = useState(null) // group label being renamed
  const [renameVal, setRenameVal] = useState('')
  const [copied, setCopied] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncTick, setSyncTick] = useState(0)

  const refresh = (list) => {
    setFavorites(list || loadFavorites())
    setErr('')
  }

  // Pull account-synced favorites on mount (also refreshes sync badges).
  useEffect(() => {
    let alive = true
    syncFavorites().then((list) => { if (alive) refresh(list) }).finally(() => alive && setLoading(false))
    const onSync = () => setSyncTick((t) => t + 1)
    window.addEventListener('klipin:criteria-synced', onSync)
    return () => { alive = false; window.removeEventListener('klipin:criteria-synced', onSync) }
  }, [])
  void syncTick // badge state is read fresh from the store each render

  const groups = useMemo(() => listGroups(), [favorites, syncTick])

  const resolveGroup = (group, newGroup) => (group === '__new__' ? newGroup.trim() : group)

  const add = () => {
    if (!form.prompt.trim()) return
    refresh(saveFavorite(form.prompt.trim(), form.name.trim(), resolveGroup(form.group, form.newGroup)))
    setForm({ prompt: '', name: '', group: '', newGroup: '' })
    setShowForm(false)
  }

  const startEdit = (f) => {
    setEditing(f.prompt)
    setEdit({ name: f.name, prompt: f.prompt, group: f.group, newGroup: '' })
  }

  const commitEdit = (f) => {
    const { name, prompt, group, newGroup } = edit
    const nextGroup = resolveGroup(group, newGroup)
    let list = loadFavorites()
    // prompt text changed (identity) — apply first so later ops use the new prompt
    if (prompt.trim() && prompt.trim() !== f.prompt) list = renamePrompt(f.prompt, prompt.trim())
    const key = prompt.trim() || f.prompt
    if (name.trim() !== f.name) list = renameFavorite(key, name.trim())
    if (nextGroup !== f.group) list = setFavoriteGroup(key, nextGroup)
    refresh(list)
    setEditing(null)
  }

  const remove = (f) => {
    if (!window.confirm(`Hapus favorit "${favoriteLabel(f)}"?`)) return
    refresh(removeFavorite(f.prompt))
  }

  const pin = (f) => refresh(pinFavorite(f.prompt))

  const copy = (f) => {
    const done = () => {
      setCopied(f.prompt)
      setTimeout(() => setCopied((c) => (c === f.prompt ? '' : c)), 1600)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(f.prompt).then(done).catch(() => {})
    } else {
      const ta = document.createElement('textarea')
      ta.value = f.prompt
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy') } catch { /* noop */ }
      document.body.removeChild(ta)
      done()
    }
  }

  const commitGroupRename = (g) => {
    const v = renameVal.trim()
    if (v && v !== g) refresh(renameGroup(g, v))
    setRenamingGroup(null)
    setRenameVal('')
  }

  const clearFolder = (g) => {
    if (!window.confirm(`Hapus folder "${g}"? Favorit di dalamnya dipindah ke Inbox (tidak dihapus).`)) return
    refresh(clearGroup(g))
  }

  const pending = isSignedIn() && favorites.some((f) => !isSynced(f.prompt))
  const pendingCount = favorites.filter((f) => !isSynced(f.prompt)).length

  const syncNow = async () => {
    setSyncing(true)
    try {
      refresh(await syncFavorites())
      showToast('✓ Favorit tersinkron ke akun')
    } catch {
      /* failure toast already emitted by the store */
    } finally {
      setSyncing(false)
    }
  }

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return favorites
    return favorites.filter((f) =>
      (f.prompt || '').toLowerCase().includes(needle) ||
      (f.name || '').toLowerCase().includes(needle) ||
      (f.group || '').toLowerCase().includes(needle))
  }, [favorites, q])

  const ungrouped = visible.filter((f) => !f.group)
  const groupedVisible = visible.filter((f) => f.group)

  const row = (f) => {
    const isEditing = editing === f.prompt
    const label = favoriteLabel(f)
    return (
      <div key={f.prompt} className="card-chrome rounded-lg p-3 transition hover:border-cyan/40">
        <div className="flex flex-wrap items-center gap-2">
          <span className="max-w-[240px] truncate font-mono text-[11px] font-semibold text-white" title={f.name || f.prompt}>
            {label}
          </span>
          <span
            title={isSynced(f.prompt) ? 'Tersimpan di akun (sinkron lintas perangkat)' : 'Tersimpan lokal di perangkat ini saja'}
            className={isSynced(f.prompt) ? 'text-cyan-soft/70' : 'text-white/20'}
          >
            <Icon name={isSynced(f.prompt) ? 'cloud' : 'upload'} className="h-3.5 w-3.5" />
          </span>
          <div className="ml-auto flex items-center gap-2 font-mono text-[11px]">
            <button
              type="button"
              onClick={() => pin(f)}
              disabled={favorites.findIndex((x) => (x.group || '') === (f.group || '')) === favorites.indexOf(f)}
              title="Pin ke atas (paling depan di foldernya)"
              aria-label={`pin ${label}`}
              className={`transition ${favorites.findIndex((x) => (x.group || '') === (f.group || '')) === favorites.indexOf(f) ? 'cursor-default text-cyan-soft/40' : 'text-white/30 hover:text-cyan-soft'}`}
            >
              <Icon name="pin" className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => copy(f)}
              title="Salin kriteria (untuk ditempel di input)"
              aria-label={`salin ${label}`}
              className={`transition ${copied === f.prompt ? 'text-signal' : 'text-white/30 hover:text-cyan-soft'}`}
            >
              <Icon name={copied === f.prompt ? 'check' : 'copy'} className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => startEdit(f)}
              title="Edit"
              aria-label={`edit ${label}`}
              className="text-white/30 transition hover:text-cyan-soft"
            >
              <Icon name="edit" className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => remove(f)}
              title="Hapus"
              aria-label={`hapus ${label}`}
              className="text-white/35 transition hover:text-danger"
            >
              <Icon name="close" className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        {isEditing ? (
          <div className="mt-2 space-y-2 rounded-md border border-cyan/20 bg-navy-2/50 p-2.5">
            <TextInput
              value={edit.name}
              onChange={(e) => setEdit((x) => ({ ...x, name: e.target.value }))}
              placeholder="Label pendek (opsional)"
              aria-label="label"
              className="!py-1.5 !text-xs"
            />
            <textarea
              value={edit.prompt}
              onChange={(e) => setEdit((x) => ({ ...x, prompt: e.target.value }))}
              rows={2}
              aria-label="kriteria"
              className="w-full resize-y rounded-md border border-cyan/25 bg-navy-2 px-2.5 py-1.5 font-mono text-[11px] text-white outline-none placeholder:text-white/25 focus:border-cyan"
            />
            <GroupPicker
              group={edit.group}
              onGroup={(g) => setEdit((x) => ({ ...x, group: g }))}
              newGroup={edit.newGroup}
              onNewGroup={(v) => setEdit((x) => ({ ...x, newGroup: v }))}
              groups={groups}
            />
            <div className="flex gap-2">
              <Button variant="cyan" size="sm" onClick={() => commitEdit(f)} disabled={!edit.prompt.trim()}>
                Simpan
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>Batal</Button>
            </div>
          </div>
        ) : (
          <p className="mt-1 line-clamp-2 font-mono text-[10px] leading-relaxed text-white/45" title={f.prompt}>
            {f.prompt}
          </p>
        )}
      </div>
    )
  }

  const folderHeader = (g, count) => (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      {renamingGroup === g ? (
        <>
          <input
            autoFocus
            value={renameVal}
            onChange={(e) => setRenameVal(e.target.value)}
            onBlur={() => commitGroupRename(g)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitGroupRename(g)
              if (e.key === 'Escape') { setRenamingGroup(null); setRenameVal('') }
            }}
            placeholder={`rename ${g}…`}
            aria-label={`rename folder ${g}`}
            className="w-40 rounded border border-cyan/40 bg-navy-2 px-2 py-0.5 font-mono text-[10px] text-cyan-soft outline-none"
          />
          <Button variant="ghost" size="sm" className="!px-2 !py-0.5 !text-[10px]" onClick={() => commitGroupRename(g)}>
            OK
          </Button>
        </>
      ) : (
        <span className="rounded border border-border-soft bg-navy-2/80 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-white/40">
          <Icon name="folder" className="h-2.5 w-2.5 inline" /> {g}
        </span>
      )}
      <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/50">{count}</span>
      {!renamingGroup && (
        <>
          <button
            type="button"
            onClick={() => { setRenamingGroup(g); setRenameVal(g) }}
            title="Rename folder"
            aria-label={`rename folder ${g}`}
            className="p-1 text-white/30 transition hover:text-cyan-soft"
          >
            <Icon name="edit" className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={() => clearFolder(g)}
            title="Hapus folder (favorit dipindah ke Inbox)"
            aria-label={`hapus folder ${g}`}
            className="p-1 text-white/30 transition hover:text-danger"
          >
            <Icon name="none" className="h-3 w-3" />
          </button>
        </>
      )}
    </div>
  )

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Micro className="block">SYS.FAVORITES — CRITERIA LIBRARY</Micro>
          <h1 className="text-chrome mt-2 text-2xl font-bold tracking-tight">Favorit criteria</h1>
          <p className="mt-1 text-sm text-white/45">
            Kelola semua kriteria custom di satu tempat — tambah, edit, folder, pin, hapus. Tersinkron ke akun.
          </p>
        </div>
        <Button variant="cyan" size="sm" onClick={() => setShowForm((v) => !v)}>
          {showForm ? <><Icon name="close" className="h-3.5 w-3.5" /> Tutup</> : <><Icon name="plus" className="h-3.5 w-3.5" /> Favorit baru</>}
        </Button>
      </div>

      {!user && (
        <p className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-border-soft bg-navy-2/50 p-2.5 text-xs text-white/45">
          <StatusDot tone="idle" />
          Belum masuk akun — perubahan hanya tersimpan di perangkat ini.
          <button type="button" onClick={onSignIn} className="text-cyan-soft underline-offset-2 hover:underline">
            Masuk untuk sinkron lintas perangkat →
          </button>
        </p>
      )}

      {user && pending && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn/10 p-2.5 text-xs text-gold">
          <Icon name="warn" className="h-3.5 w-3.5" /> {pendingCount} favorit belum tersinkron ke akun — mungkin karena koneksi terputus saat disimpan.
          <button
            type="button"
            onClick={syncNow}
            disabled={syncing}
            className="ml-auto inline-flex items-center gap-1 rounded border border-warn/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition hover:bg-warn/20 disabled:opacity-40"
          >
            {syncing ? 'menyinkronkan…' : <><Icon name="sync" className="h-3 w-3" /> Sync sekarang</>}
          </button>
        </div>
      )}

      {err && (
        <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 p-2.5 text-xs text-danger">{err}</p>
      )}

      {showForm && (
        <div className="card-chrome mb-6 rounded-xl p-4">
          <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-soft/70">NEW FAVORITE</div>
          <div className="space-y-2.5">
            <textarea
              value={form.prompt}
              onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
              rows={2}
              placeholder="Kriteria custom — cth: fokus pada momen lucu, bahasa santai, pancing komentar"
              aria-label="kriteria"
              className="w-full resize-y rounded-md border border-cyan/25 bg-navy-2 px-3 py-2 font-mono text-xs text-white outline-none placeholder:text-white/25 focus:border-cyan"
            />
            <div className="grid gap-2.5 sm:grid-cols-2">
              <TextInput
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Label pendek (opsional)"
                aria-label="label"
              />
              <GroupPicker
                group={form.group}
                onGroup={(g) => setForm((f) => ({ ...f, group: g }))}
                newGroup={form.newGroup}
                onNewGroup={(v) => setForm((f) => ({ ...f, newGroup: v }))}
                groups={groups}
              />
            </div>
            <Button variant="cyan" size="sm" onClick={add} disabled={!form.prompt.trim()}>
              Simpan favorit
            </Button>
          </div>
        </div>
      )}

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <TextInput
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Cari kriteria / label / folder…"
          aria-label="cari favorit"
          className="max-w-xs !py-1.5 !text-xs"
        />
        <span className="font-mono text-[10px] text-white/35">
          {favorites.length} total · {ungrouped.length} inbox · {groups.length} folder
        </span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-20 font-mono text-xs text-white/30">
          <Spinner className="h-4 w-4" /> menyinkronkan favorit…
        </div>
      ) : favorites.length === 0 && !showForm ? (
        <div className="card-chrome rounded-xl border-dashed py-16 text-center">
          <Icon name="star" className="mx-auto h-8 w-8 text-gold" />
          <p className="mt-2 text-sm text-white/50">
            Belum ada favorit. Klik <span className="text-cyan-soft">+ Favorit baru</span> atau simpan kriteria
            dari panel <span className="font-mono text-cyan-soft/80">Custom AI criteria</span> di input.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          {ungrouped.length > 0 && (
            <div>
              <div className="mb-2 flex items-center gap-2">
                <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-white/35">Inbox</span>
                <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/50">{ungrouped.length}</span>
              </div>
              <div className="space-y-2">{ungrouped.map(row)}</div>
            </div>
          )}
          {groups.map((g) => {
            const items = groupedVisible.filter((f) => f.group === g)
            if (!items.length) return null
            return (
              <div key={g}>
                {folderHeader(g, items.length)}
                <div className="space-y-2">{items.map(row)}</div>
              </div>
            )
          })}
          {visible.length === 0 && (
            <p className="rounded border border-dashed border-border-soft py-10 text-center font-mono text-[10px] text-white/25">
              tidak ada hasil untuk "{q}"
            </p>
          )}
        </div>
      )}
    </div>
  )
}
