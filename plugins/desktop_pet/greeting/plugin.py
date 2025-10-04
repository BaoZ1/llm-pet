from framework.plugin import BasePlugin
from framework.event import PlainEvent
from plugins.core.plugin import Plugin as CorePlugin, EventMessage


class Plugin(BasePlugin):
    deps = [CorePlugin]

    def init(self):
        self.trigger_event(
            PlainEvent(
                EventMessage("You just woke up, want to say hi to the user?"),
            )
        )
