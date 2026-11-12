//! HMAC media signatures & JSON URL signing — byte-for-byte compatible
//! with `backend/app.py` (`_media_sig`, `_sign_media_url`, `_verify_media_sig`,
//! and `_sign_media_json`).

use hmac::{Hmac, Mac};
use serde_json::Value;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

type HmacSha256 = Hmac<Sha256>;

/// SHA-256 hex digest of a raw bearer token (same as `auth._hash`).
pub fn sha256_hex(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    hex::encode(hasher.finalize())
}

/// Hex HMAC-SHA256 of `"{path}|{exp}"` (same as Python `_media_sig`).
pub fn media_sig(secret: &str, path: &str, exp: i64) -> String {
    let mut mac =
        HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC accepts keys of any length");
    mac.update(format!("{path}|{exp}").as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Append `?exp=<unix>&sig=<hmac>` (or `&exp=...`) to a `/videos` URL.
pub fn sign_media_url(secret: &str, url: &str, exp: i64) -> String {
    let bare_path = url.split('?').next().unwrap_or(url);
    let sep = if url.contains('?') { '&' } else { '?' };
    let sig = media_sig(secret, bare_path, exp);
    format!("{url}{sep}exp={exp}&sig={sig}")
}

/// Recursively sign `/videos/` media URLs inside any JSON value in-place.
/// Mirrors `backend/app.py:_sign_media_json` (idempotent when `"exp="` exists).
pub fn sign_media_json(secret: &str, exp: i64, val: &mut Value) -> bool {
    let mut hit = false;
    match val {
        Value::String(s) => {
            if s.starts_with("/videos/") && !s.contains("exp=") {
                *s = sign_media_url(secret, s, exp);
                hit = true;
            }
        }
        Value::Array(arr) => {
            for item in arr.iter_mut() {
                hit |= sign_media_json(secret, exp, item);
            }
        }
        Value::Object(map) => {
            for v in map.values_mut() {
                hit |= sign_media_json(secret, exp, v);
            }
        }
        _ => {}
    }
    hit
}

/// Path spellings accepted by Python `_verify_media_sig`.
pub fn candidates(path: &str) -> Vec<String> {
    let mut out = vec![path.to_string()];
    let rel = path.trim_start_matches('/');
    if let Some(rest) = rel.strip_prefix("videos/") {
        out.push(format!("/{rel}"));
        out.push(format!("/{rest}"));
    } else {
        out.push(format!("/videos/{rel}"));
    }
    out
}

/// Constant-time check of `exp` + HMAC over the decoded request path.
pub fn verify(secret: &str, path: &str, exp_raw: &str, sig: &str, now: i64) -> bool {
    let exp: i64 = match exp_raw.trim().parse() {
        Ok(v) => v,
        Err(_) => return false,
    };
    if exp < now {
        return false;
    }
    candidates(path).iter().any(|c| {
        let expected = media_sig(secret, c, exp);
        bool::from(expected.as_bytes().ct_eq(sig.as_bytes()))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const SECRET: &str = "klipin-music-stable-media-secret-v1";

    #[test]
    fn sign_then_verify_roundtrip() {
        let sig = media_sig(SECRET, "/videos/music_abc/song.mp3", 2_000_000_000);
        assert_eq!(sig.len(), 64);
        assert!(verify(SECRET, "/videos/music_abc/song.mp3", "2000000000", &sig, 1_900_000_000));
    }

    #[test]
    fn accepts_mount_stripped_path_like_python() {
        let sig = media_sig(SECRET, "/videos/music_abc/song.mp3", 2_000_000_000);
        assert!(verify(SECRET, "/music_abc/song.mp3", "2000000000", &sig, 0));
    }

    #[test]
    fn rejects_expired_tampered_and_garbage() {
        let sig = media_sig(SECRET, "/videos/music_abc/song.mp3", 100);
        assert!(!verify(SECRET, "/videos/music_abc/song.mp3", "100", &sig, 101));
        let sig = media_sig(SECRET, "/videos/music_abc/song.mp3", 2_000_000_000);
        assert!(!verify(SECRET, "/videos/music_abc/other.mp3", "2000000000", &sig, 0));
        assert!(!verify(SECRET, "/videos/music_abc/song.mp3", "2000000001", &sig, 0));
        assert!(!verify(SECRET, "/videos/music_abc/song.mp3", "abc", &sig, 0));
        assert!(!verify(SECRET, "/videos/music_abc/song.mp3", "2000000000", "", 0));
        assert!(!verify("other-secret", "/videos/music_abc/song.mp3", "2000000000", &sig, 0));
    }

    #[test]
    fn signs_nested_json_urls_once() {
        let mut v = json!({
            "audio_path": "/videos/music_1/song.mp3",
            "cover_path": "/videos/music_1/cover.jpg?v=1",
            "already": "/videos/music_1/stem.mp3?exp=999&sig=abc",
            "ext": "https://example.com/a.mp3",
            "list": ["/videos/music_1/lyrics.lrc", 42]
        });
        assert!(sign_media_json(SECRET, 2_000_000_000, &mut v));
        assert!(v["audio_path"].as_str().unwrap().contains("?exp=2000000000&sig="));
        assert!(v["cover_path"].as_str().unwrap().contains("?v=1&exp=2000000000&sig="));
        assert_eq!(v["already"].as_str().unwrap(), "/videos/music_1/stem.mp3?exp=999&sig=abc");
        assert_eq!(v["ext"].as_str().unwrap(), "https://example.com/a.mp3");
        assert!(v["list"][0].as_str().unwrap().contains("?exp=2000000000&sig="));
        // Idempotent second pass does not modify anything
        assert!(!sign_media_json(SECRET, 2_000_000_100, &mut v));
    }
}
