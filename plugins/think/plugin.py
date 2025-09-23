from framework.plugin import BasePlugin
from framework.config import BaseConfig
from framework.event import Event, PluginFieldEvent
from dataclasses import dataclass
from typing import cast
from langchain_core.messages import AIMessage
import pathlib

@dataclass
class SpeakEvent(Event):
    content: str

    def agent_msg(self):
        return AIMessage(self.content)

@dataclass
class Config(BaseConfig):
    force: bool = False


class Plugin(BasePlugin):
    def prompts(self):
        file_name = (
            "think_field_force.md"
            if cast(Config, self.get_config()).force
            else "think_field.md"
        )
        return {"json_fields": pathlib.Path(__file__).with_name(file_name)}
