//! Read-only SQLite WAL pool for the Rust gateway.
//!
//! Uses `rusqlite` (bundled SQLite) with `PRAGMA query_only = ON` and
//! `busy_timeout = 5000ms` so Rust never mutates `projects.db` and never
//! blocks Python writers. Any transient SQLite error causes callers to fall
//! back seamlessly to proxying the request to Python.

use std::{
    path::PathBuf,
    sync::Mutex,
    time::Duration,
};

use rusqlite::{params, types::ValueRef, Connection, OpenFlags, Row};
use serde_json::{json, Map, Number, Value};

use crate::sig;

const MAX_POOL_SIZE: usize = 8;

pub struct DbPool {
    path: PathBuf,
    conns: Mutex<Vec<Connection>>,
}

impl DbPool {
    pub fn new(path: PathBuf) -> Self {
        Self { path, conns: Mutex::new(Vec::with_capacity(MAX_POOL_SIZE)) }
    }

    fn open_conn(&self) -> rusqlite::Result<Connection> {
        let conn = Connection::open_with_flags(
            &self.path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.busy_timeout(Duration::from_millis(5000))?;
        conn.execute_batch("PRAGMA query_only = ON;")?;
        Ok(conn)
    }

    fn acquire(&self) -> rusqlite::Result<Connection> {
        if let Ok(mut guard) = self.conns.lock() {
            if let Some(c) = guard.pop() {
                return Ok(c);
            }
        }
        self.open_conn()
    }

    fn release(&self, conn: Connection) {
        if let Ok(mut guard) = self.conns.lock() {
            if guard.len() < MAX_POOL_SIZE {
                guard.push(conn);
            }
        }
    }

    pub fn with_conn<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> rusqlite::Result<T> {
        let conn = self.acquire()?;
        let res = f(&conn);
        // Only return healthy connections to the pool.
        if res.is_ok() {
            self.release(conn);
        }
        res
    }
}

fn sql_val_to_json(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => Value::Number(Number::from(i)),
        ValueRef::Real(f) => Number::from_f64(f).map(Value::Number).unwrap_or(Value::Null),
        ValueRef::Text(bytes) => Value::String(String::from_utf8_lossy(bytes).into_owned()),
        ValueRef::Blob(bytes) => Value::String(String::from_utf8_lossy(bytes).into_owned()),
    }
}

fn row_to_map(row: &Row<'_>) -> rusqlite::Result<Map<String, Value>> {
    let cols = row.as_ref().column_names();
    let mut map = Map::with_capacity(cols.len());
    for (idx, name) in cols.iter().enumerate() {
        let val = sql_val_to_json(row.get_ref(idx)?);
        map.insert((*name).to_string(), val);
    }
    Ok(map)
}

fn parse_json_obj_field(map: &mut Map<String, Value>, field: &str) {
    let parsed = match map.get(field) {
        Some(Value::String(s)) if !s.is_empty() => {
            serde_json::from_str::<Value>(s).unwrap_or_else(|_| json!({}))
        }
        Some(Value::Object(_)) => return,
        _ => json!({}),
    };
    map.insert(field.to_string(), parsed);
}

fn music_job_from_row(row: &Row<'_>) -> rusqlite::Result<Value> {
    let mut map = row_to_map(row)?;
    parse_json_obj_field(&mut map, "metadata");
    Ok(Value::Object(map))
}

fn is_pending_job(job: &Value) -> bool {
    matches!(job.get("status").and_then(|v| v.as_str()), Some("queued" | "processing"))
}

pub fn session_user(pool: &DbPool, raw_token: &str, now: f64) -> rusqlite::Result<Option<Value>> {
    if raw_token.is_empty() {
        return Ok(None);
    }
    let token_hash = sig::sha256_hex(raw_token);
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT u.google_sub, u.email, u.name, u.picture, \
             s.created_at AS session_created, s.expires_at AS session_expires \
             FROM sessions s JOIN users u ON u.google_sub = s.user_id \
             WHERE s.token_hash = ?1 AND s.expires_at > ?2",
        )?;
        let mut rows = stmt.query(params![token_hash, now])?;
        if let Some(row) = rows.next()? {
            Ok(Some(Value::Object(row_to_map(row)?)))
        } else {
            Ok(None)
        }
    })
}

/// Returns `(jobs, has_pending)` matching `db.list_music_jobs`.
pub fn list_music_jobs(
    pool: &DbPool,
    user_id: &str,
    limit: i64,
    query: &str,
) -> rusqlite::Result<(Vec<Value>, bool)> {
    let limit = limit.clamp(1, 100);
    let q_trimmed: String = query.trim().chars().take(60).collect();
    pool.with_conn(|conn| {
        let mut out = Vec::new();
        let mut has_pending = false;
        if !q_trimmed.is_empty() {
            let like = format!("%{q_trimmed}%");
            let mut stmt = conn.prepare_cached(
                "SELECT * FROM music_jobs WHERE user_id = ?1 AND \
                 (title LIKE ?2 OR prompt LIKE ?2 OR genre LIKE ?2) \
                 ORDER BY created_at DESC LIMIT ?3",
            )?;
            let mut rows = stmt.query(params![user_id, like, limit])?;
            while let Some(row) = rows.next()? {
                let job = music_job_from_row(row)?;
                has_pending |= is_pending_job(&job);
                out.push(job);
            }
        } else {
            let mut stmt = conn.prepare_cached(
                "SELECT * FROM music_jobs WHERE user_id = ?1 ORDER BY created_at DESC LIMIT ?2",
            )?;
            let mut rows = stmt.query(params![user_id, limit])?;
            while let Some(row) = rows.next()? {
                let job = music_job_from_row(row)?;
                has_pending |= is_pending_job(&job);
                out.push(job);
            }
        }
        Ok((out, has_pending))
    })
}

pub fn get_music_job(pool: &DbPool, job_id: &str, user_id: &str) -> rusqlite::Result<Option<Value>> {
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT * FROM music_jobs WHERE job_id = ?1 AND user_id = ?2",
        )?;
        let mut rows = stmt.query(params![job_id, user_id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(music_job_from_row(row)?))
        } else {
            Ok(None)
        }
    })
}

pub fn list_music_group(pool: &DbPool, group_id: &str, user_id: &str) -> rusqlite::Result<Vec<Value>> {
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT * FROM music_jobs WHERE group_id = ?1 AND user_id = ?2 ORDER BY take_index ASC",
        )?;
        let mut rows = stmt.query(params![group_id, user_id])?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push(music_job_from_row(row)?);
        }
        Ok(out)
    })
}

pub fn list_music_projects(pool: &DbPool, user_id: &str, limit: i64) -> rusqlite::Result<Vec<Value>> {
    let limit = limit.clamp(1, 100);
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT p.*, (SELECT COUNT(*) FROM music_project_revisions r \
             WHERE r.project_id = p.project_id) AS revision_count \
             FROM music_projects p WHERE p.user_id = ?1 \
             ORDER BY p.updated_at DESC LIMIT ?2",
        )?;
        let mut rows = stmt.query(params![user_id, limit])?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push(Value::Object(row_to_map(row)?));
        }
        Ok(out)
    })
}

pub fn get_music_project(pool: &DbPool, project_id: &str, user_id: &str) -> rusqlite::Result<Option<Value>> {
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT * FROM music_projects WHERE project_id = ?1 AND user_id = ?2",
        )?;
        let mut rows = stmt.query(params![project_id, user_id])?;
        let Some(proj_row) = rows.next()? else {
            return Ok(None);
        };
        let mut project = row_to_map(proj_row)?;

        let mut rev_stmt = conn.prepare_cached(
            "SELECT * FROM music_project_revisions \
             WHERE project_id = ?1 AND user_id = ?2 ORDER BY revision_index DESC",
        )?;
        let mut rev_rows = rev_stmt.query(params![project_id, user_id])?;
        let mut revs = Vec::new();
        let mut latest_snapshot = json!({});
        let mut first = true;
        while let Some(r) = rev_rows.next()? {
            let mut rmap = row_to_map(r)?;
            if first {
                first = false;
                if let Some(Value::String(s)) = rmap.get("snapshot") {
                    latest_snapshot = serde_json::from_str(s).unwrap_or_else(|_| json!({}));
                }
            }
            rmap.remove("snapshot");
            revs.push(Value::Object(rmap));
        }
        project.insert("revision_count".into(), json!(revs.len()));
        project.insert("revisions".into(), Value::Array(revs));
        project.insert("snapshot".into(), latest_snapshot);
        Ok(Some(Value::Object(project)))
    })
}

pub fn get_music_project_revision(
    pool: &DbPool,
    project_id: &str,
    revision_id: &str,
    user_id: &str,
) -> rusqlite::Result<Option<Value>> {
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT * FROM music_project_revisions WHERE revision_id = ?1 \
             AND project_id = ?2 AND user_id = ?3",
        )?;
        let mut rows = stmt.query(params![revision_id, project_id, user_id])?;
        let Some(row) = rows.next()? else {
            return Ok(None);
        };
        let mut map = row_to_map(row)?;
        parse_json_obj_field(&mut map, "snapshot");
        Ok(Some(Value::Object(map)))
    })
}

pub fn list_music_lyric_drafts(pool: &DbPool, user_id: &str, limit: i64) -> rusqlite::Result<Vec<Value>> {
    let limit = limit.clamp(1, 100);
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT draft_id, title, notes, genre, language, \
             LENGTH(lyrics) AS lyric_chars, created_at, updated_at \
             FROM music_lyric_drafts WHERE user_id = ?1 \
             ORDER BY updated_at DESC LIMIT ?2",
        )?;
        let mut rows = stmt.query(params![user_id, limit])?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push(Value::Object(row_to_map(row)?));
        }
        Ok(out)
    })
}

pub fn public_music_feed(pool: &DbPool, limit: i64) -> rusqlite::Result<Vec<Value>> {
    let limit = limit.clamp(1, 50);
    pool.with_conn(|conn| {
        let mut stmt = conn.prepare_cached(
            "SELECT * FROM music_jobs WHERE is_public = 1 AND status = 'completed' \
             ORDER BY created_at DESC LIMIT ?1",
        )?;
        let mut rows = stmt.query(params![limit])?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            let mut map = row_to_map(row)?;
            parse_json_obj_field(&mut map, "metadata");
            map.remove("user_id");
            map.remove("error");
            out.push(Value::Object(map));
        }
        Ok(out)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queries_match_python_schema_and_shaping() {
        let path = std::env::temp_dir().join(format!("kg-db-test-{}.db", std::process::id()));
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "PRAGMA journal_mode=WAL;
                CREATE TABLE users (google_sub TEXT PRIMARY KEY, email TEXT, name TEXT, picture TEXT, credits INTEGER, created_at REAL);
                CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT, created_at REAL, expires_at REAL);
                CREATE TABLE music_jobs (
                  job_id TEXT PRIMARY KEY, user_id TEXT, status TEXT, title TEXT, prompt TEXT, lyrics TEXT,
                  genre TEXT, language TEXT, provider TEXT, seed INTEGER, duration REAL,
                  audio_path TEXT, lyrics_path TEXT, cover_path TEXT, cover_source TEXT,
                  vocal_gender TEXT, group_id TEXT, take_index INTEGER, parent_job_id TEXT,
                  lora_id TEXT, take_mode TEXT, instrumental INTEGER, bpm INTEGER, music_key TEXT,
                  persona_id TEXT, is_public INTEGER, metadata TEXT, group_cache_key TEXT, error TEXT,
                  created_at REAL, updated_at REAL
                );
                CREATE TABLE music_projects (
                  project_id TEXT PRIMARY KEY, user_id TEXT, name TEXT, job_id TEXT, group_id TEXT, created_at REAL, updated_at REAL
                );
                CREATE TABLE music_project_revisions (
                  revision_id TEXT PRIMARY KEY, project_id TEXT, user_id TEXT, revision_index INTEGER,
                  label TEXT, snapshot TEXT, source_job_id TEXT, created_at REAL
                );
                CREATE TABLE music_lyric_drafts (
                  draft_id TEXT PRIMARY KEY, user_id TEXT, title TEXT, lyrics TEXT, notes TEXT,
                  genre TEXT, language TEXT, created_at REAL, updated_at REAL
                );",
            ).unwrap();
            let hash = sig::sha256_hex("secret-token");
            conn.execute(
                "INSERT INTO users VALUES ('u1','a@b.c','Alice','pic',10,1.0)",
                [],
            ).unwrap();
            conn.execute(
                "INSERT INTO sessions VALUES (?1,'u1',1.0,9999999999.0)",
                [hash],
            ).unwrap();
            conn.execute(
                "INSERT INTO music_jobs VALUES ('j1','u1','completed','Senja','indie','la','pop','id','modal',42,30.0,'/videos/j1/song.mp3','','', '', 'auto','g1',0,'','', 'final',0,120,'C','',1,'{\"stage\":\"done\"}','','',10.0,11.0)",
                [],
            ).unwrap();
            conn.execute(
                "INSERT INTO music_projects VALUES ('p1','u1','Proyek 1','j1','g1',1.0,2.0)",
                [],
            ).unwrap();
            conn.execute(
                "INSERT INTO music_project_revisions VALUES ('r1','p1','u1',1,'awal','{\"title\":\"Senja\"}','j1',2.0)",
                [],
            ).unwrap();
            conn.execute(
                "INSERT INTO music_lyric_drafts VALUES ('d1','u1','Draft','abc','n','pop','id',1.0,2.0)",
                [],
            ).unwrap();
        }

        let pool = DbPool::new(path.clone());
        let user = session_user(&pool, "secret-token", 100.0).unwrap().unwrap();
        assert_eq!(user["google_sub"], "u1");
        assert!(session_user(&pool, "wrong", 100.0).unwrap().is_none());

        let (jobs, pending) = list_music_jobs(&pool, "u1", 50, "sen").unwrap();
        assert!(!pending);
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0]["metadata"]["stage"], "done");

        let feed = public_music_feed(&pool, 10).unwrap();
        assert_eq!(feed.len(), 1);
        assert!(feed[0].get("user_id").is_none());
        assert!(feed[0].get("error").is_none());

        let proj = get_music_project(&pool, "p1", "u1").unwrap().unwrap();
        assert_eq!(proj["revision_count"], 1);
        assert_eq!(proj["snapshot"]["title"], "Senja");
        assert!(proj["revisions"][0].get("snapshot").is_none());

        let drafts = list_music_lyric_drafts(&pool, "u1", 50).unwrap();
        assert_eq!(drafts[0]["lyric_chars"], 3);

        drop(pool);
        std::fs::remove_file(&path).ok();
    }
}
