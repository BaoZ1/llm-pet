from framework.plugin import BasePlugin, PluginManager
from framework.worker import ThreadedWorker
from framework.config import BaseConfig
from framework.event import (
    PluginRefreshEvent,
    Event,
    TaskManager,
    InvokeStartEvent,
    InvokeEndEvent,
)

from enum import Enum, auto
from typing import ClassVar, TypedDict, Annotated, Sequence, cast
from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
import operator
import asyncio
from dataclasses import dataclass, field


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


class TypedMessage(HumanMessage):
    name: ClassVar[str]

    def __init__(self, content: str | list[dict]):
        if isinstance(content, str):
            super().__init__(f"=====<{self.name}>=====\n{content}")
        else:
            for d in content:
                if d["type"] == "text":
                    d["text"] = f"=====<{self.name}>=====\n{d["text"]}"
            super().__init__(content)


class UserMessage(TypedMessage):
    name = "UserInput"


class EventMessage(TypedMessage):
    name = "Event"


class InfoMessage(TypedMessage):
    name = "Info"

    def __init__(self):
        info_parts: list[str] = []

        info_parts.extend(PluginManager.infos())

        task_infos = TaskManager.task_execute_infos()
        if task_infos:
            info_parts.append(task_infos)

        info_msg = "\n\n".join(info_parts).strip()
        if len(info_msg) == 0:
            info_msg = "No Information now."
        super().__init__(info_msg)


class RevisionMessage(TypedMessage):
    name = "Revision"


@dataclass
class UserInputEvent(Event):
    tags = ["user"]
    text: str = ""
    images: list[str] = field(default_factory=list)

    def agent_msg(self):
        parts = []
        for image in self.images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image,
                    },
                }
            )
        if self.text:
            parts.append(
                {
                    "type": "text",
                    "text": self.text,
                }
            )
        return UserMessage(parts)


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
        ret = self.buffer.strip()
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
    def __init__(self, base_model: ChatOpenAI, enable_revision: bool):
        self.state = State(
            presistent_messages=[],
            input_messages=[],
            revision_data=[],
        )

        self.base_model = base_model
        self.enable_revision = enable_revision

        self.message_queue = asyncio.Queue()

        self.parser = StreamMarkerParser("[:", ":]")

        self.task: asyncio.Task = None

    def start(self, system_prompt: str, tools: Sequence[BaseTool]):
        if self.task is not None:
            self.stop()

        self.model = self.base_model.bind_tools(tools)

        self.decide_model_prompt = SystemMessage(system_prompt)
        self.revision_prompt = SystemMessage(
            f"""请仔细分析所给的对话上下文和AI助手的回复（均进行了适当简化），从多个维度进行评估。
        评估内容包括但不限于：
        - 是否提及了相关行为而未调用对应工具？
        - 是否包含了复杂格式，而不是一段足够简洁的文字？
        - 是否脱离了其提示词中的行为规范？
        
        若回复内容恰当，直接返回`true`，若存在问题，返回具体的修改意见。
        
        AI助手提示词如下：
        {system_prompt}
        """
        )
        self.graph = self.create_graph(tools)

        def create_task():
            self.task = ThreadedWorker.loop.create_task(self.run())

        ThreadedWorker.loop.call_soon_threadsafe(create_task)

    def stop(self):
        if self.task is not None:
            self.task.cancel()
            self.task = None

    async def preprocess(self, state: State):
        TaskManager.trigger_event(InvokeStartEvent())

        messages: list[HumanMessage] = []
        while not self.message_queue.empty():
            new_msg = self.message_queue.get_nowait()
            messages.append(new_msg)
            # print(f"[add message] {type(new_msg)} {new_msg}\n")

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
        try:
            msg = await self.model.ainvoke(input_msgs)
        except Exception as e:
            print(e)
            print(input_msgs)

        print(f"[invoke] {msg}\n")

        return {"response": msg}

    async def revision(self, state: State):
        if not self.enable_revision:
            return Command(
                goto="pass_revision",
                update={
                    "revision_data": [],
                    "new_messages": [state["response"]],
                },
            )

        input_msgs = [self.revision_prompt]
        msgs = []
        for msg in self.concat_msgs(
            state["input_messages"] + state["new_messages"] + [state["response"]]
        ):
            if isinstance(msg, AIMessage):
                content_parts = ["[AI] " + msg.content.strip()]
                for tc in msg.tool_calls:
                    content_parts.append(f"\n<tool call: {tc['name']}>")
                content = "\n".join(content_parts)
            elif isinstance(msg, HumanMessage):
                content_parts = []
                for c in msg.content:
                    if c["type"] == "text":
                        content_parts.append(c["text"])
                    elif c["type"] == "image_url":
                        content_parts.append("<image>")
                content = "\n".join(content_parts)
            elif isinstance(msg, ToolMessage):
                content = "[Tool] ..."
            msgs.append(content)
        input_msgs.append(HumanMessage("\n\n".join(msgs)))

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

    async def process_artifact(self, state: State):
        msg = state["new_messages"][-1]
        assert isinstance(msg, ToolMessage)

        if msg.artifact:
            if isinstance(msg.artifact, HumanMessage):
                return {"new_messages": [msg.artifact]}

    async def postprocess(self, state: State):
        # new_presistent_messages = [
        #     msg for msg in state["input_messages"] if isinstance(msg, UserMessage)
        # ] + state["new_messages"]
        new_presistent_messages = state["input_messages"] + state["new_messages"]

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
        builder.add_node("artifact", self.process_artifact)
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "info")
        builder.add_edge("info", "decide")
        builder.add_edge("decide", "revision")
        builder.add_conditional_edges(
            "pass_revision", self.tool_check, {True: "tools", False: "postprocess"}
        )
        builder.add_edge("tools", "artifact")
        builder.add_edge("artifact", "info")
        builder.add_edge("postprocess", END)

        return builder.compile()

    @staticmethod
    def concat_msgs(msgs: Sequence[BaseMessage]):
        concated_msgs: list[BaseMessage] = []
        last_human_msg_data: list[dict] = []
        for msg in msgs:
            if isinstance(msg, HumanMessage):
                if isinstance(msg.content, str):
                    last_human_msg_data.append({"type": "text", "text": msg.content})
                elif isinstance(msg.content, list):
                    last_human_msg_data.extend(msg.content)
                else:
                    raise Exception("unknown content type:", type(msg.content))
            else:
                if len(last_human_msg_data) != 0:
                    concated_msgs.append(HumanMessage(last_human_msg_data))
                    last_human_msg_data.clear()
                concated_msgs.append(msg)
        if len(last_human_msg_data) != 0:
            concated_msgs.append(HumanMessage(last_human_msg_data))
        return concated_msgs

    async def run(self):
        try:
            while True:
                while self.message_queue.empty():
                    await asyncio.sleep(0.5)
                self.state = await self.graph.ainvoke(self.state, stream_mode="values")
        except asyncio.CancelledError:
            pass


@dataclass
class Config(BaseConfig):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.6
    frequency_penalty: float = 1.0
    extra_config: dict[str, str | bool | int | float] | None = None
    enable_revision: bool = False


class Plugin(BasePlugin):
    def init(self):
        config = cast(Config, self.get_config())
        self.agent = Agent(
            ChatOpenAI(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
                temperature=config.temperature,
                frequency_penalty=config.frequency_penalty,
                extra_body=config.extra_config,
            ),
            config.enable_revision,
        )

    def clear(self):
        self.agent.stop()

    def on_event(self, e):
        msgs = e.agent_msg()
        if msgs:
            if isinstance(msgs, HumanMessage):
                msgs = [msgs]
            for msg in msgs:
                # print(f"[put message] {type(e)} {type(msg)} {msg}\n")
                self.agent.message_queue.put_nowait(msg)

        match e:
            case PluginRefreshEvent(prompt, tools):
                open(
                    self.root_dir() / "test_last_prompt.md",
                    "w",
                    encoding="utf-8",
                ).write(prompt)
                self.agent.start(prompt, tools)
