from framework.plugin import BasePlugin, PetPluginProtocol
import random
from PySide6.QtWidgets import QApplication


class Plugin(BasePlugin):
    name = "rand_init_pos"
    deps = [PetPluginProtocol]

    def init(self):
        pet = self.dep(PetPluginProtocol).pet()
        movable_size = (
            QApplication.primaryScreen().size() - pet.size()
        ).toTuple()
        init_pos = (
            random.randrange(int(movable_size[0] * 0.1), int(movable_size[0] * 0.9)),
            random.randrange(int(movable_size[1] * 0.1), int(movable_size[1] * 0.9)),
        )
        pet.move(*init_pos)


