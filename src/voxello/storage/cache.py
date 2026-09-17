"""On-disk cache of synthesized audio for phrases that repeat (roadmap milestone 2).

Fixed sentences such as the Claude Code hook alerts are synthesized once and replayed from
here afterwards, which removes the TTS round trip and keeps alerts working while the
server is down. Entries are keyed by a SHA-256 of every parameter that shapes the audio
(normalized text, voice, engine, language, speed, num_step, guidance_scale, server URL), so
changing the voice or the server simply produces new keys.

Privacy (spec sections 18 and 27): the directory is 0700 and files 0600 like the temporary
store; file names are the hash, never the text; the JSON sidecar next to each WAV stores the
voice label, provider and text length only. Least-recently-used eviction bounds the entry
count and age. The cache lives in its own directory, separate from ``TempStore``, whose
``owns()`` guard already keeps the service from deleting anything stored here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import stat
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from voxello.errors import STORAGE_ERROR, VoxelloError
from voxello.storage.wav import is_wav

log = logging.getLogger(__name__)

# Bump when the key layout changes so old entries become unreachable instead of wrong.
KEY_VERSION = 1
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_AUDIO_SUFFIX = ".wav"
_META_SUFFIX = ".json"
_TMP_MARKER = ".tmp-"
_STALE_TMP_SECONDS = 3600


def normalize_text(text: str) -> str:
    """Collapse whitespace and apply NFC so trivial variants share one entry."""
    return unicodedata.normalize("NFC", " ".join(text.split()))


@dataclass(frozen=True)
class CacheKeyParts:
    """Everything that influences the synthesized audio."""

    text: str
    voice: str | None
    engine: str
    language: str | None
    speed: float | None
    num_step: int | None
    guidance_scale: float | None
    base_url: str


@dataclass(frozen=True)
class CacheEntry:
    key: str
    path: Path
    voice: str
    provider: str
    text_chars: int
    created_at: float
    last_used: float
    size: int


class AudioCache:
    def __init__(self, directory: Path, *, max_entries: int, max_age_days: int) -> None:
        self.directory = directory
        self.max_entries = max_entries
        self.max_age_seconds = max_age_days * 86400

    # -- setup ---------------------------------------------------------------------

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, stat.S_IRWXU)
        except OSError:  # pragma: no cover - Windows
            pass

    # -- keys ----------------------------------------------------------------------

    @staticmethod
    def key(parts: CacheKeyParts) -> str:
        data: dict[str, Any] = asdict(parts)
        data["text"] = normalize_text(parts.text)
        data["v"] = KEY_VERSION
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def owns(self, path: Path) -> bool:
        try:
            return path.resolve().parent == self.directory.resolve()
        except OSError:
            return False

    # -- read ----------------------------------------------------------------------

    def lookup(self, key: str) -> CacheEntry | None:
        """Return the entry for ``key`` and mark it as recently used, or ``None``."""
        entry = self._read_entry(key)
        if entry is None:
            return None
        try:
            with entry.path.open("rb") as fh:
                header = fh.read(12)
            if not is_wav(header):
                log.warning("cache entry %s is not a WAV file; dropping it", key[:12])
                self._remove(key)
                return None
            now = time.time()
            os.utime(entry.path, (now, now))
        except OSError as exc:
            log.warning("cache entry %s unreadable: %s", key[:12], exc)
            return None
        return CacheEntry(**{**asdict(entry), "last_used": now})

    def entries(self) -> list[CacheEntry]:
        """All valid entries, most recently used first."""
        found: list[CacheEntry] = []
        for key in self._keys():
            entry = self._read_entry(key)
            if entry is not None:
                found.append(entry)
        found.sort(key=lambda e: e.last_used, reverse=True)
        return found

    def stats(self) -> tuple[int, int]:
        """Return ``(entry count, total audio bytes)``."""
        entries = self.entries()
        return len(entries), sum(e.size for e in entries)

    # -- write ---------------------------------------------------------------------

    def store(
        self, key: str, audio: bytes, *, voice: str, provider: str, text_chars: int
    ) -> CacheEntry:
        if not _KEY_RE.match(key):
            raise VoxelloError(STORAGE_ERROR, "Invalid cache key.")
        if not is_wav(audio):
            raise VoxelloError(STORAGE_ERROR, "Refusing to cache non-WAV audio.")
        created_at = time.time()
        meta = {
            "voice": voice,
            "provider": provider,
            "text_chars": text_chars,
            "created_at": created_at,
        }
        try:
            self.prepare()
            self._write_atomic(self._audio_path(key), audio)
            self._write_atomic(
                self._meta_path(key), json.dumps(meta, sort_keys=True).encode("utf-8")
            )
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, "Could not write the audio cache.") from exc
        self.evict()
        return CacheEntry(
            key=key,
            path=self._audio_path(key),
            voice=voice,
            provider=provider,
            text_chars=text_chars,
            created_at=created_at,
            last_used=created_at,
            size=len(audio),
        )

    def evict(self) -> int:
        """Drop entries older than ``max_age_days`` and the least recently used beyond
        ``max_entries``. Returns the number of entries removed."""
        removed = 0
        now = time.time()
        cutoff = now - self.max_age_seconds
        survivors: list[tuple[float, str]] = []
        try:
            for path in self.directory.iterdir():
                if _TMP_MARKER in path.name:
                    self._remove_stale_tmp(path, now)
                    continue
                if path.suffix != _AUDIO_SUFFIX or not _KEY_RE.match(path.stem):
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime <= cutoff:
                    self._remove(path.stem)
                    removed += 1
                else:
                    survivors.append((mtime, path.stem))
        except OSError as exc:
            log.warning("Could not scan the audio cache: %s", exc)
            return removed
        survivors.sort(reverse=True)
        for _, key in survivors[self.max_entries :]:
            self._remove(key)
            removed += 1
        if removed:
            log.info("audio cache evicted %d entr%s", removed, "y" if removed == 1 else "ies")
        return removed

    def clear(self) -> int:
        """Delete every entry. Returns the number of entries removed."""
        removed = 0
        if not self.directory.is_dir():
            return 0
        for path in list(self.directory.iterdir()):
            if _TMP_MARKER in path.name:
                path.unlink(missing_ok=True)
                continue
            if path.suffix == _AUDIO_SUFFIX and _KEY_RE.match(path.stem):
                removed += 1
                self._remove(path.stem)
            elif path.suffix == _META_SUFFIX and _KEY_RE.match(path.stem):
                path.unlink(missing_ok=True)
        return removed

    # -- internals -----------------------------------------------------------------

    def _audio_path(self, key: str) -> Path:
        return self.directory / f"{key}{_AUDIO_SUFFIX}"

    def _meta_path(self, key: str) -> Path:
        return self.directory / f"{key}{_META_SUFFIX}"

    def _keys(self) -> list[str]:
        try:
            return [
                p.stem
                for p in self.directory.iterdir()
                if p.suffix == _AUDIO_SUFFIX and _KEY_RE.match(p.stem)
            ]
        except OSError:
            return []

    def _read_entry(self, key: str) -> CacheEntry | None:
        if not _KEY_RE.match(key):
            return None
        audio = self._audio_path(key)
        meta_path = self._meta_path(key)
        try:
            st = audio.stat()
        except OSError:
            # Orphan sidecar without audio: clean it up.
            meta_path.unlink(missing_ok=True)
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            voice = str(meta["voice"])
            provider = str(meta["provider"])
            text_chars = int(meta["text_chars"])
            created_at = float(meta.get("created_at", st.st_mtime))
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("cache entry %s has no valid sidecar; dropping it", key[:12])
            self._remove(key)
            return None
        return CacheEntry(
            key=key,
            path=audio,
            voice=voice,
            provider=provider,
            text_chars=text_chars,
            created_at=created_at,
            last_used=st.st_mtime,
            size=st.st_size,
        )

    def _remove(self, key: str) -> None:
        for path in (self._audio_path(key), self._meta_path(key)):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover
                log.warning("Could not delete %s: %s", path.name, exc)

    def _remove_stale_tmp(self, path: Path, now: float) -> None:
        try:
            if now - path.stat().st_mtime > _STALE_TMP_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    def _write_atomic(self, target: Path, data: bytes) -> None:
        tmp = target.with_name(f"{target.name}{_TMP_MARKER}{secrets.token_urlsafe(8)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
