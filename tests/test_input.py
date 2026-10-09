"""Real reader/recorder paths with hardware, clock and window-system substitutes."""

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

import tablet_input as ti


def spec():
    return dict(ti.TABLET_SPECS[ti.CTL4100], vendor_id=ti.CTL4100[0], product_id=ti.CTL4100[1])


def interface(path=b'pen', page=0xFF0D, usage=1):
    return {'vendor_id': ti.CTL4100[0], 'product_id': ti.CTL4100[1],
            'usage_page': page, 'usage': usage, 'path': path}


def report(x=1000, y=2000, pressure=0, in_range=True):
    # Documented CTL-4100 native 192-byte 0x10 report, not a fake parser.
    data = bytearray(192)
    data[0] = 0x10
    data[1] = 0x20 if in_range else 0
    data[2:5] = x.to_bytes(3, 'little')
    data[5:8] = y.to_bytes(3, 'little')
    data[8:10] = pressure.to_bytes(2, 'little')
    return list(data)


class Hardware:
    def __init__(self, reports=(), endless=False, disconnect=False, block_open=None):
        self.reports = iter(reports)
        self.endless = endless
        self.disconnect = disconnect
        self.block_open = block_open
        self.opened = threading.Event()
        self.read_started = threading.Event()
        self.closed = False
        self.close_thread = None
        self.path = None

    def open_path(self, path):
        self.path = path
        self.opened.set()
        if self.block_open:
            self.block_open.wait(1)

    def set_nonblocking(self, value):
        pass

    def read(self, size):
        assert size >= 192  # Don't truncate CTL-4100's actual report.
        self.read_started.set()
        if self.endless:
            return report()
        try:
            return next(self.reports)
        except StopIteration:
            if self.disconnect:
                raise OSError('device disconnected')
            return []

    def close(self):
        self.closed = True
        self.close_thread = threading.get_ident()


def install_hardware(monkeypatch, hardware, interfaces=None):
    monkeypatch.setattr(ti.hid, 'enumerate', lambda *args: interfaces or [interface()])
    monkeypatch.setattr(ti.hid, 'device', lambda: hardware)


def direct(signal, callback):
    signal.connect(callback, Qt.ConnectionType.DirectConnection)


def test_detector_prioritizes_verified_model_and_supported_interface(monkeypatch):
    devices = [dict(interface(b'unknown'), product_id=0xFFFF),
               dict(interface(b'otherbrand'), vendor_id=0x256C, product_id=0x006E),
               interface(b'aux', page=0xFF00, usage=0x0B), interface()]
    monkeypatch.setattr(ti.hid, 'enumerate', lambda *args: devices)
    selected = ti.TabletDetector.detect()
    assert selected['path'] == b'pen'
    assert selected['verified'] and selected['capture_supported']


def test_unsupported_known_device_does_not_open_or_write_features(monkeypatch):
    info = dict(ti.TABLET_SPECS[(0x056A, 0x037A)], vendor_id=0x056A, product_id=0x037A)
    monkeypatch.setattr(ti.hid, 'device', lambda: pytest.fail('unsupported device was opened'))
    reader = ti.HIDPollingThread(info)
    statuses = []
    direct(reader.connection_status, statuses.append)
    reader.run()
    assert statuses == [False]
    assert '尚未支援' in reader.error_message


def test_parser_handles_native_driver_hover_and_out_of_range():
    reader = ti.HIDPollingThread(spec())
    packet = reader.parse_packet(report(x=15200, y=9500), 10)
    assert (packet['x'], packet['y'], packet['pressure'], packet['in_range'], packet['contact']) == (15200, 9500, 0, True, False)
    assert packet['capture_time'] == 10
    assert reader.parse_packet(report(pressure=100))['contact']
    assert not reader.parse_packet(report(in_range=False))['in_range']
    reader._driver_prefix = True
    assert reader.parse_packet([0] + report(pressure=100))['contact']


@pytest.mark.parametrize('data', [[0x10] + [0] * 7, [0x10] + [0] * 8,
                                 report(y=65280), report(pressure=65535), [0x11] + [0] * 191])
def test_malformed_or_unusable_reports_do_not_become_points(data):
    assert ti.HIDPollingThread(spec()).parse_packet(data) is None


def test_raw_hid_batch_reaches_recorder_and_disconnect_closes_on_reader_thread(monkeypatch):
    hardware = Hardware([report(pressure=100), report(1100, 2100), report(in_range=False)], disconnect=True)
    install_hardware(monkeypatch, hardware)
    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    reader = ti.HIDPollingThread(spec())
    batches, statuses, previews = [], [], []
    direct(reader.packets_received, lambda batch: (batches.append(batch), recorder.on_packets(batch)))
    direct(reader.connection_status, statuses.append)
    recorder.preview_updated.connect(lambda *args: previews.append(args))
    direct(reader.packet_received, lambda packet: pytest.fail('duplicate single packet'))
    caller_thread = threading.get_ident()
    reader.start()
    assert reader.wait(1000)
    assert statuses == [True, False]
    assert len(batches) == 1 and len(batches[0]) == 3
    assert len({p['capture_time'] for p in batches[0]}) == 3
    assert recorder.get_data()[0]  # Last valid batch survives the read failure.
    assert hardware.closed and hardware.close_thread != caller_thread
    assert 'device disconnected' in reader.error_message


def test_buffered_hid_hover_burst_keeps_whole_area_for_calculation(monkeypatch):
    from area_analysis import ModernCalculationEngine

    positions = [(x, y) for x in range(4000, 6001, 200)
                 for y in range(3000, 4001, 200)]
    hardware = Hardware([report(x, y) for x, y in positions])
    install_hardware(monkeypatch, hardware)
    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    reader = ti.HIDPollingThread(spec())
    received = []
    delivered = threading.Event()

    def record_batch(batch):
        recorder.on_packets(batch)
        received.extend(batch)
        if len(received) == len(positions):
            delivered.set()

    direct(reader.packets_received, record_batch)
    reader.start()
    try:
        assert delivered.wait(1), 'The real reader did not deliver the buffered burst'
    finally:
        assert reader.stop(500)
    count, _ = recorder.stop_recording()
    data, max_x, max_y = recorder.get_data()
    # Real host monotonic capture timestamps, obtained rapidly while draining
    # the HID buffer, must never act as a spatial-data sampling filter.
    assert reader.valid_packets_received == 66
    assert count == 66
    assert {(p['x'], p['y']) for p in data} == set(positions)
    result = ModernCalculationEngine.calculate(data, max_x, max_y, 152, 95)
    assert result is not None
    assert result['width_mm'] == pytest.approx(20)
    assert result['height_mm'] == pytest.approx(10)
    assert result['x_offset_mm'] == pytest.approx(50)
    assert result['y_offset_mm'] == pytest.approx(35)


def test_continuously_nonempty_buffer_still_stops_with_bounded_wait(monkeypatch):
    hardware = Hardware(endless=True)
    install_hardware(monkeypatch, hardware)
    reader = ti.HIDPollingThread(spec())
    reader.start()
    assert hardware.read_started.wait(1)
    started = time.monotonic()
    assert reader.stop(500)
    assert time.monotonic() - started < .7
    assert hardware.closed


def test_stop_during_open_is_not_overwritten_and_timeout_is_reported(monkeypatch):
    release = threading.Event()
    hardware = Hardware(endless=True, block_open=release)
    install_hardware(monkeypatch, hardware)
    reader = ti.HIDPollingThread(spec())
    reader.start()
    assert hardware.opened.wait(1)
    try:
        assert reader.stop(10) is False
    finally:
        release.set()
    assert reader.wait(1000)
    assert not hardware.read_started.is_set()
    assert hardware.closed


def test_immediate_stop_never_restarts_reader(monkeypatch):
    hardware = Hardware(endless=True)
    install_hardware(monkeypatch, hardware)
    for _ in range(10):
        reader = ti.HIDPollingThread(spec())
        reader.start()
        assert reader.stop(500)
        assert not reader.running


def test_legacy_reader_emits_single_packets_without_batch_subscriber(monkeypatch):
    hardware = Hardware([report()], disconnect=True)
    install_hardware(monkeypatch, hardware)
    reader = ti.HIDPollingThread(spec())
    packets = []
    direct(reader.packet_received, packets.append)
    reader.run()
    assert len(packets) == 1


@pytest.fixture
def clock(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(ti.time, 'monotonic', lambda: now[0])
    return now


def packet(x=1000, y=2000, pressure=0, in_range=True, captured=10.0):
    return {'x': x, 'y': y, 'pressure': pressure, 'in_range': in_range, 'capture_time': captured}


def test_hover_contact_and_proximity_have_separate_recording_and_preview_states(clock):
    recorder = ti.SmartRecorder(spec())
    previews, states = [], []
    recorder.preview_updated.connect(lambda *args: previews.append(args))
    recorder.state_updated.connect(lambda *args: states.append(args))
    recorder.start_recording()
    recorder.on_packets([packet(), packet(1100, pressure=100, captured=10.01),
                         packet(in_range=False, captured=10.02)])
    points, max_x, max_y = recorder.get_data()
    assert points == [{'x': 1000, 'y': 2000}, {'x': 1100, 'y': 2000}]
    assert (max_x, max_y) == (15200, 9500)
    assert [(p[2], p[3]) for p in previews] == [(True, False), (True, True), (False, False)]
    assert [state[2] for state in states] == [False, True, False]
    recorder.clear_all()
    recorder.record_hover = False
    recorder.on_packets([packet(), packet(1100, pressure=100, captured=10.01)])
    assert recorder.get_data()[0] == [{'x': 1100, 'y': 2000}]


def test_noise_never_changes_physical_scale_and_origin_is_valid(clock):
    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    recorder.on_packets([packet(0, 0), packet(y=65280, captured=10.01),
                         packet(x=float('nan'), captured=10.02), packet(1100, 3000, captured=10.03)])
    assert recorder.get_data() == ([{'x': 0, 'y': 0}, {'x': 1100, 'y': 3000}], 15200, 9500)


def test_captured_timestamps_keep_batch_samples_distinct_and_reject_queued_old_session(clock):
    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    recorder.on_packets([packet(captured=9.9), packet(captured=10),
                         packet(1200, captured=10.004), packet(1400, captured=10.008)])
    assert len(recorder.get_data()[0]) == 3
    clock[0] = 11
    recorder.clear_all()
    recorder.on_packet(packet(1500, captured=10.9))
    assert recorder.get_data()[0] == []
    recorder.on_packet(packet(1500, captured=11.0))
    assert recorder.get_data()[0] == [{'x': 1500, 'y': 2000}]


def test_next_song_accumulates_valid_points_and_recorded_duration_only(clock):
    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    recorder.on_packet(packet())
    clock[0] = 12
    assert recorder.stop_recording() == (1, 2)
    clock[0] = 20
    assert recorder.stop_recording() == (1, 2)
    recorder.start_recording(clear=False)
    recorder.on_packets([packet(1500, captured=19), packet(1600, captured=20)])
    clock[0] = 23
    assert recorder.stop_recording() == (2, 5)
    assert recorder.get_data()[0] == [{'x': 1000, 'y': 2000}, {'x': 1600, 'y': 2000}]
    recorder.start_recording()
    assert recorder.get_data()[0] == []
    assert recorder.last_emit_time is None


def test_hours_of_later_song_data_plateau_without_losing_earlier_area(clock):
    from area_analysis import ModernCalculationEngine

    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    early = [{'x': x, 'y': y} for x in range(4000, 6001, 100)
             for y in range(3000, 4001, 100)]
    recorder.on_packets([packet(point['x'], point['y'], captured=10 + index * .004)
                         for index, point in enumerate(early)])
    clock[0] = 12
    recorder.stop_recording()
    recorder.start_recording(clear=False)
    later = [(x, y) for x in range(4500, 5501, 100) for y in range(3300, 3701, 100)]
    captured = 12.0

    def replay(rounds):
        nonlocal captured
        for _ in range(rounds):
            batch = []
            for x, y in later:
                captured += .25
                batch.append(packet(x, y, captured=captured))
            recorder.on_packets(batch)

    replay(20)  # All later-song buckets have reached their representative limit.
    retained = len(recorder.raw_points)
    replay(2000)  # More than seven hours of later observations, without real waits.
    assert len(recorder.raw_points) == retained
    assert recorder.recording  # Capacity never forces the session to stop.
    points, max_x, max_y = recorder.get_data()
    assert min(p['x'] for p in points) == 4000
    assert max(p['x'] for p in points) == 6000
    assert min(p['y'] for p in points) == 3000
    assert max(p['y'] for p in points) == 4000
    result = ModernCalculationEngine.calculate(points, max_x, max_y, 152, 95)
    assert result['width_mm'] == pytest.approx(20)
    assert result['height_mm'] == pytest.approx(10)
    assert result['x_offset_mm'] == pytest.approx(50)
    assert result['y_offset_mm'] == pytest.approx(35)


def test_single_cell_small_motion_remains_calculable_after_compaction(clock):
    from area_analysis import ModernCalculationEngine

    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    # This entire real-sized 0.09 x 0.10 mm path occupies a single spatial cell.
    positions = [(990 + i, 1990 + i) for i in range(10)]
    recorder.on_packets([packet(x, y, captured=10 + index * .004)
                         for index, (x, y) in enumerate(positions * 1000)])
    # A newly reached boundary must update the extrema even once the cell is full.
    recorder.on_packets([packet(989, 1989, captured=51), packet(999, 2000, captured=51.01)])
    data, max_x, max_y = recorder.get_data()
    assert len(data) == 10
    result = ModernCalculationEngine.calculate(data, max_x, max_y, 152, 95)
    assert result is not None
    assert result['width_mm'] == pytest.approx(.10)
    assert result['height_mm'] == pytest.approx(.11)
    recorder.clear_all()
    assert recorder.get_data()[0] == []
    recorder.on_packets([packet(995, 1995, captured=52)])
    assert recorder.get_data()[0] == [{'x': 995, 'y': 1995}]


def test_compaction_preserves_axis_occupancy_and_exact_bounds_through_iqr(clock):
    from area_analysis import ModernCalculationEngine

    recorder = ti.SmartRecorder(spec())
    recorder.start_recording()
    samples = [{'x': x, 'y': y} for x in range(4000, 6001, 50)
               for y in range(3000, 4001, 50)]
    # Plausible but isolated far points are still rejected by the real analysis.
    samples += [{'x': 10, 'y': 10}, {'x': 15190, 'y': 9490}]
    for repeat in range(20):
        recorder.on_packets([packet(p['x'], p['y'], captured=10 + (repeat * len(samples) + i) * .004)
                             for i, p in enumerate(samples)])
    data, max_x, max_y = recorder.get_data()
    expected = ModernCalculationEngine.calculate(samples, max_x, max_y, 152, 95)
    actual = ModernCalculationEngine.calculate(data, max_x, max_y, 152, 95)
    for name in ('width_mm', 'height_mm', 'x_offset_mm', 'y_offset_mm'):
        assert actual[name] == pytest.approx(expected[name])
    for axis, extent in (('x', max_x), ('y', max_y)):
        original_bands = {min(int(p[axis] / extent * 256), 255) for p in samples}
        retained_bands = {min(int(p[axis] / extent * 256), 255) for p in data}
        assert retained_bands == original_bands
    assert len(data) <= recorder.MAX_RETAINED_POINTS


def install_windows(monkeypatch, windows):
    gui = SimpleNamespace(EnumWindows=lambda callback, result: [callback(hwnd, result) for hwnd in windows],
                          IsWindowVisible=lambda hwnd: windows[hwnd][2],
                          GetWindowText=lambda hwnd: windows[hwnd][0])
    monkeypatch.setattr(ti, 'win32gui', gui)
    monkeypatch.setattr(ti.OsuDetector, '_process_name', lambda hwnd: windows[hwnd][1])


def test_browser_titles_and_non_osu_owners_never_trigger_recording(monkeypatch):
    install_windows(monkeypatch, {1: ('osu! - Wikipedia - Browser', 'chrome.exe', True),
                                  2: ('osu! - fake song', 'notepad.exe', True),
                                  3: ('osu!', 'osu!.exe', True)})
    assert ti.OsuDetector.is_playing() == (False, 'osu!')


def test_real_osu_song_window_wins_over_menu_and_hidden_windows(monkeypatch):
    title = 'osu! - Artist - Song [Hard]'
    install_windows(monkeypatch, {1: ('osu!', 'osu!.exe', True),
                                  2: (title, 'osu!.exe', True),
                                  3: ('osu! - hidden song', 'osu!.exe', False)})
    assert ti.OsuDetector.is_playing() == (True, title)
