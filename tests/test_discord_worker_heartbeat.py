import asyncio
import logging

from gamma.integrations.discord.runtime import DiscordRuntimeConfig
from gamma.integrations.discord.worker import DiscordTextWorker


def test_ready_callback_without_owned_client_does_not_spawn_loop(tmp_path):
    class Client:
        def __init__(self): self.handlers={}
        def event(self,callback): self.handlers[callback.__name__]=callback; return callback
    client=Client()
    worker=DiscordTextWorker(DiscordRuntimeConfig(enabled=True,guild_id='1',text_channel_id='2'),
        runtime=object(),stream_client=object(),state_path=tmp_path/'state.json',logger=logging.getLogger('heartbeat-test'))
    worker._install_handlers(client)
    asyncio.run(client.handlers['on_ready']())
    assert worker._control_task is None
