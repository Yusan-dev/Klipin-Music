# TASK — Klip-in Retro Futurism Brand Integration

## Goal
Integrasikan asset baru di `public/brand/` ke frontend React/Vite/Tailwind saat ini tanpa mengubah alur aplikasi, API, routing, auth, atau logic processing video.

Arah visual: **retro futurism yang terasa dirancang manusia**, bukan full cyberpunk/neon. Pertahankan UI gelap dan token warna sekarang. Neon dipakai sebagai aksen. Background memiliki **organic flowing lines + technical grid** tipis agar halaman terasa hidup tetapi content tetap terbaca.

## Existing design system yang WAJIB dipertahankan
Frontend sudah menggunakan token CSS di `src/index.css` dan pemetaan Tailwind di `tailwind.config.js`:

- `navy` → `--c-navy` (`#0B0E1F` dark)
- `navy-2` → `--c-navy-2`
- `panel` → `--c-panel`
- `cyan` → `--c-cyan` (`#00F0FF` dark)
- `magenta` → `--c-magenta` (`#FF2EAE` dark)
- `gold` → `--c-gold` (`#FFD400` dark)
- `white` maps to adaptive `--c-ink`
- fonts: Orbitron (display), Rajdhani (body), Share Tech Mono (micro/metadata)

**Jangan membuat palette kedua.** Asset SVG sudah memakai warna yang sama dengan token existing.

## Priority 0 — Safety
- Jangan sentuh `src/api.js`, auth flow, job processing, route parsing, atau backend contract.
- Jangan menghapus light mode.
- Jangan menambah dependency baru hanya untuk dekorasi.
- Hormati `prefers-reduced-motion` yang sudah tersedia.
- Jalankan `npm run build` setelah tiap fase besar.

## Priority 1 — Global background line system
### Target
`src/components/AppShell.jsx`

Tambahkan 2–3 layer dekoratif fixed di belakang seluruh aplikasi:
1. `/brand/backgrounds/line-field.svg` — opacity sekitar `0.22–0.34` dark, `0.08–0.14` light.
2. `/brand/backgrounds/circuit-lines.svg` — opacity `0.12–0.20`.
3. Optional `/brand/backgrounds/perspective-grid.svg` hanya di halaman Home/Workbench hero, jangan di semua screen.

Implementation pattern:
- Root AppShell tetap `min-h-screen bg-navy text-white`.
- Jadikan root `relative isolate overflow-x-hidden`.
- Background layer: `pointer-events-none fixed inset-0 -z-10` atau pseudo-element.
- Tambahkan mask/fade agar lines memudar di area bawah/tepi.
- Content/sidebar/topbar harus berada di atas background (`relative z-10` bila perlu).
- Background tidak boleh mengurangi contrast form/table/modal.

Tambahkan helpers di `src/index.css`:
- `.klip-bg-lines`
- `.klip-bg-circuit`
- `.klip-bg-vignette`
- `.klip-surface` untuk card yang butuh sedikit lebih opaque agar line tidak mengganggu.

Gunakan existing token CSS dalam gradient/overlay, bukan hex baru.

## Priority 2 — Logo & favicon
### Logo
Update `Logo` di `src/components/ui.jsx` agar menggunakan asset:
- desktop expanded: `/brand/logos/logo-horizontal-dark.svg`
- compact/sidebar collapsed: `/brand/logos/logo-mark.svg`

Jangan render horizontal logo terlalu kecil. Target tinggi 30–36px.

### Favicon
Update `index.html`:
- main favicon `/brand/favicons/favicon-32.svg`
- optional icon sizes 16/32/48/192/512.
- `theme-color` tetap mengikuti `theme.jsx`.

## Priority 3 — Landing / dashboard
Target: `src/Landing.jsx`

- Hero/quick intake tetap pendek dan application-first.
- Tambahkan `hero-lines-grid.svg` sebagai dekorasi hanya di panel hero, opacity rendah.
- Jangan mengubah copy/fungsi form kecuali diperlukan untuk spacing.
- PhonePreview boleh memakai `perspective-grid.svg` sebagai subtle internal texture.
- Module cards tetap `card-chrome`, tetapi kurangi glow default; glow kuat hanya hover/focus.
- Maksimal satu accent dominan per card (cyan/magenta/gold).

## Priority 4 — Empty states dan mascot
Gunakan asset berikut berdasarkan konteks:
- Library kosong → `/brand/illustrations/empty-no-clips.svg`
- Favorites kosong → `/brand/illustrations/empty-no-favorites.svg`
- Processing → `/brand/illustrations/empty-processing.svg` atau `/brand/motion/loader.svg`
- Failed state → `/brand/illustrations/empty-failed.svg`
- Friendly helper/onboarding → `/brand/mascot/klip-bot.svg`

Mascot jangan muncul di setiap screen. Maksimal pada onboarding, empty-state, success panel, atau help.

## Priority 5 — Processing / loader
Target kemungkinan:
- `src/components/Loader.jsx`
- `src/components/ProcessingStage.jsx`
- `src/components/Waveform.jsx`

Gunakan `/brand/motion/loader.svg` dan `/brand/motion/waveform.svg` jika compatible dengan animasi existing. Bila animasi current lebih informatif, pertahankan logic current dan hanya ambil visual language-nya.

Rules:
- motion 1.5–2.4 detik, linear/ease-in-out.
- tidak flashing cepat.
- reduced motion = static.

## Priority 6 — Icons
Asset custom ada di `public/brand/icons/` untuk momen brand-specific:
- clip
- spark
- upload
- link
- subtitle
- scissors
- magic
- download

Jangan mengganti seluruh Heroicons. Pakai custom icon hanya di area signature seperti hero, onboarding, success, empty-state.

## Priority 7 — Social / metadata
Asset:
- `/brand/social/og-1200x630.svg`
- `/brand/social/youtube-thumbnail-1280x720.svg`

Untuk production social sharing, export OG ke PNG 1200×630 bila platform target tidak reliably menampilkan SVG.

Update meta di `index.html` setelah domain production final:
- `og:title`
- `og:description`
- `og:image`
- `twitter:card=summary_large_image`

## Priority 8 — Light mode compatibility
Asset SVG dark-oriented. Untuk light mode:
- jangan invert gambar.
- letakkan logo/mascot dalam subtle dark or neutral brand surface bila perlu.
- background lines harus turun opacity besar di light mode.
- semua text tetap mengikuti `--c-ink` existing.

Tambahkan light overrides di `src/index.css`, contoh:
- `html.light .klip-bg-lines { opacity: .10; mix-blend-mode: multiply; }`
- `html.light .klip-bg-circuit { opacity: .06; }`

## Human-design rules
1. Jangan gunakan glow di semua elemen.
2. Jangan membuat semua border cyan.
3. Sisakan whitespace/negative space.
4. Gunakan alignment grid, tapi dekorasi boleh sedikit asymmetric.
5. Gunakan magenta/gold sebagai punctuation, bukan background utama.
6. Heading Orbitron hanya untuk judul/label penting; body tetap Rajdhani.
7. Micro labels/telemetry boleh Share Tech Mono.
8. Line background tidak boleh melewati modal sebagai foreground.
9. Hindari gradient berlebihan; maksimal 1 gradient besar per section.
10. Radius utama 8–18px, bukan semuanya pill.

## Acceptance checklist
- [ ] `npm run build` sukses tanpa warning/error baru yang kritis.
- [ ] Home desktop 1440px tetap readable dengan line background.
- [ ] Home mobile 375px tidak overflow horizontal.
- [ ] Sidebar expanded/collapsed menampilkan logo yang benar.
- [ ] Dark mode terlihat retro-futuristic tetapi tidak berisik.
- [ ] Light mode tetap readable dan background dekoratif sangat subtle.
- [ ] Forms, dropdowns, modals, captions, processing stages tidak kehilangan contrast.
- [ ] `prefers-reduced-motion` tidak memutar animasi terus menerus.
- [ ] Empty states menggunakan illustration sesuai konteks.
- [ ] Tidak ada perubahan pada API/auth/job behavior.

## Asset map
- Logos: `public/brand/logos/`
- Favicons: `public/brand/favicons/`
- Backgrounds: `public/brand/backgrounds/`
- Mascot: `public/brand/mascot/`
- Empty states: `public/brand/illustrations/`
- Motion: `public/brand/motion/`
- Social: `public/brand/social/`
- Custom brand icons: `public/brand/icons/`
- Manifest: `public/brand/manifest.json`

## Suggested implementation order for Codex/agent
1. AppShell background layers + CSS helpers.
2. Logo + favicon.
3. Landing hero texture.
4. Empty states/mascot.
5. Loader/waveform.
6. Social metadata.
7. Responsive/light-mode review.
8. Build test and visual QA.
