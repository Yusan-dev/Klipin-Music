# TASK.MD — Migrasi & Orkestrasi AI Cover Agent di Klipin-Music (HeartMuLa -> YuE2 + SheetSage2)

## 1. Konteks & Akar Masalah Proyek
Repositori `Klipin-Music` saat ini menggunakan pipeline berbasis **HeartMuLa (`mulacover`)** untuk membuat cover lagu (mengubah aransemen/genre dan vocal persona).
**Masalah Utama Saat Ini:**
- Saat melakukan cover lagu, hasil audio sering dinyanyikan ulang dengan lirik yang melenceng/halusinasi (tidak sesuai lirik asli).
- Penyebab: *Raw audio prompt* dari lagu asli bertabrakan dengan teks lirik (terjadi *vocal stem bleeding* dan model terjebak dalam mode *audio continuation* alih-alih *melody-conditioned generation*).

## 2. Tujuan Arsitektur Baru
Mengganti/menambahkan engine utama `Klipin-Music` dengan arsitektur **Agentic Symbolic Cover Pipeline** berbasis **SheetSage2 + YuE2**:
1. **Symbolic Melody Extraction (`SheetSage2`):** Mengubah audio referensi menjadi notasi melodi simbolik (`ABC Notation` / `score.abc`) dengan opsi `melody_only=True` agar vokal asli terbuang bersih.
2. **Lyric & Style Formatting Agent:** Memastikan lirik memiliki struktur metatag (`[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`) dan menggabungkan input *Genre/Aransemen* serta *Vocal Persona* menjadi prompt `style` yang terstruktur.
3. **Melody-Conditioned Generation (`YuE2`):** Men-generate lagu cover menggunakan kondisi `abc` + `lyrics` + `style` (mode `cot="melody"`) dengan dukungan kuantisasi hemat VRAM (`INT8 ConvRot` & `Tiled VAE`).
4. **Custom Vocal Persona (Opsional/Modular):** Menyediakan hook pasca-pemrosesan (post-processing) untuk *Voice Conversion* (Seed-VC / RVC) jika input persona berupa file sampel audio.

---

## 3. Status Eksekusi

### Fase 1: Audit & Analisis Repository Saat Ini (Discovery)
- [x] **1.1. Pemetaan Struktur Proyek:** Telusuri seluruh file di workspace `Klipin-Music` untuk mengidentifikasi entry point (`app.py`, `music_service.py`), lokasi pemanggilan model (`music.py`, `mulacover_modal_app.py`), dan dependensi (`requirements.txt`).
- [x] **1.2. Identifikasi Titik Kegagalan Lirik:** Mengidentifikasi akar masalah *audio continuation* dan *vocal stem bleed* pada representasi laten audio MuLaCover vs prompt lirik.
- [x] **1.3. Laporan Analisis:** Membuat ringkasan perbandingan Mode A (ComfyUI Headless API — hemat VRAM 6GB-8GB untuk web) vs Mode B (Native SDK), dan dikonfirmasi pengguna untuk dieksekusi sampai tuntas.

### Fase 2: Pembuatan Modul Pre-Processing & AI Lyric/Style Agent
- [x] **2.1. Modul `backend/services/lyric_style_agent.py`:**
  - `normalize_lyrics(raw_lyrics: str) -> str`: otomatis mendeteksi/menyuntikkan structural section tags (`[Verse]`, `[Chorus]`, dll.) dan membersihkan artefak halusinasi ASR.
  - `build_style_prompt(genre: str, arrangement: str, vocal_persona_desc: str) -> str`: menyusun prompt akustik GMVP bahasa Inggris optimal untuk YuE2 dengan mastering studio -14 LUFS.
  - Fallback transkripsi audio otomatis via Whisper/Groq jika lirik kosong.

### Fase 3: Implementasi Engine Cover Baru (`SheetSage2` + `YuE2`)
- [x] **3.1. Base Interface `backend/engines/base_cover_engine.py`:**
  - Standar kontrak fungsi `generate_cover(...)` yang seragam untuk seluruh cover engine.
- [x] **3.2. Engine ComfyUI API `backend/engines/yue2_comfy_engine.py`:**
  - Upload audio ke ComfyUI via multipart/form-data.
  - Injeksi dinamis parameter cover ke graph workflow (`LoadAudio`, `SheetSage2AudioToABC`, `YuE2Sampler`, `YuE2VAEDecodeAudio`, `SaveAudio`).
  - Polling history asinkron dan pengunduhan hasil render audio.
- [x] **3.3. Engine Native Python `backend/engines/yue2_native_engine.py`:**
  - Ekstraksi melodi simbolik via `m-a-p/SheetSage2` (`melody_only=True` -> `score.abc`).
  - Pembersihan VRAM GPU bertahap (`gc.collect()`, `torch.cuda.empty_cache()`).
  - Inferensi `YuE2Pipeline` dengan `cot="melody"`.

### Fase 4: Integrasi ke Endpoint Utama & Pengujian
- [x] **4.1. Integrasi ke Router/Controller Utama:**
  - Didaftarkan ke `cover_engines` di endpoint `/api/music/genres` dan `/api/music/remix-url` di `backend/app.py`.
  - Dispatching engine cover di `generate_music` di `backend/music.py`.
  - Konfigurasi environment di `backend/.env` dan `.env.example`.
  - Dukungan pemilihan engine di UI `frontend/src/Music.jsx` dan `frontend/src/CoverCreate.jsx`.
- [x] **4.2. Pembuatan Template Workflow, Dokumentasi & Pengujian:**
  - Template workflow ComfyUI API di `workflows/yue2_cover_api.json`.
  - Dokumentasi teknis arsitektur cover di `docs/KLIPIN_MUSIC.md`.
  - Unit & Integration Test di `backend/tests/test_yue2_cover_engine.py` (14/14 tests PASS).
  - Total pengujian penuh modul musik: **45 passed (100% PASS)** tanpa regresi.
