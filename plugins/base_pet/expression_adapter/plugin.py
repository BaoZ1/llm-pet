from framework.plugin import BasePlugin
from framework.config import BaseConfig
from dataclasses import dataclass, field
from plugins.base_pet.plugin import Plugin as BasePetPlugin
from plugins.expression.plugin import Plugin as ExpressionPlugin, ExpressionSetEvent
from typing import cast, Annotated

@dataclass
class Config(BaseConfig):
    color_map: dict[str, Annotated[int, "", 0, 360]] = field(default_factory=dict)

class Plugin(BasePlugin):
    deps = [BasePetPlugin, ExpressionPlugin]

    def init(self):
        self.pet = self.dep(BasePetPlugin)._pet

    def on_event(self, e):
        match e:
            case ExpressionSetEvent(expression):
                if hue := cast(Config, self.get_config()).color_map.get(expression, None):
                    self.pet.color_hue = hue
                    self.pet.repaint()
                elif hue := cast(Config, self.get_config()).color_map.get("normal", None):
                    self.pet.color_hue = hue
                    self.pet.repaint()
