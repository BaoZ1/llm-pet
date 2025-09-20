from __future__ import annotations
from PySide6.QtCore import (
    Qt,
    Signal,
    QPoint,
    QRect,
    QObject,
    SignalInstance,
    QPropertyAnimation,
)
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
    QToolButton,
    QScrollArea,
    QSizePolicy,
    QFileDialog,
    QStyle,
    QGroupBox,
)
from typing import (
    Callable,
    Self,
    Sequence,
    cast,
    get_args,
    get_origin,
    _TypedDict,
    is_typeddict,
    ClassVar,
    Annotated,
)
from types import UnionType, NoneType
from enum import Enum, auto
from .agent import Event
from .config import BaseConfig
from .plugin import BasePlugin, PluginManager
from dataclasses import fields
from pathlib import Path
import sys
import os
from functools import partial

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


class TypeFieldEdit[T]:
    edits: ClassVar[dict[type, type[TypeFieldEdit]]] = {}

    changed: SignalInstance

    def __init_subclass__(cls):
        for b in cls.__orig_bases__:
            if get_origin(b) is TypeFieldEdit:
                t = get_args(b)[0]
                TypeFieldEdit.edits[get_origin(t) or t] = cls
                return

    @staticmethod
    def create[E](t: type[E]) -> TypeFieldEdit[E]:
        comment, extra_args = "", ()
        if get_origin(t) is Annotated:
            t, comment, *extra_args = get_args(t)
        idx_t = get_origin(t) or t
        if idx_t not in TypeFieldEdit.edits:
            if is_typeddict(idx_t):
                return TypeFieldEdit.edits[_TypedDict]().with_type(
                    t, comment, extra_args
                )
            elif issubclass(idx_t, Path):
                return TypeFieldEdit.edits[Path]().with_type(t, comment, extra_args)
            raise Exception(t)
        return TypeFieldEdit.edits[idx_t]().with_type(t, comment, extra_args)

    def with_type(self, t: type[T], comment: str, extra_args: tuple):
        self.t = t
        self.comment = comment
        self.extra_args = extra_args
        self.init()
        return self

    def init(self) -> None:
        raise

    def set_value(self, value: T) -> None:
        raise

    def get_value(self) -> T:
        raise


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
        if self.extra_args:
            self.setRange(*self.extra_args)

    def set_value(self, value):
        self.setValue(value)

    def get_value(self):
        return self.value()


class PathFieldEdit(QWidget, TypeFieldEdit[Path]):
    changed = Signal()

    def init(self):
        layout = QHBoxLayout()

        self.line_edit = QLineEdit()
        layout.addWidget(self.line_edit)

        self.btn = QPushButton()
        layout.addWidget(self.btn)

        self.setLayout(layout)

        self.btn.clicked.connect(self.open_explorer)
        self.line_edit.textChanged.connect(lambda _: self.changed.emit())

    def open_explorer(self):
        dialog = QFileDialog(self)
        for arg in self.extra_args:
            if isinstance(arg, QFileDialog.FileMode):
                dialog.setFileMode(arg)
        if dialog.exec():
            p = dialog.selectedFiles()[0]
            self.line_edit.setText(str(Path(p).absolute()))

    def get_value(self):
        return Path(self.line_edit.text())

    def set_value(self, value):
        self.line_edit.setText(str(value.absolute()))


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

        widget = TypeFieldEdit.create(get_args(self.t)[0])
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
        k_widget = TypeFieldEdit.create(kt)
        v_widget = TypeFieldEdit.create(vt)
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

            field_edit = TypeFieldEdit.create(t)
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
            editor = TypeFieldEdit.create(t)
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


class ConfigEdit(QWidget):
    changed = Signal()

    def __init__(self, config_class: type[BaseConfig]):
        super().__init__()

        self.config_class = config_class

        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        self.edits: dict[str, TypeFieldEdit] = {}
        for field in fields(self.config_class):
            field_layout = QHBoxLayout()

            name_layout = QVBoxLayout()
            name_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            name_label = QLabel(field.name)
            name_layout.addWidget(name_label)
            field_layout.addLayout(name_layout)

            field_edit = TypeFieldEdit.create(field.type)
            cast(QWidget, field_edit).setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self.edits[field.name] = field_edit
            field_layout.addWidget(field_edit)

            if field_edit.comment:
                comment_label = QLabel(field_edit.comment)
                name_layout.addWidget(comment_label)
            field_edit.changed.connect(self.changed.emit)

            layout.addLayout(field_layout)

        self.setLayout(layout)

    def load(self, config: BaseConfig):
        self.blockSignals(True)
        for field in fields(self.config_class):
            self.edits[field.name].set_value(getattr(config, field.name))
        self.blockSignals(False)

    def get(self):
        d = {}
        for field in fields(self.config_class):
            d[field.name] = self.edits[field.name].get_value()
        return self.config_class(**d)


class DynamicScrollArea(QScrollArea):
    def sizeHint(self):
        if w := self.widget():
            return w.sizeHint()
        return super().sizeHint()


class CollapsibleWidget(QWidget):
    expanded = Signal(QWidget)

    def __init__(self, title: str, body: QWidget):
        super().__init__()

        self.body = body

        layout = QVBoxLayout()
        # layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        self.title_widget = QToolButton(autoRaise=True)
        self.title_widget.setText(title)
        self.title_widget.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.title_widget.setArrowType(Qt.ArrowType.RightArrow)
        self.title_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.title_widget)

        self.body_area = DynamicScrollArea()
        self.body_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.body_area.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.body_area.setMinimumHeight(0)
        self.body_area.setMaximumHeight(0)
        self.body_area.setWidget(body)
        layout.addWidget(self.body_area)

        self.setLayout(layout)

        self.expand = False
        self.title_widget.clicked.connect(self.toggle)
        self.animation = QPropertyAnimation(self.body_area, "maximumHeight".encode())
        self.animation.setDuration(100)

    def toggle(self):
        self.expand = not self.expand

        if self.expand:
            self.title_widget.setArrowType(Qt.ArrowType.DownArrow)
            body_height = self.body.sizeHint().height()
            self.animation.setStartValue(0)
            self.animation.setEndValue(min(body_height, 400))
            self.expanded.emit(self)
        else:
            self.title_widget.setArrowType(Qt.ArrowType.RightArrow)
            self.animation.setStartValue(self.body_area.height())
            self.animation.setEndValue(0)
        self.animation.start()
        
    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(max(hint.width(), self.body.width()))
        return hint

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

        self.config_widgets: dict[type[BasePlugin], CollapsibleWidget] = {}
        config_content = QWidget()
        config_content.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        config_content_layout = QVBoxLayout()
        config_content_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        for plugin_class in PluginManager.plugin_classes:
            title = plugin_class.identifier()
            config_widget = ConfigEdit(plugin_class.config_type())
            config_widget.setSizePolicy(
                QSizePolicy.Policy.MinimumExpanding,
                QSizePolicy.Policy.Fixed,
            )
            config_widget.changed.connect(
                partial(self.save_config, plugin_class, config_widget.get)
            )
            wrapped_widget = CollapsibleWidget(title, config_widget)
            wrapped_widget.setSizePolicy(
                QSizePolicy.Policy.MinimumExpanding,
                QSizePolicy.Policy.Fixed,
            )
            config_content_layout.addWidget(wrapped_widget)
            self.config_widgets[plugin_class] = wrapped_widget
            wrapped_widget.expanded.connect(self.close_others)
        config_content.setLayout(config_content_layout)
        self.c = config_content
        config_list_area.setWidget(config_content)
        self.a = config_list_area
        self.setLayout(layout)

    def mousePressEvent(self, event):
        for i in range(self.c.layout().count()):
            print(self.c.layout().itemAt(i).widget().sizeHint())
        print("===================================")
        print(self.c.sizeHint(), self.c.layout().sizeHint())

    def close_others(self, open_widget: CollapsibleWidget):
        for w in self.config_widgets.values():
            if w is not open_widget and w.expand:
                w.toggle()

    def showEvent(self, event):
        for plugin_class, config_widget in self.config_widgets.items():
            cast(ConfigEdit, config_widget.body).load(plugin_class.get_config())

    def save_config(self, plugin_class: type[BasePlugin], getter: Callable):
        plugin_class.update_config(getter())


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
