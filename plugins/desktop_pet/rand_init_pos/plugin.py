from framework.plugin import BasePlugin
from plugins.desktop_pet.pet import PetPluginBase
import random
from PySide6.QtWidgets import QApplication


class Plugin(BasePlugin):
    deps = [PetPluginBase]

    def init(self):
        pet = self.dep(PetPluginBase).pet
        movable_size = (
            QApplication.primaryScreen().size() - pet.size()
        ).toTuple()
        init_pos = (
            random.randrange(int(movable_size[0] * 0.1), int(movable_size[0] * 0.9)),
            random.randrange(int(movable_size[1] * 0.1), int(movable_size[1] * 0.9)),
        )
        pet.move(*init_pos)
