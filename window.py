from PySide6.QtCore import Qt, QMargins, Signal, QPoint, QPointF, QTimer, QRect, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QBitmap, QMouseEvent, QFont
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel
from agent import *
from typing import cast


class Bubble(QLabel):
    def __init__(self, parent: QWidget, pet: QWidget):
        super().__init__(parent=parent)

        self.pet = pet

        # self.setVisible(False)
        # self.setWordWrap(True)
        # self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # self.setStyleSheet(
        #     """
        #     background-color: white;
        #     border: 2px solid #ccc;
        #     border-radius: 10px;
        #     padding: 5px;
        # """
        # )

        # font = QFont()
        # font.setPointSize(8)
        # self.setFont(font)

        # self.hide_timer = QTimer()
        # self.hide_timer.setSingleShot(True)
        # self.hide_timer.timeout.connect(self.hide)

        self.show_message("Testtesttest测试测试")

    def show_message(self, text):
        self.setText(text)
        self.adjustSize()

        self.update_pos()

        self.show()
        # self.hide_timer.start(5000)

    def update_pos(self):
        screen_size = cast(QWidget, self.parent()).size()
        pet_rect = self.pet.rect()


class Pet(QWidget):
    send_event = Signal(Event)
    add_task = Signal(Task)

    def __init__(self, parent, state: PetState):
        super().__init__(parent)

        self.setFixedSize(100, 100)

        self.pet_state = state

        self.expression = "normal"
        self.color_mapper = {
            "normal": 210,
            "happy": 120,
            "sad": 34,
            "angry": 0,
        }

        self.press_pos: QPointF
        self.start_pos: QPoint
        self.dragging = False
        
        self.bubble = Bubble(parent, self)

    def update_pos(self, event: MoveEvent):
        self.move(*event.new_pos)

    def update_expression(self, expression_type: str):
        self.expression = expression_type
        self.repaint()

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.setPen(
            QPen(QColor.fromHsv(self.color_mapper[self.expression], 255, 180), 10)
        )
        painter.setBrush(QColor.fromHsv(self.color_mapper[self.expression], 255, 255))
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
        
    def moveEvent(self, event):
        super().moveEvent(event)
        self.bubble.update_pos()

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
    recive_event = Signal(Event)

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

        self.agent = ThreadedAgent(
            [move],
            [
                GreetingTask(),
                StdinReadTask(),
                ExpressionManageTask(),
                RandomWanderEmitTask((30, 60), (50, 150)),
                DigestTask((10, 30), (1, 3)),
            ],
        )
        self.agent.register_task_callback("qt_listener", self.recive_event.emit)
        self.recive_event.connect(self.on_event)

        self.pet = Pet(self, self.agent.state)
        self.pet.send_event.connect(self.agent.trigger_event)
        self.pet.add_task.connect(self.agent.add_task)

    def on_event(self, e: Event):
        if isinstance(e, MoveEvent):
            self.pet.update_pos(e)
        elif isinstance(e, ExpressionUpdateEvent):
            self.pet.update_expression(e.expression_type)
        # elif isinstance(e, PetSpeakEvent):
        #     self.pet.bubble.show_message(e.content)

    def showEvent(self, a0):
        ret = super().showEvent(a0)

        raw_size = self.size()
        pet_size = self.pet.size()
        size = raw_size - pet_size
        self.agent.run((size.width(), size.height()))

        return ret

    def closeEvent(self, a0):
        self.agent.stop()
        self.app.quit()


if __name__ == "__main__":
    app = QApplication([])
    w = MainWindow(app)
    w.showFullScreen()
    app.exec()
