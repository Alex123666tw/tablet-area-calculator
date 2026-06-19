"""
OTD 區域計算器 v5 — 穩定修復版
基於 v4 介面加上 v2 穩定且經過驗證的核心計算邏輯。
修正了手動模式無法記錄、軌跡漂移和 HID 緩衝區卡死的問題。
"""

import sys
import time
import ctypes
import hid
import keyboard
import json
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


# ==================== HID 讀取器 (v5 修復版 - 穩定讀取 v2) ====================
class HIDTabletReader:
    """使用 HID API 穩定讀取手寫板原始數據"""
    
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
            
            self.device.set_nonblocking(True)
            
            try:
                self.device.send_feature_report([0x02, 0x02])
                print("[HID] Wacom Feature Report [0x02, 0x02] 已發送 (切換模式)")
            except Exception as e:
                print(f"[HID] Feature Report 發送失敗: {e}")
            
            self.running = True
            return True
            
        except Exception as e:
            print(f"[HID] 無法打開設備: {e}")
            return False

    def read_packet(self):
        if not self.device or not self.running:
            return None
            
        try:
            data = self.device.read(64)
            if not data:
                return None  # 緩衝區為空
                
            packet = None
            if len(data) >= 8:
                report_id = data[0]
                # 解析 Wacom 標準 Report ID 0x10
                if report_id == 0x10:
                    x = data[2] | (data[3] << 8)
                    y = data[4] | (data[5] << 8)
                    p = data[6] | (data[7] << 8)
                    
                    if 0 < x < 40000 and 0 < y < 40000:
                        packet = {'x': x, 'y': y, 'pressure': p}
                        self.last_packet = packet
                # 其他報表 ID (例如 0x02 狀態) 不解析，但我們回傳 {} 而不是 None，避免上層迴圈提早中斷
                else:
                    return {} 
                    
            return packet if packet else {} # 回傳 {} 表示這包是成功讀取但我們不需要的封包
            
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


# ==================== 數據記錄器 (恢復 v2 邏輯) ====================
class DataRecorder(QObject):
    point_added = Signal(float, float)
    pen_lifted = Signal()
    recording_cleared = Signal()

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        self.recording = False
        self.data_points = []
        self.start_time = None
        self.last_point_time = 0
        self._pen_was_down = False
        
        self.raw_max_x = tablet_info['max_x']
        self.raw_max_y = tablet_info['max_y']
        self.width_mm = tablet_info['width_mm']
        self.height_mm = tablet_info['height_mm']

    def start_recording(self):
        self.recording = True
        self.data_points = []
        self.start_time = time.time()
        self._pen_was_down = False
        self.recording_cleared.emit()

    def stop_recording(self):
        self.recording = False
        duration = time.time() - self.start_time if self.start_time else 0
        self._pen_was_down = False
        return len(self.data_points), duration

    def process_packet(self, packet):
        # packet可能是空的dict {} (不需要的封包)，或者是None (緩衝區空)
        if not self.recording or not packet:
            # 放開筆的邏輯改放在這，如果太久沒有壓力更新，視同放開
            return

        # 確保是有效的筆觸資料
        if 'pressure' not in packet:
            return

        pressure = packet['pressure']
        
        if pressure > 0:
            if not self._pen_was_down:
                self._pen_was_down = True
                self.pen_lifted.emit()
            
            now = time.time()
            # 防抖與取樣率限制 (5ms 一點)
            if now - self.last_point_time > 0.005:
                raw_x = packet['x']
                raw_y = packet['y']
                
                # 簡單邊界過濾
                if raw_x > self.raw_max_x * 1.5 or raw_y > self.raw_max_y * 1.5:
                    return
                
                # 轉 mm 顯示用
                x_mm = (raw_x / self.raw_max_x) * self.width_mm
                y_mm = (raw_y / self.raw_max_y) * self.height_mm
                
                self.data_points.append({'x': raw_x, 'y': raw_y})
                self.point_added.emit(x_mm, y_mm)
                self.last_point_time = now

        else:
            if self._pen_was_down:
                self._pen_was_down = False

    def get_data(self):
        return self.data_points

    def clear_data(self):
        self.data_points = []
        self._pen_was_down = False
        self.recording_cleared.emit()


# ==================== 軌跡預覽 ====================
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
        rect = self.scene.sceneRect()
        sx = (x_mm / self.w_mm) * rect.width()
        sy = (y_mm / self.h_mm) * rect.height()
        sx = max(0.0, min(sx, rect.width()))
        sy = max(0.0, min(sy, rect.height()))

        self.current_stroke.append((sx, sy))
        if len(self.current_stroke) > 1:
            px, py = self.current_stroke[-2]
            pen = QPen(QColor(100, 150, 255))
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
        
        cx_mm = self.w_mm / 2 + result['x_offset_mm']
        cy_mm = self.h_mm / 2 + result['y_offset_mm']

        x_mm = cx_mm - area_w / 2
        y_mm = cy_mm - area_h / 2

        rx = (x_mm / self.w_mm) * rect.width()
        ry = (y_mm / self.h_mm) * rect.height()
        rw = (area_w / self.w_mm) * rect.width()
        rh = (area_h / self.h_mm) * rect.height()

        pen = QPen(QColor(255, 220, 50))
        pen.setWidth(3)
        brush = QBrush(QColor(255, 220, 50, 60))
        self.scene.addRect(rx, ry, rw, rh, pen, brush)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        
    def showEvent(self, event):
        super().showEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)


# ==================== 區域計算引擎 (v2 完美還原) ====================
class CalculationEngine:
    @staticmethod
    def calculate(data_points, tablet_info, aspect_ratio=None):
        """完全還原 v2 的 Sweet Spot 計算邏輯"""
        if len(data_points) < 10:
            return None

        # 1. 離散值過濾：移除最外層 2% 的極端點
        xs = sorted([p['x'] for p in data_points])
        ys = sorted([p['y'] for p in data_points])
        
        n = len(data_points)
        idx_min = int(n * 0.02)
        idx_max = int(n * 0.98)
        
        x_min_th = xs[idx_min]
        x_max_th = xs[idx_max]
        y_min_th = ys[idx_min]
        y_max_th = ys[idx_max]
        
        filtered = [p for p in data_points if (x_min_th <= p['x'] <= x_max_th and y_min_th <= p['y'] <= y_max_th)]
        
        if len(filtered) < 5:
            return None
            
        # 2. 邊界框計數
        x_vals = [p['x'] for p in filtered]
        y_vals = [p['y'] for p in filtered]
        
        x_min = min(x_vals)
        x_max = max(x_vals)
        y_min = min(y_vals)
        y_max = max(y_vals)
        
        width_counts = x_max - x_min
        height_counts = y_max - y_min
        
        # 3. 自動比例校正
        if aspect_ratio:
            current_ratio = width_counts / height_counts if height_counts > 0 else 1
            if current_ratio > aspect_ratio:
                # 太寬，加高
                target_height = width_counts / aspect_ratio
                expansion = (target_height - height_counts) / 2
                y_min -= expansion
                y_max += expansion
                height_counts = target_height
            else:
                # 太高，加寬
                target_width = height_counts * aspect_ratio
                expansion = (target_width - width_counts) / 2
                x_min -= expansion
                x_max += expansion
                width_counts = target_width
                
        # 4. 單位轉換
        max_x = tablet_info['max_x']
        max_y = tablet_info['max_y']
        phy_w = tablet_info['width_mm']
        phy_h = tablet_info['height_mm']
        
        if max_x == 0 or max_y == 0:
            return None
            
        width_mm = (width_counts / max_x) * phy_w
        height_mm = (height_counts / max_y) * phy_h
        
        # 5. 計算偏移
        center_x_counts = (x_min + x_max) / 2
        center_y_counts = (y_min + y_max) / 2
        
        tablet_center_x = max_x / 2
        tablet_center_y = max_y / 2
        
        x_offset_mm = ((center_x_counts - tablet_center_x) / max_x) * phy_w
        y_offset_mm = ((center_y_counts - tablet_center_y) / max_y) * phy_h
        
        return {
            'width_mm': round(width_mm, 2),
            'height_mm': round(height_mm, 2),
            'x_offset_mm': round(x_offset_mm, 2),
            'y_offset_mm': round(y_offset_mm, 2),
            'used_points': len(filtered),
            'total_points': len(data_points)
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
        self.last_result = None

        self._build_ui()
        self._connect_tablet()

        # 輪詢 Timer，降至 1ms 避免掉包，且迴圈內不中斷直到空
        self.poll_timer = QTimer()
        self.poll_timer.setInterval(1)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        self.osu_timer = QTimer()
        self.osu_timer.setInterval(2000)
        self.osu_timer.timeout.connect(self._check_osu)

        self._setup_hotkeys()

    def _build_ui(self):
        self.setWindowTitle("OTD 區域計算器 v5 (穩定修復版)")
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

        title = QLabel("🎯 OTD 區域計算器 v5")
        title.setFont(QFont("Microsoft JhengHei", 16, QFont.Bold))
        ll.addWidget(title)

        # 手寫板資訊
        info_box = QGroupBox("硬體資訊")
        info_lay = QVBoxLayout()
        name = self.tablet_info['name']
        self.model_label = QLabel(f"硬體：{name}")
        self.model_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        self.size_label = QLabel(f"實體：{self.tablet_info['width_mm']} x {self.tablet_info['height_mm']} mm")
        self.max_bg_label = QLabel(f"座標：{self.tablet_info['max_x']} x {self.tablet_info['max_y']} (Raw)")
        
        info_lay.addWidget(self.model_label)
        info_lay.addWidget(self.size_label)
        info_lay.addWidget(self.max_bg_label)
        
        self.refresh_btn = QPushButton("重新偵測裝置")
        self.refresh_btn.setStyleSheet("background-color: #f8f9fa; border: 1px solid #ccc; border-radius: 4px; padding: 4px;")
        self.refresh_btn.clicked.connect(self._refresh_device)
        info_lay.addWidget(self.refresh_btn)
        
        info_box.setLayout(info_lay)
        ll.addWidget(info_box)

        # 設定
        settings_box = QGroupBox("追蹤設定")
        sl = QVBoxLayout()
        
        ratio_row = QHBoxLayout()
        self.ratio_check = QCheckBox("固定比例鎖定:")
        self.ratio_check.setChecked(True)
        self.ratio_input = QLineEdit("16:9")
        self.ratio_input.setFixedWidth(80)
        ratio_row.addWidget(self.ratio_check)
        ratio_row.addWidget(self.ratio_input)
        sl.addLayout(ratio_row)

        self.auto_check = QCheckBox("自動偵測 osu! (進入遊玩狀態自動錄製)")
        self.auto_check.toggled.connect(self._toggle_auto_detect)
        sl.addWidget(self.auto_check)
        
        settings_box.setLayout(sl)
        ll.addWidget(settings_box)

        # 按鈕
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("開始錄製 (F10)")
        self.start_btn.setStyleSheet("background-color: #28a745; color: white; padding: 10px; border-radius: 6px; font-weight: bold;")
        self.start_btn.clicked.connect(self._start_recording)

        self.stop_btn = QPushButton("停止錄製 (F11)")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #dc3545; color: white; padding: 10px; border-radius: 6px; font-weight: bold;")
        self.stop_btn.clicked.connect(self._stop_recording)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        ll.addLayout(btn_row)

        calc_row = QHBoxLayout()
        self.calc_btn = QPushButton("計算最佳甜蜜點")
        self.calc_btn.setStyleSheet("background-color: #007bff; color: white; padding: 10px; border-radius: 6px; font-weight: bold;")
        self.calc_btn.clicked.connect(self._calculate)
        calc_row.addWidget(self.calc_btn)
        
        self.export_btn = QPushButton("複製 OTD 設定檔")
        self.export_btn.setEnabled(False)
        self.export_btn.setStyleSheet("background-color: #6c757d; color: white; padding: 10px; border-radius: 6px; font-weight: bold;")
        self.export_btn.clicked.connect(self._export_config)
        calc_row.addWidget(self.export_btn)
        
        ll.addLayout(calc_row)

        # 狀態
        self.status_label = QLabel("⬜ 準備就緒")
        self.status_label.setStyleSheet("padding: 8px; background-color: #f0f0f0; border-radius: 5px;")
        ll.addWidget(self.status_label)

        # 結果
        self.result_text = QLabel("等待運算...")
        self.result_text.setWordWrap(True)
        self.result_text.setStyleSheet("background-color: #fffaf0; border: 1px solid #ffeeba; padding: 10px; font-family: Consolas;")
        ll.addWidget(self.result_text)

        scroll_area.setWidget(left_widget)
        
        # ── 右側預覽 ──
        right = QWidget()
        rl = QVBoxLayout(right)
        title_pre = QLabel("📊 實體繪圖板鏡射追蹤 (Raw Mirror)")
        rl.addWidget(title_pre, 0, Qt.AlignHCenter)
        self.preview = TabletPreviewWidget(self.tablet_info)
        rl.addWidget(self.preview)

        # Connect signals
        self.recorder.point_added.connect(self.preview.add_point)
        self.recorder.pen_lifted.connect(self.preview.new_stroke)
        self.recorder.recording_cleared.connect(self.preview.clear_strokes)

        main_layout.addWidget(scroll_area)
        main_layout.addWidget(right, 1)

    def _refresh_device(self):
        self.hid_reader.close()
        self.tablet_info = TabletDetector.detect()
        self.model_label.setText(f"硬體：{self.tablet_info['name']}")
        self.size_label.setText(f"實體：{self.tablet_info['width_mm']} x {self.tablet_info['height_mm']} mm")
        self.max_bg_label.setText(f"座標：{self.tablet_info['max_x']} x {self.tablet_info['max_y']} (Raw)")
        self.hid_reader = HIDTabletReader(self.tablet_info)
        self.recorder = DataRecorder(self.tablet_info)
        
        # 重新綁定預覽
        self.preview.w_mm = self.tablet_info['width_mm']
        self.preview.h_mm = self.tablet_info['height_mm']
        self.preview.clear_strokes()
        self.recorder.point_added.connect(self.preview.add_point)
        self.recorder.pen_lifted.connect(self.preview.new_stroke)
        self.recorder.recording_cleared.connect(self.preview.clear_strokes)
        
        self._connect_tablet()

    def _connect_tablet(self):
        if self.hid_reader.open():
            self.status_label.setText("✅ 硬體感測器已連線 (HID Raw Mode)")
            self.status_label.setStyleSheet("padding: 8px; background-color: #d4edda; border-radius: 5px;")
        else:
            self.status_label.setText("❌ 硬體連線失敗 (可能被 OTD 等軟體獨佔)")
            self.status_label.setStyleSheet("padding: 8px; background-color: #f8d7da; border-radius: 5px;")

    def _poll(self):
        # 確保緩衝區完全清空，讀到 None 為止
        # 忽略不是 0x10 的封包 ({})，但繼續讀
        while True:
            packet = self.hid_reader.read_packet()
            if packet is None:
                break
            if not packet:
                continue
            self.recorder.process_packet(packet)

    def _toggle_auto_detect(self, checked):
        self.auto_detect_enabled = checked
        if checked:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.osu_timer.start()
            self.status_label.setText("🔍 自動模式：等待 osu! 進入遊玩...")
        else:
            self.osu_timer.stop()
            self.status_label.setText("⬜ 手動模式：請自行控制錄製")
            self.start_btn.setEnabled(True)

    def _check_osu(self):
        if not self.auto_detect_enabled: return
        playing, title = OsuDetector.is_playing()
        if playing and not self.was_playing:
            self.was_playing = True
            self._start_recording()
            self.status_label.setText("🔴 osu! 遊玩中：自動錄製...")
            self.status_label.setStyleSheet("padding: 8px; background-color: #ffcccc; border-radius: 5px; color: #a00; font-weight: bold;")
        elif not playing and self.was_playing:
            self.was_playing = False
            self._stop_recording()
            self.status_label.setText("🔍 結算中：等待下一首...")
            self.status_label.setStyleSheet("padding: 8px; background-color: #fff3cd; border-radius: 5px;")
            self._calculate()

    def _start_recording(self):
        self.recorder.start_recording()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.last_result = None
        if not self.auto_detect_enabled:
            self.status_label.setText("🔴 錄製中，盡情移動筆尖吧！")
            self.status_label.setStyleSheet("padding: 8px; background-color: #ffcccc; border-radius: 5px;")

    def _stop_recording(self):
        count, duration = self.recorder.stop_recording()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        m, s = int(duration // 60), int(duration % 60)
        if not self.auto_detect_enabled:
            self.status_label.setText(f"⬜ 錄製完成：共收集 {count} 個座標 ({m}分{s}秒)")
            self.status_label.setStyleSheet("padding: 8px; background-color: #e2e3e5; border-radius: 5px;")

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
            self.result_text.setText("⚠️ 樣本數不足，計算失敗。請確保有足夠的繪圖數據。")
            self.export_btn.setEnabled(False)
            return

        self.last_result = result
        self.export_btn.setEnabled(True)
        self.export_btn.setStyleSheet("background-color: #17a2b8; color: white; padding: 10px; border-radius: 6px; font-weight: bold;")

        self.result_text.setText(
            f"✨ 計算完成！成功過濾 {result['total_points'] - result['used_points']} 個雜訊點。\n\n"
            f"▼ OTD 建議設定值:\n"
            f" 寬度 (Width):  {result['width_mm']} mm\n"
            f" 高度 (Height): {result['height_mm']} mm\n"
            f" X 軸偏移 (X):  {result['x_offset_mm']} mm\n"
            f" Y 軸偏移 (Y):  {result['y_offset_mm']} mm"
        )
        self.preview.show_calculated_area(result)

    def _export_config(self):
        if not self.last_result:
            return
            
        r = self.last_result
        config = {
            "Width": r['width_mm'],
            "Height": r['height_mm'],
            "X": r['x_offset_mm'],
            "Y": r['y_offset_mm'],
            "Rotation": 0
        }
        
        cb = QApplication.clipboard()
        cb.setText(json.dumps(config, indent=2))
        self.export_btn.setText("✓ 已複製到剪貼簿")
        QTimer.singleShot(2000, lambda: self.export_btn.setText("複製 OTD 設定檔"))

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
