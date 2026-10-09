"""Real Qt canvas scenarios with generated tablet samples (no HID hardware)."""

import os

import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from area_canvas import LiquidCanvas


@pytest.fixture(scope='module')
def app():
    # The application module may select Windows when imported by other tests.
    # Set this immediately before creating QApplication rather than at import.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def canvas(app):
    view = LiquidCanvas(152.0, 95.0)
    view.resize(640, 400)
    yield view
    view.close()
    view.deleteLater()
    app.processEvents()


def _sample(canvas, x, y, down=True, **kwargs):
    canvas.update_state(x, y, down, 15200, 9500, **kwargs)


def test_clear_during_contact_starts_a_new_stroke(canvas):
    _sample(canvas, .1, .1)
    _sample(canvas, .2, .2)
    assert len(canvas.trace_items) == 1
    canvas.clear_canvas()
    _sample(canvas, .8, .8)
    assert not canvas.trace_items  # first new point cannot connect to old stroke
    _sample(canvas, .9, .9)
    assert len(canvas.trace_items) == 1
    path = canvas.trace_items[0].path()
    assert path.elementAt(0).x == pytest.approx(.8 * canvas.sw)
    assert path.elementAt(0).y == pytest.approx(.8 * canvas.sh)


def test_overlay_replaces_previous_result_and_can_be_invalidated(canvas):
    _sample(canvas, .1, .1)
    _sample(canvas, .2, .2)
    baseline_items = len(canvas.scene.items())
    for center in (50.0, 60.0, 70.0):
        canvas.overlay_result({'width_mm': 20, 'height_mm': 10,
                               'x_offset_mm': center, 'y_offset_mm': 35})
        assert len(canvas.scene.items()) == baseline_items + 1
    assert canvas.overlay_item.rect().center().x() == pytest.approx(
        70 / 152 * canvas.sw)
    canvas.clear_result()
    assert len(canvas.scene.items()) == baseline_items
    assert len(canvas.trace_items) == 1
    canvas.overlay_result(None)
    assert len(canvas.scene.items()) == baseline_items


def test_hover_contact_and_out_of_range_are_distinct(canvas):
    _sample(canvas, .1, .1, False)
    _sample(canvas, .2, .2, False)
    assert canvas.live_cursor.isVisible()
    assert not canvas.trace_items  # legacy default traces contact only
    _sample(canvas, .3, .3, False, trace_hover=True)
    _sample(canvas, .4, .4, False, trace_hover=True)
    hover = canvas.trace_items[-1]
    hover_color = hover.pen().color()
    _sample(canvas, .6, .6)
    _sample(canvas, .7, .7)
    contact = canvas.trace_items[-1]
    assert contact is not hover
    assert contact.pen().color() != hover_color
    assert contact.path().elementAt(0).x == pytest.approx(.6 * canvas.sw)
    _sample(canvas, .7, .7, in_range=False)
    assert not canvas.live_cursor.isVisible()
    _sample(canvas, .9, .9)
    assert len(canvas.trace_items) == 2
    _sample(canvas, 1, 1)
    assert canvas.trace_items[-1].path().elementAt(0).x == pytest.approx(
        .9 * canvas.sw)


def test_changing_scale_or_tablet_does_not_connect_old_position(canvas):
    _sample(canvas, .1, .1)
    _sample(canvas, .2, .2)
    canvas.update_state(.6, .6, True, 30400, 19000)
    assert len(canvas.trace_items) == 1
    canvas.update_state(.7, .7, True, 30400, 19000)
    assert canvas.trace_items[-1].path().elementAt(0).x == pytest.approx(
        .6 * canvas.sw)
    canvas.phys_w, canvas.phys_h = 200, 100
    canvas.clear_canvas()
    assert canvas.sh == pytest.approx(600)
    assert not canvas.trace_items
    assert not canvas.pen_is_down
    _sample(canvas, .8, .8)
    assert not canvas.trace_items


@pytest.mark.parametrize('nx,ny,mx,my', [(float('nan'), .2, 100, 100),
                                      (.2, float('inf'), 100, 100),
                                      (2, .2, 100, 100),
                                      (.2, .2, 0, 100)])
def test_bad_preview_breaks_the_trace(canvas, nx, ny, mx, my):
    _sample(canvas, .1, .1)
    _sample(canvas, .2, .2)
    canvas.update_state(nx, ny, True, mx, my)
    _sample(canvas, .8, .8)
    assert len(canvas.trace_items) == 1
    _sample(canvas, .9, .9)
    assert canvas.trace_items[-1].path().elementAt(0).x == pytest.approx(
        .8 * canvas.sw)


def test_long_recording_is_bounded_and_renders_on_real_qt_scene(canvas, app):
    for i in range(60000):
        _sample(canvas, (i % 1000) / 1000, ((i * 17) % 1000) / 1000)
    # The canvas retains enough recent history to view movement without
    # retaining every stroke or creating one QGraphicsItem per sample.
    assert len(canvas.trace_items) <= canvas.MAX_TRACE_ITEMS
    assert len(canvas.scene.items()) <= canvas.MAX_TRACE_ITEMS + 3
    assert len(canvas.current_stroke) <= 2
    assert all(item.path().elementCount() <= canvas.PATH_BATCH_SEGMENTS + 1
               for item in canvas.trace_items)
    image = QImage(640, 400, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    canvas.scene.render(painter)
    painter.end()
    assert not image.isNull()
    assert any(image.pixelColor(x, y).alpha() > 0
               for x in range(0, 640, 20) for y in range(0, 400, 20))
    app.processEvents()
