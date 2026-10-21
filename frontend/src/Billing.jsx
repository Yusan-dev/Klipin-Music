import { useEffect, useState } from 'react'
import { API, mockCheckout, mockApprove, mockPayEnabled, getBillingStatus } from './api.js'
import { Button, Micro, Mono, Panel, StatusDot } from './components/ui.jsx'

const rupiah = (n) => 'Rp ' + new Intl.NumberFormat('id-ID').format(n)

export default function Billing({ user, onSignIn }) {
  const [state, setState] = useState({ loading: true, enabled: false, plans: [], balance: null })
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  // ── Mock payment (dev/testing) state ──
  const [mockEnabled] = useState(mockPayEnabled())
  const [mockTx, setMockTx] = useState(null)
  const [mockMsg, setMockMsg] = useState('')

  const runMock = async (packId, method) => {
    setBusy(`mock:${packId}:${method}`)
    setMockMsg('')
    try {
      const d = await mockCheckout(packId, method)
      setMockTx({ ...d, method })
    } catch (e) {
      setMockMsg(`Gagal: ${e.message}`)
    } finally {
      setBusy('')
    }
  }

  const approveMock = async () => {
    setBusy('mock:approve')
    setMockMsg('')
    try {
      const d = await mockApprove(mockTx.transaction_id)
      setMockMsg(d.message || 'Approved ✓')
      // refresh balance
      try {
        const s = await getBillingStatus()
        setState((prev) => ({ ...prev, balance: s.balance }))
      } catch { /* ignore */ }
    } catch (e) {
      setMockMsg(`Gagal: ${e.message}`)
    } finally {
      setBusy('')
    }
  }

  useEffect(() => {
    if (!user) {
      setState((s) => ({ ...s, loading: false }))
      return
    }
    Promise.all([
      fetch(`${API}/billing/plans`, { headers: { Authorization: `Bearer ${localStorage.getItem('klipin_token')}` } }).then((r) => r.json()),
      fetch(`${API}/billing/status`, { headers: { Authorization: `Bearer ${localStorage.getItem('klipin_token')}` } }).then((r) => r.json()),
    ])
      .then(([plans, status]) => setState({
        loading: false,
        enabled: !!(plans.enabled && status.enabled) || mockEnabled,
        plans: plans.packs || plans.plans || [],
        balance: status.balance || null,
      }))
      .catch(() => setState((s) => ({ ...s, loading: false })))
  }, [user, mockEnabled])

  const checkout = async (planId, provider) => {
    setBusy(`${planId}:${provider}`)
    setMsg('')
    try {
      const res = await fetch(`${API}/billing/checkout`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('klipin_token')}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ plan_id: planId, provider }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'checkout failed')
      if (provider === 'stripe' && data.url) {
        window.location.href = data.url
      } else if (data.redirect_url) {
        window.open(data.redirect_url, '_blank')
        setMsg('Pembayaran dibuka di tab baru. Status kredit otomatis terisi setelah pembayaran lunas.')
      } else if (data.token && window.snap) {
        window.snap.pay(data.token)
      } else {
        setMsg('Pembayaran dibuat — konfirmasi otomatis lewat webhook.')
      }
    } catch (e) {
      setMsg(`Gagal: ${e.message}`)
    } finally {
      setBusy('')
    }
  }

  const b = state.balance
  const pct = b && b.credits_total > 0 ? Math.min(100, Math.round((b.credits_used / b.credits_total) * 100)) : 0

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
      <Micro className="text-center">SYS.BILLING — PREPAID CREDITS · TIDAK HANGUS</Micro>
      <h2 className="mt-2 text-center text-xl font-bold tracking-tight">Kredit Prabayar</h2>
      <p className="mt-1 text-center font-mono text-xs text-white/40">
        Kredit = 1 video diproses · kredit tidak pernah hangus · kredit balik otomatis kalau render gagal
      </p>
      <p className="mt-2 text-center font-mono text-[11px] text-gold/80">
        Gratis hingga 3 klip/video · paket berbayar hingga 6 klip/video · hanya AI score &gt;90 yang dirender
      </p>

      {!user && (
        <Panel label="SIGN IN REQUIRED" className="mt-6" bodyClassName="text-center">
          <p className="mb-4 font-mono text-xs text-white/45">Masuk untuk melihat paket dan saldo kredit Anda.</p>
          <Button onClick={onSignIn}>Masuk dengan Google</Button>
        </Panel>
      )}

      {user && state.loading && (
        <div className="mt-10 flex justify-center">
          <Mono className="text-cyan/60">loading…</Mono>
        </div>
      )}

      {user && !state.loading && !state.enabled && (
        <Panel label="BILLING — DISABLED" dot="idle" className="mt-6">
          <p className="font-mono text-xs text-white/50">
            Tagihan belum diaktifkan di server ini — semua fitur berjalan <span className="text-cyan">gratis</span> tanpa batas
            kredit (mode lokal / self-host). Untuk mengaktifkan, set <span className="text-white/80">BILLING_ENABLED=1</span> dan isi
            kunci payment gateway di <span className="text-white/80">.env</span>.
          </p>
        </Panel>
      )}

      {user && !state.loading && state.enabled && b && (
        <Panel label="CREDIT BALANCE — PERMANENT" dot="ok" className="mt-6">
          <div className="flex flex-wrap items-center gap-6">
            <div>
              <Mono className="text-2xl text-cyan">{b.credits_left}</Mono>
              <Mono className="block text-[10px] uppercase tracking-[0.2em] text-white/40">
                kredit tersisa · tidak hangus
              </Mono>
            </div>
            <div className="min-w-40 flex-1">
              <div className="h-2.5 overflow-hidden rounded-full bg-navy-2">
                <div className="h-full rounded-full bg-cyan/80 transition-all" style={{ width: `${pct}%` }} />
              </div>
              <div className="mt-1.5 flex justify-between font-mono text-[10px] uppercase tracking-widest text-white/35">
                <span>{b.credits_used} dipakai</span>
                <span>{b.free_tier
                  ? (b.free_used
                    ? 'kuota gratis bulan ini habis'
                    : `kuota gratis ${b.free_monthly_limit}x bulan ini 🎁`)
                  : 'kredit permanen aktif'}</span>
              </div>
            </div>
          </div>
        </Panel>
      )}

      {user && !state.loading && state.enabled && (
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {state.plans.map((p) => (
            <Panel key={p.id} label={p.name.toUpperCase()} dot="idle" className="flex flex-col">
              <div className="flex items-baseline gap-1.5">
                <Mono className="text-2xl">{p.price_idr ? rupiah(p.price_idr) : 'Gratis'}</Mono>
                {p.price_idr > 0 && <Mono className="text-[10px] text-white/40">sekali bayar</Mono>}
              </div>
              <Mono className="mt-0.5 text-[11px] text-gold">
                {p.credits ? `${p.credits} kredit permanen` : ''}
              </Mono>
              <ul className="mt-3 flex-1 space-y-1.5">
                {p.features.map((f) => (
                  <li key={f} className="flex gap-2 font-mono text-[11px] text-white/60">
                    <span className="text-cyan">▸</span>{f}
                  </li>
                ))}
              </ul>
              <div className="mt-4 space-y-2">
                <Button
                  variant="primary"
                  size="sm"
                  className="w-full"
                  disabled={busy === (mockEnabled ? `mock:${p.id}:card` : `${p.id}:stripe`)}
                  onClick={() => mockEnabled ? runMock(p.id, 'card') : checkout(p.id, 'stripe')}
                >
                  {busy === (mockEnabled ? `mock:${p.id}:card` : `${p.id}:stripe`) ? '…' : 'Bayar dengan Kartu'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full"
                  disabled={busy === (mockEnabled ? `mock:${p.id}:qris` : `${p.id}:midtrans`)}
                  onClick={() => mockEnabled ? runMock(p.id, 'qris') : checkout(p.id, 'midtrans')}
                >
                  {busy === (mockEnabled ? `mock:${p.id}:qris` : `${p.id}:midtrans`) ? '…' : 'QRIS / Bank / E-wallet'}
                </Button>
                {mockEnabled && (
                  <Button variant="ghost" size="sm" className="w-full" onClick={() => runMock(p.id, 'payment_link')}>
                    Buat Payment Link
                  </Button>
                )}
              </div>
            </Panel>
          ))}
        </div>
      )}

      {mockEnabled && !mockTx && (
        <p className="mt-5 rounded border border-gold/30 bg-gold/5 p-3 text-center font-mono text-[11px] text-gold">
          TEST PAYMENT MODE · tidak ada uang sungguhan yang ditagihkan
        </p>
      )}

      {mockEnabled && mockTx && (
        <Panel label="KLIPIN CHECKOUT — TEST MODE" dot="idle" className="mt-6">
          <div className="rounded border border-gold/30 bg-gold/5 px-3 py-2 text-center font-mono text-[10px] font-bold tracking-widest text-gold">
            SIMULASI STAGING · BUKAN PEMBAYARAN NYATA
          </div>
          <div className="mt-4 flex items-start justify-between gap-4 border-b border-white/10 pb-4">
            <div><Mono className="text-cyan">{mockTx.pack.name}</Mono><Micro>{mockTx.pack.credits} KREDIT PERMANEN</Micro></div>
            <Mono className="text-lg">{rupiah(mockTx.pack.price_idr)}</Mono>
          </div>

          {mockTx.method === 'qris' && (
            <div className="mx-auto mt-5 max-w-xs text-center">
              <div className="mx-auto grid h-44 w-44 grid-cols-9 gap-1 rounded bg-white p-3 shadow-[0_0_30px_rgba(24,216,255,.15)]" aria-label="Simulasi kode QRIS">
                {Array.from({ length: 81 }, (_, i) => <span key={i} className={(i * 7 + Math.floor(i / 9) * 3) % 5 < 2 ? 'bg-navy' : 'bg-white'} />)}
              </div>
              <Mono className="mt-3 block text-sm">QRIS</Mono>
              <p className="font-mono text-[10px] text-white/40">Pindai dengan aplikasi pembayaran (simulasi)</p>
            </div>
          )}

          {mockTx.method === 'card' && (
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <label className="sm:col-span-2 font-mono text-[10px] text-white/50">NOMOR KARTU<input readOnly value="4242 4242 4242 4242" className="mt-1 w-full rounded border border-white/15 bg-navy px-3 py-2 text-white outline-none" /></label>
              <label className="font-mono text-[10px] text-white/50">BERLAKU<input readOnly value="12 / 30" className="mt-1 w-full rounded border border-white/15 bg-navy px-3 py-2 text-white outline-none" /></label>
              <label className="font-mono text-[10px] text-white/50">CVC<input readOnly value="123" className="mt-1 w-full rounded border border-white/15 bg-navy px-3 py-2 text-white outline-none" /></label>
            </div>
          )}

          {mockTx.method === 'payment_link' && (
            <div className="mt-5 rounded border border-cyan/20 bg-cyan/5 p-4">
              <Micro>PAYMENT LINK</Micro>
              <Mono className="mt-2 block break-all text-cyan-soft">https://pay.klip-in.id/test/{mockTx.transaction_id}</Mono>
              <p className="mt-2 font-mono text-[10px] text-white/40">Tautan simulasi untuk QA alur checkout dan callback.</p>
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <Button variant="primary" size="sm" onClick={approveMock} disabled={busy === 'mock:approve'}>
              {busy === 'mock:approve' ? 'Memproses…' : 'Simulasikan Pembayaran Berhasil'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => { setMockTx(null); setMockMsg('') }}>Batalkan</Button>
          </div>
          <Micro className="mt-3">TX {mockTx.transaction_id} · PENDING</Micro>
          {mockMsg && <p className="mt-3 font-mono text-[11px] text-gold/80">{mockMsg}</p>}
        </Panel>
      )}

      {msg && (
        <p className="mt-6 rounded-md border border-cyan/20 bg-cyan/5 p-3 text-center font-mono text-xs text-cyan-soft">
          {msg}
        </p>
      )}

      <p className="mt-8 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-white/25">
        Pembayaran diproses kartu, QRIS, bank & e-wallet · kredit diperbarui otomatis · 1 klip pertama gratis
      </p>
    </div>
  )
}
