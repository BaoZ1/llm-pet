from framework.event import PlainEvent
from framework.plugin import BasePlugin, PetPluginProtocol
from framework.window import TransparentWindow
from framework.config import BaseConfig
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




class Plugin(BasePlugin, PetPluginProtocol):
    def init(self):
        self._pet = Pet()
        self._pet.show()
        self.trigger_event(
            PlainEvent("You've just been awakened, how about saying hello to the user?")
        )

    def pet(self):
        return self._pet

    def prompts(self):
        return super().prompts()

class Config(BaseConfig):
    pass