from __future__ import annotations
from enum import Enum, auto
import json
from typing import Any, TypedDict, Annotated, ClassVar, Sequence, cast
from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    AIMessageChunk,
    ToolMessage,
)
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
import operator
import asyncio
from dataclasses import dataclass
from .event import (
    PluginRefreshEvent,
    Event,
    TaskManager,
    InvokeStartEvent,
    InvokeEndEvent,
    PluginFieldEvent,
)
from .plugin import PluginManager
from .worker import ThreadedWorker
from .config import GlobalConfig


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
    new_messages: Annotated[list[BaseMessage], clearable_add]
    info_message: HumanMessage
    response: AIMessage
    revision_data: list[BaseMessage]


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
            info_parts.append(task_infos)

        info_msg = "\n\n".join(info_parts).strip()
        if len(info_msg) == 0:
            info_msg = "No Information now."
        super().__init__(f"[Info] {info_msg}")


class RevisionMessage(HumanMessage):
    def __init__(self, content: str):
        super().__init__(f"[Revision] 据此修改意见重新回复：\n{content}")


@dataclass
class UserInputEvent(Event):
    tags = ["user"]
    content: str

    def agent_msg(self):
        return UserMessage(self.content)


@dataclass
class SpeakEvent(Event):
    text: str
    msg_id: str


@dataclass
class MarkerEvent(Event):
    marker: str
    data: str


class StreamMarkerParser:
    class State(Enum):
        normal = auto()
        in_start = auto()
        in_marker = auto()
        in_end = auto()

    def __init__(
        self,
        start_marker: str,
        end_marker: str,
    ):
        self.start_marker = start_marker
        self.end_marker = end_marker

        self.buffer = ""
        self.marker_buffer = ""
        self.state = self.State.normal
        self.start_match_index = 0
        self.end_match_index = 0

    def process(self, s: str):
        for c in s:
            match self.state:
                case self.State.normal:
                    self.normal_process(c)
                case self.State.in_start:
                    self.in_start_process(c)
                case self.State.in_marker:
                    self.in_marker_process(c)
                case self.State.in_end:
                    self.in_end_process(c)
        ret = self.buffer
        self.buffer = ""
        return ret

    def normal_process(self, c: str):
        if c == self.start_marker[0]:
            self.state = self.State.in_start
            self.start_match_index = 1
        else:
            self.buffer += c

    def in_start_process(self, c: str):
        if c == self.start_marker[self.start_match_index]:
            self.start_match_index += 1
            if self.start_match_index == len(self.start_marker):
                self.state = self.State.in_marker
        else:
            self.buffer += self.start_marker[: self.start_match_index]
            self.buffer += c
            self.state = self.State.normal

    def in_marker_process(self, c: str):
        if c == self.end_marker[0]:
            self.end_match_index = 1
            self.state = self.State.in_end
        else:
            self.marker_buffer += c

    def in_end_process(self, c: str):
        if c == self.end_marker[self.end_match_index]:
            self.end_match_index += 1
            if self.end_match_index == len(self.end_marker):
                self.state = self.State.normal
                name, data = self.marker_buffer.split(":", 1)
                TaskManager.trigger_event(MarkerEvent(name, data))
                self.marker_buffer = ""
        else:
            self.marker_buffer += self.end_marker[: self.end_match_index]
            self.marker_buffer += c
            self.state = self.State.in_marker


class Agent:
    instance: ClassVar[Agent] | None = None

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        extra_config: dict | None,
        system_prompt: str,
        tools: Sequence[BaseTool],
    ):
        self.state = State(
            presistent_messages=[],
            input_messages=[],
            revision_data=[],
        )

        self.base_model = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=0.6,
            frequency_penalty=1.0,
            extra_body=extra_config,
        )
        self.model = self.base_model.bind_tools(tools)

        self.decide_model_prompt = SystemMessage(system_prompt)
        self.graph = self.create_graph(tools)

        self.message_queue = asyncio.Queue()

        self.parser = StreamMarkerParser("[:", ":]")

        self.task: asyncio.Task = None

    @classmethod
    def init(cls, system_prompt: str, tools: Sequence[BaseTool]):
        with open("test_concated_prompt.md", "w", encoding="utf-8") as f:
            f.write(system_prompt)
        if cls.instance is not None:
            if cls.instance.task is not None:
                cls.instance.task.cancel()
            ThreadedWorker.submit_task(TaskManager.remove_callback, "agent")
        model_cfg = GlobalConfig.get().main_model
        new_instance = Agent(
            model_cfg["base_url"],
            model_cfg["api_key"],
            model_cfg["model"],
            model_cfg["extra_config"],
            system_prompt,
            tools,
        )
        cls.instance = new_instance
        new_instance.task = ThreadedWorker.loop.create_task(new_instance.run())
        ThreadedWorker.submit_task(
            TaskManager.register_callback, "agent", new_instance.on_event
        )

    async def preprocess(self, state: State):
        TaskManager.trigger_event(InvokeStartEvent())

        messages: list[HumanMessage] = []
        while not self.message_queue.empty():
            new_msg = self.message_queue.get_nowait()
            messages.append(new_msg)
            print(f"[add message] {type(new_msg)} {new_msg}\n")

        return {
            "input_messages": messages,
        }

    async def set_info(self, state: State):
        return {
            "info_message": InfoMessage(),
        }

    async def decide(self, state: State):
        input_msgs = (
            [self.decide_model_prompt]
            + state["presistent_messages"]
            + self.concat_msgs(
                state["input_messages"]
                + state["new_messages"]
                + [state["info_message"]]
            )
            + state["revision_data"]
        )

        # msg = None
        # async for chunk in self.model.astream(input_msgs):
        #     text = self.parser.process(chunk.content)
        #     if text:
        #         TaskManager.trigger_event(SpeakEvent(text, chunk.id))

        #     if msg is None:
        #         msg = chunk
        #     else:
        #         msg += chunk
        msg = await self.model.ainvoke(input_msgs)

        print(f"[invoke] {msg}\n")

        return {"response": msg}

    async def revision(self, state: State):
        if not GlobalConfig.get().enable_revision:
            return Command(
                goto="pass_revision",
                update={
                    "revision_data": [],
                    "new_messages": [state["response"]],
                },
            )

        prompt = """请仔细分析所给的对话上下文和AI助手的回复（均进行了适当简化），从多个维度进行评估。
        评估内容包括但不限于：
        - 是否提及了相关行为而未调用特定工具？
        - 是否包含了复杂格式，而不是一段足够简洁的文字？
        - 是否缺少恰当的行为标记用于丰富输出效果？
        
        若回复内容恰当，直接返回`true`，若存在问题，返回具体的修改意见。
        """
        input_msgs = [SystemMessage(prompt)]
        msgs = []
        for msg in self.concat_msgs(
            state["input_messages"] + state["new_messages"] + [state["response"]]
        ):
            if isinstance(msg, AIMessage):
                content = "[AI] " + msg.content.strip()
                for tc in msg.tool_calls:
                    content += f"\n<tool call: {tc['name']}>"
            elif isinstance(msg, HumanMessage):
                content = msg.content
            elif isinstance(msg, ToolMessage):
                content = "[Tool] ..."
            msgs.append(content)
        input_msgs.append(HumanMessage("\n".join(msgs)))

        ret = await self.base_model.ainvoke(input_msgs)
        print(f"[revision] {ret}\n")

        if "true" in ret.content:
            return Command(
                goto="pass_revision",
                update={
                    "revision_data": [],
                    "new_messages": [state["response"]],
                },
            )
        else:
            return Command(
                goto="decide",
                update={
                    "revision_data": [
                        state["response"],
                        RevisionMessage(ret.content),
                    ],
                },
            )

    async def pass_revision(self, state: State):
        new_msg = state["new_messages"][-1]
        text = self.parser.process(new_msg.content)
        TaskManager.trigger_event(SpeakEvent(text, new_msg.id))

    async def tool_check(self, state: State):
        if cast(AIMessage, state["new_messages"][-1]).tool_calls:
            return True
        return False

    async def postprocess(self, state: State):
        new_presistent_messages = [
            msg for msg in state["input_messages"] if isinstance(msg, UserMessage)
        ] + state["new_messages"]

        TaskManager.trigger_event(
            InvokeEndEvent(
                state["input_messages"],
                state["new_messages"],
            )
        )

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
        builder.add_node("revision", self.revision)
        builder.add_node("pass_revision", self.pass_revision)
        builder.add_node("tools", ToolNode(tools, messages_key="new_messages"))
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "info")
        builder.add_edge("info", "decide")
        builder.add_edge("decide", "revision")
        builder.add_conditional_edges(
            "pass_revision", self.tool_check, {True: "tools", False: "postprocess"}
        )
        builder.add_edge("tools", "info")
        builder.add_edge("postprocess", END)

        return builder.compile()

    @staticmethod
    def concat_msgs(msgs: Sequence[BaseMessage]):
        concated_msgs: list[BaseMessage] = []
        last_msg_data: list[str] = []
        for msg in msgs:
            if isinstance(msg, HumanMessage):
                last_msg_data.append(msg.content)
            else:
                if len(last_msg_data) != 0:
                    concated_msgs.append(HumanMessage("\n\n".join(last_msg_data)))
                    last_msg_data.clear()
                concated_msgs.append(msg)
        if len(last_msg_data) != 0:
            concated_msgs.append(HumanMessage("\n\n".join(last_msg_data)))
        return concated_msgs

    def on_event(self, e: Event):
        msgs = e.agent_msg()
        if msgs:
            if isinstance(msgs, HumanMessage):
                msgs = [msgs]
            for msg in msgs:
                print(f"[put message] {type(e)} {type(msg)} {msg}\n")
                self.message_queue.put_nowait(msg)

    async def run(self):
        try:
            while True:
                while self.message_queue.empty():
                    await asyncio.sleep(0.5)

                # last_state = self.state
                # async for (mode, data) in self.graph.astream(self.state, stream_mode=["messages", "values"]):
                #     if mode == "messages":
                #         print(data)
                #     elif mode == "values":
                #         last_state = data
                #     else:
                #         raise Exception(mode)
                # self.state = last_state
                self.state = await self.graph.ainvoke(self.state, stream_mode="values")
        except asyncio.CancelledError:
            pass

    @classmethod
    def class_on_event(cls, e):
        if isinstance(e, PluginRefreshEvent):
            cls.init(e.sys_prompt, e.tools)
