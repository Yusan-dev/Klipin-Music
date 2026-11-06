# Dokumentasi Klipin Music — Symbolic Cover Architecture (SheetSage2 + YuE2)

## 1. Ikhtisar Arsitektur
Klipin Music mengintegrasikan pipeline **Agentic Symbolic Cover** berbasis **SheetSage2 + YuE2 (m-a-p/YuE2-3B)** untuk mengatasi kendala halusinasi lirik (*vocal stem bleeding*) pada cover lagu.

### Akar Masalah pada Model Audio-Conditioned Lama
Pada pipeline lama berbasis HeartMuLa (`mulacover`):
- Conditioning mengandalkan sinyal audio laten langsung yang memuat vokal asli.
- Terjadi *vocal stem bleeding* di mana model mencoba menyambung audio penyanyi asli alih-alih menyanyikan lirik teks baru, memicu halusinasi kata atau pengulangan fonem fiktif.

### Solusi Symbolic-Conditioned Cover (`SheetSage2` + `YuE2`)
1. **Symbolic Melody Extraction (`SheetSage2`):**
   - Mengekstrak audio referensi menjadi notasi simbolik murni (**ABC Notation** / `score.abc`) dengan opsi `melody_only=True`.
   - Vokal asli dan drum/instrumen pengganggu tereliminasi total secara matematis, menyisakan deretan pitch & durasi birama bersih.
2. **Lyric & Style Formatting Agent (`backend/services/lyric_style_agent.py`):**
   - Menjamin lirik tersusun dalam struktur metatag terstandar (`[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Outro]`).
   - Menyusun prompt style GMVP (*Genre, Mood, Instrument, Vocals, Production*) berbahasa Inggris akustik optimal.
3. **Melody-Conditioned Generation (`YuE2`):**
   - Mengeksekusi generasi lagu menggunakan kondisi `abc` + `lyrics` + `style` dengan mode `cot="melody"`.
   - Lirik terkunci presisi sesuai ketukan dan melodi lagu referensi.

---

## 2. Mode Deployment

### Mode A: ComfyUI Headless API (Direkomendasikan untuk Web & VRAM 6-8GB)
- **Engine ID:** `yue2_comfy`
- **Karakteristik:**
  - Menggunakan node ComfyUI dengan kuantisasi `INT8 ConvRot` dan `Tiled VAE`.
  - Beroperasi asinkron melalui endpoint REST API ComfyUI (`/upload/image`, `/prompt`, `/history`, `/view`).
  - Web server FastAPI tetap ringan, responsif, dan terisolasi dari potensi crash GPU.
- **Workflow Template:** `workflows/yue2_cover_api.json`
- **Konfigurasi Environment (.env):**
  ```env
  COVER_ENGINE=yue2_comfy
  COMFYUI_URL=http://127.0.0.1:8188
  COMFYUI_TIMEOUT=600
  MAX_DURATION=360
  KLIPIN_YUE2_COMFY_ENABLED=1
  ```

### Mode B: Native Python SDK (Untuk GPU Server VRAM 16-24GB)
- **Engine ID:** `yue2_native`
- **Karakteristik:**
  - Menjalankan model `m-a-p/SheetSage2` dan `YuE2Pipeline` langsung di PyTorch GPU.
  - Memanfaatkan manajemen memori bertahap (`gc.collect()` & `torch.cuda.empty_cache()` antar tahap).
- **Konfigurasi Environment (.env):**
  ```env
  COVER_ENGINE=yue2_native
  KLIPIN_YUE2_NATIVE_ENABLED=1
  SHEETSAGE2_MODEL_PATH=m-a-p/SheetSage2
  YUE2_MODEL_PATH=m-a-p/YuE2-3B
  ```

---

## 3. Struktur Modul & Kontrak Antarmuka

- **Base Cover Engine:** `backend/engines/base_cover_engine.py` (`BaseCoverEngine`)
  - Kontrak seragam: `generate_cover(source_audio_path, lyrics, genre_style, vocal_persona, seed, max_duration, output_dir)`
- **ComfyUI REST Engine:** `backend/engines/yue2_comfy_engine.py` (`Yue2ComfyEngine`)
- **Native SDK Engine:** `backend/engines/yue2_native_engine.py` (`Yue2NativeEngine`)
- **Lyric & Style Agent:** `backend/services/lyric_style_agent.py`
  - `normalize_lyrics(raw_lyrics: str) -> str`
  - `build_style_prompt(genre: str, arrangement: str, vocal_persona_desc: str) -> str`
- **Orkestrator Utama:** `backend/music.py` (`generate_music`) & `backend/app.py` (`/api/music/remix-url`)
