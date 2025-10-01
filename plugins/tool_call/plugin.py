from dataclasses import dataclass
from framework.plugin import BasePlugin, PluginRefreshEvent
from framework.event import PluginFieldEvent, Event, Task, TaskManager, PlainEvent
from framework.agent import ToolCallEvent
import pathlib
from langchain_core.messages import ToolMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from datetime import datetime
from typing import Sequence


@dataclass
class ToolResultEvent(Event):
    tool_msgs: list[ToolMessage]

    def agent_msg(self):
        return self.tool_msgs


class ToolTask(Task):
    @property
    def name(self):
        return f"{self.__class__.__name__}-{self.time}"

    def __init__(self, tools: Sequence[BaseTool], params: list[dict]):
        self.time = datetime.now()
        self.params = [
            {**p, "id": f"{p["name"]}-{self.time}-{idx}", "type": "tool_call"}
            for idx, p in enumerate(params)
        ]
        self.tool_caller = ToolNode(tools)

    async def execute(self):
        TaskManager.trigger_event(PlainEvent(AIMessage("", tool_calls=self.params)))
        ret = await self.tool_caller.ainvoke(self.params)
        msgs: list[ToolMessage] = ret["messages"]
        # msgs = list(filter(lambda msg: msg.content != "null", msgs))
        TaskManager.trigger_event(ToolResultEvent(msgs))

    def execute_info(self):
        tool_descriptions = []
        for p in self.params:
            tool_descriptions.append(f"- {p["name"]}({p["args"]})")

        head = f"Processing Tools (Submitted at {self.time}):"
        return "\n".join([head] + tool_descriptions)


class Plugin(BasePlugin):
    def init(self):
        self.plugin_tools = []

    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("tools_field.md")}

    def on_event(self, e):
        match e:
            case PluginFieldEvent("tools", v):
                if len(v) != 0:
                    self.add_task(ToolTask(self.plugin_tools, v))
            case ToolCallEvent(tool_calls):
                self.add_task(ToolTask(tool_calls))
            case PluginRefreshEvent(_, tools):
                self.plugin_tools = tools
