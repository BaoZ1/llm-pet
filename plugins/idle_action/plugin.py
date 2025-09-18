from framework.plugin import BasePlugin, PetPluginProtocol
from framework.event import Event, PlainEvent, Task
import asyncio
from dataclasses import dataclass
import math
import random
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


@dataclass
class WanderEvent(Event):
    tags = ["move"]

    new_pos: tuple[int, int]


class WanderTask(Task):
    interval = 0.02

    def __init__(self, init_pos: tuple[int, int], target: tuple[int, int]):

        self.target = target
        self.init_pos = init_pos
        self.progress: tuple[int, int] = None

        self.speed = 50

        self.running = True

    def merge(self, old_task):
        return self, None

    def execute_info(self):
        return f"Wander: from {self.init_pos} to {self.target}; Progress: {round(self.progress[1] / self.progress[0] * 100)}%"

    def on_event(self, event):
        if "move" in event.tags and not isinstance(event, WanderEvent):
            self.running = False

    async def execute(self, manager):
        dx = self.target[0] - self.init_pos[0]
        dy = self.target[1] - self.init_pos[1]
        distance = math.hypot(dx, dy)
        step_count = round(distance / self.speed / self.interval)
        self.progress = (step_count, 0)
        for i in range(1, step_count):
            if not self.running:
                return
            new_pos = (
                int(self.init_pos[0] + dx * i / step_count),
                int(self.init_pos[1] + dy * i / step_count),
            )
            manager.trigger_event(WanderEvent(new_pos))
            await asyncio.sleep(self.interval)
            self.progress = (step_count, i)
        manager.trigger_event(WanderEvent(new_pos))
        self.progress = (step_count, step_count)


class Plugin(BasePlugin):
    name = "idle_action"
    deps = [PetPluginProtocol]

    def init(self):
        self.pet = self.dep(PetPluginProtocol).pet()
        self.screen_range: tuple[int, int] = (
            QApplication.primaryScreen().size() - self.pet.size()
        ).toTuple()

        self.bored_interval = (80, 200)
        self.bored_timer = QTimer()
        self.bored_timer.timeout.connect(self.emit_bored)
        self.bored_timer.start(random.randrange(*self.bored_interval))

        self.wander_range = (100, 300)
        self.wander_interval = (30, 60)
        self.wander_timer = QTimer()
        self.wander_timer.timeout.connect(self.start_wandering)
        self.wander_timer.start(random.randrange(*self.wander_interval) * 1e3)

    def emit_bored(self):
        self.trigger_event(PlainEvent("You feel a bit bored..."))
        self.bored_timer.start(random.randrange(*self.bored_interval) * 1e3)

    def start_wandering(self):
        current_pos: tuple[int, int] = self.pet.pos().toTuple()
        direction = random.random() * math.pi * 2
        dist = random.randint(*self.wander_range)
        target_pos = (
            min(
                max(0, current_pos[0] + dist * math.cos(direction)),
                self.screen_range[0],
            ),
            min(
                max(0, current_pos[1] + dist * math.sin(direction)),
                self.screen_range[1],
            ),
        )

        self.add_task(WanderTask(self.pet.pos().toTuple(), target_pos))
        self.wander_timer.start(random.randrange(*self.wander_interval) * 1e3)

    def on_event(self, e):
        match e:
            case WanderEvent(new_pos):
                self.pet.move(*new_pos)
            case _:
                if "move" in e.tags:
                    self.wander_timer.start(
                        random.randrange(*self.wander_interval) * 1e3
                    )
                if "user" in e.tags:
                    self.bored_timer.start(random.randrange(*self.bored_interval) * 1e3)


