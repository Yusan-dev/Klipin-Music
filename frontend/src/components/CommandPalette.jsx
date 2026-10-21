import { useEffect, useMemo, useRef, useState } from 'react'
import Icon from './Icon.jsx'

const ITEMS = [
  { label: 'Dashboard', hint: 'beranda & statistik', path: '#/', icon: 'dashboard', keywords: 'home beranda' },
  { label: 'Buat Klip', hint: 'tempel link video baru', path: '#/clip', icon: 'plus', keywords: 'new clip video buat render' },
  { label: 'Library', hint: 'semua project tersimpan', path: '#/library', icon: 'library', keywords: 'projects riwayat history' },
  { label: 'Music Studio', hint: 'buat lagu AI, cover & riwayat', path: '#/music', icon: 'music', keywords: 'music lagu cover create buat lagu ai upload mulacover remix riwayat' },
  { label: 'Ideas', hint: 'papan konten kanban', path: '#/ideas', icon: 'ideas', keywords: 'ide konten kanban draft' },
  { label: 'Favorit', hint: 'kriteria AI tersimpan', path: '#/favorites', icon: 'favorites', keywords: 'criteria favorit preset folder' },
  { label: 'Fitur', hint: 'semua kemampuan klipin', path: '#/features', icon: 'features', keywords: 'features layout karaoke broll dub' },
  { label: 'Harga', hint: 'kredit prabayar', path: '#/pricing', icon: 'pricing', keywords: 'pricing kredit harga topup' },
  { label: 'Tagihan', hint: 'riwayat & langganan', path: '#/billing', icon: 'billing', keywords: 'billing tagihan payment invoice' },
  { label: 'Pengaturan', hint: 'sinkron & preferensi', path: '#/settings', icon: 'settings', keywords: 'settings sinkron sync preferensi' },
]

/**
 * Global quick-nav palette (Ctrl/Cmd+K). Pure navigation — heavy lifting
 * stays in the pages. Keyboard-first: arrows + enter, esc to close.
 */
export default function CommandPalette({ open, onClose, onNavigate }) {
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  const results = useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return ITEMS
    return ITEMS.filter((it) => `${it.label} ${it.hint} ${it.keywords}`.toLowerCase().includes(query))
  }, [q])

  useEffect(() => {
    if (!open) return
    setQ('')
    setActive(0)
    const t = setTimeout(() => inputRef.current?.focus(), 0)
    return () => clearTimeout(t)
  }, [open])

  useEffect(() => { setActive(0) }, [q])

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const pick = (it) => {
    onClose()
    onNavigate(it.path)
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && results[active]) {
      e.preventDefault()
      pick(results[active])
    }
  }

  return (
    <div className="fixed inset-0 z-[70]" role="dialog" aria-modal="true" aria-label="Navigasi cepat" onKeyDown={onKeyDown}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />

      <div className="card-chrome absolute inset-x-4 top-[14vh] mx-auto max-w-lg overflow-hidden rounded-xl shadow-neon-cyan">
        {/* search row */}
        <div className="flex items-center gap-2 border-b border-cyan/15 bg-navy-2/60 px-3.5 py-3">
          <Icon name="search" className="h-4 w-4 shrink-0 text-cyan" aria-hidden />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ke mana? Ketik nama modul…"
            aria-label="cari perintah"
            className="w-full bg-transparent text-sm text-white outline-none placeholder:text-white/30"
          />
          <kbd className="shrink-0 rounded border border-border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-white/40">
            esc
          </kbd>
        </div>

        {/* results */}
        <ul ref={listRef} className="max-h-72 overflow-y-auto p-1.5">
          {results.length === 0 && (
            <li className="px-3 py-6 text-center font-mono text-xs text-white/40">tidak ada hasil</li>
          )}
          {results.map((it, i) => (
            <li key={it.path}>
              <button
                type="button"
                data-active={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(it)}
                className={`flex w-full items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-left transition ${
                  i === active
                    ? 'border-cyan bg-cyan/10 text-white'
                    : 'border-transparent text-white/65'
                }`}
              >
                <span className={`shrink-0 ${i === active ? 'text-cyan' : 'text-white/45'}`} aria-hidden>
                  <Icon name={it.icon} className="h-5 w-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{it.label}</span>
                  <span className="block truncate font-mono text-[10px] uppercase tracking-widest text-white/35">
                    {it.hint}
                  </span>
                </span>
                {i === active && <Icon name="play" className="h-3 w-3 shrink-0 text-cyan/70" aria-hidden />}
              </button>
            </li>
          ))}
        </ul>

        {/* footer hints */}
        <div className="flex items-center gap-4 border-t border-border-soft bg-navy-2/40 px-3.5 py-2 font-mono text-[9px] uppercase tracking-widest text-white/30">
          <span>↑↓ pilih</span>
          <span>⏎ buka</span>
          <span className="ml-auto">ctrl k toggle</span>
        </div>
      </div>
    </div>
  )
}
