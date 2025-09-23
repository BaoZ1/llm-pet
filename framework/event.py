from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Self, Never, Callable
import asyncio
from langchain_core.messages import BaseMessage


class Event(ABC):
    tags: ClassVar[list[str]] = []

    @property
    def name(self):
        return self.__class__.__name__

    def agent_msg(self) -> str | BaseMessage | None:
        return None


class InvokeStartEvent(Event):
    pass


class InvokeEndEvent(Event):
    pass


@dataclass
class PluginFieldEvent(Event):
    key: str
    value: Any


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
    async def execute(self) -> None:
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
    tasks: ClassVar[dict[str, tuple[Task, asyncio.Task]]] = {}
    event_callbacks: ClassVar[dict[str, Callable[[Event], None]]] = {}

    @classmethod
    async def task_wrapper(cls, task: Task):
        try:
            await task.execute()
        except asyncio.CancelledError:
            pass
        else:
            cls.tasks.pop(task.name)

    @classmethod
    async def add_tasks_no_check(cls, tasks: list[Task]):
        for task in tasks:
            cls.tasks[task.name] = (
                task,
                asyncio.create_task(cls.task_wrapper(task)),
            )

    @classmethod
    async def add_task(cls, new_task: Task):
        old_task, running_old_task = cls.tasks.get(new_task.name, (None, None))
        merged_task, msg = new_task.merge(old_task)
        if merged_task is not None:
            if running_old_task is not None:
                running_old_task.cancel()
            cls.tasks[merged_task.name] = (
                merged_task,
                asyncio.create_task(cls.task_wrapper(merged_task)),
            )
        cls.trigger_event(NewTaskEvent(old_task, new_task, msg))

    @classmethod
    def register_callback(cls, key: str, cb: Callable[[Event], None]):
        assert key not in cls.event_callbacks
        cls.event_callbacks[key] = cb

    @classmethod
    def remove_callback(cls, key: str):
        cls.event_callbacks.pop(key, None)

    @classmethod
    def trigger_event(cls, event: Event):
        for task, _ in cls.tasks.values():
            task.on_event(event)
        for cb in cls.event_callbacks.values():
            cb(event)

    @classmethod
    def task_execute_infos(cls) -> list[str]:
        return list(
            filter(
                lambda x: x is not None,
                [task.execute_info() for task, _ in cls.tasks.values()],
            )
        )
