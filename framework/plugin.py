from __future__ import annotations
import importlib.util
import json
import pathlib
from langchain_core.tools import tool
from typing import (
    Any,
    ClassVar,
    Self,
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
from .worker import ThreadedWorker
from .event import Task, Event, TaskManager
from PySide6.QtWidgets import QWidget

@runtime_checkable
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
    _config: ClassVar[BaseConfig] = None

    @classmethod
    def root_dir(cls):
        return pathlib.Path(inspect.getmodule(cls).__file__).parent

    @classmethod
    def identifier(cls):
        return "/".join(cls.__module__.split(".")[1:-1])

    @classmethod
    def config_type(cls) -> type[BaseConfig]:
        if hasattr(sys.modules[cls.__module__], "Config"):
            return getattr(sys.modules[cls.__module__], "Config")
        return BaseConfig

    @classmethod
    def config_fields(cls) -> tuple[str, ...]:
        return cls.config_type().__match_args__

    @classmethod
    def load_config(cls):
        config_file = cls.root_dir() / "config.yaml"
        if config_file.exists():
            config_dict = yaml.load(config_file.read_text("utf-8"), yaml.Loader)
            cls._config = cls.config_type()(**config_dict)
        else:
            cls._config = cls.config_type()()
            cls.update_config(cls._config)

    @classmethod
    def get_config(cls):
        if cls._config is None:
            cls.load_config()
        return cls._config

    @classmethod
    def update_config(cls, config: BaseConfig):
        config_file = cls.root_dir() / "config.yaml"
        config_file.write_text(
            yaml.dump(config.__dict__, sort_keys=False), "utf-8"
        )
        cls._config = config

    def dep[T: BasePlugin | PluginProtocol](self, dep: type[T]) -> T:
        assert dep in self.deps
        d = PluginManager.get_loaded_plugins(dep)
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
        ThreadedWorker.submit_task(TaskManager.trigger_event, e)

    def add_task(self, t: Task):
        ThreadedWorker.submit_task(TaskManager.add_task, t)


class Tool:
    name: str
    with_artifect: ClassVar[bool] = False

    def __init__(self, plugin: BasePlugin):
        self.plugin = plugin

    def invoke(self, *args, **kwargs):
        pass

    def langchain_wrap(self):
        assert self.invoke.__doc__

        if self.with_artifect:
            return tool(self.name, response_format="content_and_artifact")(self.invoke)
        return tool(self.name)(self.invoke)


class PluginManager:
    plugin_classes: ClassVar[list[type[BasePlugin]]]
    plugins: ClassVar[list[BasePlugin]]

    @classmethod
    def init(cls):
        cls.load_all_plugin_classes()
        cls.plugins: list[BasePlugin] = []

    @classmethod
    def load_all_plugin_classes(cls):
        plugin_classes: list[type[BasePlugin]] = []
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
            plugin_classes.append(plugin_class)

        ordered_plugin_classes: list[type[BasePlugin]] = []
        missing_deps: list[type[BasePlugin]] = []
        for plugin_class in plugin_classes:
            if plugin_class.deps:
                missing_deps.append(plugin_class)
            else:
                ordered_plugin_classes.append(plugin_class)
        while missing_deps:
            new_mssing_deps: list[type[BasePlugin]] = []
            for plugin_class in missing_deps:
                for dep in plugin_class.deps:
                    if all(
                        filter(
                            lambda c: not issubclass(c, dep),
                            ordered_plugin_classes,
                        )
                    ):
                        new_mssing_deps.append(plugin_class)
                        break
                if plugin_class not in new_mssing_deps:
                    ordered_plugin_classes.append(plugin_class)
            if len(new_mssing_deps) == len(missing_deps):
                ordered_plugin_classes.extend(new_mssing_deps)
                break
            missing_deps = new_mssing_deps
        cls.plugin_classes = ordered_plugin_classes

    @classmethod
    def init_plugins(cls):
        for plugin_class in cls.plugin_classes:
            if plugin_class.get_config().enabled:
                deps = []
                for dep_class in plugin_class.deps:
                    loaded_deps = cls.get_loaded_plugins(dep_class)
                    if len(loaded_deps) == 0:
                        raise Exception(dep_class)
                    deps.append(loaded_deps[0])
                cls.plugins.append(plugin_class())
        tools = []
        for p in cls.plugins:
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
        for p in cls.plugins:
            str_prompts: dict[str, str] = {}
            for key, data in p.prompts().items():
                if isinstance(data, str):
                    str_prompts[key] = data
                elif isinstance(data, pathlib.Path):
                    str_prompts[key] = data.read_text("utf-8")
            plugin_prompt_comps.append(str_prompts)

        prompt_comps |= cls.merge_str_dict(plugin_prompt_comps)
        system_prompt = prompt_template.format_map(prompt_comps)

        return system_prompt, tools

    @staticmethod
    def merge_str_dict(
        dicts: Sequence[dict[Any, str]], sep: str = "\n"
    ) -> dict[Any, str]:
        result = {}
        for ps in dicts:
            for key, value in ps.items():
                if key in result:
                    result[key] += sep + value
                else:
                    result[key] = value
        return result

    @classmethod
    def infos(cls):
        raw_infos: dict[str, list[dict[str | None, Any]]] = {}
        for p in cls.plugins:
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

    @classmethod
    def get_plugin_classes(cls, type: type[BasePlugin] | type[PluginProtocol]):
        return list(
            filter(
                lambda p: issubclass(p, type),
                cls.plugin_classes,
            )
        )

    @classmethod
    def get_loaded_plugins(cls, type: type[BasePlugin] | type[PluginProtocol]):
        return list(
            filter(
                lambda p: isinstance(p, type),
                cls.plugins,
            )
        )

    @classmethod
    def on_event(cls, e: Event):
        for p in cls.plugins:
            p.on_event(e)
