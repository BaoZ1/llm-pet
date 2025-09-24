from framework.plugin import BasePlugin
from framework.config import BaseConfig
from dataclasses import dataclass
from datetime import datetime
import requests

@dataclass
class Config(BaseConfig):
    time: bool = True
    location: bool = False


class Plugin(BasePlugin):
    def init(self):
        self.location = (
            requests.get("https://myip.ipip.net/")
            .text.split("  ")[1]
            .split("：")[1]
            .replace(" ", "/")
        )

    def infos(self):
        d = {}
        config: Config = self.get_config()
        if config.time:
            d["Time"] = str(datetime.now())
        if config.location:
            d["Location"] = self.location
        if len(d) != 0:
            return {
                "Environment": d,
            }
