"""Base Interface for Klipin Music Cover Engines.

Defines the standard contract that all cover engines (MuLaCover, DiffRhythm,
YuE2 ComfyUI, YuE2 Native, etc.) must implement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseCoverEngine(ABC):
    """Abstract Base Class for song cover generation engines."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Unique engine identifier (e.g. 'yue2_comfy', 'yue2_native')."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name for the engine."""
        pass

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Check if this engine is currently configured and operational.

        Returns:
            (available, message): True and empty string if available,
            or False and a descriptive reason if unavailable.
        """
        pass

    @abstractmethod
    def generate_cover(
        self,
        source_audio_path: str | Path,
        lyrics: str,
        genre_style: str,
        vocal_persona: str = "",
        seed: int | None = None,
        max_duration: int = 180,
        output_dir: str | Path = "",
        extra_params: dict[str, Any] | None = None,
        log: Any = None,
    ) -> dict[str, Any]:
        """Execute end-to-end cover generation.

        Args:
            source_audio_path: Path to normalized reference audio file.
            lyrics: Normalized lyrics text with section markers.
            genre_style: Assembled acoustic style prompt.
            vocal_persona: Persona description or voice ID.
            seed: RNG seed for reproducible generation.
            max_duration: Target duration in seconds.
            output_dir: Destination folder for output audio & stems.
            extra_params: Optional engine-specific configuration flags.
            log: Optional logging callable.

        Returns:
            Dictionary containing:
                - 'audio_path': Absolute path to the rendered audio file (.mp3 / .wav).
                - 'duration': Generated audio duration in seconds.
                - 'engine': Engine ID used.
                - 'status': 'completed' or 'failed'.
                - 'metadata': Engine-specific execution details.
        """
        pass
