from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QObject, QPropertyAnimation
from PySide6.QtGui import (
    QMoveEvent,
    QResizeEvent,
    QShowEvent,
    QIcon,
    QPixmap,
    QAction,
    QCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QSystemTrayIcon,
    QMenu,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QScrollArea,
    QSizePolicy,
)
from typing import Self, Sequence, cast
from enum import Enum, auto
from .agent import Event
from .config import BaseConfig, ConfigEdit
from .plugin import BasePlugin, PluginManager, PluginTypeBase
import sys
import os


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


def set_bubble(
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


class DynamicScrollArea(QScrollArea):
    def sizeHint(self):
        if w := self.widget():
            return w.sizeHint()
        return super().sizeHint()


class SinglePluginCollapsibleWidget(QWidget):
    expanded = Signal(QWidget)

    def __init__(self, plugin_class: type[BasePlugin]):
        super().__init__()

        self.plugin_class = plugin_class

        layout = QVBoxLayout()

        self.title_widget = QToolButton(autoRaise=True)
        self.title_widget.setText(plugin_class.identifier())
        self.title_widget.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.title_widget.setArrowType(Qt.ArrowType.RightArrow)
        self.title_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.title_widget)

        self.config_area = DynamicScrollArea()
        self.config_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.config_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.config_area.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.config_area.setMinimumHeight(0)
        self.config_area.setMaximumHeight(0)

        scroll_content_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        self.deps_hint_label = QLabel()
        self.deps_hint_label.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        scroll_layout.addWidget(self.deps_hint_label)

        self.config_widget = ConfigEdit(plugin_class.config_type())
        self.config_widget.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.config_widget.enable_changed.connect(self.refresh_display)
        scroll_layout.addWidget(self.config_widget)

        scroll_content_widget.setLayout(scroll_layout)

        self.config_area.setWidget(scroll_content_widget)
        layout.addWidget(self.config_area)

        self.setLayout(layout)

        self.expand = False
        self.title_widget.clicked.connect(self.toggle)
        self.animation = QPropertyAnimation(self.config_area, "maximumHeight".encode())
        self.animation.setDuration(100)
        
    def save(self):
        self.plugin_class.update_config(self.config_widget.get())

    def toggle(self):
        self.expand = not self.expand

        if self.expand:
            self.title_widget.setArrowType(Qt.ArrowType.DownArrow)
            body_height = self.config_area.sizeHint().height()
            self.animation.setStartValue(0)
            self.animation.setEndValue(min(body_height, 400))
            self.expanded.emit(self)
        else:
            self.title_widget.setArrowType(Qt.ArrowType.RightArrow)
            self.animation.setStartValue(self.config_area.height())
            self.animation.setEndValue(0)
        self.animation.start()

    def refresh_display(self):
        title_text_color: str
        if self.plugin_class.get_config().enabled:
            for dep in self.plugin_class.deps:
                all_deps = PluginManager.get_plugin_classes(dep)
                if len(all_deps) == 0 or all(
                    not d.get_config().enabled for d in all_deps
                ):
                    title_text_color = "red"
                    break
            else:
                title_text_color = "black"
        else:
            title_text_color = "gray"
        self.title_widget.setStyleSheet(
            f"""
                QToolButton {{
                    color: {title_text_color};
                }}
            """
        )

        if len(self.plugin_class.deps) == 0:
            self.deps_hint_label.setText("deps: None")
        else:
            dep_names = []
            for dep in self.plugin_class.deps:
                if issubclass(dep, BasePlugin):
                    name = dep.identifier()
                    if not dep.get_config().enabled:
                        name = f'<span style="color: red;">{name}</span>'
                elif issubclass(dep, PluginTypeBase):
                    dep_classes = PluginManager.get_plugin_classes(dep)
                    enabled_classes = [c for c in dep_classes if c.get_config().enabled]
                    if len(enabled_classes) == 0:
                        name = f'<span style="color: red;">{dep.__name__}</span>'
                    else:
                        class_names = ", ".join(c.identifier() for c in enabled_classes)
                        name = f"{dep.__name__}({class_names})"
                dep_names.append(name)

            self.deps_hint_label.setText("deps: " + ", ".join(dep_names))
            self.deps_hint_label.setTextFormat(Qt.TextFormat.RichText)

    def load(self, config: BaseConfig):
        self.config_widget.load(config)
        self.refresh_display()


class PluginConfigWindow(QWidget):
    instance: Self | None = None

    @classmethod
    def open(cls):
        if cls.instance is None:
            cls.instance = PluginConfigWindow()
        if cls.instance.isMinimized():
            cls.instance.showNormal()
        else:
            cls.instance.show()
        cls.instance.raise_()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()

        config_list_area = DynamicScrollArea()
        config_list_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        config_list_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        config_list_area.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Maximum,
        )
        config_list_area.setStyleSheet(
            """
            QScrollArea {
                border: none;
            }
        """
        )
        layout.addWidget(config_list_area)

        self.config_widgets: dict[type[BasePlugin], SinglePluginCollapsibleWidget] = {}
        config_content = QWidget()
        config_content.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        config_content_layout = QVBoxLayout()
        config_content_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        for plugin_class in PluginManager.plugin_classes:
            wrapped_widget = SinglePluginCollapsibleWidget(plugin_class)
            wrapped_widget.setSizePolicy(
                QSizePolicy.Policy.MinimumExpanding,
                QSizePolicy.Policy.Fixed,
            )
            config_content_layout.addWidget(wrapped_widget)
            self.config_widgets[plugin_class] = wrapped_widget
            wrapped_widget.config_widget.enable_changed.connect(self.refresh_display)
            wrapped_widget.expanded.connect(self.close_others)
        config_content.setLayout(config_content_layout)
        config_list_area.setWidget(config_content)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save)
        layout.addWidget(save_btn)

        self.setLayout(layout)
        
    def save(self):
        for w in self.config_widgets.values():
            w.save()
        self.refresh_display()

    def refresh_display(self):
        for w in self.config_widgets.values():
            w.refresh_display()

    def close_others(self, open_widget: SinglePluginCollapsibleWidget):
        for w in self.config_widgets.values():
            if w is not open_widget and w.expand:
                w.toggle()

    def showEvent(self, event):
        for plugin_class, config_widget in self.config_widgets.items():
            cast(SinglePluginCollapsibleWidget, config_widget).load(
                plugin_class.get_config()
            )


class TestTray(QWidget):
    instance: Self | None = None

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

        self.tray_icon.activated.connect(
            lambda _: self.tray_icon.contextMenu().popup(QCursor.pos())
        )

        self.tray_icon.show()

    def create_tray_menu(self):
        menu = QMenu()

        config_action = QAction("Config", self)
        config_action.triggered.connect(PluginConfigWindow.open)
        menu.addAction(config_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        restart_action = QAction("Restart", self)
        restart_action.triggered.connect(self.tray_icon.hide)
        restart_action.triggered.connect(
            lambda: os.execl(sys.executable, sys.executable, *sys.argv)
        )
        menu.addAction(restart_action)

        self.tray_icon.setContextMenu(menu)
