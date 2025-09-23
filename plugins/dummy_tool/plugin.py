from framework.plugin import BasePlugin, Tool
from datetime import datetime

class TimeTool(Tool):
    name = "get_time"
    def invoke(self):
        """Get current time
        """
        return str(datetime.now())


class Plugin(BasePlugin):
    def tools(self):
        return [TimeTool]
