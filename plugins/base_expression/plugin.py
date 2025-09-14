import asyncio
from dataclasses import dataclass
import pathlib
import time
from typing import cast
from framework.plugin import PluginInterface
from plugins.base.plugin import Plugin as BasePetPlugin
from plugins.pet_state.plugin import (
    Plugin as PetStatePlugin,
    ModifyPetStateEvent,
    PetState,
)
from framework.agent import InvokeStartEvent, InvokeEndEvent, PluginFieldEvent
from framework.event import Event, Task, TaskManager


@dataclass
class ExpressionSetEvent(Event):

    expression_type: str
    duration: str


@dataclass
class ExpressionUpdateEvent(Event):
    expression: str


class ExpressionManageTask(Task):
    def __init__(self, state: PetState):
        super().__init__()

        self.state = state

        self.should_update = asyncio.Event()

        self.normal_expression: str = "normal"
        self.current_expression: tuple[str, str] | None = None
        self.last_changed_expression: str | None = None

        self.last_update_time = time.time()
        self.delay_task: asyncio.Task | None = None

    def set_expression(self, manager: TaskManager, expression: str):
        if expression == self.last_changed_expression:
            return
        manager.trigger_event(ExpressionUpdateEvent(expression))

    async def delay_to_normal(self, manager: TaskManager, expression: str):
        try:
            current_time = time.time()
            min_time = self.last_update_time + 3
            await asyncio.sleep(max(0, min_time - current_time))
            self.set_expression(manager, expression)
            self.last_update_time = time.time()
        except asyncio.CancelledError:
            pass

    async def execute(self, manager):
        while True:
            await self.should_update.wait()
            if self.delay_task:
                self.delay_task.cancel()
                self.delay_task = None

            if self.current_expression:
                self.set_expression(manager, self.current_expression[0])
                self.last_update_time = time.time()
            else:
                self.delay_task = asyncio.create_task(
                    self.delay_to_normal(manager, self.normal_expression)
                )
            self.should_update.clear()

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
                    self.should_update.set()
            case PluginFieldEvent("expression", args):
                self.current_expression = (args["type"], args["duration"])
                self.should_update.set()
            case InvokeStartEvent():
                if (
                    self.current_expression
                    and self.current_expression[1] == "continuous"
                ):
                    self.current_expression = None
                    self.should_update.set()
            case InvokeEndEvent():
                if (
                    self.current_expression
                    and self.current_expression[1] == "temporary"
                ):
                    self.current_expression = None
                    self.should_update.set()


class Plugin(PluginInterface):
    name = "base_expression"
    dep_names = ["base_pet", "pet_state"]

    def init(self, screen):
        self.expression = "normal"
        self.color_mapper = {
            "normal": 210,
            "happy": 120,
            "sad": 34,
            "angry": 0,
        }
        self.pet = cast(BasePetPlugin, self.deps["base_pet"]).pet

    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("expression_field.md")}

    def infos(self):
        return {
            "Current State": {
                "Expression": self.expression,
            }
        }

    def init_tasks(self):
        return [
            ExpressionManageTask(cast(PetStatePlugin, self.deps["pet_state"]).state)
        ]

    def on_event(self, e):
        match e:
            case ExpressionUpdateEvent(expression):
                self.expression = expression
                self.pet.color_hue = self.color_mapper[self.expression]
                self.pet.repaint()
