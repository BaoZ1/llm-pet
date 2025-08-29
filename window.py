import asyncio
from PySide6.QtCore import (
    Qt,
    QTimer,
    QMargins,
    QObject,
    Signal,
    Slot,
    QThread,
    QPoint,
    QPointF,
)
from PySide6.QtGui import QPainter, QColor, QPen, QBitmap, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from agent import *
import threading


class AgentWorker(QObject):
    event_signal = Signal(Event)

    def __init__(self, agent: Agent, init_tasks: list[Task]):
        super().__init__()
        self.agent = agent
        self.agent.task_manager.register_callback("qt_listener", self.on_event)
        self.init_tasks = init_tasks
        self.loop = None

    def on_event(self, e: Event):
        self.event_signal.emit(e)

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.loop.run_until_complete(self.main_loop())

    async def main_loop(self):
        await self.agent.task_manager.add_tasks_no_check(self.init_tasks)
        await self.agent.run()

    def trigger_event(self, e: Event):
        self.agent.task_manager.trigger_event(e)

    def add_task(self, t: Task):
        self.loop.create_task(self._a_add_task(t))

    async def _a_add_task(self, t: Task):
        await self.agent.task_manager.add_task(t)


class Pet(QWidget):
    send_event = Signal(Event)
    add_task = Signal(Task)

    def __init__(self, parent, state: PetState):
        super().__init__(parent)

        self.setFixedSize(100, 100)

        self.pet_state = state

        self.press_pos: QPointF
        self.start_pos: QPoint
        self.dragging = False

    def update_pos(self, event: MoveEvent):
        self.move(*event.new_pos)

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.setPen(QPen(QColor(0, 90, 255), 10))
        painter.setBrush(QColor(0, 200, 255))
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

    def mousePressEvent(self, event: QMouseEvent):
        self.press_pos = event.scenePosition()
        self.start_pos = self.pos()

    def mouseMoveEvent(self, event: QMouseEvent):
        delta = event.scenePosition() - self.press_pos
        self.move(self.start_pos + delta.toPoint())
        if not self.dragging:
            self.send_event.emit(DragEvent(self.pos().toTuple(), "begin"))
            self.add_task.emit(DragTask())
            self.dragging = True
        else:
            self.send_event.emit(DragEvent(self.pos().toTuple()))

    def mouseReleaseEvent(self, event: QMouseEvent):
        delta = event.scenePosition() - self.press_pos
        self.move(self.start_pos + delta.toPoint())
        self.press_pos = None
        self.dragging = False
        self.send_event.emit(DragEvent(self.pos().toTuple(), "end"))


class MainWindow(QWidget):

    def __init__(self, app: QApplication):
        super().__init__()

        self.setWindowFlag(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.app = app

        self.quit_btn = QPushButton(self)
        self.quit_btn.clicked.connect(self.close)

        self.agent = Agent([modify_mood, move], (0, 0))
        self.agent.task_manager.register_callback("qt_listener", self.on_event)
        self.agent_event_loop = asyncio.new_event_loop()
        self.agent_thread = threading.Thread(
            target=lambda: self.agent_event_loop.run_until_complete(
                self.run_agent(
                    [
                        GreetingTask(),
                        StdinReadTask(),
                        RandomMoveEmitTask((30, 60), (50, 150)),
                        DigestTask((10, 30), (1, 3)),
                    ]
                )
            )
        )

        self.pet = Pet(self, self.agent.state)
        self.pet.send_event.connect(self.trigger_event)
        self.pet.add_task.connect(self.add_task)

    async def run_agent(self, init_tasks):
        await self.agent.task_manager.add_tasks_no_check(init_tasks)
        await self.agent.run()

    def trigger_event(self, e: Event):
        self.agent.task_manager.trigger_event(e)

    def add_task(self, t: Task):
        self.agent_event_loop.create_task(self.agent.task_manager.add_task(t))

    def on_event(self, e: Event):
        if isinstance(e, MoveEvent):
            self.pet.update_pos(e)

    def showEvent(self, a0):
        ret = super().showEvent(a0)

        raw_size = self.size()
        pet_size = self.pet.size()
        size = raw_size - pet_size
        self.agent.screen_size = (size.width(), size.height())
        self.agent_thread.start()

        return ret

    def closeEvent(self, a0):
        self.app.quit()


if __name__ == "__main__":
    app = QApplication([])
    w = MainWindow(app)
    w.showFullScreen()
    app.exec()
