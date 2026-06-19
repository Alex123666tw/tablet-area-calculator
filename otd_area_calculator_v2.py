"""
OTD 區域計算器 v2 - 簡化版
使用 HID API 直接讀取手寫板數據（不需要 Wacom 驅動）
適用於 Wacom CTL-4100 及其他手寫板
"""

import sys
import ctypes
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QLineEdit, 
                               QTextEdit, QGroupBox, QGridLayout, QCheckBox,
                               QGraphicsView, QGraphicsScene, QSizePolicy, QFormLayout)
from PySide6.QtCore import QTimer, Signal, Qt, QRectF, QObject
from PySide6.QtGui import QFont, QTabletEvent, QPen, QColor, QBrush, QPainter, QIcon, QScreen
import keyboard
import threading
import hid
import time
try:
    import win32gui
except ImportError:
    win32gui = None



# ==================== HID 手寫板讀取器 ====================


class HIDTabletReader:
    """使用 HID API 讀取手寫板數據（不需要 Wacom 驅動）"""
    
    def __init__(self, tablet_info):
        self.tablet_info = tablet_info
        self.device = None
        self.running = False
        self.last_packet = None
        
    def open(self):
        """打開 HID 設備（智慧介面選擇）"""
        try:
            vid = self.tablet_info.get('vendor_id')
            pid = self.tablet_info.get('product_id')
            
            if not vid or not pid:
                print("[ERROR] 無法獲取手寫板 VID/PID")
                return False
            
            # 列舉所有 HID 介面，找到正確的 Digitizer 介面
            all_devices = hid.enumerate(vid, pid)
            print(f"[INFO] 找到 {len(all_devices)} 個 HID 介面:")
            
            digitizer_path = None
            all_paths = []
            for i, dev in enumerate(all_devices):
                up = dev.get('usage_page', 0)
                u = dev.get('usage', 0)
                iface = dev.get('interface_number', -1)
                path = dev.get('path', b'')
                print(f"  [{i}] UsagePage=0x{up:04X} Usage=0x{u:02X} Interface={iface}")
                all_paths.append(path)
                
                # Usage Page 0x0D = Digitizer, Usage 0x02 = Pen
                if up == 0x000D:
                    digitizer_path = path
                    print(f"  >>> 找到 Digitizer 介面!")
            
            self.device = hid.device()
            
            if digitizer_path:
                # 優先使用 Digitizer 介面
                print(f"[INFO] 嘗試打開 Digitizer 介面...")
                self.device.open_path(digitizer_path)
            else:
                # 沒有找到 Digitizer，嘗試所有介面
                print("[WARN] 未找到 Digitizer 介面，嘗試逐一打開...")
                opened = False
                for path in all_paths:
                    try:
                        self.device.open_path(path)
                        opened = True
                        print(f"[OK] 已打開介面")
                        break
                    except Exception:
                        continue
                if not opened:
                    # 最後手段：用 VID/PID 打開
                    self.device.open(vid, pid)
            
            self.device.set_nonblocking(True)
            
            # 發送 Wacom Feature Init Report
            # 這個命令讓手寫板切換到「數位筆模式」，座標才會正確 (MaxX=15200, MaxY=9500)
            # 參考: OpenTabletDriver FeatureInitReport [2, 2]
            try:
                self.device.send_feature_report([0x02, 0x02])
                print("[OK] Wacom Feature Report 已發送 (切換到數位筆模式)")
            except Exception as e:
                print(f"[WARN] Feature Report 發送失敗: {e}")
            
            print(f"[OK] HID 設備已打開: VID=0x{vid:04X}, PID=0x{pid:04X}")
            self.running = True
            return True
            
        except Exception as e:
            print(f"[ERROR] 無法打開 HID 設備: {e}")
            return False
    
    def read_packet(self):
        """讀取一個 HID 報告並解析為座標和壓力"""
        if not self.device or not self.running:
            return None
        
        try:
            # 讀取 HID 報告
            # 注意: 即使是短封包（例如滑鼠模式數據）也回傳以供 Debug
            data = self.device.read(64)
            
            if not data:
                return self.last_packet  # 返回上一個有效的封包
            
            # 轉換為 bytes 以便處理
            data_bytes = bytes(data)
            
            # 基本封包結構
            packet = {
                'x': 0,
                'y': 0,
                'pressure': 0,
                'raw_hex': data_bytes.hex().upper()
            }
            
            # 嘗試解析 (如果長度足夠)
            # Wacom 封包通常至少 8-10 bytes
            if len(data) >= 8:
                report_id = data[0]
                
                # 只解析 Report ID 16 (0x10) - 這是 CTL-4100 的標準筆數據
                if report_id == 0x10:
                    try:
                        # 提取 X 座標（Byte 2-3）
                        x = data[2] | (data[3] << 8)
                        
                        # 提取 Y 座標（Byte 4-5）
                        y = data[4] | (data[5] << 8)
                        
                        # 提取壓力（Byte 6-7）
                        p = data[6] | (data[7] << 8)
                        
                        # 過濾無效座標:
                        # 1. (0,0): 初始或無數據
                        # 2. > 40000: 異常高值 (例如 0xFFFF = 65535，代表無效/溢位)
                        if x > 0 and y > 0 and x < 40000 and y < 40000:
                            packet['x'] = x
                            packet['y'] = y
                            packet['pressure'] = p
                            self.last_packet = packet
                                
                    except IndexError:
                        pass
                else:
                    # 其他 Report ID (如 0x02 狀態包) 忽略，保持壓力為 0 以免繪製雜訊
                    pass
            
            return packet
            
        except Exception as e:
            # 讀取錯誤時返回上一個有效封包
            return self.last_packet
    
    def close(self):
        """關閉 HID 設備"""
        self.running = False
        if self.device:
            try:
                self.device.close()
                print("[OK] HID 設備已關閉")
            except:
                pass




class TabletDetector:
    """使用 HID API 偵測手寫板規格"""
    
    # 已知手寫板型號的規格資料庫
    # 注意: max_x / max_y 是 HID 原始座標的最大值
    # 通過 Raw HID 讀取時，座標空間通常是正方形 (max_x ≈ max_y)
    # 實際手寫板的長寬比由 width_mm / height_mm 決定
    TABLET_SPECS = {
        # Wacom Intuos 系列
        (0x056a, 0x0374): {  # CTL-4100 (Small)
            'name': 'Wacom Intuos CTL-4100 (Small)',
            'max_x': 15200,
            'max_y': 9500,
            'width_mm': 152.0,
            'height_mm': 95.0
        },
        (0x056a, 0x0375): {  # CTL-6100 (Medium)
            'name': 'Wacom Intuos CTL-6100 (Medium)',
            'max_x': 21600,
            'max_y': 13500,
            'width_mm': 216.0,
            'height_mm': 135.0
        },
        (0x056a, 0x0376): {  # CTL-4100WL (Small Wireless)
            'name': 'Wacom Intuos CTL-4100WL (Small)',
            'max_x': 15200,
            'max_y': 9500,
            'width_mm': 152.0,
            'height_mm': 95.0
        },
    }
    
    @staticmethod
    def detect():
        """偵測連接的手寫板"""
        devices = hid.enumerate()
        
        for device in devices:
            vid = device['vendor_id']
            pid = device['product_id']
            
            # 檢查是否在已知型號列表中
            if (vid, pid) in TabletDetector.TABLET_SPECS:
                spec = TabletDetector.TABLET_SPECS[(vid, pid)].copy()
                spec['vendor_id'] = vid
                spec['product_id'] = pid
                return spec
            
            # 如果是 Wacom 但不在列表中，使用預設值
            if vid == 0x056a:
                product = device.get('product_string', 'Unknown Wacom')
                return {
                    'name': f'Wacom {product}',
                    'vendor_id': vid,
                    'product_id': pid,
                    'max_x': 15200,
                    'max_y': 9500,  # OTD 標準值
                    'width_mm': 152.0,
                    'height_mm': 95.0
                }
        
        return None


# ==================== osu! 偵測器 ====================

class OsuGameStateDetector:
    """osu! 遊戲狀態偵測器（基於視窗標題）"""
    
    @staticmethod
    def get_osu_window_title():
        """獲取 osu! 視窗標題"""
        if not win32gui:
            return None
            
        window_title = [None]
        
        def callback(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title.startswith('osu!'):
                    ctx[0] = title
                    
        try:
            win32gui.EnumWindows(callback, window_title)
        except Exception:
            return None
            
        return window_title[0]
    
    @staticmethod
    def is_playing_song():
        """判斷是否正在遊玩歌曲"""
        title = OsuGameStateDetector.get_osu_window_title()
        if not title:
            return False, "Not Running"
            
        # 標題為 "osu!" 表示在主選單或加載中
        if title == 'osu!':
            return False, "Menu"
            
        # 排除非遊玩狀態的關鍵字
        excluded = [
            'Song Select', 
            'Ranking', 
            'Results',
            'Edit', 
            'Multiplayer', 
            'Options',
            'Chat',
            'Spectator'
        ]
        
        if any(ex in title for ex in excluded):
            return False, "Menu"
            
        # 進入歌曲時，標題通常格式為：osu! - Artist - Title [Difficulty]
        if ' - ' in title:
            return True, title
            
        return False, "Menu"


class OsuDetector:
    """偵測 osu! 執行狀態"""
    
    @staticmethod
    def is_osu_running():
        """檢查 osu! 是否正在執行 (檢查視窗是否存在)"""
        if OsuGameStateDetector.get_osu_window_title():
            return True
        return False


# ==================== 數據記錄器 ====================

class DataRecorder(QObject):
    """數據記錄管理器"""
    
    point_added = Signal(float, float)  # 新增數據點信號
    pen_lifted = Signal()  # 筆離開手寫板時（斷開筆畫）
    recording_cleared = Signal()  # 清除數據信號
    
    def __init__(self):
        super().__init__()
        self.recording = False
        self.data_points = []
        self.last_point_time = 0
        self.hid_reader = None
        self._pen_was_down = False  # 追蹤筆的狀態
    
    def set_hid_reader(self, hid_reader):
        """設定 HID 讀取器實例"""
        self.hid_reader = hid_reader
    
    def start_recording(self):
        """開始記錄"""
        self.recording = True
        self.data_points = []
        self.start_time = time.time()  # 記錄開始時間
        self.recording_cleared.emit()
        print("開始記錄手寫板數據")
    
    def stop_recording(self):
        """停止記錄"""
        self.recording = False
        duration = time.time() - self.start_time if hasattr(self, 'start_time') else 0
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        print(f"停止記錄，共 {len(self.data_points)} 個數據點，時長 {minutes} 分 {seconds} 秒")
        return minutes, seconds
    
    def add_packet(self, packet):
        """添加封包數據（僅在有壓力時）"""
        if not self.recording or not packet:
            return
            
        if packet['pressure'] > 0:
            # 如果筆剛觸碰下來（之前是抬起的），發送新筆畫信號
            if not self._pen_was_down:
                self._pen_was_down = True
                self.pen_lifted.emit()  # 通知預覽開始新筆畫
            
            current_time = time.time()
            # 防止重複點（每 5ms 最多一點）
            if current_time - self.last_point_time > 0.005:
                self.data_points.append({
                    'x': packet['x'],
                    'y': packet['y']
                })
                # 發送信號給預覽組件
                self.point_added.emit(packet['x'], packet['y'])
                self.last_point_time = current_time
                
                # 每 100 點印一次進度
                if len(self.data_points) % 100 == 0:
                    print(f"[DEBUG] 已記錄 {len(self.data_points)} 個數據點")
        else:
            # 筆離開手寫板
            if self._pen_was_down:
                self._pen_was_down = False
    
    def get_data(self):
        """獲取記錄的數據"""
        return self.data_points.copy()
    
    def clear_data(self):
        """清除數據"""
        self.data_points = []
        self.recording_cleared.emit()




class TabletPreviewWidget(QGraphicsView):
    """手寫板軌跡預覽組件（類似 OTD 鏡射介面）"""
    
    def __init__(self, tablet_info):
        super().__init__()
        self.tablet_info = tablet_info
        
        # 設定場景
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        
        
        # 設定視圖屬性
        self.setRenderHint(QPainter.Antialiasing)
        
        # 啟用 tablet 追蹤
        self.setAttribute(Qt.WA_TabletTracking, True)
        
        # 讓預覽區域完全擴展以佔據右側所有空間
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 設定白色背景（非手寫板區域）
        self.setStyleSheet("background-color: white; border: 2px solid #555;")
        
        # 繪製邊框
        self.draw_border()
        
        # 儲存軌跡點
        self.stroke_points = []
        self.current_stroke = []
        
        # 記錄螢幕尺寸用於座標轉換
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        self.screen_width = screen.width()
        self.screen_height = screen.height()
        
        print(f"預覽初始化: 螢幕={self.screen_width}x{self.screen_height}, 手寫板={tablet_info['max_x']}x{tablet_info['max_y']}")
    
    def draw_border(self):
        """繪製手寫板邊框"""
        # 使用「實體尺寸比例」來決定預覽區域的形狀
        # 這樣不管座標空間是正方形還是長方形，預覽都會正確
        w_mm = self.tablet_info.get('width_mm', 152.0)
        h_mm = self.tablet_info.get('height_mm', 95.0)
        ratio = w_mm / h_mm  # 實體長寬比 (例如 152/95 ≈ 1.6)
        
        # 設定場景大小（使用手寫板的實體比例）
        if ratio > 1:  # 橫向手寫板
            scene_width = 1200
            scene_height = scene_width / ratio
        else:  # 縱向手寫板
            scene_height = 1200
            scene_width = scene_height * ratio
        
        self.scene.setSceneRect(0, 0, scene_width, scene_height)
        
        # 繪製手寫板區域背景（深灰色）
        bg_brush = QColor(45, 45, 45)
        self.scene.addRect(0, 0, scene_width, scene_height, QPen(Qt.NoPen), bg_brush)
        
        # 繪製邊框
        border_pen = QPen(QColor(100, 100, 100))
        border_pen.setWidth(3)
        self.scene.addRect(0, 0, scene_width, scene_height, border_pen)
        
        # 使用 fitInView 確保完整顯示（保持比例）
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        
        print(f"場景設定: {scene_width:.0f} x {scene_height:.0f} (實體比例 {ratio:.2f})")
    
    def add_point(self, x, y):
        """添加軌跡點"""
        # x, y 是手寫板的原始座標（0-max_x, 0-max_y）
        # 需要轉換到場景座標
        scene_rect = self.scene.sceneRect()
        max_x = self.tablet_info['max_x']
        max_y = self.tablet_info['max_y']
        
        # 先限制座標在手寫板範圍內 (防止超出規格的數值破壞顯示)
        x = max(0, min(x, max_x))
        y = max(0, min(y, max_y))
        
        # 正規化座標 (0-1 範圍) - 使用手寫板的最大座標
        norm_x = x / max_x if max_x > 0 else 0
        norm_y = y / max_y if max_y > 0 else 0
        
        # 映射到場景座標
        scene_x = norm_x * scene_rect.width()
        scene_y = norm_y * scene_rect.height()
        
        # 限制在場景範圍內
        scene_x = max(0, min(scene_x, scene_rect.width()))
        scene_y = max(0, min(scene_y, scene_rect.height()))
        
        # Debug: 每 50 點印一次
        if len(self.current_stroke) % 50 == 0:
            print(f"[PREVIEW] 原始座標: ({x:.0f}, {y:.0f}) -> 場景座標: ({scene_x:.1f}, {scene_y:.1f})")
        
        self.current_stroke.append((scene_x, scene_y))
        
        # 如果有上一點，繪製線段
        if len(self.current_stroke) > 1:
            prev_x, prev_y = self.current_stroke[-2]
            
            # 繪製藍色筆跡
            pen = QPen(QColor(100, 150, 255))  # 藍色
            pen.setWidth(3)  # 增加線條寬度
            pen.setCapStyle(Qt.RoundCap)
            
            self.scene.addLine(prev_x, prev_y, scene_x, scene_y, pen)
    
    def new_stroke(self):
        """開始新的筆畫（筆被抬起後重新觸碰時呼叫）"""
        self.current_stroke = []
    
    def clear_strokes(self):
        """清除所有軌跡"""
        self.scene.clear()
        self.draw_border()
        self.stroke_points = []
        self.current_stroke = []
        # 清除計算區域標記
        if hasattr(self, 'calculated_area_rect'):
            self.calculated_area_rect = None
    
    def show_calculated_area(self, result):
        """顯示計算出的區域（黃色半透明矩形）"""
        if not result:
            return
        
        scene_rect = self.scene.sceneRect()
        
        # 從結果中獲取區域參數（單位：mm）
        # 注意：x_offset_mm 和 y_offset_mm 是相對於手寫板中心的偏移
        width_mm = result['width_mm']
        height_mm = result['height_mm']
        x_offset_mm = result['x_offset_mm']  # 相對於中心
        y_offset_mm = result['y_offset_mm']  # 相對於中心
        
        # 手寫板的實體尺寸（mm）
        tablet_width_mm = self.tablet_info['width_mm']
        tablet_height_mm = self.tablet_info['height_mm']
        
        # 手寫板的最大座標
        max_x = self.tablet_info['max_x']
        max_y = self.tablet_info['max_y']
        
        # 將中心相對偏移轉換為左上角絕對位置（mm）
        # 手寫板中心位置
        center_x_mm = tablet_width_mm / 2
        center_y_mm = tablet_height_mm / 2
        
        # 區域中心位置（mm，從左上角開始）
        area_center_x_mm = center_x_mm + x_offset_mm
        area_center_y_mm = center_y_mm + y_offset_mm
        
        # 區域左上角位置（mm，從左上角開始）
        area_left_mm = area_center_x_mm - (width_mm / 2)
        area_top_mm = area_center_y_mm - (height_mm / 2)
        
        # 轉換為手寫板座標
        x_tablet = (area_left_mm / tablet_width_mm) * max_x
        y_tablet = (area_top_mm / tablet_height_mm) * max_y
        width_tablet = (width_mm / tablet_width_mm) * max_x
        height_tablet = (height_mm / tablet_height_mm) * max_y
        
        # 轉換為場景座標（與 add_point 相同的邏輯）
        norm_x = x_tablet / max_x
        norm_y = y_tablet / max_y
        norm_width = width_tablet / max_x
        norm_height = height_tablet / max_y
        
        rect_x = norm_x * scene_rect.width()
        rect_y = norm_y * scene_rect.height()
        rect_width = norm_width * scene_rect.width()
        rect_height = norm_height * scene_rect.height()
        
        # 繪製黃色半透明填充矩形（在藍線下方）
        pen = QPen(Qt.NoPen)  # 無邊框
        brush = QBrush(QColor(255, 220, 0, 120))  # 黃色半透明
        
        self.calculated_area_rect = self.scene.addRect(
            rect_x, rect_y, rect_width, rect_height, pen, brush
        )
        self.calculated_area_rect.setZValue(0)  # 設置在背景之上、藍線之下
        
        print(f"[PREVIEW] 顯示計算區域: {width_mm:.1f}x{height_mm:.1f} mm")
        print(f"[PREVIEW] 中心偏移: ({x_offset_mm:.1f}, {y_offset_mm:.1f}) mm")
        print(f"[PREVIEW] 左上角位置: ({area_left_mm:.1f}, {area_top_mm:.1f}) mm")
        print(f"[PREVIEW] 場景座標: ({rect_x:.1f}, {rect_y:.1f}) 尺寸: {rect_width:.1f}x{rect_height:.1f}")
    
    def resizeEvent(self, event):
        """視窗大小變更時自動縮放"""
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
    
    def showEvent(self, event):
        """視窗顯示時自動縮放"""
        super().showEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)


# ==================== 計算引擎 ====================

class CalculationEngine:
    """Sweet Spot 計算引擎"""
    
    @staticmethod
    def calculate_area(data_points, aspect_ratio, tablet_info):
        """計算最佳區域設定"""
        if len(data_points) < 10:
            return None
        
        # 1. 離散值過濾：移除最外層 2% 的極端點
        x_coords = sorted([p['x'] for p in data_points])
        y_coords = sorted([p['y'] for p in data_points])
        
        n = len(data_points)
        # 取 2% 和 98% 的位置
        idx_min = int(n * 0.02)
        idx_max = int(n * 0.98)
        
        x_min_threshold = x_coords[idx_min]
        x_max_threshold = x_coords[idx_max]
        y_min_threshold = y_coords[idx_min]
        y_max_threshold = y_coords[idx_max]
        
        # 過濾數據
        filtered_coords = []
        for p in data_points:
            if (x_min_threshold <= p['x'] <= x_max_threshold and 
                y_min_threshold <= p['y'] <= y_max_threshold):
                filtered_coords.append(p)
        
        if len(filtered_coords) < 5:
            return None
        
        # 2. 計算 Bounding Box
        x_values = [p['x'] for p in filtered_coords]
        y_values = [p['y'] for p in filtered_coords]
        
        x_min = min(x_values)
        x_max = max(x_values)
        y_min = min(y_values)
        y_max = max(y_values)
        
        width_counts = x_max - x_min
        height_counts = y_max - y_min
        
        # 3. 自動比例校正（僅當指定了 aspect_ratio 時）
        if aspect_ratio:
            current_ratio = width_counts / height_counts if height_counts > 0 else 1
            
            if current_ratio > aspect_ratio:
                # 太寬，調整高度
                target_height = width_counts / aspect_ratio
                height_expansion = (target_height - height_counts) / 2
                y_min -= height_expansion
                y_max += height_expansion
                height_counts = target_height
            else:
                # 太高，調整寬度
                target_width = height_counts * aspect_ratio
                width_expansion = (target_width - width_counts) / 2
                x_min -= width_expansion
                x_max += width_expansion
                width_counts = target_width
        
        # 4. 單位轉換：使用手寫板規格
        max_x = tablet_info['max_x']
        max_y = tablet_info['max_y']
        physical_width = tablet_info['width_mm']
        physical_height = tablet_info['height_mm']
        
        if max_x == 0 or max_y == 0:
            return None
        
        width_mm = (width_counts / max_x) * physical_width
        height_mm = (height_counts / max_y) * physical_height
        
        # 5. 計算偏移（相對於手寫板中心）
        center_x_counts = (x_min + x_max) / 2
        center_y_counts = (y_min + y_max) / 2
        
        tablet_center_x = max_x / 2
        tablet_center_y = max_y / 2
        
        x_offset_mm = ((center_x_counts - tablet_center_x) / max_x) * physical_width
        y_offset_mm = ((center_y_counts - tablet_center_y) / max_y) * physical_height
        
        return {
            'width_mm': width_mm,
            'height_mm': height_mm,
            'x_offset_mm': x_offset_mm,
            'y_offset_mm': y_offset_mm,
            'data_points_used': len(filtered_coords),
            'data_points_total': len(data_points),
            'calculated_ratio': width_mm / height_mm if height_mm > 0 else 0
        }


# ==================== 主視窗 ====================

class MainWindow(QMainWindow):
    """主應用程式視窗"""
    
    status_changed = Signal(str)
    
    def __init__(self):
        super().__init__()
        self.recorder = DataRecorder()
        self.tablet_info = None
        self.hid_reader = None  # 改用 HID 讀取器
        self.auto_detect_enabled = True # osu! 偵測狀態
        self.auto_detect_active = True # 是否正在進行偵測
        self.osu_was_running = False
        self.song_was_playing = False
        
        # 偵測手寫板
        self.detect_tablet()
        
        # 初始化 UI
        self.init_ui()
        self.setup_hotkeys()
        
        # 初始化 HID 讀取器
        self.init_hid_reader()
        
        # 連接信號
        self.status_changed.connect(self.update_status)
        
        # 設定 HID 輪詢計時器
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.poll_tablet)
        self.poll_timer.start(5)  # 每 5ms 輪詢一次
        
        # 設定 osu! 偵測計時器
        self.osu_detect_timer = QTimer()
        self.osu_detect_timer.timeout.connect(self.check_osu_status)
        self.osu_detect_timer.start(1000)  # 每秒檢查一次
    
    def detect_tablet(self):
        """偵測手寫板"""
        self.tablet_info = TabletDetector.detect()
        
        if self.tablet_info:
            print(f"偵測到手寫板: {self.tablet_info['name']}")
        else:
            print("未偵測到已知的手寫板，使用預設規格")
            # 使用預設規格
            self.tablet_info = {
                'name': '未知手寫板 (未偵測到)',
                'max_x': 15200,
                'max_y': 9500,
                'width_mm': 152.0,
                'height_mm': 95.0
            }
    
    def init_hid_reader(self):
        """初始化 HID 讀取器"""
        try:
            self.hid_reader = HIDTabletReader(self.tablet_info)
            
            if self.hid_reader.open():
                self.recorder.set_hid_reader(self.hid_reader)
                print("[OK] HID 讀取器初始化成功")
                # Update status label to show success temporarily or log it
                self.status_label.setText(f"✅ 手寫板已連線: {self.tablet_info['name']}")
                # Re-trigger auto detect status after a delay? 
                # Better to just let check_osu_status handle the "Searching..." text later.
                # But initial feedback is good.
                QTimer.singleShot(2000, lambda: self.status_changed.emit("🔍 自動偵測模式：等待 osu! 啟動..."))
            else:
                error_msg = "[ERROR] 無法打開 HID 設備 (Open Failed)"
                print(error_msg)
                self.hid_reader = None
                self.status_label.setText(f"❌ {error_msg}\n請檢查是否有其他驅動程式獨佔裝置")
                self.status_label.setStyleSheet("background-color: #ffcccc; padding: 10px; border-radius: 5px;")
                
        except Exception as e:
            error_msg = f"[ERROR] HID 初始化異常: {str(e)}"
            print(error_msg)
            self.hid_reader = None
            self.status_label.setText(f"❌ {error_msg}")
            self.status_label.setStyleSheet("background-color: #ffcccc; padding: 10px; border-radius: 5px;")
    
    def poll_tablet(self):
        """輪詢手寫板數據"""
        if self.hid_reader and self.hid_reader.running:
            packet = self.hid_reader.read_packet()
            
            # 更新 Debug 資訊 (每 10 次更新一次避免太頻繁)
            if hasattr(self, 'debug_label') and packet:
                 # 使用 getattr 避免 crash 如果 packet 沒有 raw_hex
                 raw = packet.get('raw_hex', 'N/A') 
                 osu_status = getattr(self, 'osu_debug_info', 'Wait...')
                 info = (f"X: {packet['x']:5d} | Y: {packet['y']:5d} | P: {packet['pressure']:4d}\n"
                         f"Raw: {raw}\n"
                         f"Rec: {'YES' if self.recorder.recording else 'NO'} | osu!: {osu_status}")
                 self.debug_label.setText(info)

            if packet:
                self.recorder.add_packet(packet)
    
    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("OTD 區域計算器 v2 - Simplified")
        self.setWindowIcon(QIcon())
        self.setMinimumSize(1000, 700)  # 增加最小寬度以容納左右分欄
        
        # 主容器
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # ==================== 主水平佈局（左右分欄） ====================
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # ==================== 左側控制面板 ====================
        left_panel = QWidget()
        left_panel.setMaximumWidth(450)
        left_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        
        # 標題
        title_label = QLabel("🎯 OTD 區域計算器 v2")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        left_layout.addWidget(title_label)
        
        # 手寫板資訊 GroupBox
        tablet_group = QGroupBox("手寫板資訊")
        tablet_info_layout = QVBoxLayout()
        tablet_info_layout.setSpacing(4)
        tablet_info_layout.setContentsMargins(15, 15, 10, 10)

        self.model_label = QLabel(f"型號：{self.tablet_info['name']}")
        if "未偵測到" in self.tablet_info['name']:
            self.model_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.model_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        self.model_label.setWordWrap(True)

        self.coords_label = QLabel(f"最大座標：{self.tablet_info['max_x']} × {self.tablet_info['max_y']}")
        self.size_label = QLabel(f"實體尺寸：{self.tablet_info['width_mm']:.1f} × {self.tablet_info['height_mm']:.1f} mm")
        
        tablet_info_layout.addWidget(self.model_label)
        tablet_info_layout.addWidget(self.coords_label)
        tablet_info_layout.addWidget(self.size_label)
        
        tablet_group.setLayout(tablet_info_layout)
        left_layout.addWidget(tablet_group)

        # 實時偵測數據 (Debug 用) - 移到上方以便查看
        self.debug_box = QGroupBox("實時偵測數據 (Debug)")
        debug_layout = QVBoxLayout()
        self.debug_label = QLabel("等待數據...\n(請使用手寫板移動游標)")
        self.debug_label.setStyleSheet("font-family: Consolas; font-size: 11px; color: #333; background-color: #e0e0e0; padding: 5px; border-radius: 3px;")
        debug_layout.addWidget(self.debug_label)
        self.debug_box.setLayout(debug_layout)
        left_layout.addWidget(self.debug_box)
        
        # 設定 GroupBox
        settings_group = QGroupBox("設定")
        settings_layout = QVBoxLayout()
        settings_layout.setSpacing(8)
        
        # 螢幕比例
        ratio_layout = QHBoxLayout()
        
        self.lock_ratio_checkbox = QCheckBox("固定螢幕比例:")
        self.lock_ratio_checkbox.setChecked(False) # 預設不固定
        ratio_layout.addWidget(self.lock_ratio_checkbox)
        
        self.aspect_ratio_input = QLineEdit("16:9") # Renamed from self.ratio_input
        self.aspect_ratio_input.setMaximumWidth(80)
        self.aspect_ratio_input.setEnabled(False) # 預設禁用，因為 checkbox 沒勾
        ratio_layout.addWidget(self.aspect_ratio_input)
        
        # 連接 checkbox 信號
        self.lock_ratio_checkbox.stateChanged.connect(
            lambda state: self.aspect_ratio_input.setEnabled(state == Qt.CheckState.Checked.value)
        )
        
        ratio_layout.addStretch()
        settings_layout.addLayout(ratio_layout)
        
        # 自動偵測 checkbox
        self.auto_detect_checkbox = QCheckBox("自動偵測 osu! 並開始錄製（不需要按 F10）")
        self.auto_detect_checkbox.setChecked(True)
        self.auto_detect_checkbox.stateChanged.connect(self.toggle_auto_detect) # Added connection
        settings_layout.addWidget(self.auto_detect_checkbox)
        
        settings_group.setLayout(settings_layout)
        left_layout.addWidget(settings_group)
        
        # 控制按鈕（水平排列）
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.start_btn = QPushButton("開始錄製 (F10)") # Renamed
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.start_btn.clicked.connect(self.start_recording)
        self.start_btn.setEnabled(False)  # 預設禁用（因為自動偵測預設開啟）
        
        self.stop_btn = QPushButton("停止錄製 (F11)") # Renamed from self.stop_button
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        
        # 重置按鈕
        self.reset_btn = QPushButton("重置數據")
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800; /* Orange/Red-ish for warning */
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        self.reset_btn.clicked.connect(self.reset_data)

        button_layout.addWidget(self.start_btn)
        button_layout.addWidget(self.stop_btn)
        button_layout.addWidget(self.reset_btn)
        left_layout.addLayout(button_layout)
        
        # 計算按鈕（全寬）
        self.calculate_btn = QPushButton("計算區域") # Renamed from self.calculate_button
        self.calculate_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
        """)
        self.calculate_btn.clicked.connect(self.calculate_area)
        left_layout.addWidget(self.calculate_btn)
        
        # 狀態標籤
        self.status_label = QLabel("🔍 自動偵測模式：等待 osu! 啟動...") # Updated initial text
        self.status_label.setWordWrap(True)  # 啟用自動換行
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                padding: 10px;
                border-radius: 5px;
                font-size: 12px;
            }
        """)
        left_layout.addWidget(self.status_label)
        
        # 計算結果
        result_label = QLabel("計算結果")
        result_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        left_layout.addWidget(result_label)
        
        self.results_text = QTextEdit() # Renamed from self.result_text
        self.results_text.setReadOnly(True)
        self.results_text.setMaximumHeight(120)
        self.results_text.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                padding: 8px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }
        """)
        left_layout.addWidget(self.results_text)
        
        # 使用說明（可摺疊）
        instructions_label = QLabel("使用說明：")
        instructions_label.setStyleSheet("font-weight: bold; font-size: 12px; margin-top: 5px;")
        left_layout.addWidget(instructions_label)
        
        instructions_text = QLabel(
            "【自動模式】（推薦）\n"
            "1. 勾選「自動偵測 osu!」\n"
            "2. 直接開啟 osu! 並遊玩，程式會自動開始錄製\n\n"
            "【手動模式】\n"
            "1. 取消勾選「自動偵測 osu!」\n"
            "2. 按下「開始錄製」或 F10 鍵\n"
            "3. 在螢幕上移動手寫板，覆蓋整個遊戲區域\n"
            "4. 按下「停止錄製」或 F11 鍵"
        )
        instructions_text.setStyleSheet("""
            QLabel {
                background-color: #f9f9f9;
                padding: 10px;
                padding-left: 12px;
                border-radius: 5px;
                font-size: 11px;
                line-height: 1.4;
            }
        """)
        instructions_text.setWordWrap(True)
        left_layout.addWidget(instructions_text)
        
        # 添加彈性空間，將內容推到頂部
        left_layout.addStretch()
        
        # ==================== 右側預覽區域 ====================
        # ==================== 右側：預覽區域 ====================
        right_panel = QWidget()
        right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(5)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 預覽標題
        preview_label = QLabel("📊 軌跡預覽")
        preview_label.setFont(QFont("Microsoft JhengHei", 14, QFont.Bold))
        preview_label.setStyleSheet("color: #333; padding: 10px;")
        right_layout.addWidget(preview_label)
        
        # 預覽組件
        self.preview_widget = TabletPreviewWidget(self.tablet_info)
        right_layout.addWidget(self.preview_widget)
        
        # ⭐ 關鍵：連接信號！
        self.recorder.point_added.connect(self.preview_widget.add_point)
        self.recorder.pen_lifted.connect(self.preview_widget.new_stroke)
        self.recorder.recording_cleared.connect(self.preview_widget.clear_strokes)
        print("[DEBUG] 信號已連接: recorder -> preview")
        
        # ==================== 組裝主佈局 ====================
        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, 1)  # stretch factor 1，佔據剩餘空間
    
    def setup_hotkeys(self):
        """設定熱鍵"""
        self.hotkeys_enabled = False
        
        def on_f10():
            if self.hotkeys_enabled and not self.recorder.recording:
                self.start_recording()
        
        def on_f11():
            if self.hotkeys_enabled and self.recorder.recording:
                self.stop_recording()
        
        # 在背景執行緒中監聽熱鍵
        def hotkey_thread():
            keyboard.add_hotkey('f10', on_f10)
            keyboard.add_hotkey('f11', on_f11)
            keyboard.wait()
        
        thread = threading.Thread(target=hotkey_thread, daemon=True)
        thread.start()
        
        # 預設停用熱鍵（因為自動偵測預設開啟）
        self.hotkeys_enabled = False
    
    def tabletEvent(self, event: QTabletEvent):
        """處理 tablet 事件"""
        # Debug: 檢查是否收到任何 tablet 事件
        print(f"[DEBUG] TabletEvent: type={event.type()}, pressure={event.pressure():.3f}")
        
        if event.type() in (QTabletEvent.TabletPress, QTabletEvent.TabletMove):
            # 獲取壓力值
            pressure = event.pressure()
            
            # 使用標準化位置 (0.0 - 1.0) 轉換為手寫板座標
            # position() 返回相對於視窗的位置
            pos_x = event.position().x()
            pos_y = event.position().y()
            
            # 獲取螢幕尺寸來標準化座標
            screen = QApplication.primaryScreen()
            screen_geometry = screen.geometry()
            screen_width = screen_geometry.width()
            screen_height = screen_geometry.height()
            
            # 轉換為全域座標
            global_pos = self.mapToGlobal(event.position().toPoint())
            global_x = global_pos.x()
            global_y = global_pos.y()
            
            # 標準化到 0-1 範圍
            norm_x = global_x / screen_width
            norm_y = global_y / screen_height
            
            # 轉換為手寫板座標系統
            tablet_x = norm_x * self.tablet_info['max_x']
            tablet_y = norm_y * self.tablet_info['max_y']
            
            print(f"[DEBUG] Recording: tablet_x={tablet_x:.1f}, tablet_y={tablet_y:.1f}, pressure={pressure:.3f}, recording={self.recorder.recording}")
            
            # 記錄數據
            self.recorder.add_point(tablet_x, tablet_y, pressure)
            
            # 如果有壓力，顯示 debug 訊息
            if pressure > 0 and len(self.recorder.data_points) % 50 == 1:
                print(f"Debug: tablet_x={tablet_x:.1f}, tablet_y={tablet_y:.1f}, pressure={pressure:.3f}")
            
            event.accept()
            return True
        else:
            event.ignore()
            return False
    
    def start_recording(self):
        """開始錄製 / 開始偵測"""
        if self.auto_detect_enabled:
            # 自動模式：開始偵測
            self.auto_detect_active = True
            
            # 更新按鈕狀態
            self.start_btn.setEnabled(False) # 已經開始了，禁用開始
            self.stop_btn.setEnabled(True)   # 可以暫停
            self.status_changed.emit("🔍 自動偵測中：等待 osu! 歌曲開始...")
            
            # 立即檢查
            self.song_was_playing = False # Reset song status to ensure detection
            self.check_osu_status()
        else:
            # 手動模式：開始錄製
            # 禁用自動偵測勾選框（避免衝突）
            self.auto_detect_checkbox.setEnabled(False)
            
            self.recorder.start_recording()
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.status_changed.emit("🔴 錄製中... (按 F11 停止)")

    def stop_recording(self):
        """停止錄製 / 暫停偵測"""
        if self.auto_detect_enabled:
            # 自動模式：暫停偵測
            self.auto_detect_active = False
            
            # 如果正在錄製中，也停止錄製
            if self.recorder.recording:
                 self.recorder.stop_recording()
                 print("[AUTO] 暫停偵測 - 停止當前錄製")

            # 更新按鈕狀態
            self.start_btn.setEnabled(True)  # 可以重新開始偵測
            self.stop_btn.setEnabled(False)  # 已經暫停了
            self.status_changed.emit("⏸ 自動偵測已暫停")
            
        else:
            # 手動模式：停止錄製
            # 重新啟用自動偵測勾選框
            self.auto_detect_checkbox.setEnabled(True)
            
            minutes, seconds = self.recorder.stop_recording()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status_changed.emit(f"⏹ 已停止 (已記錄 {minutes} 分 {seconds} 秒)")

    def reset_data(self):
        """重置數據"""
        if self.recorder.recording:
             # 如果正在錄製，先停止
             if self.auto_detect_enabled:
                self.auto_detect_active = False
                self.recorder.stop_recording()
                self.status_changed.emit("⏹ 重置數據 - 停止錄製")
             else:
                self.recorder.stop_recording()
                self.auto_detect_checkbox.setEnabled(True)
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
        
        # 清除數據
        self.recorder.data_points = []
        self.recorder.recording_cleared.emit()
        self.preview_widget.clear_strokes()
        self.results_text.clear()
        
        # 恢復按鈕狀態 (如果是自動模式，恢復到暫停狀態)
        if self.auto_detect_enabled:
             self.auto_detect_active = False
             self.start_btn.setEnabled(True)
             self.stop_btn.setEnabled(False)
             self.status_changed.emit("🗑️ 數據已重置 - 自動偵測已暫停")
        else:
             self.status_changed.emit("🗑️ 數據已重置")
    
    def calculate_area(self):
        """計算區域"""
        data = self.recorder.get_data()
        
        if len(data) < 10:
            self.results_text.setText("錯誤：數據點不足（至少需要 10 個點）\n請先錄製數據！")
            return
        
        # 解析螢幕比例
        aspect_ratio = None
        if self.lock_ratio_checkbox.isChecked():
            try:
                ratio_text = self.aspect_ratio_input.text().strip()
                if ':' in ratio_text:
                    w, h = map(float, ratio_text.split(':'))
                    aspect_ratio = w / h
                else:
                    aspect_ratio = float(ratio_text)
            except:
                self.results_text.setText("錯誤：無效的螢幕比例格式")
                return
        
        # 計算
        result = CalculationEngine.calculate_area(
            data, 
            aspect_ratio, 
            self.tablet_info
        )
        
        if result:
            output = f"""
[OK] 計算完成！

【建議的 OTD 區域設定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Width (寬度):    {result['width_mm']:.2f} mm
Height (高度):   {result['height_mm']:.2f} mm
X Offset (X偏移): {result['x_offset_mm']:.2f} mm
Y Offset (Y偏移): {result['y_offset_mm']:.2f} mm
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

數據統計：
- 總數據點: {result['data_points_total']}
- 使用數據點: {result['data_points_used']} (已過濾 {result['data_points_total'] - result['data_points_used']} 個極端點)
- 總數據點: {result['data_points_total']}
- 使用數據點: {result['data_points_used']} (已過濾 {result['data_points_total'] - result['data_points_used']} 個極端點)
- 固定比例: {"是 (" + f"{aspect_ratio:.3f}" + ")" if aspect_ratio else "否 (自由比例)"}
- 計算比例: {result['calculated_ratio']:.4f}

請在 OpenTabletDriver 中套用以上設定。
"""
            self.results_text.setText(output)
            
            # 在預覽區域顯示計算出的黃色矩形
            self.preview_widget.show_calculated_area(result)
        else:
            self.results_text.setText("錯誤：計算失敗（數據不足或無效）")
    
    def update_status(self, message):
        """更新狀態"""
        self.status_label.setText(message)
    
    def toggle_auto_detect(self, state):
        """切換自動偵測"""
        self.auto_detect_enabled = (state == Qt.CheckState.Checked.value)
        self.auto_detect_active = self.auto_detect_enabled # 預設開啟時也開啟偵測
        
        if self.auto_detect_enabled:
            # 自動模式 UI 設置
            self.start_btn.setText("開始偵測")
            self.start_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50; /* Green */
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 12px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                }
            """)
            self.start_btn.setEnabled(False) # 預設自動開始，所以開始按鈕禁用
            
            self.stop_btn.setText("暫停偵測")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FFC107; /* Amber/Yellow for Pause */
                    color: black;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 12px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #FFB300;
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                }
            """)
            self.stop_btn.setEnabled(False)
            self.reset_btn.setVisible(True) # Ensure Reset button is visible in Auto Mode
            
            self.hotkeys_enabled = False
            self.status_changed.emit("🔍 自動偵測開啟：等待 osu! 歌曲開始...")
            
            # 立即檢查 osu! 狀態
            self.song_was_playing = False
            self.check_osu_status()
            
        else:
            # 手動模式 UI 恢復
            self.start_btn.setText("開始錄製 (F10)")
            self.start_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 12px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                }
            """)
            self.start_btn.setEnabled(True)
            
            self.stop_btn.setText("停止錄製 (F11)")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f44336; /* Red */
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 12px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #da190b;
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                }
            """)
            self.stop_btn.setEnabled(False)
            self.reset_btn.setVisible(False) # Hide Reset button in Manual Mode
            
            self.hotkeys_enabled = True
            self.status_changed.emit("⏸ 手動模式：按 F10 開始錄製")
            
            # 如果正在自動錄製，停止它
            if self.recorder.recording:
                 self.recorder.stop_recording()
    
    def check_osu_status(self):
        """檢查 osu! 執行狀態（基於視窗標題）"""
        if not self.auto_detect_enabled or not self.auto_detect_active:
            self.osu_debug_info = "Disabled"
            return
        
        # 獲取視窗標題用於 Debug
        current_title = OsuGameStateDetector.get_osu_window_title()
        self.osu_debug_info = current_title if current_title else "Not Found"
        
        # 使用新的狀態偵測器
        osu_running = (current_title is not None)
        is_playing, status_text = False, "Not Running"
        
        if osu_running:
            is_playing, status_text = OsuGameStateDetector.is_playing_song()
        
        # 狀態 1: 歌曲剛開始 (開始錄製)
        if is_playing and not self.song_was_playing:
            if not self.recorder.recording:
                # 獲取歌曲名稱
                # 嘗試去除標準前綴 "osu! - "
                if "osu! - " in status_text:
                    song_info = status_text.replace("osu! - ", "")
                # 如果沒有標準前綴，嘗試去除 "osu!" 並修剪
                elif status_text.startswith("osu!"):
                     song_info = status_text[4:].strip()
                     # 如果修剪後以 "- " 開頭（例如 "osu! -Song"），再去除一次
                     if song_info.startswith("- "):
                         song_info = song_info[2:]
                else:
                    # 如果都不是，直接顯示完整標題
                    song_info = status_text
                
                # 如果結果為空，才顯示未知
                if not song_info.strip():
                    song_info = "未知曲目"
                
                self.start_recording()
                self.status_changed.emit(f"🎵 正在遊玩: {song_info} - 自動錄製中...")
                print(f"[AUTO] 歌曲開始: {song_info}")

        # 狀態 2: 歌曲剛結束 (停止錄製)
        elif not is_playing and self.song_was_playing:
            if self.recorder.recording:
                minutes, seconds = self.recorder.stop_recording()
                self.start_btn.setEnabled(False) # Keep disabled in auto mode
                self.stop_btn.setEnabled(False)
                
                # 更新狀態
                if osu_running:
                    self.status_changed.emit(f"⏹ 歌曲結束 - 已停止 (記錄 {minutes}分{seconds}秒) - 等待下一首...")
                else:
                    self.status_changed.emit(f"⏹ osu! 已關閉 - 已停止 (記錄 {minutes}分{seconds}秒)")
                print(f"[AUTO] 歌曲結束/暫停")

        # 狀態 3: 正在遊玩中 (更新狀態)
        elif is_playing and self.recorder.recording:
            # 可以選擇更新時間，但為了避免刷屏，這裡保持原樣或顯示當前時間
            pass
            
        # 狀態 4: osu! 在選單中 (等待)
        elif osu_running and not is_playing and not self.recorder.recording:
            # 只有當狀態改變時才發送，避免閃爍
            if self.status_label.text().startswith("⏳"): # 簡單檢查是否已經顯示等待訊息
                 pass
            else:
                 self.status_changed.emit("⏳ osu! 在選單中 - 等待歌曲開始...")

        self.osu_was_running = osu_running
        self.song_was_playing = is_playing


# ==================== 主程式 ====================

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
