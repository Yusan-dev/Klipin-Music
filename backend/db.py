"""SQLite persistence for Klipin — project library & history.

Jobs in memory are ephemeral (cleared on restart / after retention), so every
completed project is mirrored here. The frontend library view reads from this,
and re-renders (retrim) read stored options + transcript from it.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "projects.db"
_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  job_id       TEXT PRIMARY KEY,
  user_id      TEXT DEFAULT '',
  source_label TEXT,
  status       TEXT DEFAULT 'queued',
  created_at   REAL,
  duration     REAL,
  options      TEXT,
  error        TEXT,
  transcript   TEXT,
  mode         TEXT DEFAULT 'viral',
  cost         TEXT,
  metrics      TEXT
);
CREATE TABLE IF NOT EXISTS job_queue (
  job_id       TEXT PRIMARY KEY,
  spec         TEXT NOT NULL,
  status       TEXT DEFAULT 'queued',
  attempts     INTEGER DEFAULT 0,
  error        TEXT DEFAULT '',
  created_at   REAL,
  updated_at   REAL
);
CREATE TABLE IF NOT EXISTS clips (
  job_id       TEXT,
  idx          INTEGER,
  start        REAL,
  end          REAL,
  title        TEXT,
  hook         TEXT,
  video_url    TEXT,
  subtitle_url TEXT,
  size_mb      REAL,
  hook_score   INTEGER,
  thumbnail_url TEXT,
  hashtags     TEXT,
  reason       TEXT,
  topic        TEXT,
  speech_density INTEGER,
  quality_breakdown TEXT,
  layout_plan   TEXT,
  layout_plan_edited INTEGER DEFAULT 0,
  hook_variants TEXT,
  approval_status TEXT DEFAULT '',
  approval_note  TEXT,
  caption_edits TEXT,
  subject_lock TEXT,
  layer_plan TEXT,
  rev          INTEGER DEFAULT 0,
  PRIMARY KEY (job_id, idx)
);
CREATE TABLE IF NOT EXISTS tokens (
  user_id      TEXT NOT NULL,
  platform     TEXT NOT NULL,            -- youtube | tiktok | instagram
  access_token TEXT,
  refresh_token TEXT,
  expires_at   REAL,
  account_name TEXT,
  account_id   TEXT,
  updated_at   REAL,
  PRIMARY KEY (user_id, platform)
);
CREATE TABLE IF NOT EXISTS publishes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id       TEXT,
  clip_idx     INTEGER,
  platform     TEXT,
  status       TEXT DEFAULT 'published',  -- published | failed | scheduled | processing
  post_url     TEXT,
  post_id      TEXT,
  error        TEXT,
  title        TEXT,
  description  TEXT,
  privacy      TEXT,
  scheduled_at REAL,
  created_at   REAL
);
CREATE TABLE IF NOT EXISTS users (
  google_sub TEXT PRIMARY KEY,
  email      TEXT,
  name       TEXT,
  picture    TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,        -- sha256 of the bearer token (never store raw)
  user_id    TEXT NOT NULL REFERENCES users(google_sub),
  created_at REAL,
  expires_at REAL
);
CREATE TABLE IF NOT EXISTS criteria_favorites (
  user_id    TEXT NOT NULL REFERENCES users(google_sub),
  prompt     TEXT NOT NULL,
  name       TEXT,          -- optional short label (rename feature)
  grp        TEXT,          -- optional group/folder label (per niche, e.g. "finansial")
  position   REAL,          -- manual order (pin-to-top); lower sorts first
  created_at REAL,
  PRIMARY KEY (user_id, prompt)
);
CREATE TABLE IF NOT EXISTS ideas (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      TEXT NOT NULL REFERENCES users(google_sub),
  title        TEXT NOT NULL,
  description  TEXT DEFAULT '',
  status       TEXT DEFAULT 'idea',   -- idea | draft | published
  platform     TEXT DEFAULT '',       -- youtube | tiktok | instagram | ''
  scheduled_at REAL,                  -- content calendar: when to publish
  source_url   TEXT DEFAULT '',       -- original video/URL the idea came from
  job_id       TEXT DEFAULT '',       -- linked Klipin project (from Library)
  created_at   REAL,
  updated_at   REAL
);
CREATE TABLE IF NOT EXISTS brand_kits (
  user_id    TEXT PRIMARY KEY REFERENCES users(google_sub),
  kit        TEXT DEFAULT '{}',       -- JSON: {color, caption_font, caption_size, watermark, emoji}
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS usage_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL,
  job_id     TEXT NOT NULL,
  amount     INTEGER DEFAULT 1,
  state      TEXT DEFAULT 'reserved',  -- reserved | committed | released
  created_at REAL
);
CREATE TABLE IF NOT EXISTS transcript_cache (
  cache_key         TEXT PRIMARY KEY,
  source            TEXT NOT NULL,
  source_media_id   TEXT DEFAULT '',
  language          TEXT DEFAULT '',
  transcript_source TEXT DEFAULT '',
  transcript        TEXT NOT NULL,
  created_at        REAL,
  updated_at        REAL
);
CREATE TABLE IF NOT EXISTS music_jobs (
  job_id       TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  status       TEXT DEFAULT 'queued',
  title        TEXT DEFAULT '',
  prompt       TEXT DEFAULT '',
  lyrics       TEXT DEFAULT '',
  genre        TEXT DEFAULT '',
  language     TEXT DEFAULT 'id',
  provider     TEXT DEFAULT '',
  seed         INTEGER,
  duration     REAL DEFAULT 30,
  audio_path   TEXT DEFAULT '',
  lyrics_path  TEXT DEFAULT '',
  cover_path   TEXT DEFAULT '',
  cover_source TEXT DEFAULT '',
  vocal_gender TEXT DEFAULT 'auto',
  group_id     TEXT DEFAULT '',
  take_index   INTEGER DEFAULT 0,
  parent_job_id TEXT DEFAULT '',
  lora_id      TEXT DEFAULT '',
  take_mode    TEXT DEFAULT 'final',
  instrumental INTEGER DEFAULT 0,
  bpm          INTEGER DEFAULT 0,
  music_key    TEXT DEFAULT '',
  persona_id   TEXT DEFAULT '',
  is_public    INTEGER DEFAULT 0,
  metadata     TEXT DEFAULT '{}',
  group_cache_key TEXT DEFAULT '',
  error        TEXT DEFAULT '',
  created_at   REAL,
  updated_at   REAL
);
CREATE TABLE IF NOT EXISTS music_personas (
  persona_id   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  name         TEXT DEFAULT '',
  source_job_id TEXT DEFAULT '',
  genre        TEXT DEFAULT '',
  language     TEXT DEFAULT 'id',
  vocal_gender TEXT DEFAULT 'auto',
  style_tags   TEXT DEFAULT '',
  seed_base    INTEGER,
  notes        TEXT DEFAULT '',
  voice_ref_path TEXT DEFAULT '',
  voice_consent_at REAL,
  persona_type TEXT DEFAULT 'style',
  created_at   REAL
);
CREATE TABLE IF NOT EXISTS music_voice_previews (
  preview_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  spec TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  call_id TEXT DEFAULT '',
  sample_name TEXT DEFAULT '',
  error TEXT DEFAULT '',
  duration REAL DEFAULT 0,
  metrics TEXT DEFAULT '{}',
  saved_persona_id TEXT DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(user_id,request_key)
);
CREATE TABLE IF NOT EXISTS music_costs (
  job_id     TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  gpu_sec    REAL DEFAULT 0,
  usd        REAL DEFAULT 0,
  credits    INTEGER DEFAULT 0,
  state      TEXT DEFAULT 'committed',   -- committed | released
  created_at REAL
);
CREATE TABLE IF NOT EXISTS music_projects (
  project_id   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  name         TEXT DEFAULT '',
  job_id       TEXT DEFAULT '',
  group_id     TEXT DEFAULT '',
  created_at   REAL,
  updated_at   REAL
);
CREATE TABLE IF NOT EXISTS music_project_revisions (
  revision_id    TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  user_id        TEXT NOT NULL,
  revision_index INTEGER DEFAULT 0,
  label          TEXT DEFAULT '',
  snapshot       TEXT NOT NULL,
  source_job_id  TEXT DEFAULT '',
  created_at     REAL,
  UNIQUE(project_id, revision_index)
);
CREATE INDEX IF NOT EXISTS idx_music_project_revisions_project
  ON music_project_revisions(project_id, revision_index DESC);
CREATE TABLE IF NOT EXISTS music_lyric_drafts (
  draft_id   TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  title      TEXT DEFAULT '',
  lyrics     TEXT DEFAULT '',
  notes      TEXT DEFAULT '',
  genre      TEXT DEFAULT '',
  language   TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_music_lyric_drafts_user
  ON music_lyric_drafts(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS payments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL,
  provider   TEXT,            -- stripe | midtrans
  external_id TEXT,           -- stripe session id / midtrans order id
  plan_id    TEXT,
  amount_idr INTEGER DEFAULT 0,
  status     TEXT DEFAULT 'pending',  -- pending | paid | failed
  created_at REAL
);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    # 30s busy timeout: concurrent writers (jobs, timers, CLI tools) wait for
    # the lock instead of failing immediately with "database is locked".
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """Add columns added after the initial schema (idempotent)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN user_id TEXT DEFAULT ''")
    if "mode" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN mode TEXT DEFAULT 'viral'")
    if "cost" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN cost TEXT")
    if "metrics" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN metrics TEXT")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN updated_at REAL")
    clip_cols = {r[1] for r in conn.execute("PRAGMA table_info(clips)").fetchall()}
    if "hook_score" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN hook_score INTEGER")
    if "thumbnail_url" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN thumbnail_url TEXT")
    if "hashtags" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN hashtags TEXT")
    if "reason" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN reason TEXT")
    if "topic" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN topic TEXT")
    if "speech_density" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN speech_density INTEGER")
    if "caption_edits" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN caption_edits TEXT")
    if "hook_start_time" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN hook_start_time REAL")
    if "hook_end_time" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN hook_end_time REAL")
    if "keep_segments" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN keep_segments TEXT")
    if "quality_breakdown" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN quality_breakdown TEXT")
    if "layout_plan" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN layout_plan TEXT")
    if "subject_lock" not in clip_cols:
        # Manual subject lock: normalized click point [x, y] (0..1) marking the
        # face the tracker must follow. JSON text, NULL = no lock.
        conn.execute("ALTER TABLE clips ADD COLUMN subject_lock TEXT")
    if "layer_plan" not in clip_cols:
        # Lightweight professional editor state. Only normalized metadata is
        # stored here; video compositing remains a backend render concern.
        conn.execute("ALTER TABLE clips ADD COLUMN layer_plan TEXT")
    if "layout_plan_edited" not in clip_cols:
        # Per-scene layout editor: 1 = user edited layout_plan by hand
        # (rendered as-is on re-render), 0 = AI-generated plan.
        conn.execute("ALTER TABLE clips ADD COLUMN layout_plan_edited INTEGER DEFAULT 0")
    if "hook_variants" not in clip_cols:
        # A/B hook generator: JSON list of 3 variants
        # [{style, text, predicted_score, why}, ...].
        conn.execute("ALTER TABLE clips ADD COLUMN hook_variants TEXT")
    if "approval_status" not in clip_cols:
        # Approval workflow: '' | pending | approved | changes_requested.
        conn.execute("ALTER TABLE clips ADD COLUMN approval_status TEXT DEFAULT ''")
    if "approval_note" not in clip_cols:
        conn.execute("ALTER TABLE clips ADD COLUMN approval_note TEXT")
    pub_cols = {r[1] for r in conn.execute("PRAGMA table_info(publishes)").fetchall()}
    if "user_id" not in pub_cols:
        # Owner of the project the publish belongs to. Legacy rows (created
        # before ownership existed) stay '' and are only reachable through the
        # owning project (which itself must have a user_id to be accessible).
        conn.execute("ALTER TABLE publishes ADD COLUMN user_id TEXT DEFAULT ''")
    if "scheduled_at" not in pub_cols:
        conn.execute("ALTER TABLE publishes ADD COLUMN scheduled_at REAL")
    for col in ("title", "description", "privacy"):
        if col not in pub_cols:
            conn.execute(f"ALTER TABLE publishes ADD COLUMN {col} TEXT")
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "credits" not in user_cols:
        # Permanent prepaid credit balance (KlipAja model) — never expires.
        conn.execute("ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 0")
    if "free_credit_used" not in user_cols:
        # LEGACY: the old one-time free clip flag. Entitlement is now derived
        # from usage_log + users.credits (see billing.py); the column is kept
        # only so rows written by older builds still load.
        conn.execute("ALTER TABLE users ADD COLUMN free_credit_used INTEGER DEFAULT 0")
    token_cols = {r[1] for r in conn.execute("PRAGMA table_info(tokens)").fetchall()}
    if "user_id" not in token_cols:
        # The old schema had one global token per platform.  Never assign
        # those credentials to an arbitrary tenant: preserve them as orphaned
        # rows until each user reconnects their own account.
        conn.execute("ALTER TABLE tokens RENAME TO tokens_legacy_global")
        conn.execute("""CREATE TABLE tokens (
            user_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            access_token TEXT,
            refresh_token TEXT,
            expires_at REAL,
            account_name TEXT,
            account_id TEXT,
            updated_at REAL,
            PRIMARY KEY (user_id, platform)
        )""")
        conn.execute("""INSERT INTO tokens
            (user_id, platform, access_token, refresh_token, expires_at,
             account_name, account_id, updated_at)
            SELECT '', platform, access_token, refresh_token, expires_at,
                   account_name, account_id, updated_at
            FROM tokens_legacy_global""")
        conn.execute("DROP TABLE tokens_legacy_global")
    fav_cols = {r[1] for r in conn.execute("PRAGMA table_info(criteria_favorites)").fetchall()}
    if "name" not in fav_cols:
        conn.execute("ALTER TABLE criteria_favorites ADD COLUMN name TEXT")
    if "grp" not in fav_cols:
        conn.execute("ALTER TABLE criteria_favorites ADD COLUMN grp TEXT")
    if "position" not in fav_cols:
        # Manual ordering (pin-to-top). Newest-first for pre-existing rows:
        # -rowid makes the newest row (largest rowid) sort first.
        conn.execute("ALTER TABLE criteria_favorites ADD COLUMN position REAL")
        conn.execute("UPDATE criteria_favorites SET position = -rowid")
    music_cols = {r[1] for r in conn.execute("PRAGMA table_info(music_jobs)").fetchall()}
    if "cover_path" not in music_cols:
        conn.execute("ALTER TABLE music_jobs ADD COLUMN cover_path TEXT DEFAULT ''")
    if "cover_source" not in music_cols:
        conn.execute("ALTER TABLE music_jobs ADD COLUMN cover_source TEXT DEFAULT ''")
    if "vocal_gender" not in music_cols:
        conn.execute("ALTER TABLE music_jobs ADD COLUMN vocal_gender TEXT DEFAULT 'auto'")
    for col, ddl in (
        ("group_id", "TEXT DEFAULT ''"),
        ("take_index", "INTEGER DEFAULT 0"),
        ("parent_job_id", "TEXT DEFAULT ''"),
        ("lora_id", "TEXT DEFAULT ''"),
        ("take_mode", "TEXT DEFAULT 'final'"),
        ("instrumental", "INTEGER DEFAULT 0"),
        ("bpm", "INTEGER DEFAULT 0"),
        ("music_key", "TEXT DEFAULT ''"),
        ("persona_id", "TEXT DEFAULT ''"),
        ("is_public", "INTEGER DEFAULT 0"),
        ("group_cache_key", "TEXT DEFAULT ''"),
    ):
        if col not in music_cols:
            conn.execute(f"ALTER TABLE music_jobs ADD COLUMN {col} {ddl}")
    # Content-addressed dedup guard: a group (same user + same spec cache key)
    # must exist at most once, so two concurrent identical requests cannot both
    # spawn GPU renders. Partial index ignores rows without a cache key (covers,
    # extends) so they are never deduped against each other.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_music_jobs_group_cache_v2 "
        "ON music_jobs(user_id, group_cache_key, take_index) "
        "WHERE group_cache_key <> ''")
    persona_cols = {r[1] for r in conn.execute("PRAGMA table_info(music_personas)").fetchall()}
    if "voice_ref_path" not in persona_cols:
        conn.execute("ALTER TABLE music_personas ADD COLUMN voice_ref_path TEXT DEFAULT ''")
    if "voice_consent_at" not in persona_cols:
        conn.execute("ALTER TABLE music_personas ADD COLUMN voice_consent_at REAL")
    if "persona_type" not in persona_cols:
        conn.execute("ALTER TABLE music_personas ADD COLUMN persona_type TEXT DEFAULT 'style'")


def save_project(job_id: str, source_label: str = "", status: str = "queued",
                 created_at: float = 0.0, options: dict | None = None,
                 error: str = "", duration: float | None = None,
                 transcript: dict | None = None,
                 mode: str = "viral", cost: dict | None = None,
                 metrics: dict | None = None,
                 user_id: str = "") -> None:
    if CAPTURE_WRITES:
        CAPTURED_WRITES.append((
            "save_project",
            (job_id, source_label, status, created_at, options or {}, error,
             duration, transcript, mode, cost, metrics, user_id), {}))
        return
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO projects (job_id, user_id, source_label, status,
                                     created_at, options, error, duration,
                                     transcript, mode, cost, metrics)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 user_id=excluded.user_id, source_label=excluded.source_label,
                 status=excluded.status,
                 created_at=excluded.created_at, options=excluded.options,
                 error=excluded.error, duration=excluded.duration,
                 transcript=excluded.transcript, mode=excluded.mode,
                 cost=excluded.cost, metrics=excluded.metrics""",
            (job_id, user_id, source_label, status, created_at,
             json.dumps(options or {}), error, duration,
              json.dumps(transcript) if transcript is not None else None,
              mode,
              json.dumps(cost) if cost is not None else None,
              json.dumps(metrics) if metrics is not None else None),
        )


def enqueue_job(job_id: str, spec: dict) -> None:
    """Durably record work before it is submitted to the in-process runner."""
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO job_queue
                 (job_id, spec, status, attempts, error, created_at, updated_at)
               VALUES (?, ?, 'queued', 0, '', ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 spec=excluded.spec, status='queued', error='',
                 updated_at=excluded.updated_at""",
            (job_id, json.dumps(spec), now, now),
        )


def mark_job_queue(job_id: str, status: str, error: str = "") -> None:
    with _lock, _connect() as conn:
        if status == "processing":
            conn.execute(
                "UPDATE job_queue SET status=?, attempts=attempts+1, "
                "error=?, updated_at=? WHERE job_id=?",
                (status, error, time.time(), job_id),
            )
        else:
            conn.execute(
                "UPDATE job_queue SET status=?, error=?, updated_at=? "
                "WHERE job_id=?",
                (status, error, time.time(), job_id),
            )


def recoverable_jobs(max_attempts: int = 3) -> list[dict]:
    """Return interrupted/queued work; malformed specs are never executed."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM job_queue WHERE status IN ('queued','processing') "
            "AND attempts < ? ORDER BY created_at", (max_attempts,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["spec"] = json.loads(item.get("spec") or "{}")
        except (TypeError, json.JSONDecodeError):
            mark_job_queue(item["job_id"], "failed", "Invalid persisted job spec")
            continue
        if isinstance(item["spec"], dict) and item["spec"].get("kind"):
            result.append(item)
    return result


def get_cached_transcript(cache_key: str) -> dict | None:
    """Return a normalized transcript cache entry, or ``None`` on a miss."""
    if not cache_key:
        return None
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM transcript_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result["transcript"] = json.loads(result.get("transcript") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    return result


def put_cached_transcript(cache_key: str, source: str, source_media_id: str,
                          language: str, transcript_source: str,
                          transcript: dict) -> None:
    """Upsert a normalized transcript for repeat processing/repicking."""
    if not cache_key or not transcript:
        return
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO transcript_cache
                 (cache_key, source, source_media_id, language,
                  transcript_source, transcript, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET
                 language=excluded.language,
                 transcript_source=excluded.transcript_source,
                 transcript=excluded.transcript,
                 updated_at=excluded.updated_at""",
            (cache_key, source, source_media_id, language,
             transcript_source, json.dumps(transcript), now, now),
        )


def save_transcript(job_id: str, transcript: dict) -> None:
    """Persist an edited transcript (transcript editor) without touching the
    rest of the project row."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE projects SET transcript = ? WHERE job_id = ?",
            (json.dumps(transcript), job_id),
        )


def update_project_options(job_id: str, patch_options: dict) -> dict:
    """Merge-patch a project's stored options (subtitle colors, animation,
    words_per_line, etc.) and return the resulting options dict.

    Read-modify-write under the table lock: load existing JSON -> merge the
    patch (new keys overwrite, existing keys preserved unless explicitly
    cleared) -> write back atomically. Returns the merged options.
    """
    if not isinstance(patch_options, dict):
        raise TypeError("patch_options must be a dict")
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT options FROM projects WHERE job_id = ?", (job_id,)).fetchone()
        existing = json.loads(row[0]) if row and row[0] else {}
        merged = {**existing, **patch_options}
        # An explicit null/None in the patch means "clear this option".
        cleared = {k: v for k, v in merged.items()
                   if not (k in patch_options and patch_options[k] is None)}
        conn.execute(
            "UPDATE projects SET options = ?, updated_at = ? WHERE job_id = ?",
            (json.dumps(cleared), time.time(), job_id),
        )
    return cleared


def replace_clips(job_id: str, clips: list) -> None:
    if CAPTURE_WRITES:
        CAPTURED_WRITES.append(("replace_clips", (job_id, list(clips)), {}))
        return
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.executemany(
            """INSERT INTO clips (job_id, idx, start, end, title, hook,
                                  video_url, subtitle_url, size_mb, hook_score,
                                  thumbnail_url, hashtags, reason, topic,
                                  speech_density, caption_edits, rev,
                                  hook_start_time, hook_end_time, keep_segments,
                                  quality_breakdown, layout_plan,
                                  layout_plan_edited, hook_variants,
                                  approval_status, approval_note, subject_lock,
                                   layer_plan)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)""",
            [(job_id, c.get("index", i), c.get("start", 0), c.get("end", 0),
              c.get("title", ""), c.get("hook", ""),
              c.get("video_url", ""), c.get("subtitle_url", ""),
              c.get("size_mb", 0), c.get("hook_score"),
              c.get("thumbnail_url", ""),
              json.dumps(c.get("hashtags") or []),
              c.get("reason", ""),
              c.get("topic", ""),
              c.get("speech_density", 50),
              json.dumps(c.get("caption_edits") or {})
              if c.get("caption_edits") else None,
              c.get("hook_start_time"),
              c.get("hook_end_time"),
              json.dumps(c.get("keep_segments")) if c.get("keep_segments") else None,
              json.dumps(c.get("quality_breakdown") or {}),
              json.dumps(c.get("layout_plan") or []),
              1 if c.get("layout_plan_edited") else 0,
              json.dumps(c.get("hook_variants") or [])
              if c.get("hook_variants") else None,
              c.get("approval_status", ""),
              c.get("approval_note"),
              json.dumps(c.get("subject_lock"))
              if c.get("subject_lock") is not None else None,
              json.dumps(c.get("layer_plan"))
              if c.get("layer_plan") is not None else None)
             for i, c in enumerate(clips)],
        )


def get_project(job_id: str, user_id: str = "") -> dict | None:
    with _lock, _connect() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM projects WHERE job_id = ? AND user_id = ?",
                (job_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM projects WHERE job_id = ?", (job_id,)
            ).fetchone()
        clip_rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY idx", (job_id,)
        ).fetchall()
    project = dict(row) if row is not None else None
    if project is None:
        return None
    project["clips"] = [_clip_dict(c) for c in clip_rows]
    for key in ("options", "transcript", "cost", "metrics"):
        if project.get(key):
            try:
                project[key] = json.loads(project[key])
            except (TypeError, json.JSONDecodeError):
                project[key] = {}
    cost = project.get("cost")
    if isinstance(cost, dict):
        # Modal-backed projects historically stored only the compute-inclusive
        # names. Keep the public project shape compatible with older clients.
        if cost.get("total_usd") is None and cost.get("total_with_compute_usd") is not None:
            cost["total_usd"] = cost["total_with_compute_usd"]
        if cost.get("total_idr") is None:
            if cost.get("total_with_compute_idr") is not None:
                cost["total_idr"] = cost["total_with_compute_idr"]
            elif cost.get("total_usd") is not None:
                try:
                    cost["total_idr"] = round(float(cost["total_usd"]) * 16500)
                except (TypeError, ValueError):
                    pass
    return project


def list_projects(user_id: str = "") -> list:
    # `thumb` = first available clip thumbnail, so list views can render
    # visual cards without an extra request per project.
    THUMB_SEL = (
        "(SELECT c2.thumbnail_url FROM clips c2 "
        "WHERE c2.job_id = p.job_id AND c2.thumbnail_url IS NOT NULL "
        "AND c2.thumbnail_url != '' ORDER BY c2.idx LIMIT 1) AS thumb"
    )
    with _lock, _connect() as conn:
        if user_id:
            rows = conn.execute(
                f"""SELECT p.*, COUNT(c.idx) AS clip_count,
                          COALESCE(SUM(c.size_mb), 0) AS total_mb,
                          {THUMB_SEL}
                   FROM projects p LEFT JOIN clips c ON c.job_id = p.job_id
                   WHERE p.user_id = ?
                   GROUP BY p.job_id ORDER BY p.created_at DESC""",
                (user_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT p.*, COUNT(c.idx) AS clip_count,
                          COALESCE(SUM(c.size_mb), 0) AS total_mb,
                          {THUMB_SEL}
                   FROM projects p LEFT JOIN clips c ON c.job_id = p.job_id
                   GROUP BY p.job_id ORDER BY p.created_at DESC"""
            ).fetchall()
    projects = []
    for r in rows:
        p = dict(r)
        try:
            p["options"] = json.loads(p.get("options") or "{}")
        except (TypeError, json.JSONDecodeError):
            p["options"] = {}
        p.pop("transcript", None)  # keep list responses light
        projects.append(p)
    return projects


def delete_project(job_id: str, user_id: str = "") -> None:
    with _lock, _connect() as conn:
        if user_id:
            conn.execute("DELETE FROM projects WHERE job_id = ? AND user_id = ?",
                         (job_id, user_id))
            conn.execute("DELETE FROM clips WHERE job_id = ? AND "
                         "job_id NOT IN (SELECT job_id FROM projects)",
                         (job_id,))
        else:
            conn.execute("DELETE FROM projects WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))


def update_clip(job_id: str, idx: int, **fields) -> None:
    if not fields:
        return
    if CAPTURE_WRITES:
        CAPTURED_WRITES.append(("update_clip", (job_id, idx), dict(fields)))
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id, idx]
    with _lock, _connect() as conn:
        conn.execute(
            f"UPDATE clips SET {cols} WHERE job_id = ? AND idx = ?", vals
        )


def bump_clip_rev(job_id: str, idx: int) -> int:
    if CAPTURE_WRITES:
        # Remote run: record the bump, return a placeholder. The host replays
        # the real bump (and its true rev) via apply_captured_writes().
        CAPTURED_WRITES.append(("bump_clip_rev", (job_id, idx), {}))
        return 1
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE clips SET rev = rev + 1 WHERE job_id = ? AND idx = ?",
            (job_id, idx),
        )
        row = conn.execute(
            "SELECT rev FROM clips WHERE job_id = ? AND idx = ?",
            (job_id, idx),
        ).fetchone()
    return int(row["rev"]) if row else 0


def get_clips(job_id: str) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY idx", (job_id,)
        ).fetchall()
    return [_clip_dict(r) for r in rows]


def _clip_dict(row) -> dict:
    """Row -> dict with both `idx` (backend) and `index` (frontend) keys."""
    d = dict(row)
    if "idx" in d:
        d.setdefault("index", d["idx"])
    if "index" in d:
        d.setdefault("idx", d["index"])
    # hashtags are stored as a JSON string — return a real list.
    if "hashtags" in d and isinstance(d.get("hashtags"), str):
        try:
            d["hashtags"] = json.loads(d["hashtags"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["hashtags"] = []
    # caption_edits stored as a JSON string (typo fixes per caption block).
    if "caption_edits" in d and isinstance(d.get("caption_edits"), str):
        try:
            d["caption_edits"] = json.loads(d["caption_edits"] or "{}")
        except (TypeError, json.JSONDecodeError):
            d["caption_edits"] = {}
    elif "caption_edits" in d and d.get("caption_edits") is None:
        d["caption_edits"] = {}
    # keep_segments stored as a JSON string (dead-air trim sub-windows).
    if "keep_segments" in d and isinstance(d.get("keep_segments"), str):
        try:
            d["keep_segments"] = json.loads(d["keep_segments"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["keep_segments"] = []
    elif "keep_segments" in d and d.get("keep_segments") is None:
        d["keep_segments"] = []
    if "quality_breakdown" in d and isinstance(d.get("quality_breakdown"), str):
        try:
            d["quality_breakdown"] = json.loads(d["quality_breakdown"] or "{}")
        except (TypeError, json.JSONDecodeError):
            d["quality_breakdown"] = {}
    if "layout_plan" in d and isinstance(d.get("layout_plan"), str):
        try:
            d["layout_plan"] = json.loads(d["layout_plan"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["layout_plan"] = []
    # subject_lock stored as a JSON string ([x, y] normalized click point).
    if "subject_lock" in d and isinstance(d.get("subject_lock"), str):
        try:
            d["subject_lock"] = json.loads(d["subject_lock"] or "null")
        except (TypeError, json.JSONDecodeError):
            d["subject_lock"] = None
    # hook_variants stored as a JSON list (A/B hook generator).
    if "hook_variants" in d and isinstance(d.get("hook_variants"), str):
        try:
            d["hook_variants"] = json.loads(d["hook_variants"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["hook_variants"] = []
    if "layer_plan" in d and isinstance(d.get("layer_plan"), str):
        try:
            d["layer_plan"] = json.loads(d["layer_plan"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["layer_plan"] = []
    elif "layer_plan" in d and d.get("layer_plan") is None:
        d["layer_plan"] = []
    return d


# ─── OAuth tokens & publish history ────────────────────────────────────────

def save_token(user_id: str, platform: str, access_token: str, refresh_token: str = "",
               expires_at: float = 0.0, account_name: str = "",
               account_id: str = "") -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO tokens (user_id, platform, access_token, refresh_token,
                                   expires_at, account_name, account_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, platform) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=excluded.refresh_token,
                 expires_at=excluded.expires_at,
                 account_name=excluded.account_name,
                 account_id=excluded.account_id,
                 updated_at=excluded.updated_at""",
            (user_id, platform, access_token, refresh_token, expires_at,
             account_name, account_id, time.time()),
        )


def get_token(user_id: str, platform: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tokens WHERE user_id = ? AND platform = ?",
            (user_id, platform)
        ).fetchone()
    return dict(row) if row else None


def delete_token(user_id: str, platform: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM tokens WHERE user_id = ? AND platform = ?",
                     (user_id, platform))


def log_publish(job_id: str, clip_idx: int, platform: str, status: str,
                post_url: str = "", post_id: str = "", error: str = "",
                scheduled_at: float | None = None,
                user_id: str = "") -> int:
    """Insert a publish row. Returns the new row id."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """INSERT INTO publishes (job_id, user_id, clip_idx, platform,
                                      status, post_url, post_id, error,
                                      scheduled_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, user_id, clip_idx, platform, status, post_url, post_id,
             error, scheduled_at, time.time()),
        )
        return int(cur.lastrowid)


def schedule_publish(job_id: str, clip_idx: int, platform: str,
                     title: str = "", description: str = "",
                     privacy: str = "", scheduled_at: float | None = None,
                     user_id: str = "") -> int:
    """Insert a scheduled publish row (status='scheduled'). Returns row id."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """INSERT INTO publishes (job_id, user_id, clip_idx, platform,
                                      status, title, description, privacy,
                                      scheduled_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, user_id, clip_idx, platform, "scheduled", title,
             description, privacy, scheduled_at, time.time()),
        )
        return int(cur.lastrowid)


def list_due_publishes(now: float | None = None) -> list:
    """All scheduled rows whose time has come and are not yet claimed."""
    now = now if now is not None else time.time()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM publishes
               WHERE status = 'scheduled' AND scheduled_at IS NOT NULL
                 AND scheduled_at <= ?
               ORDER BY scheduled_at ASC""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def claim_publish(publish_id: int) -> bool:
    """Atomically mark a scheduled row as processing; False if already taken."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE publishes SET status = 'processing' "
            "WHERE id = ? AND status = 'scheduled'",
            (publish_id,),
        )
        return cur.rowcount > 0


def release_processing_publishes() -> None:
    """On startup, release rows stuck in 'processing' (crashed before publish
    completed) back to 'scheduled' so the scheduler can re-fire them."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE publishes SET status = 'scheduled' "
            "WHERE status = 'processing'"
        )


def release_stuck_projects() -> None:
    """On startup, mark project rows stuck in 'queued'/'processing' as 'failed'.

    The in-memory job queue is wiped on restart, so any project whose DB row
    still says 'queued' or 'processing' was interrupted mid-flight and will
    never advance. Marking them 'failed' (instead of leaving them forever
    'queued') lets the frontend poll stop and show a clear re-run prompt.
    """
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE projects SET status = 'failed', "
            "error = 'Interrupted by server restart — please re-run' "
            "WHERE status IN ('queued', 'processing')"
        )


def list_active_music_jobs() -> list:
    """Queued/processing music rows — used by the startup crash sweep."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT job_id, user_id FROM music_jobs "
            "WHERE status IN ('queued', 'processing')"
        ).fetchall()
    return [{"job_id": r["job_id"], "user_id": r["user_id"]} for r in rows]


def update_publish(publish_id: int, **fields) -> None:
    """Update arbitrary fields on a publish row (status, post_url, error…)."""
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [publish_id]
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE publishes SET {cols} WHERE id = ?", vals)


def list_publishes(job_id: str, clip_idx: int | None = None) -> list:
    with _lock, _connect() as conn:
        if clip_idx is None:
            rows = conn.execute(
                "SELECT * FROM publishes WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM publishes WHERE job_id = ? AND clip_idx = ? "
                "ORDER BY created_at DESC",
                (job_id, clip_idx),
            ).fetchall()
    return [dict(r) for r in rows]


# ─── users & sessions (Google Sign-In) ───────────────────────────────────

def upsert_user(google_sub: str, email: str = "", name: str = "",
                picture: str = "") -> None:
    """Insert or update a user identified by Google's stable `sub`."""
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO users (google_sub, email, name, picture, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(google_sub) DO UPDATE SET
                 email=excluded.email, name=excluded.name,
                 picture=excluded.picture""",
            (google_sub, email, name, picture, time.time()),
        )


def get_user(google_sub: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
    return dict(row) if row else None


def save_session(token_hash: str, user_id: str, expires_at: float) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
               VALUES (?,?,?,?)
               ON CONFLICT(token_hash) DO UPDATE SET
                 user_id=excluded.user_id, expires_at=excluded.expires_at""",
            (token_hash, user_id, time.time(), expires_at),
        )


def get_session_user(token_hash: str) -> dict | None:
    """Return the user for a session token hash, or None if missing/expired."""
    with _lock, _connect() as conn:
        row = conn.execute(
            """SELECT u.google_sub, u.email, u.name, u.picture,
                      s.created_at AS session_created, s.expires_at AS session_expires
               FROM sessions s JOIN users u ON u.google_sub = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (token_hash, time.time()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


# ─── ideas board (R10 — content calendar) ────────────────────────────────

IDEA_STATUSES = ("idea", "draft", "published")


def list_ideas(user_id: str) -> list:
    """User's ideas, newest first."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ideas WHERE user_id = ? "
            "ORDER BY COALESCE(scheduled_at, created_at) DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_idea(user_id: str, idea_id: int) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM ideas WHERE id = ? AND user_id = ?",
            (idea_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def create_idea(user_id: str, title: str, description: str = "",
                status: str = "idea", platform: str = "",
                scheduled_at: float | None = None,
                source_url: str = "", job_id: str = "") -> dict:
    """Insert a new idea and return it."""
    if status not in IDEA_STATUSES:
        status = "idea"
    now = time.time()
    with _lock, _connect() as conn:
        cur = conn.execute(
            """INSERT INTO ideas (user_id, title, description, status, platform,
                                 scheduled_at, source_url, job_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (user_id, (title or "").strip(), description or "", status,
             platform or "", scheduled_at, source_url or "", job_id or "",
             now, now),
        )
        idea_id = cur.lastrowid
    return get_idea(user_id, idea_id)


def update_idea(user_id: str, idea_id: int, **fields) -> dict | None:
    """Update allowed fields of an idea; returns the updated idea."""
    allowed = {"title", "description", "status", "platform",
               "scheduled_at", "source_url", "job_id"}
    # None = "don't touch this field"; explicit '' clears text fields.
    updates = {k: v for k, v in fields.items()
               if k in allowed and v is not None}
    if not updates:
        return get_idea(user_id, idea_id)
    if "status" in updates and updates["status"] not in IDEA_STATUSES:
        updates["status"] = "idea"
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [time.time(), idea_id, user_id]
    with _lock, _connect() as conn:
        conn.execute(
            f"UPDATE ideas SET {sets}, updated_at = ? "
            f"WHERE id = ? AND user_id = ?",
            vals,
        )
    return get_idea(user_id, idea_id)


def delete_idea(user_id: str, idea_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "DELETE FROM ideas WHERE id = ? AND user_id = ?", (idea_id, user_id),
        )


# ─── criteria favorites (R8 extras — synced across devices via account) ──

MAX_FAVORITES = 12


def list_favorites(user_id: str) -> list:
    """User's saved criteria in display order (manual pin order, then
    newest first): [{"prompt", "name", "group"}]."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT prompt, name, grp FROM criteria_favorites WHERE user_id = ? "
            "ORDER BY position ASC, created_at DESC, rowid DESC", (user_id,)
        ).fetchall()
    return [{"prompt": r["prompt"], "name": r["name"] or "",
             "group": r["grp"] or ""} for r in rows]


def list_groups(user_id: str) -> list:
    """Distinct group labels, most recently used first."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT grp, MAX(created_at) AS last FROM criteria_favorites
               WHERE user_id = ? AND grp IS NOT NULL AND grp != ''
               GROUP BY grp ORDER BY last DESC""",
            (user_id,),
        ).fetchall()
    return [r["grp"] for r in rows]


def save_favorite(user_id: str, prompt: str, name: str = "",
                  group: str = "") -> list:
    """Upsert one favorite (dedupe, moves to front) and enforce the cap."""
    text = (prompt or "").strip()
    if not text:
        return list_favorites(user_id)
    with _lock, _connect() as conn:
        m = conn.execute(
            "SELECT MIN(position) FROM criteria_favorites WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        new_pos = (m[0] if m[0] is not None else 0) - 1  # saved → front
        conn.execute(
            """INSERT INTO criteria_favorites (user_id, prompt, name, grp, position, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id, prompt) DO UPDATE SET
                 name=excluded.name, grp=excluded.grp, position=excluded.position,
                 created_at=excluded.created_at""",
            (user_id, text, (name or "").strip(), (group or "").strip(),
             new_pos, time.time()),
        )
        # Keep only the newest MAX_FAVORITES.
        conn.execute(
            """DELETE FROM criteria_favorites WHERE user_id = ? AND prompt NOT IN (
                 SELECT prompt FROM criteria_favorites WHERE user_id = ?
                 ORDER BY created_at DESC, rowid DESC LIMIT ?
               )""",
            (user_id, user_id, MAX_FAVORITES),
        )
    return list_favorites(user_id)


def rename_favorite(user_id: str, prompt: str, name: str | None = None,
                    group: str | None = None,
                    new_prompt: str | None = None) -> list:
    """Update a favorite (order kept). None = leave that field untouched,
    '' = clear it. `new_prompt` optionally changes the prompt text itself
    (merge: if the new prompt already exists, that row is dropped)."""
    text = (prompt or "").strip()
    nxt = (new_prompt or "").strip() or text
    if not text:
        return list_favorites(user_id)
    with _lock, _connect() as conn:
        if nxt != text:
            conn.execute(
                "DELETE FROM criteria_favorites WHERE user_id = ? AND prompt = ?",
                (user_id, nxt),
            )
            conn.execute(
                "UPDATE criteria_favorites SET prompt = ? "
                "WHERE user_id = ? AND prompt = ?", (nxt, user_id, text),
            )
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append((name or "").strip())
        if group is not None:
            sets.append("grp = ?")
            params.append((group or "").strip())
        if sets:
            conn.execute(
                f"UPDATE criteria_favorites SET {', '.join(sets)} "
                "WHERE user_id = ? AND prompt = ?",
                (*params, user_id, nxt),
            )
    return list_favorites(user_id)


def rename_group(user_id: str, old_group: str, new_group: str) -> list:
    """Rename a folder: all favorites in `old_group` move to `new_group`
    (empty = ungroup). Order kept."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE criteria_favorites SET grp = ? "
            "WHERE user_id = ? AND grp = ?",
            ((new_group or "").strip(), user_id, (old_group or "").strip()),
        )
    return list_favorites(user_id)


def clear_group(user_id: str, group: str) -> list:
    """Remove a folder: ungroup all its favorites (back to Inbox)."""
    return rename_group(user_id, group, "")


def pin_favorite(user_id: str, prompt: str) -> list:
    """Pin a favorite to the front of its own group row. Folder rows keep
    their order; renumber positions to stay sequential."""
    text = (prompt or "").strip()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT prompt, grp FROM criteria_favorites WHERE user_id = ? "
            "ORDER BY position ASC, created_at DESC, rowid DESC", (user_id,)
        ).fetchall()
        if not any(r["prompt"] == text for r in rows):
            return list_favorites(user_id)
        grp_of = {r["prompt"]: (r["grp"] or "") for r in rows}
        grp = grp_of[text]
        rest = [r["prompt"] for r in rows if r["prompt"] != text]
        first_same = next(
            (i for i, p in enumerate(rest) if grp_of[p] == grp), None)
        if first_same is None:
            # Only item in its row — already at the front. No-op.
            return list_favorites(user_id)
        new_order = rest[:first_same] + [text] + rest[first_same:]
        for i, p in enumerate(new_order):
            conn.execute(
                "UPDATE criteria_favorites SET position = ? "
                "WHERE user_id = ? AND prompt = ?", (i, user_id, p),
            )
    return list_favorites(user_id)


def remove_favorite(user_id: str, prompt: str) -> list:
    with _lock, _connect() as conn:
        conn.execute(
            "DELETE FROM criteria_favorites WHERE user_id = ? AND prompt = ?",
            (user_id, (prompt or "").strip()),
        )
    return list_favorites(user_id)


# ─── brand kit (R8 extra — per-account caption defaults, synced cross-device) ─

def get_brand_kit(user_id: str) -> dict:
    """User's brand kit + when it was last written: {"kit", "updated_at"}."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT kit, updated_at FROM brand_kits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"kit": {}, "updated_at": None}
    try:
        kit = json.loads(row["kit"] or "{}")
    except Exception:
        kit = {}
    return {"kit": kit if isinstance(kit, dict) else {}, "updated_at": row["updated_at"]}


def save_brand_kit(user_id: str, kit: dict) -> dict:
    """Upsert the user's brand kit. Returns the stored kit."""
    clean = kit if isinstance(kit, dict) else {}
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO brand_kits (user_id, kit, updated_at) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET kit=excluded.kit,
                 updated_at=excluded.updated_at""",
            (user_id, json.dumps(clean), time.time()),
        )
    return get_brand_kit(user_id)


# ─── billing / metering ─────────────────────────────────────────────────────

def usage_total(user_id: str, start: float, end: float) -> int:
    """Committed credits consumed between start and end (exclusive of
    releases)."""
    with _lock, _connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM usage_log
               WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                 AND state = 'committed'""",
            (user_id, start, end),
        ).fetchone()
    return int(row[0]) if row else 0


def import_project_state(project: dict, clips: list) -> None:
    """Seed the throwaway remote SQLite with the host's durable project state.

    Used by the Modal edit worker so ops.run_* read the same options,
    transcript, caption edits, subject locks and layout plans as a local run.
    Uses a full-column clip insert — replace_clips() would drop subject_lock,
    layout_plan_edited, hook_variants and approval columns.
    """
    job_id = project.get("job_id", "")
    save_project(job_id,
                 source_label=project.get("source_label", ""),
                 status=project.get("status", "done"),
                 created_at=project.get("created_at") or 0.0,
                 options=project.get("options") or {},
                 error=project.get("error", ""),
                 duration=project.get("duration"),
                 transcript=project.get("transcript") or None,
                 mode=project.get("mode", "viral"),
                 cost=project.get("cost") or None,
                 metrics=project.get("metrics") or None,
                 user_id=project.get("user_id", ""))
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.executemany(
            """INSERT INTO clips (job_id, idx, start, end, title, hook,
                                  video_url, subtitle_url, size_mb, hook_score,
                                  thumbnail_url, hashtags, reason, topic,
                                  speech_density, quality_breakdown,
                                  layout_plan, layout_plan_edited,
                                  hook_variants, approval_status, approval_note,
                                  caption_edits, subject_lock, layer_plan, rev)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(job_id, int(c.get("idx", c.get("index", i))),
              c.get("start", 0), c.get("end", 0),
              c.get("title", ""), c.get("hook", ""),
              c.get("video_url", ""), c.get("subtitle_url", ""),
              c.get("size_mb", 0), c.get("hook_score"),
              c.get("thumbnail_url", ""),
              json.dumps(c.get("hashtags") or []),
              c.get("reason", ""), c.get("topic", ""),
              c.get("speech_density", 50),
              json.dumps(c.get("quality_breakdown") or {}),
              json.dumps(c.get("layout_plan") or []),
              1 if c.get("layout_plan_edited") else 0,
              json.dumps(c.get("hook_variants") or [])
              if c.get("hook_variants") else None,
              c.get("approval_status", ""),
              c.get("approval_note"),
              json.dumps(c.get("caption_edits") or {})
              if c.get("caption_edits") else None,
              json.dumps(c.get("subject_lock"))
              if c.get("subject_lock") is not None else None,
              json.dumps(c.get("layer_plan"))
              if c.get("layer_plan") is not None else None,
              int(c.get("rev", 0)))
             for i, c in enumerate(clips or [])],
        )


def seed_billing_state(user_id: str, pack_id: str) -> None:
    """Mirror the host's watermark decision onto the remote SQLite.

    ops._is_free_tier resolves through billing.requires_klipin_watermark ->
    highest_paid_pack(user_id), which reads the payments table. The remote
    payments table is empty, so the edit worker re-seeds exactly the tier the
    host computed (free-tier users keep their Klip-in watermark on re-renders).
    """
    if pack_id not in ("mulai", "kreator", "studio"):
        return
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO payments (user_id, provider, external_id, plan_id,
                                     amount_idr, status, created_at)
               VALUES (?, 'seed', 'edit-pipeline-seed', ?, 0, 'paid', ?)""",
            (user_id, pack_id, time.time()),
        )


def usage_committed_in_range(user_id: str, start: float, end: float) -> int:
    """Count of committed generation events (not credits) in [start, end].
    For free-tier monthly metering: each committed job == one generation."""
    with _lock, _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM usage_log
               WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                 AND state = 'committed'""",
            (user_id, start, end),
        ).fetchone()
    return int(row[0] or 0)


def usage_reserved_in_range(user_id: str, start: float, end: float) -> int:
    """Count of in-flight (reserved) generations in [start, end]."""
    with _lock, _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM usage_log
               WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                 AND state = 'reserved'""",
            (user_id, start, end),
        ).fetchone()
    return int(row[0] or 0)


# ── Klipin Music ───────────────────────────────────────────────────────────
def create_music_job(job: dict) -> dict:
    fields = ("job_id", "user_id", "status", "title", "prompt", "lyrics",
              "genre", "language", "vocal_gender", "provider", "seed", "duration",
              "audio_path", "lyrics_path", "cover_path", "cover_source",
              "group_id", "take_index", "parent_job_id", "lora_id", "take_mode",
              "instrumental", "bpm", "music_key", "persona_id", "is_public",
              "metadata", "error", "group_cache_key",
              "created_at", "updated_at")
    values = [job.get(k, "" if k not in ("seed", "duration") else None)
              for k in fields]
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"INSERT INTO music_jobs ({','.join(fields)}) "
            f"VALUES ({','.join('?' for _ in fields)}) "
            "ON CONFLICT(user_id, group_cache_key, take_index) "
            "WHERE group_cache_key <> '' DO NOTHING",
            values,
        )
        deduped = (cur.rowcount or 0) == 0
    if deduped:
        existing = find_music_by_cache(
            job.get("user_id", ""), job.get("group_cache_key", ""),
            completed_only=False)
        return {"_deduped": True, **(existing or {})}
    return get_music_job(job["job_id"], job.get("user_id", ""))


def update_music_job(job_id: str, user_id: str = "", **changes) -> dict | None:
    allowed = {"status", "provider", "audio_path", "lyrics_path", "cover_path",
               "cover_source", "metadata", "lyrics",
               "error", "title", "updated_at", "duration"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if not changes:
        return get_music_job(job_id, user_id)
    changes["updated_at"] = time.time()
    where = "job_id = ?" + (" AND user_id = ?" if user_id else "")
    args = list(changes.values()) + [job_id] + ([user_id] if user_id else [])
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE music_jobs SET {','.join(f'{k}=?' for k in changes)} WHERE {where}", args)
    return get_music_job(job_id, user_id)


def get_music_job(job_id: str, user_id: str = "") -> dict | None:
    where = "job_id = ?" + (" AND user_id = ?" if user_id else "")
    args = [job_id] + ([user_id] if user_id else [])
    with _lock, _connect() as conn:
        row = conn.execute(f"SELECT * FROM music_jobs WHERE {where}", args).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.get("metadata") or "{}")
    except (TypeError, ValueError):
        result["metadata"] = {}
    return result


def list_music_jobs(user_id: str, limit: int = 50, query: str = "") -> list:
    """Riwayat + search (judul/prompt/genre). Hanya milik user."""
    limit = max(1, min(int(limit), 100))
    with _lock, _connect() as conn:
        if (query or "").strip():
            like = f"%{(query or '').strip()[:60]}%"
            rows = conn.execute(
                "SELECT * FROM music_jobs WHERE user_id = ? AND "
                "(title LIKE ? OR prompt LIKE ? OR genre LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, like, like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM music_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [get_music_job(r["job_id"], user_id) for r in rows]


# ── Music studio projects (versioned song workspace) ──────────────────────
MUSIC_SNAPSHOT_LIMIT = 200_000


def _music_revision_meta(row) -> dict:
    rev = dict(row)
    rev.pop("snapshot", None)
    return rev


def create_music_project(user_id: str, name: str, snapshot: dict,
                         job_id: str = "", group_id: str = "") -> dict:
    import secrets as _secrets
    now = time.time()
    pid = "mproj_" + _secrets.token_hex(6)
    rid = "mrev_" + _secrets.token_hex(6)
    payload = json.dumps(snapshot or {}, ensure_ascii=False)[:MUSIC_SNAPSHOT_LIMIT]
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO music_projects "
            "(project_id,user_id,name,job_id,group_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, user_id, (name or "Proyek tanpa nama")[:80], job_id or "",
             group_id or "", now, now))
        conn.execute(
            "INSERT INTO music_project_revisions "
            "(revision_id,project_id,user_id,revision_index,label,snapshot,"
            "source_job_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, pid, user_id, 1, "awal", payload, job_id or "", now))
    return get_music_project(pid, user_id)


def get_music_project(project_id: str, user_id: str,
                      include_revisions: bool = True) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM music_projects WHERE project_id=? AND user_id=?",
            (project_id, user_id)).fetchone()
        if not row:
            return None
        project = dict(row)
        revs = conn.execute(
            "SELECT * FROM music_project_revisions "
            "WHERE project_id=? AND user_id=? ORDER BY revision_index DESC",
            (project_id, user_id)).fetchall()
        project["revision_count"] = len(revs)
        if include_revisions:
            project["revisions"] = [_music_revision_meta(r) for r in revs]
            try:
                project["snapshot"] = (json.loads(revs[0]["snapshot"])
                                       if revs else {})
            except (TypeError, ValueError, KeyError):
                project["snapshot"] = {}
        return project


def list_music_projects(user_id: str, limit: int = 50) -> list:
    limit = max(1, min(int(limit), 100))
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM music_project_revisions r "
            "WHERE r.project_id = p.project_id) AS revision_count "
            "FROM music_projects p WHERE p.user_id=? "
            "ORDER BY p.updated_at DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_music_project_revision(project_id: str, revision_id: str,
                               user_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM music_project_revisions WHERE revision_id=? "
            "AND project_id=? AND user_id=?",
            (revision_id, project_id, user_id)).fetchone()
    if not row:
        return None
    rev = dict(row)
    try:
        rev["snapshot"] = json.loads(rev.get("snapshot") or "{}")
    except (TypeError, ValueError):
        rev["snapshot"] = {}
    return rev


def save_music_project_revision(project_id: str, user_id: str, snapshot: dict,
                                label: str = "",
                                source_job_id: str = "") -> dict | None:
    import secrets as _secrets
    now = time.time()
    payload = json.dumps(snapshot or {}, ensure_ascii=False)[:MUSIC_SNAPSHOT_LIMIT]
    with _lock, _connect() as conn:
        project = conn.execute(
            "SELECT project_id FROM music_projects WHERE project_id=? AND user_id=?",
            (project_id, user_id)).fetchone()
        if not project:
            return None
        nxt = conn.execute(
            "SELECT COALESCE(MAX(revision_index),0)+1 FROM music_project_revisions "
            "WHERE project_id=?", (project_id,)).fetchone()[0]
        rid = "mrev_" + _secrets.token_hex(6)
        conn.execute(
            "INSERT INTO music_project_revisions "
            "(revision_id,project_id,user_id,revision_index,label,snapshot,"
            "source_job_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, project_id, user_id, int(nxt),
             (label or f"revisi {nxt}")[:120], payload, source_job_id or "", now))
        conn.execute(
            "UPDATE music_projects SET updated_at=?, "
            "job_id=COALESCE(NULLIF(?,''), job_id) WHERE project_id=?",
            (now, source_job_id or "", project_id))
    return get_music_project_revision(project_id, rid, user_id)


def delete_music_project(project_id: str, user_id: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT project_id FROM music_projects WHERE project_id=? AND user_id=?",
            (project_id, user_id)).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM music_project_revisions WHERE project_id=? AND user_id=?",
            (project_id, user_id))
        conn.execute(
            "DELETE FROM music_projects WHERE project_id=? AND user_id=?",
            (project_id, user_id))
    return True


def create_music_lyric_draft(user_id: str, title: str, lyrics: str,
                             notes: str = "", genre: str = "",
                             language: str = "") -> dict:
    import secrets as _secrets
    now = time.time()
    did = "mlyr_" + _secrets.token_hex(6)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO music_lyric_drafts "
            "(draft_id,user_id,title,lyrics,notes,genre,language,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (did, user_id, (title or "Draft tanpa judul")[:80], lyrics or "",
             notes or "", genre or "", language or "", now, now))
    return get_music_lyric_draft(did, user_id)


def get_music_lyric_draft(draft_id: str, user_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM music_lyric_drafts WHERE draft_id=? AND user_id=?",
            (draft_id, user_id)).fetchone()
    return dict(row) if row else None


def list_music_lyric_drafts(user_id: str, limit: int = 50) -> list:
    limit = max(1, min(int(limit), 100))
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT draft_id, title, notes, genre, language, "
            "LENGTH(lyrics) AS lyric_chars, created_at, updated_at "
            "FROM music_lyric_drafts WHERE user_id=? "
            "ORDER BY updated_at DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def update_music_lyric_draft(draft_id: str, user_id: str, title: str,
                             lyrics: str, notes: str = "", genre: str = "",
                             language: str = "") -> dict | None:
    now = time.time()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT draft_id FROM music_lyric_drafts "
            "WHERE draft_id=? AND user_id=?", (draft_id, user_id)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE music_lyric_drafts SET title=?, lyrics=?, notes=?, genre=?, "
            "language=?, updated_at=? WHERE draft_id=? AND user_id=?",
            ((title or "Draft tanpa judul")[:80], lyrics or "", notes or "",
             genre or "", language or "", now, draft_id, user_id))
    return get_music_lyric_draft(draft_id, user_id)


def delete_music_lyric_draft(draft_id: str, user_id: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT draft_id FROM music_lyric_drafts "
            "WHERE draft_id=? AND user_id=?", (draft_id, user_id)).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM music_lyric_drafts WHERE draft_id=? AND user_id=?",
            (draft_id, user_id))
    return True


def set_music_public(job_id: str, user_id: str, public: bool) -> dict | None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE music_jobs SET is_public = ?, updated_at = ? "
            "WHERE job_id = ? AND user_id = ?",
            (1 if public else 0, time.time(), job_id, user_id))
    return get_music_job(job_id, user_id)


def public_music_feed(limit: int = 30) -> list:
    """Feed publik: hanya field aman (tanpa user_id)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM music_jobs WHERE is_public = 1 AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()
    out = []
    for r in rows:
        job = dict(r)
        try:
            job["metadata"] = json.loads(job.get("metadata") or "{}")
        except (TypeError, ValueError):
            job["metadata"] = {}
        job.pop("user_id", None)
        job.pop("error", None)
        out.append(job)
    return out


# ── Music personas (style & voice-signature lock) ─────────────────────
def create_music_persona(user_id: str, name: str, source: dict) -> dict:
    import secrets as _secrets
    pid = "persona_" + _secrets.token_hex(6)
    meta = source.get("metadata") or {}
    tags = [str(meta.get("genre_label") or source.get("genre") or ""),
            f"{source.get('language') or 'id'} vocal",
            f"{source.get('vocal_gender') or 'auto'} vocal"]
    style = ", ".join(t for t in tags if t)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO music_personas (persona_id, user_id, name, source_job_id, "
            "genre, language, vocal_gender, style_tags, seed_base, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, user_id, (name or source.get("title") or "Persona")[:80],
             source.get("job_id", ""), source.get("genre", ""),
             source.get("language", "id"), source.get("vocal_gender", "auto"),
             style[:300], source.get("seed"), "", time.time()))
    return get_music_persona(pid, user_id)


def create_music_voice_persona(user_id: str, name: str, vocal_gender: str,
                              voice_ref_path: str, consent_at: float,
                              persona_id: str = "") -> dict:
    """Create an account-owned persona backed by an encrypted voice sample."""
    import secrets as _secrets
    pid = persona_id or ("persona_" + _secrets.token_hex(6))
    gender = vocal_gender if vocal_gender in ("male", "female") else "auto"
    style = f"{gender} vocal" if gender != "auto" else "user voice reference"
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO music_personas (persona_id,user_id,name,vocal_gender,"
            "style_tags,voice_ref_path,voice_consent_at,persona_type,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, user_id, (name or "Suara saya")[:80], gender, style,
             voice_ref_path, float(consent_at), "recorded", time.time()))
    return get_music_persona(pid, user_id)


def create_music_designed_persona(user_id: str, name: str, vocal_gender: str,
                                 style_tags: str, notes: str = "") -> dict:
    """Save a model-conditioned vocal direction, not a cloned voice identity."""
    import secrets as _secrets
    pid = "persona_" + _secrets.token_hex(6)
    gender = vocal_gender if vocal_gender in ("male", "female") else "auto"
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO music_personas (persona_id,user_id,name,vocal_gender,"
            "style_tags,notes,persona_type,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, user_id, (name or "Karakter vokal")[:80], gender,
             (style_tags or "")[:300], (notes or "")[:500], "designed", time.time()))
    return get_music_persona(pid, user_id)


def get_music_persona(persona_id: str, user_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM music_personas WHERE persona_id = ? AND user_id = ?",
            (persona_id, user_id)).fetchone()
    return dict(row) if row else None


def list_music_personas(user_id: str) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM music_personas WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_music_persona(persona_id: str, user_id: str) -> bool:
    with _lock, _connect() as conn:
        conn.execute("UPDATE music_voice_previews SET status='deleted',sample_name='' "
                     "WHERE saved_persona_id=? AND user_id=?", (persona_id, user_id))
        cur = conn.execute(
            "DELETE FROM music_personas WHERE persona_id = ? AND user_id = ?",
            (persona_id, user_id))
    return (cur.rowcount or 0) > 0


def clear_music_persona_voice(persona_id: str, user_id: str) -> dict | None:
    """Detach an account's encrypted sample while retaining its style persona."""
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE music_personas SET voice_ref_path='',voice_consent_at=NULL "
            "WHERE persona_id=? AND user_id=?", (persona_id, user_id))
    return get_music_persona(persona_id, user_id)


def record_music_cost(job_id: str, user_id: str, gpu_sec: float,
                      usd: float, credits: int, state: str = "committed") -> None:
    """Metering per-take (tabel sendiri — tidak memakan kuota video)."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO music_costs "
            "(job_id, user_id, gpu_sec, usd, credits, state, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, user_id, round(float(gpu_sec or 0), 1),
             round(float(usd or 0), 6), int(credits or 0),
             state, time.time()))


def music_cost_total(user_id: str) -> dict:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(gpu_sec),0), COALESCE(SUM(usd),0), "
            "COALESCE(SUM(credits),0) FROM music_costs "
            "WHERE user_id = ? AND state = 'committed'",
            (user_id,)).fetchone()
    return {"takes": int(row[0] or 0), "gpu_sec": round(float(row[1] or 0), 1),
            "usd": round(float(row[2] or 0), 4),
            "credits": int(row[3] or 0)}


def list_music_group(group_id: str, user_id: str) -> list:
    """Both takes of one Create (Suno-style A/B), ordered by take_index."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM music_jobs WHERE group_id = ? AND user_id = ? ORDER BY take_index ASC",
            (group_id, user_id),
        ).fetchall()
    return [get_music_job(r["job_id"], user_id) for r in rows]


def find_music_by_cache(user_id: str, cache_key: str,
                        completed_only: bool = True) -> dict | None:
    """Content-addressed lookup on the GROUP cache key (spec minus seed).

    ``completed_only=False`` also returns an in-flight group so a duplicate
    request can join the running render instead of paying for a second one.
    """
    if not cache_key:
        return None
    clause = "user_id = ? AND group_cache_key = ?"
    args = [user_id, cache_key]
    if completed_only:
        clause += " AND status = 'completed'"
    with _lock, _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM music_jobs WHERE {clause} "
            "ORDER BY created_at ASC LIMIT 1",
            args,
        ).fetchone()
    return get_music_job(row["job_id"], user_id) if row else None


def credits_purchased(user_id: str) -> int:
    """Total credits ever purchased/ granted (never decreases)."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(credits, 0) FROM users WHERE google_sub = ?",
            (user_id,),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def credits_balance(user_id: str) -> int:
    """Credits AVAILABLE right now: purchases minus in-flight (reserved) and
    spent (committed) usage. Reserved-but-uncommitted jobs hold their credit
    so firing two jobs can't double-spend the last one. Released rows free
    the credit back. Never expires — the KlipAja model."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM usage_log "
            "WHERE user_id = ? AND state IN ('reserved', 'committed')",
            (user_id,),
        ).fetchone()
    used = int(row[0] or 0)
    return max(0, credits_purchased(user_id) - used)


def grant_credits(user_id: str, amount: int) -> None:
    """Add permanent credits to a user's balance (purchase or promo)."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO users (google_sub, credits, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(google_sub) DO UPDATE SET credits = credits + ?",
            (user_id, amount, time.time(), amount),
        )


def usage_log(user_id: str, job_id: str, amount: int,
              state: str = "reserved") -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO usage_log (user_id, job_id, amount, state, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, job_id, amount, state, time.time()),
        )


def usage_set_state(user_id: str, job_id: str, state: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE usage_log SET state = ? WHERE user_id = ? AND job_id = ?",
            (state, user_id, job_id),
        )


def record_payment(user_id: str, provider: str, external_id: str,
                   plan_id: str, amount_idr: int, status: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO payments (user_id, provider, external_id, plan_id,
                 amount_idr, status, created_at) VALUES (?,?,?,?,?,?,?)""",
            (user_id, provider, external_id, plan_id, amount_idr,
             status, time.time()),
        )


def payment_status(external_id: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM payments WHERE external_id = ? "
            "ORDER BY id DESC LIMIT 1", (external_id,),
        ).fetchone()
    return row[0] if row else None


def apply_paid_pack_once(user_id: str, provider: str, external_id: str,
                         plan_id: str, amount_idr: int,
                         credits: int) -> bool:
    """Atomically credit one confirmed provider transaction at most once."""
    with _lock, _connect() as conn:
        seen = conn.execute(
            "SELECT 1 FROM payments WHERE provider = ? AND external_id = ? "
            "AND status = 'paid' LIMIT 1", (provider, external_id),
        ).fetchone()
        if seen:
            return False
        conn.execute(
            "INSERT INTO users (google_sub, credits, created_at) VALUES (?,?,?) "
            "ON CONFLICT(google_sub) DO UPDATE SET credits = credits + ?",
            (user_id, credits, time.time(), credits),
        )
        conn.execute(
            """INSERT INTO payments (user_id, provider, external_id, plan_id,
                 amount_idr, status, created_at) VALUES (?,?,?,?,?,'paid',?)""",
            (user_id, provider, external_id, plan_id, amount_idr, time.time()),
        )
        return True


def highest_paid_pack(user_id: str) -> str:
    """Highest product tier ever confirmed for permanent-credit accounts."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT plan_id FROM payments WHERE user_id = ? AND status = 'paid'",
            (user_id,),
        ).fetchall()
    rank = {"mulai": 1, "kreator": 2, "studio": 3}
    return max((r[0] for r in rows if r[0] in rank),
               key=lambda p: rank[p], default="free")


# ── Remote edit capture mode ────────────────────────────────────────────────
# Studio edit jobs (retrim / rerender / cut / export / caption_edit) can run on
# the Modal L4 worker. The remote container holds only a throwaway SQLite for
# transient caches — durable clip state MUST stay on the API host. In capture
# mode the write helpers above record their arguments instead of touching
# SQLite; the host replays them via apply_captured_writes() after the rendered
# files are downloaded. Read paths are unaffected: the worker seeds the remote
# database with import_project_state() before dispatching.
CAPTURE_WRITES = False
CAPTURED_WRITES: list = []


def start_capture() -> list:
    """Switch write helpers into record-only mode. Returns the capture list."""
    global CAPTURE_WRITES, CAPTURED_WRITES
    CAPTURE_WRITES = True
    CAPTURED_WRITES = []
    return CAPTURED_WRITES


def apply_captured_writes(job_id: str, captured: list) -> dict:
    """Replay writes recorded on the remote worker into the host database.

    Revision bumps are replayed LAST, once per clip: the remote recorded them
    with placeholder values (its SQLite was throwaway), so any captured ``rev``
    field is stripped before applying the surrounding update. Returns
    {clip_idx: new_rev} so the caller can patch returned result payloads.
    """
    global CAPTURE_WRITES
    assert not CAPTURE_WRITES, "apply_captured_writes() during capture mode"
    bump_idx: list[int] = []
    for name, args, kwargs in (captured or []):
        if name == "update_clip":
            fields = {k: v for k, v in (kwargs or {}).items() if k != "rev"}
            update_clip(args[0], args[1], **fields)
        elif name == "bump_clip_rev":
            bump_idx.append(int(args[1]))
        elif name == "replace_clips":
            replace_clips(args[0], args[1])
        elif name == "save_project":
            (jid, source_label, status, created_at, options, error, duration,
             transcript, mode, cost, metrics, user_id) = args
            save_project(jid, source_label=source_label, status=status,
                         created_at=created_at, options=options, error=error,
                         duration=duration, transcript=transcript, mode=mode,
                         cost=cost, metrics=metrics, user_id=user_id)
    revs: dict = {}
    for idx in dict.fromkeys(bump_idx):  # unique, order-preserving
        revs[idx] = bump_clip_rev(job_id, idx)
    return revs
