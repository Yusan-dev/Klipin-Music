from pathlib import Path

import pytest

from music import (GENRE_LANGUAGE, LANGUAGES, cache_key, engine_status,
                   generate_music, genre_info, language_info, language_options,
                   normalize_lyrics, normalize_language_mix, provider_hint,
                   _duet_role_lyrics, _seed_vc_convert,
                   resolve_language, tags_for, annotate_lyric_languages)


def test_local_genres_include_javanese_options():
    assert genre_info("pop-jawa")["mood"] == "upbeat"
    assert genre_info("campursari")["label"] == "Campursari"


def test_lyrics_are_timed_and_sections_preserved():
    rows = normalize_lyrics("[Verse]\nSatu\nDua\n[Chorus]\nTiga", 12)
    assert len(rows) == 3
    assert rows[0]["section"] == "Verse"
    assert rows[-1]["section"] == "Chorus"
    assert rows[0]["start"] == 0
    assert rows[-1]["end"] == 12


def test_duet_role_lyrics_alternate_verses_and_share_hook_sections():
    lyrics = ("[Verse]\nBaris wanita\n[Chorus]\nHook bersama\n"
              "[Verse]\nBaris pria\n[Bridge]\nJembatan bersama")
    female = _duet_role_lyrics(lyrics, "female")
    male = _duet_role_lyrics(lyrics, "male")
    assert "Baris wanita" in female
    assert "Baris pria" not in female
    assert "Baris pria" in male
    assert "Baris wanita" not in male
    assert "Hook bersama" in female and "Hook bersama" in male
    assert "Jembatan bersama" in female and "Jembatan bersama" in male


def test_seed_vc_adapter_is_safe_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("KLIPIN_SEED_VC_ROOT", raising=False)
    source = tmp_path / "source.mp3"
    reference = tmp_path / "reference.wav"
    source.write_bytes(b"source")
    reference.write_bytes(b"reference")
    output, mode = _seed_vc_convert(source, tmp_path / "converted.mp3", reference)
    assert output == source
    assert mode == "unconfigured"


def test_music_cache_key_is_stable():
    spec = {"prompt": "jawa", "lyrics": "halo", "genre": "pop-jawa", "language": "id", "provider": "auto", "seed": 4, "duration": 30}
    assert cache_key(spec) == cache_key(dict(spec))


def test_full_song_never_silently_falls_back_to_instrumental(tmp_path, monkeypatch):
    monkeypatch.delenv("KLIPIN_ACE_STEP_URL", raising=False)
    monkeypatch.delenv("KLIPIN_ACE-STEP_COMMAND", raising=False)
    monkeypatch.delenv("KLIPIN_HEARTMULA_COMMAND", raising=False)
    # Tanpa ini `music.py` memuat backend/.env dan mencoba jalan ke worker GPU
    # Modal sungguhan dari dalam test — pastikan jalur generatif mati total.
    monkeypatch.delenv("KLIPIN_HEARTMULA_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_YUE2_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_ACE_STEP_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_HEARTMULA_ROOT", raising=False)
    monkeypatch.delenv("KLIPIN_HEARTMULA_MODEL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="Full-song generation requires"):
        generate_music({"prompt": "lagu Jawa", "genre": "jazz-pop",
                        "lyrics": "[Verse]\nIki lagu",
                        "duration": 30, "provider": "auto", "full_song": True}, tmp_path)


def test_persisted_modal_provider_labels_are_normalized():
    from music import _normalize_provider

    assert _normalize_provider("heartmula-modal") == "heartmula"
    assert _normalize_provider("heartmula-modal-duet") == "heartmula"
    assert _normalize_provider("ace-step-modal") == "ace-step"
    assert _normalize_provider("diffrhythm-modal") == "diffrhythm"
    assert _normalize_provider("auto") == "auto"


def test_no_engine_message_names_what_to_configure(monkeypatch):
    monkeypatch.delenv("KLIPIN_HEARTMULA_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_DIFFRHYTHM_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_ACE_STEP_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_MULACOVER_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_HEARTMULA_ROOT", raising=False)
    monkeypatch.delenv("KLIPIN_ACE_STEP_URL", raising=False)
    hint = provider_hint()
    assert "backend/.env" in hint
    assert "KLIPIN_DIFFRHYTHM_MODAL_ENABLED" in hint


def test_diffrhythm_is_primary_music_provider_when_enabled(monkeypatch):
    monkeypatch.delenv("KLIPIN_HEARTMULA_MODAL_ENABLED", raising=False)
    monkeypatch.setenv("KLIPIN_DIFFRHYTHM_MODAL_ENABLED", "1")
    from music import engine_status, provider_hint

    assert engine_status(False)["provider"] == "diffrhythm-modal"
    assert "DiffRhythm Modal" in provider_hint()


def test_diffrhythm_dispatch_is_music_only_and_records_model_license(tmp_path, monkeypatch):
    import music as music_module

    monkeypatch.setenv("KLIPIN_DIFFRHYTHM_MODAL_ENABLED", "1")
    monkeypatch.delenv("KLIPIN_HEARTMULA_MODAL_ENABLED", raising=False)
    monkeypatch.delenv("KLIPIN_ACE_STEP_MODAL_ENABLED", raising=False)
    monkeypatch.setattr(music_module, "_fetch_cover_art", lambda *args, **kwargs: ("", ""))
    monkeypatch.setattr(music_module, "export_wav_lufs", lambda *args, **kwargs: "")
    monkeypatch.setattr(music_module, "analyze_bpm_key", lambda *args, **kwargs: {})

    def fake_diffrhythm(spec, out_path, log=None):
        out_path.write_bytes(b"diffrhythm-audio" * 300)
        return {"model": "ASLP-lab/DiffRhythm-full", "license_note": "Apache-2.0"}

    monkeypatch.setattr(music_module, "_diffrhythm_modal_generate", fake_diffrhythm)
    result = music_module.generate_music({
        "prompt": "instrumental hangat",
        "genre": "ambient",
        "duration": 30,
        "provider": "diffrhythm",
        "instrumental": True,
        "full_song": True,
    }, tmp_path)

    assert result["provider"] == "diffrhythm-modal"
    assert result["metadata"]["model"] == "ASLP-lab/DiffRhythm-full"
    assert result["metadata"]["license_note"] == "Apache-2.0"


def test_suno_pipe_headers_and_security_guards():
    from music import is_safe_public_url, normalize_lyrics, sung_line_count
    from song_plan import format_pipe_header

    header = format_pipe_header("Chorus", "chorus", "lead with harmony", "full band")
    assert header == "[Chorus | Explosive Anthemic | lead with harmony | full band]"
    sheet = f"{header}\n*thunder rolls*\nKita bernyanyi bersama\n[End]"
    assert sung_line_count(sheet) == 1
    rows = normalize_lyrics(sheet, 12.0)
    assert len(rows) == 1
    assert rows[0]["section"] == "Chorus"
    assert rows[0]["text"] == "Kita bernyanyi bersama"

    assert is_safe_public_url("http://127.0.0.1:8000/admin")[0] is False
    assert is_safe_public_url("http://169.254.169.254/latest/meta-data")[0] is False
    assert is_safe_public_url("http://localhost:3000/")[0] is False


def test_pop_jawa_locks_vocal_language_to_jawa():
    assert resolve_language("pop-jawa", "id") == ("jv", True)
    assert resolve_language("pop-jawa", "") == ("jv", True)
    assert resolve_language("campursari", "en") == ("jv", True)
    assert resolve_language("dangdut-koplo", "id") == ("jv", True)
    assert resolve_language("sunda", "") == ("su", True)
    assert resolve_language("bali", "") == ("ban", True)
    assert resolve_language("keroncong", "") == ("id", True)
    assert resolve_language("kpop", "") == ("ko", True)
    assert resolve_language("jpop", "") == ("ja", True)
    assert resolve_language("arab", "") == ("ar", True)


def test_global_genres_leave_language_free():
    for genre in ("pop", "edm", "rock", "rnb", "jazz", "lofi", "hiphop",
                  "folk", "akustik", "metal", "ambient", "afrobeats"):
        assert genre not in GENRE_LANGUAGE
    assert resolve_language("pop", "en") == ("en", False)
    assert resolve_language("pop", "") == ("id", False)


def test_advanced_override_can_unlock_a_locked_genre():
    assert resolve_language("pop-jawa", "id", override=True) == ("id", False)
    # Override tanpa pilihan bahasa tetap memakai bahasa genre.
    assert resolve_language("pop-jawa", "", override=True) == ("jv", False)


def test_language_tags_are_injected_into_provider_tags():
    tags = tags_for({"genre": "pop-jawa", "language": "jv",
                     "vocal_gender": "duet", "prompt": "Lagu hangat"})
    assert "javanese vocal" in tags
    assert "javanese lyrics" in tags
    assert "duet" in tags
    # Format tags HeartMuLa: koma tanpa spasi sesudahnya.
    assert ", " not in tags
    indonesia = tags_for({"genre": "pop", "language": "id",
                          "vocal_gender": "auto", "prompt": "Lagu sore"})
    assert "indonesian vocal" in indonesia
    assert ", " not in indonesia


def test_bilingual_language_mix_validates_and_annotates_sections():
    assert normalize_language_mix("jv", "ko", "chorus") == ("ko", "chorus")
    rows = annotate_lyric_languages([
        {"text": "siji", "section": "Verse"},
        {"text": "hana", "section": "Chorus"},
    ], {"language": "jv", "language_secondary": "ko", "language_mix": "chorus"})
    assert [row["language"] for row in rows] == ["jv", "ko"]


def test_bilingual_language_mix_rejects_same_language():
    import pytest
    with pytest.raises(ValueError, match="berbeda"):
        normalize_language_mix("jv", "jv", "chorus")


def test_languages_cover_the_previous_ui_dropdown():
    ids = {item["id"] for item in LANGUAGES}
    for expected in ("jv", "id", "ms", "ja", "ko", "zh", "hi", "th", "vi", "tl",
                     "ar", "fa", "tr", "ur", "en", "es", "pt", "fr", "de",
                     "it", "ru", "su", "ban"):
        assert expected in ids
    assert language_info("jv")["label"] == "Jawa"
    noroeste = [item for item in language_options() if item["id"] == "jv"]
    assert noroeste and noroeste[0]["locked_for"]



def test_arrangement_prompt_for_genre_is_genre_specific():
    from music import _arrangement_prompt_for_genre, genre_info

    reggae_info = genre_info("reggae")
    reggae_prompt = _arrangement_prompt_for_genre("reggae", reggae_info, bpm=92, key="C major")
    assert "one-drop reggae drum groove" in reggae_prompt
    assert "offbeat guitar" in reggae_prompt
    assert "92 BPM" in reggae_prompt
    assert "key of C major" in reggae_prompt
    assert "kendang" not in reggae_prompt.lower()

    koplo_info = genre_info("dangdut-koplo")
    koplo_prompt = _arrangement_prompt_for_genre("dangdut-koplo", koplo_info, bpm=132)
    assert "kendang koplo" in koplo_prompt.lower()
    assert "132 BPM" in koplo_prompt


def test_arrangement_cover_preserves_original_vocal_and_restyles_backing(tmp_path, monkeypatch):
    import base64
    from bgm import render_bgm
    from music import generate_music

    monkeypatch.setenv("KLIPIN_VOICE_MODAL_ENABLED", "0")
    monkeypatch.setenv("KLIPIN_ACE_STEP_MODAL_ENABLED", "0")
    monkeypatch.setenv("KLIPIN_MULACOVER_MODAL_ENABLED", "0")

    src_audio = tmp_path / "source.m4a"
    render_bgm("upbeat", str(src_audio), 8.0)
    src_b64 = base64.b64encode(src_audio.read_bytes()).decode("ascii")

    out_dir = tmp_path / "job_arrange"
    result = generate_music(
        {
            "title": "Pop Reggae Cover",
            "prompt": "santai di pantai",
            "lyrics": "[Verse]\nNada santai menemani sore",
            "genre": "reggae",
            "duration": 8,
            "cover_mode": True,
            "cover_engine": "arrangement",
            "cover_source_b64": src_b64,
        },
        out_dir,
    )
    assert result["provider"] == "arrangement-cover"
    assert result["metadata"]["cover_engine"] == "arrangement"
    assert result["metadata"]["cover_fidelity"] == "original-vocal+genre-restyle"
    assert (out_dir / result["audio_filename"]).stat().st_size > 1024


def test_standalone_music_service_exposes_only_music_and_shared_routes():
    import subprocess
    import sys

    code = (
        "import music_service; "
        "paths = {getattr(r, 'path', '') for r in music_service.app.router.routes}; "
        "assert any(p.startswith('/api/music') for p in paths), paths; "
        "assert '/api/music/service-info' in paths, paths; "
        "assert '/api/process' not in paths, paths; "
        "assert '/api/projects' not in paths, paths; "
        "print('STANDALONE_OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STANDALONE_OK" in proc.stdout
