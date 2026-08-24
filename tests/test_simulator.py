import unittest

from backturbo.model import DriverInput
from backturbo.simulator import VirtualVehicle


class SimulatorTests(unittest.TestCase):
    def test_acceleration_builds_speed_rpm_and_boost(self) -> None:
        car = VirtualVehicle()
        controls = DriverInput(throttle=1.0, gear=2)
        result = None
        for _ in range(500):
            result = car.update(controls, 0.016)
        assert result is not None
        self.assertGreater(result.speed_kmh, 20.0)
        self.assertGreater(result.rpm, 2200.0)
        self.assertGreater(result.boost_bar, 0.05)

    def test_clutch_decouples_engine_and_wheels(self) -> None:
        car = VirtualVehicle()
        for _ in range(200):
            car.update(DriverInput(throttle=0.8, gear=2), 0.016)
        before = car.speed_kmh
        result = car.update(DriverInput(throttle=1.0, clutch_pressed=True, gear=2), 0.1)
        self.assertGreater(result.rpm, 1000.0)
        self.assertLessEqual(result.speed_kmh, before)

    def test_inputs_are_clamped(self) -> None:
        car = VirtualVehicle()
        result = car.update(DriverInput(throttle=5.0, gear=99), 0.016)
        self.assertEqual(result.throttle, 1.0)
        self.assertEqual(result.gear, 6)

    def test_launch_rpm_and_speed_change_without_a_handoff_drop(self) -> None:
        car = VirtualVehicle()
        frames = [car.update(DriverInput(throttle=1.0, gear=2), 1 / 60) for _ in range(240)]
        rpm_changes = [later.rpm - earlier.rpm for earlier, later in zip(frames, frames[1:])]
        speed_changes = [later.speed_kmh - earlier.speed_kmh for earlier, later in zip(frames, frames[1:])]
        self.assertGreater(frames[-1].rpm, frames[0].rpm)
        self.assertGreater(frames[-1].speed_kmh, frames[0].speed_kmh)
        self.assertGreaterEqual(min(rpm_changes), -1.0)
        self.assertLess(max(speed_changes), 0.5)

    def test_lower_gears_multiply_wheel_force_and_raise_rpm_sooner(self) -> None:
        frames = []
        for gear in (1, 2, 3):
            car = VirtualVehicle()
            for _ in range(180):
                frame = car.update(DriverInput(throttle=1.0, gear=gear), 1 / 60)
            frames.append(frame)
        first, second, third = frames
        self.assertGreater(first.speed_kmh, second.speed_kmh)
        self.assertGreater(second.speed_kmh, third.speed_kmh)
        self.assertGreater(first.rpm, second.rpm)
        self.assertGreater(second.rpm, third.rpm)


if __name__ == "__main__":
    unittest.main()
