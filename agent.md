# Klipin Music Agent Guard (`agent.md`)

> **ATURAN WAJIB (HARD BOUNDARY):**
> Seluruh pekerjaan dalam skop ini **HANYA BOLEH** mengubah modul **Klipin Music** (`backend/music.py`, `backend/song_plan.py`, `backend/lyrics_writer.py`, `backend/*_modal_app.py` khusus musik, endpoint `/api/music/*` di `backend/app.py`, `frontend/src/Music.jsx`, `frontend/src/CoverCreate.jsx`, `frontend/src/components/MusicWaveform.jsx`, `frontend/src/components/VoiceDesigner.jsx`, serta tes/dokumentasi musik).
> **DILARANG KERAS** mengubah modul lain (termasuk pipeline video Shorts/Reels 9:16, `backend/video.py`, `backend/pipeline.py`, `backend/subtitles.py`, `backend/gpu.py`, `backend/ffmpeg_utils.py`, `backend/modal_app.py`, atau kontrak `docs/RENDER_PERFORMANCE_CONTRACT.md`).

---

## 1. Isolasi Modul (Strict Module Boundary)

1. **Jangan sentuh pipeline video Klipin:**
   - Resolusi 9:16 (`1080x1920`, SAR `1:1`), `portrait guardian`, L4/8 CPU/16 GiB video worker (`modal_app.py`), `KLIPIN_FAST_RENDER=1`, dan `KLIPIN_GPU=1` (Modal-only NVENC lock) tidak boleh disentuh.
   - Sebelum menyelesaikan perubahan apa pun, wajib jalankan `python scripts/render_contract_guard.py` untuk memastikan 17 invariant video tetap `PASS`.
2. **File yang diizinkan untuk dimodifikasi:**
   - Backend Music: `backend/music.py`, `backend/song_plan.py`, `backend/lyrics_writer.py`, `backend/music_modal_app.py`, `backend/mulacover_modal_app.py`, `backend/ace_step_modal_app.py`, `backend/diffrhythm_modal_app.py`, `backend/voice_modal_app.py`, `backend/voice_personas.py`, `backend/persona_previews.py`, dan blok rute `/api/music/*` di `backend/app.py`.
   - Frontend Music: `frontend/src/Music.jsx`, `frontend/src/CoverCreate.jsx`, `frontend/src/components/MusicWaveform.jsx`, `frontend/src/components/VoiceDesigner.jsx`.
   - Test & Docs Music: `backend/tests/test_music*.py`, `docs/KLIPIN_MUSIC.md`, `docs/SUNO_REVERSE_ENGINEERING_RESEARCH.md`.

---

## 2. Kebijakan Lisensi Model Musik (Commercial-Ready Only)

1. **DILARANG menggunakan YuE / YuE2 (`m-a-p/YuE2-3B`):**
   - Bobot model YuE2 berlisensi **CC BY-NC 4.0** (Non-Commercial). Model ini telah **dihapus** dari Klipin Music agar seluruh output platform aman untuk komersialisasi. Jangan pernah menambahkan kembali `yue2` atau `yue2_modal_app.py`.
2. **DILARANG menggunakan SongGeneration / LeVo (Tencent):**
   - Berlisensi non-komersial khusus riset/akademik.
3. **Model Resmi Klipin Music yang Dipertahankan & Ditambahkan (Gratis & Komersial):**
   - **HeartMuLa (`heartmula`)** — *Apache 2.0 License*. Engine final utama (default) untuk pembuatan lagu penuh ber-vokal dan duet role-separated.
   - **MuLaCover (`mulacover`)** — Engine cover berbasis kondisi simbolik (melodi, chord, drum referensi) untuk cover faithful.
   - **ACE-Step (`ace-step`)** — *MIT / Apache 2.0 License*. Engine Diffusion-Transformer (DiT) cepat untuk draft A/B, iterasi cepat, dan cover audio-to-audio.
   - **DiffRhythm (`diffrhythm`)** — *Apache 2.0 License* (`ASLP-lab/DiffRhythm-full` + `ASLP-lab/DiffRhythm-vae`). Engine end-to-end latent diffusion gratis & komersial untuk generasi lagu penuh dari lirik ber-timestamp (`.lrc`) dan style prompt.

---

## 3. Arsitektur Alur Kerja Setara Suno AI (Hasil Reverse Engineering)

Berdasarkan riset reverse-engineering (`REA` / `.tools/rea` & `suno-field-guide`), Klipin Music mengimplementasikan prinsip inti Suno v4.5/v5.5:

1. **Two-Zone Input & GMVP Prompting (Genre + Mood + Vocals + Production):**
   - Style tags disusun mengikuti urutan prioritas yang dipatuhi model: `Genre → Mood → Instrument → Vocal Style → Production/Atmosphere`.
   - Menghindari "Pop Gravity Well" dengan menginjeksikan tag spesifik instrumen dan arahan negatif (`negative_tags` / `no <element>`).
   - Mendukung **Vocal Clarity Override** (seperti teknik *Broadway clarity / close-mic forward vocal*) agar artikulasi lirik Indonesia & Nusantara terdengar tajam dan tidak tenggelam di balik instrumen.
2. **Pipe-Stacked Section Meta-Tags (`[Section | Mood | Vocal | Instrument]`):**
   - Penanda struktur lirik mendukung format pipe-stacking ala Suno (`[Chorus | Anthemic | Stacked Harmonies | Full Band]`) dengan maksimal 7 elemen per tag dan urutan prioritas kiri-ke-kanan.
   - Mendukung kontrol level lirik: `(backing vocals / call-and-response)`, `*sound cues / live atmosphere*`, `ALL CAPS` untuk klimaks vokal, dan penutup eksplisit `[End]` agar lagu tidak menggantung di akhir durasi.
3. **Anti-AI-Cliché Lyric Engine:**
   - `lyrics_writer.py` memblokir kata-kata klise AI ("neon", "shadows", "ashes", "ghost in the machine", "chains", dll.), menjaga disiplin 6–11 kata per baris (satu napas), dan mempertahankan jumlah baris yang presisi terhadap durasi lagu (`SECONDS_PER_LINE = 4.7`).
4. **Post-Processing & Mastering Studio (`-14 LUFS`):**
   - Mengatasi masalah umum output model musik AI (muddiness di 200–350 Hz dan harshness di >8 kHz) dengan surgical EQ ringan sebelum loudness normalization `-14 LUFS` (`-1.5 dBTP`).

---

## 4. Standar Keamanan & Refactoring Kode Klipin Music

1. **Cegah Format-String & Command Injection:**
   - Jangan pernah memanggil `.format(**user_dict)` pada string argumen eksternal yang berisi input pengguna (`prompt`, `lyrics`). Gunakan substitusi token eksplisit dan allowlist provider (`ALLOWED_EXTERNAL_PROVIDERS`).
2. **Validasi Path & Anti-Path-Traversal:**
   - Semua pembacaan file audio referensi (`ref_audio_path`) wajib memverifikasi ekstensi audio yang diizinkan (`.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.ogg`, `.opus`), menolak symlink, dan membatasi ukuran maksimal (15 MB).
3. **Proteksi SSRF pada Remix URL (`/api/music/remix-url`):**
   - Tolak skema selain `http`/`https`, hostname lokal (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`), link-local metadata (`169.254.169.254`), dan IP privat (RFC 1918) sebelum memanggil pengunduh audio.
