import asyncio
import threading
from typing import Callable, Coroutine, ClassVar


class ThreadedWorker:
    loop: ClassVar[asyncio.AbstractEventLoop]
    thread: threading.Thread = None

    @classmethod
    def start(cls):
        if cls.thread:
            raise

        cls.loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(cls.loop)
            cls._running = True
            cls.loop.run_forever()

        cls.thread = threading.Thread(target=run, daemon=True)
        cls.thread.start()

    @classmethod
    def submit_task(cls, task: Callable, *args, **kwargs):
        future = asyncio.Future()

        def handle_exception(e):
            future.set_exception(e)

        def execute():
            try:
                result = task(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    async_task = asyncio.run_coroutine_threadsafe(result, cls.loop)
                    async_task.add_done_callback(lambda f: future.set_result(f.result()))
                else:
                    future.set_result(result)
            except Exception as e:
                cls.loop.call_soon_threadsafe(handle_exception, e)

        cls.loop.call_soon_threadsafe(execute)
        return future

    @classmethod
    def stop(cls):
        if cls.loop:
            cls.loop.call_soon_threadsafe(cls.loop.stop)
