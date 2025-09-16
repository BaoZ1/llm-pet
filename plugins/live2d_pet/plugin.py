from framework.plugin import BasePlugin
from framework.config import BaseConfig
from framework.window import TransparentWindow
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QVBoxLayout, QLayout
import live2d.v3 as live2d
from OpenGL import GL

class Live2dRenderer(QOpenGLWidget):

    def __init__(self, model_json: str) -> None:
        super().__init__()
        
        self.model: live2d.LAppModel | None = None
        self.model_json = model_json

    def initializeGL(self) -> None:
        live2d.glInit()
        self.model = live2d.LAppModel()

        self.model.LoadModelJson(self.model_json)

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


class Live2dPet(TransparentWindow):
    def __init__(self, model_json: str):
        super().__init__()

        layout = QVBoxLayout(self)
        self.render_widget = Live2dRenderer(model_json)
        layout.addWidget(self.render_widget)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        
        self.render_widget.resize(300, 500)


class Plugin(BasePlugin):
    name = "live2d_pet"


    def init(self):
        pass
        # self.pet = Live2dPet(
        #     pathlib.Path(__file__).parent
        #     / "test_model"
        #     / "plugins\live2d_pet\test_model\Haru.model3.json"
        # )
        


class Config(BaseConfig):
    pass

