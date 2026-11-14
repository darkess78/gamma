"""Development app for the coordination core.

Separate from production Shana (port 8000) and the dashboard (8001): it binds
loopback only, keeps its state under ``data/coordination-dev``, and starts only
when GAMMA_COORDINATION_DEV=1. It does not publish speech, write Shana's memory,
or touch the live source tree.

    GAMMA_COORDINATION_DEV=1 python -m gamma.coordination.app --port 8010
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..coding.durable_practice import PracticeRunner, PracticeYield
from .core import Core
from .store import Store


class InputBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    request_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')


def practice_proposer(endpoint, model):
    """Offline practice through the local learner; yields while the GPU is in use."""
    from ..coding.learner import LocalPracticeLearner, PracticeInferenceUnavailable

    learner = LocalPracticeLearner(endpoint=endpoint, model=model, timeout_seconds=120)

    def propose(task, files, feedback):
        try:
            learner.require_idle()  # Any resident model (e.g. another project's) means wait.
        except Exception as exc:
            raise PracticeYield(type(exc).__name__) from exc
        try:
            return learner(task, files, feedback)
        except PracticeInferenceUnavailable as exc:
            raise RuntimeError('practice_inference_failed') from exc

    return propose


def build_core(data_dir, *, decide, propose, check=None, **options):
    from ..coding.curriculum import discord_foundations
    from ..coding.sandbox import run_practice_check

    store = Store(Path(data_dir))
    runner = PracticeRunner(store, propose=propose, check=check or run_practice_check)
    return Core(store, decide=decide, runner=runner,
                practice_ids=[task.id for task in discord_foundations()], **options)


def create_app(core: Core, *, tick_seconds=1.0) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        async def loop():
            while True:
                await asyncio.to_thread(core.tick)
                await asyncio.sleep(tick_seconds)
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()
            core.close()

    app = FastAPI(title='Shana coordination core (dev)', lifespan=lifespan)

    @app.post('/api/coordination/input')
    async def post_input(body: InputBody):
        try:
            return await asyncio.to_thread(core.input, body.text, request_id=body.request_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/coordination/sleep')
    async def post_sleep():
        return await asyncio.to_thread(core.sleep)

    @app.post('/api/coordination/wake')
    async def post_wake():
        return await asyncio.to_thread(core.wake)

    @app.get('/api/coordination/status')
    async def get_status():
        return await asyncio.to_thread(core.status)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--data-dir', default=None)
    parser.add_argument('--practice-model', default='qwen3.5:4b')
    args = parser.parse_args()
    if os.environ.get('GAMMA_COORDINATION_DEV') != '1':
        raise SystemExit('Set GAMMA_COORDINATION_DEV=1 to run the development coordination core.')

    import uvicorn

    from ..config import settings
    from .decide import build_llm_decider

    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir / 'coordination-dev'
    core = build_core(data_dir, decide=build_llm_decider(),
                      propose=practice_proposer(settings.local_llm_endpoint, args.practice_model))
    uvicorn.run(create_app(core), host='127.0.0.1', port=args.port, log_level='info')


if __name__ == '__main__':
    main()
