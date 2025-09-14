import asyncio
import threading
from typing import Callable, Coroutine


class ThreadedWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = None
        self._running = False

    def start(self):
        if self._running:
            return

        def run():
            asyncio.set_event_loop(self.loop)
            self._running = True
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def submit_task(self, task: Callable, *args, **kwargs):
        future = asyncio.Future()

        def handle_exception(e):
            future.set_exception(e)

        def execute():
            try:
                result = task(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    async_task = asyncio.run_coroutine_threadsafe(result, self.loop)
                    async_task.add_done_callback(lambda f: future.set_result(f.result()))
                else:
                    future.set_result(result)
            except Exception as e:
                self.loop.call_soon_threadsafe(handle_exception, e)

        self.loop.call_soon_threadsafe(execute)
        return future

    def stop(self):
        if self._running:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._running = False
