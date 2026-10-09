"""Bounded, batched tablet traces with one replaceable area overlay."""

from collections import deque
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QSizePolicy


class LiquidCanvas(QGraphicsView):
    # At most 32 paths of 128 segments, rather than thousands of line items.
    MAX_TRACE_ITEMS = 32
    PATH_BATCH_SEGMENTS = 128

    def __init__(self, phys_w, phys_h):
        super().__init__()
        self.phys_w, self.phys_h = phys_w, phys_h
        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.NoIndex)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setStyleSheet("""
            QGraphicsView {
                background-color: #1e1e1e;
                border: 2px solid #333;
                border-radius: 10px;
            }
        """)
        self._setup_background()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _setup_background(self):
        dimensions = float(self.phys_w), float(self.phys_h)
        if not all(math.isfinite(value) and value > 0 for value in dimensions):
            raise ValueError("畫布尺寸必須是有限正數")
        self.sw = 1200.0
        self.sh = self.sw * (dimensions[1] / dimensions[0])
        if not math.isfinite(self.sh) or self.sh <= 0:
            raise ValueError("畫布比例超出可顯示範圍")
        self.scene.clear()
        self.scene.setSceneRect(0, 0, self.sw, self.sh)
        self.trace_items = deque()
        self.current_stroke = deque(maxlen=2)
        self.pen_is_down = False
        self._last_point = None
        self._last_scale = None
        self._trace_mode = None
        self._active_path = None
        self._active_item = None
        self._active_segments = 0
        self.overlay_item = None

        grid = QPainterPath()
        for i in range(1, 10):
            grid.moveTo(i * self.sw / 10, 0)
            grid.lineTo(i * self.sw / 10, self.sh)
            grid.moveTo(0, i * self.sh / 10)
            grid.lineTo(self.sw, i * self.sh / 10)
        self.scene.addPath(grid, QPen(QColor(60, 60, 60, 80), 1))
        self.scene.addRect(0, 0, self.sw, self.sh,
                           QPen(QColor(100, 100, 100), 4))
        self.live_cursor = self.scene.addEllipse(
            -5, -5, 10, 10, QPen(Qt.NoPen),
            QBrush(QColor(255, 255, 255, 120))
        )
        self.live_cursor.setZValue(100)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _break_trace(self):
        self.current_stroke.clear()
        self._last_point = None
        self._active_path = None
        self._active_item = None
        self._active_segments = 0

    def _append_segment(self, start, end, is_down):
        if (self._active_path is None or
                self._active_segments >= self.PATH_BATCH_SEGMENTS):
            self._active_path = QPainterPath()
            self._active_path.moveTo(*start)
            pen = QPen(QColor(0, 150, 255, 180) if is_down
                       else QColor(170, 170, 170, 90), 3 if is_down else 1)
            pen.setCapStyle(Qt.RoundCap)
            self._active_item = self.scene.addPath(self._active_path, pen)
            self.trace_items.append(self._active_item)
            self._active_segments = 0
            if len(self.trace_items) > self.MAX_TRACE_ITEMS:
                self.scene.removeItem(self.trace_items.popleft())
        self._active_path.lineTo(*end)
        self._active_segments += 1
        self._active_item.setPath(self._active_path)

    def update_state(self, nx, ny, is_down, max_x, max_y, *,
                     in_range=True, trace_hover=False):
        """Show normalized hover/contact movement, without retaining input data."""
        try:
            nx, ny, max_x, max_y = map(float, (nx, ny, max_x, max_y))
            valid = (all(math.isfinite(value)
                         for value in (nx, ny, max_x, max_y)) and
                     0 <= nx <= 1 and 0 <= ny <= 1 and
                     max_x > 0 and max_y > 0)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid or not in_range:
            self._break_trace()
            self.pen_is_down = False
            self._trace_mode = None
            self._last_scale = None
            self.live_cursor.hide()
            return
        point = nx * self.sw, ny * self.sh
        mode = bool(is_down)
        scale = max_x, max_y
        if self._trace_mode != mode or self._last_scale != scale:
            self._break_trace()
        if self._trace_mode != mode:
            self.live_cursor.setBrush(QBrush(
                QColor(0, 200, 255, 200) if mode
                else QColor(255, 255, 255, 120)
            ))
        self.live_cursor.show()
        self.live_cursor.setPos(*point)
        self.pen_is_down = mode
        self._trace_mode = mode
        self._last_scale = scale
        tracing = mode or bool(trace_hover)
        if tracing and self._last_point is not None and point != self._last_point:
            self._append_segment(self._last_point, point, mode)
        self._last_point = point if tracing else None
        if tracing:
            self.current_stroke.append(point)
        else:
            self._break_trace()

    def clear_canvas(self):
        self._setup_background()

    def overlay_result(self, res):
        """Replace the previous result; None clears it without clearing traces."""
        self.clear_result()
        if not res:
            return
        try:
            width, height, cx, cy = (
                float(res[key]) for key in
                ('width_mm', 'height_mm', 'x_offset_mm', 'y_offset_mm')
            )
            if (not all(math.isfinite(value) for value in (width, height, cx, cy))
                    or width <= 0 or height <= 0):
                return
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        self.overlay_item = self.scene.addRect(
            (cx - width / 2) / self.phys_w * self.sw,
            (cy - height / 2) / self.phys_h * self.sh,
            width / self.phys_w * self.sw,
            height / self.phys_h * self.sh,
            QPen(QColor(255, 200, 0, 200), 5),
            QBrush(QColor(255, 200, 0, 50))
        )
        self.overlay_item.setZValue(50)

    def clear_result(self):
        """Invalidate the suggested area without removing recorded traces."""
        if self.overlay_item is not None:
            self.scene.removeItem(self.overlay_item)
            self.overlay_item = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
