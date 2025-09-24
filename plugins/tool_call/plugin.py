from dataclasses import dataclass
from framework.plugin import BasePlugin, PluginManager
from framework.event import PluginFieldEvent, Event, Task, TaskManager, PlainEvent
from framework.agent import ToolCallEvent
import pathlib
import json
from langchain_core.messages import ToolMessage, AIMessage
from langgraph.prebuilt import ToolNode
from datetime import datetime
from typing import Self
import asyncio


@dataclass
class ToolResultEvent(Event):
    tool_msgs: list[ToolMessage]

    def agent_msg(self):
        return self.tool_msgs


class ToolTask(Task):
    @property
    def name(self):
        return f"{self.__class__.__name__}-{self.time}"

    def __init__(self, params: list[dict]):
        self.time = datetime.now()
        self.params = [
            {**p, "id": f"{p["name"]}-{self.time}-{idx}", "type": "tool_call"}
            for idx, p in enumerate(params)
        ]
        self.tool_caller = ToolNode(PluginManager.tools)

    async def execute(self):
        TaskManager.trigger_event(PlainEvent(AIMessage("", tool_calls=self.params)))
        ret = await self.tool_caller.ainvoke(self.params)
        TaskManager.trigger_event(ToolResultEvent(ret["messages"]))

    def execute_info(self):
        tool_descriptions = []
        for p in self.params:
            tool_descriptions.append(f"- {p["name"]}({p["args"]})")

        return "\n".join(
            [f"Processing Tools (Submitted at {self.time}):", *tool_descriptions]
        )


class Plugin(BasePlugin):
    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("tools_field.md")}

    def on_event(self, e):
        match e:
            case PluginFieldEvent("tools", v):
                if len(v) != 0:
                    self.add_task(ToolTask(v))
            case ToolCallEvent(tool_calls):
                self.add_task*ToolTask(tool_calls)
