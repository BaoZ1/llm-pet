from framework.plugin import BasePlugin
from framework.config import BaseConfig
from dataclasses import dataclass
from pathlib import Path
from typing import cast

@dataclass
class Config(BaseConfig):
    charactor_file: Path | None = None

class Plugin(BasePlugin):
    def init(self):
        return super().init()

    def prompts(self):
        if file := cast(Config, self.get_config()).charactor_file:
            return {"identity_description": file}
