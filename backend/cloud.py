"""Cloud backup — optional S3/R2 mirror of a completed project's clips.

Ported from QuClippers `s3_uploader.py`, with the cost doctrine applied:
Cloudflare R2 (egress $0) is the recommended target, but any S3-compatible
endpoint works. boto3 is imported lazily so the core app never needs it.

Env vars (all optional — when unset, backup is a silent no-op):
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
  AWS_REGION            (default: us-east-1)
  AWS_S3_BUCKET         (private bucket; clips are backed up here)
  AWS_ENDPOINT_URL      (set to https://<account>.r2.cloudflarestorage.com
                         for Cloudflare R2; omit for AWS S3)

Backup runs fire-and-forget after a job completes (see app.py) and is best
effort: a failure is logged, never fatal.
"""

import os
import threading

_upload_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID")
                and os.environ.get("AWS_SECRET_ACCESS_KEY")
                and os.environ.get("AWS_S3_BUCKET"))


def _client():
    import boto3
    kwargs = {
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
        "region_name": os.environ.get("AWS_REGION", "us-east-1"),
    }
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


def backup_project(job_id: str, work_dir: str, sink=None) -> dict:
    """Upload a project's rendered clips (+ subtitle files) to the bucket.

    Returns {uploaded: [keys], skipped: [names]}.
    """
    if not enabled():
        return {"uploaded": [], "skipped": [], "reason": "not configured"}
    bucket = os.environ.get("AWS_S3_BUCKET", "")
    client = _client()
    uploaded = []
    skipped = []
    prefix = f"klipin/{job_id}"
    with _upload_lock:
        for name in sorted(os.listdir(work_dir)):
            if not (name.endswith(".mp4") or name.endswith(".ass")):
                continue
            path = os.path.join(work_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                client.upload_file(path, bucket, f"{prefix}/{name}")
                uploaded.append(f"{prefix}/{name}")
            except Exception as e:  # noqa: BLE001
                sink and sink(f"⚠️ backup failed for {name}: {e}")
                skipped.append(name)
    sink and sink(f"☁️ Cloud backup: {len(uploaded)} file(s) → s3://{bucket}/{prefix}")
    return {"uploaded": uploaded, "skipped": skipped}
