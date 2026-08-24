from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .model import OBDFrame


@dataclass(frozen=True, slots=True)
class SurgeEvent:
    intensity: float
    rpm: float
    boost_bar: float
    throttle_drop: float
    lift_rate: float
    reason: str


class BackTurboDetector:
    """Detects a compressor-surge opportunity from normalized vehicle frames."""

    def __init__(self, cooldown_seconds: float = 0.28) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.previous: OBDFrame | None = None
        self.peak_boost = 0.0
        self.last_event_time = float("-inf")
        self.throttle_history: deque[tuple[float, float]] = deque()

    def update(self, frame: OBDFrame) -> SurgeEvent | None:
        previous = self.previous
        self.throttle_history.append((frame.timestamp, frame.throttle))
        while self.throttle_history and frame.timestamp - self.throttle_history[0][0] > 0.24:
            self.throttle_history.popleft()

        if frame.throttle > 0.16 and frame.rpm > 1700.0:
            self.peak_boost = max(self.peak_boost, frame.boost_bar)

        event: SurgeEvent | None = None
        if previous is not None:
            peak_time, peak_throttle = max(self.throttle_history, key=lambda item: item[1])
            throttle_drop = peak_throttle - frame.throttle
            lift_seconds = max(1 / 120, frame.timestamp - peak_time)
            lift_rate = throttle_drop / lift_seconds
            clutch_edge = frame.clutch_pressed and not previous.clutch_pressed
            charged = self.peak_boost >= 0.015 and previous.rpm >= 1800.0
            throttle_lift = throttle_drop >= 0.12 and frame.throttle <= 0.55
            clutch_lift = clutch_edge and peak_throttle >= 0.18
            cooled_down = frame.timestamp - self.last_event_time >= self.cooldown_seconds

            if charged and cooled_down and (throttle_lift or clutch_lift):
                boost_part = min(1.0, max(0.0, (self.peak_boost - 0.01) / 0.82))
                rpm_part = min(1.0, max(0.0, (previous.rpm - 1800.0) / 5200.0))
                drop_part = min(1.0, max(throttle_drop, 0.25 if clutch_lift else 0.0))
                rate_part = min(1.0, lift_rate / 5.0)
                intensity = max(0.04, min(1.0, 0.48 * boost_part + 0.20 * rpm_part + 0.20 * drop_part + 0.12 * rate_part))
                if self.peak_boost < 0.09 or intensity < 0.18:
                    reason = "valve_release"
                elif not clutch_lift and (self.peak_boost < 0.22 or intensity < 0.32):
                    reason = "pressure_release"
                elif clutch_lift:
                    reason = "clutch"
                else:
                    reason = "throttle_lift"
                event = SurgeEvent(
                    intensity=intensity,
                    rpm=previous.rpm,
                    boost_bar=self.peak_boost,
                    throttle_drop=drop_part,
                    lift_rate=lift_rate,
                    reason=reason,
                )
                self.last_event_time = frame.timestamp
                self.peak_boost = 0.0
                self.throttle_history.clear()

        self.previous = frame
        if event is None and previous is not None and frame.throttle <= 0.16:
            elapsed = max(0.0, min(0.2, frame.timestamp - previous.timestamp))
            self.peak_boost = max(0.0, self.peak_boost - elapsed * 0.28)
        return event
