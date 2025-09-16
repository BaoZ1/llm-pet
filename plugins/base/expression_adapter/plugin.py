from framework.plugin import BasePlugin
from framework.config import BaseConfig
from plugins.base.plugin import Plugin as BasePetPlugin
from plugins.expression.plugin import Plugin as ExpressionPlugin, ExpressionSetEvent

class Plugin(BasePlugin):
    deps = [BasePetPlugin, ExpressionPlugin]

    def init(self):
        self.pet = self.dep(BasePetPlugin)._pet
        self.color_map = {
            "normal": 210,
            "happy": 120,
            "sad": 34,
            "angry": 0,
        }

    def on_event(self, e):
        match e:
            case ExpressionSetEvent(expression):
                self.pet.color_hue = self.color_map[expression]
                self.pet.repaint()


class Config(BaseConfig):
    pass