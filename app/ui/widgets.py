from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


def label(text: str, role: str = "muted", wrap: bool = True) -> QLabel:
    item = QLabel(text)
    item.setProperty("role", role)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.TextFormat.PlainText)
    return item


class Button(QPushButton):
    """Native stylesheet hover feedback; no per-button animation or timer."""

    def __init__(self, text="", variant="", icon_path=None, parent=None):
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_path:
            self.setIcon(QIcon(str(icon_path)))
            self.setIconSize(QSize(18, 18))

def unavailable(text: str, stage: int, primary=False) -> Button:
    item = Button(text, "primary" if primary else "")
    item.setEnabled(False)
    item.setToolTip(f"Эта функция будет доступна на этапе {stage}.")
    return item


class GlassCard(QFrame):
    """Compact square black panel, matching the supplied reference."""

    def __init__(self, surface, parent=None):
        super().__init__(parent)
        self.surface = surface
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(16, 16, 16, 16)
        self.content.setSpacing(12)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#030303"))
        painter.setPen(QPen(QColor("#292929"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class StatusRing(QWidget):
    """Applied/selected count from the journal, not a performance score."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(128, 128)
        self.applied, self.total = None, None
        self.set_counts(0, 0)

    def set_counts(self, applied, total):
        if (applied, total) == (self.applied, self.total):
            return
        self.applied, self.total = applied, total
        self.setAccessibleName(f"Применено по журналу: {applied} из {total} выбранных")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#333333"), 7))
        painter.drawEllipse(QRectF(12, 12, 104, 104))
        if self.total:
            painter.setPen(QPen(QColor("#0178D4"), 7))
            painter.drawArc(QRectF(12, 12, 104, 104), 90 * 16,
                            -round(360 * 16 * self.applied / self.total))
        painter.setPen(QColor("#EEEEEE"))
        font = self.font()
        font.setPixelSize(15)
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 40, 128, 32), Qt.AlignmentFlag.AlignCenter,
                         f"{self.applied} / {self.total}")
        font.setPixelSize(11)
        font.setWeight(font.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#8D979C"))
        painter.drawText(QRectF(0, 72, 128, 24), Qt.AlignmentFlag.AlignCenter, "ПРИМЕНЕНО")
