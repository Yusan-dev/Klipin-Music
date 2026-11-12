//! Response headers for natively served responses, mirroring what Python adds:
//!
//! * `CORSMiddleware` (`backend/app.py`): echo allowed `Origin`, `Vary: Origin`.
//! * `security_headers_middleware` (`backend/middleware_security.py`):
//!   - non-JSON: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.
//!   - JSON: also `Strict-Transport-Security` (off localhost), `Referrer-Policy`,
//!     `Content-Security-Policy`, and `Permissions-Policy`.

use axum::http::{header, HeaderMap, HeaderName, HeaderValue};

const ALLOWED_ORIGINS: [&str; 8] = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
    "https://klip-in.id",
    "https://www.klip-in.id",
];

const TUNNEL_SUFFIXES: [&str; 4] = [
    "trycloudflare.com",
    "ngrok-free.app",
    "ngrok-free.dev",
    "ngrok.io",
];

/// Equivalent of the Python `allow_origin_regex` (fully matched):
/// `https://([a-z0-9-]+\.)(trycloudflare\.com|ngrok-free\.app|ngrok-free\.dev|ngrok\.io)`
fn is_tunnel_origin(origin: &str) -> bool {
    let Some(host) = origin.strip_prefix("https://") else {
        return false;
    };
    TUNNEL_SUFFIXES.iter().any(|suffix| {
        host.strip_suffix(suffix)
            .and_then(|label| label.strip_suffix('.'))
            .map(|label| {
                !label.is_empty()
                    && label
                        .bytes()
                        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            })
            .unwrap_or(false)
    })
}

pub fn is_allowed_origin(origin: &str) -> bool {
    ALLOWED_ORIGINS.contains(&origin) || is_tunnel_origin(origin)
}

/// Apply CORS headers when the request carries an allowed `Origin`.
pub fn decorate_cors(req_headers: &HeaderMap, resp_headers: &mut HeaderMap) {
    if let Some(origin) = req_headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) {
        if is_allowed_origin(origin) {
            if let Ok(v) = HeaderValue::from_str(origin) {
                resp_headers.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, v);
                resp_headers.append(header::VARY, HeaderValue::from_static("Origin"));
            }
        }
    }
}

/// Add CORS + security headers for a natively served media or SSE response.
pub fn decorate_media(req_headers: &HeaderMap, resp_headers: &mut HeaderMap) {
    decorate_cors(req_headers, resp_headers);
    resp_headers
        .entry(header::X_CONTENT_TYPE_OPTIONS)
        .or_insert(HeaderValue::from_static("nosniff"));
    resp_headers
        .entry(header::X_FRAME_OPTIONS)
        .or_insert(HeaderValue::from_static("DENY"));
}

/// Add CORS + full security headers matching `security_headers_middleware` for JSON.
pub fn decorate_json(req_headers: &HeaderMap, resp_headers: &mut HeaderMap) {
    decorate_media(req_headers, resp_headers);

    let host = req_headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !host.contains("localhost") && !host.contains("127.0.0.1") {
        resp_headers
            .entry(header::STRICT_TRANSPORT_SECURITY)
            .or_insert(HeaderValue::from_static("max-age=31536000; includeSubDomains; preload"));
    }

    resp_headers
        .entry(header::REFERRER_POLICY)
        .or_insert(HeaderValue::from_static("strict-origin-when-cross-origin"));
    resp_headers
        .entry(HeaderName::from_static("content-security-policy"))
        .or_insert(HeaderValue::from_static(
            "default-src 'self' data:; script-src 'self' 'unsafe-inline'; \
             style-src 'self' 'unsafe-inline'; img-src 'self' data:; \
             connect-src 'self'; font-src 'self'; frame-ancestors 'none'; \
             base-uri 'self'",
        ));
    resp_headers
        .entry(HeaderName::from_static("permissions-policy"))
        .or_insert(HeaderValue::from_static(
            "camera=(), microphone=(self), geolocation=(), payment=(), usb=()",
        ));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn origin_rules_match_python_config() {
        assert!(is_allowed_origin("http://localhost:5175"));
        assert!(is_allowed_origin("https://klip-in.id"));
        assert!(is_allowed_origin("https://abc-123.trycloudflare.com"));
        assert!(is_allowed_origin("https://x.ngrok-free.app"));
        assert!(!is_allowed_origin("https://a.b.trycloudflare.com"));
        assert!(!is_allowed_origin("https://.trycloudflare.com"));
        assert!(!is_allowed_origin("https://ABC.trycloudflare.com"));
        assert!(!is_allowed_origin("http://abc.trycloudflare.com"));
        assert!(!is_allowed_origin("https://evil.com"));
        assert!(!is_allowed_origin("https://eviltrycloudflare.com"));
    }

    #[test]
    fn decorates_only_allowed_origins() {
        let mut req = HeaderMap::new();
        req.insert(header::ORIGIN, HeaderValue::from_static("https://evil.com"));
        let mut resp = HeaderMap::new();
        decorate_media(&req, &mut resp);
        assert!(resp.get(header::ACCESS_CONTROL_ALLOW_ORIGIN).is_none());
        assert_eq!(resp.get(header::X_CONTENT_TYPE_OPTIONS).unwrap(), "nosniff");
        assert_eq!(resp.get(header::X_FRAME_OPTIONS).unwrap(), "DENY");

        req.insert(header::ORIGIN, HeaderValue::from_static("http://localhost:5175"));
        let mut resp = HeaderMap::new();
        decorate_json(&req, &mut resp);
        assert_eq!(
            resp.get(header::ACCESS_CONTROL_ALLOW_ORIGIN).unwrap(),
            "http://localhost:5175"
        );
        assert_eq!(resp.get(header::VARY).unwrap(), "Origin");
        assert!(resp.get("content-security-policy").is_some());
    }
}
