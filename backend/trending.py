"""Trending YouTube per kategori untuk beranda Klipin (dinamis, bukan statis).

Kebanyakan klip Klipin bersumber dari YouTube, jadi beranda menampilkan 5
channel yang sedang ramai per kategori (berita / podcast / olahraga). Datanya
diambil live via yt-dlp `ytsearch` (flat, tanpa download) lalu dikelompokkan
per channel — sehingga daftarnya berubah-ubah mengikuti YouTube, bukan
hardcode yang basi.

Kontrak:
- Metadata-only: tidak menyentuh download/render/guardian. Cache in-memory
  TTL 6 jam; saat fetch gagal, sajikan cache basi daripada error.
- Tanpa API key YouTube; thumbnail memakai i.ytimg.com (publik, gratis).
"""

import threading
import time

CACHE_TTL = 3600  # 1 jam — viral naik/redup hitungan jam, bukan hari

# Dua query per kategori: 1 broad (apa yang sedang ramai) + 1 anchor
# (channel besar yang wajib muncul: Densu/HAS Creative di podcast, Sport77
# di olahraga). Hasil digabung lalu di-ranking per channel — daftar tetap
# dinamis mengikuti YouTube, tapi channel raksasa tidak tenggelam.
CATEGORIES = {
    "berita": [
        "ytsearch10:berita indonesia terbaru hari ini",
        "ytsearch5:kompas tv breaking news live",
    ],
    "podcast": [
        "ytsearch10:podcast indonesia terbaru",
        "ytsearch5:denny sumargo has creative podcast",
    ],
    "olahraga": [
        "ytsearch10:sepak bola indonesia highlights",
        "ytsearch5:sport77",
    ],
}

TOP_CHANNELS = 5

_cache: dict = {}
_lock = threading.Lock()


def _fetch_entries(queries: list) -> list:
    """Flat search via yt-dlp: cepat (tanpa download/metadata penuh).

    Multi-query digabung berurutan (broad dulu, anchor kemudian); satu query
    gagal tidak menggugurkan yang lain.
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 15,
    }
    out = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        for query in queries:
            try:
                info = ydl.extract_info(query, download=False)
            except Exception:
                continue
            for e in (info or {}).get("entries") or []:
                if e and e.get("id"):
                    out.append(e)
    return out


def _rank_channels(entries: list) -> list:
    """Kelompokkan video per channel → 5 channel teratas.

    Urutan: total views (bila tersedia di flat field) lalu frekuensi
    kemunculan, lalu peringkat pencarian. Video teratas tiap channel menjadi
    tombol "klip video ini" (URL video asli, siap masuk intake).
    """
    groups: dict = {}
    for rank, e in enumerate(entries):
        vid = str(e.get("id") or "")
        if not vid or vid.startswith("ytsearch"):
            continue
        channel = str(e.get("channel") or e.get("uploader") or "Unknown").strip()
        key = str(e.get("channel_id") or channel)
        g = groups.setdefault(key, {
            "channel": channel,
            "channel_id": e.get("channel_id"),
            "hits": 0,
            "views": 0,
            "best_rank": rank,
            "top_video": None,
        })
        g["hits"] += 1
        try:
            v = int(e.get("view_count") or 0)
        except (TypeError, ValueError):
            v = 0
        g["views"] += v
        if g["top_video"] is None:
            url = str(e.get("url") or "")
            video_url = url if url.startswith("http") else \
                f"https://www.youtube.com/watch?v={vid}"
            g["top_video"] = {
                "id": vid,
                "title": str(e.get("title") or "Tanpa judul"),
                "url": video_url,
                "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "duration": e.get("duration"),
                "view_count": e.get("view_count"),
            }

    ranked = sorted(
        groups.values(),
        key=lambda g: (-g["views"], -g["hits"], g["best_rank"]),
    )
    out = []
    for g in ranked[:TOP_CHANNELS]:
        channel_id = g.get("channel_id")
        out.append({
            "channel": g["channel"],
            "channel_url": (f"https://www.youtube.com/channel/{channel_id}"
                            if channel_id else None),
            "videos_found": g["hits"],
            "total_views": g["views"] or None,
            "top_video": g["top_video"],
        })
    return out


def get_trending(category: str = "berita", force: bool = False) -> dict:
    """Kembalikan {category, updated_at, stale, channels:[...]}.

    `stale=True` berarti fetch live gagal dan ini data cache lama — frontend
    tetap menampilkannya dengan label waktu update.
    """
    category = str(category or "berita").strip().lower()
    if category not in CATEGORIES:
        category = "berita"
    now = time.time()
    with _lock:
        hit = _cache.get(category)
        if hit and not force and now - hit["at"] < CACHE_TTL:
            return {**hit["data"], "stale": False}
    try:
        entries = _fetch_entries(CATEGORIES[category])
        if not entries:
            raise RuntimeError("trending fetch returned no entries")
        data = {"category": category, "updated_at": int(now),
                "channels": _rank_channels(entries)}
        with _lock:
            _cache[category] = {"at": now, "data": data}
        return {**data, "stale": False}
    except Exception:
        with _lock:
            hit = _cache.get(category)
            if hit:
                return {**hit["data"], "stale": True}
        raise
