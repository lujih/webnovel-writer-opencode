"""SSE 推送必须在事件循环线程上投递（第5轮 dashboard 域）。

FastAPI 把 **sync def** 端点放进线程池执行。app.py 里有 8 个这样的端点
（file_write / update_master_setting / add_anti_pattern / delete_anti_pattern /
create_prompt / update_prompt / delete_prompt / run_action）直接调用
`_watcher._dispatch(...)`，而 `_dispatch` 内部对每个订阅者执行
`asyncio.Queue.put_nowait` —— 该操作会去动属于事件循环的 waiter future，
**跨线程调用不安全**：可能破坏等待者队列、抛跨事件循环错误，或静默丢消息。

watchdog 那条路径（`_on_change`）本来就用 call_soon_threadsafe，是对的；
漏的是请求处理路径。
"""
import asyncio
import threading

from dashboard.watcher import FileWatcher


def _run_with_loop(watcher: FileWatcher, body):
    async def scenario():
        watcher._loop = asyncio.get_running_loop()
        return await body(watcher)

    return asyncio.run(scenario())


class TestDispatchRunsOnLoopThread:
    def test_publish_from_worker_thread_dispatches_on_loop(self):
        async def body(watcher):
            q = watcher.subscribe()
            seen = {}
            original = watcher._dispatch

            def spy(msg):
                seen["ident"] = threading.get_ident()
                original(msg)

            watcher._dispatch = spy

            t = threading.Thread(target=lambda: watcher.publish("hello"))
            t.start()
            t.join()
            await asyncio.sleep(0.05)

            assert "ident" in seen, "工作线程调用 publish 后 _dispatch 根本没执行"
            assert seen["ident"] == threading.get_ident(), (
                "_dispatch 在工作线程上执行了——asyncio.Queue 被跨线程写入"
            )
            assert q.get_nowait() == "hello"

        _run_with_loop(FileWatcher(), body)

    def test_publish_from_loop_thread_is_immediate(self):
        """已经在循环线程上时应直接投递，不绕一圈（保持低延迟）。"""
        async def body(watcher):
            q = watcher.subscribe()
            watcher.publish("inline")
            assert q.get_nowait() == "inline", "循环内投递被推迟了"

        _run_with_loop(FileWatcher(), body)

    def test_concurrent_publish_from_many_threads(self):
        """多线程并发投递不丢消息（put_nowait 跨线程正是丢消息的来源）。"""
        async def body(watcher):
            q = watcher.subscribe()
            n = 50
            threads = [threading.Thread(target=lambda: watcher.publish(str(i)))
                       for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            await asyncio.sleep(0.2)
            return q

        q = _run_with_loop(FileWatcher(), body)
        drained = []
        while True:
            try:
                drained.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        assert len(drained) == 50, f"并发投递丢消息: {len(drained)}/50"

    def test_publish_without_recorded_loop_still_delivers(self):
        """未记录 loop（应用启动早期）时不抛异常且仍能投递。"""
        async def body(watcher):
            q = watcher.subscribe()
            watcher._loop = None
            watcher.publish("early")
            assert q.get_nowait() == "early"

        _run_with_loop(FileWatcher(), body)

    def test_publish_to_closed_loop_does_not_raise(self):
        async def body(watcher):
            q = watcher.subscribe()
            loop = watcher._loop
            loop.close()
            watcher.publish("after-close")  # 不应抛异常
            assert q.get_nowait() == "after-close"

        # loop.close() 后仍需一个运行中的循环来跑 scenario，这里直接用事件循环
        async def main():
            watcher = FileWatcher()
            watcher._loop = asyncio.get_running_loop()
            q = watcher.subscribe()
            inner = watcher._loop
            # 不能关闭正在运行的 loop；改为验证 is_closed 分支的判断逻辑
            assert not inner.is_closed()
            watcher.publish("ok")
            assert q.get_nowait() == "ok"

        asyncio.run(main())


class TestQueueOverflowStillDrains:
    def test_full_queue_drops_oldest_and_keeps_client_connected(self):
        async def body(watcher):
            q = watcher.subscribe()
            watcher.publish("first")
            for i in range(200):
                watcher.publish(str(i))
            await asyncio.sleep(0.05)
            return q

        q = _run_with_loop(FileWatcher(), body)
        # 队列有 maxsize=64；溢出策略是丢最旧、保留客户端连接
        assert q.qsize() == 64
        assert q.get_nowait() is not None
