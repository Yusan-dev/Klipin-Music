import { Micro, StatusDot } from './components/ui.jsx'
import Icon from './components/Icon.jsx'

/** Module: Fitur — features grid + cost transparency. */
export default function Features({ onStart }) {
  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      <header className="text-center">
        <Micro className="inline-flex items-center gap-2 rounded-full border border-cyan/30 bg-navy-2/70 px-4 py-1.5 text-cyan-soft">
          <StatusDot tone="ok" pulse />
          MODUL FITUR — SEMUA YANG KAMU BUTUH UNTUK VIRAL
        </Micro>
        <h1 className="mx-auto mt-4 max-w-3xl font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
          <span className="text-chrome">Semua tools clipper</span>
          <br />
          dalam satu dashboard
        </h1>
      </header>

      <FeaturesGrid />
      <CostSection />
    </div>
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

function FeaturesGrid() {
  return (
    <section id="fitur" className="relative py-14">
      <SectionHead
        index="01"
        title="Semua yang kamu butuh untuk viral"
        sub="Table stakes ala Opus Clip & KlipAja — tapi dihitung transparan, berbahasa Indonesia, dan tanpa biaya langganan bulanan."
      />
      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="card-chrome group rounded-xl p-5">
            <div className="flex items-center justify-between">
              <Micro className="flex items-center gap-2">
                <StatusDot tone={f.tone} pulse={f.pulse} />
                {f.tag}
              </Micro>
              <span className="font-display text-lg font-bold text-cyan/30 transition group-hover:text-cyan/70">{f.num}</span>
            </div>
            <div className="mt-4 flex items-center gap-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan/30 bg-cyan/10 text-cyan-soft">
                <Icon name={f.icon} className="h-4 w-4" />
              </span>
              <h3 className="font-display text-base font-bold tracking-wide text-white">{f.title}</h3>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-white/50">{f.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

function CostSection() {
  return (
    <section className="relative py-14">
      <SectionHead
        index="02"
        title="Biaya transparan, bukan tebakan"
        sub="Setiap job menampilkan breakdown riil: ASR, analisis AI, dan render — dalam Rupiah. Tidak ada biaya tersembunyi, tidak ada langganan."
      />
      <div className="mt-10 grid gap-4 sm:grid-cols-3">
        {COSTS.map((c) => (
          <div key={c.label} className="card-chrome rounded-xl p-5 text-center">
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/40">{c.label}</div>
            <div className="mt-2 font-display text-2xl font-bold text-gold">{c.amount}</div>
            <div className="mt-1 text-xs text-white/45">{c.desc}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

const FEATURES = [
  { num: '01', icon: 'link', tag: 'SYS.INPUT', tone: 'ok', pulse: true, title: 'Input fleksibel', desc: 'Tempel link YouTube/TikTok atau upload file — sampai 2 GB. Tidak pernah minta cookie akun pribadi.' },
  { num: '02', icon: 'mic', tag: 'SYS.ASR', tone: 'ok', pulse: false, title: 'Transkripsi akurat', desc: 'AI speech-to-text dengan timestamp per kata — akurasi tinggi untuk Bahasa Indonesia.' },
  { num: '03', icon: 'sparkles', tag: 'SYS.ANALYZE', tone: 'run', pulse: true, title: 'AI pilih momen', desc: 'AI menganalisis transcript + sampel frame, lalu memilih 3–15 momen terbaik dengan judul & hook.' },
  { num: '04', icon: 'grid', tag: 'SYS.LAYOUT', tone: 'brand', pulse: false, title: '7 mode layout', desc: 'Auto, General, Center, Face Track, Split, Gameplay, dan 1:1 — termasuk deteksi wajah untuk mengikuti pembicara.' },
  { num: '05', icon: 'paint', tag: 'SYS.CAPTION', tone: 'magenta', pulse: true, title: 'Karaoke & gaya custom', desc: 'Animasi kata-per-kata, warna, font, posisi, emoji, dan watermark — dibakar langsung ke video.' },
  { num: '06', icon: 'trim', tag: 'SYS.EDIT', tone: 'ok', pulse: false, title: 'Editor bebas potong', desc: 'Pilih banyak bagian source → jahit jadi 1 klip, caption tetap sinkron. Plus trim, thumbnail, face swap, clean & SFX.' },
  { num: '07', icon: 'package', tag: 'SYS.EXPORT', tone: 'ok', pulse: false, title: 'Export & publish', desc: 'Unduh ZIP klip portrait 9:16 atau feed 1:1, lalu publish ke YouTube, TikTok & Instagram.' },
  { num: '08', icon: 'fire', tag: 'SYS.HASHTAG', tone: 'run', pulse: true, title: 'Skor viral + hashtag', desc: 'Hook score per klip + saran judul & hashtag viral untuk upload yang lebih cepat naik.' },
]

const COSTS = [
  { label: 'Transkripsi AI', amount: '±Rp15', desc: 'speech-to-text akurat, ~40× lebih cepat' },
  { label: 'Analisis AI', amount: '±Rp7', desc: 'hanya transcript + sampel frame' },
  { label: 'Render', amount: '±Rp3', desc: 'otomatis di server, 1080p' },
]
