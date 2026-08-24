from array import array
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

    def test_high_boost_recording_is_split_into_multiple_surges(self) -> None:
        engine = SoundEngine(enabled=False, engine_enabled=False)
        try:
            engine.enabled = True
            event = SurgeEvent(0.78, 6_200, 0.70, 0.92, 6.2, "throttle_lift")
            engine._activate_flutter(event)
            engine.render_chunk(engine._flutter_total)
            self.assertGreaterEqual(engine._recorded_flutter_pulses, 5)
            self.assertLessEqual(engine._recorded_flutter_pulses, 16)
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
            pou_pcm = engine.render_chunk(2_048)
            engine._activate_flutter(psh)
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
