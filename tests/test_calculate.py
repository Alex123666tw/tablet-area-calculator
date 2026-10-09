"""Unit tests for the pure calculation logic (no hardware required)."""

import math

import pytest

from area_analysis import ModernCalculationEngine, parse_aspect_ratio


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
    assert res['width_mm'] / res['height_mm'] == pytest.approx(16 / 9)
    assert res['x_offset_mm'] - res['width_mm'] / 2 >= -0.01
    assert res['x_offset_mm'] + res['width_mm'] / 2 <= 152.0 + 0.01
    assert res['y_offset_mm'] - res['height_mm'] / 2 >= -0.01
    assert res['y_offset_mm'] + res['height_mm'] / 2 <= 95.0 + 0.01
    assert res['was_shrunk'] is True
    assert 0 < res['coverage_fraction'] < 1
    assert res['warning']


def _rectangle(x_start=4000, y_start=3000):
    return [{'x': x, 'y': y}
            for x in range(x_start, x_start + 2001, 100)
            for y in range(y_start, y_start + 1001, 100)]


def _assert_valid_area(result, width=152.0, height=95.0, ratio=None):
    assert result is not None
    for key in ('width_mm', 'height_mm', 'x_offset_mm', 'y_offset_mm',
                'raw_w', 'raw_h'):
        assert math.isfinite(result[key])
    assert result['width_mm'] > 0
    assert result['height_mm'] > 0
    assert result['x_offset_mm'] - result['width_mm'] / 2 >= -1e-10
    assert result['y_offset_mm'] - result['height_mm'] / 2 >= -1e-10
    assert result['x_offset_mm'] + result['width_mm'] / 2 <= width + 1e-10
    assert result['y_offset_mm'] + result['height_mm'] / 2 <= height + 1e-10
    if ratio is not None:
        assert result['width_mm'] / result['height_mm'] == pytest.approx(ratio)


@pytest.mark.parametrize('x_start,y_start', [(100, 100), (13100, 8400)])
@pytest.mark.parametrize('ratio', [16 / 9, 9 / 16])
def test_ratio_at_board_edges_moves_entire_rectangle(x_start, y_start, ratio):
    result = ModernCalculationEngine.calculate(
        _rectangle(x_start, y_start), 15200, 9500, 152.0, 95.0, ratio,
    )
    _assert_valid_area(result, ratio=ratio)
    assert result['was_shrunk'] is False
    assert result['coverage_fraction'] == 1


def test_ratio_uses_mm_when_axis_scales_differ():
    points = [{'x': x, 'y': y}
              for x in range(100, 1101, 50)
              for y in range(100, 1101, 50)]
    result = ModernCalculationEngine.calculate(
        points, 10000, 10000, 200.0, 100.0, 16 / 9,
    )
    _assert_valid_area(result, 200.0, 100.0, 16 / 9)
    assert result['width_mm'] == pytest.approx(20.0)
    assert result['height_mm'] == pytest.approx(11.25)
    assert result['was_shrunk'] is False


def test_square_near_top_left_keeps_16_by_9_after_translation():
    points = [{'x': x, 'y': y}
              for x in range(100, 1101, 50)
              for y in range(100, 1101, 50)]
    result = ModernCalculationEngine.calculate(
        points, 15200, 9500, 152.0, 95.0, 16 / 9,
    )
    _assert_valid_area(result, ratio=16 / 9)
    assert result['width_mm'] == pytest.approx(10 * 16 / 9)
    assert result['height_mm'] == pytest.approx(10)
    assert result['x_offset_mm'] - result['width_mm'] / 2 == pytest.approx(0)
    assert result['coverage_fraction'] == 1


def test_long_center_pause_preserves_movement_and_still_rejects_noise():
    movement = _rectangle()
    points = ([{'x': 5000, 'y': 3500}] * 1000 + movement +
              [{'x': 15000, 'y': 9400}] * 50)
    result = ModernCalculationEngine.calculate(
        points, 15200, 9500, 152.0, 95.0,
    )
    _assert_valid_area(result)
    assert result['width_mm'] == pytest.approx(20.0)
    assert result['height_mm'] == pytest.approx(10.0)
    assert result['used_points'] == 1000 + len(movement)
    assert result['total_points'] == len(points)


def test_center_pause_with_submillimeter_jitter_preserves_movement():
    # Every dwell sample differs, so exact coordinate deduplication alone
    # would still let the pause flatten the legitimate playing rectangle.
    dwell = [{'x': 4975 + i % 50, 'y': 3490 + i // 50}
             for i in range(1000)]
    result = ModernCalculationEngine.calculate(
        dwell + _rectangle(), 15200, 9500, 152.0, 95.0,
    )
    _assert_valid_area(result)
    assert result['width_mm'] == pytest.approx(20.0)
    assert result['height_mm'] == pytest.approx(10.0)


def test_cross_shaped_activity_with_repeated_axes_has_area():
    points = ([{'x': 5000, 'y': y} for y in range(3000, 4001, 10)] +
              [{'x': x, 'y': 3500} for x in range(4500, 5501, 10)])
    result = ModernCalculationEngine.calculate(points, 15200, 9500, 152.0, 95.0)
    _assert_valid_area(result)
    assert result['width_mm'] == pytest.approx(10.0)
    assert result['height_mm'] == pytest.approx(10.0)


@pytest.mark.parametrize('points', [
    [{'x': 5000, 'y': 3500}] * 20,
    [{'x': x, 'y': 3500} for x in range(4000, 6001, 100)],
    [{'x': 18000, 'y': 12000}] * 20,
    [{'x': -1, 'y': -1}] * 20,
])
def test_stationary_zero_area_and_outside_input_do_not_produce_settings(points):
    assert ModernCalculationEngine.calculate(
        points, 15200, 9500, 152.0, 95.0,
    ) is None


@pytest.mark.parametrize('invalid', [0, -1, float('nan'), float('inf'),
                                   -float('inf'), True, 'invalid'])
@pytest.mark.parametrize('field', range(5))
def test_invalid_dimensions_scales_and_ratio_are_rejected(invalid, field):
    args = [15200, 9500, 152.0, 95.0, 16 / 9]
    args[field] = invalid
    assert ModernCalculationEngine.calculate(_rectangle(), *args) is None


@pytest.mark.parametrize('coordinate', [float('nan'), float('inf'),
                                       -float('inf'), 'invalid'])
def test_nonfinite_or_malformed_coordinate_is_rejected(coordinate):
    points = _rectangle() + [{'x': coordinate, 'y': 3500}]
    assert ModernCalculationEngine.calculate(
        points, 15200, 9500, 152.0, 95.0,
    ) is None


@pytest.mark.parametrize('text', ['', '0', '-1', 'NaN', 'inf', '16:0',
                                '0:9', '-16:-9', '16:9:1', 'abc'])
def test_invalid_ratio_text_does_not_silently_select_a_default(text):
    with pytest.raises(ValueError):
        parse_aspect_ratio(text)


def test_ratio_parser_supports_both_entry_formats():
    assert parse_aspect_ratio(' 16 : 9 ') == pytest.approx(16 / 9)
    assert parse_aspect_ratio('1.5') == 1.5


def test_only_ctl4100_is_marked_verified():
    # Honesty invariant: only hardware we actually tested may claim verified.
    from otd_area_calculator import TABLET_SPECS, TabletDetector
    ctl4100 = (0x056A, 0x0374)
    assert TABLET_SPECS[ctl4100]['verified'] is True
    assert all(not spec['verified']
               for key, spec in TABLET_SPECS.items() if key != ctl4100)
    assert TabletDetector._unknown()['verified'] is False


def test_tablet_db_breadth_loaded():
    # The OpenTabletDriver-derived spec table should add broad coverage beyond
    # the handful of built-in entries.
    from otd_area_calculator import TABLET_SPECS
    assert len(TABLET_SPECS) > 100
