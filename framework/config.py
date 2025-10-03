from __future__ import annotations
from types import NoneType, UnionType
from typing import (
    _TypedDict,
    Annotated,
    ClassVar,
    TypedDict,
    cast,
    get_args,
    get_origin,
    is_typeddict,
)
import yaml
from pathlib import Path
from dataclasses import dataclass, fields, field, is_dataclass
from PySide6.QtCore import Qt, Signal, SignalInstance
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QStackedWidget,
    QLabel,
    QLineEdit,
    QSpinBox,
    QPushButton,
    QSizePolicy,
    QFileDialog,
)
from langchain_openai import ChatOpenAI


yaml.add_multi_representer(
    Path,
    lambda d, v: d.represent_scalar("!path", str(v.absolute())),
)
yaml.add_constructor(
    "!path",
    lambda l, n: Path(l.construct_scalar(n)),
)


@dataclass
class BaseConfig:
    enabled: bool = False

@dataclass
class DataclassType:
    pass


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
            elif is_dataclass(idx_t):
                return TypeFieldEdit.edits[DataclassType]().with_type(
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


class DataclassFieldEdit(QWidget, TypeFieldEdit[DataclassType]):
    changed = Signal()

    def init(self):
        layout = QVBoxLayout()
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        for field in fields(self.t):
            field_layout = QHBoxLayout()

            name_label = QLabel(field.name)
            field_layout.addWidget(name_label)

            field_type = eval(field.type) if isinstance(field.type, str) else field.type
            field_edit = TypeFieldEdit.create(field_type)
            field_layout.addWidget(field_edit)

            layout.addLayout(field_layout)

            field_edit.changed.connect(self.changed.emit)

        self.setLayout(layout)

    def get_value(self):
        d = {}
        for i, field in enumerate(fields(self.t)):
            d[field.name] = cast(
                TypeFieldEdit, self.layout().itemAt(i).layout().itemAt(1).widget()
            ).get_value()
        return self.t(**d)

    def set_value(self, value):
        for i, field in enumerate(fields(self.t)):
            cast(
                TypeFieldEdit, self.layout().itemAt(i).layout().itemAt(1).widget()
            ).set_value(getattr(value, field.name))


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
        vt = type(value)
        idx = origins.index(vt)
        self.type_selector.setCurrentIndex(idx)
        self.editors[vt].set_value(value)

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
    enable_changed = Signal()

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

            field_type = eval(field.type) if isinstance(field.type, str) else field.type
            field_edit = TypeFieldEdit.create(field_type)
            cast(QWidget, field_edit).setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self.edits[field.name] = field_edit
            field_layout.addWidget(field_edit)

            if field_edit.comment:
                comment_label = QLabel(field_edit.comment)
                name_layout.addWidget(comment_label)
            field_edit.changed.connect(self.changed.emit)
            if field.name == "enabled":
                field_edit.changed.connect(self.enable_changed.emit)

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
