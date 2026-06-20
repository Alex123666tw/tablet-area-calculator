"""
OTD 區域計算器 v1.0 (The True Auto-Calibrating Version)
A complete rewrite replacing all former versions.
Solves the "vertical squiggles" X-axis mapping issue via:
  1. Multi-threaded background HID polling (0 drops)
  2. Auto-calibrating physical boundaries (Dynamic Max X/Y detection)
  3. IQR Outlier Rejection instead of hard 2% cuts
  4. Real-time modern PySide6 responsive UI
"""

import sys
import os
import time
import json
import numpy as np
import hid

# Disable Qt stealing tablet/pen focus which breaks osu! raw input
os.environ["QT_WINTAB_DISABLE"] = "1"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
os.environ["QT_QPA_PLATFORM"] = "windows:nowmpointer"

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox, QCheckBox,
    QGraphicsView, QGraphicsScene, QSizePolicy, QScrollArea, QFrame, QSplitter
)
from PySide6.QtCore import QTimer, Signal, Qt, QObject, QThread
from PySide6.QtGui import QFont, QPen, QColor, QBrush, QPainter

try:
    import win32gui
except ImportError:
    win32gui = None

# ==================== Tablet Hardware Detection ====================
TABLET_SPECS = {
    # Wacom Intuos (CTL-4100 / 6100)
    (0x056A, 0x0374): {'name': 'Wacom Intuos S (CTL-4100)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0},
    (0x056A, 0x0375): {'name': 'Wacom Intuos M (CTL-6100)', 'max_x': 21600, 'max_y': 13500, 'width_mm': 216.0, 'height_mm': 135.0},
    (0x056A, 0x0376): {'name': 'Wacom Intuos S BT (CTL-4100WL)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0},
    # Wacom One (CTL-472 / 672)
    (0x056A, 0x037A): {'name': 'Wacom One S (CTL-472)', 'max_x': 15200, 'max_y': 9500, 'width_mm': 152.0, 'height_mm': 95.0},
    (0x056A, 0x037B): {'name': 'Wacom One M (CTL-672)', 'max_x': 21600, 'max_y': 13500, 'width_mm': 216.0, 'height_mm': 135.0},
}

WACOM_VENDOR_ID = 0x056A

class TabletDetector:
    @staticmethod
    def detect():
        try:
            all_devices = hid.enumerate(0, 0)
        except Exception as e:
            print(f"Error enumerating: {e}")
            return TabletDetector._unknown()

        seen = set()
        for d in all_devices:
            vid = d.get('vendor_id', 0)
            pid = d.get('product_id', 0)
            key = (vid, pid)
            if key in seen or vid == 0:
                continue
            seen.add(key)

            if key in TABLET_SPECS:
                spec = TABLET_SPECS[key].copy()
                spec['vendor_id'] = vid
                spec['product_id'] = pid
                print(f"[偵測] 找到: {spec['name']}")
                return spec

            if vid == WACOM_VENDOR_ID:
                product = d.get('product_string', f'0x{pid:04X}')
                return {
                    'name': f'Wacom {product} (Unknown)',
                    'vendor_id': vid, 'product_id': pid,
                    'max_x': 15200, 'max_y': 9500,
                    'width_mm': 152.0, 'height_mm': 95.0,
                }

        return TabletDetector._unknown()

    @staticmethod
    def _unknown():
        return {
            'name': '未偵測到手寫板',
            'vendor_id': None, 'product_id': None,
            'max_x': 15200, 'max_y': 9500,
            'width_mm': 152.0, 'height_mm': 95.0,
        }

# ==================== Multi-Threaded HID Engine ====================
class HIDPollingThread(QThread):
    packet_received = Signal(dict)
    connection_status = Signal(bool)

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        self.device = None
        self.running = False

    def open_device(self):
        vid = self.tablet_info.get('vendor_id')
        pid = self.tablet_info.get('product_id')

        if not vid or not pid:
            return False

        try:
            all_devices = hid.enumerate(vid, pid)
            digitizer_path = None
            all_paths = []

            for dev in all_devices:
                up = dev.get('usage_page', 0)
                path = dev.get('path', b'')
                all_paths.append(path)
                if up == 0x000D:  # Digitizer
                    digitizer_path = path
                    break

            self.device = hid.device()

            if digitizer_path:
                print(f"[HID] 使用 Digitizer 介面: {digitizer_path}")
                self.device.open_path(digitizer_path)
            else:
                opened = False
                for path in all_paths:
                    try:
                        self.device.open_path(path)
                        opened = True
                        print(f"[HID] 已打開介面: {path}")
                        break
                    except Exception:
                        continue
                if not opened:
                    self.device.open(vid, pid)

            self.device.set_nonblocking(True)  # Still use non-blocking, but poll fast

            try:
                self.device.send_feature_report([0x02, 0x02])
                print("[HID] Wacom Feature Report 發送成功")
            except Exception as e:
                print(f"[HID] Feature Report 發送略過: {e}")

            return True

        except Exception as e:
            print(f"[HID] 無法打開設備: {e}")
            self.device = None
            return False

    def run(self):
        self.running = True
        # Open the device on this worker thread so the read loop never touches a
        # half-initialised handle (open previously ran on the GUI thread, racing
        # this loop). Report success/failure back to the UI via a signal.
        if not self.open_device():
            self.connection_status.emit(False)
            return
        self.connection_status.emit(True)

        while self.running:
            try:
                # Read all available packets in buffer rapidly
                while True:
                    data = self.device.read(64)
                    if not data:
                        break  # Buffer empty, break inner loop to sleep

                    if len(data) >= 8 and data[0] == 0x10:

                        # Wacom Intuos S 24-bit aligned payload format
                        x = data[2] | (data[3] << 8) | (data[4] << 16)
                        y = data[5] | (data[6] << 8) | (data[7] << 16)
                        p = data[8] | (data[9] << 8)

                        self.packet_received.emit({'x': x, 'y': y, 'pressure': p})

            except Exception:
                # Device likely disconnected or contended; back off so a persistent
                # failure doesn't become a silent 1000 Hz exception spin.
                time.sleep(0.05)

            # Sleep 1ms to keep an effective ~1000 Hz poll without pinning a core.
            time.sleep(0.001)

    def stop(self):
        self.running = False
        self.wait()
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None

# ==================== osu! Detector ====================
class OsuDetector:
    @staticmethod
    def get_osu_title():
        if not win32gui:
            return None
        try:
            result = []
            def callback(hwnd, ctx):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title and 'osu!' in title.lower():
                        ctx.append(title)
            win32gui.EnumWindows(callback, result)
            return result[0] if result else None
        except Exception:
            return None

    @staticmethod
    def is_playing():
        title = OsuDetector.get_osu_title()
        if not title:
            return False, 'Not found'
        if ' - ' in title and 'osu!' in title.lower():
            return True, title
        return False, title

# ==================== Smart Auto-Calibrating Data Recorder ====================
class SmartRecorder(QObject):
    # Sends: X_normalized (0-1), Y_normalized (0-1), Pressure_bool, AutoMaxX, AutoMaxY
    state_updated = Signal(float, float, bool, float, float)
    recording_cleared = Signal()

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        self.recording = False

        self.raw_points = []
        self.start_time = None

        # Dynamic boundaries (Self-learning)
        # Start with the official specs as a baseline so the cursor isn't skewed before calibration
        self.dyn_max_x = float(self.tablet_info.get('max_x', 15200))
        self.dyn_max_y = float(self.tablet_info.get('max_y', 9500))

        # Rate Limiting
        self.last_emit_time = 0
        self.pen_down_state = False
        # UI refresh throttle (decouple cursor/trace redraw from the 1000 Hz packet rate)
        self._ui_last_emit = 0.0
        self._ui_last_down = False

    def start_recording(self):
        self.recording = True
        self.raw_points = []
        self.start_time = time.time()
        self.pen_down_state = False
        self.recording_cleared.emit()

    def stop_recording(self):
        self.recording = False
        self.pen_down_state = False
        dur = time.time() - self.start_time if self.start_time else 0
        return len(self.raw_points), dur

    def on_packet(self, packet):
        x = packet['x']
        y = packet['y']
        p = packet['pressure']

        # 0. Reject completely blank generic padding packets
        if x == 0 and y == 0:
            return

        is_down = p > 0

        # 1. Self-Learning Boundaries (Continually expand max known limit)
        if x > self.dyn_max_x and x < 100000:  # clamp madness
            self.dyn_max_x = float(x)
        if y > self.dyn_max_y and y < 100000:
            self.dyn_max_y = float(y)

        # 2. Record if recording
        if self.recording and is_down:
            now = time.time()
            if now - self.last_emit_time > 0.003:  # Max 333 points per second to prevent memory bloat
                self.raw_points.append({'x': x, 'y': y})
                self.last_emit_time = now

        # 3. Emit real-time state for the UI cursor (even when not recording).
        #    Throttle to ~120 Hz so the GUI thread isn't flooded by the 1000 Hz
        #    packet stream, but always emit on a pen up/down edge so a stroke's
        #    start/end is never dropped.
        if self.dyn_max_x > 0 and self.dyn_max_y > 0:
            now = time.time()
            if is_down != self._ui_last_down or (now - self._ui_last_emit) > 0.008:
                self._ui_last_emit = now
                self._ui_last_down = is_down
                nx = x / self.dyn_max_x
                ny = y / self.dyn_max_y
                self.state_updated.emit(nx, ny, is_down, self.dyn_max_x, self.dyn_max_y)

    def get_data(self):
        return self.raw_points, self.dyn_max_x, self.dyn_max_y

    def clear_all(self):
        self.raw_points = []
        self.dyn_max_x = float(self.tablet_info.get('max_x', 15200))
        self.dyn_max_y = float(self.tablet_info.get('max_y', 9500))
        self.last_emit_time = 0
        self.pen_down_state = False
        self._ui_last_emit = 0.0
        self._ui_last_down = False
        self.recording_cleared.emit()


# ==================== Data Science IQR Calculator ====================
class ModernCalculationEngine:
    @staticmethod
    def calculate(data_points, max_x, max_y, phys_w_mm, phys_h_mm, aspect_ratio=None):
        if len(data_points) < 10:
            return None

        # Extract axes
        xs = np.array([p['x'] for p in data_points])
        ys = np.array([p['y'] for p in data_points])

        # 1. IQR (Interquartile Range) Robust Outlier Rejection
        # Much smarter than dumping 2% of legit edges.
        def get_iqr_bounds(data, multiplier=1.5):
            q1, q3 = np.percentile(data, [25, 75])
            iqr = q3 - q1
            lower = q1 - (iqr * multiplier)
            upper = q3 + (iqr * multiplier)
            # Clamp to absolute raw boundaries
            return max(lower, 0), min(upper, max(data))

        min_x_th, max_x_th = get_iqr_bounds(xs, 1.5)
        min_y_th, max_y_th = get_iqr_bounds(ys, 1.5)

        # Filter the points
        valid = [(p['x'], p['y']) for p in data_points
                 if min_x_th <= p['x'] <= max_x_th and min_y_th <= p['y'] <= max_y_th]

        if len(valid) < 5:
            return None

        vx = [p[0] for p in valid]
        vy = [p[1] for p in valid]

        # 2. Find bounding box of valid data (Raw Units)
        bb_x_min = min(vx)
        bb_x_max = max(vx)
        bb_y_min = min(vy)
        bb_y_max = max(vy)

        raw_width = bb_x_max - bb_x_min
        raw_height = bb_y_max - bb_y_min

        # Safety clamp the maxes (avoid div by 0); these are the known raw extents.
        safe_max_x = max(max_x, 1.0)
        safe_max_y = max(max_y, 1.0)

        # 3. Enforce Aspect Ratio (Screen Ratio Matching)
        if aspect_ratio:
            current_ratio = raw_width / raw_height if raw_height > 0 else 1.0
            if current_ratio > aspect_ratio:
                # Need to be taller
                target_height = raw_width / aspect_ratio
                expansion = (target_height - raw_height) / 2
                bb_y_min -= expansion
                bb_y_max += expansion
            else:
                # Need to be wider
                target_width = raw_height * aspect_ratio
                expansion = (target_width - raw_width) / 2
                bb_x_min -= expansion
                bb_x_max += expansion

        # Clamp the (possibly expanded) box to the physical tablet so the
        # suggested area can never run off the edge into coordinates OTD would
        # simply truncate.
        bb_x_min = max(0.0, bb_x_min)
        bb_y_min = max(0.0, bb_y_min)
        bb_x_max = min(safe_max_x, bb_x_max)
        bb_y_max = min(safe_max_y, bb_y_max)
        raw_width = bb_x_max - bb_x_min
        raw_height = bb_y_max - bb_y_min

        # 4. Map back to Millimeters using Auto-Calibrated Maximums
        final_w_mm = (raw_width / safe_max_x) * phys_w_mm
        final_h_mm = (raw_height / safe_max_y) * phys_h_mm

        # 5. Calculate Absolute Center Position (What OTD calls "X Offset" and "Y Offset")
        center_raw_x = (bb_x_min + bb_x_max) / 2.0
        center_raw_y = (bb_y_min + bb_y_max) / 2.0

        # OTD's "X" and "Y" are literally just the absolute physical center point of the rectangle
        # measured in millimeters from the top-left (0,0) of the tablet surface.
        absolute_center_x_mm = (center_raw_x / safe_max_x) * phys_w_mm
        absolute_center_y_mm = (center_raw_y / safe_max_y) * phys_h_mm

        return {
            'width_mm': round(final_w_mm, 2),
            'height_mm': round(final_h_mm, 2),
            'x_offset_mm': round(absolute_center_x_mm, 2),
            'y_offset_mm': round(absolute_center_y_mm, 2),
            'used_points': len(valid),
            'total_points': len(data_points),
            'raw_w': round(raw_width, 1),
            'raw_h': round(raw_height, 1)
        }

# ==================== Modern UI Canvas ====================
class LiquidCanvas(QGraphicsView):
    def __init__(self, phys_w, phys_h):
        super().__init__()
        self.phys_w = phys_w
        self.phys_h = phys_h

        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)

        # Styling
        self.setStyleSheet("""
            QGraphicsView {
                background-color: #1e1e1e;
                border: 2px solid #333;
                border-radius: 10px;
            }
        """)

        # Logic state
        self.current_stroke = []
        self.pen_is_down = False
        self.live_cursor = None
        self.trace_items = []
        self.MAX_TRACE_ITEMS = 5000
        self._setup_background()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _setup_background(self):
        # Master Scene Canvas (1200 x height scaled to physical ratio)
        ratio = self.phys_w / self.phys_h if self.phys_h else 1.6
        self.sw = 1200.0
        self.sh = 1200.0 / ratio

        self.scene.setSceneRect(0, 0, self.sw, self.sh)
        self.scene.clear()
        # Bounded trace history so long sessions don't accumulate unlimited items
        self.trace_items = []

        # Gridlines Background
        pen = QPen(QColor(60, 60, 60, 80))
        pen.setWidth(1)
        for i in range(1, 10):
            self.scene.addLine(i * (self.sw/10), 0, i * (self.sw/10), self.sh, pen)
            self.scene.addLine(0, i * (self.sh/10), self.sw, i * (self.sh/10), pen)

        # Draw physical boundary line
        border = QPen(QColor(100, 100, 100))
        border.setWidth(4)
        self.scene.addRect(0, 0, self.sw, self.sh, border)

        # Create live cursor
        self.live_cursor = self.scene.addEllipse(-5, -5, 10, 10, QPen(Qt.NoPen), QBrush(QColor(255, 255, 255, 150)))
        self.live_cursor.setZValue(100)  # Keep on top

    def update_state(self, nx, ny, is_down, max_x, max_y):
        """nx, ny are 0.0 to 1.0 (normalized against dynamic max)"""
        vx = nx * self.sw
        vy = ny * self.sh

        # Update hover cursor
        self.live_cursor.setPos(vx, vy)

        # Color change on pressure
        if is_down:
            self.live_cursor.setBrush(QBrush(QColor(0, 200, 255, 200)))

            # Stroke tracing
            if not self.pen_is_down:
                self.pen_is_down = True
                self.current_stroke = []  # new stroke

            self.current_stroke.append((vx, vy))
            if len(self.current_stroke) > 1:
                p1 = self.current_stroke[-2]
                p2 = self.current_stroke[-1]

                # Trace line, kept in a bounded ring so memory stays flat over long sessions
                trace_pen = QPen(QColor(0, 150, 255, 180))
                trace_pen.setWidth(3)
                trace_pen.setCapStyle(Qt.RoundCap)
                line_item = self.scene.addLine(p1[0], p1[1], p2[0], p2[1], trace_pen)
                self.trace_items.append(line_item)
                if len(self.trace_items) > self.MAX_TRACE_ITEMS:
                    self.scene.removeItem(self.trace_items.pop(0))

        else:
            self.live_cursor.setBrush(QBrush(QColor(255, 255, 255, 120)))
            self.pen_is_down = False
            self.current_stroke = []

    def clear_canvas(self):
        self._setup_background()

    def overlay_result(self, res):
        # Absolute TopLeft in mm
        tl_x_mm = res['x_offset_mm'] - (res['width_mm'] / 2.0)
        tl_y_mm = res['y_offset_mm'] - (res['height_mm'] / 2.0)

        # Convert mm to canvas space
        rx = (tl_x_mm / self.phys_w) * self.sw
        ry = (tl_y_mm / self.phys_h) * self.sh
        rw = (res['width_mm'] / self.phys_w) * self.sw
        rh = (res['height_mm'] / self.phys_h) * self.sh

        # Draw glowing rectangle
        glow = QPen(QColor(255, 200, 0, 200))
        glow.setWidth(5)
        rect_brush = QBrush(QColor(255, 200, 0, 50))

        r_item = self.scene.addRect(rx, ry, rw, rh, glow, rect_brush)
        r_item.setZValue(50)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)


# ==================== Main App Window ====================
class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OTD Area Calculator v1.0 (Auto-Calibrated Edition)")
        self.setMinimumSize(1100, 650)

        # 1. Hardware Initialization
        self.tablet_info = TabletDetector.detect()

        self.recorder = SmartRecorder(self.tablet_info)
        self.hid_thread = HIDPollingThread(self.tablet_info)

        # UI State
        self.auto_mode = False
        self.was_playing = False
        self.last_cfg = None

        self._build_ui()

        # osu timer
        self.osu_timer = QTimer()
        self.osu_timer.setInterval(2000)
        self.osu_timer.timeout.connect(self._check_osu)

        # Route HID packets -> recorder, and connection state -> status label.
        # Wire everything before start() so the first status update isn't missed.
        self.hid_thread.packet_received.connect(self.recorder.on_packet)
        self.hid_thread.connection_status.connect(self._on_connection)
        self.hid_thread.start()  # opens the device on its own thread, then polls

    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet("background-color: #2b2b2b; color: #f0f0f0;")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # === Left Panel ===
        left_panel = QFrame()
        left_panel.setMaximumWidth(400)
        left_panel.setMinimumWidth(380)
        ll = QVBoxLayout(left_panel)
        ll.setSpacing(15)

        # Header
        header = QLabel("OTD 區域計算器 v1.0 🚀")
        header.setFont(QFont("Segoe UI", 24, QFont.Bold))
        header.setStyleSheet("color: #00d2ff;")
        ll.addWidget(header)

        # Info Box
        info_box = QGroupBox("硬體追蹤狀態")
        info_box.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 6px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #888; }")
        il = QVBoxLayout(info_box)

        self.lbl_name = QLabel(f"裝置：{self.tablet_info['name']}")
        self.lbl_name.setStyleSheet("font-weight: bold; color: #fff;")

        self.lbl_dyn_max = QLabel("原始邊界：(正在校準中...)")
        self.lbl_dyn_max.setStyleSheet("color: #00ff88;")

        il.addWidget(self.lbl_name)
        il.addWidget(self.lbl_dyn_max)

        btn_refresh = QPushButton("重新偵測裝置")
        btn_refresh.setStyleSheet("background-color: #444; padding: 6px; border-radius: 4px;")
        btn_refresh.clicked.connect(self._refresh)
        il.addWidget(btn_refresh)

        ll.addWidget(info_box)

        # Settings
        set_box = QGroupBox("組態設定")
        set_box.setStyleSheet(info_box.styleSheet())
        sl = QVBoxLayout(set_box)

        self.chk_ratio = QCheckBox("鎖定手繪板比例")
        self.chk_ratio.setChecked(True)
        self.inp_ratio = QLineEdit("16:9")

        row1 = QHBoxLayout()
        row1.addWidget(self.chk_ratio)
        row1.addWidget(self.inp_ratio)
        sl.addLayout(row1)

        self.chk_auto = QCheckBox("自動偵測 osu! 遊玩狀態錄製")
        self.chk_auto.toggled.connect(self._toggle_auto)
        sl.addWidget(self.chk_auto)
        ll.addWidget(set_box)

        # Actions
        self.btn_rec = QPushButton("🔴 開始錄製")
        self.btn_rec.setStyleSheet("background-color: #28a745; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 14px;")
        self.btn_rec.clicked.connect(self._start_rec)
        ll.addWidget(self.btn_rec)

        self.btn_stop = QPushButton("⏹ 停止錄製")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("background-color: #555; color: #888; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 14px;")
        self.btn_stop.clicked.connect(self._stop_rec)
        ll.addWidget(self.btn_stop)

        row2 = QHBoxLayout()
        self.btn_calc = QPushButton("✨ 計算最佳鏡設區域")
        self.btn_calc.setStyleSheet("background-color: #007bff; padding: 10px; border-radius: 5px;")
        self.btn_calc.clicked.connect(self._calc)
        row2.addWidget(self.btn_calc)
        ll.addLayout(row2)

        self.btn_reset = QPushButton("🗑 清空畫布與重新校準")
        self.btn_reset.setStyleSheet("background-color: #dc3545; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        self.btn_reset.clicked.connect(self._reset_all)
        ll.addWidget(self.btn_reset)

        self.btn_copy = QPushButton("📋 複製 OTD 設定")
        self.btn_copy.setEnabled(False)
        self.btn_copy.setStyleSheet("background-color: #6c757d; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        self.btn_copy.clicked.connect(self._copy_cfg)
        ll.addWidget(self.btn_copy)

        # Status Log
        self.lbl_status = QLabel("⬜ 準備就緒。")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("background-color: #111; border: 1px solid #000; padding: 10px; border-radius: 5px; color: #ccc;")
        ll.addWidget(self.lbl_status)

        ll.addStretch()
        main_layout.addWidget(left_panel)

        # === Right Panel (Canvas) ===
        self.canvas = LiquidCanvas(self.tablet_info['width_mm'], self.tablet_info['height_mm'])
        main_layout.addWidget(self.canvas, 1)

        # Connect UI logic
        self.recorder.state_updated.connect(self._on_smart_update)
        self.recorder.recording_cleared.connect(self.canvas.clear_canvas)

    def _on_connection(self, ok):
        if ok:
            self._log("✅ 裝置已連線。多執行緒背景讀取中。")
        else:
            self._log("❌ 無法開啟裝置。請確保 OTD 或其他驅動沒有佔用手寫板。")

    def _refresh(self):
        self.hid_thread.stop()
        self.tablet_info = TabletDetector.detect()
        self.lbl_name.setText(f"裝置：{self.tablet_info['name']}")

        self.hid_thread = HIDPollingThread(self.tablet_info)
        self.recorder = SmartRecorder(self.tablet_info)

        # reconnect logic
        self.hid_thread.packet_received.connect(self.recorder.on_packet)
        self.hid_thread.connection_status.connect(self._on_connection)
        self.recorder.state_updated.connect(self._on_smart_update)
        self.recorder.recording_cleared.connect(self.canvas.clear_canvas)

        # Reset canvas physicals
        self.canvas.phys_w = self.tablet_info['width_mm']
        self.canvas.phys_h = self.tablet_info['height_mm']
        self.canvas.clear_canvas()

        self.hid_thread.start()  # opens device on its thread; status arrives via signal

    def _on_smart_update(self, nx, ny, is_down, mx, my):
        # Update Canvas
        self.canvas.update_state(nx, ny, is_down, mx, my)
        # Update raw max label dynamically
        self.lbl_dyn_max.setText(f"自動校準最大邊界：{int(mx)} x {int(my)}")

    def _toggle_auto(self, c):
        self.auto_mode = c
        if c:
            self.osu_timer.start()
            self._btn_modes(False, False)
            self._log("🔍 等待 osu! 進入遊玩狀態自動錄製...")
        else:
            self.osu_timer.stop()
            self._btn_modes(True, False)
            self._log("⬜ 手動模式已啟用。")

    def _btn_modes(self, can_start, can_stop):
        if not self.auto_mode:
            if can_start:
                self.btn_rec.setEnabled(True)
                self.btn_rec.setStyleSheet("background-color: #28a745; font-weight: bold; padding: 12px; border-radius: 6px;")
            else:
                self.btn_rec.setEnabled(False)
                self.btn_rec.setStyleSheet("background-color: #555; color: #888; font-weight: bold; padding: 12px; border-radius: 6px;")

            if can_stop:
                self.btn_stop.setEnabled(True)
                self.btn_stop.setStyleSheet("background-color: #dc3545; font-weight: bold; padding: 12px; border-radius: 6px;")
            else:
                self.btn_stop.setEnabled(False)
                self.btn_stop.setStyleSheet("background-color: #555; color: #888; font-weight: bold; padding: 12px; border-radius: 6px;")

    def _start_rec(self):
        self.recorder.start_recording()
        self.last_cfg = None
        self.btn_copy.setEnabled(False)
        self._btn_modes(False, True)
        self._log("🔴 錄製中，盡情移動筆尖吧！")

    def _stop_rec(self):
        pts, dur = self.recorder.stop_recording()
        self._btn_modes(True, False)
        self._log(f"⏹ 錄製結束。共收集 {pts} 個點 (錄製時長 {dur:.1f} 秒)。")

    def _check_osu(self):
        if not self.chk_auto.isChecked():
            return

        playing, tit = OsuDetector.is_playing()
        if playing and not self.was_playing:
            self.was_playing = True
            self.recorder.start_recording()
            self._log(f"🔴 osu! 自動追蹤中：{tit}")
            self.setStyleSheet("QMainWindow { border: 2px solid red; }")
        elif not playing and self.was_playing:
            self.was_playing = False
            pts, dur = self.recorder.stop_recording()
            self._log(f"🔍 歌曲結束 (收集 {pts} 點)。等待下一首...")
            self.setStyleSheet("")
            self._calc()

    def _calc(self):
        data, max_x, max_y = self.recorder.get_data()

        ratio = None
        if self.chk_ratio.isChecked():
            txt = self.inp_ratio.text()
            try:
                if ':' in txt:
                    a, b = txt.split(':')
                    ratio = float(a)/float(b)
                else:
                    ratio = float(txt)
            except (ValueError, ZeroDivisionError):
                ratio = 1.777

        # Send raw data to modern IQR processor
        res = ModernCalculationEngine.calculate(
            data, max_x, max_y,
            self.tablet_info['width_mm'],
            self.tablet_info['height_mm'],
            ratio
        )

        if not res:
            self._log("⚠️ 座標數據不足，請多畫幾筆來計算鏡設區域面積。")
            return

        self.last_cfg = res
        self.btn_copy.setEnabled(True)

        self.canvas.overlay_result(res)

        self._log(f"✨ 最佳鏡設區域計算結果:\n\n"
                  f" 寬度 (Width): {res['width_mm']} mm\n"
                  f" 高度 (Height): {res['height_mm']} mm\n"
                  f" X 軸偏移 (X): {res['x_offset_mm']} mm\n"
                  f" Y 軸偏移 (Y): {res['y_offset_mm']} mm\n\n"
                  f"(已過濾雜訊點: {res['total_points'] - res['used_points']}\n"
                  f"最大偵測邊界: {int(max_x)} x {int(max_y)})")

    def _reset_all(self):
        self.recorder.clear_all()
        self.last_cfg = None
        self.btn_copy.setEnabled(False)
        self.lbl_dyn_max.setText("原始邊界：(已重設，請重新畫圖)")
        self._log("🗑 畫布與校準數據已經清空！(現在邊界已恢復預設值，隨時可以重新捕捉)")

    def _copy_cfg(self):
        if not self.last_cfg:
            return
        r = self.last_cfg
        cfg = {
            "Width": r['width_mm'],
            "Height": r['height_mm'],
            "X": r['x_offset_mm'],
            "Y": r['y_offset_mm'],
            "Rotation": 0,
        }
        QApplication.clipboard().setText(json.dumps(cfg, indent=2))
        self.btn_copy.setText("✓ 已複製到剪貼簿")
        QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 複製 OTD 設定"))

    def _log(self, text):
        self.lbl_status.setText(text)

    def closeEvent(self, e):
        self.hid_thread.stop()
        e.accept()

if __name__ == '__main__':
    # The platform plugin (windows:nowmpointer) is selected via QT_QPA_PLATFORM
    # set at the top of this module, so no extra argv hacks are needed here.
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from PySide6.QtGui import QPalette
    # Modern dark color palette globally
    palette = app.palette()
    palette.setColor(QPalette.Window, QColor(43, 43, 43))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    app.setPalette(palette)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
