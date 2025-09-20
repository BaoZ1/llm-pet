from framework.plugin import BasePlugin, PetPluginProtocol
from framework.agent import InvokeStartEvent, SpeakEvent
from framework.window import TransparentWindow, set_bubble, WidgetBubbleRef, BubbleDirection, BubbleOverflowAction
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QLayout


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

    def show_message(self, text):
        self.loading = False

        self.hide_timer.stop()

        self.label.setText(text)
        self.adjustSize()

        self.show()
        self.hide_timer.start(5000)

    def on_finish_show(self):
        if self.loading:
            self.show_loading()
        else:
            self.hide()


class Plugin(BasePlugin):
    deps = [PetPluginProtocol]

    def init(self):
        pet = self.dep(PetPluginProtocol).pet()
        self.text_bubble = TextBubble()
        set_bubble(
            self.text_bubble,
            WidgetBubbleRef(pet),
            [BubbleDirection.Top, BubbleDirection.Center],
            [BubbleOverflowAction.Flip, BubbleOverflowAction.Shift],
        )

    def on_event(self, e):
        match e:
            case InvokeStartEvent():
                self.text_bubble.show_loading()
            case SpeakEvent(text):
                self.text_bubble.show_message(text)


