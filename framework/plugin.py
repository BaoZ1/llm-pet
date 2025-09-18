from __future__ import annotations
import importlib.util
import json
import pathlib
from langchain_core.tools import tool
from typing import (
    Any,
    ClassVar,
    Sequence,
    TYPE_CHECKING,
    Protocol,
    runtime_checkable,
    cast,
)
import sys
import inspect
import yaml
from .config import BaseConfig

if TYPE_CHECKING:
    from .agent import Task, Event, Agent, TaskManager
    from .worker import ThreadedWorker
    from PySide6.QtWidgets import QWidget


class PluginProtocol(Protocol):
    pass


@runtime_checkable
class DisplayPluginProtocol(PluginProtocol, Protocol):
    def widgets(self) -> list[QWidget]:
        raise


@runtime_checkable
class PetPluginProtocol(DisplayPluginProtocol, Protocol):
    def pet(self) -> QWidget:
        raise

    def widgets(self):
        return [self.pet()]


class BasePlugin:
    deps: ClassVar[Sequence[type[BasePlugin] | type[PluginProtocol]]] = []

    def __init__(self, manager: PluginManager):
        self.manager = manager
        self.config = self.read_config()

    @classmethod
    def root_dir(cls):
        return pathlib.Path(inspect.getmodule(cls).__file__).parent

    @classmethod
    def config_type(cls) -> type[BaseConfig]:
        if hasattr(sys.modules[cls.__module__], "Config"):
            return getattr(sys.modules[cls.__module__], "Config")
        return BaseConfig

    @classmethod
    def config_fields(cls) -> tuple[str, ...]:
        return cls.config_type().__match_args__

    @classmethod
    def read_config(cls):
        config_file = cls.root_dir() / "config.yaml"
        if config_file.exists():
            config_dict = yaml.load(config_file.read_text("utf-8"), yaml.Loader)
        else:
            config_dict = {}
        return cls.config_type()(**config_dict)

    def save_config(self):
        config_file = self.root_dir() / "config.yaml"
        config_file.write_text(
            yaml.dump(self.config.__dict__, sort_keys=False), "utf-8"
        )

    def dep[T: BasePlugin | PluginProtocol](self, dep: type[T]) -> T:
        assert dep in self.deps
        d = self.manager.get_plugins(dep)
        assert len(d) > 0
        return d[0]

    def init(self):
        pass

    def prompts(self) -> dict[str, str | pathlib.Path]:
        return {}

    def tools(self) -> list[type[Tool]]:
        return []

    def init_tasks(self) -> list[Task]:
        return []

    def infos(self) -> dict[str, dict[str | None, Any]]:
        return {}

    def on_event(self, e: Event):
        pass

    def trigger_event(self, e: Event):
        self.manager.worker.submit_task(self.manager.task_manager.trigger_event, e)

    def add_task(self, t: Task):
        self.manager.worker.submit_task(self.manager.task_manager.add_task, t)


class Tool:
    with_artifect: ClassVar[bool] = False

    def __init__(self, plugin: BasePlugin):
        self.plugin = plugin

    def invoke(self, *args, **kwargs):
        pass

    def langchain_wrap(self):
        assert self.invoke.__doc__

        if self.with_artifect:
            return tool(self.invoke, response_format="content_and_artifact")
        return tool(self.invoke)


class PluginManager:
    def __init__(
        self,
        agent: Agent,
        task_manager: TaskManager,
        worker: ThreadedWorker,
    ):
        self.agent = agent
        self.task_manager = task_manager
        self.worker = worker

    def init(self):
        plugin_class_list: list[type[BasePlugin]] = []
        plugins_root = pathlib.Path("plugins")
        for import_file in plugins_root.rglob("plugin.py"):
            module_name = str(
                import_file.absolute().relative_to(sys.path[0]).with_suffix("")
            ).replace("\\", ".")
            if module_name not in sys.modules:
                spec = importlib.util.spec_from_file_location(
                    module_name, str(import_file.absolute())
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules[module_name] = module
            else:
                module = sys.modules[module_name]
            plugin_class: type[BasePlugin] = getattr(module, "Plugin")
            if plugin_class.read_config().enabled:
                plugin_class_list.append(plugin_class)

        missing_deps: list[type[BasePlugin]] = []
        self.plugins: list[BasePlugin] = []
        for plugin_class in plugin_class_list:
            if plugin_class.deps:
                missing_deps.append(plugin_class)
            else:
                p = plugin_class(self)
                self.plugins.append(p)
        while missing_deps:
            new_mssing_deps: list[type[BasePlugin]] = []
            for plugin_class in missing_deps:
                for dep in plugin_class.deps:
                    if len(self.get_plugins(dep)) == 0:
                        new_mssing_deps.append(plugin_class)
                        break
                if plugin_class not in new_mssing_deps:
                    p = plugin_class(self)
                    self.plugins.append(p)
            if len(new_mssing_deps) == len(missing_deps):
                raise Exception([p.deps for p in new_mssing_deps])
            missing_deps = new_mssing_deps

        tools = []
        for p in self.plugins:
            p.init()
            tools.extend([f(p).langchain_wrap() for f in p.tools()])

        prompt_folder = pathlib.Path("prompts/zh-CN")
        with (
            (prompt_folder / "template.md").open(encoding="utf-8") as t_f,
            (prompt_folder / "default_slots.json").open(encoding="utf-8") as d_f,
        ):
            prompt_template = t_f.read()
            prompt_comps: dict[str, str] = json.load(d_f)

        plugin_prompt_comps: list[dict[str, str]] = []
        for p in self.plugins:
            str_prompts: dict[str, str] = {}
            for key, data in p.prompts().items():
                if isinstance(data, str):
                    str_prompts[key] = data
                elif isinstance(data, pathlib.Path):
                    str_prompts[key] = data.read_text("utf-8")
            plugin_prompt_comps.append(str_prompts)

        prompt_comps |= self.merge_str_dict(plugin_prompt_comps)
        system_prompt = prompt_template.format_map(prompt_comps)

        return system_prompt, tools

    def merge_str_dict(
        self, dicts: Sequence[dict[Any, str]], sep: str = "\n"
    ) -> dict[Any, str]:
        result = {}
        for ps in dicts:
            for key, value in ps.items():
                if key in result:
                    result[key] += sep + value
                else:
                    result[key] = value
        return result

    def infos(self):
        raw_infos: dict[str, list[dict[str | None, Any]]] = {}
        for p in self.plugins:
            for title, infos in p.infos().items():
                raw_infos.setdefault(title, []).append(infos)
        md_structured_infos: dict[str, str] = {}
        for title, group in raw_infos.items():
            info_lines: list[str] = []
            for d in group:
                if None in d.keys():
                    info_lines.append(str(d.pop(None)))
            names = sum(
                [list(d.keys()) for d in group],
                [],
            )
            assert len(names) == len(set(names))
            for d in group:
                for name, value in d.items():
                    info_lines.append(f"- **{name}**: {value}")
            md_structured_infos[title] = "\n".join(info_lines)
        formated_infos = [f"[Info] {k}\n{v}" for k, v in md_structured_infos.items()]
        return formated_infos

    def get_plugins(self, type: type[BasePlugin] | type[PluginProtocol]):
        return list(
            filter(
                lambda p: isinstance(p, type),
                self.plugins,
            )
        )

    def on_event(self, e: Event):
        for p in self.plugins:
            p.on_event(e)

    def base_model(self):
        return self.agent.base_model
