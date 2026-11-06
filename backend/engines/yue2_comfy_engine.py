"""ComfyUI Headless API Cover Engine (SheetSage2 + YuE2).

Executes melody extraction via SheetSage2 and melody-conditioned generation
via YuE2 on an external ComfyUI server instance using INT8 ConvRot + Tiled VAE
for 6GB-8GB VRAM footprint.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from engines.base_cover_engine import BaseCoverEngine


class Yue2ComfyEngine(BaseCoverEngine):
    """Engine executing SheetSage2 + YuE2 cover generation via ComfyUI REST API."""

    def __init__(self, base_url: str | None = None, workflow_path: Path | None = None) -> None:
        raw_url = base_url or os.environ.get("COMFYUI_URL") or "http://127.0.0.1:8188"
        self.base_url = raw_url.rstrip("/")
        default_wf = Path(__file__).resolve().parent.parent.parent / "workflows" / "yue2_cover_api.json"
        self.workflow_path = workflow_path or default_wf

    @property
    def engine_id(self) -> str:
        return "yue2_comfy"

    @property
    def name(self) -> str:
        return "YuE2 + SheetSage2 (ComfyUI Headless API — VRAM 6-8GB)"

    def is_available(self) -> tuple[bool, str]:
        """Check if ComfyUI server is reachable."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/system_stats",
                headers={"User-Agent": "Klipin-Music/1.0"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    return True, ""
                return False, f"ComfyUI mengembalikan HTTP {resp.status}"
        except Exception as e:
            return False, f"ComfyUI tidak dapat dihubungi di {self.base_url}: {e}"

    def upload_audio(self, audio_path: Path) -> str:
        """Upload source audio to ComfyUI input directory via multipart/form-data."""
        audio_path = Path(audio_path).resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(f"Source audio not found: {audio_path}")

        boundary = f"----WebKitFormBoundary{os.urandom(16).hex()}"
        filename = audio_path.name
        content_type = "audio/mpeg" if audio_path.suffix.lower() == ".mp3" else "audio/wav"

        body = bytearray()
        # image/file field
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(audio_path.read_bytes())
        body.extend(b"\r\n")

        # overwrite field
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="overwrite"\r\n\r\n1\r\n')

        # type field
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="type"\r\n\r\ninput\r\n')
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        upload_url = f"{self.base_url}/upload/image"
        req = urllib.request.Request(
            upload_url,
            data=bytes(body),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Klipin-Music/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                return str(res_data.get("name") or filename)
        except urllib.error.URLError as err:
            raise RuntimeError(f"Gagal mengunggah audio ke ComfyUI di {upload_url}: {err}") from err

    def queue_prompt(self, workflow: dict[str, Any], client_id: str = "klipin_music") -> str:
        """Submit a prompt execution request to ComfyUI."""
        payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/prompt",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Klipin-Music/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    raise RuntimeError(f"ComfyUI /prompt response missing prompt_id: {data}")
                return str(prompt_id)
        except urllib.error.URLError as err:
            raise RuntimeError(f"Gagal mengirim workflow ke ComfyUI /prompt: {err}") from err

    def poll_history(self, prompt_id: str, timeout_sec: float = 600.0, poll_interval: float = 2.0, log: Any = None) -> dict[str, Any]:
        """Poll /history/{prompt_id} until completion or error."""
        start_time = time.time()
        url = f"{self.base_url}/history/{prompt_id}"

        while time.time() - start_time < timeout_sec:
            req = urllib.request.Request(url, headers={"User-Agent": "Klipin-Music/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                log and log(f"Polling warning: {e}")
                time.sleep(poll_interval)
                continue

            if prompt_id in history:
                item = history[prompt_id]
                status_info = item.get("status", {})
                if status_info.get("status_str") == "error":
                    messages = status_info.get("messages", [])
                    raise RuntimeError(f"ComfyUI workflow error: {messages}")
                outputs = item.get("outputs", {})
                if outputs:
                    return outputs

            time.sleep(poll_interval)

        raise TimeoutError(f"ComfyUI generation timed out after {timeout_sec:.0f}s (prompt_id={prompt_id})")

    def download_output_audio(self, filename: str, subfolder: str = "", file_type: str = "output", target_path: Path | None = None) -> Path:
        """Download rendered audio file from ComfyUI /view endpoint."""
        query = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": file_type,
        })
        url = f"{self.base_url}/view?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Klipin-Music/1.0"})

        if target_path is None:
            target_path = Path("output.wav")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with urllib.request.urlopen(req, timeout=120.0) as resp, open(target_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        return target_path

    def _inject_workflow_params(
        self,
        workflow: dict[str, Any],
        uploaded_filename: str,
        lyrics: str,
        style_prompt: str,
        seed: int | None = None,
        max_duration: int = 180,
    ) -> dict[str, Any]:
        """Inject runtime cover parameters into the ComfyUI workflow JSON graph."""
        wf = json.loads(json.dumps(workflow))

        # Scan nodes and populate appropriate inputs
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            ctype = node.get("class_type", "")
            inputs = node.get("inputs", {})

            if ctype in ("LoadAudio", "LoadAudioPath"):
                inputs["audio"] = uploaded_filename
            elif ctype in ("SheetSage2AudioToABC", "SheetSage2Transcriber"):
                inputs["melody_only"] = True
                inputs["mode"] = "melody"
            elif ctype == "YuE2GenerateMusic":
                inputs["style"] = style_prompt
                inputs["lyrics"] = lyrics
                inputs["cot"] = "melody"
                if seed is not None:
                    inputs["seed"] = seed
            elif ctype == "EmptyYuE2LatentAudio":
                inputs["seconds"] = max_duration
            elif ctype in ("YuE2StylePrompt", "StylePrompt"):
                inputs["style"] = style_prompt
            elif ctype in ("YuE2LyricsPrompt", "LyricsPrompt"):
                inputs["lyrics"] = lyrics
            elif ctype in ("YuE2Sampler", "KSampler", "YuE2Pipeline"):
                if seed is not None:
                    inputs["seed"] = seed
                inputs["cot"] = "melody"
                inputs["max_duration"] = max_duration

        return wf

    def generate_cover(
        self,
        source_audio_path: str | Path,
        lyrics: str,
        genre_style: str,
        vocal_persona: str = "",
        seed: int | None = None,
        max_duration: int = 180,
        output_dir: str | Path = "",
        extra_params: dict[str, Any] | None = None,
        log: Any = None,
    ) -> dict[str, Any]:
        """Execute cover generation via ComfyUI."""
        available, reason = self.is_available()
        if not available:
            raise RuntimeError(f"Engine ComfyUI tidak aktif: {reason}")

        if not self.workflow_path.is_file():
            raise FileNotFoundError(f"Workflow template tidak ditemukan: {self.workflow_path}")

        work_dir = Path(output_dir or ".").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        log and log(f"🚀 [YuE2 ComfyUI] Mengunggah audio referensi: {source_audio_path}")
        uploaded_name = self.upload_audio(Path(source_audio_path))

        log and log(f"🧩 [YuE2 ComfyUI] Menyiapkan workflow (max_duration={max_duration}s, seed={seed})")
        workflow_template = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        populated_wf = self._inject_workflow_params(
            workflow=workflow_template,
            uploaded_filename=uploaded_name,
            lyrics=lyrics,
            style_prompt=genre_style,
            seed=seed,
            max_duration=max_duration,
        )

        log and log("📤 [YuE2 ComfyUI] Mengirim antrean prompt ke ComfyUI...")
        prompt_id = self.queue_prompt(populated_wf)

        timeout = float(os.environ.get("COMFYUI_TIMEOUT", 600.0))
        log and log(f"⏳ [YuE2 ComfyUI] Menunggu eksekusi prompt {prompt_id}...")
        outputs = self.poll_history(prompt_id, timeout_sec=timeout, log=log)

        # Locate audio output from nodes
        audio_filename = ""
        subfolder = ""
        for node_id, node_output in outputs.items():
            if isinstance(node_output, dict):
                # Standard SaveAudio node outputs audio list
                audio_list = node_output.get("audio") or node_output.get("ui", {}).get("audio")
                if audio_list and isinstance(audio_list, list):
                    first_audio = audio_list[0]
                    audio_filename = first_audio.get("filename", "")
                    subfolder = first_audio.get("subfolder", "")
                    break

        if not audio_filename:
            # Fallback search for any .wav/.mp3 file mentioned in outputs
            out_str = json.dumps(outputs)
            import re
            m = re.search(r'"filename":\s*"([^"]+\.(?:wav|mp3|flac|m4a))"', out_str)
            if m:
                audio_filename = m.group(1)
            else:
                raise RuntimeError(f"Tidak ada file audio ditemukan dalam output ComfyUI: {outputs}")

        target_audio = work_dir / f"yue2_cover_{prompt_id[:8]}.mp3"
        log and log(f"📥 [YuE2 ComfyUI] Mengunduh output audio {audio_filename}...")
        self.download_output_audio(
            filename=audio_filename,
            subfolder=subfolder,
            target_path=target_audio,
        )

        # Determine output duration
        from music import probe_audio_seconds
        dur = probe_audio_seconds(target_audio)

        return {
            "audio_path": str(target_audio),
            "duration": dur or float(max_duration),
            "engine": self.engine_id,
            "status": "completed",
            "metadata": {
                "model": "m-a-p/YuE2-3B",
                "symbolic_transcription": "m-a-p/SheetSage2",
                "cot": "melody",
                "backend": "comfyui_headless",
                "prompt_id": prompt_id,
                "vram_mode": "INT8-ConvRot+TiledVAE",
            },
        }
