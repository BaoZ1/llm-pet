import sys

sys.pycache_prefix = "__pycache__"

from PySide6.QtWidgets import QApplication
from framework.event import TaskManager
from framework.plugin import PluginManager
from framework.worker import ThreadedWorker
from framework.window import TestTray, EventBridge

def main():
    ThreadedWorker.start()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    bridge = EventBridge()
    TaskManager.register_callback("bridge", bridge.event_recived.emit)
    bridge.event_recived.connect(PluginManager.on_event)

    PluginManager.init()

    TestTray.init()

    app.exec()

    ThreadedWorker.stop()


if __name__ == "__main__":
    main()
