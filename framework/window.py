from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QObject, SignalInstance
from PySide6.QtGui import QMoveEvent, QResizeEvent, QShowEvent, QIcon, QPixmap, QAction
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QStackedWidget,
    QLabel,
    QLineEdit,
    QCheckBox,
    QSpinBox,
    QPushButton,
)
from typing import Sequence, cast, get_args, get_origin, _TypedDict, is_typeddict
from types import UnionType, NoneType
from enum import Enum, auto
from .agent import Event
from .plugin import BasePlugin
from dataclasses import fields
from pathlib import Path


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


TYPE_FIELD_EDITS: dict[type, type[TypeFieldEdit]] = {}


class TypeFieldEdit[T]:
    changed: SignalInstance

    def __init_subclass__(cls):
        for b in cls.__orig_bases__:
            if get_origin(b) is TypeFieldEdit:
                t = get_args(b)[0]
                TYPE_FIELD_EDITS[get_origin(t) or t] = cls
                return

    def with_type(self, t: type[T]):
        self.t = t
        self.init()
        return self

    def init(self) -> None:
        raise

    def set_value(self, value: T) -> None:
        raise

    def get_value(self) -> T:
        raise


def create_type_edit[T](t: type[T]) -> TypeFieldEdit[T]:
    idx_t = get_origin(t) or t
    if idx_t not in TYPE_FIELD_EDITS:
        if is_typeddict(idx_t):
            return TYPE_FIELD_EDITS[_TypedDict]().with_type(t)
        raise Exception(t)
    return TYPE_FIELD_EDITS[idx_t]().with_type(t)


class BoolFieldEdit(QPushButton, TypeFieldEdit[bool]):
    changed = Signal()

    def init(self):

        self.setCheckable(True)
        self.clicked.connect(lambda: self.set_value(self.isChecked()))
        self.clicked.connect(self.changed.emit)

    def set_value(self, value):
        self.setText(str(value))
        self.setChecked(value)

    def get_value(self):
        return self.isChecked()


class StrFieldEdit(QLineEdit, TypeFieldEdit[str]):
    changed = Signal()

    def init(self):
        self.textChanged.connect(lambda _: self.changed.emit())

    def set_value(self, value):
        self.setText(value)

    def get_value(self):
        return self.text()


class IntFieldEdit(QSpinBox, TypeFieldEdit[int]):
    changed = Signal()

    def init(self):
        self.valueChanged.connect(lambda _: self.changed.emit())

    def set_value(self, value):
        self.setValue(value)

    def get_value(self):
        return self.value()


class NoneFieldEdit(QWidget, TypeFieldEdit[NoneType]):
    changed = Signal()

    def init(self):
        pass

    def set_value(self, value):
        pass

    def get_value(self):
        pass


class MutableSequenceFieldEditBase(QWidget):
    insert = Signal()
    remove = Signal()

    def __init__(self):
        super().__init__()

        layout = QHBoxLayout()
        layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)

        self.insert_btn = QPushButton()
        layout.addWidget(self.insert_btn)

        self.remove_btn = QPushButton()
        layout.addWidget(self.remove_btn)

        self.setLayout(layout)

        self.insert_btn.clicked.connect(self.insert.emit)
        self.remove_btn.clicked.connect(self.remove.emit)


class ListFieldEditItem(MutableSequenceFieldEditBase):
    def __init__(self, edit: TypeFieldEdit):
        super().__init__()

        self.edit = edit
        cast(QHBoxLayout, self.layout()).insertWidget(0, edit)


class ListFieldEdit(QWidget, TypeFieldEdit[list]):
    changed = Signal()

    def init(self):
        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        self.append_btn = QPushButton()
        layout.addWidget(self.append_btn)

        self.setLayout(layout)

        self.append_btn.clicked.connect(self.append)

    def set_value(self, value):
        while self.layout().count() - 1 > len(value):
            self.remove(0)
        while self.layout().count() - 1 < len(value):
            self.append()
        for i in range(len(value)):
            cast(
                ListFieldEditItem,
                self.layout().itemAt(i).widget(),
            ).edit.set_value(value[i])

    def get_value(self):
        ret = []
        for i in range(self.layout().count() - 1):
            ret.append(
                cast(
                    ListFieldEditItem,
                    self.layout().itemAt(i).widget(),
                ).edit.get_value()
            )
        return ret

    def insert(self, idx: int):
        layout = cast(QVBoxLayout, self.layout())

        widget = create_type_edit(get_args(self.t)[0])
        wrapped_widget = ListFieldEditItem(widget)
        layout.insertWidget(idx, wrapped_widget)

        widget.changed.connect(self.changed.emit)
        wrapped_widget.insert.connect(
            lambda: self.insert(layout.indexOf(wrapped_widget))
        )
        wrapped_widget.remove.connect(
            lambda: self.remove(layout.indexOf(wrapped_widget))
        )
        self.changed.emit()

    def append(self):
        self.insert(self.layout().count() - 1)

    def remove(self, idx: int):
        w = self.layout().takeAt(idx).widget()
        w.setParent(None)
        w.deleteLater()
        self.changed.emit()


class DictFieldEditItem(MutableSequenceFieldEditBase):

    def __init__(self, key_edit: TypeFieldEdit, value_edit: TypeFieldEdit):
        super().__init__()

        self.key_edit = key_edit
        cast(QHBoxLayout, self.layout()).insertWidget(0, key_edit)

        self.value_edit = value_edit
        cast(QHBoxLayout, self.layout()).insertWidget(1, value_edit)


class DictFieldEdit(QWidget, TypeFieldEdit[dict]):
    changed = Signal()

    def init(self):
        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        self.append_btn = QPushButton()
        layout.addWidget(self.append_btn)

        self.setLayout(layout)

        self.append_btn.clicked.connect(self.append)

    def set_value(self, value):
        while self.layout().count() - 1 > len(value):
            self.remove(0)
        while self.layout().count() - 1 < len(value):
            self.append()
        for i, (k, v) in enumerate(value.items()):
            item = cast(
                DictFieldEditItem,
                self.layout().itemAt(i).widget(),
            )
            item.key_edit.set_value(k)
            item.value_edit.set_value(v)

    def get_value(self):
        ret = {}
        for i in range(self.layout().count() - 1):
            item = cast(
                DictFieldEditItem,
                self.layout().itemAt(i).widget(),
            )
            ret[item.key_edit.get_value()] = item.value_edit.get_value()
        return ret

    def insert(self, idx: int):
        layout = cast(QVBoxLayout, self.layout())

        kt, vt = get_args(self.t)
        k_widget = create_type_edit(kt)
        v_widget = create_type_edit(vt)
        wrapped_widget = DictFieldEditItem(k_widget, v_widget)
        layout.insertWidget(idx, wrapped_widget)

        k_widget.changed.connect(self.changed.emit)
        v_widget.changed.connect(self.changed.emit)
        wrapped_widget.insert.connect(
            lambda: self.insert(layout.indexOf(wrapped_widget))
        )
        wrapped_widget.remove.connect(
            lambda: self.remove(layout.indexOf(wrapped_widget))
        )
        self.changed.emit()

    def append(self):
        self.insert(self.layout().count() - 1)

    def remove(self, idx: int):
        w = self.layout().takeAt(idx).widget()
        w.setParent(None)
        w.deleteLater()
        self.changed.emit()


class TypedDictFieldEdit(QWidget, TypeFieldEdit[_TypedDict]):
    changed = Signal()

    def init(self):
        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        for k, t in self.t.__annotations__.items():
            field_layout = QHBoxLayout()

            name_label = QLabel(k)
            field_layout.addWidget(name_label)

            field_edit = create_type_edit(t)
            field_layout.addWidget(field_edit)

            layout.addLayout(field_layout)

            field_edit.changed.connect(self.changed.emit)

        self.setLayout(layout)

    def get_value(self):
        d = {}
        for i, k in enumerate(self.t.__annotations__.keys()):
            d[k] = cast(
                TypeFieldEdit, self.layout().itemAt(i).layout().itemAt(1).widget()
            ).get_value()
        return self.t(d)

    def set_value(self, value):
        for i, k in enumerate(self.t.__annotations__.keys()):
            cast(
                TypeFieldEdit, self.layout().itemAt(i).layout().itemAt(1).widget()
            ).set_value(value[k])


class DynamicStackWidget(QStackedWidget):

    def sizeHint(self):
        if self.currentWidget():
            return self.currentWidget().sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):
        if self.currentWidget():
            return self.currentWidget().minimumSizeHint()
        return super().minimumSizeHint()


class UnionFieldEdit(QWidget, TypeFieldEdit[UnionType]):
    changed = Signal()

    def init(self):
        self.field_types = get_args(self.t)

        layout = QHBoxLayout()
        layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)

        head_layout = QVBoxLayout()

        self.type_selector = QComboBox()
        head_layout.addWidget(self.type_selector)

        head_layout.addStretch()

        layout.addLayout(head_layout)

        field_layout = QVBoxLayout()

        field_layout.addStretch()

        self.field_input = QStackedWidget()
        field_layout.addWidget(self.field_input)

        layout.addLayout(field_layout)

        self.setLayout(layout)

        self.editors: dict[type, TypeFieldEdit] = {}
        for t in self.field_types:
            editor = create_type_edit(t)
            self.type_selector.addItem(self.type_name(t))
            self.field_input.addWidget(editor)
            self.editors[t] = editor

            editor.changed.connect(self.changed)

        self.type_selector.currentIndexChanged.connect(self.change_type_idx)
        self.type_selector.currentIndexChanged.connect(lambda _: self.changed.emit())

    def get_value(self):
        return cast(TypeFieldEdit, self.field_input.currentWidget()).get_value()

    def set_value(self, value):
        origins = [get_origin(t) or t for t in self.field_types]
        idx = origins.index(type(value))
        self.type_selector.setCurrentIndex(idx)
        self.editors[idx].set_value(value)

    def change_type_idx(self, idx):
        self.field_input.setCurrentIndex(idx)

    def type_name(self, ty: type):
        if is_typeddict(ty):
            return "TypedDict"
        if args := get_args(ty):
            return f"{get_origin(ty).__name__}[{", ".join(self.type_name(a) for a in args)}]"
        return ty.__name__


class PluginConfigWidget(QWidget):
    def __init__(self, plugin_class: type[BasePlugin]):
        super().__init__()

        self.plugin_class = plugin_class
        self.config_class = plugin_class.config_type()

        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        for field in fields(self.config_class):
            field_layout = QHBoxLayout()

            name_label = QLabel(field.name)
            field_layout.addWidget(name_label)

            field_edit = create_type_edit(field.type)
            field_layout.addWidget(field_edit)

            layout.addLayout(field_layout)

        self.setLayout(layout)

    def load(self):
        config = self.plugin_class.read_config()
        for i, field in enumerate(fields(self.config_class)):
            cast(
                TypeFieldEdit,
                self.layout().itemAt(i).layout().itemAt(1).widget(),
            ).set_value(getattr(config, field.name))

    def get_config(self):
        d = {}
        for i, field in enumerate(fields(self.config_class)):
            d[field.name] = cast(
                TypeFieldEdit,
                self.layout().itemAt(i).layout().itemAt(1).widget(),
            ).get_value()
        return self.config_class(**d)


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
