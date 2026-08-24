import unittest

from backturbo.detector import BackTurboDetector
from backturbo.model import OBDFrame


def frame(time: float, throttle: float, rpm: float = 4000.0, boost: float = 0.55, clutch: bool = False) -> OBDFrame:
    return OBDFrame(time, throttle, clutch, 3, rpm, 60.0, boost)


class DetectorTests(unittest.TestCase):
    def test_triggers_on_charged_sudden_lift(self) -> None:
        detector = BackTurboDetector()
        self.assertIsNone(detector.update(frame(1.0, 0.85)))
        event = detector.update(frame(1.1, 0.05, boost=0.20))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.reason, "throttle_lift")
        self.assertGreater(event.intensity, 0.2)
        self.assertGreater(event.boost_bar, 0.1)
        self.assertGreater(event.throttle_drop, 0.5)

    def test_does_not_trigger_without_boost(self) -> None:
        detector = BackTurboDetector()
        detector.update(frame(1.0, 0.85, boost=0.0))
        self.assertIsNone(detector.update(frame(1.1, 0.0, boost=-0.4)))

    def test_low_boost_produces_a_small_event(self) -> None:
        detector = BackTurboDetector()
        detector.update(frame(1.0, 0.42, rpm=2_200, boost=0.025))
        event = detector.update(frame(1.16, 0.20, rpm=2_100, boost=-0.05))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertLess(event.intensity, 0.35)
        self.assertGreater(event.lift_rate, 0.5)
        self.assertEqual(event.reason, "valve_release")

    def test_medium_boost_produces_pressure_release(self) -> None:
        detector = BackTurboDetector()
        detector.update(frame(1.0, 0.58, rpm=3_000, boost=0.18))
        event = detector.update(frame(1.12, 0.18, rpm=2_900, boost=0.02))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.reason, "pressure_release")

    def test_clutch_edge_can_trigger(self) -> None:
        detector = BackTurboDetector()
        detector.update(frame(1.0, 0.55, boost=0.50))
        event = detector.update(frame(1.1, 0.50, boost=0.30, clutch=True))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.reason, "clutch")

    def test_cooldown_prevents_double_trigger(self) -> None:
        detector = BackTurboDetector(cooldown_seconds=0.5)
        detector.update(frame(1.0, 0.8))
        self.assertIsNotNone(detector.update(frame(1.1, 0.0)))
        detector.update(frame(1.2, 0.8))
        self.assertIsNone(detector.update(frame(1.3, 0.0)))


if __name__ == "__main__":
    unittest.main()
