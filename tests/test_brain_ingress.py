import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gamma.api import routes
from gamma.brain.queue import BrainQueue
from gamma.schemas.response import AssistantResponse


def test_proactive_loop_uses_queue_and_stop_cancels_waiting_cycle():
    import asyncio
    import anyio
    from unittest.mock import Mock
    from gamma.brain import runtime
    from gamma.proactive import ProactiveScheduler
    async def run(cancel):
        queued, entered, release, evaluated = (threading.Event() for _ in range(4))
        priorities = []
        class ObservedQueue(BrainQueue):
            def submit(self, function, *, priority="foreground"):
                priorities.append(priority)
                future = super().submit(function, priority=priority)
                if self.status()["pending"]:
                    queued.set()
                return future
        queue = ObservedQueue(capacity=2)
        scheduler = ProactiveScheduler(stream_brain=Mock(), bus=Mock())
        executed = []
        def evaluate(**kwargs):
            assert kwargs["defer_delivery"] is True
            executed.append(threading.get_ident())
            evaluated.set()
        scheduler.evaluate_once = evaluate
        with patch.object(runtime, "_queue", queue), patch.object(routes.settings, "proactive_idle_tick_seconds", 1):
            try:
                held = queue.submit(lambda: (entered.set(), release.wait(5), threading.get_ident())[-1])
                assert await asyncio.to_thread(entered.wait, 3)
                queued.clear()
                await scheduler.start()
                assert await asyncio.to_thread(queued.wait, 3)
                assert not evaluated.is_set()
                if cancel:
                    await scheduler.stop()
                release.set()
                worker = await asyncio.wrap_future(held)
                if cancel:
                    await asyncio.wrap_future(queue.submit(lambda: None))
                    assert not evaluated.is_set()
                else:
                    assert await asyncio.to_thread(evaluated.wait, 3)
                    assert executed == [worker]
                    assert priorities[1:3] == ["background", "background"]
            finally:
                release.set()
                await scheduler.stop()
                await asyncio.to_thread(queue.close, timeout=3)
    anyio.run(run, False)
    anyio.run(run, True)


def test_stream_rechecks_presence_after_waiting_and_shares_execution_lane():
    from unittest.mock import Mock
    from gamma.presence import presence_state_for_mode
    from gamma.stream.models import StreamActor, StreamInputEvent
    submitted, entered, release = threading.Event(), threading.Event(), threading.Event()
    class ObservedQueue(BrainQueue):
        def submit(self, function):
            future = super().submit(function)
            if self.status()["pending"]:
                submitted.set()
            return future
    queue = ObservedQueue(capacity=2)
    state = {"value": presence_state_for_mode("go_live")}
    brain = Mock()
    worker_ids = []
    def handle(*args, **kwargs):
        from concurrent.futures import Future
        worker_ids.append(threading.get_ident())
        assert kwargs["defer_delivery"] is True
        completed = Future()
        completed.set_result("result")
        return completed
    brain.handle_event.side_effect = handle
    event = StreamInputEvent(kind="chat_message", text="Shana hello", actor=StreamActor(source="twitch"))
    with patch.object(routes, "get_brain_queue", return_value=queue), patch.object(routes, "get_stream_brain", return_value=brain), patch.object(
        routes, "load_presence_state", side_effect=lambda **_: state["value"]
    ), ThreadPoolExecutor(max_workers=1) as requests:
        try:
            held = queue.submit(lambda: (entered.set(), release.wait(5), threading.get_ident())[-1])
            assert entered.wait(3)
            submitted.clear()
            response = requests.submit(routes.stream_event, event, True)
            assert submitted.wait(3)
            brain.handle_event.assert_not_called()
            state["value"] = presence_state_for_mode("sleep")
            release.set()
            assert response.result(3) == "result"
            assert worker_ids == [held.result(3)]
            prepared = brain.handle_event.call_args.args[0]
            assert prepared.metadata["presence_mode"] == "sleep"
            assert prepared.metadata["presence_suppressed"] is True
            assert brain.handle_event.call_args.kwargs["synthesize_speech"] is False
            assert "presence_mode" not in event.metadata
        finally:
            release.set()
            queue.close(timeout=3)


def test_stream_stop_invalidates_waiting_ingress():
    from unittest.mock import Mock
    from fastapi import HTTPException
    from gamma.stream.models import StreamInputEvent
    import pytest
    entered, submitted, release = (threading.Event() for _ in range(3))
    class ObservedQueue(BrainQueue):
        def submit(self, function):
            future = super().submit(function)
            if self.status()["pending"]:
                submitted.set()
            return future
    queue = ObservedQueue(capacity=2)
    brain = Mock()
    with patch.object(routes, "get_brain_queue", return_value=queue), patch.object(routes, "get_stream_brain", return_value=brain), patch.object(
        routes, "_cancel_active_live_turns", return_value={}
    ), ThreadPoolExecutor(max_workers=1) as requests:
        try:
            queue.submit(lambda: (entered.set(), release.wait(5)))
            assert entered.wait(3)
            submitted.clear()
            response = requests.submit(routes.stream_event, StreamInputEvent(kind="chat_message", text="old"))
            assert submitted.wait(3)
            routes.stream_stop(reason="test")
            release.set()
            with pytest.raises(HTTPException) as error:
                response.result(3)
            assert error.value.status_code == 409
            brain.handle_event.assert_not_called()
            brain.stop_stream.assert_called_once()
        finally:
            release.set()
            queue.close(timeout=3)


def test_image_routes_use_same_queue_and_bound_uploads():
    from gamma.brain import runtime
    from gamma.schemas.response import VisionAnalysis
    from unittest.mock import Mock
    queue = BrainQueue(capacity=2)
    service = Mock()
    calls = []
    service.respond_with_image.side_effect = lambda **kw: (calls.append(threading.get_ident()), AssistantResponse(spoken_text="image"))[1]
    service.analyze_image.side_effect = lambda **kw: (calls.append(threading.get_ident()), VisionAnalysis(summary="image"))[1]
    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(runtime, "_queue", queue), patch.object(routes, "get_conversation_service", return_value=service), TestClient(app) as client:
        try:
            worker = queue.submit(threading.get_ident).result(3)
            for endpoint in ("/v1/conversation/respond-with-image", "/v1/vision/analyze"):
                response = client.post(endpoint, data={"user_text": "describe"}, files={"image_file": ("fixture.png", b"fixture", "image/png")})
                assert response.status_code == 200
                with patch.object(routes.settings, "vision_max_image_bytes", 2):
                    assert client.post(endpoint, data={"user_text": "describe"}, files={"image_file": ("fixture.png", b"oversized", "image/png")}).status_code == 400
            assert calls == [worker, worker]
            assert service.respond_with_image.call_args.kwargs["image_bytes"] == b"fixture"
        finally:
            queue.close(timeout=3)


def test_async_shutdown_and_request_cancellation_preserve_active_execution():
    import asyncio
    import anyio
    import pytest
    from gamma.brain import runtime
    from gamma.brain.queue import BrainQueueClosed
    queue = BrainQueue(capacity=2)
    entered, release = threading.Event(), threading.Event()
    async def run():
        with patch.object(runtime, "_queue", queue):
            active = asyncio.create_task(runtime.await_brain_job(lambda: (entered.set(), release.wait(5))))
            assert await asyncio.to_thread(entered.wait, 3)
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
            assert queue.status()["active"]
            waiting = asyncio.create_task(runtime.await_brain_job(lambda: "must not execute"))
            await asyncio.sleep(0)
            assert queue.status()["pending"] == 1
            assert not queue.close(timeout=0)
            with pytest.raises(BrainQueueClosed):
                await waiting
            assert queue.status()["active"]
            release.set()
            assert await asyncio.to_thread(queue.close, timeout=3)
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_image_synthesis_releases_brain_and_cancel_revokes_grant():
    import asyncio
    import io
    import anyio
    import pytest
    from fastapi import UploadFile
    from unittest.mock import Mock
    from gamma.brain import runtime
    from gamma.delivery.executor import DeliveryExecutor
    queue = BrainQueue()
    entered, release = threading.Event(), threading.Event()
    service = Mock()
    response = AssistantResponse(spoken_text="image", tts_metadata={"delivery_grant": "image-grant"})
    service.respond_with_image.return_value = response
    service.deliver_response.side_effect = lambda *a, **kw: (entered.set(), release.wait(5))
    async def run():
        with patch.object(runtime, "_queue", queue), patch.object(routes, "get_conversation_service", return_value=service), patch.object(
            routes, "_image_delivery", DeliveryExecutor(capacity=1)
        ):
            task = asyncio.create_task(routes.conversation_respond_with_image(user_text="fixture",
                image_file=UploadFile(file=io.BytesIO(b"fixture"), filename="fixture.png"),
                synthesize_speech=True, vision_mode=None, session_id=None))
            assert await asyncio.to_thread(entered.wait, 3)
            assert await asyncio.wrap_future(queue.submit(lambda: "next decision")) == "next decision"
            assert service.respond_with_image.call_args.kwargs["defer_delivery"] is True
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            service.cancel_delivery.assert_called_once_with("image-grant")
            release.set()
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_http_ingress_queues_work_and_status_remains_responsive():
    queue = BrainQueue(capacity=1)
    entered, release = threading.Event(), threading.Event()
    threads = []
    class Service:
        def deliver_response(self, response, **kwargs):
            return response

        def respond(self, **kwargs):
            threads.append(threading.get_ident())
            if kwargs["user_text"] == "first":
                entered.set()
                assert release.wait(5)
            return AssistantResponse(spoken_text=kwargs["user_text"])
    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(routes, "get_brain_queue", return_value=queue), patch.object(
        routes, "get_conversation_service", return_value=Service()
    ), TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as requests:
        try:
            first = requests.submit(client.post, "/v1/conversation/respond", json={"user_text": "first"})
            assert entered.wait(3)
            status = client.get("/v1/brain/status")
            assert status.status_code == 200
            assert status.json()["active"] is True
            waiting = queue.submit(lambda: "waiting")
            overloaded = client.post("/v1/conversation/respond", json={"user_text": "rejected"})
            assert overloaded.status_code == 503
            assert overloaded.headers["retry-after"] == "1"
            assert len(threads) == 1
            release.set()
            assert first.result(3).json()["spoken_text"] == "first"
            assert waiting.result(3) == "waiting"
            second = client.post("/v1/conversation/respond", json={"user_text": "second"})
            assert second.status_code == 200
            assert second.json()["spoken_text"] == "second"
            assert len(set(threads)) == 1
        finally:
            release.set()
            assert queue.close(timeout=3)
        assert client.post("/v1/conversation/respond", json={"user_text": "after stop"}).status_code == 503


def test_spoken_http_delivery_does_not_block_next_brain_decision():
    queue = BrainQueue()
    entered, release = threading.Event(), threading.Event()
    decisions, deliveries = [], []
    class Service:
        def respond(self, **kwargs):
            assert kwargs["defer_delivery"] is True
            decisions.append(threading.get_ident())
            return AssistantResponse(spoken_text=kwargs["user_text"])

        def deliver_response(self, response, *, synthesize_speech):
            deliveries.append(threading.get_ident())
            if synthesize_speech:
                entered.set()
                assert release.wait(5)

    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(routes, "get_brain_queue", return_value=queue), patch.object(
        routes, "get_conversation_service", return_value=Service()
    ), TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as requests:
        try:
            spoken = requests.submit(client.post, "/v1/conversation/respond", json={"user_text": "spoken", "synthesize_speech": True})
            assert entered.wait(3)
            assert client.get("/v1/brain/status").json()["active"] is False
            text = client.post("/v1/conversation/respond", json={"user_text": "text"})
            assert text.status_code == 200 and text.json()["spoken_text"] == "text"
            assert not spoken.done()
            assert len(set(decisions)) == 1 and decisions[0] not in deliveries
            release.set()
            assert spoken.result(3).status_code == 200
            delivered = len(deliveries)
            evaluation = client.post("/v1/conversation/respond", json={"user_text": "evaluation", "evaluation_mode": True})
            assert evaluation.status_code == 200 and len(deliveries) == delivered
        finally:
            release.set()
            queue.close(timeout=3)


def test_new_lifecycle_cannot_replace_draining_worker():
    from gamma.brain import runtime
    queue = BrainQueue(capacity=1)
    entered, release = threading.Event(), threading.Event()
    from gamma.brain.queue import BrainQueueClosed
    import pytest
    with patch.object(runtime, "_queue", queue):
        try:
            active = queue.submit(lambda: (entered.set(), release.wait(3)))
            assert entered.wait(3)
            assert not queue.close(timeout=0)
            with pytest.raises(BrainQueueClosed):
                runtime.start_brain_queue()
            assert runtime.get_brain_queue() is queue
            release.set()
            active.result(3)
            assert queue.close(timeout=3)
            replacement = runtime.start_brain_queue()
            assert replacement is not queue
            assert replacement.submit(lambda: 1).result(3) == 1
            assert replacement.close(timeout=3)
        finally:
            release.set()
            queue.close(timeout=3)
