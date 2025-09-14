import random
from PySide6.QtCore import Qt, QMargins, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QBitmap, QFont
from PySide6.QtWidgets import QWidget, QLabel
from framework.agent import InvokeStartEvent, SpeakEvent
from framework.event import PlainEvent, Task
from framework.plugin import PluginInterface
from framework.window import (
    BubbleRef,
    BubbleController,
    BubbleDirection,
    BubbleOverflowAction,
)


class TextBubble(QLabel):
    def __init__(self, parent: QWidget, ref: BubbleRef):
        super().__init__(parent)

        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            """
            background-color: white;
            border: 2px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        """
        )

        font = QFont()
        font.setPointSize(14)
        self.setFont(font)

        self.loading = False

        self.hide()
        self.hide_timer = QTimer()
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.on_finish_show)

        self.bubble_controller = BubbleController(
            parent,
            self,
            ref,
            [BubbleDirection.Top, BubbleDirection.Center],
            [BubbleOverflowAction.Flip, BubbleOverflowAction.Shift],
        )

    def show_loading(self):
        self.loading = True

        if self.hide_timer.isActive():
            return

        self.setText("...")
        self.adjustSize()

        self.show()

    def show_message(self, text):
        self.loading = False

        self.hide_timer.stop()

        self.setText(text)
        self.adjustSize()

        self.show()
        self.hide_timer.start(5000)

    def on_finish_show(self):
        if self.loading:
            self.show_loading()
        else:
            self.hide()


class Pet(QWidget):
    def __init__(self, parent):
        super().__init__(parent)

        self.setFixedSize(100, 100)

        self.color_hue = 210

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.setPen(QPen(QColor.fromHsv(self.color_hue, 255, 180), 10))
        painter.setBrush(QColor.fromHsv(self.color_hue, 255, 255))
        painter.drawEllipse(self.rect() + QMargins(-5, -5, -5, -5))

        self.update_mask()

    def update_mask(self):
        mask = QBitmap(self.size())
        mask.fill(Qt.GlobalColor.color0)

        mask_painter = QPainter(mask)
        mask_painter.setBrush(Qt.GlobalColor.color1)
        mask_painter.setPen(Qt.PenStyle.NoPen)
        mask_painter.drawEllipse(self.rect())
        mask_painter.end()

        self.setMask(mask)


class GreetingTask(Task):
    async def execute(self, manager):
        manager.trigger_event(
            PlainEvent("You've just been awakened, how about saying hello to the user?")
        )


class Plugin(PluginInterface):
    name = "base_pet"

    def init(self, screen):
        self.pet = Pet(screen)
        self.text_bubble = TextBubble(screen, BubbleRef(self.pet))

        raw_size = screen.size()
        pet_size = self.pet.size()
        movable_size = (raw_size - pet_size).toTuple()
        init_pos = (
            random.randrange(int(movable_size[0] * 0.1), int(movable_size[0] * 0.9)),
            random.randrange(int(movable_size[1] * 0.1), int(movable_size[1] * 0.9)),
        )
        self.pet.move(*init_pos)
        self.pet.show()
        
    def prompts(self):
        return super().prompts()

    def init_tasks(self):
        return [GreetingTask()]

    def on_event(self, e):
        match e:
            case InvokeStartEvent():
                self.text_bubble.show_loading()
            case SpeakEvent(text):
                self.text_bubble.show_message(text)
