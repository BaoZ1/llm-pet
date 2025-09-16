from framework.plugin import BasePlugin, PetPluginProtocol
from framework.config import BaseConfig
from framework.agent import Event, Task
import asyncio
from PySide6.QtCore import Qt, QObject, QEvent, QPoint, Signal
from PySide6.QtGui import QMouseEvent, QCursor


class DragEventFilter(QObject):
    pressed = Signal(QMouseEvent)
    moved = Signal(QMouseEvent)
    released = Signal(QMouseEvent)

    def eventFilter(self, watched, event):
        if isinstance(event, QMouseEvent):
            match event.type():
                case QEvent.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.pressed.emit(event)
                case QEvent.Type.MouseMove:
                    if event.buttons() & Qt.MouseButton.LeftButton:
                        self.moved.emit(event)
                case QEvent.Type.MouseButtonRelease:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.released.emit(event)
        return False


class DragEvent(Event):
    tags = ["move", "user"]


class DragStartEvent(DragEvent):
    def agent_msg(self):
        return "You are being dragged up by the user!"


class DragEndEvent(DragEvent):
    def agent_msg(self):
        return "You are put down by the user!"


class DragTask(Task):
    check_interval = 0.02

    def __init__(self):
        self.running = True

    async def execute(self, manager):
        while self.running:
            await asyncio.sleep(self.check_interval)

    def execute_info(self):
        return "You are being dragged by the user"

    def on_event(self, event):
        if isinstance(event, DragEndEvent):
            self.running = False


class Plugin(BasePlugin):
    name = "drag"
    deps = [PetPluginProtocol]

    def init(self):
        self.press_pos: QPoint
        self.start_pos: QPoint
        self.dragging = False

        self.pet = self.dep(PetPluginProtocol).pet()
        self.event_filter = DragEventFilter()
        self.pet.installEventFilter(self.event_filter)
        self.event_filter.pressed.connect(self.mouse_press)
        self.event_filter.moved.connect(self.mouse_move)
        self.event_filter.released.connect(self.mouse_release)

    def mouse_press(self, e: QMouseEvent):
        self.press_pos = QCursor.pos()
        self.start_pos = self.pet.pos()

    def mouse_move(self, e: QMouseEvent):
        delta = QCursor.pos() - self.press_pos
        self.pet.move(self.start_pos + delta)
        if not self.dragging:
            self.trigger_event(DragStartEvent())
            self.add_task(DragTask())
            self.dragging = True
        else:
            self.trigger_event(DragEvent())

    def mouse_release(self, e: QMouseEvent):
        if self.dragging:
            delta = QCursor.pos() - self.press_pos
            self.pet.move(self.start_pos + delta)
            self.press_pos = None
            self.dragging = False
            self.trigger_event(DragEndEvent())


class Config(BaseConfig):
    pass
