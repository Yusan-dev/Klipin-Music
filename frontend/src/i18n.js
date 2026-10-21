// Lightweight i18n without a library. Default locale is English (global
// market); Indonesian is the built-in second locale. The active locale is
// persisted in localStorage so a refreshing global visitor stays in English.
const DICT = {
  en: {
    pricing_eyebrow: 'PRICING — PREPAID CREDITS, NEVER EXPIRE',
    pricing_head1: 'Pay once,',
    pricing_head2: 'permanent credits',
    pricing_sub: 'Prepaid credit model — pay once, never expires. Your first clip is free for every new user.',
    pricing_cheapest: 'The cheapest professional clipper — a one-time pack, not a monthly subscription.',
    pricing_currency_note: 'Display currency', 
    pricing_checkout_note: 'Checkout is processed in IDR (Rupiah); other prices are shown for reference.',
    price_per_credit: 'per credit',
    pricing_methods: 'Credit card · Bank transfer · Card (via Stripe/Midtrans)',
    pricing_start: 'Start →',
    pricing_best: 'most popular',
    pricing_currency: 'Currency',
    pricing_testimonials: 'Used by creators worldwide',
    pricing_testimonials_sub: 'Podcasters, streamers, film reviewers — cut clips fast, effortlessly.',
    pricing_faq: 'Common questions',
    faq_cookie_q: 'Does Klipin ask for my YouTube cookies?',
    faq_cookie_a: 'Never. All rendering happens on our servers, just like Opus Clip — we never access your personal account.',
    faq_input_q: 'What can I process?',
    faq_input_a: 'YouTube/TikTok links or MP4/MOV/MKV files up to 2 GB. Roadmap: Google Drive, Zoom, Vimeo.',
    faq_cost_q: 'What does one video actually cost?',
    faq_cost_a: 'One credit = one processed video. From {lowest} per 10-credit pack — below the ~$25/video common elsewhere. Failed renders are auto-refunded.',
    faq_ai_q: 'How does AI pick the best moments?',
    faq_ai_a: 'AI analyzes the transcript + sampled frames to find the highest-emotion moments, then scores each clip (hook score) so you know which to post first.',
    faq_now_q: 'Can I use it right now?',
    faq_now_a: 'Yes — sign in with Google and get 1 free clip to try. Then buy a non-expiring prepaid credit pack.',
    credits_lbl: 'permanent credits',
    perk_all_prev: 'Everything in',
    perk_no_watermark: 'No watermark',
    perk_all_exports: 'Export all aspects',
    perk_auto_scheduler: 'Auto-publish scheduler',
  },
  id: {
    pricing_eyebrow: 'MODUL HARGA — KREDIT PRABAYAR, TIDAK HANGUS',
    pricing_head1: 'Bayar sekali,',
    pricing_head2: 'kredit permanen',
    pricing_sub: 'Model ala pasar Indonesia — kredit prabayar, sekali bayar, tidak pernah hangus. Klip pertama gratis untuk semua pengguna baru.',
    pricing_cheapest: 'Clipper profesional termurah — bayar sekali, bukan langganan bulanan.',
    pricing_currency_note: 'Mata uang tampilan',
    pricing_checkout_note: 'Checkout diproses dalam IDR (Rupiah); harga lain hanya untuk referensi.',
    price_per_credit: 'per kredit',
    pricing_methods: 'QRIS · GoPay · OVO · DANA · ShopeePay · transfer bank · kartu',
    pricing_start: 'Mulai pakai →',
    pricing_best: 'paling laris',
    pricing_currency: 'Mata uang',
    pricing_testimonials: 'Dipakai kreator Indonesia & global',
    pricing_testimonials_sub: 'Dari podcaster, streamer, sampai reviewer film — semua butuh klip cepat tanpa ribet.',
    pricing_faq: 'Pertanyaan umum',
    faq_cookie_q: 'Apakah Klipin meminta cookie YouTube saya?',
    faq_cookie_a: 'Tidak pernah. Semua proses render di server kami, sama seperti Opus Clip — tidak ada akses ke akun pribadi kamu.',
    faq_input_q: 'Video apa saja yang bisa diproses?',
    faq_input_a: 'Link YouTube/TikTok atau file MP4/MOV/MKV sampai 2 GB. Roadmap: Google Drive, Zoom, Vimeo.',
    faq_cost_q: 'Berapa biaya sebenarnya per video?',
    faq_cost_a: 'Satu kredit = satu video diproses. Mulai {lowest} (paket 10 kredit) — di bawah biaya umum kompetitor. Kredit yang gagal render otomatis kembali, kamu tidak rugi.',
    faq_ai_q: 'Bagaimana AI memilih momen terbaik?',
    faq_ai_a: 'AI menganalisis transkrip + sampel frame untuk menemukan momen emosi tertinggi, lalu memberi skor viral (hook score) per klip — jadi kamu tahu klip mana yang paling layak di-upload duluan.',
    faq_now_q: 'Bisakah saya pakai sekarang?',
    faq_now_a: 'Ya — daftar dengan akun Google dan dapat 1 klip gratis untuk mencoba. Setelah itu beli paket kredit prabayar yang tidak pernah hangus.',
    credits_lbl: 'kredit permanen',
    perk_all_prev: 'Semua fitur',
    perk_no_watermark: 'Tanpa watermark',
    perk_all_exports: 'Export semua aspect',
    perk_auto_scheduler: 'Auto-publish scheduler',
  },
}

const STORAGE_KEY = 'klipin_locale'
const DEFAULT = 'en'

export function getLocale() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'id' ? 'id' : 'en'
  } catch {
    return DEFAULT
  }
}

export function setLocale(locale) {
  const v = locale === 'id' ? 'id' : 'en'
  try { localStorage.setItem(STORAGE_KEY, v) } catch { /* ignore */ }
  try { document.documentElement.lang = v } catch { /* ignore */ }
  try { window.dispatchEvent(new CustomEvent('klipin:locale', { detail: v })) } catch { /* ignore */ }
  return v
}

export function initLocale() {
  const v = getLocale()
  try { document.documentElement.lang = v } catch { /* ignore */ }
  return v
}

// t('pricing_eyebrow', { lowest: '$1.15' }) — returns the value for the
// active locale, falling back to English.
export function t(key, vars) {
  const loc = getLocale()
  let s = (DICT[loc] && DICT[loc][key]) || DICT[DEFAULT][key] || key
  if (vars) {
    for (const [k, val] of Object.entries(vars)) {
      s = s.replace(new RegExp(`{${k}}`, 'g'), String(val))
    }
  }
  return s
}