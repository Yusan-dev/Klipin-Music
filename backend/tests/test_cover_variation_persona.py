"""Cover takes must vary per seed and carry the persona's vocal character."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.lyric_style_agent import arrangement_variation, build_style_prompt  # noqa: E402
import music  # noqa: E402


def test_arrangement_variation_changes_with_seed():
    variations = {arrangement_variation("reggae", seed, 45) for seed in range(1794158980, 1794158992)}
    assert len(variations) >= 6


def test_arrangement_variation_is_reproducible_for_same_seed():
    assert arrangement_variation("reggae", 4242, 50) == arrangement_variation("reggae", 4242, 50)


def test_style_prompt_puts_user_direction_first_with_variation():
    prompt = build_style_prompt("reggae", arrangement="saxophone intro and guitar",
                                vocal_persona_desc="warm breathy female", variation="steppers groove")
    assert prompt.startswith("saxophone intro and guitar")
    assert "one-drop" in prompt and "steppers groove" in prompt
    assert "BOENTER" not in prompt  # rigid full preset is not pinned on every take


def test_cover_direction_strips_pipeline_clauses_and_default_prompt():
    spec = {"prompt": "sexophone intro; preserve the extracted lyrics exactly, keep order; make it reggae"}
    assert music._cover_arrangement_direction(spec) == "sexophone intro"
    assert music._cover_arrangement_direction({"arrangement_prompt": "rearrange this song as reggae"}) == ""


def test_cover_vocal_direction_uses_persona_tags():
    spec = {"persona_id": "persona_x", "style_tags": "warm rich vocal tone, airy breathy texture",
            "vocal_gender": "female"}
    desc = music._cover_vocal_direction(spec)
    assert desc.startswith("warm rich vocal tone")
    assert "airy breathy texture" in desc

