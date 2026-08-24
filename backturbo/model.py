from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(slots=True)
class DriverInput:
    """Inputs that will eventually be replaced by values derived from the car."""

    throttle: float = 0.0
    clutch_pressed: bool = False
    brake_pressed: bool = False
    gear: int = 0

    def normalized(self) -> "DriverInput":
        return DriverInput(
            throttle=max(0.0, min(1.0, self.throttle)),
            clutch_pressed=bool(self.clutch_pressed),
            brake_pressed=bool(self.brake_pressed),
            gear=max(0, min(6, int(self.gear))),
        )


@dataclass(frozen=True, slots=True)
class OBDFrame:
    """Canonical data consumed by the sound logic, independent of its source."""

    timestamp: float
    throttle: float
    clutch_pressed: bool
    gear: int
    rpm: float
    speed_kmh: float
    boost_bar: float

    @classmethod
    def stopped(cls) -> "OBDFrame":
        return cls(monotonic(), 0.0, False, 0, 850.0, 0.0, -0.65)

