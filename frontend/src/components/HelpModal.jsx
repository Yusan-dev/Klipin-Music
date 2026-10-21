import { useEffect } from 'react'
import Icon from './Icon.jsx'
import { Button, Micro } from './ui.jsx'

/**
 * Quick-help modal (AI-copilot-style onboarding). Zero backend — a small
 * friendly guide so new users know exactly what to do next. Replaces the
 * need for a support chat on day one.
 */
const GUIDES = [
  {
    icon: 'link',
    title: 'Buat klip dari link',
    body: 'Tempel link YouTube/TikTok di halaman utama, lalu klik "Buat klip". AI menyalin audio, memilih momen terbaik, dan merender klip 9:16 dengan subtitle karaoke otomatis.',
  },
  {
    icon: 'upload',
    title: 'Upload video sendiri',
    body: 'Punya file MP4/MOV/MKV? Klik "Upload file" di halaman utama. Maksimal 2 GB per video, dan tidak perlu cookie akun apa pun.',
  },
  {
    icon: 'paint',
    title: 'Ganti gaya subtitle',
    body: 'Buka project → tombol "Studio". Ubah warna, font, posisi, dan animasi kata-per-kata. Brand kit-mu tersimpan otomatis untuk video berikutnya.',
  },
  {
    icon: 'rocket',
    title: 'Publish langsung',
    body: 'Hubungkan akun YouTube/TikTok/Instagram di Pengaturan → PUBLISH. Lalu publish klip sekarang atau jadwalkan — semua tanpa keluar dari Klipin.',
  },
]

export default function HelpModal({ open, onClose }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[70]" role="dialog" aria-modal="true" aria-label="Panduan cepat">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="card-chrome absolute inset-x-4 top-[10vh] mx-auto max-w-2xl overflow-hidden rounded-xl shadow-neon-cyan">
        <header className="flex items-center justify-between gap-3 border-b border-cyan/15 bg-navy-2/60 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-cyan/40 bg-cyan/10 text-cyan-soft">
              <Icon name="help" className="h-4 w-4" />
            </span>
            <div>
              <Micro>PANDUAN CEPAT</Micro>
              <div className="text-sm font-semibold text-white">Mulai dalam 4 langkah</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Tutup panduan"
            className="rounded-md border border-border p-1.5 text-white/50 transition hover:border-cyan/50 hover:text-white"
          >
            <Icon name="close" className="h-4 w-4" />
          </button>
        </header>

        <div className="max-h-[60vh] space-y-3 overflow-y-auto p-5">
          {GUIDES.map((g, i) => (
            <div key={g.title} className="flex gap-3 rounded-lg border border-border-soft bg-navy-2/40 p-3.5">
              <div className="flex flex-col items-center">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyan/30 bg-cyan/10 text-cyan-soft`}>
                  <Icon name={g.icon} className="h-4 w-4" />
                </span>
                <span className="mt-1 font-mono text-[9px] text-white/30">{i + 1}</span>
              </div>
              <div className="min-w-0">
                <h3 className="font-display text-sm font-bold tracking-wide text-white">{g.title}</h3>
                <p className="mt-1 text-sm leading-relaxed text-white/55">{g.body}</p>
              </div>
            </div>
          ))}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-border-soft bg-navy-2/40 px-5 py-3">
          <span className="font-mono text-[10px] uppercase tracking-widest text-white/35">
            bantuan: <a href="mailto:support@klip-in.id" className="text-cyan-soft/80 transition hover:text-cyan-soft">support@klip-in.id</a>
          </span>
          <Button size="sm" onClick={onClose}>Mengerti, lanjut →</Button>
        </footer>
      </div>
    </div>
  )
}
