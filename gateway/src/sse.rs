//! Native Rust SSE stream for `/api/music/group/{group_id}/stream`.
//!
//! Reads lightweight snapshots from SQLite WAL (`spawn_blocking`) instead of
//! occupying a Python worker or acquiring Python's global `db._lock`.
//! When any pending take already wrote its final 4 output artifacts (or hits
//! a periodic safety check), it pokes `GET /api/music/group/{group_id}` once
//! so Python runs `_reconcile_finished_music_job` and refunds/promotes DB state.

use std::{
    convert::Infallible,
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};

use axum::{
    body::{Body, Bytes},
    http::{header, HeaderMap, HeaderValue, Method, Request, Response, StatusCode, Uri, Version},
};
use serde_json::json;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::{db, headers, sig, AppState, now_unix};

async fn has_complete_artifacts(media_root: &Path, job_id: &str) -> bool {
    if job_id.is_empty() || job_id.contains("..") || job_id.contains('/') || job_id.contains('\\') {
        return false;
    }
    let dir = media_root.join(job_id);
    for name in ["song.mp3", "lyrics.lrc", "cover.jpg", "metadata.json"] {
        let Ok(meta) = tokio::fs::metadata(dir.join(name)).await else {
            return false;
        };
        if !meta.is_file() || meta.len() == 0 {
            return false;
        }
    }
    true
}

async fn trigger_reconcile(st: &AppState, group_id: &str, auth_header: &str) {
    let Ok(uri) = format!("{}/api/music/group/{}", st.upstream, group_id).parse::<Uri>() else {
        return;
    };
    let mut req = Request::new(Body::empty());
    *req.method_mut() = Method::GET;
    *req.uri_mut() = uri;
    *req.version_mut() = Version::HTTP_11;
    if let Ok(v) = HeaderValue::from_str(auth_header) {
        req.headers_mut().insert(header::AUTHORIZATION, v);
    }
    let _ = tokio::time::timeout(Duration::from_secs(5), st.client.request(req)).await;
}

fn is_terminal(takes: &[serde_json::Value]) -> bool {
    !takes.is_empty()
        && takes.iter().all(|t| {
            matches!(t.get("status").and_then(|v| v.as_str()), Some("completed" | "failed"))
        })
}

pub async fn stream_group(
    st: Arc<AppState>,
    req_headers: &HeaderMap,
    group_id: String,
    user_id: String,
    auth_header: String,
) -> Result<Response<Body>, ()> {
    let Some(pool) = st.db_pool.clone() else {
        return Err(());
    };

    // Initial check synchronously (offloaded to blocking pool) before committing SSE headers
    // so SQLite issues cleanly fall back to Python proxy.
    let gid_init = group_id.clone();
    let uid_init = user_id.clone();
    let pool_init = pool.clone();
    let initial = tokio::task::spawn_blocking(move || db::list_music_group(&pool_init, &gid_init, &uid_init))
        .await
        .map_err(|_| ())?
        .map_err(|_| ())?;

    let (tx, rx) = mpsc::channel::<Result<Bytes, Infallible>>(16);

    tokio::spawn(async move {
        if initial.is_empty() {
            let _ = tx
                .send(Ok(Bytes::from_static(
                    b"event: error\ndata: {\"detail\":\"Music group not found\"}\n\n",
                )))
                .await;
            return;
        }

        let start = Instant::now();
        let max_dur = Duration::from_secs(30 * 60);
        let mut takes = initial;
        let mut last_raw = String::new();
        let mut tick: u32 = 0;

        loop {
            let raw_group = json!({ "group_id": group_id, "takes": takes });
            let raw_str = serde_json::to_string(&raw_group).unwrap_or_default();
            if raw_str != last_raw {
                last_raw = raw_str;
                let mut signed = raw_group;
                let exp = now_unix() + st.media_ttl;
                sig::sign_media_json(&st.media_secret, exp, &mut signed);
                if let Ok(payload) = serde_json::to_string(&signed) {
                    let frame = format!("event: group\ndata: {payload}\n\n");
                    if tx.send(Ok(Bytes::from(frame))).await.is_err() {
                        break;
                    }
                }
            }

            if is_terminal(&takes) {
                let _ = tx.send(Ok(Bytes::from_static(b"event: end\ndata: {}\n\n"))).await;
                break;
            }

            if start.elapsed() > max_dur {
                break;
            }

            if tx.send(Ok(Bytes::from_static(b": keep-alive\n\n"))).await.is_err() {
                break;
            }

            tokio::time::sleep(Duration::from_secs(1)).await;
            tick = tick.wrapping_add(1);

            // Reconcile safety bridge if artifacts are already on disk or every 20s for stale guard.
            let mut should_reconcile = tick.is_multiple_of(20);
            if !should_reconcile {
                if let Some(root) = st.media_root.as_deref() {
                    for t in &takes {
                        if matches!(t.get("status").and_then(|v| v.as_str()), Some("queued" | "processing")) {
                            if let Some(jid) = t.get("job_id").and_then(|v| v.as_str()) {
                                if has_complete_artifacts(root, jid).await {
                                    should_reconcile = true;
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            if should_reconcile {
                trigger_reconcile(&st, &group_id, &auth_header).await;
            }

            let gid = group_id.clone();
            let uid = user_id.clone();
            let p = pool.clone();
            match tokio::task::spawn_blocking(move || db::list_music_group(&p, &gid, &uid)).await {
                Ok(Ok(next)) if !next.is_empty() => takes = next,
                _ => {}
            }
        }
    });

    let mut resp = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-cache")
        .header(header::CONNECTION, "keep-alive")
        .header("x-accel-buffering", "no")
        .body(Body::from_stream(ReceiverStream::new(rx)))
        .expect("SSE response is valid");
    headers::decorate_media(req_headers, resp.headers_mut());
    Ok(resp)
}
