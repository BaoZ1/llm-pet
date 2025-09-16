from framework.plugin import BasePlugin
from framework.config import BaseConfig
from plugins.pet_state.plugin import Plugin as PetStatePlugin, ModifyPetStateEvent
from PySide6.QtCore import QTimer
import random


class Plugin(BasePlugin):
    deps = [PetStatePlugin]

    def init(self):
        self.interval = (30, 50)
        self.reduce_range = (1, 3)

        self.timer = QTimer(singleShot=True)
        self.timer.timeout.connect(self.on_timeout)
        self.timer.start(random.randrange(*self.interval) * 1e3)

    def on_timeout(self):
        reduce_amount = random.randint(*self.reduce_range)
        self.trigger_event(ModifyPetStateEvent("hunger", -reduce_amount))
        self.timer.start(random.randrange(*self.interval) * 1e3)


class Config(BaseConfig):
    pass