from framework.plugin import BasePlugin
from framework.agent import InvokeStartEvent, InvokeEndEvent, PluginFieldEvent
from framework.event import Event
from dataclasses import dataclass
import pathlib
import time
from plugins.pet_state.plugin import Plugin as PetStatePlugin, ModifyPetStateEvent
from PySide6.QtCore import QTimer


@dataclass
class ExpressionSetEvent(Event):
    expression: str


class Plugin(BasePlugin):
    deps = [PetStatePlugin]

    def init(self):
        self.state = self.dep(PetStatePlugin).state

        self.normal_expression: str = "normal"
        self.current_expression: tuple[str, str] | None = None
        self.expression: str | None = None

        self.last_update_time = time.time()
        self.clear_expression_timer: QTimer | None = None

    def prompts(self):
        return {"json_fields": self.root_dir() / "expression_field.md"}

    def infos(self):
        return {
            "Current State": {
                "Expression": self.expression,
            }
        }

    def set_expression(self, expression: str):
        if expression == self.expression:
            return
        self.expression = expression
        self.trigger_event(ExpressionSetEvent(expression))
        self.last_update_time = time.time()

    def try_set_expression(self):
        if self.clear_expression_timer is not None:
            self.clear_expression_timer.stop()
            self.clear_expression_timer = None

        if self.current_expression:
            self.set_expression(self.current_expression[0])
            self.last_update_time = time.time()
        else:
            current_time = time.time()
            min_time = self.last_update_time + 3
            self.clear_expression_timer = QTimer(singleShot=True)
            self.clear_expression_timer.timeout.connect(
                lambda: self.set_expression(self.normal_expression)
            )
            self.clear_expression_timer.start(max(0, min_time - current_time) * 1e3)

    def refresh_normal_expression(self):
        old_expression = self.normal_expression
        if self.state["mood"] < -20:
            self.normal_expression = "sad"
        if old_expression != self.normal_expression:
            return True
        return False


    def on_event(self, event):
        match event:
            case ModifyPetStateEvent():
                if self.refresh_normal_expression():
                    self.try_set_expression()
            case PluginFieldEvent("expression", {"type": t, "duration": d}):
                self.current_expression = (t, d)
                self.try_set_expression()
            case InvokeStartEvent():
                if (
                    self.current_expression
                    and self.current_expression[1] == "continuous"
                ):
                    self.current_expression = None
                    self.try_set_expression()
            case InvokeEndEvent():
                if (
                    self.current_expression
                    and self.current_expression[1] == "temporary"
                ):
                    self.current_expression = None
                    self.try_set_expression()

