"""Normal Qt workflows with only the external HID stream and game state replaced."""
import json
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtWidgets import QApplication

import otd_area_calculator as app_module
import tablet_input


class ExternalHID(QObject):
    packets_received = Signal(list)
    connection_status = Signal(bool)
    finished = Signal()

    def __init__(self, info):
        super().__init__()
        self.active = False
        self.hold_stop = False
        self.last_error = ''

    def start(self):
        self.active = True
        self.connection_status.emit(True)

    def stop(self, timeout_ms=1000):
        if not self.active:
            return True
        if self.hold_stop:
            return False
        self.finish()
        return True

    def finish(self):
        if self.active:
            self.active = False
            self.finished.emit()


@pytest.fixture
def window(monkeypatch):
    application = QApplication.instance() or QApplication([])
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(tablet_input.time, 'monotonic', lambda: clock.value)
    spec = app_module.TABLET_SPECS[(0x056A, 0x0374)].copy()
    spec.update(vendor_id=0x056A, product_id=0x0374, capture_supported=True)
    monkeypatch.setattr(app_module.TabletDetector, 'detect', lambda: spec.copy())
    monkeypatch.setattr(app_module, 'HIDPollingThread', ExternalHID)
    w = app_module.ApplicationWindow()
    w.test_clock = clock
    yield w
    w.hid_thread.hold_stop = False
    w.close()
    application.processEvents()


def feed(w, positions=None, pressure=0):
    if positions is None:
        positions = [(x, y) for x in range(4000, 6001, 200)
                     for y in range(3000, 4001, 200)]
    batch = []
    for x, y in positions:
        w.test_clock.value += .010
        batch.append({'x': x, 'y': y, 'pressure': pressure,
                      'in_range': True, 'capture_time': w.test_clock.value})
    w.hid_thread.packets_received.emit(batch)


def completed_recording(w):
    w._start_rec()
    feed(w)
    w._stop_rec()
    w._calc()
    assert w.last_cfg is not None
    assert w.btn_copy.isEnabled()


def test_manual_hover_record_calculate_and_copy_native_area(window):
    completed_recording(window)
    result = window.last_cfg
    assert result['width_mm'] > 0 and result['height_mm'] > 0
    assert result['width_mm'] / result['height_mm'] == pytest.approx(16 / 9)
    window._copy_cfg()
    copied = json.loads(QApplication.clipboard().text())
    assert copied == {'Width': result['width_mm'], 'Height': result['height_mm'],
                      'XPosition': result['x_offset_mm'],
                      'YPosition': result['y_offset_mm'], 'Rotation': 0}


def test_mode_changes_keep_a_running_session_stoppable(window):
    window._start_rec()
    feed(window)
    window.chk_auto.setChecked(True)
    window.chk_auto.setChecked(False)
    assert window.recorder.recording
    assert window.btn_stop.isEnabled()
    assert not window.btn_rec.isEnabled()
    window._stop_rec()
    assert not window.recorder.recording
    assert window.btn_rec.isEnabled()


def test_auto_next_song_retains_samples_and_invalidates_previous_result(window, monkeypatch):
    game = SimpleNamespace(playing=True)
    monkeypatch.setattr(app_module.OsuDetector, 'is_playing',
                        lambda: (game.playing, 'osu! - Song' if game.playing else 'osu!'))
    window.chk_auto.setChecked(True)
    window._check_osu()
    feed(window)
    game.playing = False
    window._check_osu()
    assert window.last_cfg is not None
    old_points = window.recorder.raw_points.copy()
    game.playing = True
    window._check_osu()
    assert window.recorder.recording
    assert window.recorder.raw_points == old_points
    assert window.last_cfg is None
    assert not window.btn_copy.isEnabled()
    feed(window, [(x, y) for x in range(7000, 8001, 100)
                  for y in range(5000, 6001, 100)])
    game.playing = False
    window._check_osu()
    assert window.last_cfg is not None
    assert len(window.recorder.raw_points) > len(old_points)


def test_refresh_during_auto_play_restarts_with_new_data(window, monkeypatch):
    completed_recording(window)
    window.chk_auto.setChecked(True)
    monkeypatch.setattr(app_module.OsuDetector, 'is_playing', lambda: (True, 'osu! - Song'))
    window._check_osu()
    old_recorder = window.recorder
    window._refresh()
    assert window.recorder is not old_recorder
    assert not window.recorder.raw_points
    assert window.last_cfg is None
    assert not window.btn_copy.isEnabled()
    window._check_osu()
    assert window.recorder.recording


def test_old_worker_signals_do_not_pollute_refreshed_session(window):
    old_worker = window.hid_thread
    window._refresh()
    old_worker.connection_status.emit(False)
    old_worker.packets_received.emit([{'x': 5000, 'y': 4000, 'pressure': 5,
                                      'in_range': True, 'capture_time': 200.0}])
    assert window._connected
    assert not window.recorder.raw_points


def test_disconnect_stops_recording_and_invalidates_result(window):
    completed_recording(window)
    window._start_rec()
    feed(window)
    window.hid_thread.last_error = '測試裝置斷線'
    window.hid_thread.connection_status.emit(False)
    assert not window.recorder.recording
    assert not window.btn_rec.isEnabled()
    assert not window.btn_stop.isEnabled()
    assert not window.btn_copy.isEnabled()
    assert window.last_cfg is None
    assert '斷線' in window.lbl_status.text()
    assert window.recorder.raw_points


@pytest.mark.parametrize('text', ['-16:9', '0', 'nan', 'inf', '16:0', 'bad'])
def test_invalid_ratio_never_leaves_a_copyable_result(window, text):
    completed_recording(window)
    window.inp_ratio.setText(text)
    window._calc()
    assert window.last_cfg is None
    assert not window.btn_copy.isEnabled()
    assert window.canvas.overlay_item is None
    assert '比例' in window.lbl_status.text()


def test_stationary_capture_is_not_a_zero_size_recommendation(window):
    window._start_rec()
    feed(window, [(5000, 3500)] * 30, pressure=5)
    window._stop_rec()
    window._calc()
    assert window.last_cfg is None
    assert not window.btn_copy.isEnabled()


def test_change_hover_condition_resets_previous_data_and_result(window):
    completed_recording(window)
    window.chk_hover.setChecked(False)
    assert not window.recorder.raw_points
    assert window.last_cfg is None
    window._start_rec()
    feed(window, pressure=0)
    assert not window.recorder.raw_points
    feed(window, pressure=5)
    assert window.recorder.raw_points


def test_refresh_waits_without_destroying_a_busy_worker(window):
    old_worker = window.hid_thread
    old_worker.hold_stop = True
    window._refresh()
    assert window._transitioning
    assert window.hid_thread is old_worker
    assert not window.btn_rec.isEnabled()
    old_worker.finish()
    assert not window._transitioning
    assert window.hid_thread is not old_worker
    assert window._connected


def test_close_finishes_when_external_device_releases(window):
    window.show()
    window.hid_thread.hold_stop = True
    window.close()
    assert window.isVisible()
    assert window._close_pending
    window.hid_thread.finish()
    assert not window.isVisible()


def test_unverified_result_can_be_explained_without_crashing(window):
    window.tablet_info['verified'] = False
    completed_recording(window)
    assert '尚未經實機驗證' in window.lbl_status.text()


def test_numeric_result_is_not_clipped_in_a_short_window(window):
    window.resize(960, 650)
    window.show()
    QApplication.processEvents()
    completed_recording(window)
    QApplication.processEvents()
    QApplication.processEvents()
    assert window.lbl_status.height() >= window.lbl_status.heightForWidth(window.lbl_status.width())
    pos = window.lbl_status.mapTo(window._left_scroll.viewport(), QPoint(0, 0))
    assert pos.y() >= 0
    assert pos.y() + window.lbl_status.height() <= window._left_scroll.viewport().height()
    assert '中心 X' in window.lbl_status.text()
    assert '中心 Y' in window.lbl_status.text()


def test_editing_settings_replaces_stale_result_text(window):
    completed_recording(window)
    window.inp_ratio.setText('4:3')
    assert window.last_cfg is None
    assert '已失效' in window.lbl_status.text()
