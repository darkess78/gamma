"""Authenticated loopback client and finite, explicitly opted-in Discord runner."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import uuid
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .discord_scope import discord_id, validate_scope
from .discord_release_scope import release_origin, validate_release_binding, release_owner_matches


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def isolated_origin(value):
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('invalid private interaction origin') from exc
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1'}
            or port is None or not 1024 <= port <= 65535 or port in {8000, 8001, 8080, 11434}
            or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment):
        raise ValueError('private Discord requires an isolated literal loopback API origin')
    return value.rstrip('/')


class RemotePrivateInteraction:
    """Same transport contract as PrivateInteraction, without local cognition."""
    def __init__(self, origin, bearer_token, *, opener=None, expected_release=None):
        self.expected_release = (validate_release_binding(expected_release)
                                 if expected_release is not None else None)
        self.origin = release_origin(origin) if self.expected_release else isolated_origin(origin)
        if not isinstance(bearer_token, str) or not 32 <= len(bearer_token) <= 200 or any(c.isspace() for c in bearer_token):
            raise ValueError('ephemeral private API bearer token required')
        self._token = bearer_token
        self._opener = opener or build_opener(_NoRedirect())
        self.failed = False

    def _request(self, path, payload=None, params=None):
        url = self.origin + path
        if params:
            url += '?' + urlencode(params)
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        request = Request(url, data=data, headers={'Authorization': 'Bearer ' + self._token,
                          'Content-Type': 'application/json'}, method='POST' if payload is not None else 'GET')
        try:
            with self._opener.open(request, timeout=(15 if self.expected_release and not path.endswith(('/input', '/audio-input')) else 180)) as response:
                raw = response.read(6_000_001)
            if len(raw) > 6_000_000:
                raise ValueError('oversized private API reply')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('invalid private API reply')
            return value
        except Exception as exc:
            self.failed = True
            raise RuntimeError('isolated interaction request failed') from exc

    def check_scope(self):
        if self.expected_release:
            value = self._request('/v1/coordination/private-release')
            if not release_owner_matches(value, self.expected_release):
                raise ValueError('private Discord release owner mismatch')
            return {key: value[key] for key in self.expected_release}
        value = self._request('/health')
        if value.get('scope') != 'isolated_owner_private' or value.get('status') != 'ok':
            raise ValueError('backend is not the isolated private owner')
        return {'scope': value['scope'], 'status': value['status']}

    @staticmethod
    def _body(consumer_id):
        return {'audience': 'owner_private', 'request_id': uuid.uuid4().hex, 'consumer_id': consumer_id}

    def status(self, consumer_id=None):
        params = {'audience': 'owner_private'}
        if consumer_id is not None:
            params['consumer_id'] = consumer_id
        return self._request('/v1/coordination/interaction/status', params=params)

    def connect(self, consumer_id):
        return self._request('/v1/coordination/interaction/connect', self._body(consumer_id))

    def disconnect(self, consumer_id):
        return self._request('/v1/coordination/interaction/disconnect', self._body(consumer_id))

    def control_consumer(self, consumer_id, action, value):
        return self._request('/v1/coordination/interaction/consumer-control',
                             {**self._body(consumer_id), 'action': action, 'value': value})

    @staticmethod
    def _observation(observation):
        values = observation.model_dump(exclude={'capability_speech_allowed'})
        values['observation'] = {key: values.pop(key) for key in ('channel', 'speaker', 'address_cue', 'confidence')}
        return values

    def observe(self, observation):
        return self._request('/v1/coordination/interaction/input', self._observation(observation))

    def submit_audio(self, observation, audio_bytes):
        if not isinstance(audio_bytes, bytes) or not 44 <= len(audio_bytes) <= 800_000:
            raise ValueError('bounded private PCM WAV required')
        values = self._observation(observation)
        values.pop('text')
        values['audio_base64'] = base64.b64encode(audio_bytes).decode('ascii')
        return self._request('/v1/coordination/interaction/audio-input', values)

    def interrupt(self, consumer_id, reason='owner_stop', transcript=None):
        values = self._body(consumer_id)
        values.update(reason=reason, transcript=transcript)
        return self._request('/v1/coordination/interaction/interrupt', values)

    def next_output(self, consumer_id):
        return self._request('/v1/coordination/interaction/poll', params={'audience': 'owner_private', 'consumer_id': consumer_id})

    def audio(self, output_id, consumer_id, generation, output_epoch):
        import re
        if not isinstance(output_id, str) or not re.fullmatch(r'output-[a-f0-9]{32}', output_id):
            raise ValueError('invalid private output id')
        value = self._request('/v1/coordination/interaction/audio/' + output_id,
                             params={'audience': 'owner_private', 'consumer_id': consumer_id,
                                     'generation': generation, 'output_epoch': output_epoch})
        raw = base64.b64decode(value.get('audio_base64', ''), validate=True)
        if not 44 <= len(raw) <= 4_000_000 or value.get('content_type') not in {'audio/wav', 'audio/x-wav', 'audio/wave'}:
            raise ValueError('invalid released private audio')
        return raw, value['content_type']

    def acknowledge(self, **values):
        return self._request('/v1/coordination/interaction/ack', {'audience': 'owner_private', **values})


async def run_discord(interaction, *, scope, bot_token, test_bot_user_id, duration):
    """Login only after the caller's explicit opt-in; no production config read."""
    from .discord import build_discord_client
    test_bot_user_id = discord_id(test_bot_user_id)
    client, adapter = build_discord_client(interaction, **scope)
    original_ready = client.on_ready

    @client.event
    async def on_ready():
        if str(getattr(client.user, 'id', '')) != test_bot_user_id:
            await client.close()
            raise ValueError('Discord test bot identity does not match')
        await original_ready()

    try:
        await asyncio.wait_for(client.start(bot_token, reconnect=False), timeout=duration)
    except TimeoutError:
        pass
    finally:
        try:
            await adapter.close()
        finally:
            await client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-origin', required=True)
    parser.add_argument('--owner-user-id', required=True)
    parser.add_argument('--dm-channel-id', required=True)
    parser.add_argument('--voice-guild-id')
    parser.add_argument('--voice-channel-id')
    parser.add_argument('--test-bot-user-id', required=True)
    parser.add_argument('--duration', type=int, default=300)
    parser.add_argument('--enable-network', action='store_true', help='Requires separate approval of this private test setting')
    args = parser.parse_args(argv)
    scope = validate_scope(args.owner_user_id, args.dm_channel_id, args.voice_guild_id, args.voice_channel_id)
    discord_id(args.test_bot_user_id)
    isolated_origin(args.api_origin)
    if not 5 <= args.duration <= 1800:
        raise ValueError('private Discord duration must be 5-1800 seconds')
    if not args.enable_network:
        print(json.dumps({'scope': 'isolated_owner_private', 'discord_connected': False,
                          'status': 'prepared_only', 'voice_selected': args.voice_channel_id is not None}))
        return 0
    if os.environ.get('GAMMA_ISOLATED_COORDINATION') != '1':
        raise ValueError('explicit isolated runtime marker required')
    token = os.environ.get('GAMMA_COORDINATION_DISCORD_TEST_BOT_TOKEN', '')
    if not isinstance(token, str) or not 20 <= len(token) <= 200 or any(c.isspace() for c in token):
        raise ValueError('dedicated test bot token must be supplied explicitly')
    interaction = RemotePrivateInteraction(args.api_origin, os.environ.get('GAMMA_COORDINATION_DISCORD_API_TOKEN', ''))
    interaction.check_scope()
    asyncio.run(run_discord(interaction, scope=scope, bot_token=token,
                           test_bot_user_id=args.test_bot_user_id, duration=args.duration))
    print(json.dumps({'scope': 'isolated_owner_private', 'discord_connected': False, 'status': 'closed'}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never include URLs, credentials, private transcript or provider errors.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'discord_connected': False}))
        raise SystemExit(1)
