from dataclasses import dataclass
from framework.plugin import BasePlugin
from framework.event import Event, PluginFieldEvent
from langchain_core.messages import AIMessage
import pathlib


@dataclass
class SpeakEvent(Event):
    content: str

    def agent_msg(self):
        return AIMessage(self.content)


class Plugin(BasePlugin):
    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("speak_field.md")}

    def on_event(self, e):
        match e:
            case PluginFieldEvent("speak", s):
                self.trigger_event(SpeakEvent(s))
