from framework.plugin import BasePlugin
from framework.config import BaseConfig
from dataclasses import dataclass, field
from plugins.live2d_pet.plugin import Plugin as Live2dPetPlugin
from plugins.expression.plugin import Plugin as ExpressionPlugin, ExpressionSetEvent
from live2d.v3.live2d import LAppModel
from typing import cast

@dataclass
class Config(BaseConfig):
    expression_map: dict[str, str] = field(default_factory=dict)


class Plugin(BasePlugin):
    deps = [Live2dPetPlugin, ExpressionPlugin]

    def init(self):
        self.live2d_model: LAppModel | None = None

    def on_event(self, e):
        match e:
            case ExpressionSetEvent(expression):
                if self.live2d_model is None:
                    m = self.dep(Live2dPetPlugin).live2d_model()
                    if m is None:
                        return
                    self.live2d_model = m

                if exp := cast(Config, self.config).expression_map.get(
                    expression, None
                ):
                    self.live2d_model.SetExpression(exp)
                elif exp := cast(Config, self.config).expression_map.get(
                    "normal", None
                ):
                    self.live2d_model.SetExpression(exp)
