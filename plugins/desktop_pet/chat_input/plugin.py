from framework.plugin import BasePlugin, PetPluginProtocol
from framework.agent import UserInputEvent
from framework.window import (
    TransparentWindow,
    set_bubble,
    WidgetBubbleRef,
    BubbleDirection,
    BubbleOverflowAction,
)
import math
from PySide6.QtCore import Qt, QObject, QEvent, Signal
from PySide6.QtGui import QMouseEvent, QKeyEvent, QFocusEvent
from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QLayout


class ClickEventFilter(QObject):
    pressed = Signal()
    moved = Signal()
    released = Signal()

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


class InputBubble(TransparentWindow):
    text_submitted = Signal(str)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        layout.addWidget(self.text_edit)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.text_edit.setStyleSheet(
            """
            background-color: white;
            border: 2px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        """
        )

        self.update_size()
        self.text_edit.installEventFilter(self)
        self.text_edit.document().contentsChanged.connect(self.update_size)

    def showEvent(self, event):
        self.activateWindow()
        self.text_edit.setFocus()

    def update_size(self):
        new_height = (
            math.ceil(self.text_edit.document().size().height())
            + self.text_edit.contentsMargins().top()
            + self.text_edit.contentsMargins().bottom()
        )
        self.text_edit.setFixedHeight(min(new_height, 400))

    def eventFilter(self, watched, event):
        if isinstance(event, QFocusEvent):
            if event.type() == QEvent.Type.FocusOut:
                self.text_edit.clear()
                self.hide()
        elif isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Return and event.type() == QEvent.Type.KeyPress:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.text_edit.insertPlainText("\n")
                else:
                    self.text_submitted.emit(self.text_edit.toPlainText().strip())
                    self.text_edit.clear()
                    self.hide()
                return True
        return False


class Plugin(BasePlugin):
    deps = [PetPluginProtocol]

    def init(self):
        self.press_moved = True
        self.pet = self.dep(PetPluginProtocol).pet()
        self.event_filter = ClickEventFilter()
        self.pet.installEventFilter(self.event_filter)
        self.event_filter.pressed.connect(self.mouse_press)
        self.event_filter.moved.connect(self.mouse_move)
        self.event_filter.released.connect(self.mouse_release)

        self.input_bubble = InputBubble()
        set_bubble(
            self.input_bubble,
            WidgetBubbleRef(self.pet),
            (BubbleDirection.Bottom, BubbleDirection.Center),
            [BubbleOverflowAction.Shift],
        )
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
