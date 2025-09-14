from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Self, Never, Callable, TYPE_CHECKING
import asyncio
if TYPE_CHECKING:
    from .agent import Agent


class Event(ABC):
    tags: ClassVar[list[str]] = []
    msg_prefix: ClassVar[str | None] = "Event"

    @property
    def name(self):
        return self.__class__.__name__

    def trigger(self):
        pass

    def agent_msg(self) -> str | None:
        return None


@dataclass
class PlainEvent(Event):
    content: str

    def agent_msg(self):
        return self.content


class Task(ABC):
    @property
    def name(self):
        return self.__class__.__name__

    @abstractmethod
    async def execute(self, manager: "TaskManager") -> None:
        raise NotImplementedError

    def execute_info(self) -> str | None:
        return None

    def merge(self, old_task: Self | None) -> tuple[Self | None, str | None] | Never:
        if old_task is None:
            return self, None
        raise

    def on_event(self, event: Event):
        return


@dataclass
class NewTaskEvent(Event):
    old_task: Task | None
    new_task: Task
    msg: str | None

    def agent_msg(self):
        return self.msg


class TaskManager:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.tasks: dict[str, tuple[Task, asyncio.Task]] = {}
        self.event_callbacks: dict[str, Callable[[Event], None]] = {}

    async def task_wrapper(self, task: Task):
        try:
            await task.execute(self)
        except asyncio.CancelledError:
            pass
        else:
            self.tasks.pop(task.name)

    async def add_tasks_no_check(self, tasks: list[Task]):
        for task in tasks:
            self.tasks[task.name] = (
                task,
                asyncio.create_task(self.task_wrapper(task)),
            )

    async def add_task(self, new_task: Task):
        old_task, running_old_task = self.tasks.get(new_task.name, (None, None))
        merged_task, msg = new_task.merge(old_task)
        if merged_task is not None:
            if running_old_task is not None:
                running_old_task.cancel()
            self.tasks[merged_task.name] = (
                merged_task,
                asyncio.create_task(self.task_wrapper(merged_task)),
            )
        self.trigger_event(NewTaskEvent(old_task, new_task, msg))

    def register_callback(self, key: str, cb: Callable[[Event], None]):
        assert key not in self.event_callbacks
        self.event_callbacks[key] = cb

    def remove_callback(self, key: str):
        self.event_callbacks.pop(key, None)

    def trigger_event(self, event: Event):
        for task, _ in self.tasks.values():
            task.on_event(event)
        for cb in self.event_callbacks.values():
            cb(event)

    def task_execute_infos(self) -> list[str]:
        return list(
            filter(
                lambda x: x is not None,
                [task.execute_info() for task, _ in self.tasks.values()],
            )
        )
