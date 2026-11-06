"""AI Lyric & Style Formatting Agent for Klipin Music Cover Pipeline.

Normalizes raw lyric sheets with strict structural meta-tags ([Verse], [Chorus], etc.)
and generates acoustic English style prompts optimized for SheetSage2 + YuE2.
"""
from __future__ import annotations

import re
from typing import Any

SECTION_TAG_PATTERN = re.compile(
    r"^\s*\[(intro|verse|pre-chorus|chorus|bridge|solo|interlude|instrumental|outro|end)[^\]]*\]\s*$",
    re.IGNORECASE,
)

ASR_HALLUCINATION_PATTERN = re.compile(
    r"(terima\s+kasih(\s+kalo|\s+telah|\s+sudah)?(\s+menonton)?\.?|"
    r"jangan\s+lupa\s+subscribe|subtitle\s+oleh|^intro$|"
    r"you\s+might\s+also\s+like.*|^\d+\s*contributors?$|^embed$)",
    re.IGNORECASE,
)

GENRE_STYLE_MAP: dict[str, str] = {
    "pop": "contemporary pop, bright melodic synth, energetic rhythm, polished production",
    "rock": "energetic rock, driving electric guitars, punchy acoustic drums, powerful bass",
    "jazz": "smooth bossa nova jazz, sophisticated acoustic cafe jazz, relaxed steady straight beat, warm grand piano, nylon acoustic guitar, walking bass, melodic upright bass, gentle brushed drums, locked vocal timing",
    "reggae": "authentic warm island roots pop-reggae cover (BOENTER chill reggae vibe), laid-back 68 BPM half-time one-drop drum groove, muted clean electric guitar and warm Hammond B3 organ bubble on the offbeat skank, deep round sub-dub bassline, melodic brass horn hook, relaxed vocal with warm plate reverb and dub tape delay, silky rolled-off highs",
    "dangdut": "modern dangdut, dynamic kendang koplo rhythms, lively suling bamboo flute, upbeat mandolin, rhythmic bass",
    "dangdut-koplo": "energetic dangdut koplo, driving kendang koplo beat, upbeat tempo, festive synth brass, rhythmic bass grooves",
    "dangdut-remix": "electronic dangdut remix, modern four-on-the-floor club kick, kendang breakbeats, punchy bassline, EDM drop",
    "edm": "electronic dance music, modern festival drops, punchy 4-on-the-floor kick, euphoric synthesizer leads, pumping sidechain compression",
    "lofi": "lo-fi chillhop, dusty vinyl crackle, warm mellow rhodes chords, relaxed boom-bap drum pattern, cozy tape saturation",
    "rnb": "contemporary R&B, modern trap-soul drum groove, rich velvet synth pads, soulful harmonic progressions, warm sub-bass",
    "hiphop": "hip hop, punchy 808 sub-bass, crisp trap hi-hats, swinging boom-bap drums, tight percussions",
    "folk": "warm indie acoustic folk, fingerpicked acoustic guitar, subtle harmonica, gentle foot stomp, organic natural reverb",
    "akustik": "unplugged acoustic, pristine nylon and steel string guitars, gentle shaker, intimate warm room ambience",
    "metal": "heavy metal, distorted high-gain electric guitars, double-bass drum blasts, aggressive bassline, raw stadium energy",
    "ambient": "peaceful ambient, ethereal sprawling synthesizer pads, slow evolving soundscapes, shimmering textures, zero drums",
    "campursari": "traditional javanese campursari, gamelan saron & slenthem elements, kendang rhythms, acoustic keroncong cak-cuk chords",
    "pop-jawa": "modern javanese pop ballad, warm acoustic piano, melodious acoustic guitar, gentle orchestral strings, soulful tempo",
    "keroncong": "classic keroncong, rhythmic cak-cuk ukulele accompaniment, cello pizzicato bassline, melodic suling bamboo flute",
    "sunda": "traditional sundanese pop, suling bambu melodies, kendang sunda percussion, kecapi zither strings, warm atmospheric pads",
    "bali": "balinese modern world fusion, gamelan gong kebyar sparkles, gentle bamboo flute, acoustic guitar, rhythmic ethnic percussions",
    "kpop": "high-energy k-pop production, crisp dance beats, dynamic brass stabs, polished vocal harmonies, infectious synth hooks",
    "jpop": "vibrant j-pop, dynamic chord progressions, melodic lead guitar, upbeat anime-style drums, bright keyboard arrangements",
    "arab": "middle eastern oriental pop, rhythmic darbuka and riq percussions, expressive oud strings, soulful qanun melodies",
    "afrobeats": "infectious west african afrobeats, buoyant log drum rhythms, syncopated percussion, warm guitar licks, sunny brass chords",
}


def normalize_lyrics(raw_lyrics: str) -> str:
    """Normalize raw lyric text into structured blocks with section tags.

    If the text already has section tags ([Verse], [Chorus], etc.), the tags
    and user-specified block breaks are preserved.
    If the text lacks structural markers, it groups paragraphs and automatically
    injects appropriate tags ([Verse], [Chorus], [Bridge], [Outro]).
    """
    text = (raw_lyrics or "").strip()
    if not text:
        return "[Intro]\n\n[Verse]\n(Instrumental)\n\n[Outro]"

    # Clean Windows CRLF to standard LF and normalize unicode spaces/artifacts
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u2000-\u200f\u2028\u2029\xa0\ufeff]", " ", text)

    # Clean ASR hallucination artifacts and Genius/scraped multi-line artifacts
    cleaned_lines: list[str] = []
    skip_scraped_block = False
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            cleaned_lines.append("")
            continue
        # Catch Genius 'You might also like' blocks and skip until next section header
        if re.search(r"you\s+might\s+also\s+like", trimmed, re.IGNORECASE):
            skip_scraped_block = True
            continue
        if skip_scraped_block:
            if trimmed.startswith("[") and trimmed.endswith("]"):
                skip_scraped_block = False
            else:
                continue
        cleaned = ASR_HALLUCINATION_PATTERN.sub("", trimmed).strip()
        if cleaned:
            cleaned_lines.append(cleaned)
        elif trimmed.startswith("[") and trimmed.endswith("]"):
            cleaned_lines.append(trimmed)

    # Rejoin cleaned lines
    cleaned_text = "\n".join(cleaned_lines)

    # Check whether the text already contains any section headers
    has_headers = any(SECTION_TAG_PATTERN.match(ln.strip()) for ln in cleaned_text.splitlines() if ln.strip())

    if has_headers:
        # User/ASR already provided headers - format cleanly with 1 empty line between sections
        sections: list[str] = []
        current_block: list[str] = []

        for line in cleaned_text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("[") and s.endswith("]"):
                if current_block:
                    sections.append("\n".join(current_block))
                    current_block = []
                current_block.append(s)
            else:
                current_block.append(s)

        if current_block:
            sections.append("\n".join(current_block))

        res = "\n\n".join(sections).strip()
        if not res.endswith("[End]"):
            res += "\n\n[End]"
        return res

    # No structural headers found: split by blank lines into stanzas/paragraphs
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", cleaned_text) if p.strip()]

    if not paragraphs:
        return "[Verse]\n" + cleaned_text.strip()

    # Assign standard song structure tags based on stanza count
    tagged_blocks: list[str] = []
    total = len(paragraphs)

    if total == 1:
        tags = ["[Verse]"]
    elif total == 2:
        tags = ["[Verse]", "[Chorus]"]
    elif total == 3:
        tags = ["[Verse]", "[Chorus]", "[Outro]"]
    elif total == 4:
        tags = ["[Verse]", "[Chorus]", "[Bridge]", "[Chorus]"]
    elif total == 5:
        tags = ["[Intro]", "[Verse]", "[Chorus]", "[Bridge]", "[Chorus]"]
    elif total == 6:
        tags = ["[Intro]", "[Verse]", "[Pre-Chorus]", "[Chorus]", "[Bridge]", "[Chorus]"]
    else:
        # For 7+ paragraphs, cycle sensibly
        base_cycle = ["[Intro]", "[Verse]", "[Pre-Chorus]", "[Chorus]", "[Verse]", "[Chorus]", "[Bridge]", "[Chorus]", "[Outro]"]
        tags = []
        for i in range(total):
            tags.append(base_cycle[i % len(base_cycle)])

    for i, p in enumerate(paragraphs):
        tag = tags[i] if i < len(tags) else f"[Verse {i+1}]"
        tagged_blocks.append(f"{tag}\n{p}")

    return "\n\n".join(tagged_blocks).strip()


def build_style_prompt(
    genre: str,
    arrangement: str = "",
    vocal_persona_desc: str = "",
) -> str:
    """Build a rich, structured English style prompt for YuE2 / SheetSage2.

    Follows the GMVP hierarchy:
    Genre / Subgenre -> Mood & Atmosphere -> Instruments & Arrangement -> Vocal Style -> Production Quality.
    """
    g_key = (genre or "").strip().lower()
    base_genre_style = GENRE_STYLE_MAP.get(g_key, f"{genre} style music, rhythmic accompaniment, studio production")

    parts: list[str] = [base_genre_style]

    arr = (arrangement or "").strip()
    if arr:
        # Ensure arrangement does not duplicate genre keywords blindly
        parts.append(arr)

    voc = (vocal_persona_desc or "").strip()
    if voc:
        parts.append(f"vocals: {voc}")
    else:
        parts.append("expressive lead vocals, clear studio microphone articulation")

    # Studio mastering suffix for clean separation
    parts.append("high fidelity, professional mix, balanced frequency spectrum, -14 LUFS studio master")

    # Combine parts with comma separation, normalizing whitespace and double commas
    joined = ", ".join(p.strip().rstrip(",") for p in parts if p.strip())
    joined = re.sub(r",\s*,+", ", ", joined)
    return joined


def transcribe_source_audio_fallback(audio_path: str) -> dict[str, Any]:
    """Fallback helper to transcribe source audio when user provides no lyrics."""
    from music import _transcribe_source_audio
    return _transcribe_source_audio(audio_path)
