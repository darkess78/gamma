"""Actual receive sink and transport timing regressions without a Discord login."""
import asyncio,io,json,struct,wave
from types import SimpleNamespace as NS
import pytest
from gamma.coordination import discord as transport
from test_coordination_discord_private import setup

def test_installed_sdk_sink_keeps_seven_second_question_intact(monkeypatch):
 from discord.ext import voice_recv
 clock=[100.0]
 monkeypatch.setattr(transport,'_voice_timing_clock',lambda:clock[0],raising=False)
 async def run():
  adapter,service,_dm,_room=setup()
  adapter.client.loop=asyncio.get_running_loop()
  adapter._controller=NS(_state='listening')
  sink=adapter._build_sink_type()()
  owner=NS(id=1,bot=False)
  data=voice_recv.VoiceData(NS(decrypted_data=b''),owner,pcm=struct.pack('<hh',2000,4000)*960)
  for n in range(350):
   clock[0]=100+n*.02;sink.write(owner,data)
  await asyncio.sleep(0)
  assert adapter._audio_queue.empty(),'SDK sink split an unfinished seven-second question'
  clock[0]=107.18;sink.on_voice_member_speaking_stop(owner)
  await asyncio.sleep(0)
  assert adapter._audio_queue.empty()
  clock[0]=107.5800001;sink.emit(sink.buffer.expire(now=clock[0]))
  await asyncio.sleep(0)
  assert adapter._audio_queue.qsize()==1
  _epoch,item=adapter._audio_queue.get_nowait();adapter._audio_queue.task_done()
  with wave.open(io.BytesIO(item['wav']),'rb') as f:
   assert (f.getframerate(),f.getnchannels(),f.getnframes())==(16000,1,112000)
  assert len(item['wav'])<800_000
  assert item['capture_reason']=='silence_timeout'
  assert item['capture_ended_monotonic']-item['capture_started_monotonic']==pytest.approx(7.0)
  assert item['capture_released_monotonic']-item['capture_ended_monotonic']==pytest.approx(.6)
  sink.cleanup()
 asyncio.run(run())

@pytest.mark.parametrize('status,reason',[('busy','private_foreground_capacity'),('silent',None),('ignored','empty_observation'),('cancelled','cancelled_after_core'),('generated',None)])
def test_audio_outcomes_are_recorded_without_private_content(monkeypatch,status,reason):
 clock=[10.0]
 monkeypatch.setattr(transport,'_voice_timing_clock',lambda:clock[0],raising=False)
 async def run():
  adapter,service,_dm,_room=setup()
  adapter._controller=NS(_state='listening')
  async def submit(obs,audio):
   clock[0]+=2
   return {'ok':True,'status':status,'reason':reason,'result':{'private_text':'SECRET'},'transcript':'SECRET'}
  service.submit_audio=submit
  sink=NS(closed=False,epoch=adapter._voice_epoch)
  adapter._enqueue_audio(sink,{'user_id':'1','wav':b'x'*44})
  clock[0]+=1
  task=asyncio.create_task(adapter._audio_loop())
  await asyncio.wait_for(adapter._audio_queue.join(),timeout=1)
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  snapshot=adapter.voice_timing_snapshot()
  row=snapshot['recent_requests'][-1]
  assert row['status']==status
  assert row['queue_ms']==1000
  assert row['request_ms']==2000
  assert snapshot['outcomes'][status]==1
  encoded=json.dumps(snapshot)
  assert 'SECRET' not in encoded and 'private_text' not in encoded and 'transcript' not in encoded
  assert set(adapter.stats)=={'text_delivered','audio_played','ignored','dropped','failures'}
 asyncio.run(run())

def test_audio_telemetry_is_bounded_and_redacts_arbitrary_reason(monkeypatch):
 async def run():
  adapter,service,_dm,_room=setup();adapter._controller=NS(_state='listening')
  async def submit(obs,audio):return {'status':'busy','reason':'SECRET_VALUE','result':{'text':'SECRET_VALUE'}}
  service.submit_audio=submit;sink=NS(closed=False,epoch=0)
  task=asyncio.create_task(adapter._audio_loop())
  for _ in range(40):
   adapter._enqueue_audio(sink,{'user_id':'1','wav':b'x'*44})
   await asyncio.wait_for(adapter._audio_queue.join(),timeout=1)
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  snapshot=adapter.voice_timing_snapshot()
  assert len(snapshot['recent_requests'])==32
  assert snapshot['outcomes']['busy']==40
  assert 'SECRET_VALUE' not in json.dumps(snapshot)
  snapshot['recent_requests'][0]['status']='modified'
  assert adapter.voice_timing_snapshot()['recent_requests'][0]['status']=='busy'
 asyncio.run(run())


def test_safe_backend_stages_and_playback_events_are_preserved():
 from test_coordination_discord_private import Source,Voice
 async def run():
  source=Source();adapter,service,_dm,_room=setup(audio_source_factory=lambda *_:source)
  service.connect(adapter.voice_consumer)
  output=service.output('discord-voice','discord_voice')
  output['request_id']='discord-voice-'+'a'*32
  adapter._controller=NS(_state='listening',_voice=Voice(service))
  async def submit(obs,audio):
   return {'status':'generated','timing_ms':{'stt_ms':100,'foreground_wait_ms':25,'core_ms':200,'synthesis_wait_ms':0,'total_ms':400,'first_audio_ready_ms':350,'secret':'SECRET','prompt_tokens':100},'text':'SECRET'}
  service.submit_audio=submit
  adapter._enqueue_audio(NS(closed=False,epoch=0),{'user_id':'1','wav':b'x'*44})
  task=asyncio.create_task(adapter._audio_loop());await asyncio.wait_for(adapter._audio_queue.join(),timeout=1)
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  await adapter.deliver_voice_once()
  snapshot=adapter.voice_timing_snapshot();row=snapshot['recent_requests'][-1]
  assert row['backend_stt_ms']==100 and row['backend_foreground_wait_ms']==25 and row['backend_core_ms']==200 and row['backend_first_audio_ready_ms']==350
  assert [r['event'] for r in snapshot['recent_playback']]==['attempted','play_requested','started','played']
  assert 'SECRET' not in json.dumps(snapshot) and 'prompt_tokens' not in json.dumps(snapshot)
  assert all(r['request_id']==output['request_id'] for r in snapshot['recent_playback'])
 asyncio.run(run())


def test_invalid_timing_values_and_transport_errors_never_leak_details():
 async def run():
  adapter,service,_dm,_room=setup();adapter._controller=NS(_state='listening')
  async def submit(obs,audio):
   raise RuntimeError('SECRET server response body')
  service.submit_audio=submit
  adapter._enqueue_audio(NS(closed=False,epoch=0),{'user_id':'1','wav':b'x'*44,'capture_started_monotonic':float('nan'),'capture_ended_monotonic':'SECRET'})
  task=asyncio.create_task(adapter._audio_loop());await asyncio.wait_for(adapter._audio_queue.join(),timeout=1)
  task.cancel();await asyncio.gather(task,return_exceptions=True)
  snapshot=adapter.voice_timing_snapshot();row=snapshot['recent_requests'][-1]
  assert row['status']=='failed' and row['reason']=='transport_request_failed'
  assert adapter.stats['failures']==1 and 'capture_ms' not in row
  assert 'SECRET' not in json.dumps(snapshot,allow_nan=False)
 asyncio.run(run())


def test_actual_backend_public_output_correlates_playback_to_utterance(tmp_path):
 from test_coordination_discord_private import Source,Voice
 from test_private_interaction import setup as real_setup,observation
 core,service=real_setup(tmp_path)
 source=Source();adapter,_fake,_dm,_room=setup(audio_source_factory=lambda *_:source)
 adapter.interaction=service;service.connect(adapter.voice_consumer)
 adapter._controller=NS(_state='listening',_voice=Voice(service))
 request_id='discord-voice-'+'b'*32
 try:
  service.observe(observation(request_id,consumer_id=adapter.voice_consumer,channel='discord_voice'))
  public=service.status(adapter.voice_consumer)['outputs'][0]
  assert 'request_id' not in public and public['utterance_id']==request_id
  asyncio.run(adapter.deliver_voice_once())
  rows=adapter.voice_timing_snapshot()['recent_playback']
  assert [row['event'] for row in rows]==['attempted','play_requested','started','played']
  assert all(row['request_id']==request_id for row in rows)
 finally:service.close();core.close()
