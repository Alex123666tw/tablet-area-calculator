"""Record tablet play traces and estimate a bounded OpenTabletDriver area.

The normal desktop entry point stays here. Hardware capture, area analysis and
bounded drawing are separate modules so their failure boundaries remain clear.
"""
import json
import math
import os
import sys

# Keep pen input available to the game while respecting an explicit test platform.
os.environ.setdefault("QT_WINTAB_DISABLE", "1")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:nowmpointer")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGroupBox, QCheckBox, QFrame, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QColor, QPalette

# Re-export the original names for existing callers.
from tablet_input import TABLET_SPECS, TabletDetector, HIDPollingThread, OsuDetector, SmartRecorder
from area_analysis import ModernCalculationEngine, parse_aspect_ratio
from area_canvas import LiquidCanvas


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OTD Area Calculator — 遊玩軌跡區域估算")
        self.setMinimumSize(960, 650)
        self.tablet_info = TabletDetector.detect()
        self.recorder = SmartRecorder(self.tablet_info)
        self.auto_mode = False
        self.was_playing = False
        self.last_cfg = None
        self._cfg_context = None
        self._connected = False
        self._transitioning = False
        self._refresh_pending = False
        self._close_pending = False
        self._last_limits = None
        self._build_ui()
        self._wire_recorder()
        self.osu_timer = QTimer(self)
        self.osu_timer.setInterval(1000)
        self.osu_timer.timeout.connect(self._check_osu)
        self._start_input()
        self._update_controls()

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
        left_panel.setMinimumWidth(320)
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

        self.lbl_unverified = QLabel("⚠️ 未驗證型號：封包格式未經實機確認，數值僅供參考，歡迎回報")
        self.lbl_unverified.setWordWrap(True)
        self.lbl_unverified.setStyleSheet("background-color: #4d3a00; color: #ffd34d; border: 1px solid #806000; border-radius: 4px; padding: 6px;")
        self.lbl_unverified.setVisible(not self.tablet_info.get('verified', False))

        self.lbl_dyn_max = QLabel("座標尺度：依裝置規格")
        self.lbl_dyn_max.setStyleSheet("color: #00ff88;")

        il.addWidget(self.lbl_name)
        il.addWidget(self.lbl_unverified)
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

        self.chk_ratio = QCheckBox("鎖定螢幕比例")
        self.chk_ratio.setChecked(True)
        self.inp_ratio = QLineEdit("16:9")
        self.inp_ratio.setPlaceholderText("例如 16:9 或 1.7778")

        row1 = QHBoxLayout()
        row1.addWidget(self.chk_ratio)
        row1.addWidget(self.inp_ratio)
        sl.addLayout(row1)

        self.chk_auto = QCheckBox("自動偵測 osu! 遊玩狀態錄製")
        self.chk_auto.toggled.connect(self._toggle_auto)
        sl.addWidget(self.chk_auto)

        self.chk_hover = QCheckBox("包含懸空遊玩的軌跡")
        self.chk_hover.setChecked(True)
        self.chk_hover.toggled.connect(self._toggle_hover)
        sl.addWidget(self.chk_hover)
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
        self.btn_calc = QPushButton("✨ 計算建議區域")
        self.btn_calc.setStyleSheet("background-color: #007bff; padding: 10px; border-radius: 5px;")
        self.btn_calc.clicked.connect(self._calc)
        row2.addWidget(self.btn_calc)
        ll.addLayout(row2)

        self.btn_reset = QPushButton("🗑 清空軌跡與結果")
        self.btn_reset.setStyleSheet("background-color: #dc3545; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        self.btn_reset.clicked.connect(self._reset_all)
        ll.addWidget(self.btn_reset)

        self.btn_copy = QPushButton("📋 複製區域 JSON")
        self.btn_copy.setEnabled(False)
        self.btn_copy.setStyleSheet("background-color: #6c757d; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        self.btn_copy.clicked.connect(self._copy_cfg)
        self.btn_copy.setToolTip("複製毫米區域片段；可依寬、高與中心位置填入 OTD。")
        ll.addWidget(self.btn_copy)

        # Status Log
        self.lbl_status = QLabel("⬜ 準備就緒。")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.lbl_status.setStyleSheet("background-color: #111; border: 1px solid #000; padding: 10px; border-radius: 5px; color: #ccc;")
        ll.addWidget(self.lbl_status)

        ll.addStretch()
        self._left_scroll = QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QFrame.NoFrame)
        self._left_scroll.setMinimumWidth(350)
        self._left_scroll.setMaximumWidth(420)
        self._left_scroll.setWidget(left_panel)
        main_layout.addWidget(self._left_scroll)

        # === Right Panel (Canvas) ===
        self.canvas = LiquidCanvas(self.tablet_info['width_mm'], self.tablet_info['height_mm'])
        main_layout.addWidget(self.canvas, 1)

        # Connect UI logic
        self.inp_ratio.textChanged.connect(self._invalidate_result)
        self.chk_ratio.toggled.connect(self._invalidate_result)

    def _wire_recorder(self):
        self.recorder.record_hover = self.chk_hover.isChecked()
        self.recorder.preview_updated.connect(self._on_smart_update)
        self.recorder.recording_cleared.connect(self.canvas.clear_canvas)

    def _start_input(self):
        self.hid_thread = HIDPollingThread(self.tablet_info)
        self.hid_thread.packets_received.connect(self._on_packets)
        self.hid_thread.connection_status.connect(self._on_connection)
        self.hid_thread.finished.connect(self._on_thread_finished)
        self.hid_thread.start()

    def _from_current_thread(self):
        sender = self.sender()
        return sender is None or sender is self.hid_thread

    def _on_packets(self, batch):
        if self._from_current_thread() and not self._transitioning and not self._close_pending:
            self.recorder.on_packets(batch)

    def _on_connection(self, ok):
        if not self._from_current_thread() or self._transitioning or self._close_pending:
            return
        self._connected = bool(ok)
        if ok:
            self._log("✅ 裝置已連線。請移動筆尖確認游標，再開始錄製。")
        else:
            if self.recorder.recording:
                self.recorder.stop_recording()
            self.was_playing = False
            self._invalidate_result()
            reason = (getattr(self.hid_thread, "last_error", "")
                      or self.tablet_info.get("capture_error", ""))
            if self.tablet_info.get("vendor_id") is None:
                reason = "未偵測到手寫板，請連接裝置後重新偵測。"
            self._log("❌ " + (reason or "裝置無法讀取或已斷線，請重新偵測。"))
        self._update_controls()

    def _on_thread_finished(self):
        if not self._from_current_thread():
            return
        self._connected = False
        if self._close_pending:
            self.close()
        elif self._refresh_pending:
            self._complete_refresh()
        else:
            self._update_controls()

    def _refresh(self):
        if self._transitioning:
            return
        if self.recorder.recording:
            self.recorder.stop_recording()
        self._invalidate_result()
        self.was_playing = False
        self._connected = False
        self._transitioning = True
        self._refresh_pending = True
        self._update_controls()
        # Bounded wait: a slow device open must not freeze the GUI indefinitely.
        if self.hid_thread.stop(timeout_ms=100):
            self._complete_refresh()
        else:
            self._log("⏳ 正在結束裝置讀取；介面保持可操作，完成後會重新偵測。")

    def _complete_refresh(self):
        if not self._refresh_pending or self._close_pending:
            return
        self._refresh_pending = False
        self.tablet_info = TabletDetector.detect()
        self.lbl_name.setText(f"裝置：{self.tablet_info['name']}")
        self.lbl_unverified.setVisible(not self.tablet_info.get('verified', False))
        self.recorder = SmartRecorder(self.tablet_info)
        self._wire_recorder()
        self.canvas.phys_w = self.tablet_info['width_mm']
        self.canvas.phys_h = self.tablet_info['height_mm']
        self.canvas.clear_canvas()
        self._last_limits = None
        self.lbl_dyn_max.setText("座標尺度：依裝置規格")
        self._transitioning = False
        self._start_input()
        self._update_controls()

    def _on_smart_update(self, nx, ny, in_range, contact, mx, my):
        self.canvas.update_state(nx, ny, contact, mx, my,
                                 in_range=in_range, trace_hover=self.chk_hover.isChecked())
        limits = (mx, my)
        if limits != self._last_limits:
            self._last_limits = limits
            self.lbl_dyn_max.setText(f"裝置座標尺度：{int(mx)} × {int(my)}")

    def _toggle_auto(self, checked):
        self.auto_mode = bool(checked)
        self.was_playing = False
        self._invalidate_result()
        if self.auto_mode:
            self.osu_timer.start()
            self._log("🔍 自動模式已啟用，等待 osu! 遊玩；連續歌曲會累積有效軌跡。")
        else:
            self.osu_timer.stop()
            self._log("⬜ 手動模式已啟用。" + ("目前錄製繼續，可按停止。" if self.recorder.recording else ""))
        self._update_controls()

    def _toggle_hover(self, checked):
        if self.recorder.recording:
            self.recorder.stop_recording()
        self.recorder.record_hover = bool(checked)
        self.recorder.clear_all()
        self.was_playing = False
        self._invalidate_result()
        self._log("⬜ 錄製條件已更改，舊軌跡已清空，請重新錄製。")
        self._update_controls()

    def _update_controls(self):
        busy = self._transitioning or self._close_pending
        recording = self.recorder.recording
        self.btn_rec.setEnabled(self._connected and not busy and not recording and not self.auto_mode)
        self.btn_stop.setEnabled(recording and not busy)
        self.btn_calc.setEnabled(not recording and not busy and len(self.recorder.raw_points) >= 10)
        self.btn_reset.setEnabled(not busy)
        self.chk_auto.setEnabled(not busy)
        self.chk_hover.setEnabled(not busy)
        self.btn_copy.setEnabled(self.last_cfg is not None and not recording and not busy)

    def _btn_modes(self, can_start=None, can_stop=None):
        # Compatibility entry: enabled state comes from the real recording state.
        self._update_controls()

    def _invalidate_result(self, *_):
        had_result = self.last_cfg is not None
        self.last_cfg = None
        self._cfg_context = None
        if hasattr(self, "btn_copy"):
            self.btn_copy.setEnabled(False)
        if hasattr(self, "canvas"):
            self.canvas.clear_result()
        if had_result:
            self._log("⬜ 區域結果已失效，請依目前設定重新計算。")

    def _start_rec(self, clear=True):
        if not self._connected or self._transitioning or self._close_pending:
            self._log("⚠️ 請先連接可讀取的手寫板。")
            return
        if self.recorder.recording:
            return
        self._invalidate_result()
        self.recorder.start_recording(clear=clear)
        self._update_controls()
        self._log("🔴 錄製中。" + ("包含懸空軌跡。" if self.chk_hover.isChecked() else "只包含觸板軌跡。"))

    def _stop_rec(self):
        pts, dur = self.recorder.stop_recording()
        self._update_controls()
        self._log(f"⏹ 錄製結束。保留 {pts} 個點，累計 {dur:.1f} 秒。")

    def _check_osu(self):
        if not self.auto_mode or not self._connected or self._transitioning or self._close_pending:
            return
        playing, title = OsuDetector.is_playing()
        if playing and not self.was_playing:
            self.was_playing = True
            self._start_rec(clear=False)
            self._log(f"🔴 osu! 自動錄製中：{title}")
        elif not playing and self.was_playing:
            self.was_playing = False
            if self.recorder.recording:
                self._stop_rec()
                self._calc()

    def _current_context(self):
        return (id(self.recorder), len(self.recorder.raw_points),
                self.tablet_info.get('vendor_id'), self.tablet_info.get('product_id'),
                self.tablet_info['width_mm'], self.tablet_info['height_mm'],
                self.chk_ratio.isChecked(), self.inp_ratio.text(), self.chk_hover.isChecked())

    def _calc(self):
        self._invalidate_result()
        if self.recorder.recording:
            self._log("⚠️ 請先停止錄製，再計算區域。")
            return
        try:
            ratio = parse_aspect_ratio(self.inp_ratio.text()) if self.chk_ratio.isChecked() else None
        except ValueError:
            self._log("⚠️ 比例必須是有限正值，例如 16:9 或 1.7778。")
            return
        data, max_x, max_y = self.recorder.get_data()
        result = ModernCalculationEngine.calculate(
            data, max_x, max_y, self.tablet_info['width_mm'], self.tablet_info['height_mm'], ratio)
        if not result:
            self._log("⚠️ 有效移動範圍不足，請重新錄製；只停留在同一點不能計算區域。")
            return
        self.last_cfg = result
        self._cfg_context = self._current_context()
        self.canvas.overlay_result(result)
        self._update_controls()
        warning = result.get('warning') or ''
        if not self.tablet_info.get('verified', False):
            warning += " 此型號尚未經實機驗證，請確認毫米數值。"
        self._log(
            f"✨ 建議區域（依已錄製軌跡估算）：\n\n"
            f"寬度：{result['width_mm']:.2f} mm\n高度：{result['height_mm']:.2f} mm\n"
            f"中心 X：{result['x_offset_mm']:.2f} mm\n中心 Y：{result['y_offset_mm']:.2f} mm\n\n"
            f"採用 {result['used_points']} / {result['total_points']} 個點。" + ("\n" + warning if warning else ""))

    def _reset_all(self):
        if self.recorder.recording:
            self.recorder.stop_recording()
        self.recorder.clear_all()
        self.was_playing = False
        self._invalidate_result()
        self._update_controls()
        self._log("🗑 軌跡與結果已清空。座標尺度保持裝置規格；可重新錄製。")

    def _copy_cfg(self):
        if (not self.last_cfg or self.recorder.recording or self._transitioning
                or self._cfg_context != self._current_context()):
            self._invalidate_result()
            return
        r = self.last_cfg
        values = [r[k] for k in ('width_mm', 'height_mm', 'x_offset_mm', 'y_offset_mm')]
        if not all(math.isfinite(v) for v in values) or r['width_mm'] <= 0 or r['height_mm'] <= 0:
            self._invalidate_result()
            return
        # Native OTD area property names. This is an area fragment, not a full settings file.
        cfg = {"Width": r['width_mm'], "Height": r['height_mm'],
               "XPosition": r['x_offset_mm'], "YPosition": r['y_offset_mm'], "Rotation": 0}
        QApplication.clipboard().setText(json.dumps(cfg, indent=2, allow_nan=False))
        self.btn_copy.setText("✓ 區域 JSON 已複製")
        QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 複製區域 JSON"))

    def _log(self, text):
        self.lbl_status.setText(text)
        # A dense settings panel must not squeeze the numeric result to one line.
        self.lbl_status.setMinimumHeight(max(0, self.lbl_status.heightForWidth(self.lbl_status.width())))
        # Wait for the taller label's layout before scrolling to its numeric values.
        QTimer.singleShot(0, self._show_status)

    def _show_status(self):
        if not self._close_pending:
            self._left_scroll.ensureWidgetVisible(self.lbl_status)

    def closeEvent(self, event):
        if self.recorder.recording:
            self.recorder.stop_recording()
        self.osu_timer.stop()
        self._close_pending = True
        self._connected = False
        self._invalidate_result()
        if self.hid_thread.stop(timeout_ms=100):
            event.accept()
        else:
            event.ignore()
            self._update_controls()
            self._log("⏳ 正在結束裝置讀取；結束後會自動關閉。")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.Window, QColor(43, 43, 43))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    app.setPalette(palette)
    app.setFont(QFont("Segoe UI", 10))
    window = ApplicationWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
