from framework.plugin import BasePlugin
from framework.window import TransparentWindow
from plugins.desktop_pet.pet import PetPluginBase
from PySide6.QtCore import QMargins
from PySide6.QtGui import QPainter, QColor, QPen


class Pet(TransparentWindow):
    def __init__(self):
        super().__init__()

        self.setFixedSize(100, 100)

        self.color_hue = 210

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.setPen(QPen(QColor.fromHsv(self.color_hue, 255, 180), 10))
        painter.setBrush(QColor.fromHsv(self.color_hue, 255, 255))
        painter.drawEllipse(self.rect() + QMargins(-5, -5, -5, -5))


class Plugin(BasePlugin, PetPluginBase):
    def init(self):
        self.pet = Pet()
        self.pet.show()
        
    def clear(self):
        self.pet.deleteLater()

