from array import array
from math import sqrt
import unittest

from backturbo.audio import SoundEngine
from backturbo.detector import SurgeEvent


class SoundEngineTests(unittest.TestCase):
    def test_continuous_engine_pcm_follows_live_state(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            self.assertEqual(set(engine._recorded_engine_bands), set(engine.RECORDED_ENGINE_BANDS))
            engine.engine_enabled = True
            engine._target_rpm = 4_200
            engine._target_throttle = 0.72
            engine._target_boost = 0.38
            pcm = engine.render_chunk(1_024)
            values = array("h")
            values.frombytes(pcm)
            self.assertEqual(len(pcm), 1_024 * 2 * 2)
            self.assertGreater(max(abs(value) for value in values), 500)
        finally:
            engine.close()

    def test_flutter_parameters_scale_from_low_to_high_boost(self) -> None:
        low = SurgeEvent(0.08, 2_200, 0.025, 0.18, 0.8, "valve_release")
        high = SurgeEvent(0.95, 6_800, 0.85, 1.0, 8.0, "throttle_lift")
        low_values = SoundEngine.flutter_parameters(low)
        high_values = SoundEngine.flutter_parameters(high)
        for high_value, low_value in zip(high_values, low_values):
            self.assertGreater(high_value, low_value)
        self.assertLess(low_values[0], 0.25)
        self.assertGreater(high_values[0], 0.75)

    def test_turbo_spool_whine_rises_with_rpm_and_boost(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            # Isolate the separately synthesized turbo layer from the recorded
            # engine so its pitch and output level can be tested directly.
            engine._recorded_engine_bands.clear()
            engine._target_rpm = 6_600
            engine._target_throttle = 0.92
            engine._target_boost = 0.72
            engine.render_chunk(engine.SAMPLE_RATE)
            pcm = engine.render_chunk(engine.SAMPLE_RATE // 2)
            values = array("h")
            values.frombytes(pcm)
            self.assertGreater(engine._turbo_spool, 0.65)
            self.assertGreater(engine._turbo_whine_hz, 2_300.0)
            self.assertLess(engine._turbo_whine_hz, 3_300.0)
            self.assertGreater(max(abs(value) for value in values), 600)
        finally:
            engine.close()

    def test_turbo_spool_whine_uses_back_turbine_toggle(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine._recorded_engine_bands.clear()
            engine._target_rpm = 6_200
            engine._target_throttle = 0.88
            engine._target_boost = 0.65
            muted = array("h")
            muted.frombytes(engine.render_chunk(engine.SAMPLE_RATE // 2))

            engine.enabled = True
            engine.render_chunk(engine.SAMPLE_RATE)
            audible = array("h")
            audible.frombytes(engine.render_chunk(engine.SAMPLE_RATE // 2))
            self.assertLess(max(abs(value) for value in muted), 10)
            self.assertGreater(max(abs(value) for value in audible), 500)
        finally:
            engine.close()

    def test_flutter_is_rendered_inside_continuous_stream(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            event = SurgeEvent(0.7, 5_500, 0.62, 0.85, 5.0, "throttle_lift")
            engine._activate_flutter(event)
            before = engine._flutter_remaining
            pcm = engine.render_chunk(2_048)
            values = array("h")
            values.frombytes(pcm)
            self.assertLess(engine._flutter_remaining, before)
            self.assertGreater(max(abs(value) for value in values), 500)
        finally:
            engine.close()

    def test_high_boost_voice_uses_real_air_recording_for_multiple_catches(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            self.assertIsNotNone(engine._compressor_air)
            self.assertIsNotNone(engine._compressor_air_hiss)
            self.assertEqual(len(engine._reference_spool_bands), 6)
            self.assertEqual(
                set(engine._reference_release_clips),
                {"valve_release", "pressure_release"},
            )
            self.assertEqual(len(engine._reference_surge_pulses), 12)
            event = SurgeEvent(0.78, 6_200, 0.70, 0.92, 6.2, "throttle_lift")
            engine._activate_flutter(event)
            self.assertGreaterEqual(engine._reference_release_rate, 0.90)
            self.assertLessEqual(engine._reference_release_rate, 1.10)
            pcm = engine.render_chunk(engine._flutter_total)
            values = array("h")
            values.frombytes(pcm)
            self.assertGreaterEqual(engine._recorded_flutter_pulses, 10)
            self.assertLessEqual(engine._recorded_flutter_pulses, 12)
            self.assertGreater(max(abs(value) for value in values), 3_000)

            mono = [(values[i] + values[i + 1]) * 0.5 for i in range(0, len(values), 2)]
            window = int(0.010 * engine.SAMPLE_RATE)
            rms = [
                sqrt(sum(value * value for value in mono[start:start + window]) / window)
                for start in range(0, len(mono) - window + 1, window)
            ]
            early = sum(rms[:18]) / 18
            middle_start = int(len(rms) * 0.50)
            middle_end = int(len(rms) * 0.78)
            middle = sum(rms[middle_start:middle_end]) / max(1, middle_end - middle_start)
            # Several catches must remain through the pressure-decay phase,
            # before the intentionally quiet exhausted-pressure tail.
            self.assertGreater(middle, early * 0.08)
            self.assertGreater(max(rms), min(value for value in rms if value > 1.0) * 3.0)
            # Exhausted pressure must end cleanly instead of repeating the
            # last source grain as a mechanical "ga-ga-ga" tail.
            final_tail = sum(rms[-6:]) / 6
            self.assertLess(final_tail, max(rms) * 0.12)
            tail_windows = rms[int(len(rms) * 0.76):]
            self.assertGreater(
                sum(value > max(rms) * 0.002 for value in tail_windows),
                5,
            )
            # Once compressor catches stop, the air wash must take over
            # without the large one-window collapse that sounded like a cut.
            exhausted_tail = rms[int(len(rms) * 0.82):]
            for previous, current in zip(exhausted_tail, exhausted_tail[1:]):
                self.assertGreaterEqual(current, previous * 0.30)
        finally:
            engine.close()

    def test_tiny_positive_boost_does_not_mix_noisy_reference_clip(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            tiny = SurgeEvent(0.20, 2_500, 0.05, 0.28, 1.3, "valve_release")
            engine._activate_flutter(tiny)
            self.assertEqual(engine._reference_release_gain, 0.0)
            tiny_pcm = engine.render_chunk(engine._flutter_total)

            audible = SurgeEvent(0.28, 2_900, 0.11, 0.34, 1.8, "valve_release")
            engine._activate_flutter(audible)
            transition_gain = engine._reference_release_gain
            self.assertGreater(transition_gain, 0.0)

            full = SurgeEvent(0.34, 3_200, 0.18, 0.45, 2.2, "valve_release")
            engine._activate_flutter(full)
            self.assertGreater(engine._reference_release_gain, transition_gain)

            engine._activate_flutter(audible)
            audible_pcm = engine.render_chunk(engine._flutter_total)
            self.assertNotEqual(tiny_pcm, audible_pcm)
        finally:
            engine.close()

    def test_pou_and_psh_use_distinct_release_envelopes(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            pou = SurgeEvent(0.12, 2_300, 0.05, 0.22, 1.2, "valve_release")
            psh = SurgeEvent(0.34, 3_200, 0.22, 0.50, 2.8, "pressure_release")
            pou_params = engine.flutter_parameters(pou)
            psh_params = engine.flutter_parameters(psh)
            self.assertGreater(psh_params[0], pou_params[0])
            self.assertGreater(psh_params[2], pou_params[2])
            engine._activate_flutter(pou)
            self.assertGreaterEqual(engine._reference_release_rate, 0.90)
            self.assertLessEqual(engine._reference_release_rate, 1.10)
            pou_pcm = engine.render_chunk(2_048)
            engine._activate_flutter(psh)
            self.assertGreaterEqual(engine._reference_release_rate, 0.90)
            self.assertLessEqual(engine._reference_release_rate, 1.10)
            psh_pcm = engine.render_chunk(2_048)
            self.assertNotEqual(pou_pcm, psh_pcm)
        finally:
            engine.close()

    def test_reopening_throttle_cancels_active_flutter(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            engine._activate_flutter(SurgeEvent(0.9, 6_500, 0.78, 1.0, 7.0, "throttle_lift"))
            engine.render_chunk(1_024)
            self.assertGreater(engine._flutter_remaining, 2_048)
            with engine._state_lock:
                engine._cancel_flutter_requested = True
            engine.render_chunk(2_048)
            self.assertEqual(engine._flutter_remaining, 0)
        finally:
            engine.close()

    def test_afplay_fallback_bank_can_be_prepared(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine._generate_fallback_samples()
            self.assertEqual(len(engine._surge_samples), 7)
            self.assertEqual(len(engine._engine_samples), len(engine.ENGINE_RPM_BANDS))
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
