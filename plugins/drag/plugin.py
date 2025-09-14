import asyncio
from framework.agent import Event, Task
from framework.plugin import PluginInterface
from plugins.base.plugin import Plugin as BasePetPlugin
from typing import cast
from PySide6.QtCore import Qt, QObject, QEvent, QPointF, QPoint, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget


class DragEventFilter(QObject):
    pressed = Signal(QMouseEvent)
    moved = Signal(QMouseEvent)
    released = Signal(QMouseEvent)

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


class Plugin(PluginInterface):
    name = "drag"
    dep_names = ["base_pet"]

    def init(self, screen):
        self.press_pos: QPointF
        self.start_pos: QPoint
        self.dragging = False

        self.pet = cast(BasePetPlugin, self.deps["base_pet"]).pet
        self.event_filter = DragEventFilter(self.pet, screen)
        self.event_filter.pressed.connect(self.mouse_press)
        self.event_filter.moved.connect(self.mouse_move)
        self.event_filter.released.connect(self.mouse_release)

    def mouse_press(self, e: QMouseEvent):
        self.press_pos = e.scenePosition()
        self.start_pos = self.pet.pos()

    def mouse_move(self, e: QMouseEvent):
        delta = e.scenePosition() - self.press_pos
        self.pet.move(self.start_pos + delta.toPoint())
        if not self.dragging:
            self.trigger_event(DragStartEvent())
            self.add_task(DragTask())
            self.dragging = True
        else:
            self.trigger_event(DragEvent())

    def mouse_release(self, e: QMouseEvent):
        if self.dragging:
            delta = e.scenePosition() - self.press_pos
            self.pet.move(self.start_pos + delta.toPoint())
            self.press_pos = None
            self.dragging = False
            self.trigger_event(DragEndEvent())
