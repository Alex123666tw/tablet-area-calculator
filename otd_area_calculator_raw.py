"""
OTD 區域計算器 v3 — 純 HID Raw Data 模式
直接切換手寫板至 Report ID 0x02 模式，讀取絕對座標。
參考 OpenTabletDriver 實作：
  - Feature Report [0x02, 0x02] 切換模式
  - Report ID 0x02: X=[2:3], Y=[4:5], P=[6:7]
  - 100 lines/mm 解析度
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


# ==================== HID 讀取器 (Pure Raw Mode) ====================

class HIDTabletReader:
    REPORT_ID = 0x02  # 目標 Report ID (OTD 模式)

    def __init__(self, tablet_info):
        self.tablet_info = tablet_info
        self.device = None
        self.running = False
        self.last_valid_packet = None

    def open(self):
        vid = self.tablet_info.get('vendor_id')
        pid = self.tablet_info.get('product_id')
        if not vid or not pid:
            return False

        devices = hid.enumerate(vid, pid)
        print(f"[HID] 找到 {len(devices)} 個介面")

        # 嘗試所有介面
        for i, d in enumerate(devices):
            path = d.get('path', b'')
            try:
                dev = hid.device()
                dev.open_path(path)
                dev.set_nonblocking(True)

                # 關鍵：發送 Feature Report 切換到 Report ID 0x02 模式
                # 這是 OpenTabletDriver 用來初始化板子的方法
                print(f"  -> 正在初始化介面 [{i}] (Feature Report [0x02, 0x02])...")
                try:
                    dev.send_feature_report([0x02, 0x02])
                    print("     Feature Report 發送成功！")
                except Exception as e:
                    print(f"     Feature Report 發送失敗（可能已在模式中？或不支援）: {e}")
                    # 繼續嘗試使用，也許已經在模式中了

                self.device = dev
                self.running = True
                print(f"[HID] 已連接並初始化介面 [{i}]")
                return True
            except Exception as e:
                print(f"  -> 介面 [{i}] 開啟失敗: {e}")
                continue

        return False

    def read_packet(self):
        """讀取並解析 HID 封包"""
        if not self.device or not self.running:
            return None
        
        try:
            data = self.device.read(64)
            if not data:
                return None
            
            # 解析 Report ID 0x02
            # 格式參考 OTD IntuosTabletReport.cs
            # X=[2:3], Y=[4:5], P=[6:7]
            report_id = data[0]
            
            if report_id == 0x02 and len(data) >= 8:
                status = data[1]
                x = data[2] | (data[3] << 8)
                y = data[4] | (data[5] << 8)
                p = data[6] | (data[7] << 8)
                
                # Bit 0 通常是 Tip Switch
                # Bit 1 = Side 1, Bit 2 = Side 2, Bit 3 = Eraser
                tip_down = (status & 0x01) != 0
                
                # 雙重確認：壓力大於閾值才算觸碰 (避免懸浮時 P=7 之類的問題)
                is_touching = tip_down and (p > 50)
                
                packet = {
                    'x': x,
                    'y': y,
                    'pressure': p,
                    'touching': is_touching,
                    'raw_status': status,
                    'id': report_id
                }
                self.last_valid_packet = packet
                return packet

            # Return raw data for debugging
            return {'raw': data, 'id': report_id}

        except Exception as e:
            return {'error': str(e)}

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
    """記錄 Raw Tablet Data (mm)"""
    point_added = Signal(float, float)
    pen_lifted = Signal()
    recording_cleared = Signal()

    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_w_mm = tablet_info['width_mm']
        self.tablet_h_mm = tablet_info['height_mm']
        self.max_x = tablet_info['max_x']
        self.max_y = tablet_info['max_y']
        
        # 單位換算： Intuos 通常是 100 lines/mm
        # 驗證： 15200 / 152.0 = 100.0
        self.scale_x = self.tablet_w_mm / self.max_x if self.max_x else 0.01
        self.scale_y = self.tablet_h_mm / self.max_y if self.max_y else 0.01

        self.recording = False
        self.data_points = []
        self.start_time = None
        self._pen_down = False

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

    def process_packet(self, packet):
        """處理 HID 封包"""
        if not self.recording:
            return

        is_touching = packet['touching']
        
        if is_touching:
            if not self._pen_down:
                self._pen_down = True
                self.pen_lifted.emit()  # 新筆畫
            
            # 轉換為 mm
            x_mm = packet['x'] * self.scale_x
            y_mm = packet['y'] * self.scale_y
            
            self.data_points.append({'x_mm': x_mm, 'y_mm': y_mm})
            self.point_added.emit(x_mm, y_mm)
            
        else:
            if self._pen_down:
                self._pen_down = False

    def get_data(self):
        return self.data_points

    def clear_data(self):
        self.data_points = []
        self._pen_down = False
        self.recording_cleared.emit()


# ==================== 軌跡預覽 ====================

class TabletPreviewWidget(QGraphicsView):
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
        rect = self.scene.sceneRect()
        sx = (x_mm / self.w_mm) * rect.width()
        sy = (y_mm / self.h_mm) * rect.height()
        # 翻轉 Y 軸? HID 座標通常左上是 (0,0)，也就是 Y 向下增加。
        # Qt 座標也是 Y 向下增加。所以不需要翻轉，除非 OTD 定義不同。
        # Intuos Raw Data: (0,0) is Top-Left.
        
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
        self.hid_reader = HIDTabletReader(self.tablet_info)
        self.recorder = DataRecorder(self.tablet_info)
        self.auto_detect_enabled = False
        self.was_playing = False

        self._build_ui()
        self._connect_tablet()

        # 輪詢 (2ms for smoother raw data)
        self.poll_timer = QTimer()
        self.poll_timer.setInterval(2)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        # osu! 偵測 (2s)
        self.osu_timer = QTimer()
        self.osu_timer.setInterval(2000)
        self.osu_timer.timeout.connect(self._check_osu)

        self._setup_hotkeys()

    def _build_ui(self):
        self.setWindowTitle("OTD 區域計算器 v3 (HID Raw Mode)")
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
        
        mode_label = QLabel("模式：純 HID Raw Data (模擬 OTD 驅動讀取)")
        mode_label.setStyleSheet("color: #666; font-size: 11px;")
        ll.addWidget(mode_label)

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

        info_lay.addWidget(self.model_label)
        info_lay.addWidget(self.coords_label)
        info_lay.addWidget(self.size_label)
        info_box.setLayout(info_lay)
        ll.addWidget(info_box)

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
            "【注意】\n"
            "此版本直接讀取手寫板原始資料，\n"
            "若遇到衝突請嘗試關閉其他手寫板驅動。"
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
                self.status_label.setText("✅ 手寫板已連接 (Raw Report 0x02 Mode)")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #d4edda;")
            else:
                self.status_label.setText("❌ HID 設備開啟失敗 (請關閉其他驅動)")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #f8d7da;")
        else:
            self.status_label.setText("❌ 未偵測到手寫板")
            self.status_label.setStyleSheet(
                "padding: 6px; border-radius: 5px; background-color: #f8d7da;")

    def _poll(self):
        """讀取 HID 資料"""
        if not self.hid_reader or not self.hid_reader.running:
            return
        
        # 讀取封包
        packet = self.hid_reader.read_packet()
        
        if packet:
            if 'error' in packet:
                # 讀取錯誤
                pass
            elif 'raw' in packet:
                # 除錯：顯示原始數據 (非 0x02 Report ID)
                raw_hex = bytes(packet['raw'][:8]).hex().upper()
                rid = packet['id']
                self.status_label.setText(f"⚠️ 收到非 0x02 封包 (ID={rid:02X}): {raw_hex}...")
                self.status_label.setStyleSheet(
                    "padding: 6px; border-radius: 5px; background-color: #fff3cd;")
            else:
                # 正常封包
                self.recorder.process_packet(packet)
                # 恢復正常狀態顯示
                if not self.recorder.recording:
                    self.status_label.setText("✅ 接收數據中 (Report ID 0x02 OK)")
                    self.status_label.setStyleSheet(
                        "padding: 6px; border-radius: 5px; background-color: #d4edda;")

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


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft JhengHei", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
