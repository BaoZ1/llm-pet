from __future__ import annotations
from dataclasses import dataclass
import importlib.util
import json
import pathlib
from langchain_core.tools import tool, BaseTool
from typing import Any, ClassVar, Literal, Self, Sequence
import sys
import inspect
import yaml
from .config import BaseConfig
from .worker import ThreadedWorker
from .event import Task, Event, TaskManager, PluginRefreshEvent


MARKER_BEGIN = "[:"
MARKER_END = ":]"


class PluginTypeBase:
    pass


@dataclass
class PluginReloadEvent(Event):
    plugin_class: type[BasePlugin]


@dataclass
class PluginConfigUpdateEvent(Event):
    plugin_class: type[BasePlugin]


class BasePlugin:
    deps: ClassVar[Sequence[type[BasePlugin] | type[PluginTypeBase]]] = []
    _config: ClassVar[BaseConfig | None] = None
    instance: ClassVar[Self | None] = None

    @classmethod
    def load(cls):
        reload = False
        if cls.instance is not None:
            cls.unload()
            reload = True
        cls.instance = cls()
        cls.instance.init()
        for dc in cls.deps:
            cls.instance.on_dep_load(cls.instance.dep(dc))
        if reload:
            cls.trigger_event(PluginReloadEvent(cls))
            print(f"{cls.__module__} reloaded")
        else:
            print(f"{cls.__module__} loaded")

    @classmethod
    def unload(cls):
        if cls.instance is None:
            return
        cls.instance.clear()
        cls.instance = None
        print(f"{cls.__module__} unloaded")

    @classmethod
    def root_dir(cls):
        return pathlib.Path(inspect.getmodule(cls).__file__).parent

    @classmethod
    def identifier(cls):
        return "/".join(cls.__module__.split(".")[1:-1])

    @classmethod
    def config_type(cls) -> type[BaseConfig]:
        for c in cls.mro():
            if issubclass(c, BasePlugin):
                if c is BasePlugin:
                    break
                if hasattr(sys.modules[c.__module__], "Config"):
                    return getattr(sys.modules[c.__module__], "Config")
        return BaseConfig

    @classmethod
    def load_config(cls):
        config_file = cls.root_dir() / "config.yaml"
        if config_file.exists():
            config_dict = yaml.load(config_file.read_text("utf-8"), yaml.Loader)
            cls._config = cls.config_type()(**config_dict)
        else:
            cls.update_config(cls.config_type()())
            cls.load_config()

    @classmethod
    def get_config(cls) -> BaseConfig:
        if cls._config is None:
            cls.load_config()
        return cls._config

    @classmethod
    def update_config(cls, config: BaseConfig):
        if config == cls._config:
            return
        config_file = cls.root_dir() / "config.yaml"
        config_file.write_text(yaml.dump(config.__dict__, sort_keys=False), "utf-8")
        cls._config = config
        cls.trigger_event(PluginConfigUpdateEvent(cls))

    def dep[T: BasePlugin | PluginTypeBase](self, dep: type[T]) -> T:
        assert dep in self.deps
        d = PluginManager.get_loaded_plugins(dep)
        assert len(d) > 0
        return d[0]

    def init(self):
        pass

    def clear(self):
        pass

    def on_dep_load(self, dep: BasePlugin | PluginTypeBase):
        pass

    def prompts(self) -> dict[str, str | pathlib.Path]:
        return {}

    def tools(self) -> list[type[Tool]]:
        return []

    def infos(self) -> dict[str, dict[str | None, Any]]:
        return {}

    def on_event(self, e: Event):
        pass

    @staticmethod
    def trigger_event(e: Event):
        ThreadedWorker.submit_task(TaskManager.trigger_event, e)

    @staticmethod
    def add_task(t: Task):
        ThreadedWorker.submit_task(TaskManager.add_task, t)


class Tool:
    response_format: ClassVar[Literal["content", "content_and_artifact"]] = "content"

    def __init__(self, plugin: BasePlugin):
        self.plugin = plugin

    def invoke(self, *args, **kwargs):
        pass

    def langchain_wrap(self):
        return tool(
            self.__class__.__name__,
            response_format=self.response_format,
        )(self.invoke)


class PluginManager:
    plugin_classes: ClassVar[list[type[BasePlugin]]]

    @classmethod
    def init(cls):
        cls.load_all_plugin_classes()

        for plugin_class in cls.plugin_classes:
            if plugin_class.get_config().enabled:
                if cls.check_deps(plugin_class):
                    plugin_class.load()
        cls.refresh_agent_data()

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
    def loaded_plugins(cls):
        return list(
            filter(
                None,
                map(lambda pc: pc.instance, cls.plugin_classes),
            )
        )

    @classmethod
    def create_system_prompt(cls):
        prompt_variables = {
            "marker_begin": MARKER_BEGIN,
            "marker_end": MARKER_END,
        }

        prompt_folder = pathlib.Path("prompts/zh-CN")
        with (
            (prompt_folder / "template.md").open(encoding="utf-8") as t_f,
            (prompt_folder / "default_slots.json").open(encoding="utf-8") as d_f,
        ):
            prompt_template = t_f.read()
            prompt_comps: dict[str, str] = json.load(d_f)

        plugin_prompt_comps: list[dict[str, str]] = []
        for p in cls.loaded_plugins():
            str_prompts: dict[str, str] = {}
            for key, data in p.prompts().items():
                if isinstance(data, str):
                    str_prompts[key] = data
                elif isinstance(data, pathlib.Path):
                    str_prompts[key] = data.read_text("utf-8")
            plugin_prompt_comps.append(str_prompts)

        prompt_comps |= cls.merge_str_dict(plugin_prompt_comps)
        for k, v in prompt_comps.items():
            prompt_template = prompt_template.replace(f"{{{{{k}}}}}", v)
        for k, v in prompt_variables.items():
            prompt_template = prompt_template.replace(f"{{{{{k}}}}}", v)
        return prompt_template

    @classmethod
    def refresh_agent_data(cls):
        system_prompt = cls.create_system_prompt()

        tools: list[BaseTool] = []
        for p in cls.loaded_plugins():
            tools.extend(t(p).langchain_wrap() for t in p.tools())

        cls.trigger_event(PluginRefreshEvent(system_prompt, tools))

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
        for p in cls.loaded_plugins():
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
        formated_infos = [f"{k}\n{v}" for k, v in md_structured_infos.items()]
        return formated_infos

    @classmethod
    def get_plugin_classes(cls, type: type[BasePlugin] | type[PluginTypeBase]):
        return list(
            filter(
                lambda pc: issubclass(pc, type),
                cls.plugin_classes,
            )
        )

    @classmethod
    def get_loaded_plugins(cls, type: type[BasePlugin] | type[PluginTypeBase]):
        return list(
            filter(
                lambda p: isinstance(p, type),
                cls.loaded_plugins(),
            )
        )

    @classmethod
    def check_deps(cls, target: type[BasePlugin]):
        for dep in target.deps:
            if len(cls.get_loaded_plugins(dep)) == 0:
                return False
        return True

    @classmethod
    def trigger_event(cls, e: Event):
        ThreadedWorker.submit_task(TaskManager.trigger_event, e)

    @classmethod
    def try_load_single_plugin(cls, plugin_class: type[BasePlugin]):
        if cls.check_deps(plugin_class):
            plugin_class.load()
            for pc in cls.plugin_classes[cls.plugin_classes.index(plugin_class) :]:
                if pc.get_config().enabled and pc.instance is None:
                    cls.try_load_single_plugin(pc)

    @classmethod
    def unload_single_plugin(cls, plugin_class: type[BasePlugin]):
        plugin_class.unload()
        for pc in cls.plugin_classes[cls.plugin_classes.index(plugin_class) :]:
            if pc.instance is not None and not cls.check_deps(pc):
                cls.unload_single_plugin(pc)

    @classmethod
    def on_plugin_config_update(cls, plugin_class: type[BasePlugin]):
        if plugin_class.get_config().enabled:
            cls.try_load_single_plugin(plugin_class)
        else:
            cls.unload_single_plugin(plugin_class)
        cls.refresh_agent_data()

    @classmethod
    def plugin_reload_dispatch(cls, plugin_class: type[BasePlugin]):
        for pc in cls.plugin_classes[cls.plugin_classes.index(plugin_class) :]:
            if pc.instance is None:
                continue
            for dep in pc.deps:
                if cls.get_loaded_plugins(dep) is plugin_class.instance:
                    pc.instance.on_dep_load(plugin_class.instance, dep)

    @classmethod
    def on_event(cls, e: Event):
        if isinstance(e, PluginConfigUpdateEvent):
            cls.on_plugin_config_update(e.plugin_class)
            return

        if isinstance(e, PluginReloadEvent):
            cls.plugin_reload_dispatch(e.plugin_class)
            return

        for p in cls.loaded_plugins():
            p.on_event(e)
