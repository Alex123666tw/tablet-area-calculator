"""Unit tests for the pure calculation logic (no hardware required)."""

from otd_area_calculator import ModernCalculationEngine


def test_basic_rectangle_maps_to_expected_mm():
    # A filled 2000x1000-count rectangle centred at (5000, 3500).
    pts = [{'x': x, 'y': y}
           for x in range(4000, 6001, 100)
           for y in range(3000, 4001, 100)]

    res = ModernCalculationEngine.calculate(
        pts, max_x=15200, max_y=9500,
        phys_w_mm=152.0, phys_h_mm=95.0, aspect_ratio=None,
    )

    assert res is not None
    # 2000 / 15200 * 152 = 20 mm ; 1000 / 9500 * 95 = 10 mm
    assert abs(res['width_mm'] - 20.0) < 1.0
    assert abs(res['height_mm'] - 10.0) < 1.0
    # centre 5000 -> 50 mm ; centre 3500 -> 35 mm
    assert abs(res['x_offset_mm'] - 50.0) < 1.0
    assert abs(res['y_offset_mm'] - 35.0) < 1.0


def test_too_few_points_returns_none():
    assert ModernCalculationEngine.calculate(
        [{'x': 1, 'y': 1}] * 3, 15200, 9500, 152.0, 95.0,
    ) is None


def test_ratio_locked_area_stays_within_tablet():
    # Points covering the whole board; with a 16:9 lock the expansion must be
    # clamped so the suggested area never extends past the physical surface.
    pts = [{'x': x, 'y': y}
           for x in range(0, 15201, 200)
           for y in range(0, 9501, 200)]

    res = ModernCalculationEngine.calculate(
        pts, 15200, 9500, 152.0, 95.0, aspect_ratio=16 / 9,
    )

    assert res is not None
    assert res['x_offset_mm'] - res['width_mm'] / 2 >= -0.01
    assert res['x_offset_mm'] + res['width_mm'] / 2 <= 152.0 + 0.01
    assert res['y_offset_mm'] - res['height_mm'] / 2 >= -0.01
    assert res['y_offset_mm'] + res['height_mm'] / 2 <= 95.0 + 0.01


def test_only_ctl4100_is_marked_verified():
    # Honesty invariant: only hardware we actually tested may claim verified.
    from otd_area_calculator import TABLET_SPECS, TabletDetector
    ctl4100 = (0x056A, 0x0374)
    assert TABLET_SPECS[ctl4100]['verified'] is True
    assert all(not spec['verified']
               for key, spec in TABLET_SPECS.items() if key != ctl4100)
    assert TabletDetector._unknown()['verified'] is False
