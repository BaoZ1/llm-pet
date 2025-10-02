from framework.event import InvokeStartEvent, InvokeEndEvent
from framework.plugin import BasePlugin
from framework.agent import UserInputEvent, SpeakEvent
from framework.window import (
    TransparentWindow,
    set_bubble,
    WidgetBubbleRef,
    BubbleDirection,
    BubbleOverflowAction,
)
from plugins.desktop_pet.pet import PetPluginBase
import math
from PySide6.QtCore import Qt, QObject, QEvent, Signal, QTimer
from PySide6.QtGui import QMouseEvent, QKeyEvent, QFocusEvent, QFont
from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QLayout, QLabel


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


class TextBubble(TransparentWindow):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(
            """
            background-color: white;
            border: 2px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        """
        )
        font = QFont()
        font.setPointSize(14)
        self.label.setFont(font)

        layout.addWidget(self.label)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.loading = False
        
        self.concated_text = ""
        self.msg_id = None

        self.hide()
        self.hide_timer = QTimer()
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.on_finish_show)

    def show_loading(self):
        self.loading = True

        if self.hide_timer.isActive():
            return

        self.label.setText("...")
        self.label.adjustSize()

        self.show()

    def show_message(self, text: str, msg_id: str):
        self.loading = False

        self.hide_timer.stop()

        if self.msg_id != msg_id:
            self.msg_id = msg_id
            self.concated_text = ""
        self.concated_text += text
        self.label.setText(self.concated_text)
        self.adjustSize()

        self.show()
        self.hide_timer.start(5000)
        
    def stop_loading(self):
        self.loading = False

        if self.hide_timer.isActive():
            return
        
        self.hide()

    def on_finish_show(self):
        if self.loading:
            self.show_loading()
        else:
            self.hide()


class Plugin(BasePlugin):
    deps = [PetPluginBase]

    def init(self):
        self.press_moved = True

        self.event_filter = ClickEventFilter()
        self.event_filter.pressed.connect(self.mouse_press)
        self.event_filter.moved.connect(self.mouse_move)
        self.event_filter.released.connect(self.mouse_release)

        self.input_bubble = InputBubble()
        self.input_bubble.text_submitted.connect(
            lambda text: self.trigger_event(UserInputEvent(text))
        )

        self.text_bubble = TextBubble()

    def clear(self):
        self.event_filter.deleteLater()
        self.input_bubble.deleteLater()
        self.text_bubble.deleteLater()

    def on_dep_load(self, dep):
        if isinstance(dep, PetPluginBase):
            self.pet = dep.pet
            self.pet.installEventFilter(self.event_filter)
            set_bubble(
                self.input_bubble,
                WidgetBubbleRef(self.pet),
                (BubbleDirection.Bottom, BubbleDirection.Center),
                [BubbleOverflowAction.Shift],
            )
            set_bubble(
                self.text_bubble,
                WidgetBubbleRef(dep.pet),
                [BubbleDirection.Top, BubbleDirection.Center],
                [BubbleOverflowAction.Flip, BubbleOverflowAction.Shift],
            )

    def on_event(self, e):
        match e:
            case InvokeStartEvent():
                self.text_bubble.show_loading()
            case SpeakEvent(text, msg_id):
                self.text_bubble.show_message(text, msg_id)
            case InvokeEndEvent():
                self.text_bubble.stop_loading()

    def mouse_press(self):
        self.press_moved = False

    def mouse_move(self):
        self.press_moved = True

    def mouse_release(self):
        if not self.press_moved:
            self.input_bubble.show()
