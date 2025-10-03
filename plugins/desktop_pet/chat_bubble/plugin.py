import base64
from pathlib import Path
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
from PySide6.QtGui import QMouseEvent, QKeyEvent, QFocusEvent, QFont, QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication,
    QTextEdit,
    QFileDialog,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QLayout,
    QLabel,
)
from io import BytesIO
from PIL import Image


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
    input_submitted = Signal(str, list)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        input_layout = QHBoxLayout()
        input_layout

        self.images_layout = QHBoxLayout()
        self.images_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.images_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.image_labels: list[QLabel] = []

        layout.addLayout(self.images_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(
            """
            background-color: white;
            border: 2px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        """
        )
        input_layout.addWidget(self.text_edit)

        self.image_btn = QPushButton("Img")
        input_layout.addWidget(self.image_btn)

        layout.addLayout(input_layout)

        self.images = []
        self.image_btn.installEventFilter(self)
        self.image_btn.clicked.connect(self.select_image)

        self.update_size()
        self.text_edit.installEventFilter(self)
        self.text_edit.document().contentsChanged.connect(self.update_size)

    def select_image(self):
        dialog = QFileDialog(self)
        if dialog.exec():
            p = dialog.selectedFiles()[0]
            img = Image.open(p).convert("RGB")

            buffer = BytesIO()
            img.save(buffer, "JPEG", quality=85)

            img_bytes = buffer.getvalue()
            base64_string = base64.b64encode(img_bytes).decode("utf-8")

            self.images.append(f"data:image/jpeg;base64,{base64_string}")

            qp = QPixmap()
            qp.loadFromData(img_bytes)
            img_label = QLabel(pixmap=qp)
            img_label.setScaledContents(True)
            img_label.setFixedSize(50, 50)
            self.image_labels.append(img_label)
            self.images_layout.addWidget(img_label)
            img_label.installEventFilter(self)

    def showEvent(self, event):
        self.activateWindow()
        self.text_edit.setFocus()

    def clear(self):
        self.text_edit.clear()
        self.images.clear()

        for label in self.image_labels:
            self.images_layout.takeAt(0)
            label.hide()
            label.deleteLater()
        self.image_labels.clear()

    def update_size(self):
        new_height = (
            math.ceil(self.text_edit.document().size().height())
            + self.text_edit.contentsMargins().top()
            + self.text_edit.contentsMargins().bottom()
        )
        self.text_edit.setFixedHeight(min(new_height, 400))

    def handel_text_edit_event(self, event):
        if isinstance(event, QFocusEvent):
            if event.type() == QEvent.Type.FocusOut and QApplication.focusWidget() != self.image_btn:
                self.clear()
                self.hide()
        elif isinstance(event, QKeyEvent):
            if (
                event.key() == Qt.Key.Key_Return
                and event.type() == QEvent.Type.KeyPress
            ):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.text_edit.insertPlainText("\n")
                else:
                    self.input_submitted.emit(
                        self.text_edit.toPlainText().strip(), self.images
                    )
                    self.clear()
                    self.hide()
                return True
        return False

    def handel_images_btn_event(self, event: QEvent):
        if event.type() == QEvent.Type.WindowUnblocked:
            self.text_edit.setFocus()
        return False

    def handel_image_label_event(self, label: QLabel, event: QEvent):
        if isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    pass
                elif event.button() == Qt.MouseButton.RightButton:
                    idx = self.image_labels.index(label)
                    self.images_layout.takeAt(idx)
                    self.images.pop(idx)
                    self.image_labels.pop(idx)
                    label.deleteLater()
                return False
        return False

    def eventFilter(self, watched, event):
        if watched == self.text_edit:
            return self.handel_text_edit_event(event)
        elif watched == self.image_btn:
            return self.handel_images_btn_event(event)
        elif watched in self.image_labels:
            return self.handel_image_label_event(watched, event)
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
        self.input_bubble.input_submitted.connect(
            lambda text, imgs: self.trigger_event(
                UserInputEvent(text=text, images=imgs)
            )
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
