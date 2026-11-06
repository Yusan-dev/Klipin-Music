"""Enhanced temporal smoothing with easing curves and adaptive Kalman.

Phase 2 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Provides perceptually natural crop window transitions:
  - Cubic in-out easing (smooth start and stop)
  - Adaptive Kalman Q-matrix based on detected motion speed
  - Scene-type-aware smoothing (tight for talking-head, loose for wide shots)
"""
import numpy as np
from typing import List, Tuple, Optional


# ─── Easing functions ──────────────────────────────────────────────────────

def ease_linear(t: float) -> float:
    """Linear interpolation."""
    return t


def ease_in(t: float) -> float:
    """Slow start, fast end (quadratic)."""
    return t ** 2


def ease_out(t: float) -> float:
    """Fast start, slow end (quadratic)."""
    return 1 - (1 - t) ** 2


def ease_in_out_cubic(t: float) -> float:
    """Smooth start and stop (cubic). Best for camera pans."""
    if t < 0.5:
        return 4 * t ** 3
    else:
        return 1 - (-2 * t + 2) ** 3 / 2


def ease_in_out_sine(t: float) -> float:
    """Smooth start and stop (sinusoidal). Natural feel."""
    return -(np.cos(np.pi * t) - 1) / 2


EASING_FUNCTIONS = {
    "linear": ease_linear,
    "ease_in": ease_in,
    "ease_out": ease_out,
    "cubic_inout": ease_in_out_cubic,
    "sine_inout": ease_in_out_sine,
}


# ─── Adaptive Kalman Filter ────────────────────────────────────────────────

class AdaptiveKalmanFilter:
    """Kalman filter with adaptive Q-matrix based on detected motion speed.

    When the subject moves fast (high motion), the Q-matrix increases to
    allow the filter to follow quickly. When stationary, Q decreases for
    maximum smoothing.
    """

    def __init__(self, dt: float = 1/30.0,
                 base_process_var: float = 1.0,
                 base_measure_var: float = 10.0):
        self.dt = dt
        self.base_q = base_process_var
        self.base_r = base_measure_var

        # State: [x, y, vx, vy]
        self.x = np.zeros(4)
        self.P = np.eye(4) * 1000
        self.initialized = False

    def initialize(self, x: float, y: float):
        """Set initial position."""
        self.x = np.array([x, y, 0.0, 0.0])
        self.initialized = True

    def predict(self, motion_speed: float = 0.0) -> Tuple[float, float]:
        """Predict next position. motion_speed adapts the Q-matrix.

        Args:
            motion_speed: estimated speed of subject (px/frame).
                         Higher = more motion = larger Q = trust measurements more.
        """
        F = np.array([
            [1, 0, self.dt, 0],
            [0, 1, 0, self.dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ])

        # Adaptive Q: increase process noise when motion is detected
        adaptive_q = self.base_q * (1.0 + motion_speed * 0.1)
        Q = np.eye(4) * adaptive_q
        Q[0, 0] *= (1.0 + motion_speed * 0.05)  # x more uncertain
        Q[1, 1] *= (1.0 + motion_speed * 0.05)  # y more uncertain

        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

        return float(self.x[0]), float(self.x[1])

    def update(self, z_x: float, z_y: float) -> Tuple[float, float]:
        """Update with measurement."""
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
        z = np.array([z_x, z_y])

        y = z - H @ self.x
        S = H @ self.P @ H.T + np.eye(2) * self.base_r
        K = self.P @ H.T @ np.linalg.inv(S)

        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P

        return float(self.x[0]), float(self.x[1])

    def get_position(self) -> Tuple[float, float]:
        return float(self.x[0]), float(self.x[1])


# ─── Scene-Aware Smoothing ────────────────────────────────────────────────

def get_smoothing_params(scene_type: str) -> dict:
    """Get smoothing parameters based on scene type.

    Different scenes need different smoothing:
      - talking_head: tight smoothing, fast follow (subject moves head)
      - whiteboard: moderate smoothing (presenter may gesture)
      - crowd/wide: loose smoothing (slow pan, no fast follow)
      - 2_person: moderate smoothing (switch between speakers)
    """
    params = {
        "talking_head": {
            "easing": "cubic_inout",
            "kalman_q": 2.0,
            "kalman_r": 8.0,
            "window_size": 3,
        },
        "talking_head_bright": {
            "easing": "cubic_inout",
            "kalman_q": 2.0,
            "kalman_r": 8.0,
            "window_size": 3,
        },
        "whiteboard": {
            "easing": "sine_inout",
            "kalman_q": 1.0,
            "kalman_r": 12.0,
            "window_size": 5,
        },
        "2_person_close": {
            "easing": "cubic_inout",
            "kalman_q": 1.5,
            "kalman_r": 10.0,
            "window_size": 4,
        },
        "2_person_separate": {
            "easing": "cubic_inout",
            "kalman_q": 3.0,
            "kalman_r": 6.0,
            "window_size": 3,
        },
        "crowd": {
            "easing": "sine_inout",
            "kalman_q": 0.5,
            "kalman_r": 15.0,
            "window_size": 7,
        },
        "wide_shot_static": {
            "easing": "sine_inout",
            "kalman_q": 0.5,
            "kalman_r": 15.0,
            "window_size": 7,
        },
        "wide_shot_moving": {
            "easing": "ease_out",
            "kalman_q": 1.0,
            "kalman_r": 10.0,
            "window_size": 5,
        },
    }
    return params.get(scene_type, params["talking_head"])


def smooth_positions_adaptive(
    positions: List[Tuple[int, int]],
    scene_type: str = "talking_head",
    fps: float = 30.0,
    easing_name: str = None,
) -> List[Tuple[int, int]]:
    """Smooth crop positions with scene-aware adaptive Kalman + easing.

    Args:
        positions: [(x, y), ...] per-frame crop positions
        scene_type: scene classification result
        fps: video frame rate
        easing_name: override easing function (None = use scene default)

    Returns:
        Smoothed [(x, y), ...] positions
    """
    if len(positions) <= 2:
        return positions

    params = get_smoothing_params(scene_type)
    easing_name = easing_name or params["easing"]
    easing_fn = EASING_FUNCTIONS.get(easing_name, ease_in_out_cubic)

    # Create adaptive Kalman filter
    dt = 1.0 / fps
    kf = AdaptiveKalmanFilter(
        dt=dt,
        base_process_var=params["kalman_q"],
        base_measure_var=params["kalman_r"],
    )

    # First pass: Kalman filter
    kalman_positions = []
    prev_x, prev_y = positions[0]
    kf.initialize(float(prev_x), float(prev_y))

    for i, (x, y) in enumerate(positions):
        # Estimate motion speed for adaptive Q
        if i > 0:
            speed = ((x - positions[i-1][0]) ** 2 +
                     (y - positions[i-1][1]) ** 2) ** 0.5
        else:
            speed = 0.0

        # Predict with adaptive Q
        pred_x, pred_y = kf.predict(motion_speed=speed)

        # Update with measurement
        smooth_x, smooth_y = kf.update(float(x), float(y))
        kalman_positions.append((int(round(smooth_x)), int(round(smooth_y))))

    # Second pass: apply easing curve for perceptually smooth transitions
    if len(kalman_positions) <= 2:
        return kalman_positions

    eased_positions = [kalman_positions[0]]
    window = params["window_size"]

    for i in range(1, len(kalman_positions)):
        # Look at trajectory context
        start = max(0, i - window)
        end = min(len(kalman_positions), i + 1)
        context = kalman_positions[start:end]

        if len(context) < 2:
            eased_positions.append(kalman_positions[i])
            continue

        # Easing parameter: 0 at start of context, 1 at current
        t = (len(context) - 1) / max(1, window)
        t = min(1.0, t)

        # Apply easing to interpolation
        alpha = easing_fn(t)

        # Interpolate between first and last in context
        start_pos = context[0]
        end_pos = context[-1]
        eased_x = int(round(start_pos[0] + (end_pos[0] - start_pos[0]) * alpha))
        eased_y = int(round(start_pos[1] + (end_pos[1] - start_pos[1]) * alpha))
        eased_positions.append((eased_x, eased_y))

    return eased_positions


def detect_pan_speed(positions: List[Tuple[int, int]],
                     fps: float = 30.0,
                     window: int = 5) -> List[float]:
    """Detect camera pan speed at each frame.

    Returns:
        [speed_px_per_frame, ...] — positive = moving right/down
    """
    if len(positions) < 2:
        return [0.0] * len(positions)

    speeds = []
    for i in range(len(positions)):
        if i == 0:
            speeds.append(0.0)
            continue

        dx = positions[i][0] - positions[i-1][0]
        dy = positions[i][1] - positions[i-1][1]
        speed = (dx ** 2 + dy ** 2) ** 0.5
        speeds.append(speed)

    # Smooth speeds
    if len(speeds) >= window:
        smoothed = []
        for i in range(len(speeds)):
            lo = max(0, i - window // 2)
            hi = min(len(speeds), i + window // 2 + 1)
            smoothed.append(float(np.mean(speeds[lo:hi])))
        return smoothed

    return speeds
