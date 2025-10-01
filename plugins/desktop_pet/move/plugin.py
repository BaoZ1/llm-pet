from framework.plugin import BasePlugin
from framework.event import PluginFieldEvent, Event, Task, TaskManager
from framework.agent import EventMessage
from plugins.desktop_pet.pet import PetPluginBase
import asyncio
from dataclasses import dataclass
import math
import pathlib
from typing import Literal
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication


@dataclass
class MoveEvent(Event):
    tags = ["move"]

    new_pos: tuple[int, int]
    finished: bool

    def agent_msg(self):
        if self.finished:
            return EventMessage(f"You've arrived at {self.new_pos}")


class MoveTask(Task):
    speed = {
        "walk": 200,
        "run": 500,
    }
    interval = 0.02

    def __init__(
        self,
        init_pos: tuple[int, int],
        target: tuple[int, int],
        action: Literal["walk", "run"],
    ):
        self.target = target
        self.action = action
        self.init_pos = init_pos
        self.progress: tuple[int, int] = None

        self.running = True

    def merge(self, old_task):
        return self, None

    def on_event(self, event):
        if "move" in event.tags and "user" in event.tags:
            self.running = False

    async def execute(self):
        dx = self.target[0] - self.init_pos[0]
        dy = self.target[1] - self.init_pos[1]
        distance = math.hypot(dx, dy)
        step_count = round(distance / self.speed[self.action] / self.interval)
        self.progress = (step_count, 0)
        for i in range(1, step_count):
            if not self.running:
                return
            new_pos = (
                int(self.init_pos[0] + dx * i / step_count),
                int(self.init_pos[1] + dy * i / step_count),
            )
            TaskManager.trigger_event(MoveEvent(new_pos, False))
            await asyncio.sleep(self.interval)
            self.progress = (step_count, i)
        TaskManager.trigger_event(MoveEvent(new_pos, True))
        self.progress = (step_count, step_count)

    def execute_info(self):
        return f"Move: from {self.init_pos} to {self.target}; Progress: {round(self.progress[1] / self.progress[0] * 100)}%"


class Plugin(BasePlugin):
    deps = [PetPluginBase]

    def on_dep_load(self, dep):
        if isinstance(dep, PetPluginBase):
            self.pet = dep.pet
            self.screen_size: tuple[int, int] = (
                (QApplication.primaryScreen().size() - self.pet.size())
                * QApplication.primaryScreen().devicePixelRatio()
            ).toTuple()

    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("move_field.md")}

    def infos(self):
        return {
            "Screen": {
                "Screen Size": self.screen_size,
                "Your Position": (
                    self.pet.pos() * QApplication.primaryScreen().devicePixelRatio()
                ).toTuple(),
            }
        }

    def on_event(self, e):
        match e:
            case PluginFieldEvent("move", args):
                self.add_task(
                    MoveTask(
                        (
                            self.pet.pos()
                            * QApplication.primaryScreen().devicePixelRatio()
                        ).toTuple(),
                        args["target"],
                        args["action"],
                    )
                )
            case MoveEvent(new_pos, _):
                self.pet.move(
                    QPoint(*new_pos) / QApplication.primaryScreen().devicePixelRatio()
                )
