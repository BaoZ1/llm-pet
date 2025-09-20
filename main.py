import sys

sys.pycache_prefix = "__pycache__"

from PySide6.QtWidgets import QApplication
from framework.agent import Agent, TaskManager
from framework.plugin import PluginManager
from framework.worker import ThreadedWorker
from framework.window import TestTray, EventBridge

def main():
    ThreadedWorker.start()

    agent = Agent()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    PluginManager.init()

    bridge = EventBridge()
    TaskManager.register_callback("bridge", bridge.event_recived.emit)
    bridge.event_recived.connect(PluginManager.on_event)

    sys_prompt, tools = PluginManager.init_plugins()
    agent.init(sys_prompt, tools)

    TaskManager.register_callback("agent", agent.on_event)

    ThreadedWorker.submit_task(agent.run)

    TestTray.init()

    app.exec()

    ThreadedWorker.stop()


if __name__ == "__main__":
    main()
