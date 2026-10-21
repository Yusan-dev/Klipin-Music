FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fontconfig fonts-liberation fonts-symbola \
    fonts-noto-core fonts-noto-cjk curl \
    && rm -rf /var/lib/apt/lists/*

# Popular caption fonts for libass (Inter/Arial look-alikes are Liberation;
# Bebas Neue / Anton / Poppins give the bold & gaming looks KlipAja-style).
# Downloaded from Google Fonts GitHub so builds work offline afterwards.
RUN set -eux; \
    mkdir -p /usr/share/fonts/truetype/klipin; \
    cd /usr/share/fonts/truetype/klipin; \
    for f in \
      "ofl/bebasneue/BebasNeue-Regular.ttf" \
      "ofl/anton/Anton-Regular.ttf" \
      "ofl/poppins/Poppins-Bold.ttf" \
      "ofl/poppins/Poppins-SemiBold.ttf" \
      "ofl/montserrat/Montserrat-Bold.ttf" \
      "ofl/roboto/Roboto-Bold.ttf" \
      "apache/robotoslab/RobotoSlab-Bold.ttf" \
    ; do \
      curl -fsSL -O "https://raw.githubusercontent.com/google/fonts/main/$f" || true; \
    done; \
    fc-cache -f >/dev/null 2>&1 || true

WORKDIR /app
COPY requirements.txt .
# insightface pulls `opencv-python` (non-headless), which overwrites cv2
# with 5.x. Force a reinstall of headless 4.x so face-track's
# CascadeClassifier keeps working.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --force-reinstall \
        "opencv-python-headless>=4.9,<5"

# Keep the lightweight API dispatcher client in its own layer. Changing its
# version must not invalidate the much larger vision/Torch dependency layer.
RUN pip install --no-cache-dir "modal>=1.5,<2"

COPY . .

# Local-whisper fallback (last resort when Groq+Deepgram both fail).
# Separate layer so the heavy requirements layer above stays cached —
# ctranslate2 pulls no torch/CUDA, ~100MB.
RUN pip install --no-cache-dir "faster-whisper>=1.0" \
    && pip install --no-cache-dir --force-reinstall "opencv-python-headless>=4.9,<5"

# Production containers must not render or serve user media as root. Named
# volumes copy these directory permissions on first mount.
RUN groupadd --system klipin \
    && useradd --system --gid klipin --home-dir /app --shell /usr/sbin/nologin klipin \
    && mkdir -p /app/output /app/data /app/uploads \
    && chown -R klipin:klipin /app

USER klipin

EXPOSE 8000
# Single worker is REQUIRED: OAuth state, login claims, the rate limiter, and
# the in-memory job queue are process-local (see app._single_worker_guard).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
