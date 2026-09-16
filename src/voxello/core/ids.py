"""Request identifiers.

IDs look like ``vox_01J8ZK3Q4X5Y6Z7A8B9C0D1E2F``: a ULID (48-bit millisecond timestamp
plus 80 random bits, Crockford base32). They sort by creation time, are unguessable,
and contain only ``[0-9A-Z_a-z]`` so they are safe in filenames and logs.
"""

from __future__ import annotations

import re
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX = "vox_"
_ID_RE = re.compile(r"^vox_[0-9A-Z]{26}$")


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_request_id(now_ms: int | None = None) -> str:
    """Return a fresh, time-sortable request id."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    rand = secrets.randbits(80)
    return _PREFIX + _encode(ts, 10) + _encode(rand, 16)


def is_request_id(value: str) -> bool:
    return bool(_ID_RE.match(value))
