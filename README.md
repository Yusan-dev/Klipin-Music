# Klipin Music Studio (Standalone Suno AI Clone & Cover Engine)

Proyek mandiri **Klipin Music** yang dipisahkan sepenuhnya dari modul pemotong video (*Klipin Clipper*) agar dapat di-hosting secara terpisah dengan konfigurasi `.env` yang sama.

## Fitur Utama

1. **Suno AI v4.5 3-Pane Studio Workspace (`frontend/src/Music.jsx`)**
   - **Left Pane:** Create / Cover / Lyrics / Persona / Stem Studio
   - **Center Pane:** Library Feed, Filter Chips, Dual-Take A/B Cards, SSE Live Stage Progress
   - **Right Pane:** Song Inspector, Synchronized Karaoke Lyrics, Beat Grid, Stem Download
   - **Sticky Bottom Transport Bar:** Pemutar global persisten di bagian bawah layar
2. **Engine AI Musik & Cover di GPU NVIDIA L4 (Modal Serverless)**
   - **`MuLaCover` (`HeartMuLa/MuLaCover` 3B — L4 GPU):** Symbolic Lead-Sheet Cover (`melody.mid` + `chord.mid` + Genre-specific `drums.mid` seperti Reggae One-Drop & Offbeat Skank, mengikuti parameter resmi `cfg_scale=1.5, temperature=1.0, topk=250`).
   - **Vocal-Preserving & Voice-Converted Genre Cover (`Demucs htdemucs` + `Seed-VC` + `MuLaCover` — L4 GPU):** Memisahkan vokal asli penyanyi (ketukan & pelafalan 100% terkunci pada lagu sumber), opsional mengubah warna suara ke Cewe/Cowo/Persona dengan `Seed-VC`, lalu menimpanya di atas aransemen baru (`Reggae`, `Dangdut Koplo`, `Jazz`, dll.).
   - **`DiffRhythm` (`ASLP-lab/DiffRhythm-full` — Apache 2.0, L4 GPU):** Full-song latent diffusion untuk pembuatan lagu baru komersial.
   - **`HeartMuLa` (`HeartMuLa-oss-3B` — Apache 2.0, L4 GPU):** Text-to-song vokal penuh multi-bahasa (Indonesia, Jawa, Sunda, Inggris, dll.).

## Menjalankan Secara Mandiri (Docker Compose)

File `backend/.env` sudah disalin dari konfigurasi utama. Jalankan:

```bash
docker compose up --build -d
```

- **API Standalone (`music_service:app`):** `http://localhost:8002`
- **Web Studio (`VITE_APP_MODE=music`):** `http://localhost:5175`
