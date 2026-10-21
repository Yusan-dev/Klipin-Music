import { useCallback, useEffect, useRef, useState } from 'react'
import {
  disconnectPlatform, getPublishAuthUrl, getPublishConfig, getPublishStatus,
  listIdeas, timeAgo,
} from './api.js'
import {
  isSynced, lastSyncAt, loadFavorites, syncFavorites,
} from './criteriaStore.js'
import {
  lastKitSyncAt, loadKit, saveKit, syncBrandKit,
} from './brandKitStore.js'
import { Button, Micro, Panel, Select, Spinner, StatusDot, TextInput } from './components/ui.jsx'
import Icon from './components/Icon.jsx'
import { FONTS } from './components/CaptionSettings.jsx'
import { showToast } from './toast.js'

const PLATFORMS = [
  { id: 'youtube', label: 'YouTube Shorts', dot: '#FF0033' },
  { id: 'tiktok', label: 'TikTok', dot: '#69C9D0' },
  { id: 'instagram', label: 'Instagram Reels', dot: '#E1306C' },
]

const TABS = [
  { id: 'account', label: 'ACCOUNT' },
  { id: 'publish', label: 'PUBLISH' },
  { id: 'brand', label: 'BRAND KIT' },
  { id: 'system', label: 'SYSTEM' },
]

// Kept for InputStage (caption defaults) — now backed by the synced store.
export function loadBrandKit() {
  return loadKit()
}

export default function Settings({ user, authChecked, onSignIn, onSignOut }) {
  const [tab, setTab] = useState('account')
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
      <Micro className="block text-center">SYS.SETTINGS — CONTROL PANEL</Micro>
      <h1 className="text-chrome mt-2 text-center font-display text-2xl font-extrabold tracking-wide sm:text-3xl">
        Settings
      </h1>

      <div className="mt-6 grid grid-cols-2 gap-1.5 sm:grid-cols-4" role="tablist" aria-label="settings sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md border px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em] transition-all ${
              tab === t.id
                ? 'border-cyan/70 bg-cyan/10 text-cyan-soft shadow-neon-cyan'
                : 'border-border-soft bg-navy-2/60 text-white/40 hover:border-cyan/40 hover:text-white/70'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {tab === 'account' && <AccountTab user={user} authChecked={authChecked} onSignIn={onSignIn} onSignOut={onSignOut} />}
        {tab === 'publish' && <PublishTab />}
        {tab === 'brand' && <BrandKitTab />}
        {tab === 'system' && <SystemTab />}
      </div>
    </div>
  )
}

function AccountTab({ user, authChecked, onSignIn, onSignOut }) {
  return (
    <div className="space-y-4">
      <Panel label="Account" dot={user ? 'ok' : 'idle'}>
        {authChecked && user ? (
          <div className="flex flex-wrap items-center gap-4">
            {user.picture && (
              <img src={user.picture} alt="" referrerPolicy="no-referrer" className="h-14 w-14 rounded-full border border-cyan/40" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-base font-semibold text-white">{user.name || '—'}</div>
              <div className="truncate font-mono text-xs text-white/40">{user.email || '—'}</div>
            </div>
            <Button variant="ghost" onClick={onSignOut}>Sign out</Button>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-white/50">
              {authChecked ? 'You are browsing as a guest.' : 'Checking session…'}
            </p>
            {authChecked && !user && <Button onClick={onSignIn}>Masuk</Button>}
          </div>
        )}
      </Panel>
      <SyncStatus user={user} onSignIn={onSignIn} />
    </div>
  )
}

/**
 * Overall sync health panel: what lives where (account vs this device).
 * Refreshes on mount + on the klipin:criteria-synced event + manual Sync.
 */
function SyncStatus({ user, onSignIn }) {
  const [favorites, setFavorites] = useState(loadFavorites)
  const [ideasCount, setIdeasCount] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncTick, setSyncTick] = useState(0)

  const pullIdeas = () => {
    if (!user) { setIdeasCount(null); return }
    listIdeas().then((d) => setIdeasCount((d.ideas || []).length)).catch(() => setIdeasCount(null))
  }

  useEffect(() => {
    let alive = true
    syncFavorites().then((list) => { if (alive) setFavorites(list) })
    pullIdeas()
    const onSync = () => setSyncTick((t) => t + 1)
    window.addEventListener('klipin:criteria-synced', onSync)
    window.addEventListener('klipin:brand-kit-synced', onSync)
    return () => {
      alive = false
      window.removeEventListener('klipin:criteria-synced', onSync)
      window.removeEventListener('klipin:brand-kit-synced', onSync)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])
  void syncTick // favorites + badge state are read fresh from the store each render

  const syncNow = async () => {
    setSyncing(true)
    try {
      setFavorites(await syncFavorites())
      await pullIdeas()
      showToast('✓ Favorit tersinkron ke akun')
    } catch {
      /* failure toast already emitted by the store */
    } finally {
      setSyncing(false)
    }
  }

  const synced = favorites.filter((f) => isSynced(f.prompt)).length
  const localOnly = favorites.length - synced
  const last = lastSyncAt()

  return (
    <Panel
      label="Sync status"
      dot={user ? (synced === favorites.length && favorites.length > 0 ? 'ok' : 'run') : 'idle'}
      right={
        <Button variant="ghost" size="sm" onClick={syncNow} disabled={syncing}>
          {syncing ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="sync" className="h-3.5 w-3.5" />} Sync sekarang
        </Button>
      }
      bodyClassName="space-y-2.5 p-4"
    >
      <p className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em]">
        <StatusDot tone={user ? 'ok' : 'idle'} />
        <span className="text-white/60">{user ? `akun ${user.name || user.email}` : 'guest — semua data lokal'}</span>
        {last && <span className="ml-auto normal-case tracking-normal text-white/30">last sync {timeAgo(last / 1000)}</span>}
      </p>

      <SyncRow
        label="Favorit criteria"
        status={user ? (favorites.length ? (synced === favorites.length ? 'tersinkron' : `${synced} di akun · ${localOnly} lokal`) : 'kosong') : `${favorites.length} lokal`}
        tone={favorites.length === 0 ? 'idle' : synced === favorites.length ? 'ok' : 'run'}
        hint="Akun: lintas perangkat · lokal: browser ini saja"
      />
      <SyncRow
        label="Ideas board"
        status={user ? (ideasCount === null ? 'memuat…' : `${ideasCount} item di server`) : '— butuh login'}
        tone={user && ideasCount ? 'ok' : 'idle'}
        hint="Selalu tersimpan di akun"
      />
      <SyncRow
        label="Brand kit"
        status={lastKitSyncAt() ? `tersinkron · ${timeAgo(lastKitSyncAt() / 1000)}` : 'tersimpan di browser ini'}
        tone={lastKitSyncAt() ? 'ok' : 'idle'}
        hint="Preferensi tampilan — kini tersinkron lintas perangkat"
      />
      <SyncRow
        label="Publish connections"
        status={user ? 'di akun (lihat tab PUBLISH)' : '— butuh login'}
        tone={user ? 'ok' : 'idle'}
      />

      {!user && (
        <p className="border-t border-border-soft pt-2.5 text-xs text-white/40">
          <button type="button" onClick={onSignIn} className="text-cyan-soft underline-offset-2 hover:underline">
            Masuk dengan Google
          </button>{' '}
          untuk menyinkronkan favorit criteria ke akun dan mengaksesnya dari perangkat lain.
        </p>
      )}
    </Panel>
  )
}

function SyncRow({ label, status, tone, hint }) {
  return (
    <div className="flex items-center gap-2.5 rounded-md border border-border-soft bg-navy-2/40 px-3 py-2">
      <StatusDot tone={tone} />
      <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-white/45">{label}</span>
      <span className="ml-auto text-right font-mono text-[11px] text-white/75">{status}</span>
      {hint && <span className="hidden w-full basis-full pl-6 font-mono text-[9px] text-white/25 sm:block">{hint}</span>}
    </div>
  )
}

function PublishTab() {
  const [config, setConfig] = useState(null)
  const [statuses, setStatuses] = useState({})
  const [connecting, setConnecting] = useState('')
  const connectTimer = useRef(null)

  const refresh = useCallback(async () => {
    const cfg = await getPublishConfig().catch(() => null)
    setConfig(cfg)
    const st = {}
    for (const p of PLATFORMS) {
      if (cfg?.[p.id]?.configured) {
        st[p.id] = await getPublishStatus(p.id).catch(() => null)
      }
    }
    setStatuses(st)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Cleanup OAuth polling timer on unmount.
  useEffect(() => {
    return () => { if (connectTimer.current) clearInterval(connectTimer.current) }
  }, [])

  const connect = async (platform) => {
    setConnecting(platform)
    try {
      const { url } = await getPublishAuthUrl(platform)
      const win = window.open(url, 'klipin_oauth', 'width=600,height=720')
      const deadline = Date.now() + 180_000
      if (connectTimer.current) clearInterval(connectTimer.current)
      connectTimer.current = setInterval(async () => {
        if (Date.now() > deadline || (win && win.closed)) {
          clearInterval(connectTimer.current)
          connectTimer.current = null
          setConnecting('')
          await refresh()
          return
        }
        try {
          const st = await getPublishStatus(platform)
          if (st.connected) {
            clearInterval(connectTimer.current)
            connectTimer.current = null
            setConnecting('')
            setStatuses((s) => ({ ...s, [platform]: st }))
          }
        } catch { /* transient */ }
      }, 1200)
    } catch {
      setConnecting('')
    }
  }

  const disconnect = async (platform) => {
    await disconnectPlatform(platform).catch(() => {})
    await refresh()
  }

  return (
    <Panel label="Auto-publish connections" dot="brand" bodyClassName="space-y-3 p-4">
      {PLATFORMS.map((p) => {
        const cfg = config?.[p.id]
        const st = statuses[p.id]
        const busy = connecting === p.id
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
                <span className="font-mono text-xs text-white/30">missing env credentials — see backend/.env</span>
              ) : !cfg?.base_url_ok ? (
                <span className="font-mono text-xs text-warn">PUBLIC_BASE_URL not set — needed for OAuth</span>
              ) : !st?.connected ? (
                <Button variant="ghost" size="sm" onClick={() => connect(p.id)} disabled={!!connecting}>
                  {busy ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="link" className="h-3.5 w-3.5" />} Connect
                </Button>
              ) : (
                <Button variant="danger" size="sm" onClick={() => disconnect(p.id)}>Disconnect</Button>
              )}
            </div>
          </div>
        )
      })}
      <p className="font-mono text-[10px] text-white/30">
        OAuth redirect URI: {'{PUBLIC_BASE_URL}/api/publish/callback/{platform}'}
      </p>
    </Panel>
  )
}

const COLORS = ['#FFE500', '#FFFFFF', '#5EEAD4', '#7C8CF8', '#F472B6', '#4ADE80']
const FONT_SIZE_MIN = 12
const FONT_SIZE_MAX = 100

function BrandKitTab() {
  const [kit, setKit] = useState(() => loadKit())
  const [saved, setSaved] = useState(false)
  const [syncTick, setSyncTick] = useState(0)

  // Pull the account's kit on mount; refresh the sync badge on changes.
  useEffect(() => {
    let alive = true
    syncBrandKit().then((k) => { if (alive) setKit(k) })
    const onSync = () => setSyncTick((t) => t + 1)
    window.addEventListener('klipin:brand-kit-synced', onSync)
    return () => { alive = false; window.removeEventListener('klipin:brand-kit-synced', onSync) }
  }, [])
  void syncTick

  const set = (patch) => setKit((k) => ({ ...k, ...patch }))

  const save = () => {
    saveKit(kit)
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  return (
    <Panel label="Brand kit defaults" dot="magenta" bodyClassName="space-y-4 p-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Micro>Default caption color</Micro>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {COLORS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => set({ color: c })}
                className={`h-7 w-7 rounded-md border transition ${kit.color === c ? 'border-white ring-2 ring-brand/60' : 'border-border'}`}
                style={{ background: c }}
                aria-label={`caption color ${c}`}
              />
            ))}
          </div>
        </div>
        <div>
          <Micro>Default font</Micro>
          <Select
            value={FONTS.includes(kit.caption_font) ? kit.caption_font : 'Montserrat'}
            onChange={(e) => set({ caption_font: e.target.value })}
            className="mt-1.5"
          >
            {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
          </Select>
        </div>
        <div>
          <Micro>Default size</Micro>
          <div className="mt-1.5 flex items-center gap-2">
            <input
              type="range"
              min={FONT_SIZE_MIN}
              max={FONT_SIZE_MAX}
              step={1}
              value={kit.caption_size || 72}
              onChange={(e) => set({ caption_size: Number(e.target.value) })}
              className="w-full accent-cyan"
              aria-label="default caption size"
            />
            <span className="w-12 shrink-0 text-right font-mono text-sm text-cyan-soft">
              {kit.caption_size || 72}px
            </span>
          </div>
        </div>
        <div>
          <Micro>Default watermark</Micro>
          <TextInput value={kit.watermark || ''} onChange={(e) => set({ watermark: e.target.value })} placeholder="@yourhandle" className="mt-1.5" />
        </div>
      </div>

      <label className="flex cursor-pointer items-center gap-2.5 text-sm text-white/70">
        <input
          type="checkbox"
          checked={kit.show_klipin_watermark !== false}
          onChange={(e) => set({ show_klipin_watermark: e.target.checked })}
          className="h-4 w-4 rounded accent-cyan"
        />
        <span className="inline-flex items-center gap-1 text-sm text-white/70" title="Tampilkan watermark 'Klip-in' di tengah video (gratis & Mulai)">
          Tampilkan watermark Klip-in
        </span>
      </label>

      <label className="flex cursor-pointer items-center gap-2.5 text-sm text-white/70">
        <input
          type="checkbox"
          checked={kit.emoji !== false}
          onChange={(e) => set({ emoji: e.target.checked })}
          className="h-4 w-4 rounded accent-cyan"
        />
        <span className="inline-flex items-center gap-1 text-sm text-white/70" title="Emoji otomatis dari kata kunci multi-bahasa (uang→💰, wow→🤯, menang→🏆)">
          Smart emojis <Icon name="sparkles" className="h-3.5 w-3.5" />
        </span>
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={save}>{saved ? '✓ Saved' : 'Save defaults'}</Button>
        <span
          className="font-mono text-[10px]"
          title={lastKitSyncAt() ? 'Tersinkron ke akun — berlaku lintas perangkat' : 'Hanya tersimpan di perangkat ini — masuk untuk sinkron'}
        >
          {lastKitSyncAt() ? <span className="inline-flex items-center gap-1 text-cyan-soft/70"><Icon name="cloud" className="h-3 w-3" /> tersinkron ke akun</span> : <span className="inline-flex items-center gap-1 text-white/30"><Icon name="upload" className="h-3 w-3" /> lokal di perangkat ini</span>}
        </span>
        <span className="font-mono text-[10px] text-white/30">applied to new clips only</span>
      </div>
    </Panel>
  )
}

function SystemTab() {
  const [config, setConfig] = useState(null)
  useEffect(() => {
    getPublishConfig().then(setConfig).catch(() => setConfig(null))
  }, [])

  const configured = PLATFORMS.filter((p) => config?.[p.id]?.configured)

  return (
    <Panel label="System" dot="run" bodyClassName="space-y-3 p-4">
      <Row k="Platform" v="Klip-in — AI video clipper" />
            <Row k="Version" v="v1.0" />
            <Row k="Render" v="Otomatis di server (cloud)" />
            <Row k="Biaya" v="≈ $0.0015 / 8-menit video" />
            <Row k="Publish configured" v={configured.length ? configured.map((p) => p.id).join(', ') : 'none'} />
            <p className="border-t border-border-soft pt-3 font-mono text-[10px] text-white/30">
              Semua proses transkripsi, analisis, dan render ditangani otomatis oleh AI di server kami.
            </p>
    </Panel>
  )
}

function Row({ k, v }) {
  return (
    <div className="flex items-center justify-between gap-4 font-mono text-xs">
      <span className="uppercase tracking-[0.15em] text-white/35">{k}</span>
      <span className="text-right text-white/75">{v}</span>
    </div>
  )
}
