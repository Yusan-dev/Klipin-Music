//! Decide whether a `/videos/*` request can be served natively by Rust.
//!
//! The rule is deliberately a strict SUBSET of what `backend/app.py`
//! (`_VideosApp`) allows, so the gateway can never widen access:
//!
//! * only `GET`/`HEAD` (checked by the caller),
//! * never when the query mentions `download` (attachment names, on-the-fly
//!   transcode — Python keeps owning that),
//! * only with a valid HMAC signature OR for the public `music_*/<file>` audio
//!   / art / lyric assets that Python also serves without auth,
//! * only clean relative paths (no `.`/`..`/empty segments, no backslashes or
//!   NUL), and only files that canonicalize inside the media root.
//!
//! Anything else returns `None` and the caller proxies it to Python
//! unchanged (token auth, legacy-file self-healing, 401/404 responses …).

use std::path::{Path, PathBuf};

use percent_encoding::percent_decode_str;

use crate::sig;

/// Extensions Python treats as public music media (`is_music_media`).
const MUSIC_EXTS: [&str; 12] = [
    "mp3", "wav", "ogg", "flac", "m4a", "aac", "jpg", "jpeg", "png", "webp", "lrc", "json",
];

#[derive(Debug, Default, PartialEq, Eq)]
pub struct MediaQuery {
    pub exp: Option<String>,
    pub sig: Option<String>,
}

/// Same parsing as Python: split on `&`, keep parts containing `=`,
/// `partition('=')`, later keys overwrite earlier ones, values NOT decoded.
pub fn parse_query(q: &str) -> MediaQuery {
    let mut out = MediaQuery::default();
    for part in q.split('&') {
        if let Some((k, v)) = part.split_once('=') {
            match k {
                "exp" => out.exp = Some(v.to_string()),
                "sig" => out.sig = Some(v.to_string()),
                _ => {}
            }
        }
    }
    out
}

/// Percent-decoded path segments below `/videos/`, or `None` when the path
/// is not a clean media path.
pub fn media_segments(decoded_path: &str) -> Option<Vec<String>> {
    let rel = decoded_path.strip_prefix("/videos/")?;
    if rel.is_empty() {
        return None;
    }
    let mut segs = Vec::new();
    for seg in rel.split('/') {
        if seg.is_empty()
            || seg == "."
            || seg == ".."
            || seg.contains('\\')
            || seg.contains('\0')
        {
            return None;
        }
        segs.push(seg.to_string());
    }
    Some(segs)
}

/// Python `is_music_media`: exactly `music_<id>/<file>` with a media suffix.
pub fn is_music_media(segs: &[String]) -> bool {
    if segs.len() != 2 || !segs[0].starts_with("music_") {
        return false;
    }
    Path::new(&segs[1])
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| MUSIC_EXTS.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// Pure authorization step (no filesystem access). Returns the relative
/// segments to serve when the request may be handled natively.
pub fn authorize(secret: &str, raw_path: &str, raw_query: &str, now: i64) -> Option<Vec<String>> {
    if raw_query.contains("download") {
        return None;
    }
    let decoded = percent_decode_str(raw_path).decode_utf8().ok()?;
    let segs = media_segments(&decoded)?;
    let q = parse_query(raw_query);
    let signed_ok = match (q.exp.as_deref(), q.sig.as_deref()) {
        (Some(exp), Some(s)) if !exp.is_empty() && !s.is_empty() => {
            sig::verify(secret, &decoded, exp, s, now)
        }
        _ => false,
    };
    if signed_ok || is_music_media(&segs) {
        Some(segs)
    } else {
        None
    }
}

/// Resolve segments to a regular file that canonicalizes inside `root`
/// (`root` must already be canonical). Symlinks escaping the root are refused.
pub async fn resolve_file(root: &Path, segs: &[String]) -> Option<PathBuf> {
    let mut candidate = root.to_path_buf();
    for s in segs {
        candidate.push(s);
    }
    let canon = tokio::fs::canonicalize(&candidate).await.ok()?;
    if !canon.starts_with(root) {
        return None;
    }
    let meta = tokio::fs::metadata(&canon).await.ok()?;
    if meta.is_file() {
        Some(canon)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &str = "s3cret";

    #[test]
    fn query_parsing_matches_python() {
        assert_eq!(
            parse_query("exp=1&sig=ab&exp=2"),
            MediaQuery { exp: Some("2".into()), sig: Some("ab".into()) }
        );
        assert_eq!(parse_query("noequals&x=1"), MediaQuery::default());
    }

    #[test]
    fn public_music_media_is_native() {
        assert_eq!(
            authorize(SECRET, "/videos/music_1/song.mp3", "", 0),
            Some(vec!["music_1".into(), "song.mp3".into()])
        );
        assert!(authorize(SECRET, "/videos/music_1/cover.JPG", "", 0).is_some());
    }

    #[test]
    fn non_music_requires_valid_signature() {
        assert!(authorize(SECRET, "/videos/job1/clip_1.mp4", "", 0).is_none());
        let s = sig::media_sig(SECRET, "/videos/job1/clip_1.mp4", 500);
        let q = format!("exp=500&sig={s}");
        assert!(authorize(SECRET, "/videos/job1/clip_1.mp4", &q, 100).is_some());
        assert!(authorize(SECRET, "/videos/job1/clip_1.mp4", &q, 501).is_none());
    }

    #[test]
    fn signature_uses_decoded_path() {
        let s = sig::media_sig(SECRET, "/videos/job1/my clip.mp4", 500);
        let q = format!("exp=500&sig={s}");
        assert!(authorize(SECRET, "/videos/job1/my%20clip.mp4", &q, 0).is_some());
    }

    #[test]
    fn download_and_unsafe_paths_fall_back_to_python() {
        assert!(authorize(SECRET, "/videos/music_1/song.mp3", "download=1", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1/../x.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1/%2e%2e/x.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1//song.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1/a%5Cb.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1/.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/music_1/sub/song.mp3", "", 0).is_none());
        assert!(authorize(SECRET, "/videos/", "", 0).is_none());
        assert!(authorize(SECRET, "/videosx/music_1/song.mp3", "", 0).is_none());
    }

    #[tokio::test]
    async fn resolve_refuses_missing_dirs_and_escapes() {
        let root = std::env::temp_dir().join(format!("kg-test-{}", std::process::id()));
        let music = root.join("music_1");
        std::fs::create_dir_all(&music).unwrap();
        std::fs::write(music.join("song.mp3"), b"abc").unwrap();
        let root = std::fs::canonicalize(&root).unwrap();
        let ok = resolve_file(&root, &["music_1".into(), "song.mp3".into()]).await;
        assert_eq!(ok, Some(root.join("music_1").join("song.mp3")));
        assert!(resolve_file(&root, &["music_1".into(), "nope.mp3".into()]).await.is_none());
        assert!(resolve_file(&root, &["music_1".into()]).await.is_none());
        std::fs::remove_dir_all(&root).ok();
    }
}

