"""
OTD 區域計算器 v4 — 原始輸入版 (Raw Input)
結合 v2 的 HID 讀取能力與 v3 的界面改進。
直接讀取手寫板原始 HID 數據，計算物理區域，不受 OTD 設定影響。

【重要】
此版本直接讀取手寫板硬體座標 (0~MaxX, 0~MaxY)。
計算出的區域是基於手寫板實體尺寸的絕對位置。
"""

import sys
import time
import ctypes
import hid
import keyboard
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox, QCheckBox,
    QGraphicsView, QGraphicsScene, QSizePolicy, QScrollArea
)
from PySide6.QtCore import QTimer, Signal, Qt, QObject
from PySide6.QtGui import QFont, QPen, QColor, QBrush, QPainter

try:
    import win32gui
except ImportError:
    win32gui = None


# ==================== 手寫板資料庫 (From v2/v3) ====================
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


# ==================== 手寫板偵測器 ====================
class TabletDetector:
    @staticmethod
    def detect():
        try:
            # Enumerate all devices to find standard tablets
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

            if key in TABLET_SPECS:
                spec = TABLET_SPECS[key].copy()
                spec['vendor_id'] = vid
                spec['product_id'] = pid
                print(f"[偵測] 找到: {spec['name']}")
                return spec

            if vid == WACOM_VENDOR_ID:
                product = d.get('product_string', f'0x{pid:04X}')
                # Assume standard CTL-4100 specs for unknown Wacom small tablets as fallback
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


# ==================== HID 讀取器 (From v2 - Robust) ====================
class HIDTabletReader:
    """使用 HID API 直接讀取手寫板原始數據"""
    
    def __init__(self, tablet_info):
        self.tablet_info = tablet_info
        self.device = None
        self.running = False
        self.last_packet = None
        
    def open(self):
        vid = self.tablet_info.get('vendor_id')
        pid = self.tablet_info.get('product_id')
        
        if not vid or not pid:
            return False
        
        try:
            # 1. 嘗試尋找並開啟 Digitizer 介面 (Usage Page 0x0D)
            all_devices = hid.enumerate(vid, pid)
            target_path = None
            
            for dev in all_devices:
                if dev.get('usage_page') == 0x0D: # Digitizer
                    target_path = dev.get('path')
                    print(f"[HID] 找到 Digitizer 介面: {target_path}")
                    break
            
            # 2. 如果沒找到明確的 Digitizer，嘗試開啟第一個介面 (通常可行)
            if not target_path and all_devices:
                target_path = all_devices[0].get('path')
                print(f"[HID] 使用預設介面: {target_path}")

            if not target_path:
                print("[HID] 未找到可用介面")
                return False

            self.device = hid.device()
            self.device.open_path(target_path)
            self.device.set_nonblocking(True)
            
            # 3. 發送 Feature Report [0x02, 0x02] 切換到 Digitizer Mode
            # 這對於取得完整的 0-15200 座標範圍通常是必要的
            try:
                self.device.send_feature_report([0x02, 0x02])
                print("[HID] 已發送 Feature Report (Init Digitizer Mode)")
            except Exception as e:
                print(f"[HID] Feature Report 發送失敗 (可能是非 Wacom 設備或無需初始化): {e}")

            self.running = True
            return True

        except Exception as e:
            print(f"[HID] 開啟設備失敗: {e}")
            return False

    def read_packet(self):
        if not self.device or not self.running:
            return None
        
        try:
            data = self.device.read(64)
            if not data:
                return None
            
            # 解析邏輯 (兼容 Wacom 標準格式)
            # Wacom Report ID 0x10 (16) 常用於筆數據
            # Format: ID(1) + Status(1) + X(2) + Y(2) + Pressure(2)
            # 或者 ID(1) + X(2) + Y(2) + Pressure(2) ... 需判斷
            
            # 參考 v2 的解析邏輯 (Report ID 0x10)
            report_id = data[0]
            packet = None

            if len(data) >= 8:
                # 只有 Report ID 16 包含完整座標
                if report_id == 0x10:
                    x = data[2] | (data[3] << 8)
                    y = data[4] | (data[5] << 8)
                    p = data[6] | (data[7] << 8)
                    
                    # Filter invalid coordinates (Same as v2)
                    if x > 0 and y > 0 and x < 40000 and y < 40000:
                        packet = {'x': x, 'y': y, 'pressure': p}

                
                # 有些設備可能使用不同的 Report ID，這裡保留擴充性
                # 如果是 v2 測試成功的，以上邏輯應該足夠
            return packet

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


# ==================== osu! 偵測器 (From v3) ====================
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


# ==================== 數據記錄器 ====================
class DataRecorder(QObject):
    """記錄原始手寫板座標"""
    point_added = Signal(float, float) # 這裡改發 float (mm) 或 int (raw)? 發送 mm 給 UI 比較方便
    pen_lifted = Signal()
    recording_cleared = Signal()

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        self.recording = False
        self.data_points = [] # 存儲 {'x': raw_x, 'y': raw_y}
        self.start_time = None
        self.last_point_time = 0
        self.last_raw_x = -1
        self.last_raw_y = -1
        self._pen_down = False
        
        self.raw_max_x = tablet_info['max_x']
        self.raw_max_y = tablet_info['max_y']
        self.width_mm = tablet_info['width_mm']
        self.height_mm = tablet_info['height_mm']

    def start_recording(self):
        self.recording = True
        self.data_points = []
        self.start_time = time.time()
        self._pen_down = False
        self.last_raw_x = -1
        self.recording_cleared.emit()

    def stop_recording(self):
        self.recording = False
        duration = time.time() - self.start_time if self.start_time else 0
        self._pen_down = False
        return len(self.data_points), duration

    def process_packet(self, packet):
        """由主迴圈呼叫，傳入解析後的封包 {'x', 'y', 'pressure'}"""
        if not self.recording or not packet:
            # 處理筆抬起邏輯
            return

        pressure = packet['pressure']
        
        if pressure > 0:
            if not self._pen_down:
                self._pen_down = True
                self.pen_lifted.emit()
                # Reset jump filter on new stroke
                self.last_raw_x = -1 
            
            self._record_point(packet['x'], packet['y'])
        else:
            if self._pen_down:
                self._pen_down = False
                self.last_raw_x = -1

    def _record_point(self, raw_x, raw_y):
        now = time.time()
        # if now - self.last_point_time < 0.001: # Remove time limit to capture full rate
        #    return

        # 簡單過濾異常值
        if raw_x > self.raw_max_x * 1.5 or raw_y > self.raw_max_y * 1.5:
            return

        # Jump Filter (防跳動過濾)
        # 如果跟上一點距離過大 (例如 > 20% 寬度)，視為雜訊
        if self.last_raw_x != -1:
            dist_sq = (raw_x - self.last_raw_x)**2 + (raw_y - self.last_raw_y)**2
            # 閾值設定為寬度的 20% (例如 15200 * 0.2 ~= 3000)
            threshold = (self.raw_max_x * 0.2) ** 2
            if dist_sq > threshold:
                # 忽略此點 (但保留 last_raw_x 不變，假設這一點是錯的)
                return 

        self.last_raw_x = raw_x
        self.last_raw_y = raw_y

        # 轉換為 mm 供預覽顯示 (但儲存原始數據以求精確)
        x_mm = (raw_x / self.raw_max_x) * self.width_mm
        y_mm = (raw_y / self.raw_max_y) * self.height_mm

        self.data_points.append({'x': raw_x, 'y': raw_y}) # 保存原始值
        self.point_added.emit(x_mm, y_mm) # UI 顯示用 mm
        self.last_point_time = now

    def get_data(self):
        return self.data_points

    def clear_data(self):
        self.data_points = []
        self._pen_down = False
        self.recording_cleared.emit()


# ==================== 軌跡預覽 (From v3, Modified for Raw Input) ====================
class TabletPreviewWidget(QGraphicsView):
    def __init__(self, tablet_info):
        super().__init__()
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
        # 根據實體尺寸繪製背景
        ratio = self.w_mm / self.h_mm if self.h_mm else 1.6
        if ratio >= 1:
            sw, sh = 1200, 1200 / ratio
        else:
            sh, sw = 1200, 1200 * ratio

        self.scene.setSceneRect(0, 0, sw, sh)
        self.scene.addRect(0, 0, sw, sh, QPen(Qt.NoPen), QColor(45, 45, 45))
        
        # 繪製邊框
        border = QPen(QColor(100, 100, 100))
        border.setWidth(3)
        self.scene.addRect(0, 0, sw, sh, border)
        
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def add_point(self, x_mm, y_mm):
        # 接收 mm 座標
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
        # result 的 x_offset, y_offset 是相對於中心的偏移 (mm)
        # 中心點 mm
        cx_mm = self.w_mm / 2 + result['x_offset_mm']
        cy_mm = self.h_mm / 2 + result['y_offset_mm']

        # 左上角 mm
        x_mm = cx_mm - area_w / 2
        y_mm = cy_mm - area_h / 2

        # 轉場景座標
        rx = (x_mm / self.w_mm) * rect.width()
        ry = (y_mm / self.h_mm) * rect.height()
        rw = (area_w / self.w_mm) * rect.width()
        rh = (area_h / self.h_mm) * rect.height()

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
    def calculate(data_points, tablet_info, screen_ratio=None):
        """data_points: list of {'x': raw, 'y': raw}"""
        if len(data_points) < 50:
            return None

        max_x = tablet_info['max_x']
        max_y = tablet_info['max_y']
        phy_w = tablet_info['width_mm']
        phy_h = tablet_info['height_mm']

        xs = sorted([p['x'] for p in data_points])
        ys = sorted([p['y'] for p in data_points])

        n = len(data_points)
        lo = max(0, int(n * 0.02))
        hi = min(n - 1, int(n * 0.98) - 1)

        raw_x_min = xs[lo]
        raw_x_max = xs[hi]
        raw_y_min = ys[lo]
        raw_y_max = ys[hi]

        raw_w = raw_x_max - raw_x_min
        raw_h = raw_y_max - raw_y_min

        # 比例校正 (基於原始計數)
        if screen_ratio and screen_ratio > 0 and raw_h > 0:
            current_ratio = raw_w / raw_h
            if current_ratio > screen_ratio:
                # 太寬，增加高度來符合比例
                target_h = raw_w / screen_ratio
                diff = target_h - raw_h
                raw_h = target_h
                raw_y_min -= diff / 2
                raw_y_max += diff / 2
            else:
                # 太高，增加寬度
                target_w = raw_h * screen_ratio
                diff = target_w - raw_w
                raw_w = target_w
                raw_x_min -= diff / 2
                raw_x_max += diff / 2

        # 轉為 mm
        width_mm = (raw_w / max_x) * phy_w
        height_mm = (raw_h / max_y) * phy_h

        # 計算中心偏移 (mm)
        center_raw_x = (raw_x_min + raw_x_max) / 2
        center_raw_y = (raw_y_min + raw_y_max) / 2
        
        tablet_center_x = max_x / 2
        tablet_center_y = max_y / 2
        
        x_offset_mm = ((center_raw_x - tablet_center_x) / max_x) * phy_w
        y_offset_mm = ((center_raw_y - tablet_center_y) / max_y) * phy_h

        return {
            'width_mm': round(width_mm, 2),
            'height_mm': round(height_mm, 2),
            'x_offset_mm': round(x_offset_mm, 2),
            'y_offset_mm': round(y_offset_mm, 2),
            'total_points': n,
            'used_points': hi - lo + 1
        }


# ==================== 主視窗 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.tablet_info = TabletDetector.detect()
        self.hid_reader = HIDTabletReader(self.tablet_info)
        self.recorder = DataRecorder(self.tablet_info)
        self.auto_detect_enabled = False
        self.was_playing = False

        self._build_ui()
        self._connect_tablet()

        # 輪詢 Timer (5ms)
        self.poll_timer = QTimer()
        self.poll_timer.setInterval(5)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        # Osu Timer (2s)
        self.osu_timer = QTimer()
        self.osu_timer.setInterval(2000)
        self.osu_timer.timeout.connect(self._check_osu)

        self._setup_hotkeys()

    def _build_ui(self):
        self.setWindowTitle("OTD 區域計算器 v4 (Raw Input)")
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
        scroll_area.setMinimumWidth(400)
        scroll_area.setMaximumWidth(450)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")

        left_widget = QWidget()
        ll = QVBoxLayout(left_widget)
        ll.setSpacing(8)
        ll.setContentsMargins(10, 5, 10, 5)

        title = QLabel("🎯 OTD 區域計算器 v4")
        title.setFont(QFont("Microsoft JhengHei", 16, QFont.Bold))
        ll.addWidget(title)
        
        subtitle = QLabel("原始輸入版 - 不依賴 OTD 設定")
        subtitle.setStyleSheet("color: #666; font-size: 12px;")
        ll.addWidget(subtitle)

        # 手寫板資訊
        info_box = QGroupBox("手寫板資訊")
        info_lay = QVBoxLayout()
        name = self.tablet_info['name']
        self.model_label = QLabel(f"型號：{name}")
        self.model_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        self.size_label = QLabel(
            f"物理尺寸：{self.tablet_info['width_mm']} x {self.tablet_info['height_mm']} mm")
        self.max_bg_label = QLabel(
            f"原始解析度：{self.tablet_info['max_x']} x {self.tablet_info['max_y']}")
        
        info_lay.addWidget(self.model_label)
        info_lay.addWidget(self.size_label)
        info_lay.addWidget(self.max_bg_label)
        info_box.setLayout(info_lay)
        ll.addWidget(info_box)

        # 設定
        settings_box = QGroupBox("設定")
        sl = QVBoxLayout()
        
        ratio_row = QHBoxLayout()
        self.ratio_check = QCheckBox("固定螢幕比例:")
        self.ratio_input = QLineEdit("16:9")
        self.ratio_input.setFixedWidth(80)
        ratio_row.addWidget(self.ratio_check)
        ratio_row.addWidget(self.ratio_input)
        sl.addLayout(ratio_row)

        self.auto_check = QCheckBox("自動偵測 osu! 並錄製")
        self.auto_check.toggled.connect(self._toggle_auto_detect)
        sl.addWidget(self.auto_check)
        
        settings_box.setLayout(sl)
        ll.addWidget(settings_box)

        # 按鈕
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("開始錄製 (F10)")
        self.start_btn.setStyleSheet("background-color: #28a745; color: white; padding: 8px; border-radius: 5px;")
        self.start_btn.clicked.connect(self._start_recording)

        self.stop_btn = QPushButton("停止錄製 (F11)")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #dc3545; color: white; padding: 8px; border-radius: 5px;")
        self.stop_btn.clicked.connect(self._stop_recording)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        ll.addLayout(btn_row)

        self.calc_btn = QPushButton("計算區域")
        self.calc_btn.setStyleSheet("background-color: #007bff; color: white; padding: 8px; border-radius: 5px;")
        self.calc_btn.clicked.connect(self._calculate)
        ll.addWidget(self.calc_btn)

        # 狀態
        self.status_label = QLabel("⬜ 就緒")
        self.status_label.setStyleSheet("padding: 6px; background-color: #f0f0f0; border-radius: 5px;")
        ll.addWidget(self.status_label)

        # 結果
        self.result_text = QLabel("")
        self.result_text.setWordWrap(True)
        self.result_text.setStyleSheet("background-color: #f9f9f9; padding: 10px; font-family: Consolas;")
        ll.addWidget(self.result_text)

        scroll_area.setWidget(left_widget)
        
        # ── 右側預覽 ──
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("📊 物理區域預覽"), 0, Qt.AlignHCenter)
        self.preview = TabletPreviewWidget(self.tablet_info)
        rl.addWidget(self.preview)

        # Connect signals
        self.recorder.point_added.connect(self.preview.add_point)
        self.recorder.pen_lifted.connect(self.preview.new_stroke)
        self.recorder.recording_cleared.connect(self.preview.clear_strokes)

        main_layout.addWidget(scroll_area)
        main_layout.addWidget(right, 1)

    def _connect_tablet(self):
        if self.hid_reader.open():
            self.status_label.setText("✅ 手寫板已連接 (HID Raw Mode)")
            self.status_label.setStyleSheet("padding: 6px; background-color: #d4edda; border-radius: 5px;")
        else:
            self.status_label.setText("❌ HID 設備開啟失敗 / 無法獨佔")
            self.status_label.setStyleSheet("padding: 6px; background-color: #f8d7da; border-radius: 5px;")

    def _poll(self):
        # Read all available packets in the buffer
        while True:
            packet = self.hid_reader.read_packet()
            if packet is None: # Buffer empty
                break
            if not packet: # Invalid data, skip
                continue
                
            self.recorder.process_packet(packet)


    def _toggle_auto_detect(self, checked):
        self.auto_detect_enabled = checked
        if checked:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.osu_timer.start()
            self.status_label.setText("🔍 等待偵測 osu!...")
        else:
            self.osu_timer.stop()
            self.status_label.setText("⬜ 手動模式")
            self.start_btn.setEnabled(True)

    def _check_osu(self):
        if not self.auto_detect_enabled: return
        playing, title = OsuDetector.is_playing()
        if playing and not self.was_playing:
            self.was_playing = True
            self._start_recording()
            self.status_label.setText("🔴 自動錄製中")
            self.status_label.setStyleSheet("padding: 6px; background-color: #ffcccc; border-radius: 5px;")
        elif not playing and self.was_playing:
            self.was_playing = False
            self._stop_recording()
            self.status_label.setText("🔍 等待下次遊玩...")
            self.status_label.setStyleSheet("padding: 6px; background-color: #fff3cd; border-radius: 5px;")

    def _start_recording(self):
        self.recorder.start_recording()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        if not self.auto_detect_enabled:
            self.status_label.setText("🔴 錄製中...")
            self.status_label.setStyleSheet("padding: 6px; background-color: #ffcccc; border-radius: 5px;")

    def _stop_recording(self):
        count, duration = self.recorder.stop_recording()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not self.auto_detect_enabled:
            self.status_label.setText(f"⬜ 已停止 ({count} 點)")
            self.status_label.setStyleSheet("padding: 6px; background-color: #f0f0f0; border-radius: 5px;")

    def _calculate(self):
        data = self.recorder.get_data()
        ratio = None
        if self.ratio_check.isChecked():
            try:
                txt = self.ratio_input.text()
                if ':' in txt:
                    a, b = txt.split(':')
                    ratio = float(a)/float(b)
                else:
                    ratio = float(txt)
            except:
                ratio = 16/9
        
        result = CalculationEngine.calculate(data, self.tablet_info, ratio)
        if not result:
            self.result_text.setText("數據不足或計算失敗")
            return

        self.result_text.setText(
            f"✓ 計算完成！\n"
            f"Width:    {result['width_mm']} mm\n"
            f"Height:   {result['height_mm']} mm\n"
            f"X Offset: {result['x_offset_mm']} mm\n"
            f"Y Offset: {result['y_offset_mm']} mm"
        )
        self.preview.show_calculated_area(result)

    def _setup_hotkeys(self):
        try:
            keyboard.on_press_key('F10', lambda _: self._hotkey_start())
            keyboard.on_press_key('F11', lambda _: self._hotkey_stop())
        except: pass

    def _hotkey_start(self):
        if not self.auto_detect_enabled and not self.recorder.recording:
            QTimer.singleShot(0, self._start_recording)

    def _hotkey_stop(self):
        if self.recorder.recording:
            QTimer.singleShot(0, self._stop_recording)

    def closeEvent(self, event):
        self.hid_reader.close()
        try: keyboard.unhook_all()
        except: pass
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft JhengHei", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
