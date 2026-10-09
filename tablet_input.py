"""Hardware input and recording; tablet geometry comes from trusted specifications.

CTL-4100 USB format and proximity are documented by OpenTabletDriver:
Configurations/Wacom/CTL-4100.json and Parsers/Wacom/IntuosV2/IntuosV2Report.cs
in https://github.com/OpenTabletDriver/OpenTabletDriver/tree/0.6.x .
The Wacom-driver interface prefixes the same report with one byte.
"""

import ctypes
import json
import math
import os
import sys
import threading
import time

import hid
from PySide6.QtCore import QObject, QThread, Signal, SIGNAL

try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None


WACOM_VENDOR_ID = 0x056A
CTL4100 = (WACOM_VENDOR_ID, 0x0374)
_BUILTIN_SPECS = {
    CTL4100: {'name': 'Wacom Intuos S (CTL-4100)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0, 'verified': True},
    (0x056A, 0x0375): {'name': 'Wacom Intuos M (CTL-6100)', 'max_x': 21600, 'max_y': 13500, 'width_mm': 216.0, 'height_mm': 135.0, 'verified': False},
    (0x056A, 0x0376): {'name': 'Wacom Intuos S BT (CTL-4100WL)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0, 'verified': False},
    (0x056A, 0x037A): {'name': 'Wacom One S (CTL-472)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0, 'verified': False},
    (0x056A, 0x037B): {'name': 'Wacom One M (CTL-672)', 'max_x': 21600, 'max_y': 13500, 'width_mm': 216.0, 'height_mm': 135.0, 'verified': False},
}


def _tablet_db_path():
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'tablet_db.json')


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _load_tablet_db():
    """Load valid breadth specs; a database entry never implies parser support."""
    specs = {}
    try:
        with open(_tablet_db_path(), encoding='utf-8') as fh:
            entries = json.load(fh).get('tablets', {})
        if not isinstance(entries, dict):
            return specs
        for key, entry in entries.items():
            try:
                vid, pid = (int(part) for part in key.split(','))
                if not (0 < vid <= 65535 and 0 < pid <= 65535) or not isinstance(entry, dict):
                    continue
                values = {name: _positive(entry.get(name))
                          for name in ('max_x', 'max_y', 'width_mm', 'height_mm')}
                if any(value is None for value in values.values()):
                    continue
                specs[(vid, pid)] = dict(values, name=entry.get('name', f'Tablet {key}'), verified=False)
            except (ValueError, TypeError, AttributeError):
                continue
    except (OSError, ValueError, AttributeError):
        pass
    return specs


def _build_tablet_specs():
    specs = _load_tablet_db()
    specs.update(_BUILTIN_SPECS)
    return specs


TABLET_SPECS = _build_tablet_specs()


def _supported_interface(device):
    """Restrict capture to a documented model/interface combination."""
    if (device.get('vendor_id'), device.get('product_id')) != CTL4100:
        return False
    return (device.get('usage_page'), device.get('usage')) in ((0xFF0D, 1), (0xFF00, 0x0A))


class TabletDetector:
    @staticmethod
    def detect():
        try:
            devices = hid.enumerate(0, 0)
        except Exception as exc:
            info = TabletDetector._unknown()
            info['capture_error'] = f'無法列舉 HID 裝置：{exc}'
            return info
        # Do not deduplicate VID/PID before considering every interface.
        candidates = [d for d in devices if (d.get('vendor_id'), d.get('product_id')) in TABLET_SPECS]
        candidates.sort(key=lambda d: (
            not _supported_interface(d),
            not TABLET_SPECS[(d['vendor_id'], d['product_id'])].get('verified', False),
            d.get('usage_page') != 0xFF0D,
        ))
        if candidates:
            device = candidates[0]
            info = TABLET_SPECS[(device['vendor_id'], device['product_id'])].copy()
            for key in ('vendor_id', 'product_id', 'path', 'usage_page', 'usage', 'interface_number'):
                info[key] = device.get(key)
            info['capture_supported'] = _supported_interface(device)
            info['capture_error'] = '' if info['capture_supported'] else '已辨識規格，但尚未支援此裝置／介面的封包格式'
            return info
        for device in devices:
            if device.get('vendor_id') == WACOM_VENDOR_ID:
                info = TabletDetector._unknown()
                info.update(name=f"Wacom {device.get('product_string') or hex(device.get('product_id', 0))} (Unknown)",
                            vendor_id=WACOM_VENDOR_ID, product_id=device.get('product_id'),
                            capture_error='未知 Wacom 型號，無可信尺度與封包格式，無法錄製')
                return info
        return TabletDetector._unknown()

    @staticmethod
    def _unknown():
        return {'name': '未偵測到手寫板', 'vendor_id': None, 'product_id': None,
                'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0,
                'verified': False, 'capture_supported': False, 'capture_error': '未偵測到可錄製的手寫板'}


class HIDPollingThread(QThread):
    packet_received = Signal(dict)
    packets_received = Signal(list)
    connection_status = Signal(bool)

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info.copy()
        self.device = None
        self.running = True
        self._stop_requested = threading.Event()
        self._driver_prefix = False
        self.error_message = ''
        self.valid_packets_received = 0

    @property
    def last_error(self):
        return self.error_message

    def start(self, priority=QThread.Priority.InheritPriority):
        if self.isRunning():
            return
        # Reset before launching, never in run(): an immediate stop stays set.
        self._stop_requested.clear()
        self.running = True
        super().start(priority)

    def _active(self):
        return self.running and not self._stop_requested.is_set()

    def open_device(self):
        vid, pid = self.tablet_info.get('vendor_id'), self.tablet_info.get('product_id')
        if (vid, pid) != CTL4100:
            self.error_message = '此裝置尚未支援封包解析，無法錄製'
            return False
        try:
            interfaces = [d for d in hid.enumerate(vid, pid) if _supported_interface(d) and d.get('path')]
            selected_path = self.tablet_info.get('path')
            interfaces.sort(key=lambda d: (d.get('path') != selected_path, d.get('usage_page') != 0xFF0D))
            for interface in interfaces:
                if not self._active():
                    return False
                device = hid.device()
                try:
                    device.open_path(interface['path'])
                    device.set_nonblocking(True)
                    self.device = device
                    self._driver_prefix = interface.get('usage_page') == 0xFF00
                    self.error_message = ''
                    # CTL-4100 uses its native reports without mode-changing
                    # feature writes (OTD CTL-4100 config has no initialization).
                    return True
                except Exception as exc:
                    self.error_message = f'無法開啟手寫板：{exc}'
                    try:
                        device.close()
                    except Exception:
                        pass
            if not self.error_message:
                self.error_message = '找不到支援的 CTL-4100 筆資料介面'
        except Exception as exc:
            self.error_message = f'無法列舉手寫板介面：{exc}'
        return False

    def parse_packet(self, data, capture_time=None):
        """Decode only the documented pen report, including out-of-range edges."""
        if self._driver_prefix:
            data = data[1:]
        if len(data) < 10 or data[0] != 0x10:
            return None
        x = data[2] | (data[3] << 8) | (data[4] << 16)
        y = data[5] | (data[6] << 8) | (data[7] << 16)
        pressure = data[8] | (data[9] << 8)
        in_range = bool(data[1] & 0x20)
        if pressure > 4095:
            return None
        if in_range and not (0 <= x <= self.tablet_info['max_x'] and 0 <= y <= self.tablet_info['max_y']):
            return None
        return {'x': x, 'y': y, 'pressure': pressure, 'in_range': in_range,
                'contact': in_range and pressure > 0,
                'capture_time': time.monotonic() if capture_time is None else capture_time}

    def _emit_batch(self, batch):
        if not batch:
            return
        self.valid_packets_received += len(batch)
        if self.receivers(SIGNAL('packets_received(QVariantList)')):
            self.packets_received.emit(batch)
        else:
            # Preserve old callers without flooding the new batch caller twice.
            for packet in batch:
                self.packet_received.emit(packet)

    def run(self):
        batch = []
        try:
            if not self._active() or not self.open_device():
                self.connection_status.emit(False)
                return
            if not self._active():
                return
            self.connection_status.emit(True)  # Opened; not proof of pen reports.
            while self._active():
                batch = []
                started = time.monotonic()
                while self._active() and len(batch) < 64 and time.monotonic() - started < .008:
                    data = self.device.read(193)
                    if not data:
                        break
                    packet = self.parse_packet(data, time.monotonic())
                    if packet is not None:
                        batch.append(packet)
                if self._active():
                    self._emit_batch(batch)
                self._stop_requested.wait(.001)
        except Exception as exc:
            if self._active():
                self._emit_batch(batch)
            self.error_message = f'手寫板讀取中斷：{exc}'
            self.connection_status.emit(False)
        finally:
            self.running = False
            if self.device is not None:
                try:
                    self.device.close()
                except Exception:
                    pass
                self.device = None

    def stop(self, timeout_ms=1000):
        self.running = False
        self._stop_requested.set()
        if QThread.currentThread() == self:
            return False
        return self.wait(max(0, int(timeout_ms)))


class OsuDetector:
    @staticmethod
    def _process_name(hwnd):
        """Verify the actual window owner, not a browser title containing osu!."""
        if not win32process or os.name != 'nt':
            return None
        handle = None
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong))
            kernel.QueryFullProcessImageNameW.restype = ctypes.c_bool
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buf))
            if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        except Exception:
            return None
        finally:
            if handle:
                kernel.CloseHandle(handle)
        return None

    @staticmethod
    def get_osu_title():
        if not win32gui:
            return None
        try:
            titles = []
            def callback(hwnd, result):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd)
                if (title == 'osu!' or title.startswith('osu! - ')) and OsuDetector._process_name(hwnd) in ('osu!.exe', 'osu.exe'):
                    result.append(title)
            win32gui.EnumWindows(callback, titles)
            return next((title for title in titles if title.startswith('osu! - ')), titles[0] if titles else None)
        except Exception:
            return None

    @staticmethod
    def is_playing():
        title = OsuDetector.get_osu_title()
        if not title:
            return False, 'Not found'
        return title.startswith('osu! - ') and bool(title[7:].strip()), title


class SmartRecorder(QObject):
    # Match the analysis engine's occupancy strips. Each occupied 2D cell
    # retains its exact axis extrema, so filtering whole strips still sees
    # exactly the same occupied bands and surviving rectangle bounds.
    SPATIAL_BANDS = 256
    POINTS_PER_CELL = 10
    MAX_RETAINED_POINTS = SPATIAL_BANDS * SPATIAL_BANDS * POINTS_PER_CELL
    # Third argument retains the physical contact (pressure) contract.
    state_updated = Signal(float, float, bool, float, float)
    preview_updated = Signal(float, float, bool, bool, float, float)
    recording_cleared = Signal()

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info.copy()
        self.recording = False
        self.record_hover = True
        self.raw_points = []
        self._spatial_cells = {}
        self.start_time = None
        self._duration = 0.0
        # Compatibility names, fixed to authoritative extents; never self-learn
        # a whole-tablet scale from a player's activity or malformed sample.
        self.dyn_max_x = _positive(tablet_info.get('max_x')) or 0.0
        self.dyn_max_y = _positive(tablet_info.get('max_y')) or 0.0
        self.last_emit_time = None
        self.pen_down_state = False
        self._ui_last_emit = None
        self._ui_last_down = False
        self._ui_last_in_range = False
        self._last_position = (0.0, 0.0)
        self._minimum_capture_time = None

    def start_recording(self, clear=True):
        if clear:
            self.clear_all()
        if self.recording:
            return
        self.start_time = time.monotonic()
        self.recording = True
        self.last_emit_time = None
        self.pen_down_state = False

    def stop_recording(self):
        if self.recording and self.start_time is not None:
            self._duration += max(0.0, time.monotonic() - self.start_time)
        self.recording = False
        self.start_time = None
        self.pen_down_state = False
        return len(self.raw_points), self._duration

    def on_packets(self, batch):
        for packet in batch:
            self.on_packet(packet)

    def _retain_point(self, x, y):
        key = (min(int(x / self.dyn_max_x * self.SPATIAL_BANDS), self.SPATIAL_BANDS - 1),
               min(int(y / self.dyn_max_y * self.SPATIAL_BANDS), self.SPATIAL_BANDS - 1))
        indexes = self._spatial_cells.setdefault(key, [])
        point = {'x': x, 'y': y}
        if len(indexes) < self.POINTS_PER_CELL:
            indexes.append(len(self.raw_points))
            self.raw_points.append(point)
            if len(indexes) == self.POINTS_PER_CELL:
                initial = [self.raw_points[index] for index in indexes]
                extrema = (min(initial, key=lambda p: p['x']), max(initial, key=lambda p: p['x']),
                           min(initial, key=lambda p: p['y']), max(initial, key=lambda p: p['y']))
                for index, extreme in zip(indexes[-4:], extrema):
                    self.raw_points[index] = extreme
            return
        # Keep the first six observations (including repeat samples needed by
        # the analysis minimum), plus four extrema accumulated across all songs.
        for index, axis, lower in zip(indexes[-4:], ('x', 'x', 'y', 'y'), (True, False, True, False)):
            if (point[axis] < self.raw_points[index][axis] if lower else
                    point[axis] > self.raw_points[index][axis]):
                self.raw_points[index] = point

    def on_packet(self, packet):
        try:
            x, y, pressure = (float(packet[key]) for key in ('x', 'y', 'pressure'))
            captured = float(packet.get('capture_time', time.monotonic()))
            if not all(math.isfinite(value) for value in (x, y, pressure, captured)) or not 0 <= pressure <= 4095:
                return
        except (TypeError, ValueError, KeyError, OverflowError):
            return
        if self._minimum_capture_time is not None and captured < self._minimum_capture_time:
            return
        # Legacy callers lacking proximity may record contact, never invent hover.
        in_range = bool(packet.get('in_range', pressure > 0))
        contact = in_range and pressure > 0
        if not in_range:
            self.pen_down_state = False
            if self._ui_last_in_range:
                self._ui_last_down = False
                self._ui_last_in_range = False
                self.state_updated.emit(*self._last_position, False, self.dyn_max_x, self.dyn_max_y)
                self.preview_updated.emit(*self._last_position, False, False, self.dyn_max_x, self.dyn_max_y)
            return
        if not (self.dyn_max_x > 0 and self.dyn_max_y > 0 and 0 <= x <= self.dyn_max_x and 0 <= y <= self.dyn_max_y):
            return
        self.pen_down_state = contact
        if self.recording and (self.record_hover or contact) and captured >= self.start_time:
            # Buffered HID reports may be drained within microseconds even
            # though the pen visited distinct positions. Storage is already
            # spatially bounded; only preview rendering needs time throttling.
            self._retain_point(x, y)
            self.last_emit_time = captured
        self._last_position = (x / self.dyn_max_x, y / self.dyn_max_y)
        if not self._ui_last_in_range or contact != self._ui_last_down or self._ui_last_emit is None or captured - self._ui_last_emit >= .008:
            self._ui_last_emit = captured
            self._ui_last_down = contact
            self._ui_last_in_range = True
            self.state_updated.emit(*self._last_position, contact, self.dyn_max_x, self.dyn_max_y)
            self.preview_updated.emit(*self._last_position, True, contact, self.dyn_max_x, self.dyn_max_y)

    def get_data(self):
        return self.raw_points.copy(), self.dyn_max_x, self.dyn_max_y

    def clear_all(self):
        self.raw_points = []
        self._spatial_cells = {}
        self._duration = 0.0
        self.start_time = time.monotonic() if self.recording else None
        self.last_emit_time = None
        self.pen_down_state = False
        self._ui_last_emit = None
        self._ui_last_down = False
        self._ui_last_in_range = False
        self._last_position = (0.0, 0.0)
        self._minimum_capture_time = time.monotonic()
        self.recording_cleared.emit()
