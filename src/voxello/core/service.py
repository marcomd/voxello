"""VoxelloService: turns agent intents into synthesis, playback and notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path

from voxello.config import Settings
from voxello.core.ids import new_request_id
from voxello.core.models import (
    TERMINAL_STATES,
    HealthReport,
    NotifyResult,
    PlaybackHealth,
    Priority,
    RequestRecord,
    RequestState,
    RequestSummary,
    SpeechMode,
    SpeechResult,
    StatusReport,
    StopResult,
    TTSHealth,
)
from voxello.errors import (
    INVALID_PARAMETER,
    INVALID_TEXT,
    PLAYBACK_UNAVAILABLE,
    TEXT_TOO_LONG,
    VoxelloError,
)
from voxello.logging_setup import describe_text
from voxello.notifications.base import Notifier
from voxello.notifications.desktop import DesktopNotifier
from voxello.playback.base import AudioPlayer
from voxello.playback.detect import detect_backend
from voxello.playback.manager import Outcome, PlaybackItem, PlaybackManager
from voxello.playback.subprocess_player import SubprocessPlayer
from voxello.storage.cache import AudioCache, CacheKeyParts
from voxello.storage.files import OutputStore, TempStore
from voxello.storage.wav import wav_duration_ms
from voxello.tts.base import TTSProvider
from voxello.tts.voicestudio import VoiceStudioProvider

log = logging.getLogger(__name__)

HISTORY_SIZE = 50
HEALTH_CACHE_SECONDS = 10.0
VALID_CHANNELS = ("voice", "desktop", "file")


class VoxelloService:
    def __init__(
        self,
        settings: Settings,
        *,
        provider: TTSProvider,
        player: AudioPlayer | None,
        notifier: Notifier | None,
        temp_store: TempStore,
        output_store: OutputStore,
        audio_cache: AudioCache | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.player = player
        self.notifier = notifier
        self.temp_store = temp_store
        self.output_store = output_store
        self.audio_cache = audio_cache
        self.playback: PlaybackManager | None = (
            PlaybackManager(
                player,
                max_queue_size=settings.playback.max_queue_size,
                interrupt_clears_queue=settings.playback.interrupt_clears_queue,
                on_outcome=self._on_playback_outcome,
            )
            if player is not None
            else None
        )
        self._records: OrderedDict[str, RequestRecord] = OrderedDict()
        self._synth_lock = asyncio.Semaphore(1)
        self._generating = 0
        self._cache_hits = 0
        self._sweeper: asyncio.Task[None] | None = None
        self._health_cache: tuple[float, HealthReport] | None = None
        self._started = False

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        provider: TTSProvider | None = None,
        player: AudioPlayer | None = None,
        notifier: Notifier | None = None,
    ) -> VoxelloService:
        if provider is None:
            if settings.tts.provider != "voicestudio":  # pragma: no cover - enforced by config
                raise VoxelloError(
                    INVALID_PARAMETER, f"Unknown TTS provider {settings.tts.provider}"
                )
            provider = VoiceStudioProvider(settings.tts.voicestudio)
        if player is None and settings.playback.enabled:
            backend = detect_backend(settings.playback.backend)
            if backend is not None:
                player = SubprocessPlayer(backend, settings.playback.volume)
            else:
                log.warning("No supported audio player detected; playback disabled")
        if notifier is None and settings.notifications.desktop:
            notifier = DesktopNotifier()
        temp_store = TempStore(
            settings.storage.resolved_temp_dir(),
            settings.storage.temp_retention_minutes,
            settings.storage.cleanup_on_start,
        )
        output_store = OutputStore(settings.output.resolved_directory())
        audio_cache = cls.cache_from_settings(settings)
        return cls(
            settings,
            provider=provider,
            player=player,
            notifier=notifier,
            temp_store=temp_store,
            output_store=output_store,
            audio_cache=audio_cache,
        )

    @staticmethod
    def cache_from_settings(settings: Settings) -> AudioCache | None:
        """Build the audio cache described by ``settings.cache`` (``None`` when disabled)."""
        if not settings.cache.enabled:
            return None
        return AudioCache(
            settings.cache.resolved_directory(),
            max_entries=settings.cache.max_entries,
            max_age_days=settings.cache.max_age_days,
        )

    # -- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self.temp_store.prepare()
        if self.audio_cache is not None:
            self.audio_cache.prepare()
        if self.playback is not None:
            self.playback.start()
        self._sweeper = asyncio.create_task(self.temp_store.run_sweeper(), name="voxello-sweeper")
        self._started = True
        log.info(
            "Voxello started (provider=%s, player=%s, notifier=%s)",
            self.provider.name,
            self.player.name if self.player else None,
            getattr(self.notifier, "command", None) if self.notifier else None,
        )

    async def aclose(self) -> None:
        if not self._started:
            return
        self._started = False
        if self.playback is not None:
            await self.playback.aclose()
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
            self._sweeper = None
        for record in self._records.values():
            if record.audio_path is not None:
                self.temp_store.discard(record.audio_path)
        await self.provider.aclose()
        log.info("Voxello stopped")

    # -- speak ---------------------------------------------------------------------

    async def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        interrupt: bool | None = None,
        save: bool | None = None,
        play: bool = True,
        mode: SpeechMode = "verbatim",
        client_id: str | None = None,
        cache: bool | None = None,
    ) -> SpeechResult:
        request_id = new_request_id()
        record = RequestRecord(
            id=request_id,
            state=RequestState.RECEIVED,
            text_chars=len(text),
            voice=voice,
            mode=mode,
            client_id=client_id,
        )
        self._remember(record)
        interrupt = self.settings.playback.default_interrupt if interrupt is None else interrupt
        save = self.settings.output.save_by_default if save is None else save
        try:
            record.set_state(RequestState.VALIDATING)
            text = self._validate_text(text)
            voice = self._validate_voice(voice)
            if play and self.playback is None:
                raise VoxelloError(
                    PLAYBACK_UNAVAILABLE, "No supported local audio player was detected."
                )

            record.set_state(RequestState.GENERATING)
            described = describe_text(text, self.settings.logging.log_text)
            log.info(
                "speak %s: %s voice=%s mode=%s interrupt=%s play=%s save=%s cache=%s client=%s",
                request_id,
                described,
                voice or self.settings.tts.voicestudio.voice or "server-default",
                mode,
                interrupt,
                play,
                save,
                cache,
                client_id,
            )

            # Cache lookup happens before the synthesis lock so hits never wait on the TTS
            # server (roadmap 2.2). The cache only ever holds short, repeatable phrases.
            cache_key = (
                self._cache_key(text, voice) if self._cache_allowed(text, mode, cache) else None
            )
            entry = (
                self.audio_cache.lookup(cache_key)
                if cache_key is not None and self.audio_cache is not None
                else None
            )
            if entry is not None:
                audio_path = entry.path
                record.cached = True
                record.voice = entry.voice
                record.duration_ms = wav_duration_ms(entry.path)
                provider_name = entry.provider
                self._cache_hits += 1
                log.info(
                    "cache hit %s: %s (duration=%s ms)", request_id, described, record.duration_ms
                )
            else:
                if cache_key is not None:
                    log.info("cache miss %s: %s", request_id, described)
                started = time.monotonic()
                self._generating += 1
                try:
                    async with self._synth_lock:
                        synthesis = await self.provider.synthesize(text, voice)
                finally:
                    self._generating -= 1
                generation_ms = round((time.monotonic() - started) * 1000)

                audio_path = self.temp_store.write(synthesis.audio)
                record.duration_ms = wav_duration_ms(synthesis.audio)
                record.voice = synthesis.voice
                provider_name = synthesis.provider
                log.info(
                    "generated %s in %d ms (duration=%s ms)",
                    request_id,
                    generation_ms,
                    record.duration_ms,
                )
                if cache_key is not None and self.audio_cache is not None:
                    try:
                        self.audio_cache.store(
                            cache_key,
                            synthesis.audio,
                            voice=synthesis.voice,
                            provider=synthesis.provider,
                            text_chars=len(text),
                        )
                    except VoxelloError as exc:
                        # A cache write failure must never fail the speech itself.
                        log.warning("cache store failed for %s: %s", request_id, exc)
            record.audio_path = audio_path
            record.set_state(RequestState.GENERATED)

            saved_path: Path | None = None
            if save:
                saved_path = self.output_store.save_audio(audio_path, request_id)
                record.saved_path = saved_path
                record.set_state(RequestState.PERSISTED)

            if play and self.playback is not None:
                status = await self.playback.enqueue(
                    PlaybackItem(request_id, audio_path), interrupt=interrupt
                )
                record.set_state(
                    RequestState.QUEUED if status == "queued" else RequestState.PLAYING
                )
            else:
                self.temp_store.discard(audio_path)
                record.audio_path = None
                record.set_state(RequestState.COMPLETED)
                status = "saved" if saved_path else "generated"

            return SpeechResult(
                status=status,
                request_id=request_id,
                duration_ms=record.duration_ms,
                saved_path=str(saved_path) if saved_path else None,
                provider=provider_name,
                voice=record.voice or "server-default",
                cached=record.cached,
            )
        except VoxelloError as exc:
            record.error = exc.code
            record.set_state(RequestState.ERROR)
            if record.audio_path is not None:
                self.temp_store.discard(record.audio_path)
                record.audio_path = None
            log.warning("speak %s failed: %s", request_id, exc)
            raise

    # -- stop / status -------------------------------------------------------------

    async def stop(self, request_id: str | None = None) -> StopResult:
        if self.playback is None:
            return StopResult(status="idle", request_id=request_id)
        status, dropped = await self.playback.stop(request_id)
        log.info("stop_speaking(%s) -> %s (dropped %d)", request_id, status, dropped)
        return StopResult(status=status, request_id=request_id, cleared_queue=dropped)  # type: ignore[arg-type]

    async def status(self) -> StatusReport:
        playback = self.playback
        current = playback.current if playback else None
        queue_length = playback.queue_length if playback else 0
        if self._generating:
            state = "generating"
        elif playback is not None and playback.is_stopping:
            state = "stopping"
        elif current is not None:
            state = "playing"
        elif queue_length:
            state = "queued"
        else:
            state = "idle"
        recent = [
            RequestSummary(
                request_id=r.id,
                state=r.state,
                duration_ms=r.duration_ms,
                client_id=r.client_id,
                error=r.error,
            )
            for r in list(self._records.values())[-5:]
        ][::-1]
        return StatusReport(
            status=state,  # type: ignore[arg-type]
            request_id=current.request_id if current else None,
            provider=self.provider.name,
            voice=self.settings.tts.voicestudio.voice or "server-default",
            queue_length=queue_length,
            cache_hits=self._cache_hits,
            health=await self.health(),
            recent=recent,
        )

    async def health(self, *, force: bool = False) -> HealthReport:
        now = time.monotonic()
        if not force and self._health_cache and now - self._health_cache[0] < HEALTH_CACHE_SECONDS:
            return self._health_cache[1]
        provider_health = await self.provider.health()
        report = HealthReport(
            voxello="ok",
            tts=TTSHealth(
                provider=self.provider.name,
                status=provider_health.status,
                detail=provider_health.detail,
                version=provider_health.version,
            ),
            playback=PlaybackHealth(
                status="ok" if self.player is not None else "unavailable",
                backend=self.player.name if self.player is not None else None,
            ),
            desktop_notifications=(
                "ok" if self.notifier is not None and self.notifier.available() else "unavailable"
            ),
        )
        self._health_cache = (now, report)
        return report

    # -- notify --------------------------------------------------------------------

    async def notify(
        self,
        message: str,
        *,
        channels: list[str] | None = None,
        priority: Priority = "normal",
        title: str | None = None,
        client_id: str | None = None,
        cache: bool | None = None,
    ) -> NotifyResult:
        channels = channels or ["voice", "desktop"]
        for channel in channels:
            if channel not in VALID_CHANNELS:
                raise VoxelloError(
                    INVALID_PARAMETER,
                    f"Unknown channel '{channel}'. Valid channels: {', '.join(VALID_CHANNELS)}.",
                )
        message = self._validate_text(message)
        title = title or self.settings.notifications.title
        results: dict[str, str] = {}
        request_id: str | None = None
        saved_path: str | None = None

        wants_voice = "voice" in channels and self.settings.notifications.voice
        wants_file = "file" in channels
        if "voice" in channels and not self.settings.notifications.voice:
            results["voice"] = "disabled"
        if wants_voice or wants_file:
            interrupt = (
                True if priority in ("high", "critical") else (False if priority == "low" else None)
            )
            try:
                speech = await self.speak(
                    message,
                    interrupt=interrupt,
                    save=wants_file,
                    play=wants_voice,
                    mode="notification",
                    client_id=client_id,
                    cache=cache,
                )
                request_id = speech.request_id
                saved_path = speech.saved_path
                if wants_voice:
                    results["voice"] = speech.status
                if wants_file:
                    try:
                        self.output_store.save_text(message, speech.request_id)
                        results["file"] = "saved"
                    except VoxelloError as exc:
                        results["file"] = f"error:{exc.code}"
            except VoxelloError as exc:
                if wants_voice:
                    results["voice"] = f"error:{exc.code}"
                if wants_file:
                    results["file"] = f"error:{exc.code}"

        if "desktop" in channels:
            if not self.settings.notifications.desktop:
                results["desktop"] = "disabled"
            elif self.notifier is None or not self.notifier.available():
                results["desktop"] = "unavailable"
            else:
                try:
                    await self.notifier.send(title, message)
                    results["desktop"] = "sent"
                except VoxelloError as exc:
                    results["desktop"] = f"error:{exc.code}"

        ok = [
            v for v in results.values() if not v.startswith("error:") and v not in ("unavailable",)
        ]
        if len(ok) == len(results):
            status = "delivered"
        elif ok:
            status = "partial"
        else:
            status = "failed"
        log.info("notify -> %s %s", status, results)
        return NotifyResult(
            status=status,  # type: ignore[arg-type]
            channels=results,
            request_id=request_id,
            saved_path=saved_path,
        )

    # -- internals -----------------------------------------------------------------

    def _validate_text(self, text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise VoxelloError(INVALID_TEXT, "Text must be a non-empty string.")
        limit = self.settings.limits.max_text_chars
        if len(text) > limit:
            raise VoxelloError(
                TEXT_TOO_LONG,
                f"Text is {len(text)} characters; the limit is {limit}. "
                "Speak a short summary and keep details in the textual answer.",
            )
        return text.strip()

    @staticmethod
    def _validate_voice(voice: str | None) -> str | None:
        if voice is None:
            return None
        voice = voice.strip()
        if not voice:
            return None
        if len(voice) > 128 or any(ch in voice for ch in "\x00\n\r"):
            raise VoxelloError(INVALID_PARAMETER, "Invalid voice identifier.")
        return voice

    def _cache_allowed(self, text: str, mode: SpeechMode, cache: bool | None) -> bool:
        """Cache policy (roadmap 2.2): notifications by default, anything on request,
        never beyond the configured length bounds or when the cache is disabled."""
        if self.audio_cache is None or not self.settings.cache.enabled or cache is False:
            return False
        if cache is None and mode != "notification":
            return False
        bounds = self.settings.cache
        return bounds.min_text_chars <= len(text) <= bounds.max_text_chars

    def _cache_key(self, text: str, voice: str | None) -> str:
        vs = self.settings.tts.voicestudio
        return AudioCache.key(
            CacheKeyParts(
                text=text,
                voice=voice or vs.voice,
                engine=vs.engine,
                language=vs.language,
                speed=vs.speed,
                num_step=vs.num_step,
                guidance_scale=vs.guidance_scale,
                base_url=vs.base_url,
            )
        )

    def _remember(self, record: RequestRecord) -> None:
        self._records[record.id] = record
        while len(self._records) > HISTORY_SIZE:
            _, old = self._records.popitem(last=False)
            if old.audio_path is not None and old.state in TERMINAL_STATES:
                self.temp_store.discard(old.audio_path)

    def get_record(self, request_id: str) -> RequestRecord | None:
        return self._records.get(request_id)

    async def _on_playback_outcome(self, item: PlaybackItem, outcome: Outcome) -> None:
        record = self._records.get(item.request_id)
        if outcome == "playing":
            if record is not None:
                record.set_state(RequestState.PLAYING)
            return
        mapping: dict[str, RequestState] = {
            "completed": RequestState.COMPLETED,
            "cancelled": RequestState.CANCELLED,
            "dropped": RequestState.CANCELLED,
            "error": RequestState.ERROR,
        }
        if record is not None:
            record.set_state(mapping[outcome])
            if outcome == "error":
                record.error = record.error or "playback_error"
            record.audio_path = None
        self.temp_store.discard(item.path)
        log.info("playback %s -> %s", item.request_id, outcome)
