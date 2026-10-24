import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec=importlib.util.spec_from_file_location('session',Path(__file__).parents[1]/'src/gamma/integrations/discord/session.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class SessionTest(unittest.IsolatedAsyncioTestCase):
    def setup_session(self):
        self.calls=[]; self.sent=[]; self.commands=[]
        async def ingest(msg): self.calls.append(msg); return {'ok':True,'ignored':False,'result':{'approved':'hello'}}
        async def send(msg,text): self.sent.append((msg,text))
        async def command(uid,action): self.commands.append((uid,action)); return {'ok':True,'state':'listening','reason':None}
        self.ingest=ingest; self.send=send; self.command=command
        self.kwargs=dict(guild_id='100',text_channel_id='200',owner_user_id='1',reply_enabled=True,ingest=ingest,send=send,approved_reply=lambda r:r.get('approved'),message_chunks=lambda t:[t],voice_command=command)
        return module.DiscordSession(**self.kwargs)
    def message(self,text='hello',uid='1',guild='100',channel='200',bot=False):
        return SimpleNamespace(content=text,author=SimpleNamespace(id=int(uid),bot=bot),guild=SimpleNamespace(id=int(guild)) if guild else None,channel=SimpleNamespace(id=int(channel)))
    async def test_text_routes_once_and_replies(self):
        session=self.setup_session(); message=self.message()
        response=await session.handle_message(message)
        self.assertTrue(response['ok']); self.assertEqual(self.calls,[message]); self.assertEqual(self.sent,[(message,'hello')])
    async def test_public_conversation_but_owner_only_commands(self):
        session=self.setup_session()
        await session.handle_message(self.message(uid='2'))
        self.assertEqual(len(self.calls),1)
        response=await session.handle_message(self.message('!shana join',uid='2'))
        self.assertFalse(response['ok']); self.assertEqual(response['reason'],'owner_required')
        self.assertEqual(self.commands,[]); self.assertEqual(len(self.calls),1)
    async def test_only_supported_owner_commands(self):
        session=self.setup_session()
        for action in ['join','leave','pause','resume','status']:
            response=await session.handle_message(self.message('!shana '+action))
            self.assertTrue(response['ok'])
        self.assertEqual(len(self.commands),5); self.assertEqual(self.calls,[])
        for text in ['!shana shell','!shana join extra','!shana','!shana rm -rf']:
            response=await session.handle_message(self.message(text))
            self.assertFalse(response['ok']); self.assertEqual(response['reason'],'unknown_command')
        self.assertEqual(len(self.commands),5); self.assertEqual(self.calls,[])
    async def test_allowlist_and_bot_filter(self):
        session=self.setup_session()
        for message in [self.message(guild='101'),self.message(channel='201'),self.message(bot=True),self.message(guild=''),self.message(text=' ')]:
            response=await session.handle_message(message)
            self.assertTrue(response['ignored'])
        self.assertEqual(self.calls,[]); self.assertEqual(self.sent,[])
    async def test_reply_disabled_and_silence(self):
        self.setup_session(); self.kwargs['reply_enabled']=False
        await module.DiscordSession(**self.kwargs).handle_message(self.message())
        self.assertEqual(self.sent,[])
        self.kwargs['reply_enabled']=True; self.kwargs['approved_reply']=lambda _:None
        await module.DiscordSession(**self.kwargs).handle_message(self.message())
        self.assertEqual(self.sent,[])
    async def test_busy_rejects_extra_work(self):
        self.setup_session(); entered=asyncio.Event(); release=asyncio.Event()
        async def slow(msg): entered.set(); await release.wait(); return {'ok':True,'result':{'approved':'done'}}
        self.kwargs['ingest']=slow; session=module.DiscordSession(**self.kwargs)
        task=asyncio.create_task(session.handle_message(self.message()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        response=await asyncio.wait_for(session.handle_message(self.message(uid='2')),timeout=1)
        self.assertEqual(response['reason'],'busy'); release.set(); await task
        self.assertEqual(len(self.sent),1)
        self.assertTrue((await session.handle_message(self.message()))['ok'])
    async def test_delivery_error_never_retries(self):
        self.setup_session(); self.kwargs['message_chunks']=lambda _:['one','two','three']
        async def fail(msg,text):
            self.sent.append(text)
            if text=='two': raise TimeoutError('secret details')
        self.kwargs['send']=fail; session=module.DiscordSession(**self.kwargs)
        response=await session.handle_message(self.message())
        self.assertFalse(response['ok']); self.assertEqual(response['reason'],'delivery_error')
        self.assertEqual(self.sent,['one','two']); self.assertNotIn('secret',str(response))
    async def test_cancellation_releases_busy(self):
        self.setup_session()
        async def cancelled(msg): raise asyncio.CancelledError()
        self.kwargs['ingest']=cancelled; session=module.DiscordSession(**self.kwargs)
        for _ in range(2):
            with self.assertRaises(asyncio.CancelledError):await session.handle_message(self.message())
    async def test_upstream_failure_must_remain_failure(self):
        self.setup_session()
        async def fail(msg):return {'ok':False,'error':'private details'}
        self.kwargs['ingest']=fail
        response=await module.DiscordSession(**self.kwargs).handle_message(self.message())
        self.assertFalse(response['ok']); self.assertEqual(self.sent,[])
        self.assertNotIn('private',str(response))
    async def test_busy_includes_delivery(self):
        self.setup_session(); entered=asyncio.Event(); release=asyncio.Event()
        async def slow(msg,text):entered.set(); await release.wait()
        self.kwargs['send']=slow; session=module.DiscordSession(**self.kwargs)
        task=asyncio.create_task(session.handle_message(self.message()))
        await asyncio.wait_for(entered.wait(),timeout=1)
        response=await asyncio.wait_for(session.handle_message(self.message(uid='2')),timeout=1)
        self.assertEqual(response['reason'],'busy'); release.set(); await task
    async def test_command_delivery_error_must_remain_failure(self):
        self.setup_session()
        async def fail(msg,text):raise TimeoutError('private')
        self.kwargs['send']=fail
        response=await module.DiscordSession(**self.kwargs).handle_message(self.message('!shana status'))
        self.assertFalse(response['ok']); self.assertEqual(response['reason'],'delivery_error')


if __name__=='__main__':unittest.main()
