from __future__ import annotations

from math import exp, pi, sin
from time import monotonic

from .model import DriverInput, OBDFrame


class VirtualVehicle:
    """Smooth, physics-inspired approximation of a turbocharged manual ZN6."""

    GEAR_RATIOS = (0.0, 3.626, 2.188, 1.541, 1.213, 1.000, 0.767)
    FINAL_DRIVE = 4.100
    TYRE_CIRCUMFERENCE_M = 1.985  # close to the stock 215/45R17 tyre
    TYRE_RADIUS_M = TYRE_CIRCUMFERENCE_M / (2.0 * pi)
    MASS_KG = 1_240.0
    IDLE_RPM = 850.0
    REDLINE_RPM = 7_600.0

    def __init__(self) -> None:
        self.rpm = self.IDLE_RPM
        self.speed_kmh = 0.0
        self.boost_bar = -0.65
        self._effective_throttle = 0.0

    @staticmethod
    def _approach(current: float, target: float, rate: float, dt: float) -> float:
        return target + (current - target) * exp(-rate * dt)

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def _engine_torque_nm(self, throttle: float) -> float:
        """Broad torque curve rather than a gear-specific fixed acceleration value."""
        rpm_position = self._clamp((self.rpm - 900.0) / 6_300.0, 0.0, 1.0)
        naturally_aspirated_torque = 112.0 + 88.0 * sin(rpm_position * pi * 0.57)
        turbo_gain = max(0.0, self.boost_bar) * 82.0
        limiter_cut = self._clamp((self.REDLINE_RPM + 80.0 - self.rpm) / 330.0, 0.0, 1.0)
        return (naturally_aspirated_torque + turbo_gain) * throttle * limiter_cut

    def _road_load_force(self, brake_pressed: bool) -> float:
        speed_mps = self.speed_kmh / 3.6
        rolling = self.MASS_KG * 9.81 * 0.012
        aerodynamic = 0.5 * 1.225 * 0.65 * speed_mps * speed_mps
        braking = 8_000.0 if brake_pressed else 0.0
        return rolling + aerodynamic + braking

    def update(self, raw_input: DriverInput, dt: float) -> OBDFrame:
        controls = raw_input.normalized()
        dt = self._clamp(dt, 0.001, 0.1)

        # The pedal and throttle plate do not move discontinuously. Keeping this
        # separate from the reported pedal value preserves fast lift detection.
        throttle_rate = 5.5 if controls.throttle > self._effective_throttle else 12.0
        self._effective_throttle = self._approach(
            self._effective_throttle, controls.throttle, throttle_rate, dt
        )

        driven = controls.gear > 0 and not controls.clutch_pressed
        road_force = self._road_load_force(controls.brake_pressed)

        if not driven:
            free_rev_target = self.IDLE_RPM + self._effective_throttle**0.68 * 6_350.0
            self.rpm = self._approach(self.rpm, free_rev_target, 5.4, dt)
            speed_mps = max(0.0, self.speed_kmh / 3.6 - road_force / self.MASS_KG * dt)
            self.speed_kmh = speed_mps * 3.6
        else:
            ratio = self.GEAR_RATIOS[controls.gear] * self.FINAL_DRIVE
            wheel_rpm = self.speed_kmh * 1000.0 / 60.0 / self.TYRE_CIRCUMFERENCE_M
            coupled_rpm = max(self.IDLE_RPM, wheel_rpm * ratio)

            # With the clutch engaged, wheel speed and engine speed are tied by
            # the selected ratio.  There is no automatic launch-RPM boost here:
            # use the clutch input for an intentional free-rev / gear change.
            target_rpm = max(self.IDLE_RPM, coupled_rpm)
            self.rpm = self._approach(self.rpm, target_rpm, 18.0, dt)

            wheel_force = self._engine_torque_nm(self._effective_throttle) * ratio * 0.88 / self.TYRE_RADIUS_M
            wheel_force = min(wheel_force, 6_300.0)  # basic tyre-grip limit
            engine_braking = (1.0 - self._effective_throttle) * (24.0 + self.rpm / 95.0) * ratio / self.TYRE_RADIUS_M
            acceleration = (wheel_force - engine_braking - road_force) / self.MASS_KG
            speed_mps = max(0.0, self.speed_kmh / 3.6 + acceleration * dt)
            self.speed_kmh = speed_mps * 3.6

        self.rpm = self._clamp(self.rpm, 750.0, self.REDLINE_RPM + 80.0)

        if self._effective_throttle > 0.08 and self.rpm > 1_850.0:
            spool = self._clamp((self.rpm - 1_850.0) / 2_900.0, 0.0, 1.0)
            boost_target = -0.18 + self._effective_throttle * (0.28 + 0.72 * spool)
            boost_rate = 2.0
        else:
            boost_target = -0.72 + self._effective_throttle * 0.16
            boost_rate = 5.4
        self.boost_bar = self._approach(self.boost_bar, boost_target, boost_rate, dt)

        return OBDFrame(
            timestamp=monotonic(),
            throttle=controls.throttle,
            clutch_pressed=controls.clutch_pressed,
            gear=controls.gear,
            rpm=self.rpm,
            speed_kmh=self.speed_kmh,
            boost_bar=self.boost_bar,
        )
