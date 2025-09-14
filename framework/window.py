from PySide6.QtCore import Qt, Signal, QPoint, QRect, QSize, QObject
from PySide6.QtGui import QCursor, QMoveEvent
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from typing import Sequence
from enum import Enum, auto
from .agent import Event


class BubbleDirection(Enum):
    Center = auto()
    Top = auto()
    Bottom = auto()
    Left = auto()
    Right = auto()


class BubbleOverflowAction(Enum):
    Flip = auto()
    Shift = auto()
    Auto_Place = auto()


class BubbleRef(QObject):
    moved = Signal()

    def __init__(self, ref: QWidget | None):
        super().__init__()

        self.ref = ref
        self.old_move_event = self.ref.moveEvent
        self.ref.moveEvent = self.ref_move_event

    def ref_move_event(self, e: QMoveEvent):
        self.old_move_event(e)
        self.moved.emit()

    def get_rect(self):
        if self.ref is None:
            return QRect(QCursor.pos(), QSize(0, 0))
        if isinstance(self.ref, QWidget):
            return self.ref.geometry()
        raise


class BubbleController:
    def __init__(
        self,
        screen: QWidget,
        target: QWidget,
        ref: BubbleRef,
        direction: tuple[BubbleDirection, BubbleDirection],
        overflow_actions: Sequence[BubbleOverflowAction] = [],
    ):
        self.screen = screen
        self.target = target
        self.ref = ref
        self.direction = direction
        self.overflow_actions = overflow_actions

        self.old_resize_event = self.target.resizeEvent
        self.target.resizeEvent = self.target_resize_event

        self.ref.moved.connect(self.update_pos)

    def bind_screen(self, screen: QWidget):
        self.screen = screen

    def target_resize_event(self, e):
        self.old_resize_event(e)
        self.update_pos()

    def calc_rect(self, direction: tuple[BubbleDirection, BubbleDirection]):
        ref_rect = self.ref.get_rect()
        target_rect = QRect(QPoint(0, 0), self.target.size())

        match direction[0]:
            case BubbleDirection.Top:
                target_rect.moveBottom(ref_rect.top())
            case BubbleDirection.Center:
                target_rect.moveBottom(
                    ref_rect.center().y() + target_rect.height() // 2
                )
            case BubbleDirection.Bottom:
                target_rect.moveTop(ref_rect.bottom())

        match direction[1]:
            case BubbleDirection.Left:
                target_rect.moveRight(ref_rect.left())
            case BubbleDirection.Center:
                target_rect.moveRight(ref_rect.center().x() + target_rect.width() // 2)
            case BubbleDirection.Right:
                target_rect.moveLeft(ref_rect.right())

        return target_rect

    def update_pos(self):
        rect = self.calc_rect(self.direction)

        target_rect = self.ref.get_rect()
        screen_rect = self.screen.rect()

        for action in self.overflow_actions:
            if screen_rect.contains(rect):
                break
            match action:
                case BubbleOverflowAction.Flip:
                    new_direction = [*self.direction]

                    if (
                        rect.top() < screen_rect.top()
                        and target_rect.center().y() < screen_rect.height() / 2
                        and self.direction[0] == BubbleDirection.Top
                    ):
                        new_direction[0] = BubbleDirection.Bottom
                    elif (
                        rect.bottom() > screen_rect.bottom()
                        and target_rect.center().y() > screen_rect.height() / 2
                        and self.direction[0] == BubbleDirection.Bottom
                    ):
                        new_direction[0] = BubbleDirection.Top

                    if (
                        rect.left() < screen_rect.left()
                        and target_rect.center().x() < screen_rect.width() / 2
                        and self.direction[1] == BubbleDirection.Left
                    ):
                        new_direction[1] = BubbleDirection.Right
                    elif (
                        rect.right() > screen_rect.right()
                        and target_rect.center().x() > screen_rect.width() / 2
                        and self.direction[1] == BubbleDirection.Right
                    ):
                        new_direction[1] = BubbleDirection.Left

                    rect = self.calc_rect(new_direction)
                case BubbleOverflowAction.Shift:
                    rect.setY(
                        max(
                            screen_rect.top(),
                            min(screen_rect.bottom() - rect.height(), rect.y()),
                        )
                    )
                    rect.setX(
                        max(
                            screen_rect.left(),
                            min(screen_rect.right() - rect.width(), rect.x()),
                        )
                    )
                case BubbleOverflowAction.Auto_Place:
                    new_direction = []

                    if target_rect.center().y() < screen_rect.height() / 2:
                        new_direction.append(BubbleDirection.Bottom)
                    elif target_rect.center().y() > screen_rect.height() / 2:
                        new_direction.append(BubbleDirection.Top)
                    else:
                        new_direction.append(BubbleDirection.Center)

                    if target_rect.center().x() < screen_rect.width() / 2:
                        new_direction.append(BubbleDirection.Right)
                    elif target_rect.center().x() > screen_rect.width() / 2:
                        new_direction.append(BubbleDirection.Left)
                    else:
                        new_direction.append(BubbleDirection.Center)

                    rect = self.calc_rect(new_direction)

        return self.target.move(rect.topLeft())


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

    def closeEvent(self, a0):
        self.app.quit()


class EventBridge(QObject):
    event_recived = Signal(Event)

    def __init__(self):
        super().__init__()
        self.moveToThread(QApplication.instance().thread())
