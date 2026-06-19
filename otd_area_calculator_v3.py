"""
OTD 區域計算器 v3 — 混合方案 (Hybrid Mode)
使用 HID API 偵測筆觸碰/抬起 + Windows 游標位置追蹤座標

【重要】
此模式依賴 Windows 游標位置，因此受限於 OTD 驅動設定的區域。
若要測量全板面，請在 OTD 驅動中暫時將區域設定為「全螢幕 (Full Area)」。
"""

import sys
import time
import ctypes
import ctypes.wintypes
import hid
import keyboard
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox, QCheckBox,
    QGraphicsView, QGraphicsScene, QSizePolicy, QScrollArea, QMessageBox
)
from PySide6.QtCore import QTimer, Signal, Qt, QObject
from PySide6.QtGui import QFont, QPen, QColor, QBrush, QPainter

try:
    import win32gui
except ImportError:
    win32gui = None


# ==================== 手寫板資料庫 ====================

TABLET_DATABASE = {
    (0x056A, 0x0374): {
        'name': 'Wacom Intuos S (CTL-4100)',
        'max_x': 15200, 'max_y': 9500, 'max_pressure': 4095,
        'width_mm': 152.0, 'height_mm': 95.0,
    },
    (0x056A, 0x0375): {
        'name': 'Wacom Intuos M (CTL-6100)',
        'max_x': 21600, 'max_y': 13500, 'max_pressure': 4095,
        'width_mm': 216.0, 'height_mm': 135.0,
    },
    (0x056A, 0x0376): {
        'name': 'Wacom Intuos S BT (CTL-4100WL)',
        'max_x': 15200, 'max_y': 9500, 'max_pressure': 4095,
        'width_mm': 152.0, 'height_mm': 95.0,
    },
    (0x056A, 0x037A): {
        'name': 'Wacom One S (CTL-472)',
        'max_x': 15200, 'max_y': 9500, 'max_pressure': 4095,
        'width_mm': 152.0, 'height_mm': 95.0,
    },
    (0x056A, 0x037B): {
        'name': 'Wacom One M (CTL-672)',
        'max_x': 21600, 'max_y': 13500, 'max_pressure': 4095,
        'width_mm': 216.0, 'height_mm': 135.0,
    },
}

WACOM_VENDOR_ID = 0x056A


# ==================== 螢幕工具 ====================

def get_cursor_pos():
    """取得目前游標位置（螢幕像素座標）"""
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def get_screen_size():
    """取得主螢幕解析度"""
    w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
    h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
    return w, h


# ==================== HID 筆狀態讀取器 ====================

class HIDPenStateReader:
    """
    只用 HID 判斷筆是否觸碰板面（byte[1] bit 0 = tip switch）。
    不讀取座標（座標由游標位置提供）。
    相容 Report ID 0x10 (Default) 和 0x02 (OTD Mode)。
    """
    def __init__(self, tablet_info):
        self.tablet_info = tablet_info
        self.device = None
        self.running = False

    def open(self):
        vid = self.tablet_info.get('vendor_id')
        pid = self.tablet_info.get('product_id')
        if not vid or not pid:
            return False

        devices = hid.enumerate(vid, pid)
        print(f"[HID] 找到 {len(devices)} 個介面")

        for i, d in enumerate(devices):
            path = d.get('path', b'')
            try:
                dev = hid.device()
                dev.open_path(path)
                dev.set_nonblocking(True)
                
                # 不發送 Feature Report，避免干擾 OTD
                # 直接讀取目前的 Report ID

                self.device = dev
                self.running = True
                print(f"[HID] 已連接介面 [{i}] (Passive Mode)")
                return True
            except Exception as e:
                print(f"  -> 介面 [{i}] 開啟失敗: {e}")
                continue

        return False

    def read_pen_state(self):
        """
        讀取筆狀態。回傳:
          True  = 筆觸碰板面
          False = 筆懸浮或離開
          None  = 無新資料
        """
        if not self.device or not self.running:
            return None
        try:
            data = self.device.read(64)
            if not data:
                return None
            
            # 無論 Report ID 是 0x02 還是 0x10，Status Byte 都在 byte[1]
            if len(data) >= 2:
                status = data[1]
                tip_switch = (status & 0x01) != 0
                return tip_switch
                
            return None
        except Exception:
            return None

    def close(self):
        self.running = False
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None


# ==================== 手寫板偵測器 ====================

class TabletDetector:
    @staticmethod
    def detect():
        try:
            all_devices = hid.enumerate(0, 0)
        except Exception:
            return TabletDetector._unknown()

        seen = set()
        for d in all_devices:
            vid = d.get('vendor_id', 0)
            pid = d.get('product_id', 0)
            key = (vid, pid)
            if key in seen or vid == 0:
                continue
            seen.add(key)

            if key in TABLET_DATABASE:
                spec = TABLET_DATABASE[key].copy()
                spec['vendor_id'] = vid
                spec['product_id'] = pid
                print(f"[偵測] 找到: {spec['name']}")
                return spec

            if vid == WACOM_VENDOR_ID:
                product = d.get('product_string', f'0x{pid:04X}')
                return {
                    'name': f'Wacom {product}',
                    'vendor_id': vid, 'product_id': pid,
                    'max_x': 15200, 'max_y': 9500,
                    'width_mm': 152.0, 'height_mm': 95.0,
                    'max_pressure': 4095,
                }

        return TabletDetector._unknown()

    @staticmethod
    def _unknown():
        return {
            'name': '未偵測到手寫板',
            'vendor_id': None, 'product_id': None,
            'max_x': 15200, 'max_y': 9500,
            'width_mm': 152.0, 'height_mm': 95.0,
            'max_pressure': 4095,
        }


# ==================== osu! 偵測器 ====================

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

    @staticmethod
    def is_running():
        return OsuDetector.get_osu_title() is not None


# ==================== 數據記錄器 ====================

class DataRecorder(QObject):
    """記錄游標座標（在筆觸碰板面時）"""
    point_added = Signal(float, float)
    pen_lifted = Signal()
    recording_cleared = Signal()

    def __init__(self, screen_w, screen_h, tablet_info):
        super().__init__()
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.tablet_w_mm = tablet_info['width_mm']
        self.tablet_h_mm = tablet_info['height_mm']

        # OTD 區域設定（預設 = 全區域）
        self.otd_w = tablet_info['width_mm']
        self.otd_h = tablet_info['height_mm']
        self.otd_x = tablet_info['width_mm'] / 2   # 中心 X
        self.otd_y = tablet_info['height_mm'] / 2   # 中心 Y

        self.recording = False
        self.data_points = []
        self.start_time = None
        self.last_point_time = 0
        self._pen_down = False

    def set_otd_area(self, w, h, cx, cy):
        """更新 OTD 區域設定"""
        self.otd_w = w
        self.otd_h = h
        self.otd_x = cx
        self.otd_y = cy

    def start_recording(self):
        self.recording = True
        self.data_points = []
        self.start_time = time.time()
        self._pen_down = False
        self.recording_cleared.emit()

    def stop_recording(self):
        self.recording = False
        duration = time.time() - self.start_time if self.start_time else 0
        self._pen_down = False
        return len(self.data_points), duration

    def process_pen_state(self, is_touching):
        """由主迴圈呼叫，傳入筆狀態"""
        if not self.recording:
            return

        if is_touching is None:
            # 無新 HID 資料，維持現狀
            if self._pen_down:
                self._record_point()
            return

        if is_touching:
            if not self._pen_down:
                self._pen_down = True
                self.pen_lifted.emit()  # 開始新筆畫
            self._record_point()
        else:
            if self._pen_down:
                self._pen_down = False

    def _record_point(self):
        now = time.time()
        if now - self.last_point_time < 0.005:
            return

        cx, cy = get_cursor_pos()

        # 螢幕座標 → 手寫板 mm 座標
        # OTD 將手寫板的 [otd_x - otd_w/2, otd_x + otd_w/2] 區域
        # 映射到螢幕的 [0, -screen_w] -> 修正：映射到[0, screen_w]
        x_mm = (cx / self.screen_w) * self.otd_w + (self.otd_x - self.otd_w / 2)
        y_mm = (cy / self.screen_h) * self.otd_h + (self.otd_y - self.otd_h / 2)

        self.data_points.append({'x_mm': x_mm, 'y_mm': y_mm})
        self.point_added.emit(x_mm, y_mm)
        self.last_point_time = now

    def get_data(self):
        return self.data_points

    def clear_data(self):
        self.data_points = []
        self._pen_down = False
        self.recording_cleared.emit()


# ==================== 軌跡預覽 ====================

class TabletPreviewWidget(QGraphicsView):
    """手寫板軌跡預覽（使用 mm 座標）"""

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        self.w_mm = tablet_info['width_mm']
        self.h_mm = tablet_info['height_mm']

        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: white; border: 2px solid #555;")
        self.current_stroke = []
        self._draw_background()

    def _draw_background(self):
        ratio = self.w_mm / self.h_mm if self.h_mm else 1.6
        if ratio >= 1:
            sw, sh = 1200, 1200 / ratio
        else:
            sh, sw = 1200, 1200 * ratio

        self.scene.setSceneRect(0, 0, sw, sh)
        self.scene.addRect(0, 0, sw, sh, QPen(Qt.NoPen), QColor(45, 45, 45))
        border = QPen(QColor(100, 100, 100))
        border.setWidth(3)
        self.scene.addRect(0, 0, sw, sh, border)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def add_point(self, x_mm, y_mm):
        """mm 座標 → 場景座標"""
        rect = self.scene.sceneRect()
        sx = (x_mm / self.w_mm) * rect.width()
        sy = (y_mm / self.h_mm) * rect.height()
        sx = max(0.0, min(sx, rect.width()))
        sy = max(0.0, min(sy, rect.height()))

        self.current_stroke.append((sx, sy))
        if len(self.current_stroke) > 1:
            px, py = self.current_stroke[-2]
            pen = QPen(QColor(50, 120, 255))
            pen.setWidth(2)
            self.scene.addLine(px, py, sx, sy, pen)

    def new_stroke(self):
        self.current_stroke = []

    def clear_strokes(self):
        self.scene.clear()
        self.current_stroke = []
        self._draw_background()

    def show_calculated_area(self, result):
        rect = self.scene.sceneRect()
        area_w = result['width_mm']
        area_h = result['height_mm']
        cx = self.w_mm / 2 + result['x_offset_mm']
        cy = self.h_mm / 2 + result['y_offset_mm']

        rx = (cx - area_w / 2) / self.w_mm * rect.width()
        ry = (cy - area_h / 2) / self.h_mm * rect.height()
        rw = area_w / self.w_mm * rect.width()
        rh = area_h / self.h_mm * rect.height()

        pen = QPen(QColor(255, 220, 50))
        pen.setWidth(3)
        brush = QBrush(QColor(255, 220, 50, 40))
        self.scene.addRect(rx, ry, rw, rh, pen, brush)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)


# ==================== 區域計算引擎 ====================

class CalculationEngine:
    @staticmethod
    def calculate(data_points, tablet_info, screen_ratio=16/9):
        """data_points 已經是 mm 座標"""
        if len(data_points) < 50:
            return None

        w_mm = tablet_info['width_mm']
        h_mm = tablet_info['height_mm']

        xs = sorted([p['x_mm'] for p in data_points])
        ys = sorted([p['y_mm'] for p in data_points])

        n = len(data_points)
        lo = max(0, int(n * 0.02))
        hi = min(n - 1, int(n * 0.98) - 1)

        x_min, x_max = xs[lo], xs[hi]
        y_min, y_max = ys[lo], ys[hi]

        raw_w = x_max - x_min
        raw_h = y_max - y_min

        # 螢幕比例校正
        if screen_ratio > 0 and raw_h > 0:
            current_ratio = raw_w / raw_h
            if current_ratio > screen_ratio:
                raw_h = raw_w / screen_ratio
            else:
                raw_w = raw_h * screen_ratio

        cx = (x_min + x_max) / 2
        cy = (y_min + y_max) / 2
        x_offset = cx - w_mm / 2
        y_offset = cy - h_mm / 2

        return {
            'width_mm': round(raw_w, 2),
            'height_mm': round(raw_h, 2),
            'x_offset_mm': round(x_offset, 2),
            'y_offset_mm': round(y_offset, 2),
            'total_points': len(data_points),
            'used_points': hi - lo + 1,
            'screen_ratio': screen_ratio,
        }


# ==================== 主視窗 ====================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.tablet_info = TabletDetector.detect()
        self.hid_reader = HIDPenStateReader(self.tablet_info)
        self.screen_w, self.screen_h = get_screen_size()
        self.recorder = DataRecorder(self.screen_w, self.screen_h, self.tablet_info)
        self.auto_detect_enabled = False
        self.was_playing = False

        print(f"[螢幕] {self.screen_w}×{self.screen_h}")

        self._build_ui()
        self._connect_tablet()

        # 輪詢 HID 筆狀態 (5ms)
        self.poll_timer = QTimer()
        self.poll_timer.setInterval(5)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        # osu! 偵測 (2s)
        self.osu_timer = QTimer()
        self.osu_timer.setInterval(2000)
        self.osu_timer.timeout.connect(self._check_osu)

        self._setup_hotkeys()

    def _build_ui(self):
        self.setWindowTitle("OTD 區域計算器 v3 (Hybrid Mode)")
        self.setMinimumSize(1000, 600)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ── 左側面板 ──
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setMinimumWidth(420)
        scroll_area.setMaximumWidth(480)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")

        left_widget = QWidget()
        ll = QVBoxLayout(left_widget)
        ll.setSpacing(8)
        ll.setContentsMargins(20, 5, 10, 5)

        title = QLabel("🎯 OTD 區域計算器 v3")
        title.setFont(QFont("Microsoft JhengHei", 16, QFont.Bold))
        ll.addWidget(title)

        # 手寫板資訊
        info_box = QGroupBox("手寫板資訊")
        info_lay = QVBoxLayout()
        info_lay.setContentsMargins(12, 20, 12, 10)
        info_lay.setSpacing(6)

        name = self.tablet_info['name']
        self.model_label = QLabel(f"型號：{name}")
        color = "red" if "未偵測到" in name else "#0066cc"
        self.model_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 12px;")
        self.model_label.setWordWrap(True)

        self.coords_label = QLabel(
            f"座標範圍：{self.tablet_info['max_x']} × {self.tablet_info['max_y']}")
        self.coords_label.setStyleSheet("font-size: 11px;")

        self.size_label = QLabel(
            f"實體尺寸：{self.tablet_info['width_mm']:.1f} × "
            f"{self.tablet_info['height_mm']:.1f} mm")
        self.size_label.setStyleSheet("font-size: 11px;")

        self.screen_label = QLabel(
            f"螢幕解析度：{self.screen_w} × {self.screen_h}")
        self.screen_label.setStyleSheet("font-size: 11px; color: #666;")

        info_lay.addWidget(self.model_label)
        info_lay.addWidget(self.coords_label)
        info_lay.addWidget(self.size_label)
        info_lay.addWidget(self.screen_label)
        info_box.setLayout(info_lay)
        ll.addWidget(info_box)

        # OTD 區域設定
        otd_box = QGroupBox("OTD 區域設定 (若要測量全板，請設為全螢幕)")
        otd_lay = QVBoxLayout()
        otd_lay.setSpacing(4)
        otd_lay.setContentsMargins(12, 16, 12, 8)

        otd_note = QLabel("輸入目前 OTD 設定，或先將 OTD 設為 Full Area。")
        otd_note.setStyleSheet("font-size: 10px; color: #666;")
        otd_note.setWordWrap(True)
        otd_lay.addWidget(otd_note)

        w_mm = self.tablet_info['width_mm']
        h_mm = self.tablet_info['height_mm']

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Width:"))
        self.otd_w_input = QLineEdit(f"{w_mm}")
        self.otd_w_input.setFixedWidth(70)
        row1.addWidget(self.otd_w_input)
        row1.addWidget(QLabel("Height:"))
        self.otd_h_input = QLineEdit(f"{h_mm}")
        self.otd_h_input.setFixedWidth(70)
        row1.addWidget(self.otd_h_input)
        row1.addStretch()
        otd_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("X:"))
        self.otd_x_input = QLineEdit(f"{w_mm / 2}")
        self.otd_x_input.setFixedWidth(70)
        row2.addWidget(self.otd_x_input)
        row2.addWidget(QLabel("Y:"))
        self.otd_y_input = QLineEdit(f"{h_mm / 2}")
        self.otd_y_input.setFixedWidth(70)
        row2.addWidget(self.otd_y_input)
        row2.addStretch()
        otd_lay.addLayout(row2)

        self.otd_apply_btn = QPushButton("套用")
        self.otd_apply_btn.setFixedWidth(60)
        self.otd_apply_btn.setStyleSheet(
            "QPushButton { background-color: #6c757d; color: white; "
            "padding: 4px; border-radius: 3px; font-size: 11px; }")
        self.otd_apply_btn.clicked.connect(self._apply_otd_settings)
        otd_lay.addWidget(self.otd_apply_btn)

        otd_box.setLayout(otd_lay)
        ll.addWidget(otd_box)

        # 設定
        settings_box = QGroupBox("設定")
        sl = QVBoxLayout()
        sl.setSpacing(8)
        sl.setContentsMargins(12, 16, 12, 8)

        ratio_row = QHBoxLayout()
        self.ratio_check = QCheckBox("固定螢幕比例:")
        self.ratio_input = QLineEdit("16:9")
        self.ratio_input.setFixedWidth(80)
        ratio_row.addWidget(self.ratio_check)
        ratio_row.addWidget(self.ratio_input)
        ratio_row.addStretch()
        sl.addLayout(ratio_row)

        self.auto_check = QCheckBox("自動偵測 osu! 並錄製")
        self.auto_check.toggled.connect(self._toggle_auto_detect)
        sl.addWidget(self.auto_check)

        settings_box.setLayout(sl)
        ll.addWidget(settings_box)

        # 按鈕
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("開始錄製 (F10)")
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #28a745; color: white; "
            "font-size: 13px; padding: 8px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #218838; }")
        self.start_btn.clicked.connect(self._start_recording)

        self.stop_btn = QPushButton("停止錄製 (F11)")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #dc3545; color: white; "
            "font-size: 13px; padding: 8px; border-radius: 5px; }"
            "QPushButton:disabled { background-color: #6c757d; }")
        self.stop_btn.clicked.connect(self._stop_recording)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        ll.addLayout(btn_row)

        self.calc_btn = QPushButton("計算區域")
        self.calc_btn.setStyleSheet(
            "QPushButton { background-color: #007bff; color: white; "
            "font-size: 13px; padding: 8px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #0056b3; }")
        self.calc_btn.clicked.connect(self._calculate)
        ll.addWidget(self.calc_btn)

        # 狀態
        self.status_label = QLabel("⬜ 就緒")
        self.status_label.setStyleSheet(
            "padding: 6px; border-radius: 5px; background-color: #f0f0f0;")
        self.status_label.setWordWrap(True)
        ll.addWidget(self.status_label)

        # 計算結果
        result_title = QLabel("計算結果")
        result_title.setFont(QFont("Microsoft JhengHei", 12, QFont.Bold))
        ll.addWidget(result_title)

        self.result_text = QLabel("")
        self.result_text.setWordWrap(True)
        self.result_text.setStyleSheet(
            "background-color: #f9f9f9; padding: 10px; border-radius: 5px; "
            "font-family: Consolas; font-size: 12px; min-height: 60px;")
        self.result_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        ll.addWidget(self.result_text)

        # 使用說明
        help_title = QLabel("使用說明：")
        help_title.setStyleSheet(
            "font-weight: bold; font-size: 12px; margin-top: 3px;")
        ll.addWidget(help_title)

        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFixedHeight(130)
        help_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #ddd; border-radius: 5px; "
            "background-color: #f9f9f9; }")

        help_text = QLabel(
            "【自動模式】（推薦）\n"
            "1. 勾選「自動偵測 osu!」\n"
            "2. 直接開啟 osu! 並遊玩\n"
            "3. 程式會自動開始/停止錄製\n\n"
            "【手動模式】\n"
            "1. 按「開始錄製」或 F10\n"
            "2. 在遊戲中正常遊玩 1-2 首歌\n"
            "3. 按「停止錄製」或 F11\n"
            "4. 按「計算區域」得到 OTD 設定值\n\n"
            "【套用結果】\n"
            "將 Width/Height/Offset 填入 OTD\n"
            "的 Tablet Area 設定即可。"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(
            "padding: 10px; font-size: 11px; background-color: #f9f9f9;")
        help_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        help_scroll.setWidget(help_text)
        ll.addWidget(help_scroll)

        scroll_area.setWidget(left_widget)

        # ── 右側預覽 ──
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        preview_title = QLabel("📊 軌跡預覽")
        preview_title.setFont(QFont("Microsoft JhengHei", 14, QFont.Bold))
        preview_title.setStyleSheet("color: #333; padding: 10px;")
        rl.addWidget(preview_title)

        self.preview = TabletPreviewWidget(self.tablet_info)
        rl.addWidget(self.preview)

        # 信號連接
        self.recorder.point_added.connect(self.preview.add_point)
        self.recorder.pen_lifted.connect(self.preview.new_stroke)
        self.recorder.recording_cleared.connect(self.preview.clear_strokes)

        main_layout.addWidget(scroll_area)
        main_layout.addWidget(right, 1)

    def _connect_tablet(self):
        if self.tablet_info.get('vendor_id'):
            ok = self.hid_reader.open()
            if ok:
                self.status_label.setText("✅ 手寫板已連接（HID 筆狀態 + 游標追蹤）")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #d4edda;")
            else:
                self.status_label.setText("❌ HID 設備開啟失敗")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #f8d7da;")
        else:
            self.status_label.setText("❌ 未偵測到手寫板")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #f8d7da;")

    def _apply_otd_settings(self):
        """讀取 UI 的 OTD 區域設定並更新記錄器"""
        try:
            w = float(self.otd_w_input.text())
            h = float(self.otd_h_input.text())
            cx = float(self.otd_x_input.text())
            cy = float(self.otd_y_input.text())
            if w > 0 and h > 0:
                self.recorder.set_otd_area(w, h, cx, cy)
                self.status_label.setText(
                    f"✅ OTD 區域已更新：{w}×{h}mm @ ({cx}, {cy})")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #d4edda;")
                print(f"[OTD] 區域設定: {w}×{h}mm, 中心=({cx}, {cy})")
        except ValueError:
            self.status_label.setText("❌ OTD 設定值無效")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #f8d7da;")

    def _poll(self):
        """每 5ms 讀 HID 筆狀態 + 游標位置"""
        if not self.hid_reader or not self.hid_reader.running:
            return
        pen_state = self.hid_reader.read_pen_state()
        self.recorder.process_pen_state(pen_state)

    def _start_recording(self):
        self.recorder.start_recording()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("🔴 錄製中... (按 F11 停止)")
        self.status_label.setStyleSheet(
            "padding: 6px; border-radius: 5px; background-color: #f8d7da; "
            "color: red; font-weight: bold;")

    def _stop_recording(self):
        count, duration = self.recorder.stop_recording()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        m, s = int(duration // 60), int(duration % 60)
        self.status_label.setText(
            f"⬜ 已停止 ({count} 點, {m}分{s}秒)")
        self.status_label.setStyleSheet(
            "padding: 6px; border-radius: 5px; background-color: #f0f0f0;")

    def _toggle_auto_detect(self, checked):
        self.auto_detect_enabled = checked
        if checked:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.osu_timer.start()
            self.status_label.setText("🔍 等待偵測 osu!...")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #fff3cd;")
        else:
            self.osu_timer.stop()
            if self.recorder.recording:
                self._stop_recording()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status_label.setText("⬜ 手動模式")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #f0f0f0;")
            self.was_playing = False

    def _check_osu(self):
        if not self.auto_detect_enabled:
            return
        playing, title = OsuDetector.is_playing()
        if playing and not self.was_playing:
            self.was_playing = True
            self._start_recording()
            self.status_label.setText("🔴 自動錄製中 (偵測到遊玩)")
        elif not playing and self.was_playing:
            self.was_playing = False
            self._stop_recording()
            self.status_label.setText("🔍 等待偵測 osu!...")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #fff3cd;")

    def _calculate(self):
        data = self.recorder.get_data()
        if len(data) < 50:
            self.result_text.setText(
                f"數據不足（目前 {len(data)} 點，至少需要 50 點）")
            return

        ratio = None
        if self.ratio_check.isChecked():
            txt = self.ratio_input.text().strip()
            try:
                if ':' in txt:
                    a, b = txt.split(':')
                    ratio = float(a) / float(b)
                else:
                    ratio = float(txt)
            except ValueError:
                ratio = 16 / 9
        if ratio is None:
            ratio = 0

        result = CalculationEngine.calculate(data, self.tablet_info, ratio)
        if not result:
            self.result_text.setText("計算失敗")
            return

        self.result_text.setText(
            f"✓ 計算完成！\n\n"
            f"【建議的 OTD 區域設定】\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Width:    {result['width_mm']:7.2f} mm\n"
            f"Height:   {result['height_mm']:7.2f} mm\n"
            f"X Offset: {result['x_offset_mm']:+7.2f} mm\n"
            f"Y Offset: {result['y_offset_mm']:+7.2f} mm\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"數據: {result['total_points']} 點 "
            f"(使用 {result['used_points']})")

        self.preview.show_calculated_area(result)

    def _setup_hotkeys(self):
        try:
            keyboard.on_press_key('F10', lambda _: self._hotkey_start())
            keyboard.on_press_key('F11', lambda _: self._hotkey_stop())
        except Exception as e:
            print(f"[熱鍵] 設定失敗: {e}")

    def _hotkey_start(self):
        if not self.auto_detect_enabled and not self.recorder.recording:
            QTimer.singleShot(0, self._start_recording)

    def _hotkey_stop(self):
        if self.recorder.recording:
            QTimer.singleShot(0, self._stop_recording)

    def closeEvent(self, event):
        self.poll_timer.stop()
        self.osu_timer.stop()
        self.hid_reader.close()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        event.accept()


# ==================== 主程式 ====================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft JhengHei", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
