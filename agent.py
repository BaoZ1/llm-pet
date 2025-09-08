import json
from typing import Any, Literal, Never, Self, TypedDict, Annotated, ClassVar, Callable
from collections.abc import AsyncGenerator, Coroutine
from abc import ABC, abstractmethod
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool, BaseTool
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    AIMessageChunk,
)
from langgraph.prebuilt import InjectedState, create_react_agent, ToolNode
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import operator
import asyncio
from dataclasses import dataclass
import random
import datetime
import time
import math

LANGUAGE = "zh-CN"

with (
    open(f"prompts/{LANGUAGE}/decide_model.md", encoding="utf-8") as decide_file,
    open(f"prompts/{LANGUAGE}/instruction.md", encoding="utf-8") as instruction_file,
    open(f"prompts/{LANGUAGE}/messages.json", encoding="utf-8") as messages_file,
):
    TRANSLATED_TEXTS = {
        "decide": decide_file.read(),
        "prompt": instruction_file.read(),
        "messages": json.load(messages_file),
    }


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

    def _get_request_payload(self, input_, *, stop = None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage) and last_msg.additional_kwargs.get("partial", False):
                payload["messages"][-1]["partial"] = True
        return payload


class PetState(TypedDict):
    mood: int
    health: int
    hunger: int
    position: tuple[int, int]


PET_STATE_RANGE = {
    "mood": (-100, 100),
    "health": (0, 100),
    "hunger": (0, 100),
}
PET_STATE_DESC_MAPPER = {
    "mood": {
        90: "very happy",
        60: "happy",
        40: "good",
        10: "fair",
        -20: "bad",
        -40: "very bad",
        -70: "terrible",
        -999: "despair",
    },
    "health": {
        90: "healthy",
        80: "slightly uncomfortable",
        70: "uncomfortable",
        60: "ill",
        40: "critically ill",
        -999: "dying",
    },
    "hunger": {
        90: "stuffed",
        80: "full",
        70: "satisfied",
        60: "slightly hungry",
        50: "hungry",
        30: "very hungry",
        -999: "starving",
    },
}


def pet_state_desc(state: PetState, state_name: str):
    value = state[state_name]
    for lb, desc in PET_STATE_DESC_MAPPER[state_name].items():
        if value > lb:
            return desc


def pet_state_modify_check(state_name: str, value: int):
    return min(
        max(
            value,
            PET_STATE_RANGE[state_name][0],
        ),
        PET_STATE_RANGE[state_name][1],
    )


def clearable_add(a: list, b: list | None):
    if b is None:
        return []
    return a + b


class State(TypedDict):
    presistent_messages: Annotated[list[BaseMessage], operator.add]
    input_messages: Annotated[list[BaseMessage], clearable_add]
    new_messages: Annotated[list[BaseMessage], clearable_add]
    decide_result: dict
    pet: PetState


class Event(ABC):
    tags: ClassVar[list[str]] = []
    msg_prefix: str | None = "Event"

    @property
    def name(self):
        return self.__class__.__name__

    def trigger(self, state: PetState) -> str | None:
        return None


class Task(ABC):
    @property
    def name(self):
        return self.__class__.__name__

    @abstractmethod
    async def execute(self, manager: "TaskManager", state: PetState) -> None:
        return NotImplemented

    def execute_info(self) -> str | None:
        return None

    def merge(self, old_task: Self | None) -> tuple[Self | None, str | None] | Never:
        if old_task is None:
            return self, None
        raise

    def on_event(self, event: Event):
        return


class TaskManager:
    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.tasks: dict[str, tuple[Task, asyncio.Task]] = {}
        self.event_callbacks: dict[str, Callable[[Event], None]] = {}

    async def task_wrapper(self, task: Task):
        try:
            await task.execute(self, self.agent.state["pet"])
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
        return msg

    def register_callback(self, key: str, cb: Callable[[Event], None]):
        assert key not in self.event_callbacks
        self.event_callbacks[key] = cb

    def remove_callback(self, key: str):
        self.event_callbacks.pop(key, None)

    def trigger_event(self, event: Event):
        if msg := event.trigger(self.agent.state["pet"]):
            if event.msg_prefix:
                msg = f"[{event.msg_prefix}] {msg}"
            self.agent.push_message(msg)
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


class ModifyPetStateEvent(Event):

    def __init__(self, name: str, delta: int):
        super().__init__()

        self.state_name = name
        self.delta = delta

    def trigger(self, state):
        prev_desc = pet_state_desc(state, self.state_name)
        state[self.state_name] = pet_state_modify_check(
            state[self.state_name] + self.delta
        )
        new_desc = pet_state_desc(state, self.state_name)

        if self.delta > 0:
            base_text = f'Your "{self.state_name}" state value increased.'
        elif self.delta < 0:
            base_text = f'Your "{self.state_name}" state value decreased.'
        else:
            raise

        if new_desc != prev_desc:
            extra_text = f'Your "{self.state_name}" state changes from "{prev_desc}" to "{new_desc}"'
        else:
            extra_text = ""

        return base_text + extra_text


class DigestTask(Task):
    check_interval: int = 3

    def __init__(
        self,
        interval: tuple[int, int],
        reduce_range: tuple[int, int],
    ):
        self.interval = interval
        self.reduce_range = reduce_range
        self.remain_time: int

    async def execute(self, manager, state):
        while True:
            self.remain_time = random.randint(*self.interval)
            while self.remain_time > 0:
                await asyncio.sleep(self.check_interval)
                self.remain_time -= self.check_interval
            reduce_amount = random.randint(*self.reduce_range)
            manager.trigger_event(ModifyPetStateEvent("hunger", -reduce_amount))


class UserInputEvent(Event):
    tags = ["user"]
    msg_prefix = "User Input"

    def __init__(self, content: str):
        super().__init__()

        self.content = content

    def trigger(self, state):
        return self.content


class StdinReadTask(Task):

    async def execute(self, manager, state):
        loop = asyncio.get_running_loop()
        while True:
            user_input = await loop.run_in_executor(None, input)
            manager.trigger_event(UserInputEvent(user_input))


class Agent:

    def __init__(self, tools: list[BaseTool], screen_size: tuple[int, int]):
        self.state = State(
            presistent_messages=[],
            input_messages=[],
            new_messages=[],
            pet=PetState(
                mood=50,
                health=98,
                hunger=90,
                position=(0, 0),
            ),
        )
        self.screen_size = screen_size

        self.task_manager = TaskManager(self)

        self.decide_model_prompt = SystemMessage(TRANSLATED_TEXTS["decide"])
        self.decide_model = ChatCustom(
            base_url="https://api.moonshot.cn/v1",
            api_key=open("test_moonshot_key.txt").read(),
            model="kimi-k2-turbo-preview",
            temperature=0.6,
            frequency_penalty=1.0,
        ).bind_tools(tools)
        self.model = ChatCustom(
            base_url="https://api.moonshot.cn/v1",
            api_key=open("test_moonshot_key.txt").read(),
            model="kimi-k2-turbo-preview",
            temperature=0.6,
            frequency_penalty=1.0,
        ).bind_tools(tools)
        # self.model = ChatQwen(
        #     base_url="http://127.0.0.1:8900/v1",
        #     api_key="empty",
        #     model="Qwen3",
        #     temperature=0.6,
        #     frequency_penalty=1.0,
        # ).bind_tools(tools)
        # self.model = ChatDeepSeek(
        #     api_base="http://127.0.0.1:8900/v1",
        #     api_key="EMPTY",
        #     model="deepseek-chat",
        #     reasoning_effort="minimal",
        # )
        self.graph = self.create_graph(tools)

        self.prompt_msg = SystemMessage(TRANSLATED_TEXTS["prompt"])

        self.message_queue = asyncio.Queue()

    async def preprocess(self, state: State):
        raw_messages = []
        while not self.message_queue.empty():
            raw_messages.append(self.message_queue.get_nowait())

        state_info = f"[Info] Current State:\n"
        for name in PET_STATE_DESC_MAPPER.keys():
            state_info += f"- **{name}**: {pet_state_desc(state['pet'], name)}\n"
        raw_messages.append(state_info)

        environment_info = f"[Info] Environment:\n"
        environment_info += f"- Screen size: {self.screen_size}; Your position: {state["pet"]['position']}\n"
        environment_info += f"- Time: {datetime.datetime.now()}"
        raw_messages.append(environment_info)

        task_infos = self.task_manager.task_execute_infos()
        aggregate_task_info = "[Info] Running Tasks:\n"
        if len(task_infos):
            for info_line in task_infos:
                aggregate_task_info += f"- {info_line}\n"
        else:
            aggregate_task_info += "None"
        raw_messages.append(aggregate_task_info)

        return {
            "input_messages": [HumanMessage("\n\n".join(raw_messages))],
        }

    async def decide(self, state: State):
        PREFIX = '{"content":"'

        input_msgs = (
            [self.decide_model_prompt]
            + state["presistent_messages"]
            + state["input_messages"]
            + state["new_messages"]
            + [
                AIMessage(
                    PREFIX,
                    additional_kwargs={"partial": True},
                )
            ]
        )

        msg = await self.model.ainvoke(input_msgs, seed=time.time_ns())

        return {"decide_result": json.loads(PREFIX + msg.content)}

    async def process_text(self, state: State):
        print(state["decide_result"]["content"])
        return {"new_messages": [AIMessage(state["decide_result"]["content"])]}

    async def tool_check(self, state: State):
        if state["decide_result"]["tool"]:
            return True
        return False

    async def tool_prepare(self, state: State):
        return {
            "new_messages": [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            **state["decide_result"]["tool"],
                            "id": f"tool_call_{datetime.datetime.now()}",
                        }
                    ],
                )
            ]
        }

    async def tool_artifact_process(self, state: State):
        msg: ToolMessage = state["new_messages"][-1]
        if msg.artifact:
            if isinstance(msg.artifact, Task):
                if msg := await self.task_manager.add_task(msg.artifact):
                    return {"new_messages": [HumanMessage(f"[Event] {msg}")]}
        return {}

    async def postprocess(self, state: State):
        new_presistent_messages = []
        for msg_item in state["input_messages"][0].content.split("\n\n"):
            if msg_item.startswith("[User Input]"):
                new_presistent_messages.append(HumanMessage(msg_item))
            # if isinstance(msg, HumanMessage) and not msg.content.startswith("[Info]"):
            #     new_presistent_messages.append(msg)
        for msg in state["new_messages"]:
            if isinstance(msg, (AIMessage, ToolMessage)):
                new_presistent_messages.append(msg)
        return {
            "presistent_messages": new_presistent_messages,
            "input_messages": None,
            "new_messages": None,
        }

    def create_graph(self, tools: list[BaseTool]):
        builder = StateGraph(State)

        builder.add_node("preprocess", self.preprocess)
        builder.add_node("decide", self.decide)
        builder.add_node("text", self.process_text)
        builder.add_node("tool_prepare", self.tool_prepare)
        builder.add_node("tools", ToolNode(tools, messages_key="new_messages"))
        builder.add_node("tool_artifact_process", self.tool_artifact_process)
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "decide")
        builder.add_edge("decide", "text")
        builder.add_conditional_edges(
            "text",
            self.tool_check,
            {True: "tool_prepare", False: "postprocess"},
        )
        builder.add_edge("tool_prepare", "tools")
        builder.add_edge("tools", "tool_artifact_process")
        builder.add_edge("tool_artifact_process", "decide")
        builder.add_edge("postprocess", END)

        return builder.compile()

    def push_message(self, message: BaseMessage):
        self.message_queue.put_nowait(message)

    async def run(self):
        try:
            while True:
                while self.message_queue.empty():
                    await asyncio.sleep(0.5)
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


@tool
def modify_mood(delta: int, pet_state: Annotated[PetState, InjectedState("pet")]):
    """Modify your "mood" state according to your interaction with the user

    Args:
        delta: The amount your "mood" state changes. Positive value for better mood and negative for worse.
            The corresponding relationship between the absolute value of the change amount and the mood change is as follows:
            - (0~3): Slight fluctuations
            - (3~10): Significant swings
            - (10~20): Obviously ups and downs
            - (20~50): Severe physical or mental shock
            - Higher values are forbidden.
    """
    print(f"Mood changed: {delta}")
    pet_state["mood"] = pet_state_modify_check("mood", pet_state["mood"] + delta)
    return (
        f'Successfully modified mood state. Your mood state are "{pet_state_desc(pet_state, "mood")}" now.',
    )


class MoveEvent(Event):
    def __init__(self, new_pos: tuple[int, int], finished: bool):
        self.new_pos = new_pos
        self.finished = finished

    def trigger(self, state):
        state["position"] = self.new_pos
        if self.finished:
            return f"You've arrived at {self.new_pos}"


class MoveTask(Task):
    speed = {
        "walk": 50,
        "run": 120,
    }
    interval = 0.02

    def __init__(self, target: tuple[int, int], action: Literal["walk", "run"]):
        self.target = target
        self.action = action
        self.init_pos: tuple[int, int] = None
        self.progress: tuple[int, int] = None

        self.running = True

    def merge(self, old_task):
        return self, f"Start moving towards {self.target}"

    def on_event(self, event):
        if isinstance(event, DragEvent):
            self.running = False

    async def execute(self, manager, state):
        init_pos = self.init_pos = state["position"]
        dx = self.target[0] - init_pos[0]
        dy = self.target[1] - init_pos[1]
        distance = math.hypot(dx, dy)
        step_count = round(distance / self.speed[self.action] / self.interval)
        self.progress = (step_count + 1, 0)
        for i in range(1, step_count):
            if not self.running:
                return
            new_pos = (
                int(init_pos[0] + dx * i / step_count),
                int(init_pos[1] + dy * i / step_count),
            )
            manager.trigger_event(MoveEvent(new_pos, False))
            await asyncio.sleep(self.interval)
            self.progress = (step_count, i)
        manager.trigger_event(MoveEvent(new_pos, True))
        self.progress = (step_count, step_count)

    def execute_info(self):
        return f"Move: from {self.init_pos} to {self.target}; Progress: {round(self.progress[1] / self.progress[0] * 100)}%"


@tool(response_format="content_and_artifact")
def move(target: tuple[int, int], action: Literal["walk", "run"]):
    """Move to the specified position on the screen

    Args:
        target: The position you want to move to. It can't exceed the screen.
        action: Describe how you go there.
    """
    return f'Trying start move to {target} with action "{action}"', MoveTask(
        target, action
    )


class DragEvent(MoveEvent):
    tags = ["user"]

    def __init__(
        self, pos: tuple[int, int], mode: Literal["begin", "end"] | None = None
    ):
        super().__init__(pos, False)
        self.pos = pos
        self.mode = mode

    def trigger(self, state):
        state["position"] = self.pos
        if self.mode == "begin":
            return "You are being dragged up by the user!"
        elif self.mode == "end":
            return "You are put down by the user!"


class DragTask(Task):
    check_interval = 0.02

    def __init__(self):
        self.running = True

    async def execute(self, manager, state):
        while self.running:
            await asyncio.sleep(self.check_interval)

    def execute_info(self):
        return "You are being dragged by the user"

    def on_event(self, event):
        if isinstance(event, DragEvent) and event.mode == "end":
            self.running = False


class PlainEvent(Event):
    def __init__(self, content: str):
        self.content = content

    def trigger(self, state):
        return self.content


class IdleTask(Task):
    check_interval = 1

    def __init__(self, interval: tuple[int, int]):
        self.interval = interval
        self.rest_time: int

    async def execute(self, manager, state):
        while True:
            self.rest_time = random.randint(*self.interval)
            while self.rest_time > 0:
                await asyncio.sleep(self.check_interval)
                self.rest_time -= self.check_interval
            manager.trigger_event(PlainEvent("You feel a bit bored..."))

    def on_event(self, event):
        if "user" in event.tags:
            self.rest_time = random.randint(*self.interval)


class GreetingTask(Task):
    async def execute(self, manager, state):
        manager.trigger_event(
            PlainEvent("You've just been awakened, how about saying hello to the user?")
        )


class RandomMoveEmitTask(Task):
    check_interval = 1

    def __init__(self, interval: tuple[int, int], dist_range: tuple[float, float]):
        self.interval = interval
        self.dist_range = dist_range

        self.remained_time: float

    async def execute(self, manager, state):
        while True:
            self.remained_time = random.randint(*self.interval)
            while self.remained_time > 0:
                await asyncio.sleep(self.check_interval)
                self.remained_time -= self.check_interval

            current_pos = state["position"]
            screen_size = manager.agent.screen_size
            direction = random.random() * math.pi * 2
            dist = random.randint(*self.dist_range)
            target_pos = (
                min(
                    max(0, current_pos[0] + dist * math.cos(direction)),
                    screen_size[0],
                ),
                min(
                    max(0, current_pos[1] + dist * math.sin(direction)),
                    screen_size[1],
                ),
            )

            await manager.add_task(MoveTask(target_pos, "walk"))

    def on_event(self, event):
        if isinstance(event, MoveEvent):
            self.remained_time = random.randint(*self.interval)
