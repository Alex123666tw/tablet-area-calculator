"""
OTD Area Calculator - Wacom/OTD 手寫板區域計算器
使用 Win32 Wintab API 監聽手寫板原始座標，計算最佳 OpenTabletDriver 區域設定
"""

import sys
import ctypes
from ctypes import wintypes, Structure, POINTER, c_uint, c_int, c_long, c_char, byref, sizeof
import numpy as np
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QLineEdit, 
                               QTextEdit, QGroupBox, QGridLayout)
from PySide6.QtCore import QTimer, Signal, QObject
from PySide6.QtGui import QFont
import keyboard
import threading


# ==================== Wintab API 定義 ====================

# Wintab 常數
WTI_DEVICES = 100
WTI_DDCTXS = 400
WTI_DEFCONTEXT = 3
DVC_NAME = 1
DVC_X = 2
DVC_Y = 3
DVC_NPRESSURE = 15

LCTYPE_PEN = 0x0001
PK_CONTEXT = 0x0001
PK_X = 0x0002
PK_Y = 0x0004
PK_NORMAL_PRESSURE = 0x0400

WT_PACKET = 0x7FF0
WT_PROXIMITY = 0x7FF5


# Wintab 結構體
class AXIS(Structure):
    _fields_ = [
        ("axMin", c_long),
        ("axMax", c_long),
        ("axUnits", c_uint),
        ("axResolution", c_int)
    ]


class LOGCONTEXT(Structure):
    _fields_ = [
        ("lcName", c_char * 40),
        ("lcOptions", c_uint),
        ("lcStatus", c_uint),
        ("lcLocks", c_uint),
        ("lcMsgBase", c_uint),
        ("lcDevice", c_uint),
        ("lcPktRate", c_uint),
        ("lcPktData", c_uint),
        ("lcPktMode", c_uint),
        ("lcMoveMask", c_uint),
        ("lcBtnDnMask", c_long),
        ("lcBtnUpMask", c_long),
        ("lcInOrgX", c_long),
        ("lcInOrgY", c_long),
        ("lcInOrgZ", c_long),
        ("lcInExtX", c_long),
        ("lcInExtY", c_long),
        ("lcInExtZ", c_long),
        ("lcOutOrgX", c_long),
        ("lcOutOrgY", c_long),
        ("lcOutOrgZ", c_long),
        ("lcOutExtX", c_long),
        ("lcOutExtY", c_long),
        ("lcOutExtZ", c_long),
        ("lcSensX", c_int),
        ("lcSensY", c_int),
        ("lcSensZ", c_int),
        ("lcSysMode", c_int),
        ("lcSysOrgX", c_int),
        ("lcSysOrgY", c_int),
        ("lcSysExtX", c_int),
        ("lcSysExtY", c_int),
        ("lcSysSensX", c_int),
        ("lcSysSensY", c_int)
    ]


class PACKET(Structure):
    _fields_ = [
        ("pkContext", c_uint),
        ("pkX", c_long),
        ("pkY", c_long),
        ("pkNormalPressure", c_uint)
    ]


# ==================== Wintab API 包裝類 ====================

class WintabAPI:
    """Win32 Wintab API 包裝器"""
    
    def __init__(self):
        self.wintab = None
        self.context = None
        self.hwnd = None
        self.tablet_info = {
            'name': 'Unknown',
            'max_x': 0,
            'max_y': 0,
            'physical_width_mm': 0,
            'physical_height_mm': 0
        }
        
        try:
            self.wintab = ctypes.windll.LoadLibrary("Wintab32.dll")
            print("✓ Wintab32.dll 載入成功")
        except Exception as e:
            print(f"✗ 無法載入 Wintab32.dll: {e}")
            raise
    
    def detect_tablet(self):
        """自動偵測手寫板規格"""
        if not self.wintab:
            return False
        
        try:
            # 獲取設備名稱
            device_name = ctypes.create_string_buffer(50)
            self.wintab.WTInfoA(WTI_DEVICES, DVC_NAME, byref(device_name))
            self.tablet_info['name'] = device_name.value.decode('utf-8', errors='ignore')
            
            # 獲取 X 軸資訊
            axis_x = AXIS()
            self.wintab.WTInfoA(WTI_DEVICES, DVC_X, byref(axis_x))
            self.tablet_info['max_x'] = axis_x.axMax
            
            # 獲取 Y 軸資訊
            axis_y = AXIS()
            self.wintab.WTInfoA(WTI_DEVICES, DVC_Y, byref(axis_y))
            self.tablet_info['max_y'] = axis_y.axMax
            
            # 計算實體尺寸（mm）
            # axResolution 是每英寸的單位數，axUnits 通常是 TU_INCHES (1)
            if axis_x.axResolution > 0:
                self.tablet_info['physical_width_mm'] = (axis_x.axMax / axis_x.axResolution) * 25.4
            
            if axis_y.axResolution > 0:
                self.tablet_info['physical_height_mm'] = (axis_y.axMax / axis_y.axResolution) * 25.4
            
            print(f"✓ 偵測到手寫板: {self.tablet_info['name']}")
            print(f"  最大座標: {self.tablet_info['max_x']} × {self.tablet_info['max_y']}")
            print(f"  實體尺寸: {self.tablet_info['physical_width_mm']:.1f} × {self.tablet_info['physical_height_mm']:.1f} mm")
            
            return True
            
        except Exception as e:
            print(f"✗ 偵測手寫板失敗: {e}")
            return False
    
    def open_context(self, hwnd):
        """開啟 Wintab 上下文"""
        self.hwnd = hwnd
        
        try:
            # 獲取預設上下文
            lc = LOGCONTEXT()
            self.wintab.WTInfoA(WTI_DEFCONTEXT, 0, byref(lc))
            
            # 設定上下文選項
            lc.lcOptions |= LCTYPE_PEN
            lc.lcPktData = PK_X | PK_Y | PK_NORMAL_PRESSURE | PK_CONTEXT
            lc.lcPktMode = 0
            lc.lcMoveMask = PK_X | PK_Y | PK_NORMAL_PRESSURE
            
            # 開啟上下文
            self.context = self.wintab.WTOpenA(hwnd, byref(lc), True)
            
            if self.context:
                print("✓ Wintab 上下文開啟成功")
                return True
            else:
                print("✗ 無法開啟 Wintab 上下文")
                return False
                
        except Exception as e:
            print(f"✗ 開啟上下文失敗: {e}")
            return False
    
    def get_packet(self):
        """獲取手寫板封包"""
        if not self.context:
            return None
        
        try:
            packet = PACKET()
            if self.wintab.WTPacket(self.context, 0, byref(packet)):
                return {
                    'x': packet.pkX,
                    'y': packet.pkY,
                    'pressure': packet.pkNormalPressure
                }
        except:
            pass
        
        return None
    
    def close(self):
        """關閉 Wintab 上下文"""
        if self.context and self.wintab:
            try:
                self.wintab.WTClose(self.context)
                print("✓ Wintab 上下文已關閉")
            except:
                pass


# ==================== 數據記錄器 ====================

class DataRecorder(QObject):
    """數據記錄管理器"""
    
    status_changed = Signal(str)
    
    def __init__(self):
        super().__init__()
        self.recording = False
        self.data_points = []
        self.wintab = None
        self.poll_timer = None
    
    def set_wintab(self, wintab):
        """設定 Wintab API 實例"""
        self.wintab = wintab
    
    def start_recording(self):
        """開始記錄"""
        if not self.wintab or not self.wintab.context:
            self.status_changed.emit("錯誤：Wintab 未初始化")
            return
        
        self.recording = True
        self.data_points = []
        self.status_changed.emit("🔴 錄製中... (按 F11 停止)")
        print("開始記錄手寫板數據")
    
    def stop_recording(self):
        """停止記錄"""
        self.recording = False
        count = len(self.data_points)
        self.status_changed.emit(f"⏹ 已停止 (記錄了 {count} 個數據點)")
        print(f"停止記錄，共 {count} 個數據點")
    
    def add_packet(self, packet):
        """添加封包數據（僅在有壓力時）"""
        if self.recording and packet and packet['pressure'] > 0:
            self.data_points.append({
                'x': packet['x'],
                'y': packet['y']
            })
    
    def get_data(self):
        """獲取記錄的數據"""
        return self.data_points.copy()
    
    def clear_data(self):
        """清除數據"""
        self.data_points = []


# ==================== 計算引擎 ====================

class CalculationEngine:
    """Sweet Spot 計算引擎"""
    
    @staticmethod
    def calculate_area(data_points, aspect_ratio, tablet_info):
        """
        計算最佳區域設定
        
        Args:
            data_points: 記錄的座標點列表 [{'x': int, 'y': int}, ...]
            aspect_ratio: 螢幕寬高比 (width/height)
            tablet_info: 手寫板資訊字典
        
        Returns:
            dict: 包含 width_mm, height_mm, x_offset_mm, y_offset_mm
        """
        if len(data_points) < 10:
            return None
        
        # 轉換為 numpy 陣列
        coords = np.array([[p['x'], p['y']] for p in data_points])
        
        # 1. 離散值過濾：移除最外層 2% 的極端點
        x_coords = coords[:, 0]
        y_coords = coords[:, 1]
        
        x_min_threshold = np.percentile(x_coords, 2)
        x_max_threshold = np.percentile(x_coords, 98)
        y_min_threshold = np.percentile(y_coords, 2)
        y_max_threshold = np.percentile(y_coords, 98)
        
        # 過濾數據
        mask = (
            (x_coords >= x_min_threshold) & (x_coords <= x_max_threshold) &
            (y_coords >= y_min_threshold) & (y_coords <= y_max_threshold)
        )
        filtered_coords = coords[mask]
        
        if len(filtered_coords) < 5:
            return None
        
        # 2. 計算 Bounding Box
        x_min = filtered_coords[:, 0].min()
        x_max = filtered_coords[:, 0].max()
        y_min = filtered_coords[:, 1].min()
        y_max = filtered_coords[:, 1].max()
        
        width_counts = x_max - x_min
        height_counts = y_max - y_min
        
        # 3. 自動比例校正
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
        
        # 4. 單位轉換：counts → mm
        max_x = tablet_info['max_x']
        max_y = tablet_info['max_y']
        physical_width = tablet_info['physical_width_mm']
        physical_height = tablet_info['physical_height_mm']
        
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
            'data_points_total': len(data_points)
        }


# ==================== 主視窗 ====================

class MainWindow(QMainWindow):
    """主應用程式視窗"""
    
    def __init__(self):
        super().__init__()
        self.wintab = None
        self.recorder = DataRecorder()
        self.poll_timer = QTimer()
        
        self.init_ui()
        self.init_wintab()
        self.setup_hotkeys()
        
        # 連接信號
        self.recorder.status_changed.connect(self.update_status)
        self.poll_timer.timeout.connect(self.poll_tablet)
        
        # 開始輪詢手寫板
        self.poll_timer.start(5)  # 每 5ms 輪詢一次
    
    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("OTD 區域計算器 - Wacom/OTD Area Calculator")
        self.setMinimumSize(600, 500)
        
        # 主 widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # 標題
        title = QLabel("🎯 OTD 區域計算器")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # 手寫板資訊區
        tablet_group = QGroupBox("手寫板資訊")
        tablet_layout = QGridLayout()
        
        self.tablet_name_label = QLabel("偵測中...")
        self.tablet_coords_label = QLabel("-")
        self.tablet_size_label = QLabel("-")
        
        tablet_layout.addWidget(QLabel("型號:"), 0, 0)
        tablet_layout.addWidget(self.tablet_name_label, 0, 1)
        tablet_layout.addWidget(QLabel("最大座標:"), 1, 0)
        tablet_layout.addWidget(self.tablet_coords_label, 1, 1)
        tablet_layout.addWidget(QLabel("實體尺寸:"), 2, 0)
        tablet_layout.addWidget(self.tablet_size_label, 2, 1)
        
        tablet_group.setLayout(tablet_layout)
        layout.addWidget(tablet_group)
        
        # 設定區
        settings_group = QGroupBox("設定")
        settings_layout = QHBoxLayout()
        
        settings_layout.addWidget(QLabel("螢幕比例:"))
        self.aspect_ratio_input = QLineEdit("16:9")
        self.aspect_ratio_input.setMaximumWidth(100)
        settings_layout.addWidget(self.aspect_ratio_input)
        settings_layout.addStretch()
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # 控制區
        control_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("開始錄製 (F10)")
        self.start_btn.clicked.connect(self.start_recording)
        self.start_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; padding: 10px; font-weight: bold; }")
        
        self.stop_btn = QPushButton("停止錄製 (F11)")
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton { background-color: #f44336; color: white; padding: 10px; font-weight: bold; }")
        
        self.calculate_btn = QPushButton("計算區域")
        self.calculate_btn.clicked.connect(self.calculate_area)
        self.calculate_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; padding: 10px; font-weight: bold; }")
        
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.calculate_btn)
        
        layout.addLayout(control_layout)
        
        # 狀態顯示
        self.status_label = QLabel("準備就緒")
        self.status_label.setStyleSheet("QLabel { padding: 10px; background-color: #e0e0e0; border-radius: 5px; }")
        layout.addWidget(self.status_label)
        
        # 結果顯示
        results_group = QGroupBox("計算結果")
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setMaximumHeight(150)
        results_layout = QVBoxLayout()
        results_layout.addWidget(self.results_text)
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)
        
        # 說明
        info_label = QLabel(
            "使用說明：\n"
            "1. 確認手寫板已連接並顯示資訊\n"
            "2. 按 F10 或點擊「開始錄製」\n"
            "3. 在 osu! 中遊玩一首歌（正常移動即可）\n"
            "4. 按 F11 或點擊「停止錄製」\n"
            "5. 點擊「計算區域」查看建議設定"
        )
        info_label.setStyleSheet("QLabel { color: #666; font-size: 10pt; padding: 10px; }")
        layout.addWidget(info_label)
    
    def init_wintab(self):
        """初始化 Wintab"""
        try:
            self.wintab = WintabAPI()
            
            if self.wintab.detect_tablet():
                # 更新 UI
                info = self.wintab.tablet_info
                self.tablet_name_label.setText(info['name'])
                self.tablet_coords_label.setText(f"{info['max_x']} × {info['max_y']}")
                self.tablet_size_label.setText(f"{info['physical_width_mm']:.1f} × {info['physical_height_mm']:.1f} mm")
                
                # 開啟上下文
                hwnd = int(self.winId())
                if self.wintab.open_context(hwnd):
                    self.recorder.set_wintab(self.wintab)
                    self.update_status("✓ 手寫板已就緒")
                else:
                    self.update_status("✗ 無法開啟 Wintab 上下文")
            else:
                self.update_status("✗ 未偵測到手寫板")
                
        except Exception as e:
            self.update_status(f"✗ 初始化失敗: {e}")
    
    def setup_hotkeys(self):
        """設定熱鍵"""
        def on_f10():
            if not self.recorder.recording:
                self.start_recording()
        
        def on_f11():
            if self.recorder.recording:
                self.stop_recording()
        
        # 在背景執行緒中監聽熱鍵
        def hotkey_thread():
            keyboard.add_hotkey('f10', on_f10)
            keyboard.add_hotkey('f11', on_f11)
            keyboard.wait()  # 保持執行緒運行
        
        thread = threading.Thread(target=hotkey_thread, daemon=True)
        thread.start()
    
    def poll_tablet(self):
        """輪詢手寫板數據"""
        if self.wintab and self.wintab.context:
            packet = self.wintab.get_packet()
            if packet:
                self.recorder.add_packet(packet)
    
    def start_recording(self):
        """開始錄製"""
        self.recorder.start_recording()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
    
    def stop_recording(self):
        """停止錄製"""
        self.recorder.stop_recording()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
    
    def calculate_area(self):
        """計算區域"""
        data = self.recorder.get_data()
        
        if len(data) < 10:
            self.results_text.setText("錯誤：數據點不足（至少需要 10 個點）")
            return
        
        # 解析螢幕比例
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
            self.wintab.tablet_info
        )
        
        if result:
            output = f"""
✓ 計算完成！

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
- 螢幕比例: {aspect_ratio:.3f}

請在 OpenTabletDriver 中套用以上設定。
"""
            self.results_text.setText(output)
        else:
            self.results_text.setText("錯誤：計算失敗（數據不足或無效）")
    
    def update_status(self, message):
        """更新狀態"""
        self.status_label.setText(message)
    
    def closeEvent(self, event):
        """關閉事件"""
        if self.wintab:
            self.wintab.close()
        event.accept()


# ==================== 主程式 ====================

def main():
    app = QApplication(sys.argv)
    
    # 設定應用程式樣式
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
