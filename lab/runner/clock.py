"""Configurable test clock and deterministic id generation.

Every fixture, driver, and signer receives the same ``LabClock`` and ``LabRng``
so that a seeded run reproduces the same identifiers, timestamps, nonces, and
signed fixture validity windows.
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LabClock:
    """A clock that can be frozen, offset, or advanced explicitly.

    ``frozen_at`` pins ``now()`` to a fixed epoch second (used by deterministic
    suites). When ``frozen_at`` is ``None`` the clock follows wall time plus
    ``offset_seconds``.
    """

    frozen_at: Optional[float] = None
    offset_seconds: float = 0.0
    _ticks: int = field(default=0, repr=False)

    def now(self) -> float:
        if self.frozen_at is not None:
            return self.frozen_at + self.offset_seconds
        return time.time() + self.offset_seconds

    def now_int(self) -> int:
        return int(self.now())

    def iso(self) -> str:
        return datetime.fromtimestamp(self.now(), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def advance(self, seconds: float) -> None:
        self.offset_seconds += seconds

    def tick(self) -> int:
        """Monotonic counter for ordering events within one run."""
        self._ticks += 1
        return self._ticks


class LabRng:
    """Seeded random source used for ids, nonces, and fixture material."""

    def __init__(self, seed: int):
        self.seed = seed
        self._rng = random.Random(seed)

    def hex(self, nbytes: int = 8) -> str:
        return "".join(f"{self._rng.getrandbits(8):02x}" for _ in range(nbytes))

    def token(self, prefix: str, nbytes: int = 6) -> str:
        return f"{prefix}_{self.hex(nbytes)}"

    def uuid(self) -> str:
        h = self.hex(16)
        return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"

    def bytes(self, n: int) -> bytes:
        return bytes(self._rng.getrandbits(8) for _ in range(n))

    def child(self, label: str) -> "LabRng":
        """Derive an independent stream so fixture ids do not perturb client ids.

        Uses SHA-256 rather than ``hash()`` because string hashing is salted per
        process and would break seed reproducibility.
        """
        digest = hashlib.sha256(f"{self.seed}:{label}".encode("utf-8")).digest()
        return LabRng(int.from_bytes(digest[:4], "big"))
