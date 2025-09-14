from __future__ import annotations
import importlib.util
import json
import pathlib
from langchain_core.tools import tool
from typing import Any, ClassVar, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Task, Event, Agent, TaskManager
    from .worker import ThreadedWorker
    from PySide6.QtWidgets import QWidget


class PluginInterface:
    name: ClassVar[str]
    dep_names: ClassVar[Sequence[str]] = []

    def __init__(self, manager: PluginManager, deps: dict[str, PluginInterface]):
        self.manager = manager
        self.deps = deps

    def init(self, screen: QWidget):
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

    def __init__(self, plugin: PluginInterface):
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
        window: QWidget,
        agent: Agent,
        task_manager: TaskManager,
        worker: ThreadedWorker,
    ):
        self.window = window
        self.agent = agent
        self.task_manager = task_manager
        self.worker = worker

    def init(self):
        plugin_class_list: list[type[PluginInterface]] = []
        plugins_root = pathlib.Path("plugins")
        for plugin_folder in plugins_root.iterdir():
            import_file = plugin_folder / "plugin.py"
            spec = importlib.util.spec_from_file_location(
                "plugin", str(import_file.absolute())
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plugin_class_list.append(getattr(module, "Plugin"))

        missing_deps: list[type[PluginInterface]] = []
        self.plugins: dict[str, PluginInterface] = {}
        self.ordered_plugins: list[PluginInterface] = []
        for plugin_class in plugin_class_list:
            if plugin_class.dep_names:
                missing_deps.append(plugin_class)
            else:
                p = plugin_class(self, {})
                self.plugins[p.name] = p
                self.ordered_plugins.append(p)
        while missing_deps:
            new_mssing_deps = []
            for plugin_class in missing_deps:
                for dep_name in plugin_class.dep_names:
                    if dep_name not in self.plugins:
                        new_mssing_deps.append(plugin_class)
                        break
                if plugin_class not in new_mssing_deps:
                    p = plugin_class(
                        self,
                        {
                            dep_name: self.plugins[dep_name]
                            for dep_name in plugin_class.dep_names
                        },
                    )
                    self.plugins[p.name] = p
                    self.ordered_plugins.append(p)
            if len(new_mssing_deps) == len(missing_deps):
                raise
            missing_deps = new_mssing_deps

        tools = []
        tasks = []
        for p in self.ordered_plugins:
            p.init(self.window)
            tools.extend([f(p).langchain_wrap() for f in p.tools()])
            tasks.extend(p.init_tasks())

        prompt_folder = pathlib.Path("prompts/zh-CN")
        with (
            (prompt_folder / "template.md").open(encoding="utf-8") as t_f,
            (prompt_folder / "default_slots.json").open(encoding="utf-8") as d_f,
        ):
            prompt_template = t_f.read()
            prompt_comps: dict[str, str] = json.load(d_f)
            
        plugin_prompt_comps: list[dict[str, str]] = []
        for p in self.plugins.values():
            str_prompts: dict[str, str] = {}
            for key, data in p.prompts().items():
                if isinstance(data, str):
                    str_prompts[key] = data
                elif isinstance(data, pathlib.Path):
                    str_prompts[key] = data.read_text("utf-8")
            plugin_prompt_comps.append(str_prompts)
                        
        prompt_comps |= self.merge_str_dict(plugin_prompt_comps)
        system_prompt = prompt_template.format_map(prompt_comps)

        return system_prompt, tools, tasks

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
        for p in self.ordered_plugins:
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

    def on_event(self, e: Event):
        for p in self.plugins.values():
            p.on_event(e)
