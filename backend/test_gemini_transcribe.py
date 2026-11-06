"""Quick test: transcribe an audio file with gemini-3.5-transcribe."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise RuntimeError("Set GEMINI_API_KEY in the environment before running this test")

from google import genai

client = genai.Client()

audio_path = "output/1a000f41c00-0/audio.mp3"
print(f"Uploading {audio_path} …")
audio_file = client.files.upload(file=audio_path)
print(f"  uri={audio_file.uri} mime={audio_file.mime_type}")

print("Transcribing with gemini-3.5-transcribe …")
interaction = client.interactions.create(
    model="gemini-3.5-transcribe",
    input=[
        {
            "type": "audio",
            "uri": audio_file.uri,
            "mime_type": audio_file.mime_type,
        }
    ],
    generation_config={
        "transcription_config": {
            # auto language detection; id-ID is supported
            "language_codes": ["id-ID"],
            "mode": {
                "type": "verbatim",
                "timestamp_granularities": ["word"],
            },
        }
    },
)

text = getattr(interaction, "output_text", "")
print("=== TRANSCRIPT (first 600 chars) ===")
print(text[:600])
print(f"\n[total length: {len(text)} chars]")

# extract word annotations
words = []
for step in getattr(interaction, "steps", []) or []:
    for content in getattr(step, "content", []) or []:
        for ann in getattr(content, "annotations", []) or []:
            if getattr(ann, "type", None) == "word_info":
                words.append(ann)
print(f"\n[word annotations: {len(words)}]")
for w in words[:8]:
    sp = f"[{w.speaker}] " if getattr(w, "speaker", None) else ""
    print(f"  {sp}{w.start_offset}->{w.end_offset} {w.text}")
