"""Unit and integration tests for YuE2 + SheetSage2 AI Cover Architecture."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend modules are importable
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.lyric_style_agent import (
    normalize_lyrics,
    build_style_prompt,
    ASR_HALLUCINATION_PATTERN,
)
from engines.base_cover_engine import BaseCoverEngine
from engines.yue2_comfy_engine import Yue2ComfyEngine
from engines.yue2_native_engine import Yue2NativeEngine


# ============================================================================
# 1. Tests for Lyric & Style Agent (backend/services/lyric_style_agent.py)
# ============================================================================

def test_normalize_lyrics_preserves_existing_tags():
    sheet = "[Verse]\nDi malam yang sunyi\n[Chorus]\nAku tetap bernyanyi"
    normalized = normalize_lyrics(sheet)
    assert "[Verse]" in normalized
    assert "[Chorus]" in normalized
    assert "Di malam yang sunyi" in normalized
    assert "Aku tetap bernyanyi" in normalized


def test_normalize_lyrics_auto_tags_unstructured_paragraphs():
    # 2 paragraphs -> [Verse], [Chorus]
    raw = "Baris satu\nBaris dua\n\nReff pertama\nReff kedua"
    result = normalize_lyrics(raw)
    assert result.startswith("[Verse]")
    assert "[Chorus]" in result
    assert "Reff pertama" in result

    # 3 paragraphs -> [Verse], [Chorus], [Outro]
    raw_3 = "Bait satu\n\nBait dua\n\nPenutup lagu"
    res_3 = normalize_lyrics(raw_3)
    assert "[Verse]" in res_3
    assert "[Chorus]" in res_3
    assert "[Outro]" in res_3


def test_normalize_lyrics_strips_asr_hallucinations():
    raw = (
        "[Verse]\n"
        "Menatap senja di kota ini\n"
        "Terima kasih sudah menonton\n"
        "Jangan lupa subscribe\n"
        "[Chorus]\n"
        "Kau selalu di hatiku"
    )
    result = normalize_lyrics(raw)
    assert "Menatap senja di kota ini" in result
    assert "Kau selalu di hatiku" in result
    assert "Terima kasih sudah menonton" not in result
    assert "Jangan lupa subscribe" not in result


def test_normalize_lyrics_empty_fallback():
    assert "[Verse]" in normalize_lyrics("")
    assert "[Verse]" in normalize_lyrics("   ")


def test_build_style_prompt_gmvp_hierarchy():
    prompt = build_style_prompt(
        genre="reggae",
        arrangement="acoustic guitar riffs, summer island atmosphere",
        vocal_persona_desc="warm female vocals with slight rasp",
    )
    assert "reggae" in prompt.lower()
    assert "one-drop" in prompt.lower()
    assert "acoustic guitar riffs" in prompt.lower()
    assert "warm female vocals" in prompt.lower()
    assert "-14 lufs" in prompt.lower()


def test_build_style_prompt_regional_genres():
    koplo_prompt = build_style_prompt(genre="dangdut-koplo")
    assert "kendang koplo" in koplo_prompt.lower()

    jazz_prompt = build_style_prompt(genre="jazz")
    assert "piano" in jazz_prompt.lower()
    assert "walking bass" in jazz_prompt.lower()


# ============================================================================
# 2. Tests for BaseCoverEngine Contract Interface
# ============================================================================

def test_base_cover_engine_contract():
    with pytest.raises(TypeError):
        # BaseCoverEngine cannot be instantiated directly without abstract methods
        BaseCoverEngine()


# ============================================================================
# 3. Tests for ComfyUI Engine (backend/engines/yue2_comfy_engine.py)
# ============================================================================

def test_yue2_comfy_engine_init_and_availability():
    engine = Yue2ComfyEngine(base_url="http://127.0.0.1:8188")
    assert engine.engine_id == "yue2_comfy"
    assert "ComfyUI" in engine.name

    # When ComfyUI is not reachable
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        avail, reason = engine.is_available()
        assert avail is False
        assert "Connection refused" in reason

    # When ComfyUI responds 200 OK
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        avail, reason = engine.is_available()
        assert avail is True
        assert reason == ""


def test_yue2_comfy_workflow_injection(tmp_path):
    workflow_path = tmp_path / "test_wf.json"
    raw_wf = {
        "1": {"class_type": "LoadAudio", "inputs": {"audio": "old.mp3"}},
        "2": {"class_type": "SheetSage2AudioToABC", "inputs": {"melody_only": False}},
        "3": {"class_type": "YuE2StylePrompt", "inputs": {"style": "old style"}},
        "4": {"class_type": "YuE2LyricsPrompt", "inputs": {"lyrics": "old lyrics"}},
        "5": {"class_type": "YuE2Sampler", "inputs": {"seed": 1, "cot": "direct", "max_duration": 100}},
    }
    workflow_path.write_text(json.dumps(raw_wf), encoding="utf-8")

    engine = Yue2ComfyEngine(base_url="http://127.0.0.1:8188", workflow_path=workflow_path)
    populated = engine._inject_workflow_params(
        workflow=raw_wf,
        uploaded_filename="uploaded_ref.mp3",
        lyrics="[Verse]\nNew lyrics",
        style_prompt="new reggae style",
        seed=999,
        max_duration=240,
    )

    assert populated["1"]["inputs"]["audio"] == "uploaded_ref.mp3"
    assert populated["2"]["inputs"]["melody_only"] is True
    assert populated["3"]["inputs"]["style"] == "new reggae style"
    assert populated["4"]["inputs"]["lyrics"] == "[Verse]\nNew lyrics"
    assert populated["5"]["inputs"]["seed"] == 999
    assert populated["5"]["inputs"]["cot"] == "melody"
    assert populated["5"]["inputs"]["max_duration"] == 240


def test_yue2_comfy_generate_cover_flow(tmp_path):
    wf_path = tmp_path / "wf.json"
    wf_path.write_text(json.dumps({"1": {"class_type": "LoadAudio", "inputs": {}}}), encoding="utf-8")
    engine = Yue2ComfyEngine(base_url="http://127.0.0.1:8188", workflow_path=wf_path)

    ref_audio = tmp_path / "source.mp3"
    ref_audio.write_bytes(b"dummy mp3 source bytes" * 100)

    # Mock availability, upload, queue prompt, poll history, and download
    with patch.object(engine, "is_available", return_value=(True, "")), \
         patch.object(engine, "upload_audio", return_value="source.mp3"), \
         patch.object(engine, "queue_prompt", return_value="prompt-abc-123"), \
         patch.object(engine, "poll_history", return_value={
             "7": {"audio": [{"filename": "cover_out.wav", "subfolder": "music"}]}
         }), \
         patch.object(engine, "download_output_audio") as mock_dl:

        def fake_download(*args, **kwargs):
            tgt = kwargs.get("target_path") or tmp_path / "downloaded.mp3"
            tgt.write_bytes(b"fake rendered music" * 200)
            return tgt

        mock_dl.side_effect = fake_download

        res = engine.generate_cover(
            source_audio_path=ref_audio,
            lyrics="[Verse]\nTes lagu",
            genre_style="reggae groove",
            seed=42,
            max_duration=60,
            output_dir=tmp_path / "out",
        )

        assert res["engine"] == "yue2_comfy"
        assert res["status"] == "completed"
        assert res["metadata"]["cot"] == "melody"
        assert res["metadata"]["backend"] == "comfyui_headless"
        assert Path(res["audio_path"]).exists()


# ============================================================================
# 4. Tests for Native Python Engine (backend/engines/yue2_native_engine.py)
# ============================================================================

def test_yue2_native_engine_init_and_availability():
    engine = Yue2NativeEngine()
    assert engine.engine_id == "yue2_native"
    assert "Native" in engine.name

    # When torch or cuda is not available
    with patch.dict("sys.modules", {"torch": None}):
        avail, reason = engine.is_available()
        assert avail is False


def test_yue2_native_extract_symbolic_melody_mock(tmp_path):
    engine = Yue2NativeEngine()
    fake_audio = tmp_path / "fake.mp3"
    fake_audio.write_bytes(b"dummy audio data")

    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = {"abc": "X:1\nT:Test\nM:4/4\nK:C\nC D E F|G2 G2|"}

    mock_automodel = MagicMock()
    mock_automodel.from_pretrained.return_value.to.return_value = mock_transcriber

    with patch.dict("sys.modules", {
        "torch": mock_torch,
        "transformers": MagicMock(AutoModel=mock_automodel),
    }):
        abc = engine.extract_symbolic_melody(fake_audio)
        assert "K:C" in abc
        assert "C D E F" in abc
        mock_transcriber.transcribe.assert_called_once_with(str(fake_audio), melody_only=True)


# ============================================================================
# 5. Integration Test: backend/music.py generate_music with Yue2
# ============================================================================

def test_generate_music_with_yue2_comfy_mock(tmp_path, monkeypatch):
    import music as music_module

    fake_source = tmp_path / "source.mp3"
    fake_source.write_bytes(b"dummy audio source" * 50)
    out_dir = tmp_path / "job_yue2"

    def fake_generate_cover(self, *args, **kwargs):
        rendered = out_dir / "rendered_yue2.mp3"
        rendered.parent.mkdir(parents=True, exist_ok=True)
        rendered.write_bytes(b"rendered audio data" * 200)
        return {
            "audio_path": str(rendered),
            "duration": 45.0,
            "engine": "yue2_comfy",
            "status": "completed",
            "metadata": {"cot": "melody", "model": "m-a-p/YuE2-3B"},
        }

    monkeypatch.setattr(Yue2ComfyEngine, "generate_cover", fake_generate_cover)
    monkeypatch.setattr(music_module, "_fetch_cover_art", lambda *args, **kwargs: ("", ""))
    monkeypatch.setattr(music_module, "export_wav_lufs", lambda *args, **kwargs: "")
    monkeypatch.setattr(music_module, "probe_audio_seconds", lambda p: 45.0)

    spec = {
        "title": "Aishiteru Reggae Cover",
        "prompt": "santai pantai",
        "lyrics": "[Verse]\nMenunggu dalam hening\n[Chorus]\nAishiteru",
        "genre": "reggae",
        "duration": 45,
        "cover_mode": True,
        "cover_engine": "yue2_comfy",
        "ref_audio_path": str(fake_source),
    }

    result = music_module.generate_music(spec, out_dir)
    assert result["provider"] == "yue2_comfy-cover"
    assert result["metadata"]["cover_fidelity"] == "symbolic-melody-conditioned"
    assert result["metadata"]["cover_engine"] == "yue2_comfy"
    assert (out_dir / result["audio_filename"]).stat().st_size > 100


# ============================================================================
# 6. Integration Test: backend/app.py music_genres and cover_engines
# ============================================================================

def test_app_cover_engines_includes_yue2():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    # Mock require_user dependency
    from app import require_user
    app.dependency_overrides[require_user] = lambda: {"google_sub": "test_user_sub_123", "email": "test@klipin.id"}

    try:
        resp = client.get("/api/music/genres")
        assert resp.status_code == 200
        data = resp.json()
        cover_engine_ids = [e["id"] for e in data.get("cover_engines", [])]
        assert "yue2_comfy" in cover_engine_ids
        assert "yue2_native" in cover_engine_ids
    finally:
        app.dependency_overrides.clear()
