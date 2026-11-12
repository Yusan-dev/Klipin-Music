//! Klipin Music edge gateway (Rust · axum + tokio + hyper-util + SQLite WAL).
//!
//! Phase 1:
//!   * `/videos/*` audio, cover art, lyrics and signed media streamed natively.
//!
//! Phase 2 (`GATEWAY_NATIVE_DB=1`):
//!   * Hot read endpoints (`/api/auth/me`, `/api/music/feed`, `/api/music/jobs`,
//!     `/api/music/status/{id}`, `/api/music/group/{id}`, `/api/music/projects*`,
//!     `/api/music/lyrics-drafts`) and `/api/music/group/{id}/stream` SSE are
//!     served directly from SQLite WAL without blocking Python's GIL or double
//!     JSON re-encoding.
//!   * Non-terminal jobs (`queued`/`processing`) on status/jobs/group routes
//!     fall back seamlessly to Python so artifact reconciliation & stale-credit
//!     refunds keep 100% parity.

mod db;
mod headers;
mod media;
mod sig;
mod sse;

use std::{
    net::SocketAddr,
    path::PathBuf,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use axum::{
    body::Body,
    extract::{ConnectInfo, State},
    http::{
        header, HeaderMap, HeaderName, HeaderValue, Method, Request, Response, StatusCode, Uri,
        Version,
    },
    routing::get,
    Router,
};
use hyper_util::{
    client::legacy::{connect::HttpConnector, Client},
    rt::TokioExecutor,
};
use percent_encoding::percent_decode_str;
use serde_json::{json, Value};
use tower::ServiceExt;
use tower_http::services::ServeFile;

pub type HttpClient = Client<HttpConnector, Body>;

pub struct AppState {
    pub upstream: String,
    pub media_root: Option<PathBuf>,
    pub media_secret: String,
    pub media_ttl: i64,
    pub db_pool: Option<Arc<db::DbPool>>,
    pub client: HttpClient,
}

const HOP_BY_HOP: [&str; 8] = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

fn is_hop_by_hop(name: &HeaderName) -> bool {
    HOP_BY_HOP.contains(&name.as_str())
}

fn env_nonempty(key: &str) -> Option<String> {
    std::env::var(key).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

pub fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn now_unix_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn json_error(status: StatusCode, detail: &str, req_headers: Option<&HeaderMap>) -> Response<Body> {
    let body = serde_json::to_string(&json!({ "detail": detail }))
        .unwrap_or_else(|_| "{\"detail\":\"Internal error\"}".to_string());
    let mut resp = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(body))
        .expect("static response is valid");
    if let Some(rh) = req_headers {
        headers::decorate_json(rh, resp.headers_mut());
    }
    resp
}

fn json_ok(req_headers: &HeaderMap, media_secret: &str, media_ttl: i64, mut payload: Value) -> Response<Body> {
    let exp = now_unix() + media_ttl;
    sig::sign_media_json(media_secret, exp, &mut payload);
    let body = match serde_json::to_vec(&payload) {
        Ok(b) => b,
        Err(_) => return json_error(StatusCode::INTERNAL_SERVER_ERROR, "Serialization failed", Some(req_headers)),
    };
    let mut resp = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(body))
        .expect("json response is valid");
    headers::decorate_json(req_headers, resp.headers_mut());
    resp
}

fn bearer_token(req_headers: &HeaderMap) -> Option<(String, String)> {
    let raw = req_headers.get(header::AUTHORIZATION)?.to_str().ok()?.trim();
    if raw.len() >= 7 && raw[..7].eq_ignore_ascii_case("bearer ") {
        let token = raw[7..].trim();
        if !token.is_empty() {
            return Some((token.to_string(), raw.to_string()));
        }
    }
    None
}

fn query_param(raw_query: &str, key: &str) -> Option<String> {
    for part in raw_query.split('&') {
        if let Some((k, v)) = part.split_once('=') {
            if k == key {
                let plus_space = v.replace('+', " ");
                if let Ok(decoded) = percent_decode_str(&plus_space).decode_utf8() {
                    return Some(decoded.into_owned());
                }
            }
        }
    }
    None
}

fn decode_segment(seg: &str) -> Option<String> {
    if seg.is_empty() {
        return None;
    }
    let decoded = percent_decode_str(seg).decode_utf8().ok()?.into_owned();
    if decoded.is_empty() || decoded.contains('/') || decoded.contains('\0') {
        return None;
    }
    Some(decoded)
}

async fn authenticate(pool: &Arc<db::DbPool>, req_headers: &HeaderMap) -> Result<Option<(Value, String, String)>, ()> {
    let Some((token, full_auth)) = bearer_token(req_headers) else {
        return Ok(None);
    };
    let p = pool.clone();
    let now = now_unix_f64();
    let user_opt = tokio::task::spawn_blocking(move || db::session_user(&p, &token, now))
        .await
        .map_err(|_| ())?
        .map_err(|_| ())?;
    let Some(user) = user_opt else {
        return Ok(None);
    };
    let uid = user
        .get("google_sub")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if uid.is_empty() {
        return Ok(None);
    }
    Ok(Some((user, uid, full_auth)))
}

async fn try_native_api(
    st: &Arc<AppState>,
    method: &Method,
    path: &str,
    raw_query: &str,
    req_headers: &HeaderMap,
) -> Option<Response<Body>> {
    if method != Method::GET {
        return None;
    }
    let pool = st.db_pool.as_ref()?;

    // 1. Public endpoints (no auth)
    if path == "/api/auth/me" {
        let user = match authenticate(pool, req_headers).await {
            Ok(Some((u, _, _))) => u,
            Ok(None) => Value::Null,
            Err(()) => return None,
        };
        return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, json!({ "user": user })));
    }

    if path == "/api/music/feed" {
        let limit = query_param(raw_query, "limit")
            .and_then(|v| v.parse::<i64>().ok())
            .unwrap_or(30)
            .clamp(1, 50);
        let p = pool.clone();
        let songs = tokio::task::spawn_blocking(move || db::public_music_feed(&p, limit))
            .await
            .ok()?
            .ok()?;
        return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, json!({ "songs": songs })));
    }

    // 2. Authenticated Music Studio hot read / SSE endpoints
    let is_music_read = path == "/api/music/jobs"
        || path == "/api/music/projects"
        || path == "/api/music/lyrics-drafts"
        || path.starts_with("/api/music/status/")
        || path.starts_with("/api/music/group/")
        || path.starts_with("/api/music/projects/");
    if !is_music_read {
        return None;
    }

    let auth_res = authenticate(pool, req_headers).await.ok()?;
    let Some((_, user_id, full_auth)) = auth_res else {
        return Some(json_error(
            StatusCode::UNAUTHORIZED,
            "Authentication required — sign in with Google",
            Some(req_headers),
        ));
    };

    if path == "/api/music/jobs" {
        let q = query_param(raw_query, "q").unwrap_or_default();
        let p = pool.clone();
        let uid = user_id.clone();
        let (jobs, has_pending) = tokio::task::spawn_blocking(move || db::list_music_jobs(&p, &uid, 50, &q))
            .await
            .ok()?
            .ok()?;
        // Any active job might need disk artifact reconciliation or stale credit release by Python.
        if has_pending {
            return None;
        }
        return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, json!({ "jobs": jobs })));
    }

    if let Some(rest) = path.strip_prefix("/api/music/status/") {
        let job_id = decode_segment(rest)?;
        let p = pool.clone();
        let uid = user_id.clone();
        let job = tokio::task::spawn_blocking(move || db::get_music_job(&p, &job_id, &uid))
            .await
            .ok()?
            .ok()?;
        let Some(job) = job else {
            return Some(json_error(StatusCode::NOT_FOUND, "Music job not found", Some(req_headers)));
        };
        if matches!(job.get("status").and_then(|v| v.as_str()), Some("queued" | "processing")) {
            return None;
        }
        return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, job));
    }

    if let Some(rest) = path.strip_prefix("/api/music/group/") {
        if let Some(gid_raw) = rest.strip_suffix("/stream") {
            let group_id = decode_segment(gid_raw)?;
            return sse::stream_group(st.clone(), req_headers, group_id, user_id, full_auth)
                .await
                .ok();
        }
        let group_id = decode_segment(rest)?;
        let p = pool.clone();
        let uid = user_id.clone();
        let gid = group_id.clone();
        let takes = tokio::task::spawn_blocking(move || db::list_music_group(&p, &gid, &uid))
            .await
            .ok()?
            .ok()?;
        if takes.is_empty() {
            return Some(json_error(StatusCode::NOT_FOUND, "Music group not found", Some(req_headers)));
        }
        if takes.iter().any(|t| matches!(t.get("status").and_then(|v| v.as_str()), Some("queued" | "processing"))) {
            return None;
        }
        return Some(json_ok(
            req_headers,
            &st.media_secret,
            st.media_ttl,
            json!({ "group_id": group_id, "takes": takes }),
        ));
    }

    if path == "/api/music/projects" {
        let p = pool.clone();
        let uid = user_id.clone();
        let projects = tokio::task::spawn_blocking(move || db::list_music_projects(&p, &uid, 50))
            .await
            .ok()?
            .ok()?;
        return Some(json_ok(
            req_headers,
            &st.media_secret,
            st.media_ttl,
            json!({ "projects": projects }),
        ));
    }

    if let Some(rest) = path.strip_prefix("/api/music/projects/") {
        let segs: Vec<&str> = rest.split('/').collect();
        if segs.len() == 1 {
            let pid = decode_segment(segs[0])?;
            let p = pool.clone();
            let uid = user_id.clone();
            let proj = tokio::task::spawn_blocking(move || db::get_music_project(&p, &pid, &uid))
                .await
                .ok()?
                .ok()?;
            let Some(proj) = proj else {
                return Some(json_error(StatusCode::NOT_FOUND, "Music project not found", Some(req_headers)));
            };
            return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, proj));
        }
        if segs.len() == 3 && segs[1] == "revisions" {
            let pid = decode_segment(segs[0])?;
            let rid = decode_segment(segs[2])?;
            let p = pool.clone();
            let uid = user_id.clone();
            let rev = tokio::task::spawn_blocking(move || db::get_music_project_revision(&p, &pid, &rid, &uid))
                .await
                .ok()?
                .ok()?;
            let Some(rev) = rev else {
                return Some(json_error(StatusCode::NOT_FOUND, "Music revision not found", Some(req_headers)));
            };
            return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, rev));
        }
        return None;
    }

    if path == "/api/music/lyrics-drafts" {
        let p = pool.clone();
        let uid = user_id.clone();
        let drafts = tokio::task::spawn_blocking(move || db::list_music_lyric_drafts(&p, &uid, 50))
            .await
            .ok()?
            .ok()?;
        return Some(json_ok(req_headers, &st.media_secret, st.media_ttl, json!({ "drafts": drafts })));
    }

    None
}

async fn health(State(st): State<Arc<AppState>>) -> Response<Body> {
    let body = format!(
        "{{\"status\":\"ok\",\"service\":\"klipin-gateway\",\"native_media\":{},\"native_db\":{},\"upstream\":\"{}\"}}",
        st.media_root.is_some(),
        st.db_pool.is_some(),
        st.upstream
    );
    Response::builder()
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::CACHE_CONTROL, "no-store")
        .body(Body::from(body))
        .expect("static response is valid")
}

async fn handle(
    State(st): State<Arc<AppState>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    req: Request<Body>,
) -> Response<Body> {
    if (req.method() == Method::GET || req.method() == Method::HEAD)
        && req.uri().path().starts_with("/videos/")
    {
        if let Some(root) = st.media_root.as_deref() {
            let raw_query = req.uri().query().unwrap_or("");
            if let Some(segs) =
                media::authorize(&st.media_secret, req.uri().path(), raw_query, now_unix())
            {
                if let Some(file) = media::resolve_file(root, &segs).await {
                    return serve_media(file, req).await;
                }
            }
        }
    }

    if req.method() == Method::GET {
        let method = req.method().clone();
        let path = req.uri().path().to_string();
        let raw_query = req.uri().query().unwrap_or("").to_string();
        let req_headers = req.headers().clone();
        if let Some(native_resp) = try_native_api(&st, &method, &path, &raw_query, &req_headers).await {
            return native_resp;
        }
    }

    proxy(&st, peer, req).await
}

async fn serve_media(file: PathBuf, req: Request<Body>) -> Response<Body> {
    let req_headers = req.headers().clone();
    let mime = mime_guess::from_path(&file).first_or(mime_guess::mime::TEXT_PLAIN);
    match ServeFile::new_with_mime(&file, &mime).oneshot(req).await {
        Ok(resp) => {
            let mut resp = resp.map(Body::new);
            headers::decorate_media(&req_headers, resp.headers_mut());
            resp
        }
        Err(err) => {
            eprintln!("[gateway] media read failed for {}: {err}", file.display());
            json_error(StatusCode::INTERNAL_SERVER_ERROR, "Media read failed", Some(&req_headers))
        }
    }
}

async fn proxy(st: &AppState, peer: SocketAddr, req: Request<Body>) -> Response<Body> {
    let (parts, body) = req.into_parts();
    let path_and_query = parts.uri.path_and_query().map(|p| p.as_str()).unwrap_or("/");
    let uri: Uri = match format!("{}{}", st.upstream, path_and_query).parse() {
        Ok(u) => u,
        Err(_) => return json_error(StatusCode::BAD_REQUEST, "Invalid request target", Some(&parts.headers)),
    };

    let mut out_headers = HeaderMap::with_capacity(parts.headers.len() + 1);
    for (name, value) in parts.headers.iter() {
        if !is_hop_by_hop(name) && name != "x-forwarded-for" {
            out_headers.append(name.clone(), value.clone());
        }
    }
    let peer_ip = peer.ip().to_string();
    let xff = match parts.headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()) {
        Some(prev) if !prev.trim().is_empty() => format!("{prev}, {peer_ip}"),
        _ => peer_ip,
    };
    if let Ok(v) = HeaderValue::from_str(&xff) {
        out_headers.insert(HeaderName::from_static("x-forwarded-for"), v);
    }

    let mut upstream_req = Request::new(body);
    *upstream_req.method_mut() = parts.method.clone();
    *upstream_req.uri_mut() = uri;
    *upstream_req.version_mut() = Version::HTTP_11;
    *upstream_req.headers_mut() = out_headers;

    let upstream = match st.client.request(upstream_req).await {
        Ok(r) => r,
        Err(err) => {
            eprintln!("[gateway] upstream error {} {}: {err}", parts.method, parts.uri.path());
            return json_error(StatusCode::BAD_GATEWAY, "Music API is unavailable, please retry", None);
        }
    };

    let (mut up_parts, up_body) = upstream.into_parts();
    let hop: Vec<HeaderName> =
        up_parts.headers.keys().filter(|n| is_hop_by_hop(n)).cloned().collect();
    for name in hop {
        up_parts.headers.remove(&name);
    }
    Response::from_parts(up_parts, Body::new(up_body))
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! { _ = ctrl_c => {}, _ = terminate => {} }
}

#[tokio::main]
async fn main() {
    let listen = env_nonempty("GATEWAY_LISTEN").unwrap_or_else(|| "0.0.0.0:8000".into());
    let upstream = env_nonempty("GATEWAY_UPSTREAM")
        .unwrap_or_else(|| "http://music-api:8000".into())
        .trim_end_matches('/')
        .to_string();
    let media_secret = env_nonempty("KLIPIN_MEDIA_SECRET")
        .or_else(|| env_nonempty("AUTH_SECRET"))
        .unwrap_or_else(|| "klipin-music-stable-media-secret-v1".into());
    let media_ttl = env_nonempty("KLIPIN_MEDIA_TTL")
        .and_then(|v| v.parse::<i64>().ok())
        .unwrap_or(24 * 3600);

    let native_media = env_nonempty("GATEWAY_NATIVE_MEDIA").as_deref() != Some("0");
    let media_root = if native_media {
        let root = env_nonempty("GATEWAY_MEDIA_ROOT").unwrap_or_else(|| "/srv/output".into());
        match std::fs::canonicalize(&root) {
            Ok(p) if p.is_dir() => Some(p),
            _ => {
                eprintln!("[gateway] media root {root} missing; /videos will be proxied to Python");
                None
            }
        }
    } else {
        None
    };

    let native_db = env_nonempty("GATEWAY_NATIVE_DB").as_deref() != Some("0");
    let db_pool = if native_db {
        let db_path = env_nonempty("GATEWAY_DB_PATH").unwrap_or_else(|| "/srv/data/projects.db".into());
        let p = PathBuf::from(&db_path);
        if p.parent().map(|d| d.is_dir()).unwrap_or(false) {
            eprintln!("[gateway] native SQLite WAL read path enabled at {}", p.display());
            Some(Arc::new(db::DbPool::new(p)))
        } else {
            eprintln!("[gateway] DB dir missing for {db_path}; API reads will be proxied to Python");
            None
        }
    } else {
        None
    };

    let mut connector = HttpConnector::new();
    connector.set_connect_timeout(Some(Duration::from_secs(10)));
    connector.set_nodelay(true);
    let client: HttpClient = Client::builder(TokioExecutor::new())
        .pool_idle_timeout(Duration::from_secs(60))
        .pool_max_idle_per_host(64)
        .build(connector);

    let state = Arc::new(AppState {
        upstream: upstream.clone(),
        media_root,
        media_secret,
        media_ttl,
        db_pool,
        client,
    });
    let native_m = state.media_root.is_some();
    let native_d = state.db_pool.is_some();

    let app = Router::new()
        .route("/gateway/health", get(health))
        .fallback(handle)
        .with_state(state);

    let addr: SocketAddr = listen.parse().expect("GATEWAY_LISTEN must be host:port");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind gateway port");
    eprintln!("[gateway] listening on {addr} -> upstream {upstream} (native media: {native_m}, native db: {native_d})");
    axum::serve(listener, app.into_make_service_with_connect_info::<SocketAddr>())
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("gateway server error");
}
