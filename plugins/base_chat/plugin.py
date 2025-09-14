from dataclasses import dataclass
import math
from typing import cast
from PySide6.QtCore import Qt, QObject, QEvent, Signal
from PySide6.QtGui import QMouseEvent, QKeyEvent
from PySide6.QtWidgets import QWidget, QTextEdit
from framework.event import Event
from framework.plugin import PluginInterface
from plugins.base.plugin import Plugin as BasePetPlugin
from framework.window import (
    BubbleRef,
    BubbleController,
    BubbleDirection,
    BubbleOverflowAction,
)


class ClickEventFilter(QObject):
    pressed = Signal()
    moved = Signal()
    released = Signal()

    def __init__(self, target: QWidget, screen: QWidget):
        super().__init__()

        self.target = target
        self.target.installEventFilter(self)

        self.screen = screen

    def eventFilter(self, watched, event):
        if isinstance(event, QMouseEvent):
            match event.type():
                case QEvent.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.pressed.emit()
                case QEvent.Type.MouseMove:
                    if event.buttons() & Qt.MouseButton.LeftButton:
                        self.moved.emit()
                case QEvent.Type.MouseButtonRelease:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.released.emit()
        return False


class InputBubble(QTextEdit):
    text_submitted = Signal(str)

    def __init__(self, parent: QWidget, ref: BubbleRef):
        super().__init__(parent=parent)

        self.setStyleSheet(
            """
            QTextEdit {
                background-color: white;
                border: 2px solid #ccc;
                border-radius: 10px;
                padding: 5px;
            }
        """
        )

        self.hide()

        self.bubble_controller = BubbleController(
            parent,
            self,
            ref,
            [BubbleDirection.Bottom, BubbleDirection.Center],
            [BubbleOverflowAction.Flip, BubbleOverflowAction.Shift],
        )

        self.document().contentsChanged.connect(self.update_size)

    def show(self):
        super().show()
        self.update_size()
        self.setFocus()

    def update_size(self):
        new_height = (
            math.ceil(self.document().size().height())
            + self.contentsMargins().top()
            + self.contentsMargins().bottom()
        )
        self.setFixedHeight(min(new_height, 400))

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Return:
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
            else:
                self.text_submitted.emit(self.toPlainText().strip())
                self.clear()
                self.hide()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.clear()
        self.hide()


@dataclass
class UserInputEvent(Event):
    tags = ["user"]
    msg_prefix = "User Input"

    content: str

    def agent_msg(self):
        return self.content


class Plugin(PluginInterface):
    name = "base_chat"
    dep_names = ["base_pet"]

    def init(self, screen):
        self.press_moved = True

        self.pet = cast(BasePetPlugin, self.deps["base_pet"]).pet
        self.event_filter = ClickEventFilter(self.pet, screen)
        self.event_filter.pressed.connect(self.mouse_press)
        self.event_filter.moved.connect(self.mouse_move)
        self.event_filter.released.connect(self.mouse_release)

        self.input_bubble = InputBubble(screen, BubbleRef(self.pet))
        self.input_bubble.text_submitted.connect(
            lambda text: self.trigger_event(UserInputEvent(text))
        )

    def mouse_press(self):
        self.press_moved = False

    def mouse_move(self):
        self.press_moved = True

    def mouse_release(self):
        if not self.press_moved:
            self.input_bubble.show()
