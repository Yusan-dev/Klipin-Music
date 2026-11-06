"""Small synchronous provider pool with circuit breaking.

The media pipeline is mostly synchronous, so this deliberately mirrors the
async design from TASK.md without forcing an event-loop into FFmpeg workers.
It is provider-agnostic and can be used by ASR, scoring, or vision stages.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Iterable


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider is unavailable or failed."""


@dataclass
class _State:
    failures: int = 0
    open_until: float = 0.0


class ProviderPool:
    def __init__(self, providers: Iterable, failure_threshold: int = 3,
                 cooldown_sec: float = 60.0, max_retries: int = 2,
                 base_delay: float = 1.0, sleep: Callable = time.sleep):
        self.providers = list(providers)
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.0, float(base_delay))
        self._sleep = sleep
        self._state = {self._name(p): _State() for p in self.providers}

    @staticmethod
    def _name(provider) -> str:
        return str(getattr(provider, "name", None) or
                   getattr(provider, "__name__", None) or
                   provider.__class__.__name__)

    def call(self, *args, **kwargs):
        last = None
        now = time.time()
        for provider in self.providers:
            name = self._name(provider)
            state = self._state.setdefault(name, _State())
            if now < state.open_until:
                continue
            fn = getattr(provider, "call", provider)
            for attempt in range(self.max_retries + 1):
                try:
                    result = fn(*args, **kwargs)
                    state.failures = 0
                    state.open_until = 0.0
                    return result
                except Exception as exc:  # provider-specific errors vary
                    last = exc
                    state.failures += 1
                    if state.failures >= self.failure_threshold:
                        state.open_until = time.time() + self.cooldown_sec
                    if attempt >= self.max_retries:
                        break
                    self._sleep(self.base_delay * (2 ** attempt) *
                                (0.75 + random.random() * 0.5))
        raise AllProvidersFailedError(str(last) if last else "no provider available")

