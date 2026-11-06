"""Face swap — swap a user-supplied face onto any clip, fully local & free.

Ported from QuClippers `faceswap_free.py`: InsightFace (`buffalo_l` +
`inswapper_128.onnx`) runs 100 % on this machine — no API key, no credits,
no upload to third parties. The original clip audio is re-muxed back after
swapping.

Models download once on first use (~400 MB) into `INSIGHTFACE_HOME`
(default: backend/.insightface, which is volume-mounted so it survives
rebuilds). InsightFace is imported lazily: the rest of Klipin works even
when the heavy deps aren't installed.
"""
import os
import subprocess
import time

from gpu import encode_args

_INSIGHTFACE_HOME = os.environ.get(
    "INSIGHTFACE_HOME",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".insightface"),
)
os.environ.setdefault("INSIGHTFACE_HOME", _INSIGHTFACE_HOME)

_enabled = True
_IMPORT_ERROR = None
try:
    import cv2
    from insightface.app import FaceAnalysis
    import insightface
except Exception as _e:  # noqa: BLE001
    _enabled = False
    _IMPORT_ERROR = _e


def is_available() -> bool:
    return _enabled


def availability_reason() -> str:
    return (f"InsightFace unavailable: {_IMPORT_ERROR}" if _IMPORT_ERROR
            else "ok")


class FreeFaceSwapper:
    """Local face swap engine (buffalo_l + inswapper_128)."""

    def __init__(self):
        if not is_available():
            raise RuntimeError(availability_reason())
        self.app = FaceAnalysis(
            name="buffalo_l",
            root=_INSIGHTFACE_HOME,
            providers=["CPUExecutionProvider"],  # CPU-only, no CUDA warnings
            allowed_modules=["detection", "recognition"],
        )
        self.app.prepare(ctx_id=-1, det_size=(640, 640))  # CPU only

        # insightface 1.0.1 has a bug: download_onnx() returns None after a
        # successful (non-zip) download, so get_model(... download=True)
        # raises. Trigger the download, then load from the resolved path.
        model_file = os.path.join(_INSIGHTFACE_HOME, "models",
                                  "inswapper_128.onnx")
        if not os.path.exists(model_file):
            try:
                insightface.model_zoo.get_model(
                    "inswapper_128.onnx", root=_INSIGHTFACE_HOME,
                    download=True)
            except Exception:  # noqa: BLE001
                pass
        if not os.path.exists(model_file):
            raise RuntimeError("inswapper_128.onnx download failed")
        self.swapper = insightface.model_zoo.get_model(
            model_file, download=False)


def process_faceswap(clip_path: str, face_image_path: str, out_path: str,
                     frame_step: int = 2, log=None) -> str:
    """Swap faces in `clip_path` with the one from `face_image_path`.

    `frame_step=2` swaps every 2nd frame for speed (visually identical for
    vertical shorts); 1 swaps every frame. Original audio is re-muxed back.
    """
    log = log or (lambda m: None)
    if not is_available():
        raise RuntimeError(
            "Face swap needs: pip install insightface onnxruntime "
            "opencv-python-headless"
        )
    log("🧠 Initializing InsightFace (buffalo_l + inswapper_128)...")
    swapper = FreeFaceSwapper()

    src_face_img = cv2.imread(face_image_path)
    if src_face_img is None:
        raise ValueError(f"Cannot read face image: {face_image_path}")
    src_faces = swapper.app.get(src_face_img)
    if not src_faces:
        raise ValueError("No face detected in the uploaded face image.")

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open clip: {clip_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    tmp_video = out_path + ".tmp.mp4"
    writer = cv2.VideoWriter(tmp_video, fourcc, fps, (width, height))

    idx = 0
    swap_face = src_faces[0]
    t0 = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out_frame = frame
        if idx % frame_step == 0:
            detected = swapper.app.get(frame)
            if detected:
                out_frame = swapper.swapper.get(
                    frame, detected[0], swap_face, paste_back=True
                )
        writer.write(out_frame)
        idx += 1
        if idx % 100 == 0:
            elapsed = (time.time() - t0) / 60
            log(f"🔁 {idx}/{frame_count} frames ({elapsed:.1f} min)")

    cap.release()
    writer.release()
    if idx == 0:
        raise ValueError("No frames read from clip.")

    # Re-mux the ORIGINAL clip's audio (input 0) over the swapped video
    # (input 1). The ported QuClippers code read `out_path` here, which does
    # not exist yet, silently dropping audio — this is the corrected version.
    audio_ok = False
    try:
        fn = out_path + ".aud.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", clip_path, "-i", tmp_video,
             "-map", "0:a:0?", "-map", "1:v", "-map_metadata", "-1",
             "-c:a", "aac", "-b:a", "128k", *encode_args(log=None),
             "-shortest", fn],
            check=True, capture_output=True,
        )
        if os.path.exists(fn):
            os.replace(fn, out_path)
            audio_ok = True
    except Exception:  # noqa: BLE001
        pass
    if audio_ok:
        os.remove(tmp_video)
        log("✅ Face swap done (audio preserved)")
        return out_path
    # No audio track to mux — keep the silent swapped video.
    os.replace(tmp_video, out_path)
    log("✅ Face swap done (no audio track found)")
    return out_path


def run_faceswap(job_id: str, clip_idx: int, face_image_path: str,
                 out_root: str = "", sink=None) -> dict:
    """Job entrypoint: swap the face of one clip of a project.

    Saves `clip_{idx}_swap.mp4` in the project dir and returns its URL.
    """
    import db

    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    clips = project.get("clips") or []
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")

    log = sink or (lambda m: None)
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip video no longer available on disk")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_swap.mp4")
    log(f"🔄 Face swap: clip {clip_idx + 1} (CPU, free InsightFace)...")
    process_faceswap(src, face_image_path, out, frame_step=2, log=log)
    return {
        "clip_idx": clip_idx,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}_swap.mp4",
        "size_mb": round(os.path.getsize(out) / 1e6, 1),
    }
