import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from music import (
    _lyrics_to_lrc,
    _music_lyrics_from_manual,
    _music_lyrics_from_transcript,
    _music_timed_lines_from_transcript,
    _split_long_lyric_line,
    _transcribe_source_audio,
    generate_music,
    transcribe_audio_groq,
)


def test_lrc_real_timestamps():
    """LRC generator uses exact ASR timestamps when lyrics_timed is provided."""
    timed_lines = [
        {"text": "Bila cinta tak lagi untukku", "start": 4.5, "end": 8.2, "section": "Verse"},
        {"text": "Bila melangkah tak seindah dulu", "start": 9.15, "end": 14.8, "section": "Verse"},
        {"text": "Aishiteru", "start": 62.4, "end": 66.8, "section": "Chorus"},
    ]
    lrc = _lyrics_to_lrc("", 180.0, lyrics_timed=timed_lines)
    assert "[00:04.50]Bila cinta tak lagi untukku" in lrc
    assert "[00:09.15]Bila melangkah tak seindah dulu" in lrc
    assert "[01:02.40]Aishiteru" in lrc
    # Real timestamps must be strictly preserved, not divided into even intervals
    assert lrc.splitlines()[0].startswith("[00:04.50]")


def test_lrc_fallback_even_slots():
    """LRC generator falls back to even slot distribution when lyrics_timed is absent."""
    sheet = "[Verse]\nBaris satu\nBaris dua\n[Chorus]\nReff bersama"
    lrc = _lyrics_to_lrc(sheet, 60.0, lyrics_timed=None)
    lines = lrc.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("[00:00.00]Baris satu")
    # Even slots across 60 seconds
    assert "Reff bersama" in lines[-1]


def test_transcribe_audio_groq_missing_key_raises(tmp_path, monkeypatch):
    """transcribe_audio_groq raises ValueError if GROQ_API_KEY is not set."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    fake_audio = tmp_path / "test.mp3"
    fake_audio.write_bytes(b"dummy audio data")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        transcribe_audio_groq(fake_audio)


def test_transcribe_audio_groq_parsing(tmp_path, monkeypatch):
    """transcribe_audio_groq sends multipart request and parses verbose_json."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_fake_secret_key_123")
    fake_audio = tmp_path / "sample.mp3"
    fake_audio.write_bytes(b"dummy-mp3-bytes" * 50)

    groq_payload = {
        "text": "Aishiteru menunggumu",
        "segments": [
            {
                "id": 0,
                "start": 1.25,
                "end": 4.80,
                "text": "Aishiteru menunggumu",
                "tokens": [101, 102],
            }
        ],
        "language": "id",
    }
    raw_response = json.dumps(groq_payload).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_response
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = transcribe_audio_groq(fake_audio)
        assert result["text"] == "Aishiteru menunggumu"
        assert len(result["segments"]) == 1
        assert result["source"] == "groq-whisper-large-v3-turbo"
        assert mock_urlopen.called

        # Verify headers & URL without exposing raw secret in unhandled errors
        req = mock_urlopen.call_args[0][0]
        assert "api.groq.com" in req.full_url
        assert req.headers["Authorization"] == "Bearer gsk_test_fake_secret_key_123"
        assert "multipart/form-data" in req.headers["Content-type"]


def test_transcribe_source_audio_prefers_groq_then_whisper_fallback(tmp_path, monkeypatch):
    """_transcribe_source_audio prefers Groq and falls back to local Whisper."""
    fake_audio = tmp_path / "audio.mp3"
    fake_audio.write_bytes(b"dummy")

    # 1. When Groq succeeds
    monkeypatch.setenv("GROQ_API_KEY", "valid_key")
    with patch("music.transcribe_audio_groq", return_value={"text": "Groq speech", "segments": [], "source": "groq-whisper-large-v3-turbo"}):
        res = _transcribe_source_audio(fake_audio)
        assert res["source"] == "groq-whisper-large-v3-turbo"
        assert res["text"] == "Groq speech"

    # 2. When Groq fails, falls back to local transcribe
    with patch("music.transcribe_audio_groq", side_effect=RuntimeError("Groq 500")):
        with patch("transcribe.transcribe", return_value={"text": "Local speech", "segments": [], "source": "faster-whisper"}):
            res_fb = _transcribe_source_audio(fake_audio)
            assert res_fb["source"] == "faster-whisper"
            assert res_fb["text"] == "Local speech"


def test_transcript_cleaning_filters_music_cues_and_preserves_words():
    """_music_lyrics_from_transcript filters [musik] and splits long lines verbatim."""
    transcript = {
        "text": "[musik] Menunggu sesuatu yang sangat menyebalkan bagiku. Saat ku harus berdiri tegak di sini.",
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "[musik]"},
            {"start": 2.6, "end": 7.0, "text": "Menunggu sesuatu yang sangat menyebalkan bagiku."},
            {"start": 7.1, "end": 12.0, "text": "(Music)"},
            {"start": 12.1, "end": 18.0, "text": "Saat ku harus berdiri tegak di sini."},
        ],
    }
    lyrics = _music_lyrics_from_transcript(transcript)
    assert "[musik]" not in lyrics.lower()
    assert "(music)" not in lyrics.lower()
    assert "Menunggu sesuatu yang sangat menyebalkan bagiku." in lyrics
    assert "Saat ku harus berdiri tegak di sini." in lyrics
    assert lyrics.startswith("[Verse]")

    timed = _music_timed_lines_from_transcript(transcript)
    assert len(timed) == 2
    assert timed[0]["text"] == "Menunggu sesuatu yang sangat menyebalkan bagiku."
    assert timed[0]["start"] == 2.6
    assert timed[1]["text"] == "Saat ku harus berdiri tegak di sini."
    assert timed[1]["start"] == 12.1


def test_lyric_priority_manual_over_asr():
    """Manual lyrics take precedence over ASR and preserve user phrasing."""
    manual = "[Intro]\nOne two three\n[Chorus]\nLagu indah ini milik kita"
    locked = _music_lyrics_from_manual(manual)
    assert locked.startswith("[Intro]")
    assert "One two three" in locked
    assert "Lagu indah ini milik kita" in locked


def test_cover_pipeline_fail_honestly_when_no_lyrics(tmp_path):
    """Cover mode without source lyrics must fail honestly rather than inventing lyrics via ensure_sheet."""
    import base64
    spec = {
        "title": "Cover Tanpa Lirik",
        "genre": "pop",
        "cover_mode": True,
        "cover_engine": "mulacover",
        "ref_audio_b64": base64.b64encode(b"dummy-audio-bytes" * 100).decode("ascii"),
        "lyrics": "",
        "full_song": True,
        "duration": 30,
    }
    with pytest.raises(RuntimeError, match="Cover membutuhkan lirik sumber persis"):
        generate_music(spec, tmp_path)


def test_cover_pipeline_vocal_engine_allows_placeholder_when_lyrics_absent(tmp_path, monkeypatch):
    """Arrangement / voice engine retains original vocal stem with placeholder when lyrics are empty."""
    import base64
    from bgm import render_bgm

    monkeypatch.setenv("KLIPIN_VOICE_MODAL_ENABLED", "0")
    monkeypatch.setenv("KLIPIN_ACE_STEP_MODAL_ENABLED", "0")
    monkeypatch.setenv("KLIPIN_MULACOVER_MODAL_ENABLED", "0")

    src_audio = tmp_path / "src.m4a"
    render_bgm("chill", str(src_audio), 8.0)
    src_b64 = base64.b64encode(src_audio.read_bytes()).decode("ascii")

    out_dir = tmp_path / "job_arr"
    result = generate_music(
        {
            "title": "Reggae Vocal Cover",
            "genre": "reggae",
            "duration": 8,
            "cover_mode": True,
            "cover_engine": "arrangement",
            "cover_source_b64": src_b64,
            "lyrics": "",  # Empty lyrics
        },
        out_dir,
    )
    assert result["provider"] == "arrangement-cover"
    assert result["metadata"]["lyrics_source"] == "source_vocal_stem"
    assert "Vokal asli sumber dipertahankan" in result["lyrics_text"]


def test_remix_url_and_transcribe_url_api_endpoints():
    """Verify input validation, SSRF blocking, rights requirement on API routes."""
    from fastapi.testclient import TestClient
    from app import app, require_user

    app.dependency_overrides[require_user] = lambda: {"google_sub": "sub123", "email": "test@klipin.ai"}
    client = TestClient(app)

    # SSRF guard
    res = client.post("/api/music/remix-url", json={"url": "http://localhost:8000/", "rights_confirmed": True, "genre": "reggae"})
    assert res.status_code == 400
    assert "lokal" in res.json()["detail"].lower() or "privat" in res.json()["detail"].lower()

    # Rights guard
    res_rights = client.post("/api/music/remix-url", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "rights_confirmed": False, "genre": "reggae"})
    assert res_rights.status_code == 400
    assert "izin" in res_rights.json()["detail"].lower()

    # transcribe-url route exists and forwards to transcribe_only
    res_transcribe = client.post("/api/music/transcribe-url", json={"url": "http://127.0.0.1:8080/", "rights_confirmed": True, "genre": "reggae"})
    assert res_transcribe.status_code == 400
    assert "privat" in res_transcribe.json()["detail"].lower() or "lokal" in res_transcribe.json()["detail"].lower()


def test_transcript_injects_intro_and_instrumental_markers_from_timestamps():
    """When first vocal segment starts >= 6.0s or has >= 8.0s interlude gap, [Intro] and [Instrumental] are injected."""
    transcript = {
        "text": "Menunggu sesuatu yang sangat menyebalkan bagiku. Aishiteru.",
        "segments": [
            {"start": 19.53, "end": 25.4, "text": "Menunggu sesuatu yang sangat menyebalkan bagiku."},
            {"start": 26.0, "end": 31.2, "text": "Saat ku harus bersabar dan trus menunggu."},
            {"start": 45.0, "end": 50.5, "text": "Aishiteru bukti cinta untukmu."},
        ],
    }
    lyrics = _music_lyrics_from_transcript(transcript)
    assert lyrics.startswith("[Intro]\n\n[Verse]\n")
    assert "[Instrumental]" in lyrics
    assert "Menunggu sesuatu yang sangat menyebalkan bagiku." in lyrics
    assert "Aishiteru bukti cinta untukmu." in lyrics


def test_mulacover_inject_vocal_timing_markers():
    """Verify _inject_vocal_timing_markers prepends [Intro] only when first_vocal_sec >= 6.0s."""
    from music import _inject_vocal_timing_markers

    raw = "[Verse]\nMenunggu sesuatu"
    assert _inject_vocal_timing_markers(raw, 2.5) == raw
    injected = _inject_vocal_timing_markers(raw, 19.53)
    assert injected.startswith("[Intro]\n\n[Verse]\n")
    assert _inject_vocal_timing_markers(injected, 19.53) == injected


def test_studio_equalizer_normalize_and_apply(tmp_path):
    """Verify 8-band Studio Equalizer normalization, FFmpeg mastering, backup, and reset."""
    from bgm import render_bgm
    from music import apply_studio_eq, normalize_studio_eq_spec

    spec = normalize_studio_eq_spec({"preset": "reggae-dub"})
    assert spec["preset"] == "reggae-dub"
    assert len(spec["bands"]) == 8
    assert spec["bands"][0] == 4.5
    assert spec["active"] is True

    custom = normalize_studio_eq_spec({
        "preset": "custom",
        "bands": [20.0, -20.0, 1.5, 0, 0, 0, 0, 3.0],
        "preamp_db": 9.0,
        "compressor": 0.5,
    })
    assert custom["bands"][0] == 12.0
    assert custom["bands"][1] == -12.0
    assert custom["preamp_db"] == 6.0

    song_file = tmp_path / "song.m4a"
    render_bgm("chill", str(song_file), 6.0)
    assert song_file.is_file() and song_file.stat().st_size > 1024

    applied = apply_studio_eq(song_file, {"preset": "reggae-dub"})
    assert applied["applied"] is True
    backup_file = tmp_path / "song.raw_master.m4a"
    assert backup_file.is_file() and backup_file.stat().st_size > 1024

    reset_res = apply_studio_eq(song_file, {"reset": True})
    assert reset_res["applied"] is False
    assert reset_res["restored_from_backup"] is True


def test_studio_equalizer_api_endpoints():
    """Verify GET /api/music/eq-presets and POST /api/music/equalize/{job_id} error handling."""
    from fastapi.testclient import TestClient
    from app import app, require_user

    app.dependency_overrides[require_user] = lambda: {"google_sub": "sub123", "email": "test@klipin.ai"}
    client = TestClient(app)

    presets_res = client.get("/api/music/eq-presets")
    assert presets_res.status_code == 200
    body = presets_res.json()
    assert body["frequencies"] == [60, 150, 400, 1000, 2400, 5000, 10000, 16000]
    assert any(p["id"] == "reggae-dub" for p in body["presets"])

    not_found = client.post("/api/music/equalize/music_nonexistent", json={"preset": "reggae-dub"})
    assert not_found.status_code == 404
    assert "tidak ditemukan" in not_found.json()["detail"].lower()

