from __future__ import annotations
import json
from typing import Any, TypedDict, Annotated, ClassVar, Sequence
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
    info_message: HumanMessage
    response: AIMessage
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
            info_parts.append(task_infos)

        info_msg = "\n\n".join(info_parts).strip()
        if len(info_msg) == 0:
            info_msg = "No Information now."
        super().__init__(f"[Info] {info_msg}")


@dataclass
class UserInputEvent(Event):
    tags = ["user"]
    content: str

    def agent_msg(self):
        return UserMessage(self.content)


@dataclass
class ToolCallEvent(Event):
    tool_calls: list[dict[str, Any]]


class Agent:
    instance: ClassVar[Agent] | None = None

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        tools: Sequence[BaseTool],
    ):
        self.state = State(
            presistent_messages=[],
            input_messages=[],
        )

        self.base_model = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=0.6,
            frequency_penalty=1.0,
        )
        self.model = self.base_model.bind_tools(tools)

        self.decide_model_prompt = SystemMessage(system_prompt)
        self.graph = self.create_graph(tools)

        self.message_queue = asyncio.Queue()
        self.should_invoke: bool = False

        self.task: asyncio.Task = None

    @classmethod
    def init(cls, system_prompt: str, tools: Sequence[BaseTool]):
        if cls.instance is not None:
            if cls.instance.task is not None:
                cls.instance.task.cancel()
            ThreadedWorker.submit_task(TaskManager.remove_callback, "agent")
        global_config = GlobalConfig.get()
        new_instance = Agent(
            global_config.base_url,
            global_config.api_key,
            global_config.model,
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

        # messages: list[HumanMessage] = [HumanMessage("[System] Below are new messages")]
        messages: list[HumanMessage] = []
        while not self.message_queue.empty():
            new_msg = self.message_queue.get_nowait()
            messages.append(new_msg)
            print(f"[add message] {type(new_msg)} {new_msg}\n")
        self.should_invoke = False

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
            + state["input_messages"]
            + [state["info_message"]]
        )

        msg: AIMessage = await self.model.ainvoke(input_msgs)
        print(f"[invoke] {msg}\n")

        return {"response": msg}

    async def check_response(self, state: State):
        msg = state["response"]
        if msg.content:
            try:
                decision = json.loads(msg.content)
            except json.JSONDecodeError:
                print("[[WRONG FORMAT]]\n")
                return Command(goto="fix_format", update={"response": msg})
            else:
                return Command(
                    goto="fields_process", update={"decide_result": decision}
                )
        elif tool_calls := msg.additional_kwargs.get("tool_calls", None):
            TaskManager.trigger_event(
                ToolCallEvent(
                    [
                        {"name": tc["name"], "args": tc["function"]["arguments"]}
                        for tc in tool_calls
                    ]
                )
            )
            return Command(goto="postprocess")
        else:
            raise Exception(f"[UNKNOWN MESSAGE ERROR] {msg}\n")

    async def fix_format(self, state: State):
        input_msgs = (
            [self.decide_model_prompt]
            + state["presistent_messages"]
            + state["input_messages"]
            + [state["info_message"]]
            + [
                HumanMessage(
                    f"[System] You just gave the response with wrong format: {state["response"].content}, try fix it"
                )
            ]
        )
        msg: AIMessage = await self.model.ainvoke(input_msgs)
        print(f"[fixed message] {msg}\n")

        return {"response": msg}

    async def response_fields_process(self, state: State):
        for k, v in state["decide_result"].items():
            TaskManager.trigger_event(PluginFieldEvent(k, v))

    async def postprocess(self, state: State):
        new_presistent_messages = []
        for msg in state["input_messages"]:
            if isinstance(msg, (UserMessage, AIMessage, ToolMessage)):
                new_presistent_messages.append(msg)

        TaskManager.trigger_event(InvokeEndEvent())

        return {
            "presistent_messages": new_presistent_messages,
            "input_messages": None,
        }

    def create_graph(self, tools: list[BaseTool]):
        builder = StateGraph(State)

        builder.add_node("preprocess", self.preprocess)
        builder.add_node("info", self.set_info)
        builder.add_node("decide", self.decide)
        builder.add_node("fix_format", self.fix_format)
        builder.add_node("check_response", self.check_response)
        builder.add_node("fields_process", self.response_fields_process)
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "info")
        builder.add_edge("info", "decide")
        builder.add_edge("decide", "check_response")
        builder.add_edge("fix_format", "check_response")
        builder.add_edge("fields_process", "postprocess")
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
            if isinstance(msgs, (str, BaseMessage)):
                msgs = [msgs]
            for msg in msgs:
                if isinstance(msg, str):
                    msg = EventMessage(msg)
                print(f"[put message] {type(e)} {type(msg)} {msg}\n")
                self.message_queue.put_nowait(msg)
                if not isinstance(msg, AIMessage):
                    self.should_invoke = True

    async def run(self):
        try:
            while True:
                while not self.should_invoke:
                    await asyncio.sleep(0.5)
                assert not self.message_queue.empty()

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
