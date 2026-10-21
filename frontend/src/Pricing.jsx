import { useEffect, useState } from 'react'
import { Button, Micro, StatusDot } from './components/ui.jsx'
import { getPricing } from './api.js'
import { initLocale, setLocale as persistLocale, getLocale, t } from './i18n.js'

/** Module: Pricing — multi-currency, bilingual (EN default). */
export default function Pricing({ onStart }) {
  const [open, setOpen] = useState(0)
  const [locale, setLocale] = useState(getLocale)
  const [currency, setCurrency] = useState('USD')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => { initLocale() }, [])

  useEffect(() => {
    let cancelled = false
    setErr('')
    getPricing(currency)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e) => { if (!cancelled) setErr(e.message) })
    return () => { cancelled = true }
  }, [currency])

  const switchLocale = (v) => { setLocale(persistLocale(v)) }

  const packs = (data?.packs || []).map((p, i) => ({
    key: p.id,
    name: p.name,
    price: p.price,
    price_per_credit: p.price_per_credit,
    credits: `${p.credits} ${t('credits_lbl')}`,
    perks: perkLines(p.id, locale),
    popular: i === 1,
  }))
  const lowest = (data?.packs && data.packs.length)
    ? data.packs.reduce((m, p) => (p.price_per_credit < m.price_per_credit ? p : m)).price : null

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      <header className="text-center">
        <div className="mb-4 flex items-center justify-center gap-2">
          {['en', 'id'].map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => switchLocale(l)}
              aria-pressed={locale === l}
              className={`rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-wider transition ${
                locale === l
                  ? 'border-cyan bg-cyan/15 text-cyan'
                  : 'border-border bg-navy-2 text-white/40 hover:border-cyan/50 hover:text-white/75'
              }`}
            >
              {l === 'en' ? 'EN' : 'ID'}
            </button>
          ))}
        </div>
        <Micro className="inline-flex items-center gap-2 rounded-full border border-gold/40 bg-gold/10 px-4 py-1.5 text-gold">
          <StatusDot tone="run" pulse />
          {t('pricing_eyebrow')}
        </Micro>
        <h1 className="mx-auto mt-4 max-w-3xl font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
          <span className="text-chrome">{t('pricing_head1')}</span>{' '}
          <span className="text-neon">{t('pricing_head2')}</span>
        </h1>
        <p className="mx-auto mt-3 max-w-2xl text-white/50">{t('pricing_sub')}</p>
        <p className="mx-auto mt-2 max-w-2xl font-mono text-[11px] text-cyan-soft/70">
          {t('pricing_cheapest')}
        </p>
      </header>

      <section id="harga" className="py-12">
        <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
            {t('pricing_currency_note')}
          </span>
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            aria-label="currency"
            className="rounded border border-border bg-navy-2 px-2 py-1 font-mono text-[11px] text-white outline-none focus:border-cyan"
          >
            {(data?.currencies || ALL_CURRENCIES).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>

        {err && (
          <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 p-2.5 text-center font-mono text-xs text-danger">
            {err}
          </p>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          {packs.map((p, i) => (
            <div
              key={p.key}
              className={`card-chrome relative rounded-xl p-6 ${p.popular ? 'border-magenta/50 shadow-neon-magenta' : ''}`}
            >
              {p.popular && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full border border-magenta/50 bg-navy px-3 py-0.5 font-mono text-[9px] uppercase tracking-widest text-magenta-soft">
                  {t('pricing_best')}
                </span>
              )}
              <div className="font-display text-sm font-bold tracking-widest text-cyan-soft">{p.name}</div>
              <div className="mt-3 font-display text-3xl font-extrabold text-white">
                {p.price}
                <span className="ml-1 text-sm font-normal text-white/40">{t('price_per_credit')}</span>
              </div>
              <div className="mt-1 font-mono text-[11px] text-gold">{p.price_per_credit}</div>
              <div className="mt-1 font-mono text-[11px] text-white/35">{p.credits}</div>
              <ul className="mt-5 space-y-2">
                {p.perks.map((perk) => (
                  <li key={perk} className="flex items-start gap-2 text-sm text-white/60">
                    <span className="led mt-1.5 h-1 w-1 shrink-0 bg-cyan text-cyan" />
                    {perk}
                  </li>
                ))}
              </ul>
              <Button variant={p.popular ? 'magenta' : 'ghost'} className="mt-6 w-full" onClick={() => onStart()}>
                {t('pricing_start')}
              </Button>
            </div>
          ))}
        </div>
        <p className="mt-5 text-center font-mono text-[11px] uppercase tracking-[0.15em] text-white/30">
          {t('pricing_methods')}
        </p>
        <p className="mt-2 text-center font-mono text-[10px] text-white/25">
          {t('pricing_checkout_note')}
        </p>
      </section>

      <Testimonials />
      <Faq open={open} setOpen={setOpen} lowest={lowest} />
    </div>
  )
}
function perkLines(packId, locale) {
  const en = {
    mulai: ['Up to 6 clips per video', 'Only AI score >90 rendered', 'Non-expiring credits', 'All layout modes', 'Karaoke subtitles', 'Klipin watermark'],
    kreator: ['Up to 6 clips per video', 'Only AI score >90 rendered', 'Everything in MULAI', 'Free cut editor', 'Clean & SFX & face swap', 'No watermark', 'Export all aspects'],
    studio: ['Up to 6 clips per video', 'Only AI score >90 rendered', 'Everything in KREATOR', 'Auto-publish scheduler', 'Brand template', 'Priority rendering'],
  }
  const id = {
    mulai: ['Hingga 6 klip per video', 'Hanya render AI score >90', 'Kredit tidak hangus', 'Semua mode layout', 'Subtitle karaoke', 'Watermark Klipin'],
    kreator: ['Hingga 6 klip per video', 'Hanya render AI score >90', 'Semua fitur MULAI', 'Editor cut bebas', 'Clean & SFX & face swap', 'Tanpa watermark', 'Export semua aspect'],
    studio: ['Hingga 6 klip per video', 'Hanya render AI score >90', 'Semua fitur KREATOR', 'Auto-publish scheduler', 'Brand template', 'Prioritas render'],
  }
  return (locale === 'id' ? id : en)[packId] || []
}

function Testimonials() {
  const isId = getLocale() === 'id'
  const items = isId
    ? [
        { name: 'Rizky Pratama', initials: 'RP', role: 'Clipper TikTok · Podcast', quote: 'Satu episode 2 jam jadi 5 klip dalam 15 menit. Biaya-nya kejutan — di bawah seribu perak per klip.' },
        { name: 'Sari Wulandari', initials: 'SW', role: 'Reviewer Film', quote: 'Hook score-nya akurat banget. Klip yang nilainya tinggi memang yang langsung naik pas di-publish.' },
        { name: 'Andi Firmansyah', initials: 'AF', role: 'Streamer', quote: 'Mode Gameplay + karaoke langsung jadi. Upload ke Shorts tiap hari tanpa edit manual.' },
      ]
    : [
        { name: 'Rizky Pratama', initials: 'RP', role: 'TikTok Clipper · Podcast', quote: 'A 2-hour episode becomes 5 clips in 15 minutes. The cost is a surprise — well under a dollar per clip.' },
        { name: 'Sari Wulandari', initials: 'SW', role: 'Film Reviewer', quote: 'The hook score is spot-on. High-scoring clips are the ones that pop as soon as they post.' },
        { name: 'Andi Firmansyah', initials: 'AF', role: 'Streamer', quote: 'Gameplay mode + karaoke done in one click. Posting Shorts daily with zero manual editing.' },
      ]
  return (
    <section className="py-12">
      <SectionHead
        index="01"
        title={t('pricing_testimonials')}
        sub={t('pricing_testimonials_sub')}
      />
      <div className="mt-10 grid gap-4 md:grid-cols-3">
        {items.map((test) => (
          <figure key={test.name} className="card-chrome rounded-xl p-5">
            <div className="flex gap-0.5 text-gold" aria-label="5 dari 5">
              {[0, 1, 2, 3, 4].map((star) => (<span key={star}>★</span>))}
            </div>
            <blockquote className="mt-3 text-sm leading-relaxed text-white/65">“{test.quote}”</blockquote>
            <figcaption className="mt-4 flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full border border-cyan/40 bg-cyan/10 font-display text-xs font-bold text-cyan-soft">
                {test.initials}
              </div>
              <div>
                <div className="text-sm font-semibold text-white">{test.name}</div>
                <div className="font-mono text-[10px] text-white/40">{test.role}</div>
              </div>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}

function Faq({ open, setOpen, lowest }) {
  const faqs = [
    { q: t('faq_cookie_q'), a: t('faq_cookie_a') },
    { q: t('faq_input_q'), a: t('faq_input_a') },
    { q: t('faq_cost_q'), a: t('faq_cost_a', { lowest: lowest || '(price)' }) },
    { q: t('faq_ai_q'), a: t('faq_ai_a') },
    { q: t('faq_now_q'), a: t('faq_now_a') },
  ]
  return (
    <section id="faq" className="mx-auto max-w-3xl py-12">
      <SectionHead index="02" title={t('pricing_faq')} sub="" />
      <div className="mt-8 space-y-3">
        {faqs.map((f, i) => (
          <div key={f.q} className="card-chrome overflow-hidden rounded-xl">
            <button
              type="button"
              onClick={() => setOpen(open === i ? -1 : i)}
              className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
            >
              <span className="font-display text-sm font-bold tracking-wide text-white">{f.q}</span>
              <span className={`font-mono text-cyan-soft transition-transform ${open === i ? 'rotate-45' : ''}`}>+</span>
            </button>
            {open === i && (
              <p className="px-5 pb-4 text-sm leading-relaxed text-white/55">{f.a}</p>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function SectionHead({ index, title, sub }) {
  return (
    <div className="text-center">
      <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-magenta-soft">/{index}</div>
      <h2 className="mx-auto mt-2 max-w-2xl font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
        {title}
      </h2>
      {sub && <p className="mx-auto mt-3 max-w-2xl text-white/50">{sub}</p>}
    </div>
  )
}

const ALL_CURRENCIES = ['IDR', 'USD', 'EUR', 'GBP', 'SGD', 'MYR', 'INR', 'PHP', 'BRL']
