import asyncio
import io
import json
import threading
from unittest.mock import patch

import pytest

from gamma.errors import ExternalServiceError
from gamma.integrations.twitch.client import GammaStreamClient
from gamma.integrations.twitch.eventsub import TwitchEventSubWorker, TwitchEventSubConfig
from gamma.stream.models import StreamInputEvent


def response(payload, status=202):
    result = io.BytesIO(json.dumps(payload).encode())
    result.status = status
    return result


def test_queued_client_enforces_receipt_and_request_bounds():
    client = GammaStreamClient(base_url='http://127.0.0.1:1', queued=True)
    event = StreamInputEvent(event_id='fixture', kind='system')
    receipt = {'schema_version': 1, 'job_id': 'fixture', 'state': 'accepted', 'duplicate': False}
    with patch('urllib.request.urlopen', return_value=response(receipt)) as send:
        assert client.post_event(event) == receipt
        assert '/v1/stream/jobs?' in send.call_args.args[0].full_url
        assert json.loads(send.call_args.args[0].data)['schema_version'] == 1
        assert send.call_count == 1
    for payload, status in [({}, 202), ({k: v for k, v in receipt.items() if k != 'schema_version'}, 202),
                            ({**receipt, 'schema_version': True}, 202), ({**receipt, 'schema_version': 2}, 202), ({**receipt, 'job_id': 'wrong'}, 202),
                            ({**receipt, 'state': 'invented'}, 202), ({**receipt, 'state': []}, 202),
                            ({**receipt, 'duplicate': 0}, 202), (receipt, 200)]:
        with patch('urllib.request.urlopen', return_value=response(payload, status)) as send:
            with pytest.raises(ExternalServiceError):
                client.post_event(event)
            assert send.call_count == 1
    event.text = 'x' * 65536
    with patch('urllib.request.urlopen') as send:
        with pytest.raises(ExternalServiceError):
            client.post_event(event)
        send.assert_not_called()


def test_legacy_client_keeps_synchronous_result_contract():
    client = GammaStreamClient(base_url='http://127.0.0.1:1')
    with patch('urllib.request.urlopen', return_value=response({'result': 'legacy'}, 200)) as send:
        assert client.post_event(StreamInputEvent(kind='system')) == {'result': 'legacy'}
        assert '/v1/stream/events?' in send.call_args.args[0].full_url


def test_eventsub_http_post_does_not_block_async_connection_loop(tmp_path):
    entered, release = threading.Event(), threading.Event()
    completed = []
    class Client:
        def post_event(self, *args, **kwargs):
            entered.set()
            assert release.wait(2), 'async loop was blocked by HTTP post'
            completed.append(True)
            return {'ok': True}
    class Socket:
        async def __aiter__(self):
            yield json.dumps({'metadata': {'message_type': 'notification', 'message_id': 'fixture'},
                              'payload': {'subscription': {'type': 'channel.follow'},
                                          'event': {'user_id': '42', 'user_name': 'Fixture'}}})
    worker = TwitchEventSubWorker(
        config=TwitchEventSubConfig(client_id='fixture', oauth_token='fictional', broadcaster_user_id='42'),
        client=Client(), state_path=tmp_path / 'state.json')
    async def run():
        task = asyncio.create_task(worker._run_socket(Socket(), reconnects=0))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            release.set()
            await task
        finally:
            release.set()
    asyncio.run(run())
    assert completed == [True]
    assert worker._notification_count == 1
    default = TwitchEventSubWorker(config=worker.config, state_path=tmp_path / 'default.json')
    assert default.client.queued and default.client.timeout_seconds == 5
