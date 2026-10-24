"""Bounded playback message relay; authentication belongs to its route."""
import asyncio
import json


async def relay(browser, upstream):
    async def incoming():
        while True:
            message = await browser.receive_text()
            try:
                if not isinstance(message, str) or len(message) > 8192:
                    raise ValueError()
                payload = json.loads(message)
                if not isinstance(payload, dict) or payload.get("type") not in (
                    "subscriber_capabilities", "playback_heartbeat", "playback_prepare", "playback_ack"
                ):
                    raise ValueError()
            except (ValueError, TypeError):
                await browser.close(code=1008)
                return
            await upstream.send(message)

    async def outgoing():
        while True:
            message = await upstream.recv()
            if not isinstance(message, str) or len(message) > 2097152:
                await browser.close(code=1009)
                return
            await browser.send_text(message)

    tasks = [asyncio.create_task(incoming()), asyncio.create_task(outgoing())]
    try:
        completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in completed:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
