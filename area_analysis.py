"""Convert recorded tablet positions into a finite, board-contained mm area."""

import math

import numpy as np


def _positive_finite(value):
    if isinstance(value, bool):
        raise ValueError("數值必須是有限正數")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("數值必須是有限正數")
    return number


def parse_aspect_ratio(text):
    """Parse a decimal or width:height; reject invalid locks with ValueError."""
    try:
        parts = str(text).strip().split(":")
        if len(parts) == 1:
            return _positive_finite(parts[0])
        if len(parts) == 2:
            return _positive_finite(
                _positive_finite(parts[0]) / _positive_finite(parts[1])
            )
    except (ValueError, TypeError, OverflowError):
        pass
    raise ValueError("比例須為有限正數，例如 16:9 或 1.7778")


class ModernCalculationEngine:
    @staticmethod
    def calculate(data_points, max_x, max_y, phys_w_mm, phys_h_mm,
                  aspect_ratio=None):
        """Return None for unusable data, otherwise a full precision mm rectangle.

        Dwell samples count toward recording statistics, but each occupied
        1/256-board strip votes once per axis in the IQR fence. Sub-strip pen
        jitter and long pauses cannot outweigh the player's movement. The
        fence selects strips; the area still uses original exact positions.
        """
        try:
            max_x, max_y, phys_w_mm, phys_h_mm = map(
                _positive_finite, (max_x, max_y, phys_w_mm, phys_h_mm)
            )
            ratio = (_positive_finite(aspect_ratio)
                     if aspect_ratio is not None else None)
            if len(data_points) < 10:
                return None
            positions = np.array(
                [(float(point['x']), float(point['y']))
                 for point in data_points], dtype=float
            )
        except (TypeError, ValueError, KeyError, OverflowError):
            return None

        # A nonfinite sample is malformed input. Finite out-of-board samples
        # can be noise and are discarded before calculating any statistics.
        if not np.isfinite(positions).all():
            return None
        positions = positions[
            (positions[:, 0] >= 0) & (positions[:, 0] <= max_x) &
            (positions[:, 1] >= 0) & (positions[:, 1] <= max_y)
        ]
        if len(positions) < 10:
            return None
        unique, counts = np.unique(positions, axis=0, return_counts=True)
        cells = np.minimum(
            (unique / (max_x, max_y) * 256).astype(np.int64), 255
        )
        keep = np.ones(len(unique), dtype=bool)
        for axis in range(2):
            values = np.unique(cells[:, axis])
            # Cell indices avoid overflow for large but finite raw scales.
            # Axis occupancy also prevents a cross-shaped path from weighting
            # its shared central coordinates more heavily than its arms.
            q1, q3 = np.percentile(values, [25, 75])
            spread = q3 - q1
            keep &= ((cells[:, axis] >= q1 - 1.5 * spread) &
                     (cells[:, axis] <= q3 + 1.5 * spread))
        valid = unique[keep]
        if not len(valid) or int(counts[keep].sum()) < 5:
            return None

        low, high = valid.min(axis=0), valid.max(axis=0)
        # Normalize first; multiplying raw counts by mm/count can overflow.
        left, top = low / (max_x, max_y) * (phys_w_mm, phys_h_mm)
        right, bottom = high / (max_x, max_y) * (phys_w_mm, phys_h_mm)
        width, height = float(right - left), float(bottom - top)
        if width <= 0 or height <= 0:
            return None
        center_x = left + width / 2
        center_y = top + height / 2
        was_shrunk = False

        if ratio is not None:
            # Start with the largest ratio-preserving box that fits the board.
            # Use its fractions to expand around the samples without overflow.
            if phys_w_mm / phys_h_mm > ratio:
                fit_w, fit_h = phys_h_mm * ratio, phys_h_mm
            else:
                fit_w, fit_h = phys_w_mm, phys_w_mm / ratio
            if fit_w <= 0 or fit_h <= 0:
                return None
            fraction = max(width / fit_w, height / fit_h)
            was_shrunk = bool(fraction > 1)
            fraction = min(1.0, fraction)
            width, height = fit_w * fraction, fit_h * fraction

        # Move the whole rectangle before clipping; independent edge clamps
        # change the selected ratio. Oversize ratio boxes already scaled above.
        center_x = min(max(center_x, width / 2), phys_w_mm - width / 2)
        center_y = min(max(center_y, height / 2), phys_h_mm - height / 2)
        output = (width, height, center_x, center_y)
        if not all(math.isfinite(value) for value in output):
            return None
        if width <= 0 or height <= 0:
            return None

        used_points = int(counts[keep].sum())
        if was_shrunk:
            mm_positions = valid / (max_x, max_y) * (phys_w_mm, phys_h_mm)
            tolerance = np.array((phys_w_mm, phys_h_mm)) * 1e-12
            inside = (
                (mm_positions >= (center_x - width / 2,
                                  center_y - height / 2) - tolerance) &
                (mm_positions <= (center_x + width / 2,
                                  center_y + height / 2) + tolerance)
            ).all(axis=1)
            coverage = int(counts[keep][inside].sum()) / used_points
        else:
            coverage = 1.0

        return {
            'width_mm': float(width), 'height_mm': float(height),
            'x_offset_mm': float(center_x), 'y_offset_mm': float(center_y),
            'used_points': used_points, 'total_points': len(data_points),
            'raw_w': float(width / phys_w_mm * max_x),
            'raw_h': float(height / phys_h_mm * max_y),
            'was_shrunk': was_shrunk,
            'coverage_fraction': coverage,
            'warning': ("所選比例無法涵蓋全部有效軌跡；已等比例縮小到板內。"
                        if was_shrunk else None),
        }
