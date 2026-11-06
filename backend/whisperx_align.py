#!/usr/bin/env python3
"""WhisperX integration for Klipin - replaces align.py energy heuristic.

This module provides CTC-based forced alignment using wav2vec2,
which is the same approach used by TikTok/CapCut (ByteDance FireRed).

Falls back to align.py energy heuristic if WhisperX unavailable.
"""

import os
import subprocess
import tempfile
import logging
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

# Global cache for alignment model
_align_model = None
_align_processor = None
_device = "cpu"


def _load_whisperx_align_model(language: str = "id"):
    """Load wav2vec2 alignment model for the given language."""
    global _align_model, _align_processor
    
    if _align_model is not None:
        return _align_model, _align_processor
    
    try:
        import whisperx
        import torch
        
        # Use CPU by default (Klipin containers may not have GPU)
        device = _device
        
        # WhisperX has limited language-specific models. Indonesian ('id') is not
        # in the default list, so we fall back to English multilingual model
        # which works well for many languages via XLSR pretraining.
        # Supported: ja, zh, nl, uk, pt, ar, cs, ru, pl, hu, fi, fa, el, tr, 
        #            da, he, vi, ko, ur, te, hi, ca, ml, no, nn, sk, sl, hr, 
        #            ro, eu, gl, ka, lv, tl, en (torchaudio fallback)
        supported_langs = {"ja", "zh", "nl", "uk", "pt", "ar", "cs", "ru", "pl", 
                          "hu", "fi", "fa", "el", "tr", "da", "he", "vi", "ko", 
                          "ur", "te", "hi", "ca", "ml", "no", "nn", "sk", "sl", 
                          "hr", "ro", "eu", "gl", "ka", "lv", "tl"}
        
        align_lang = language if language in supported_langs else "en"
        
        log.info(f"Loading WhisperX alignment model for language '{align_lang}' (requested: '{language}') on {device}")
        _align_model, _align_processor = whisperx.load_align_model(
            language_code=align_lang,
            device=device
        )
        return _align_model, _align_processor
    except Exception as e:
        log.warning(f"Failed to load WhisperX alignment model: {e}")
        return None, None


def align_words_whisperx(audio_path: str, words: List[Dict], language: str = "id") -> List[Dict]:
    """
    Align word timestamps using WhisperX (wav2vec2 CTC alignment).
    
    This replaces the energy-based heuristic in align.py with true
    acoustic model forced alignment.
    
    Args:
        audio_path: Path to audio file (will be converted to 16kHz mono)
        words: List of {word, start, end} from whisper
        language: Language code (default 'id' for Indonesian)
    
    Returns:
        Refined word list with improved timestamps
    """
    if not words:
        return words
    
    try:
        import whisperx
        import torch
    except ImportError:
        log.warning("WhisperX not installed, falling back to energy alignment")
        return _fallback_energy_alignment(audio_path, words)
    
    model, processor = _load_whisperx_align_model(language)
    if model is None:
        return _fallback_energy_alignment(audio_path, words)
    
    try:
        # Convert audio to 16kHz mono if needed
        wav_path = _ensure_16khz_mono(audio_path)
        
        # Prepare segments for whisperx alignment
        # whisperx expects segments with 'text' and word-level info
        segments = [{
            "text": " ".join(w.get("word", "") for w in words),
            "words": words,
            "start": words[0].get("start", 0),
            "end": words[-1].get("end", words[0].get("start", 0) + 10)
        }]
        
        # Run alignment
        result = whisperx.align(
            segments,
            model,
            processor,
            wav_path,
            _device,
            return_char_alignments=False
        )
        
        # Extract refined words
        refined_words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                if "word" in w and "start" in w and "end" in w:
                    refined_words.append({
                        "word": w["word"],
                        "start": round(float(w["start"]), 3),
                        "end": round(float(w["end"]), 3),
                        "score": w.get("score", 1.0)
                    })
        
        # Cleanup temp file
        if wav_path != audio_path:
            try:
                os.remove(wav_path)
            except Exception:
                pass
        
        if refined_words:
            log.info(f"WhisperX aligned {len(refined_words)} words")
            return refined_words
        else:
            log.warning("WhisperX returned no words, falling back")
            return _fallback_energy_alignment(audio_path, words)
            
    except Exception as e:
        log.warning(f"WhisperX alignment failed: {e}, falling back to energy")
        return _fallback_energy_alignment(audio_path, words)


def _ensure_16khz_mono(audio_path: str) -> str:
    """Ensure audio is 16kHz mono WAV for WhisperX."""
    import subprocess
    
    # Check if already correct format
    try:
        import soundfile as sf
        info = sf.info(audio_path)
        if info.samplerate == 16000 and info.channels == 1:
            return audio_path
    except Exception:
        pass
    
    # Convert using ffmpeg
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", audio_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-sample_fmt", "s16", tmp
    ], capture_output=True, check=True)
    return tmp


def _fallback_energy_alignment(audio_path: str, words: List[Dict]) -> List[Dict]:
    """Fallback to original align.py energy heuristic."""
    try:
        from align import align_words
        return align_words(audio_path, words)
    except Exception as e:
        log.error(f"Fallback alignment also failed: {e}")
        return words


def align_transcript_whisperx(transcript: Dict, audio_path: str, 
                               language: str = "id") -> Dict:
    """
    Align all word timestamps in a transcript using WhisperX.
    
    Drop-in replacement for align.align_transcript.
    """
    segments = transcript.get("segments") or []
    total_words = 0
    
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            continue
        total_words += len(words)
        refined = align_words_whisperx(audio_path, words, language)
        seg["words"] = refined
        if refined:
            seg["start"] = refined[0].get("start", seg.get("start", 0))
            seg["end"] = refined[-1].get("end", seg.get("end", 0))
    
    if total_words > 0:
        log.info(f"WhisperX forced alignment complete: {total_words} words refined")
    
    return transcript


# =====================================================================
# Integration point for transcribe.py
# =====================================================================

def maybe_align_with_whisperx(transcript: Dict, audio_path: str, 
                               language: str = "id") -> Dict:
    """
    Main entry point - tries WhisperX first, falls back to energy alignment.
    
    Call this from transcribe.py after getting whisper results.
    """
    # Check if WhisperX is available
    try:
        import whisperx
        import torch
        has_whisperx = True
    except ImportError:
        has_whisperx = False
    
    if has_whisperx:
        log.info("Using WhisperX (wav2vec2 CTC) for forced alignment")
        return align_transcript_whisperx(transcript, audio_path, language)
    else:
        log.info("WhisperX not available, using energy heuristic alignment")
        from align import align_transcript
        return align_transcript(transcript, audio_path)


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    
    # Test import
    try:
        import whisperx
        print("✅ WhisperX available")
    except ImportError:
        print("❌ WhisperX not installed")
    
    # Test alignment model loading
    model, processor = _load_whisperx_align_model("id")
    if model:
        print("✅ Alignment model loaded")
    else:
        print("❌ Alignment model failed to load")