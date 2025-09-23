from __future__ import annotations
import json
from typing import (
    Any,
    Never,
    Self,
    TypedDict,
    Annotated,
    ClassVar,
    Callable,
    Sequence,
    TYPE_CHECKING,
)
from abc import ABC, abstractmethod
from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable
from langchain_core.tools import tool, BaseTool
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    AIMessageChunk,
)
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
import operator
import asyncio
from dataclasses import dataclass
import datetime
import time
from .event import (
    Event,
    TaskManager,
    InvokeStartEvent,
    InvokeEndEvent,
    PluginFieldEvent,
)
from .plugin import PluginManager


class ChatCustom(ChatOpenAI):
    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        gc = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if len(choices) > 0:
            choice = choices[0]
            if delta := choice["delta"]:
                if rc := delta.get("reasoning_content"):
                    gc.message.additional_kwargs["reasoning_content"] = rc
        return gc

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage) and last_msg.additional_kwargs.get(
                "partial", False
            ):
                payload["messages"][-1]["partial"] = True
        return payload


def clearable_add(a: list, b: list | None):
    if b is None:
        return []
    return a + b


class State(TypedDict):
    presistent_messages: Annotated[list[BaseMessage], operator.add]
    input_messages: Annotated[list[HumanMessage], clearable_add]
    info_message: HumanMessage
    new_messages: Annotated[list[BaseMessage], clearable_add]
    decide_result: dict


class UserMessage(HumanMessage):
    def __init__(self, content: str):
        super().__init__(f"[User Input] {content}")


class EventMessage(HumanMessage):
    def __init__(self, content: str):
        super().__init__(f"[Event] {content}")


class InfoMessage(HumanMessage):
    def __init__(self):
        info_parts: list[str] = []

        info_parts.extend(PluginManager.infos())

        task_infos = TaskManager.task_execute_infos()
        if task_infos:
            aggregate_task_info = "[Info] Running Tasks:\n"
            for task_info_line in task_infos:
                aggregate_task_info += f"- {task_info_line}\n"
            info_parts.append(aggregate_task_info)

        info_msg = "\n\n".join(info_parts).strip()
        if len(info_msg) == 0:
            info_msg = "[Info] No Information now."
        super().__init__(f"[Info] {info_msg}")


class Agent:

    def __init__(self):
        self.state = State(
            presistent_messages=[],
            input_messages=[],
            new_messages=[],
        )

        self.base_model = ChatCustom(
            base_url="https://api.moonshot.cn/v1",
            api_key=open("test_moonshot_key.txt").read(),
            model="kimi-k2-turbo-preview",
            temperature=0.6,
            frequency_penalty=1.0,
        )
        self.model: Runnable
        self.decide_model_prompt: SystemMessage

        self.graph: CompiledStateGraph

        self.message_queue = asyncio.Queue()
        self.should_invoke: bool = False

    def init(self, system_prompt: str, tools: Sequence[BaseTool]):
        self.model = self.base_model.bind_tools(tools)
        self.decide_model_prompt = SystemMessage(system_prompt)
        self.graph = self.create_graph(tools)

    async def preprocess(self, state: State):
        TaskManager.trigger_event(InvokeStartEvent())

        messages: list[HumanMessage] = [HumanMessage("[System] Below are new messages")]
        while not self.message_queue.empty():
            messages.append(self.message_queue.get_nowait())
        self.should_invoke = False

        return {
            "input_messages": messages,
        }

    async def set_info(self, state: State):
        # info_parts: list[str] = []

        # info_parts.extend(PluginManager.infos())

        # task_infos = TaskManager.task_execute_infos()
        # if task_infos:
        #     aggregate_task_info = "[Info] Running Tasks:\n"
        #     for task_info_line in task_infos:
        #         aggregate_task_info += f"- {task_info_line}\n"
        #     info_parts.append(aggregate_task_info)

        # info_msg = "\n\n".join(info_parts).strip()
        # if len(info_msg) == 0:
        #     info_msg = "[Info] No Information now."

        return {
            "info_message": InfoMessage(),
        }

    async def decide(self, state: State):
        input_msgs = (
            [self.decide_model_prompt]
            + state["presistent_messages"]
            + state["input_messages"]
            + state["new_messages"]
            + [state["info_message"]]
        )

        msg: AIMessage = await self.model.ainvoke(input_msgs)

        return {"decide_result": json.loads(msg.content)}

    async def response_fields_process(self, state: State):
        for k, v in state["decide_result"].items():
            if k == "tools":
                continue
            TaskManager.trigger_event(PluginFieldEvent(k, v))


    async def tool_check(self, state: State):
        return len(state["decide_result"].get("tools", [])) > 0

    # async def get_tool_call_msg(self, state: State):
    #     tool_call_system = SystemMessage(
    #         "Call appropriate tools based on message history and final decision content."
    #     )
    #     input_msgs = (
    #         [tool_call_system]
    #         + state["presistent_messages"]
    #         + state["input_messages"]
    #         + state["new_messages"]
    #         + [state["info_message"]]
    #         + [
    #             AIMessage(json.dumps(state["decide_result"])),
    #             HumanMessage(
    #                 "You've just made such a decision, now you should call tools directly."
    #             ),
    #         ]
    #     )

    #     msg: AIMessage = await self.model.ainvoke(input_msgs)
    #     print(msg.invalid_tool_calls)
    #     assert msg.tool_calls
    #     return {"new_messages": [msg]}

    async def tool_prepare(self, state: State):
        return {
            "new_messages": [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            **tool_call,
                            "id": f"{tool_call["name"]}_{datetime.datetime.now()}",
                        }
                        for tool_call in state["decide_result"]["tools"]
                    ],
                )
            ]
        }

    # async def tool_artifact_process(self, state: State):
    #     msg: ToolMessage = state["new_messages"][-1]
    #     print(msg)
    #     if msg.artifact:
    #         if isinstance(msg.artifact, Task):
    #             await TaskManager.add_task(msg.artifact)
    #         elif isinstance(msg.artifact, Event):
    #             TaskManager.trigger_event(msg.artifact)
    #         if not self.message_queue.empty():
    #             return {"new_messages": [HumanMessage(self.message_queue.get_nowait())]}

    async def postprocess(self, state: State):
        new_presistent_messages = []
        for msg in state["input_messages"]:
            # if msg.content.startswith("[User Input]"):
            if isinstance(msg, UserMessage):
                new_presistent_messages.append(msg)
        for msg in state["new_messages"]:
            if isinstance(msg, (AIMessage, ToolMessage)):
                new_presistent_messages.append(msg)

        TaskManager.trigger_event(InvokeEndEvent())

        return {
            "presistent_messages": new_presistent_messages,
            "input_messages": None,
            "new_messages": None,
        }

    def create_graph(self, tools: list[BaseTool]):
        builder = StateGraph(State)

        builder.add_node("preprocess", self.preprocess)
        builder.add_node("info", self.set_info)
        builder.add_node("decide", self.decide)
        builder.add_node("fields_process", self.response_fields_process)
        builder.add_node("tool_prepare", self.tool_prepare)
        # builder.add_node("tool_call_msg", self.get_tool_call_msg)
        builder.add_node("tools", ToolNode(tools, messages_key="new_messages"))
        # builder.add_node("tool_artifact_process", self.tool_artifact_process)
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "info")
        builder.add_edge("info", "decide")
        builder.add_edge("decide", "fields_process")
        # builder.add_edge("fields_process", "postprocess")
        builder.add_conditional_edges(
            "fields_process",
            self.tool_check,
            {True: "tool_prepare", False: "postprocess"},
        )
        builder.add_edge("tool_prepare", "tools")
        # builder.add_edge("tool_call_msg", "tools")
        # builder.add_edge("tools", "info")
        builder.add_edge("tools", "info")
        # builder.add_edge("tool_artifact_process", "info")
        builder.add_edge("postprocess", END)

        return builder.compile()

    def on_event(self, e: Event):
        msg = e.agent_msg()
        if msg:
            if isinstance(msg, str):
                msg = EventMessage(msg)
            self.message_queue.put_nowait(msg)
            if not isinstance(msg, AIMessage):
                self.should_invoke = True

    async def run(self):
        try:
            while True:
                while not self.should_invoke:
                    await asyncio.sleep(0.5)
                assert not self.message_queue.empty()
                final_state = None
                thinking = False
                async for mode, data in self.graph.astream(
                    self.state, stream_mode=["messages", "values"]
                ):
                    match mode:
                        case "messages":
                            msg, _ = data
                            if isinstance(msg, AIMessageChunk):
                                if rc := msg.additional_kwargs.get("reasoning_content"):
                                    if not thinking:
                                        print("<think>")
                                        thinking = True
                                    print(rc, end="", flush=True)
                                if c := msg.content:
                                    if thinking:
                                        print("</think>")
                                        thinking = False
                                    print(c, end="", flush=True)
                            # elif not isinstance(msg, HumanMessage):
                            #     print(type(msg), msg)
                        case "values":
                            final_state = data
                print()
                self.state = final_state
        except asyncio.CancelledError:
            pass
