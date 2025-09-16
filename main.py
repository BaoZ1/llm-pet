import sys

sys.pycache_prefix = "__pycache__"

from PySide6.QtWidgets import QApplication
from framework.agent import Agent, TaskManager
from framework.plugin import PluginManager
from framework.worker import ThreadedWorker
from framework.window import TestTray, EventBridge

def main():
    threaded_worker = ThreadedWorker()
    threaded_worker.start()

    agent = Agent()

    app = QApplication([])

    tm = TaskManager(agent)
    pm = PluginManager(
        agent,
        tm,
        threaded_worker,
    )

    bridge = EventBridge()
    tm.register_callback("bridge", bridge.event_recived.emit)
    bridge.event_recived.connect(pm.on_event)

    sys_prompt, tools = pm.init()
    agent.init(sys_prompt, tools, tm, pm)

    tm.register_callback("agent", agent.on_event)

    threaded_worker.submit_task(agent.run)
        
    TestTray.init()

    app.exec()

    threaded_worker.stop()


if __name__ == "__main__":
    main()
