from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QObject
from PySide6.QtGui import QMoveEvent, QResizeEvent, QShowEvent, QIcon, QPixmap, QAction
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu, QMessageBox
from typing import Sequence
from enum import Enum, auto
from .agent import Event


class TransparentWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlag(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


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


class MoveEventFilter(QObject):
    moved = Signal(QMoveEvent)

    def __init__(self, target: QWidget, screen: QWidget):
        super().__init__()

        self.target = target
        self.target.installEventFilter(self)

        self.screen = screen


class BubbleRef(QObject):
    moved = Signal()
    
    def get_rect(self) -> QRect:
        raise

class WidgetBubbleRef(BubbleRef):
    def __init__(self, ref: QWidget):
        super().__init__()
        
        self.ref = ref
        ref.installEventFilter(self)
        
    def get_rect(self):
        return self.ref.geometry()
    
    def eventFilter(self, watched, event):
        if isinstance(event, QMoveEvent):
            self.moved.emit()
        return False


class BubbleController(QObject):

    def __init__(
        self,
        target: QWidget,
        ref: BubbleRef,
        direction: tuple[BubbleDirection, BubbleDirection],
        overflow_actions: Sequence[BubbleOverflowAction] = [],
    ):
        super().__init__()
        self.target = target
        self.ref = ref
        self.direction = direction
        self.overflow_actions = overflow_actions

        self.target.installEventFilter(self)

        self.ref.moved.connect(self.update_pos)

    def eventFilter(self, watched, event):
        if isinstance(event, (QResizeEvent, QShowEvent)):
            self.update_pos()
        return False

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
        if not self.target.isVisible():
            return

        rect = self.calc_rect(self.direction)
        target_rect = self.ref.get_rect()
        screen_rect = QApplication.primaryScreen().geometry()

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
        self.target.move(rect.topLeft())


def config_bubble(
    w: QWidget,
    ref: BubbleRef,
    direction: tuple[BubbleDirection, BubbleDirection],
    overflow_actions: Sequence[BubbleOverflowAction] = [],
):

    w._bubble_controller = BubbleController(
        w,
        ref,
        direction,
        overflow_actions,
    )


class EventBridge(QObject):
    event_recived = Signal(Event)

    def __init__(self):
        super().__init__()
        self.moveToThread(QApplication.instance().thread())


class TestTray(QWidget):
    instance: TestTray | None = None

    @staticmethod
    def init():
        if TestTray.instance is None:
            TestTray.instance = TestTray()

    def __init__(self):
        super().__init__()

        self.tray_icon = QSystemTrayIcon(self)

        icon = QIcon()
        pixmap = QPixmap(16, 16)
        pixmap.fill("#3498db")
        icon.addPixmap(pixmap)
        self.tray_icon.setIcon(icon)

        self.tray_icon.setToolTip("Running...")

        self.create_tray_menu()

        self.tray_icon.activated.connect(self.on_tray_activated)

        self.tray_icon.show()

    def create_tray_menu(self):
        menu = QMenu()

        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.quit_application)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_notification()

    def show_settings(self):
        QMessageBox.information(self, "设置", "这里是设置界面")

    def show_notification(self):
        self.tray_icon.showMessage(
            "应用程序通知",
            "应用程序正在后台运行",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def quit_application(self):
        self.tray_icon.hide()
        QApplication.quit()
