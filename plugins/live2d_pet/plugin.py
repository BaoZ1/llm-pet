from framework.plugin import BasePlugin, PetPluginProtocol
from framework.config import BaseConfig
from framework.window import TransparentWindow
import pathlib
from typing import cast
from dataclasses import dataclass
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QVBoxLayout, QLayout
import live2d.v3 as live2d
from OpenGL import GL


class Live2dRenderer(QOpenGLWidget):

    def __init__(self, model_json: str) -> None:
        super().__init__()

        live2d.init()

        self.model: live2d.LAppModel | None = None
        self.model_json = model_json
        
        self.exp_idx = 1

    def initializeGL(self) -> None:
        live2d.glInit()
        self.model = live2d.LAppModel()

        self.model.LoadModelJson(self.model_json)
        print(self.model.GetExpressionIds(), self.model.GetMotionGroups())

        self.startTimer(int(1000 / 120))

    def resizeGL(self, w: int, h: int) -> None:
        GL.glViewport(0, 0, w, h)
        self.model.Resize(w, h)

    def paintGL(self) -> None:
        live2d.clearBuffer()

        self.model.Update()

        self.model.Draw()

    def timerEvent(self, a0):
        self.update()
        
    # def mousePressEvent(self, event):
    #     self.model.SetExpression(f"F0{self.exp_idx}")
    #     self.exp_idx = self.exp_idx % 8 + 1


class Live2dPet(TransparentWindow):
    def __init__(self, model_json: str):
        super().__init__()

        layout = QVBoxLayout(self)
        self.render_widget = Live2dRenderer(model_json)
        layout.addWidget(self.render_widget)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.render_widget.setFixedSize(300, 500)
        
        # self.render_widget.show()


@dataclass
class Config(BaseConfig):
    model_json: pathlib.Path = pathlib.Path(__file__)


class Plugin(BasePlugin, PetPluginProtocol):
    def init(self):
        self._pet = Live2dPet(
            str((self.root_dir() / cast(Config, self.read_config()).model_json).absolute())
        )
        
        self._pet.show()
        
    def live2d_model(self):
        return self._pet.render_widget.model

    def pet(self):
        return self._pet
