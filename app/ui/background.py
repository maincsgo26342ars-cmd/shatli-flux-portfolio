from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DarkSurface(QWidget):
    """Reference-style solid black surface with a square blue outline."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        painter.setPen(QPen(QColor("#0178D4"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
