"""Cold startup loads providers without a turn, speech or an unbounded wait."""
from contextlib import nullcontext
from io import BytesIO
import json
import threading
import time
import wave
from unittest.mock import Mock
import pytest
from gamma.coordination.inference_profile import warm_release_inference
from test_private_inference_profile import PROFILE, configured

def warm_settings():
 value=configured();value.local_llm_model="qwen3.8:27b";return value


def install(monkeypatch, failure=False):
 calls=[]
 def response(req,**kwargs):
  body=json.loads(req.data);calls.append((req.full_url,body,kwargs['timeout']))
  if failure and len(calls)==1:raise OSError('PRIVATE_ERROR_CONTENT')
  return BytesIO(json.dumps({'model':body['model'],'done':True,'response':'PRIVATE_TRANSCRIPT','context':['PRIVATE'],'load_duration':1000000}).encode())
 monkeypatch.setattr('urllib.request.urlopen',response)
 monkeypatch.setattr('gamma.llm.backend_admission.backend_slot',lambda *a,**kw:nullcontext())
 return calls


def test_startup_loads_exact_models_and_silent_stt_without_retaining_text(monkeypatch):
 calls=install(monkeypatch);audio=[]
 def transcribe(raw):audio.append(raw);return 'PRIVATE_TRANSCRIPT'
 result=warm_release_inference(PROFILE,warm_settings(),transcribe)
 assert result['status']=='ready' and result['stt']['status']=='ready'
 assert [x[0] for x in calls]==['http://127.0.0.1:11434/api/generate','http://127.0.0.1:11435/api/generate']
 assert all(body['prompt']=='' and body['keep_alive']=='30m' and timeout<=10 for _,body,timeout in calls)
 assert calls[1][1]['options']['num_gpu']==32 and calls[1][1]['options']['num_thread']==8
 assert len(audio)==1
 with wave.open(BytesIO(audio[0])) as w:
  assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(1,2,16000,4000)
  assert w.readframes(4000)==bytes(8000)
 assert 'PRIVATE' not in json.dumps(result) and result['models']['primary']['load_ms']==1.0


def test_provider_unavailability_is_visible_and_never_claims_readiness(monkeypatch):
 calls=install(monkeypatch,failure=True)
 r=warm_release_inference(PROFILE,warm_settings())
 assert r['status']=='degraded' and r['models']['primary']['status']=='unavailable'
 assert r['models']['review']['status']=='ready' and len(calls)==2
 assert 'PRIVATE' not in json.dumps(r)


def test_stt_startup_wait_is_bounded_without_claiming_cancelled_native_work(monkeypatch):
 install(monkeypatch);entered=threading.Event();release=threading.Event();finished=threading.Event()
 def held(_raw):
  entered.set();release.wait(2);finished.set();return 'discarded'
 started=time.monotonic()
 try:
  result=warm_release_inference(PROFILE,warm_settings(),held,maximum_seconds=.05)
  assert entered.is_set() and time.monotonic()-started<.5
  assert result['status']=='degraded' and result['stt']['status']=='warming'
 finally:
  release.set()
  if entered.is_set():assert finished.wait(1)


def test_unconfigured_release_does_not_start_models_or_stt(monkeypatch):
 calls=install(monkeypatch);transcribe=Mock()
 assert warm_release_inference(None,warm_settings(),transcribe)=={'status':'not_requested'}
 assert calls==[] and not transcribe.called


@pytest.mark.parametrize('budget',[True,0,-1,31,'30',float('inf'),float('nan')])
def test_warmup_budget_is_finite_and_validated_before_work(monkeypatch,budget):
 calls=install(monkeypatch)
 with pytest.raises(ValueError):warm_release_inference(PROFILE,warm_settings(),maximum_seconds=budget)
 assert calls==[]
