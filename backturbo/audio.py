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
        self._target_rpm = 850.0
        self._target_throttle = 0.0
        self._target_boost = -0.65
        self._current_rpm = 850.0
        self._current_throttle = 0.0
        self._current_boost = -0.65
        self._pending_flutter: SurgeEvent | None = None
        self._engine_phase = 0.0
        self._whine_phase = 0.0
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
        if event.reason == "valve_release":
            duration = 0.075 + 0.090 * boost + 0.055 * drop
            amplitude = 0.055 + 0.24 * boost + 0.10 * drop
            carrier_hz = 155.0 + 75.0 * rpm + 35.0 * boost
            return duration, 0.0, amplitude, carrier_hz
        if event.reason == "pressure_release":
            duration = 0.16 + 0.18 * boost + 0.08 * drop
            amplitude = 0.10 + 0.34 * boost + 0.12 * drop
            carrier_hz = 205.0 + 90.0 * rpm + 55.0 * boost
            return duration, 0.0, amplitude, carrier_hz
        duration = 0.10 + 0.52 * boost + 0.10 * rpm + 0.12 * drop
        pulse_rate = 8.0 + 7.5 * rpm + 4.0 * lift + 2.5 * boost
        amplitude = 0.035 + 0.38 * boost + 0.13 * drop
        carrier_hz = 245.0 + 205.0 * rpm + 75.0 * boost
        return duration, pulse_rate, amplitude, carrier_hz

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
        self._flutter_total = max(1, int(duration * self.SAMPLE_RATE))
        self._flutter_remaining = self._flutter_total
        self._flutter_rate = pulse_rate
        self._flutter_amplitude = amplitude
        self._flutter_carrier_hz = carrier_hz
        self._flutter_mode = event.reason
        boost_part = max(0.0, min(1.0, event.boost_bar / 0.85))
        self._recorded_flutter_position = 0.0
        self._recorded_flutter_rate = 1.28 - boost_part * 0.34
        self._recorded_flutter_gain = 0.30 + amplitude * 1.55
        self._recorded_flutter_pulses = 0
        self._release_air = 0.0
        self._release_air_previous = 0.0
        self._release_tone_phase = 0.0
        self._flutter_canceling = False
        rpm_part = max(0.0, min(1.0, (event.rpm - 1_800.0) / 5_400.0))
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
            pulse_width = 0.018 + event.intensity * 0.012
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
            if self.engine_enabled and boost > -0.15:
                whine_hz = 430.0 + rpm * 0.16 + max(0.0, boost) * 520.0
                self._whine_phase = (self._whine_phase + 2.0 * math.pi * whine_hz / self.SAMPLE_RATE) % (2.0 * math.pi)
                whine = math.sin(self._whine_phase) * (0.008 + max(0.0, boost) * 0.022)

            flutter_left = 0.0
            flutter_right = 0.0
            if self.enabled and self._flutter_remaining > 0:
                progress = 1.0 - self._flutter_remaining / self._flutter_total
                if self._flutter_mode in {"valve_release", "pressure_release"}:
                    # Low charge: a short plosive "pou". Medium charge: a
                    # broader filtered-air "pssh". These are pressure-release
                    # voices, not truncated compressor-flutter samples.
                    attack = min(1.0, progress * (55.0 if self._flutter_mode == "valve_release" else 32.0))
                    tail_power = 2.4 if self._flutter_mode == "valve_release" else 1.35
                    envelope = attack * max(0.0, 1.0 - progress) ** tail_power
                    self._noise_seed = (1_664_525 * self._noise_seed + 1_013_904_223) & 0xFFFFFFFF
                    raw_noise = self._noise_seed / 0xFFFFFFFF * 2.0 - 1.0
                    air_alpha = 0.18 if self._flutter_mode == "valve_release" else 0.31
                    self._release_air += air_alpha * (raw_noise - self._release_air)
                    airy = self._release_air - self._release_air_previous * 0.72
                    self._release_air_previous = self._release_air
                    tone_hz = self._flutter_carrier_hz * (1.0 - 0.48 * progress)
                    self._release_tone_phase += 2.0 * math.pi * tone_hz / self.SAMPLE_RATE
                    tone = math.sin(self._release_tone_phase)
                    if self._flutter_mode == "valve_release":
                        voice = tone * 0.64 + airy * 0.82
                    else:
                        voice = tone * 0.18 + airy * 1.42
                    cancel_fade = min(1.0, self._flutter_remaining / max(1, int(0.025 * self.SAMPLE_RATE)))
                    flutter_left = voice * self._flutter_amplitude * envelope * cancel_fade
                    flutter_right = voice * self._flutter_amplitude * envelope * cancel_fade * 0.96
                    self._flutter_remaining -= 1
                    left = max(-1.0, min(1.0, engine_left + whine + flutter_left))
                    right = max(-1.0, min(1.0, engine_right + whine + flutter_right))
                    pcm.append(int(left * 32767))
                    pcm.append(int(right * 32767))
                    continue
                if self._recorded_flutter is not None:
                    pulse_rate = self._flutter_rate * (1.0 - 0.43 * progress)
                    if not self._flutter_canceling:
                        self._flutter_phase += pulse_rate / self.SAMPLE_RATE
                        if self._flutter_phase >= 1.0:
                            self._flutter_phase -= 1.0
                            pulse_strength = (1.0 - progress) ** 0.42
                            self._flutter_env_left = max(self._flutter_env_left, pulse_strength)
                            self._flutter_env_right = max(self._flutter_env_right, pulse_strength * 0.95)
                            self._recorded_flutter_pulses += 1
                    source_frames = len(self._recorded_flutter) // 2
                    source_frame = min(source_frames - 2, int(self._recorded_flutter_position))
                    fraction = self._recorded_flutter_position - source_frame
                    base = source_frame * 2
                    next_base = base + 2
                    fade = min(1.0, self._flutter_remaining / max(1, int(0.025 * self.SAMPLE_RATE)))
                    # A tiny floor preserves compressor texture, while the
                    # pulse envelope creates the clearly separated air catches.
                    gate_left = 0.025 + self._flutter_env_left * 1.08
                    gate_right = 0.025 + self._flutter_env_right * 1.08
                    gain = self._recorded_flutter_gain * fade
                    flutter_left = (
                        self._recorded_flutter[base] * (1.0 - fraction)
                        + self._recorded_flutter[next_base] * fraction
                    ) / 32768.0 * gain * gate_left
                    flutter_right = (
                        self._recorded_flutter[base + 1] * (1.0 - fraction)
                        + self._recorded_flutter[next_base + 1] * fraction
                    ) / 32768.0 * gain * gate_right
                    envelope_decay = 0.955 if self._flutter_canceling else self._flutter_env_decay
                    self._flutter_env_left *= envelope_decay
                    self._flutter_env_right *= envelope_decay
                    self._recorded_flutter_position += self._recorded_flutter_rate
                    if self._recorded_flutter_position >= source_frames - 2:
                        self._flutter_remaining = 1
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
                self._recorded_flutter = array("h", samples[onset:])
        except Exception:
            self._recorded_flutter = None

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
