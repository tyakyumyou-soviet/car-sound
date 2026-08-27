from __future__ import annotations

import math
import random
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from array import array
from pathlib import Path
from time import monotonic
from typing import Generator

try:
    import miniaudio
except ImportError:  # The app still has an afplay fallback for a bare Python install.
    miniaudio = None

from .detector import SurgeEvent


class SoundEngine:
    """Recorded GT-86 engine and compressor-release audio on Core Audio."""

    SAMPLE_RATE = 44_100
    CHUNK_FRAMES = 512
    ENGINE_INTERVAL_SECONDS = 0.28  # afplay fallback only
    ENGINE_RPM_BANDS = tuple(range(750, 7_751, 500))
    RECORDED_ENGINE_BANDS = tuple(range(3_000, 7_501, 500))

    def __init__(self, enabled: bool = True, engine_enabled: bool = True) -> None:
        self.enabled = enabled
        self.engine_enabled = engine_enabled
        self.stream_player = None
        self.player = shutil.which("afplay")
        self._directory = tempfile.TemporaryDirectory(prefix="backturbo-")
        self._surge_samples: list[Path] = []
        self._engine_samples: dict[int, Path] = {}
        self._players: list[subprocess.Popen[bytes]] = []
        self._active_flutter_player: subprocess.Popen[bytes] | None = None
        self._last_engine_play = float("-inf")

        self._state_lock = threading.Lock()
        self._profile_controls = {
            "small": {"pitch": 0.70, "tail": 0.65, "volume": 0.85},
            "medium": {"pitch": 0.92, "tail": 0.95, "volume": 1.00},
            "high": {"pitch": 1.10, "tail": 1.00, "volume": 1.15},
        }
        self._target_rpm = 850.0
        self._target_throttle = 0.0
        self._target_boost = -0.65
        self._current_rpm = 850.0
        self._current_throttle = 0.0
        self._current_boost = -0.65
        self._pending_flutter: SurgeEvent | None = None
        self._engine_phase = 0.0
        self._whine_phase_low = 0.0
        self._whine_phase = 0.0
        self._whine_phase_detuned = 0.0
        self._whine_phase_secondary = 0.0
        self._turbo_spool = 0.0
        self._turbo_coast_mix = 0.0
        self._turbo_whine_hz = 720.0
        self._turbo_air = 0.0
        self._turbo_air_low = 0.0
        self._turbo_pitch_wander = 0.0
        self._whine_noise_seed = 0x34_86_15
        self._reference_spool_bands: dict[int, array] = {}
        self._reference_spool_phases: dict[int, float] = {}
        self._turbo_types_spool_bands: dict[int, array] = {}
        self._turbo_types_spool_phases: dict[int, float] = {}
        self._turbo_types_coast_bands: dict[int, array] = {}
        self._turbo_types_coast_phases: dict[int, float] = {}
        self._reference_release_clips: dict[str, array] = {}
        self._reference_surge_pulses: list[array] = []
        self._reference_active_pulses: list[tuple[array, float, float, float]] = []
        self._reference_release_position = 0.0
        self._reference_release_rate = 1.0
        self._reference_release_gain = 0.0
        self._flutter_phase = 0.0
        self._flutter_carrier_left = 0.0
        self._flutter_carrier_right = 0.0
        self._flutter_env_left = 0.0
        self._flutter_env_right = 0.0
        self._flutter_right_delay = 0
        self._flutter_total = 0
        self._flutter_remaining = 0
        self._flutter_rate = 0.0
        self._flutter_amplitude = 0.0
        self._flutter_carrier_hz = 0.0
        self._flutter_delay_samples = 0
        self._flutter_mode = "flutter"
        self._flutter_env_decay = 0.9977
        self._flutter_canceling = False
        self._cancel_flutter_requested = False
        self._active_profile = "small"
        self._profile_pitch_scale = 1.0
        self._profile_tail_scale = 1.0
        self._noise_seed = 0x86_34_26
        self._noise_left = 0.0
        self._noise_right = 0.0
        self._fire_phase = 0.0
        self._combustion_env = 0.0
        self._combustion_variation = 1.0
        self._exhaust_resonators = [[0.0, 0.0] for _ in range(3)]
        self._engine_air = 0.0
        self._engine_mechanical = 0.0
        self._release_air = 0.0
        self._release_air_previous = 0.0
        self._release_tone_phase = 0.0
        self._release_load_mix = 0.0
        self._release_depth = 0.0
        self._surge_resonators_left = [[0.0, 0.0], [0.0, 0.0]]
        self._surge_resonators_right = [[0.0, 0.0], [0.0, 0.0]]
        self._surge_noise_low_left = 0.0
        self._surge_noise_low_right = 0.0
        self._surge_noise_high_left = 0.0
        self._surge_noise_high_right = 0.0
        self._surge_noise_high_second_left = 0.0
        self._surge_noise_high_second_right = 0.0

        self._assets = Path(__file__).resolve().parent.parent / "assets" / "audio"
        self._engine_grains: dict[int, array] = {}
        self._engine_grain_phases: dict[int, float] = {}
        self._recorded_engine_bands: dict[int, array] = {}
        self._recorded_engine_phases: dict[int, float] = {}
        self._recorded_flutter: array | None = None
        self._recorded_flutter_position = 0.0
        self._recorded_flutter_rate = 1.0
        self._recorded_flutter_gain = 0.0
        self._recorded_flutter_pulses = 0
        self._recorded_flutter_gate_left = 0.0
        self._recorded_flutter_gate_right = 0.0
        self._compressor_air: array | None = None
        self._compressor_air_hiss: array | None = None
        self._compressor_air_position = 0.0
        self._compressor_air_rate = 1.0
        self._surge_pulse_wait = 0
        self._surge_pulse_index = 0
        self._surge_pulse_scale = 1.0
        self._surge_pulse_age = 0
        self._surge_pulse_strength = 0.0
        self._surge_tail_output_scale = 1.0
        self._surge_measured_pulse_limit = 12
        self._surge_modeled_tail_count = 4
        self._surge_body_frequency = 620.0
        self._surge_body_left = [0.0, 0.0]
        self._surge_body_right = [0.0, 0.0]
        self._surge_hiss_left = 0.0
        self._surge_hiss_right = 0.0
        self._load_recorded_samples()

        self._stream_process: subprocess.Popen[bytes] | None = None
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._stream_failed = False
        self._audio_device = None
        self._audio_generator: Generator[bytes | array, int, None] | None = None

        if self.enabled or self.engine_enabled:
            self._ensure_stream()
        if self._audio_device is None:
            self._generate_fallback_samples()

    @property
    def available(self) -> bool:
        return self._audio_device is not None or self.player is not None

    @property
    def backend(self) -> str:
        if self._audio_device is not None:
            return "Core Audio / miniaudio (約12–35 ms)"
        if self.player is not None:
            return "afplay fallback (高遅延)"
        return "音声出力なし"

    @staticmethod
    def flutter_parameters(event: SurgeEvent) -> tuple[float, float, float, float]:
        """Return duration, initial pulse rate, amplitude, and carrier frequency."""
        boost = max(0.0, min(1.0, event.boost_bar / 0.85))
        rpm = max(0.0, min(1.0, (event.rpm - 1_800.0) / 5_400.0))
        drop = max(0.0, min(1.0, event.throttle_drop))
        lift = max(0.0, min(1.0, event.lift_rate / 6.0))
        depth = max(0.0, min(1.0, (drop - 0.08) / 0.82))
        release = max(0.0, min(1.0, depth * 0.75 + lift * 0.25))
        if event.reason == "valve_release":
            duration = 0.060 + 0.055 * boost + 0.075 * release
            amplitude = (0.090 + 0.28 * boost + 0.14 * drop) * (0.55 + 0.45 * release)
            carrier_hz = (28.0 + 15.0 * rpm + 6.0 * boost) * (0.82 + 0.18 * release)
            return duration, 0.0, amplitude, carrier_hz
        if event.reason == "pressure_release":
            medium = max(0.0, min(1.0, (event.boost_bar - 0.15) / 0.30))
            duration = 0.20 + 0.18 * medium + (0.08 + 0.12 * medium) * release
            amplitude = (0.24 + 0.24 * medium + 0.12 * drop) * (0.45 + 0.55 * release)
            carrier_hz = (150.0 + 120.0 * medium + 35.0 * rpm) * (0.78 + 0.22 * release)
            return duration, 0.0, amplitude, carrier_hz
        # The supplied video's final full-lift surge lasts substantially
        # longer than its brief and ordinary release events.  Preserve that
        # separation instead of time-compressing every high-boost catch into
        # roughly 0.7 seconds.
        # Leave only enough room for the last measured catch to decay. The
        # pulse train itself now supplies the fade; a long air-only coast made
        # an unwanted final ``shhh`` after the flutter had already finished.
        release_tail = 0.22 + 0.12 * boost
        full_duration = 0.16 + 0.74 * boost + 0.15 * rpm + 0.15 * drop + release_tail
        partial_duration = 0.18 + 0.20 * boost + 0.04 * rpm
        duration = partial_duration + (full_duration - partial_duration) * release
        # Acoustic pitch and repetition rate are independent. A repetition
        # rate near 20 Hz collapses into a "gara-gara" rattle, while roughly
        # 10–15 Hz remains a sequence of distinct compressor catches.
        pulse_rate = 7.0 + 1.7 * rpm + 1.8 * release + 1.0 * boost
        amplitude = (0.035 + 0.38 * boost + 0.13 * drop) * (0.35 + 0.65 * release)
        carrier_hz = 1_050.0 + 720.0 * rpm + 360.0 * boost
        return duration, pulse_rate, amplitude, carrier_hz

    @staticmethod
    def profile_for_reason(reason: str) -> str:
        if reason == "valve_release":
            return "small"
        if reason == "pressure_release":
            return "medium"
        return "high"

    def set_profile_controls(
        self, profile: str, *, pitch: float, tail: float, volume: float,
    ) -> None:
        if profile not in self._profile_controls:
            raise ValueError("unknown sound profile")
        self._profile_controls[profile] = {
            "pitch": max(0.50, min(1.50, float(pitch))),
            "tail": max(0.50, min(1.60, float(tail))),
            "volume": max(0.50, min(1.50, float(volume))),
        }

    def profile_controls(self) -> dict[str, dict[str, float]]:
        return {name: dict(values) for name, values in self._profile_controls.items()}

    def play(self, event: SurgeEvent) -> None:
        if not self.enabled:
            return
        if self._audio_device is not None:
            self._ensure_stream()
            with self._state_lock:
                self._pending_flutter = event
                self._cancel_flutter_requested = False
            return
        if self.player is None:
            return
        duration, rate, amplitude, _ = self.flutter_parameters(event)
        level = min(len(self._surge_samples) - 1, max(0, round((duration - 0.1) / 0.74 * 6)))
        self._active_flutter_player = self._start_fallback(
            self._surge_samples[level],
            volume=min(0.82, 0.25 + amplitude),
            rate=max(0.72, min(1.28, rate / 15.0)),
        )

    def update_engine(self, rpm: float, throttle: float, boost_bar: float) -> None:
        if self._audio_device is not None:
            if self.engine_enabled or self.enabled:
                self._ensure_stream()
            with self._state_lock:
                self._target_rpm = rpm
                self._target_throttle = throttle
                self._target_boost = boost_bar
                if throttle >= 0.12:
                    self._cancel_flutter_requested = True
            return

        now = monotonic()
        if throttle >= 0.12 and self._active_flutter_player is not None:
            if self._active_flutter_player.poll() is None:
                self._active_flutter_player.terminate()
            self._active_flutter_player = None
        if not self.engine_enabled or self.player is None or now - self._last_engine_play < self.ENGINE_INTERVAL_SECONDS:
            return
        self._last_engine_play = now
        band = min(self.ENGINE_RPM_BANDS, key=lambda candidate: abs(candidate - rpm))
        load = max(0.0, min(1.0, throttle * 0.75 + max(0.0, boost_bar) * 0.25))
        self._start_fallback(
            self._engine_samples[band],
            volume=0.13 + load * 0.20,
            rate=max(0.90, min(1.10, rpm / band)),
        )

    def _ensure_stream(self) -> None:
        if self._audio_device is not None or self._stream_failed or miniaudio is None:
            return
        try:
            self._stream_stop.clear()
            self._audio_device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=self.SAMPLE_RATE,
                buffersize_msec=24,
                callback_periods=2,
                app_name="ZN6 Back-Turbine Lab",
            )
            self._audio_generator = self._pcm_callback()
            next(self._audio_generator)
            self._audio_device.start(self._audio_generator)
        except Exception:
            self._stream_failed = True
            if self._audio_device is not None:
                self._audio_device.close()
            self._audio_device = None
            self._generate_fallback_samples()

    def _pcm_callback(self) -> Generator[bytes, int, None]:
        required_frames = yield b""
        while not self._stream_stop.is_set():
            required_frames = yield self.render_chunk(required_frames or self.CHUNK_FRAMES)

    def _activate_flutter(self, event: SurgeEvent) -> None:
        duration, pulse_rate, amplitude, carrier_hz = self.flutter_parameters(event)
        self._active_profile = self.profile_for_reason(event.reason)
        controls = self._profile_controls[self._active_profile]
        self._profile_pitch_scale = controls["pitch"]
        self._profile_tail_scale = controls["tail"]
        duration *= self._profile_tail_scale
        amplitude *= controls["volume"]
        carrier_hz *= self._profile_pitch_scale
        self._flutter_total = max(1, int(duration * self.SAMPLE_RATE))
        self._flutter_remaining = self._flutter_total
        self._flutter_rate = pulse_rate
        self._flutter_amplitude = amplitude
        self._flutter_carrier_hz = carrier_hz
        self._flutter_mode = event.reason
        boost_part = max(0.0, min(1.0, event.boost_bar / 0.85))
        rpm_part = max(0.0, min(1.0, (event.rpm - 1_800.0) / 5_400.0))
        self._release_load_mix = max(0.0, min(1.0, (event.boost_bar - 0.15) / 0.30))
        drop_depth = max(0.0, min(1.0, (event.throttle_drop - 0.08) / 0.82))
        lift_speed = max(0.0, min(1.0, event.lift_rate / 6.0))
        self._release_depth = max(0.0, min(1.0, drop_depth * 0.75 + lift_speed * 0.25))
        high_load_mix = max(0.0, min(1.0, (event.boost_bar - 0.45) / 0.25))
        pulse_extent = self._release_depth * (0.65 + high_load_mix * 0.35)
        self._surge_measured_pulse_limit = 3 + round(9 * pulse_extent)
        self._surge_modeled_tail_count = 1 + round(3 * self._release_depth)
        self._recorded_flutter_position = 0.0
        # A charged compressor produces a noticeably higher-pitched, thinner
        # "shu-tu-tu" than a low-pressure whoosh. Speeding the real recording
        # up with boost raises its pitch without inventing a hard sine tone.
        self._recorded_flutter_rate = 1.25 + rpm_part * 0.12 + boost_part * 0.13
        self._recorded_flutter_gain = 0.30 + amplitude * 1.55
        self._recorded_flutter_pulses = 0
        self._recorded_flutter_gate_left = 0.0
        self._recorded_flutter_gate_right = 0.0
        self._compressor_air_position = 0.0
        self._compressor_air_rate = (
            1.00 + rpm_part * 0.12 + boost_part * 0.10
        ) * self._profile_pitch_scale ** 0.20
        self._surge_pulse_wait = int(0.010 * self.SAMPLE_RATE)
        self._surge_pulse_index = 0
        self._surge_pulse_scale = (
            1.14 - rpm_part * 0.12 - boost_part * 0.20
        ) * self._profile_tail_scale
        self._surge_pulse_age = int(1.0 * self.SAMPLE_RATE)
        self._surge_pulse_strength = 0.0
        self._surge_tail_output_scale = 1.0
        self._surge_body_frequency = 660.0 + rpm_part * 180.0 + boost_part * 120.0
        self._surge_body_left = [0.0, 0.0]
        self._surge_body_right = [0.0, 0.0]
        self._surge_hiss_left = 0.0
        self._surge_hiss_right = 0.0
        self._reference_release_position = 0.0
        self._reference_active_pulses.clear()
        release_clip = self._reference_release_clips.get(event.reason)
        if release_clip is None and event.reason == "clutch":
            release_clip = self._reference_release_clips.get("throttle_lift")
        # Never squeeze a complete recording into a short simulated event.
        # A brief lift simply plays less of the native-rate recording.
        self._reference_release_rate = max(
            0.75,
            min(
                1.25,
                (0.96 + rpm_part * 0.08 + boost_part * 0.04)
                * self._profile_pitch_scale,
            ),
        )
        if event.reason == "valve_release":
            # The supplied clip contains engine/road content that overwhelms
            # a tiny 0.01-0.09 bar release. Keep that range purely procedural,
            # then fade the recording in gradually so 0.10 bar cannot create
            # a sudden timbre/volume jump.
            reference_mix = max(0.0, min(1.0, (event.boost_bar - 0.09) / 0.07))
            self._reference_release_gain = (0.34 + amplitude * 0.78) * reference_mix
        elif event.reason == "pressure_release":
            reference_mix = max(0.0, min(1.0, (event.boost_bar - 0.18) / 0.20))
            self._reference_release_gain = (0.24 + amplitude * 0.52) * reference_mix
        elif release_clip is not None or event.reason in {"throttle_lift", "clutch"}:
            self._reference_release_gain = 0.34 + amplitude * 0.78
            if event.reason == "throttle_lift":
                self._reference_release_gain *= 0.25 + self._release_depth * 0.75
        else:
            self._reference_release_gain = 0.0
        self._release_air = 0.0
        self._release_air_previous = 0.0
        self._release_tone_phase = 0.0
        self._surge_resonators_left = [[0.0, 0.0], [0.0, 0.0]]
        self._surge_resonators_right = [[0.0, 0.0], [0.0, 0.0]]
        self._surge_noise_low_left = 0.0
        self._surge_noise_low_right = 0.0
        self._surge_noise_high_left = 0.0
        self._surge_noise_high_right = 0.0
        self._surge_noise_high_second_left = 0.0
        self._surge_noise_high_second_right = 0.0
        self._flutter_canceling = False
        self._flutter_delay_samples = int((0.0060 - rpm_part * 0.0025) * self.SAMPLE_RATE)
        if event.reason in {"valve_release", "pressure_release"}:
            self._flutter_phase = 0.0
            self._flutter_env_left = amplitude
            self._flutter_env_right = amplitude * 0.91
            self._flutter_env_decay = math.exp(-1.0 / (self.SAMPLE_RATE * 0.032))
        else:
            self._flutter_phase = 0.96  # first pulse follows almost immediately
            self._flutter_env_left = 0.0
            self._flutter_env_right = 0.0
            # Real R34-style surge is not a continuous whoosh. Each compressor
            # re-catch is a short air burst, with enough tail to read as
            # "shu-tu-tu" rather than a digital tremolo.
            pulse_width = 0.030 + event.intensity * 0.018
            self._flutter_env_decay = math.exp(-1.0 / (self.SAMPLE_RATE * pulse_width))
        self._flutter_right_delay = 0

    def render_chunk(self, frames: int) -> bytes:
        """Render one stereo PCM block. Public for deterministic audio tests."""
        with self._state_lock:
            target_rpm = self._target_rpm
            target_throttle = self._target_throttle
            target_boost = self._target_boost
            pending = self._pending_flutter
            self._pending_flutter = None
            cancel_flutter = self._cancel_flutter_requested
            self._cancel_flutter_requested = False
        if pending is not None:
            self._activate_flutter(pending)
        elif cancel_flutter and self._flutter_remaining > 0:
            # Reopening the throttle restores compressor flow. Avoid a digital
            # click, but end surge fast enough to disappear under acceleration.
            self._flutter_canceling = True
            self._flutter_remaining = min(self._flutter_remaining, int(0.045 * self.SAMPLE_RATE))

        block_seconds = frames / self.SAMPLE_RATE
        smoothing = 1.0 - math.exp(-8.0 * block_seconds)
        start_rpm = self._current_rpm
        start_throttle = self._current_throttle
        start_boost = self._current_boost
        end_rpm = start_rpm + (target_rpm - start_rpm) * smoothing
        end_throttle = start_throttle + (target_throttle - start_throttle) * smoothing
        end_boost = start_boost + (target_boost - start_boost) * smoothing
        pcm = array("h")

        for index in range(frames):
            mix = (index + 1) / frames
            rpm = start_rpm + (end_rpm - start_rpm) * mix
            throttle = start_throttle + (end_throttle - start_throttle) * mix
            boost = start_boost + (end_boost - start_boost) * mix

            firing_hz = max(24.0, rpm / 30.0)
            self._engine_phase = (self._engine_phase + 2.0 * math.pi * firing_hz / self.SAMPLE_RATE) % (2.0 * math.pi)
            engine_left = 0.0
            engine_right = 0.0
            if self.engine_enabled:
                engine_left, engine_right = self._render_recorded_engine(rpm, throttle)

            whine = 0.0
            # Compressor spool belongs to the turbo-effects bus.  It remains
            # audible with the engine recording muted, and follows the same
            # toggle as lift-off surge / back-turbine audio.
            if self.enabled:
                rpm_spool = max(0.0, min(1.0, (rpm - 1_500.0) / 5_700.0))
                boost_spool = max(0.0, min(1.0, (boost + 0.08) / 0.90))
                target_spool = boost_spool * 0.72 + rpm_spool * throttle * 0.28
                if target_spool > self._turbo_spool:
                    # The selected first reference is a conventional
                    # single-scroll-style pull: quiet initial loading followed
                    # by a much quicker whistle rise once the rotor is moving.
                    spool_tau = 0.30 if self._turbo_spool < 0.28 else 0.13
                else:
                    spool_tau = 0.42
                spool_alpha = 1.0 - math.exp(-1.0 / (self.SAMPLE_RATE * spool_tau))
                self._turbo_spool += spool_alpha * (target_spool - self._turbo_spool)
                coast_target = 1.0 if (
                    self._turbo_spool > 0.08
                    and (target_spool + 0.01 < self._turbo_spool or throttle < 0.12)
                ) else 0.0
                coast_tau = 0.045 if coast_target > self._turbo_coast_mix else 0.12
                coast_alpha = 1.0 - math.exp(-1.0 / (self.SAMPLE_RATE * coast_tau))
                self._turbo_coast_mix += coast_alpha * (coast_target - self._turbo_coast_mix)
                # The supplied multi-turbo reference has a curved main ridge
                # rising from roughly 1.1 kHz into the 4-5 kHz region, plus a
                # weaker upper ridge.  The steeper exponent preserves spool
                # lag at low pressure instead of making idle sound electric.
                self._turbo_whine_hz = (
                    1_050.0
                    + 4_000.0 * self._turbo_spool ** 1.45
                    + rpm_spool * 280.0
                )
                self._whine_noise_seed = (
                    1_664_525 * self._whine_noise_seed + 1_013_904_223
                ) & 0xFFFFFFFF
                raw_turbo_air = self._whine_noise_seed / 0xFFFFFFFF * 2.0 - 1.0
                self._turbo_pitch_wander += 0.0008 * (raw_turbo_air - self._turbo_pitch_wander)
                whine_hz = self._turbo_whine_hz * (1.0 + self._turbo_pitch_wander * 0.008)
                self._whine_phase_low = (
                    self._whine_phase_low + 2.0 * math.pi * whine_hz * 0.43 / self.SAMPLE_RATE
                ) % (2.0 * math.pi)
                self._whine_phase = (
                    self._whine_phase + 2.0 * math.pi * whine_hz / self.SAMPLE_RATE
                ) % (2.0 * math.pi)
                self._whine_phase_detuned = (
                    self._whine_phase_detuned
                    + 2.0 * math.pi * whine_hz * 1.011 / self.SAMPLE_RATE
                ) % (2.0 * math.pi)
                self._whine_phase_secondary = (
                    self._whine_phase_secondary
                    + 2.0 * math.pi * whine_hz * 1.72 / self.SAMPLE_RATE
                ) % (2.0 * math.pi)
                self._turbo_air_low += 0.018 * (raw_turbo_air - self._turbo_air_low)
                turbulent_air = raw_turbo_air - self._turbo_air_low
                self._turbo_air += 0.34 * (turbulent_air - self._turbo_air)
                drive_level = min(1.0, 0.35 + throttle * 0.65 + self._turbo_coast_mix * 0.18)
                spool_level = self._turbo_spool ** 1.15 * drive_level
                turbine_tone = (
                    math.sin(self._whine_phase_low + self._turbo_air * 0.08) * 0.24
                    + math.sin(self._whine_phase + self._turbo_air * 0.13) * 0.31
                    + math.sin(self._whine_phase_detuned - self._turbo_air * 0.07) * 0.13
                    + math.sin(self._whine_phase_secondary - self._turbo_air * 0.11) * 0.11
                )
                whine = turbine_tone * spool_level * 0.026 + self._turbo_air * spool_level * 0.013
                recorded_spool = self._render_reference_spool(self._turbo_spool)
                accelerating_spool = self._render_turbo_types_spool(self._turbo_spool)
                coast_spool = self._render_turbo_types_coast(self._turbo_spool)
                turbo_types_spool = (
                    accelerating_spool * (1.0 - self._turbo_coast_mix)
                    + coast_spool * self._turbo_coast_mix
                )
                real_spool_gate = max(0.0, min(1.0, (self._turbo_spool - 0.08) / 0.22))
                real_spool_gate = real_spool_gate * real_spool_gate * (3.0 - 2.0 * real_spool_gate)
                whine += recorded_spool * spool_level * 0.22
                whine += turbo_types_spool * spool_level * real_spool_gate * 0.27

            flutter_left = 0.0
            flutter_right = 0.0
            if self.enabled and self._flutter_remaining > 0:
                progress = 1.0 - self._flutter_remaining / self._flutter_total
                if self._flutter_mode in {"valve_release", "pressure_release"}:
                    # Low charge: a short plosive "pou". Medium charge: a
                    # broader filtered-air "pssh". These are pressure-release
                    # voices, not truncated compressor-flutter samples.
                    if self._flutter_mode == "valve_release":
                        attack_rate = 55.0
                        tail_power = 2.4
                    else:
                        attack_rate = 40.0 - self._release_load_mix * 12.0
                        tail_power = 1.70 - self._release_load_mix * 0.65
                    attack = min(1.0, progress * attack_rate)
                    envelope = attack * max(0.0, 1.0 - progress) ** tail_power
                    self._noise_seed = (1_664_525 * self._noise_seed + 1_013_904_223) & 0xFFFFFFFF
                    raw_noise = self._noise_seed / 0xFFFFFFFF * 2.0 - 1.0
                    air_alpha = (
                        0.11
                        if self._flutter_mode == "valve_release"
                        else 0.14 + self._release_load_mix * 0.12
                    )
                    self._release_air += air_alpha * (raw_noise - self._release_air)
                    airy = self._release_air - self._release_air_previous * 0.72
                    self._release_air_previous = self._release_air
                    tone_hz = self._flutter_carrier_hz * (1.0 - 0.48 * progress)
                    self._release_tone_phase += 2.0 * math.pi * tone_hz / self.SAMPLE_RATE
                    tone = (
                        math.sin(self._release_tone_phase)
                        + math.sin(self._release_tone_phase * 2.0) * 0.38
                        + math.sin(self._release_tone_phase * 3.0) * 0.10
                    ) / 1.48
                    if self._flutter_mode == "valve_release":
                        voice = tone * 0.95 + airy * 0.42
                    else:
                        tone_mix = 0.62 - self._release_load_mix * 0.30
                        air_mix = 0.70 + self._release_load_mix * 0.65
                        voice = tone * tone_mix + airy * air_mix
                        # A medium-load release has a second, rounded pressure
                        # collapse: clearly "psh-ko", not a longer low POU and
                        # not yet a high-load flutter train.
                        secondary = math.exp(-((progress - 0.56) / 0.11) ** 2)
                        voice += (
                            tone * 0.58 + airy * 0.24
                        ) * secondary * (0.25 + self._release_depth * 0.75)
                    cancel_fade = min(1.0, self._flutter_remaining / max(1, int(0.025 * self.SAMPLE_RATE)))
                    flutter_left = voice * self._flutter_amplitude * envelope * cancel_fade
                    flutter_right = voice * self._flutter_amplitude * envelope * cancel_fade * 0.96
                    reference_left, reference_right = self._render_reference_release()
                    flutter_left += reference_left
                    flutter_right += reference_right
                    self._flutter_remaining -= 1
                    left = max(-1.0, min(1.0, engine_left + whine + flutter_left))
                    right = max(-1.0, min(1.0, engine_right + whine + flutter_right))
                    pcm.append(int(left * 32767))
                    pcm.append(int(right * 32767))
                    continue
                if self._flutter_mode in {"throttle_lift", "clutch"}:
                    if self._compressor_air is not None:
                        flutter_left, flutter_right = self._render_recorded_air_surge(progress)
                    else:
                        flutter_left, flutter_right = self._render_compressor_surge(progress)
                    reference_left, reference_right = self._render_reference_release()
                    flutter_left = flutter_left * 0.72 + reference_left
                    flutter_right = flutter_right * 0.72 + reference_right
                    self._flutter_remaining -= 1
                    left = max(-1.0, min(1.0, engine_left + whine + flutter_left))
                    right = max(-1.0, min(1.0, engine_right + whine + flutter_right))
                    pcm.append(int(left * 32767))
                    pcm.append(int(right * 32767))
                    continue
                pulse_rate = self._flutter_rate * (1.0 - 0.43 * progress)
                if self._flutter_mode != "valve_release" and not self._flutter_canceling:
                    self._flutter_phase += pulse_rate / self.SAMPLE_RATE
                    if self._flutter_phase >= 1.0:
                        self._flutter_phase -= 1.0
                        strength = self._flutter_amplitude * (1.0 - progress) ** 0.55
                        self._flutter_env_left = max(self._flutter_env_left, strength)
                        self._flutter_right_delay = self._flutter_delay_samples
                if self._flutter_right_delay > 0:
                    self._flutter_right_delay -= 1
                    if self._flutter_right_delay == 0:
                        self._flutter_env_right = max(self._flutter_env_right, self._flutter_env_left * 0.94)

                carrier = self._flutter_carrier_hz * (1.0 - 0.32 * progress)
                self._flutter_carrier_left += 2.0 * math.pi * carrier / self.SAMPLE_RATE
                self._flutter_carrier_right += 2.0 * math.pi * carrier * 0.975 / self.SAMPLE_RATE
                self._noise_seed = (1_664_525 * self._noise_seed + 1_013_904_223) & 0xFFFFFFFF
                raw_noise = self._noise_seed / 0xFFFFFFFF * 2.0 - 1.0
                self._noise_left = self._noise_left * 0.92 + raw_noise * 0.08
                self._noise_right = self._noise_right * 0.94 - raw_noise * 0.06
                tone_mix = 0.56 if self._flutter_mode == "valve_release" else 0.76
                flutter_left = self._flutter_env_left * (
                    tone_mix * math.sin(self._flutter_carrier_left) + (1.0 - tone_mix) * self._noise_left
                )
                flutter_right = self._flutter_env_right * (
                    tone_mix * math.sin(self._flutter_carrier_right) + (1.0 - tone_mix) * self._noise_right
                )
                envelope_decay = 0.965 if self._flutter_canceling else self._flutter_env_decay
                self._flutter_env_left *= envelope_decay
                self._flutter_env_right *= envelope_decay
                self._flutter_remaining -= 1

            left = max(-1.0, min(1.0, engine_left + whine + flutter_left))
            right = max(-1.0, min(1.0, engine_right + whine + flutter_right))
            pcm.append(int(left * 32767))
            pcm.append(int(right * 32767))

        self._current_rpm = end_rpm
        self._current_throttle = end_throttle
        self._current_boost = end_boost
        return pcm.tobytes()

    def _render_reference_spool(self, spool: float) -> float:
        """Blend measured acceleration grains from the supplied R34 video."""
        return self._render_spool_bank(
            self._reference_spool_bands,
            self._reference_spool_phases,
            spool,
            playback_rate=0.92 + spool * 0.20,
        )

    def _render_turbo_types_spool(self, spool: float) -> float:
        """Blend the clean tonal rise measured in the multi-turbo reference."""
        return self._render_spool_bank(
            self._turbo_types_spool_bands,
            self._turbo_types_spool_phases,
            spool,
            playback_rate=0.96 + spool * 0.12,
        )

    def _render_turbo_types_coast(self, spool: float) -> float:
        """Blend measured rundown grains while the compressor retains inertia."""
        return self._render_spool_bank(
            self._turbo_types_coast_bands,
            self._turbo_types_coast_phases,
            spool,
            playback_rate=0.95 + spool * 0.10,
        )

    @staticmethod
    def _render_spool_bank(
        bands: dict[int, array],
        phases: dict[int, float],
        spool: float,
        *,
        playback_rate: float,
    ) -> float:
        if not bands:
            return 0.0
        keys = sorted(bands)
        position = max(0.0, min(1.0, spool)) * (len(keys) - 1)
        lower_index = min(len(keys) - 1, int(position))
        upper_index = min(len(keys) - 1, lower_index + 1)
        blend = position - lower_index
        lower_weight = math.cos(blend * math.pi * 0.5)
        upper_weight = math.sin(blend * math.pi * 0.5)
        mixed = 0.0
        for index, weight in ((lower_index, lower_weight), (upper_index, upper_weight)):
            if weight <= 0.0:
                continue
            key = keys[index]
            loop = bands[key]
            frames = len(loop) // 2
            phase = phases[key]
            frame = int(phase) % frames
            next_frame = (frame + 1) % frames
            fraction = phase - int(phase)
            base = frame * 2
            next_base = next_frame * 2
            left = loop[base] * (1.0 - fraction) + loop[next_base] * fraction
            right = loop[base + 1] * (1.0 - fraction) + loop[next_base + 1] * fraction
            mixed += (left + right) * 0.5 / 32768.0 * weight
            phases[key] = (phase + playback_rate) % frames
        return mixed

    def _render_reference_release(self) -> tuple[float, float]:
        """Read the matching real lift-off event from the supplied video."""
        if self._reference_release_gain <= 0.0:
            return 0.0, 0.0
        if self._flutter_mode in {"throttle_lift", "clutch"} and self._reference_surge_pulses:
            return self._render_reference_surge_pulses()
        clip = self._reference_release_clips.get(self._flutter_mode)
        if clip is None and self._flutter_mode == "clutch":
            clip = self._reference_release_clips.get("throttle_lift")
        if clip is None:
            return 0.0, 0.0
        frames = len(clip) // 2
        frame = int(self._reference_release_position)
        if frame >= frames - 1:
            return 0.0, 0.0
        fraction = self._reference_release_position - frame
        base = frame * 2
        next_base = base + 2
        left = clip[base] * (1.0 - fraction) + clip[next_base] * fraction
        right = clip[base + 1] * (1.0 - fraction) + clip[next_base + 1] * fraction
        self._reference_release_position += self._reference_release_rate
        cancel_fade = 1.0
        if self._flutter_canceling:
            cancel_fade = min(1.0, self._flutter_remaining / max(1, int(0.025 * self.SAMPLE_RATE)))
        gain = self._reference_release_gain * cancel_fade / 32768.0
        return left * gain, right * gain

    def _trigger_reference_surge_pulse(self, pulse_number: int, strength: float) -> None:
        """Start one native-speed air catch from the measured pulse bank."""
        if not self._reference_surge_pulses:
            return
        index = min(pulse_number, len(self._reference_surge_pulses) - 1)
        pulse = self._reference_surge_pulses[index]
        # Tiny deterministic variation avoids a sampler-machine repetition,
        # while the strict bounds prevent the old compressed/helium effect.
        variation = ((pulse_number * 37) % 7 - 3) * 0.006
        rate = max(0.75, min(1.25, self._reference_release_rate + variation))
        gain = self._reference_release_gain * strength
        self._reference_active_pulses.append((pulse, 0.0, rate, gain))

    def _render_reference_surge_pulses(self) -> tuple[float, float]:
        left = right = 0.0
        still_active: list[tuple[array, float, float, float]] = []
        cancel_fade = 1.0
        if self._flutter_canceling:
            cancel_fade = min(1.0, self._flutter_remaining / max(1, int(0.025 * self.SAMPLE_RATE)))
        progress = 1.0 - self._flutter_remaining / max(1, self._flutter_total)
        pressure_tail = max(0.0, min(1.0, (1.0 - progress) / 0.20))
        pressure_tail = pressure_tail * pressure_tail * (3.0 - 2.0 * pressure_tail)
        for pulse, position, rate, gain in self._reference_active_pulses:
            frames = len(pulse) // 2
            frame = int(position)
            if frame >= frames - 1:
                continue
            fraction = position - frame
            base = frame * 2
            next_base = base + 2
            left += (
                pulse[base] * (1.0 - fraction) + pulse[next_base] * fraction
            ) / 32768.0 * gain * cancel_fade * pressure_tail
            right += (
                pulse[base + 1] * (1.0 - fraction) + pulse[next_base + 1] * fraction
            ) / 32768.0 * gain * cancel_fade * pressure_tail
            next_position = position + rate
            if next_position < frames - 1:
                still_active.append((pulse, next_position, rate, gain))
        self._reference_active_pulses = still_active
        return left, right

    def _render_recorded_air_surge(self, progress: float) -> tuple[float, float]:
        """Render a forceful, decelerating compressor-surge pulse train.

        A real air recording supplies the turbulence.  A damped intake-path
        resonance adds the rounded ``ko/po`` body, while a brighter recording
        is strongest only on the first ``shu``.  Nothing is hard-switched, so
        repeated pulses remain air movements rather than metallic impacts.
        """
        assert self._compressor_air is not None
        if not self._flutter_canceling:
            measured_pulse_count = min(
                len(self._reference_surge_pulses), self._surge_measured_pulse_limit,
            )
            # Four low-level modeled catches continue after the twelve unique
            # recorded grains. They fade the rhythm itself without looping a
            # recording (which previously became a mechanical "ga-ga-ga").
            maximum_pulses = (
                measured_pulse_count + self._surge_modeled_tail_count
                if measured_pulse_count
                else 8 + self._surge_modeled_tail_count
            )
            source_pulses_available = self._surge_pulse_index < maximum_pulses
            pressure_can_recatch = progress < 0.92
            if self._surge_pulse_wait <= 0 and source_pulses_available and pressure_can_recatch:
                pulse_number = self._surge_pulse_index
                pressure_strength = (1.0 - progress) ** 0.10
                if pulse_number < measured_pulse_count:
                    pulse_fraction = pulse_number / max(1, measured_pulse_count + 5)
                    strength = (1.0 - pulse_fraction) ** 0.72 * pressure_strength
                    self._surge_tail_output_scale = 1.0
                else:
                    tail_profiles = {
                        1: ((0.035,), (0.10,)),
                        2: ((0.18, 0.04), (0.72, 0.10)),
                        3: ((0.24, 0.10, 0.035), (0.82, 0.48, 0.10)),
                        4: ((0.30, 0.20, 0.12, 0.06), (0.90, 0.75, 0.55, 0.08)),
                    }
                    modeled_tail, modeled_output = tail_profiles[self._surge_modeled_tail_count]
                    tail_index = min(pulse_number - measured_pulse_count, len(modeled_tail) - 1)
                    strength = modeled_tail[tail_index] * pressure_strength
                    self._surge_tail_output_scale = modeled_output[tail_index]
                self._surge_pulse_strength = strength
                self._surge_pulse_age = 0
                # Pressure and wheel speed fall after every re-catch.  This
                # downward body sweep is what turns a flat "sh-sh-sh" into
                # the heavier "shu-KO-ko-po-po" heard from an open intake.
                self._surge_body_frequency = max(
                    360.0,
                    self._flutter_carrier_hz * 0.30 * (0.94 ** pulse_number),
                )
                if pulse_number < measured_pulse_count and self._release_depth >= 0.25:
                    self._trigger_reference_surge_pulse(pulse_number, strength)
                self._recorded_flutter_pulses += 1
                # Measured from the supplied R34 reference: a flutter event
                # is not metronomic. It starts with a few close catches, then
                # leaves irregular, widening gaps as the compressor slows.
                # Peak-to-peak gaps measured from the supplied R34 clip.  The
                # early catches are quick and uneven (not a rattle). Keep the
                # widest interval below the point where it is perceived as a
                # stopped sample followed by a restart.
                reference_gaps = (
                    0.060, 0.075, 0.070, 0.080, 0.065, 0.075, 0.080,
                    0.085, 0.085, 0.090, 0.095,
                )
                gap = reference_gaps[min(self._surge_pulse_index, len(reference_gaps) - 1)]
                self._surge_pulse_index += 1
                self._surge_pulse_wait = max(1, round(gap * self._surge_pulse_scale * self.SAMPLE_RATE))
            elif self._surge_pulse_wait > 0:
                self._surge_pulse_wait -= 1

        source_frames = len(self._compressor_air) // 2
        frame = int(self._compressor_air_position) % source_frames
        next_frame = (frame + 1) % source_frames
        fraction = self._compressor_air_position - frame
        base = frame * 2
        next_base = next_frame * 2
        sample_left = self._compressor_air[base] * (1.0 - fraction) + self._compressor_air[next_base] * fraction
        sample_right = self._compressor_air[base + 1] * (1.0 - fraction) + self._compressor_air[next_base + 1] * fraction
        hiss_left = sample_left
        hiss_right = sample_right
        if self._compressor_air_hiss is not None:
            hiss_frames = len(self._compressor_air_hiss) // 2
            hiss_frame = frame % hiss_frames
            hiss_next = (hiss_frame + 1) % hiss_frames
            hiss_base = hiss_frame * 2
            hiss_next_base = hiss_next * 2
            hiss_left = (
                self._compressor_air_hiss[hiss_base] * (1.0 - fraction)
                + self._compressor_air_hiss[hiss_next_base] * fraction
            )
            hiss_right = (
                self._compressor_air_hiss[hiss_base + 1] * (1.0 - fraction)
                + self._compressor_air_hiss[hiss_next_base + 1] * fraction
            )
        self._compressor_air_position = (self._compressor_air_position + self._compressor_air_rate) % source_frames

        age_seconds = self._surge_pulse_age / self.SAMPLE_RATE
        partial_softness = 1.0 - self._release_depth
        attack_seconds = (
            (0.0023 if self._surge_pulse_index <= 1 else 0.0034)
            + partial_softness * 0.0065
        )
        decay_seconds = 0.034 - min(0.010, max(0, self._surge_pulse_index - 1) * 0.0012)
        pulse_envelope = (
            (1.0 - math.exp(-age_seconds / attack_seconds))
            * math.exp(-age_seconds / decay_seconds)
            * self._surge_pulse_strength
        )
        self._surge_pulse_age += 1

        # Low-Q state-variable resonator: turbulent air excites the intake
        # volume, producing a rounded body without introducing a pure tone.
        resonator_f = min(0.16, 2.0 * math.sin(math.pi * self._surge_body_frequency / self.SAMPLE_RATE))
        resonance_damping = 0.42
        body_input_left = sample_left / 32768.0
        body_input_right = sample_right / 32768.0
        self._surge_body_left[0] += resonator_f * self._surge_body_left[1]
        high_left = body_input_left - self._surge_body_left[0] - resonance_damping * self._surge_body_left[1]
        self._surge_body_left[1] += resonator_f * high_left
        self._surge_body_right[0] += resonator_f * self._surge_body_right[1]
        high_right = body_input_right - self._surge_body_right[0] - resonance_damping * self._surge_body_right[1]
        self._surge_body_right[1] += resonator_f * high_right

        # Smooth the bright layer separately.  The first release is a broad
        # "shu"; following catches rapidly become body-heavy "ko/po" pulses.
        hiss_smoothing = 0.24
        self._surge_hiss_left += hiss_smoothing * (hiss_left / 32768.0 - self._surge_hiss_left)
        self._surge_hiss_right += hiss_smoothing * (hiss_right / 32768.0 - self._surge_hiss_right)
        first_release = 1.0 if self._surge_pulse_index <= 1 else 0.0
        transient_scale = 0.22 + self._release_depth * 0.78
        hiss_mix = (
            0.22
            + first_release * 0.95 * transient_scale
            + max(0.0, 0.22 - progress) * 0.55
        )
        body_mix = 2.75 + progress * 0.95
        pressure_front = 1.05 + 4.8 * transient_scale * math.exp(-age_seconds / 0.012)
        fade = min(1.0, self._flutter_remaining / max(1, int(0.035 * self.SAMPLE_RATE)))
        pressure_tail = max(0.0, min(1.0, (1.0 - progress) / 0.20))
        pressure_tail = pressure_tail * pressure_tail * (3.0 - 2.0 * pressure_tail)
        # The surge sits on top of a loud recorded engine loop.  Keep enough
        # headroom for the final mix, but make the air catches clearly audible
        # rather than disappearing behind the engine on throttle lift.
        gain = (
            self._flutter_amplitude
            * 2.58
            * pulse_envelope
            * fade
            * pressure_tail
            * self._surge_tail_output_scale
        )
        if self._release_depth < 0.25:
            # With recorded transients disabled, restore only the smooth air
            # body so a shallow lift remains audible over the engine bed.
            gain *= 1.0 + (0.25 - self._release_depth) / 0.25 * 1.8
        left_voice = (
            self._surge_hiss_left * hiss_mix
            + self._surge_body_left[1] * body_mix
            + self._surge_body_left[0] * pressure_front
        )
        right_voice = (
            self._surge_hiss_right * hiss_mix
            + self._surge_body_right[1] * body_mix
            + self._surge_body_right[0] * pressure_front
        )
        return (
            left_voice * gain,
            right_voice * gain * 0.97,
        )

    def _render_compressor_surge(self, progress: float) -> tuple[float, float]:
        """Render one physical air-catch sample without the old flutter recording."""
        pulse_rate = self._flutter_rate * (1.0 - 0.34 * progress)
        if not self._flutter_canceling:
            self._flutter_phase += pulse_rate / self.SAMPLE_RATE
            if self._flutter_phase >= 1.0:
                self._flutter_phase -= 1.0
                strength = (1.0 - progress) ** 0.46
                self._flutter_env_left = max(self._flutter_env_left, strength)
                self._flutter_env_right = max(self._flutter_env_right, strength * 0.94)
                self._recorded_flutter_pulses += 1

        envelope_decay = 0.955 if self._flutter_canceling else self._flutter_env_decay
        self._flutter_env_left *= envelope_decay
        self._flutter_env_right *= envelope_decay
        attack = 1.0 - math.exp(-1.0 / (self.SAMPLE_RATE * 0.015))
        self._recorded_flutter_gate_left += attack * (
            self._flutter_env_left - self._recorded_flutter_gate_left
        )
        self._recorded_flutter_gate_right += attack * (
            self._flutter_env_right - self._recorded_flutter_gate_right
        )

        self._noise_seed = (1_664_525 * self._noise_seed + 1_013_904_223) & 0xFFFFFFFF
        raw_left = self._noise_seed / 0xFFFFFFFF * 2.0 - 1.0
        self._noise_seed = (1_664_525 * self._noise_seed + 1_013_904_223) & 0xFFFFFFFF
        raw_right = self._noise_seed / 0xFFFFFFFF * 2.0 - 1.0
        noise_highpass = 1.0 - math.exp(-2.0 * math.pi * 360.0 / self.SAMPLE_RATE)
        noise_lowpass = 1.0 - math.exp(-2.0 * math.pi * 2_800.0 / self.SAMPLE_RATE)
        self._surge_noise_low_left += noise_highpass * (raw_left - self._surge_noise_low_left)
        self._surge_noise_low_right += noise_highpass * (raw_right - self._surge_noise_low_right)
        self._surge_noise_high_left += noise_lowpass * (
            (raw_left - self._surge_noise_low_left) - self._surge_noise_high_left
        )
        self._surge_noise_high_right += noise_lowpass * (
            (raw_right - self._surge_noise_low_right) - self._surge_noise_high_right
        )
        self._surge_noise_high_second_left += noise_lowpass * (
            self._surge_noise_high_left - self._surge_noise_high_second_left
        )
        self._surge_noise_high_second_right += noise_lowpass * (
            self._surge_noise_high_right - self._surge_noise_high_second_right
        )

        fade = min(1.0, self._flutter_remaining / max(1, int(0.030 * self.SAMPLE_RATE)))
        # Two low-pass stages turn white noise into a continuous rush of air.
        # Deliberately avoid saturation here: it creates the brittle "bachi"
        # transient that does not exist in a pressure-release sound.
        amplitude = self._flutter_amplitude * 1.65 * fade
        left = self._surge_noise_high_second_left * amplitude * self._recorded_flutter_gate_left
        right = self._surge_noise_high_second_right * amplitude * self._recorded_flutter_gate_right
        return left, right

    def _render_recorded_engine(self, rpm: float, throttle: float) -> tuple[float, float]:
        """Pitch and blend neighboring steady slices from a real GT-86 dyno pull."""
        if not self._recorded_engine_bands:
            # A missing asset must not bring the toy-like oscillator back.
            return 0.0, 0.0

        bands = self.RECORDED_ENGINE_BANDS
        clamped = max(float(bands[0]), min(float(bands[-1]), rpm))
        upper_index = next((i for i, band in enumerate(bands) if band >= clamped), len(bands) - 1)
        lower_index = max(0, upper_index - 1)
        lower_band = bands[lower_index]
        upper_band = bands[upper_index]
        blend = 0.0 if lower_band == upper_band else (clamped - lower_band) / (upper_band - lower_band)
        lower_weight = math.cos(blend * math.pi * 0.5)
        upper_weight = math.sin(blend * math.pi * 0.5)

        left = right = 0.0
        for band, weight in ((lower_band, lower_weight), (upper_band, upper_weight)):
            if weight <= 0.0:
                continue
            loop = self._recorded_engine_bands[band]
            source_frames = len(loop) // 2
            phase = self._recorded_engine_phases[band]
            frame = int(phase) % source_frames
            next_frame = (frame + 1) % source_frames
            fraction = phase - int(phase)
            base = frame * 2
            next_base = next_frame * 2
            left += (loop[base] * (1.0 - fraction) + loop[next_base] * fraction) * weight
            right += (loop[base + 1] * (1.0 - fraction) + loop[next_base + 1] * fraction) * weight
            playback_rate = max(0.58, min(1.35, rpm / band))
            self._recorded_engine_phases[band] = (phase + playback_rate) % source_frames

        # The recording already contains intake, exhaust and mechanical texture.
        # Throttle changes level only; synthesizing another "load tone" is what
        # made the previous version sound like a toy.
        gain = 0.075 + max(0.0, min(1.0, throttle)) * 0.31
        return left / 32768.0 * gain, right / 32768.0 * gain

    def _load_recorded_samples(self) -> None:
        if miniaudio is None:
            return
        engine_path = self._assets / "gt86_dyno.mp3"
        try:
            if engine_path.exists():
                decoded_engine = miniaudio.decode_file(
                    str(engine_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                # The documented 3000 -> 7200 rpm fourth-gear pull occupies
                # approximately 29.4 -> 36.0 seconds in this recording.
                for rpm in self.RECORDED_ENGINE_BANDS:
                    center_seconds = 29.4 + (rpm - 3_000) / 4_200.0 * 6.6
                    loop = self._extract_engine_loop(decoded_engine.samples, center_seconds, rpm)
                    if loop:
                        self._recorded_engine_bands[rpm] = loop
                        self._recorded_engine_phases[rpm] = 0.0
        except Exception:
            self._recorded_engine_bands.clear()
            self._recorded_engine_phases.clear()

        compressor_air_path = self._assets / "compressor_air.mp3"
        try:
            if compressor_air_path.exists():
                decoded_air = miniaudio.decode_file(
                    str(compressor_air_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                # Use only the stationary part of the air rush.  Including
                # the recording's own long fade made our later modeled surge
                # catches disappear (two independent decays multiplied).
                # The opening valve and closing mechanism remain outside.
                start = int(0.88 * self.SAMPLE_RATE) * 2
                end = int(1.32 * self.SAMPLE_RATE) * 2
                stable_air = array("h", decoded_air.samples[start:end])
                self._compressor_air = self._prepare_compressor_air(
                    stable_air, highpass_hz=240.0, lowpass_hz=1_900.0, gain=4.3,
                )
                self._compressor_air_hiss = self._prepare_compressor_air(
                    stable_air, highpass_hz=900.0, lowpass_hz=6_200.0, gain=2.8,
                )
        except Exception:
            self._compressor_air = None
            self._compressor_air_hiss = None

        reference_path = self._assets / "user_r34_reference.mp3"
        try:
            if reference_path.exists():
                decoded_reference = miniaudio.decode_file(
                    str(reference_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                reference = decoded_reference.samples
                spool_start_seconds = 5.95
                spool_end_seconds = 7.90
                spool_start = int(spool_start_seconds * self.SAMPLE_RATE) * 2
                spool_end = int(spool_end_seconds * self.SAMPLE_RATE) * 2
                spool_source = self._prepare_compressor_air(
                    array("h", reference[spool_start:spool_end]),
                    highpass_hz=900.0, lowpass_hz=6_200.0, gain=1.35,
                )
                # Clean acceleration between the third and fourth lift events.
                # Multiple grains preserve the measured rising spectral bundle
                # without playing the source as a one-shot recording.
                for index, center in enumerate((6.18, 6.48, 6.78, 7.08, 7.38, 7.68)):
                    loop = self._extract_reference_loop(
                        spool_source, center - spool_start_seconds, 0.22,
                    )
                    if loop:
                        self._reference_spool_bands[index] = loop
                        self._reference_spool_phases[index] = 0.0

                release_ranges = {
                    # Brief lift: its short clip is cut off naturally if the
                    # driver immediately reapplies throttle.
                    "valve_release": (5.38, 5.72),
                    "pressure_release": (7.98, 8.70),
                }
                for reason, (start_seconds, end_seconds) in release_ranges.items():
                    start = int(start_seconds * self.SAMPLE_RATE) * 2
                    end = int(end_seconds * self.SAMPLE_RATE) * 2
                    self._reference_release_clips[reason] = self._prepare_compressor_air(
                        array("h", reference[start:end]),
                        highpass_hz=520.0, lowpass_hz=7_200.0, gain=1.18,
                    )

                # Split the final long surge into native-speed air catches.
                # Scheduling these grains independently preserves pitch and
                # transient length at every simulated boost level.
                surge_start_seconds = 9.35
                surge_end_seconds = 10.90
                surge_start = int(surge_start_seconds * self.SAMPLE_RATE) * 2
                surge_end = int(surge_end_seconds * self.SAMPLE_RATE) * 2
                surge_source = self._prepare_compressor_air(
                    array("h", reference[surge_start:surge_end]),
                    highpass_hz=650.0, lowpass_hz=7_200.0, gain=1.35,
                )
                pulse_centers = (
                    0.035, 0.095, 0.190, 0.310, 0.490, 0.650,
                    0.760, 0.940, 1.100, 1.240, 1.360, 1.490,
                )
                for center in pulse_centers:
                    pulse = self._extract_reference_pulse(surge_source, center)
                    if pulse:
                        self._reference_surge_pulses.append(pulse)
        except Exception:
            self._reference_spool_bands.clear()
            self._reference_spool_phases.clear()
            self._reference_release_clips.clear()
            self._reference_surge_pulses.clear()

        turbo_types_path = self._assets / "turbo_intro_accel_reference.wav"
        try:
            if turbo_types_path.exists():
                decoded_types = miniaudio.decode_file(
                    str(turbo_types_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                tonal_source = self._prepare_compressor_air(
                    array("h", decoded_types.samples),
                    highpass_hz=1_250.0, lowpass_hz=9_200.0, gain=1.55,
                )
                # The first example rises quickly after initial lag. Shorter
                # grains preserve that rapid whistle sweep without smearing
                # adjacent pitch regions together.
                for index, center in enumerate((0.10, 0.32, 0.54, 0.76, 0.98, 1.20, 1.42, 1.64)):
                    loop = self._extract_reference_loop(tonal_source, center, 0.13)
                    if loop:
                        self._turbo_types_spool_bands[index] = loop
                        self._turbo_types_spool_phases[index] = 0.0
        except Exception:
            self._turbo_types_spool_bands.clear()
            self._turbo_types_spool_phases.clear()

        turbo_coast_path = self._assets / "turbo_intro_coast_reference.wav"
        try:
            if turbo_coast_path.exists():
                decoded_coast = miniaudio.decode_file(
                    str(turbo_coast_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                coast_source = self._prepare_compressor_air(
                    array("h", decoded_coast.samples),
                    highpass_hz=1_250.0, lowpass_hz=9_200.0, gain=1.55,
                )
                # Source time runs high-to-low speed; reverse the center list
                # so increasing dictionary index still means increasing spool.
                for index, center in enumerate((0.68, 0.58, 0.48, 0.38, 0.28, 0.18, 0.08)):
                    loop = self._extract_reference_loop(coast_source, center, 0.11)
                    if loop:
                        self._turbo_types_coast_bands[index] = loop
                        self._turbo_types_coast_phases[index] = 0.0
        except Exception:
            self._turbo_types_coast_bands.clear()
            self._turbo_types_coast_phases.clear()

        flutter_path = self._assets / "turbo_flutter.mp3"
        try:
            if flutter_path.exists():
                decoded = miniaudio.decode_file(
                    str(flutter_path), output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=2, sample_rate=self.SAMPLE_RATE,
                )
                samples = decoded.samples
                onset = 0
                for frame in range(len(samples) // 2):
                    base = frame * 2
                    if max(abs(samples[base]), abs(samples[base + 1])) > 240:
                        onset = max(0, frame - int(0.004 * self.SAMPLE_RATE)) * 2
                        break
                source = array("h", samples[onset:])
                # Isolate compressor air. Retaining a portion of the original
                # low band made its mechanical texture repeat as "gara-gara".
                low_left = low_right = 0.0
                low_second_left = low_second_right = 0.0
                air_left = air_right = 0.0
                highpassed = array("h")
                highpass_alpha = 1.0 - math.exp(-2.0 * math.pi * 1_050.0 / self.SAMPLE_RATE)
                air_lowpass_alpha = 1.0 - math.exp(-2.0 * math.pi * 8_500.0 / self.SAMPLE_RATE)
                for index in range(0, len(source), 2):
                    left = source[index]
                    right = source[index + 1]
                    low_left += highpass_alpha * (left - low_left)
                    low_right += highpass_alpha * (right - low_right)
                    first_left = left - low_left
                    first_right = right - low_right
                    low_second_left += highpass_alpha * (first_left - low_second_left)
                    low_second_right += highpass_alpha * (first_right - low_second_right)
                    second_left = first_left - low_second_left
                    second_right = first_right - low_second_right
                    air_left += air_lowpass_alpha * (second_left - air_left)
                    air_right += air_lowpass_alpha * (second_right - air_right)
                    highpassed.append(round(max(-30_000.0, min(30_000.0, air_left * 2.20))))
                    highpassed.append(round(max(-30_000.0, min(30_000.0, air_right * 2.20))))
                self._recorded_flutter = highpassed
        except Exception:
            self._recorded_flutter = None

    @classmethod
    def _prepare_compressor_air(
        cls,
        source: array,
        *,
        highpass_hz: float = 240.0,
        lowpass_hz: float = 1_900.0,
        gain: float = 4.3,
    ) -> array:
        """Keep the recorded air texture while removing valve/mechanical bite.

        The supplied R34 reference has most of its flutter energy around
        1.6 kHz.  The unprocessed CC0 air release extends strongly past 6 kHz,
        which reads as a metallic click once it is chopped into pulses.  A
        gentle speech-band filter keeps it recognisably *air*, not a synth or
        a sampled valve mechanism.
        """
        highpass_state = [0.0, 0.0]
        lowpass_first = [0.0, 0.0]
        lowpass_second = [0.0, 0.0]
        highpass_alpha = 1.0 - math.exp(-2.0 * math.pi * highpass_hz / cls.SAMPLE_RATE)
        lowpass_alpha = 1.0 - math.exp(-2.0 * math.pi * lowpass_hz / cls.SAMPLE_RATE)
        filtered = array("h")
        for index in range(0, len(source), 2):
            for channel, sample in enumerate((source[index], source[index + 1])):
                highpass_state[channel] += highpass_alpha * (sample - highpass_state[channel])
                air = sample - highpass_state[channel]
                lowpass_first[channel] += lowpass_alpha * (air - lowpass_first[channel])
                lowpass_second[channel] += lowpass_alpha * (
                    lowpass_first[channel] - lowpass_second[channel]
                )
                # Filtering costs level; restore it linearly, without a clipper
                # or saturation stage that would create the unwanted "bachi".
                filtered.append(round(max(-30_000.0, min(30_000.0, lowpass_second[channel] * gain))))
        return filtered

    @classmethod
    def _extract_reference_loop(cls, source: array, center_seconds: float, duration_seconds: float) -> array:
        """Extract a short acceleration grain with a soft loop boundary."""
        source_frames = len(source) // 2
        frames = max(256, int(duration_seconds * cls.SAMPLE_RATE))
        center = int(center_seconds * cls.SAMPLE_RATE)
        start = max(0, min(source_frames - frames, center - frames // 2))
        loop = array("h", source[start * 2:(start + frames) * 2])
        fade_frames = min(int(0.022 * cls.SAMPLE_RATE), frames // 4)
        for offset in range(fade_frames):
            blend = (offset + 1) / fade_frames
            tail_frame = frames - fade_frames + offset
            for channel in (0, 1):
                tail_index = tail_frame * 2 + channel
                head_index = offset * 2 + channel
                value = loop[tail_index] * (1.0 - blend) + loop[head_index] * blend
                loop[tail_index] = round(max(-32_768.0, min(32_767.0, value)))
        return loop

    @classmethod
    def _extract_reference_pulse(cls, source: array, center_seconds: float) -> array:
        """Cut one measured air catch with click-free edges."""
        source_frames = len(source) // 2
        before_frames = int(0.014 * cls.SAMPLE_RATE)
        pulse_frames = int(0.084 * cls.SAMPLE_RATE)
        center = int(center_seconds * cls.SAMPLE_RATE)
        start = max(0, min(source_frames - pulse_frames, center - before_frames))
        pulse = array("h", source[start * 2:(start + pulse_frames) * 2])
        fade_frames = min(int(0.006 * cls.SAMPLE_RATE), pulse_frames // 4)
        for frame in range(fade_frames):
            fade_in = (frame + 1) / fade_frames
            fade_out = (fade_frames - frame - 1) / fade_frames
            tail_frame = pulse_frames - fade_frames + frame
            for channel in (0, 1):
                head_index = frame * 2 + channel
                tail_index = tail_frame * 2 + channel
                pulse[head_index] = round(pulse[head_index] * fade_in)
                pulse[tail_index] = round(pulse[tail_index] * fade_out)
        return pulse

    @classmethod
    def _extract_engine_loop(cls, source: array, center_seconds: float, rpm: int) -> array:
        """Make a short, cycle-length loop with a click-free overlap seam."""
        source_frames = len(source) // 2
        firing_period = cls.SAMPLE_RATE * 30.0 / rpm
        cycles = max(8, round(0.12 * rpm / 30.0))
        loop_frames = max(256, round(cycles * firing_period))
        fade_frames = min(round(0.012 * cls.SAMPLE_RATE), loop_frames // 3)
        center = round(center_seconds * cls.SAMPLE_RATE)
        start = max(0, min(source_frames - loop_frames - fade_frames, center - loop_frames // 2))
        raw = source[start * 2:(start + loop_frames + fade_frames) * 2]
        if len(raw) < (loop_frames + fade_frames) * 2:
            return array("h")

        result = array("h", raw[:loop_frames * 2])
        for frame in range(fade_frames):
            mix = frame / max(1, fade_frames - 1)
            for channel in range(2):
                head_index = frame * 2 + channel
                tail_index = (loop_frames + frame) * 2 + channel
                result[head_index] = round(raw[tail_index] * (1.0 - mix) + raw[head_index] * mix)

        # Dyno distance changes considerably during the pull. Normalize each
        # RPM slice so moving between bands does not sound like volume pumping.
        dc = [sum(result[channel::2]) / loop_frames for channel in range(2)]
        centered = [result[i] - dc[i % 2] for i in range(len(result))]
        rms = math.sqrt(sum(value * value for value in centered) / max(1, len(centered)))
        peak = max(1.0, max(abs(value) for value in centered))
        normalize = min(5.0, 5_600.0 / max(1.0, rms), 30_000.0 / peak)
        for index, value in enumerate(centered):
            result[index] = round(value * normalize)
        return result

    def _generate_fallback_samples(self) -> None:
        if self._surge_samples and self._engine_samples:
            return
        for level in range(7):
            path = Path(self._directory.name) / f"flutter-{level}.wav"
            self._write_flutter_sample(path, level / 6, seed=8600 + level)
            self._surge_samples.append(path)
        for rpm in self.ENGINE_RPM_BANDS:
            path = Path(self._directory.name) / f"engine-{rpm}.wav"
            self._write_engine_sample(path, rpm)
            self._engine_samples[rpm] = path

    def _start_fallback(self, path: Path, volume: float, rate: float = 1.0) -> subprocess.Popen[bytes]:
        self._players = [process for process in self._players if process.poll() is None]
        process = subprocess.Popen(
            [self.player, "-v", f"{volume:.2f}", "-r", f"{rate:.2f}", "-q", "1", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._players.append(process)
        return process

    def close(self) -> None:
        self._stream_stop.set()
        if self._audio_device is not None:
            self._audio_device.stop()
            self._audio_device.close()
            self._audio_device = None
        process = self._stream_process
        if self._stream_thread is not None and self._stream_thread is not threading.current_thread():
            self._stream_thread.join(timeout=1.0)
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.4)
                except subprocess.TimeoutExpired:
                    process.kill()
        for player in self._players:
            if player.poll() is None:
                player.terminate()
        self._players.clear()
        self._directory.cleanup()

    @classmethod
    def _write_flutter_sample(cls, path: Path, intensity: float, seed: int) -> None:
        rng = random.Random(seed)
        pulse_count = 2 + round(intensity * 8)
        duration = 0.12 + intensity * 0.70
        samples: list[tuple[int, int]] = []
        for index in range(int(duration * cls.SAMPLE_RATE)):
            t = index / cls.SAMPLE_RATE
            progress = t / duration
            left = right = 0.0
            for pulse in range(pulse_count):
                center = 0.018 + (pulse / max(1, pulse_count - 1)) ** 1.18 * duration * 0.78
                local = t - center
                envelope = math.exp(-((local / (0.008 + intensity * 0.006)) ** 2))
                frequency = (410.0 + intensity * 120.0) * (1.0 - progress * 0.28)
                noise = rng.uniform(-1.0, 1.0) * 0.12
                left += envelope * (math.sin(2 * math.pi * frequency * local) * 0.88 + noise)
                delayed = local - 0.0048
                right += math.exp(-((delayed / (0.008 + intensity * 0.006)) ** 2)) * (
                    math.sin(2 * math.pi * frequency * 0.98 * delayed) * 0.88 + noise
                )
            amplitude = 0.12 + intensity * 0.46
            samples.append((int(math.tanh(left) * amplitude * 32767), int(math.tanh(right) * amplitude * 32767)))
        cls._write_wav(path, samples)

    @classmethod
    def _write_engine_sample(cls, path: Path, rpm: int) -> None:
        firing_hz = rpm / 30.0
        cycles = max(1, round(firing_hz * 0.32))
        duration = cycles / firing_hz
        samples: list[int] = []
        for index in range(int(duration * cls.SAMPLE_RATE)):
            t = index / cls.SAMPLE_RATE
            phase = 2.0 * math.pi * firing_hz * t
            value = 0.52 * math.sin(phase) + 0.25 * math.sin(phase * 2 + 0.31) + 0.13 * math.sin(phase * 3)
            fade = min(1.0, t / 0.025, (duration - t) / 0.025)
            samples.append(int(math.tanh(value * 1.18) * 0.56 * fade * 32767))
        cls._write_wav(path, samples)

    @classmethod
    def _write_wav(cls, path: Path, samples: list[int] | list[tuple[int, int]]) -> None:
        with wave.open(str(path), "wb") as output:
            stereo = bool(samples and isinstance(samples[0], tuple))
            output.setnchannels(2 if stereo else 1)
            output.setsampwidth(2)
            output.setframerate(cls.SAMPLE_RATE)
            if stereo:
                output.writeframes(b"".join(struct.pack("<hh", left, right) for left, right in samples))
            else:
                output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
