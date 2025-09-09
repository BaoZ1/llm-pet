import json
import threading
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


class PetState(TypedDict):
    mood: int
    health: int
    hunger: int
    position: tuple[int, int]
    expression: str


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
    input_messages: Annotated[list[HumanMessage], clearable_add]
    info_message: HumanMessage
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
        raise NotImplementedError

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
        self.trigger_event(NewTaskEvent(old_task, new_task, msg))

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


class NewTaskEvent(Event):
    def __init__(self, old_task: Task | None, new_task: Task, message: str | None):
        super().__init__()

        self.old_task = old_task
        self.new_task = new_task
        self.message = message

    def trigger(self, state):
        return self.message


class ModifyPetStateEvent(Event):

    def __init__(self, name: str, delta: int):
        super().__init__()

        self.state_name = name
        self.delta = delta

    def trigger(self, state):
        prev_desc = pet_state_desc(state, self.state_name)
        state[self.state_name] = pet_state_modify_check(
            self.state_name, state[self.state_name] + self.delta
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

        # return base_text + extra_text
        if len(extra_text):
            return extra_text


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


class InvokeStartEvent(Event):
    pass


class InvokeEndEvent(Event):
    pass

class PetSpeakEvent(Event):
    def __init__(self, content: str):
        super().__init__()
        
        self.content = content

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
        self.model = ChatCustom(
            base_url="https://api.moonshot.cn/v1",
            api_key=open("test_moonshot_key.txt").read(),
            model="kimi-k2-turbo-preview",
            temperature=0.6,
            frequency_penalty=1.0,
        ).bind_tools(tools)

        self.graph = self.create_graph(tools)

        self.prompt_msg = SystemMessage(TRANSLATED_TEXTS["prompt"])

        self.message_queue = asyncio.Queue()

        self.running = False

    async def preprocess(self, state: State):
        self.task_manager.trigger_event(InvokeStartEvent())

        raw_messages: list[str] = ["Below are new messages"]
        while not self.message_queue.empty():
            raw_messages.append(self.message_queue.get_nowait())

        return {
            "input_messages": [HumanMessage(msg) for msg in raw_messages],
        }

    async def set_info(self, state: State):
        info_parts: list[str] = []

        state_info = f"[Info] Current State:\n"
        for name in PET_STATE_DESC_MAPPER.keys():
            state_info += f"- **{name}**: {pet_state_desc(state['pet'], name)}\n"
        info_parts.append(state_info)

        environment_info = f"[Info] Environment:\n"
        environment_info += f"- Screen size: {self.screen_size}; Your position: {state["pet"]['position']}\n"
        environment_info += f"- Time: {datetime.datetime.now()}"
        info_parts.append(environment_info)

        task_infos = self.task_manager.task_execute_infos()
        if task_infos:
            aggregate_task_info = "[Info] Running Tasks:\n"
            for task_info_line in task_infos:
                aggregate_task_info += f"- {task_info_line}\n"
            info_parts.append(aggregate_task_info)

        return {
            "info_message": HumanMessage("\n\n".join(info_parts)),
        }

    async def decide(self, state: State):
        PREFIX = '{"content":"'

        input_msgs = (
            [self.decide_model_prompt]
            + state["presistent_messages"]
            + state["input_messages"]
            + state["new_messages"]
            + [
                state["info_message"],
                AIMessage(
                    PREFIX,
                    additional_kwargs={"partial": True},
                ),
            ]
        )

        msg = await self.model.ainvoke(input_msgs, seed=time.time_ns())

        return {"decide_result": json.loads(PREFIX + msg.content)}

    async def response_fields_process(self, state: State):
        if state["decide_result"]["mood_delta"] != 0:
            self.task_manager.trigger_event(
                ModifyPetStateEvent("mood", state["decide_result"]["mood_delta"])
            )
        if state["decide_result"]["expression"]:
            self.task_manager.trigger_event(
                ExpressionSetEvent(
                    state["decide_result"]["expression"]["type"],
                    state["decide_result"]["expression"]["duration"],
                )
            )

        self.task_manager.trigger_event(
            PetSpeakEvent(state["decide_result"]["content"])
        )

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
                            "id": f"{state["decide_result"]["tool"]["name"]}_{datetime.datetime.now()}",
                        }
                    ],
                )
            ]
        }

    async def tool_artifact_process(self, state: State):
        msg: ToolMessage = state["new_messages"][-1]
        if msg.artifact:
            if isinstance(msg.artifact, Task):
                await self.task_manager.add_task(msg.artifact)
            elif isinstance(msg.artifact, Event):
                await self.task_manager.trigger_event(msg.artifact)
            if not self.message_queue.empty():
                return {"new_messages": [HumanMessage(self.message_queue.get_nowait())]}

    async def postprocess(self, state: State):
        new_presistent_messages = []
        for msg in state["input_messages"]:
            if msg.content.startswith("[User Input]"):
                new_presistent_messages.append(msg)
        for msg in state["new_messages"]:
            if isinstance(msg, (AIMessage, ToolMessage)):
                new_presistent_messages.append(msg)

        self.task_manager.trigger_event(InvokeEndEvent())

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
        builder.add_node("tools", ToolNode(tools, messages_key="new_messages"))
        builder.add_node("tool_artifact_process", self.tool_artifact_process)
        builder.add_node("postprocess", self.postprocess)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "info")
        builder.add_edge("info", "decide")
        builder.add_edge("decide", "fields_process")
        builder.add_conditional_edges(
            "fields_process",
            self.tool_check,
            {True: "tool_prepare", False: "postprocess"},
        )
        builder.add_edge("tool_prepare", "tools")
        builder.add_edge("tools", "tool_artifact_process")
        builder.add_edge("tool_artifact_process", "info")
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


class ExpressionSetEvent(Event):

    def __init__(self, expression_type: str, duration: str):
        super().__init__()

        self.expression_type = expression_type
        self.duration = duration


class ExpressionUpdateEvent(Event):
    def __init__(self, expression_type: str):
        super().__init__()

        self.expression_type = expression_type

    def trigger(self, state):
        state["expression"] = self.expression_type


class ExpressionManageTask(Task):
    def __init__(self):
        super().__init__()

        self.should_update = asyncio.Event()

        self.normal_expression: str = "normal"
        self.current_expression: tuple[str, str] | None = None

        self.state: PetState

        self.last_update_time = time.time()
        self.delay_task: asyncio.Task | None = None

    async def delay_to_normal(self, manager: TaskManager, expression):
        try:
            current_time = time.time()
            min_time = self.last_update_time + 3
            await asyncio.sleep(max(0, min_time - current_time))
            manager.trigger_event(ExpressionUpdateEvent(expression))
            self.last_update_time = time.time()
        except asyncio.CancelledError:
            pass

    async def execute(self, manager, state):
        self.state = state
        while True:
            await self.should_update.wait()
            if self.delay_task:
                self.delay_task.cancel()
                self.delay_task = None

            if self.current_expression:
                manager.trigger_event(ExpressionUpdateEvent(self.current_expression[0]))
                self.last_update_time = time.time()
            else:
                self.delay_task = asyncio.create_task(
                    self.delay_to_normal(manager, self.normal_expression)
                )
            self.should_update.clear()

    def refresh_normal_expression(self):
        old_expression = self.normal_expression
        if self.state["mood"] < -20:
            self.normal_expression = "sad"
        if old_expression != self.normal_expression:
            return True
        return False

    def on_event(self, event):
        if isinstance(event, ModifyPetStateEvent):
            if self.refresh_normal_expression():
                self.should_update.set()
        elif isinstance(event, ExpressionSetEvent):
            self.current_expression = (event.expression_type, event.duration)
            self.should_update.set()
        elif isinstance(event, InvokeStartEvent):
            if self.current_expression and self.current_expression[1] == "continuous":
                self.current_expression = None
                self.should_update.set()
        elif isinstance(event, InvokeEndEvent):
            if self.current_expression and self.current_expression[1] == "temporary":
                self.current_expression = None
                self.should_update.set()


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
    """Move smoothly to the specified position on the screen

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


class WanderTask(MoveTask):
    @property
    def name(self):
        return self.__class__.__base__.__name__

    def __init__(self, target):
        super().__init__(target, "walk")

    def merge(self, old_task):
        return self, f"Start wandering towards {self.target}"

    def execute_info(self):
        return f"Wander: from {self.init_pos} to {self.target}; Progress: {round(self.progress[1] / self.progress[0] * 100)}%"


class RandomWanderEmitTask(Task):
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

            await manager.add_task(WanderTask(target_pos))

    def on_event(self, event):
        if isinstance(event, (MoveEvent, UserInputEvent)):
            self.remained_time = random.randint(*self.interval)


class ThreadedAgent:

    def __init__(self, tools: list[BaseTool], init_tasks: list[Task]):
        self.agent = Agent(tools, (0, 0))
        self.init_tasks = init_tasks

        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop = None
        self._run_task: asyncio.Task = None

    def run(self, screen_size: tuple[int, int]):
        if self._thread and self._thread.is_alive():
            return

        self.agent.screen_size = screen_size

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_agent_loop,
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if not self._thread or not self._thread.is_alive():
            return

        self._stop_event.set()

        if self._loop and self._run_task:
            self._loop.call_soon_threadsafe(self._run_task.cancel)

        self._thread.join()

    def add_task(self, task: Task):

        if not self._loop or not self._thread or not self._thread.is_alive():
            print("Agent not running")
            return

        asyncio.run_coroutine_threadsafe(
            self.agent.task_manager.add_task(task), self._loop
        )

    @property
    def state(self):
        return self.agent.state["pet"]

    def register_task_callback(self, key: str, callback: Callable[[Event], None]):
        self.agent.task_manager.register_callback(key, callback)

    def trigger_event(self, event: Event):

        if not self._thread or not self._thread.is_alive():
            print("Agent not running")
            return

        self.agent.task_manager.trigger_event(event)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _run_agent_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._run_task = self._loop.create_task(self._run_agent())
            self._loop.run_until_complete(self._run_task)
        except:
            pass
        finally:
            if self._loop.is_running():
                self._loop.stop()

            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()

            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            self._loop.close()
            self._loop = None
            self._run_task = None

    async def _run_agent(self):
        await self.agent.task_manager.add_tasks_no_check(self.init_tasks)
        await self.agent.run()
