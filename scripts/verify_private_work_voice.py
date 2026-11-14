"""Finite real P3 worker plus P5 voice journey in the isolated Gamma candidate.

The saved-candidate gate controls only observation timing. Proposals, protected
checks and replies remain real. Generated input speech is a controlled fixture;
fetched output does not prove a person heard it or establish natural delivery.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.request
import uuid

from growth_probe_support import Host, checker_files, hashed, source_files
from verify_coordination_foundation import publish, until


GOAL = '''Build text_metrics.py as a standard-library command line tool. Read
all text from standard input and print one JSON object with exactly three keys:
lines = len(text.splitlines()), nonempty = number of those lines whose strip()
is not empty, characters = len(text) including line endings. Preserve Unicode.
No command line options, file reads or writes, dependencies, or network. Empty
stdin produces zeros. Modify only text_metrics.py. All work is disposable and
cannot activate application changes.'''
CHECK = r'''import json,subprocess,sys,unittest
class MetricsFlow(unittest.TestCase):
    def test_cli(self):
        for text in ('', 'one\n\ntwo\n', 'caf\u00e9\n x'):
            result=subprocess.run([sys.executable,'text_metrics.py'],input=text,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            lines=text.splitlines()
            self.assertEqual(json.loads(result.stdout),{'lines':len(lines),'nonempty':sum(bool(s.strip()) for s in lines),'characters':len(text)})
'''
INDEPENDENT = r'''import json,subprocess,sys
for text in ('  \n\t\n', 'a\nb\n', '\u03b1\u03b2\n\u96ea', '\n', 'last line without a newline'):
    result=subprocess.run([sys.executable,'text_metrics.py'],input=text.encode('utf-8'),capture_output=True)
    assert result.returncode==0,result.stderr
    lines=text.splitlines()
    expected={'lines':len(lines),'nonempty':sum(bool(s.strip()) for s in lines),'characters':len(text)}
    assert json.loads(result.stdout)==expected,(result.stdout,expected)
print('independent_text_metrics_cli_passed')
'''


def independent_check(files):
    from gamma.coding.sandbox import run_practice_check
    from gamma.coding.practice import _digest
    values={**files,'independent_metrics_owner_flow.py':INDEPENDENT}
    with tempfile.TemporaryDirectory(prefix='p5-independent-metrics-') as folder:
        root=Path(folder)
        for name,text in values.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        result=run_practice_check(root,SimpleNamespace(name='independent_metrics_owner_cli',
            module='independent_metrics_owner_flow',kind='feature'))
    return {**result,'candidate_source_sha256':_digest(files),
        'checker_sha256':hashlib.sha256(INDEPENDENT.encode()).hexdigest(),
        'independent_of_learner_checks':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.8:27b')
    parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--real-audio',action='store_true')
    parser.add_argument('--profile',default='qwen_shana_dataset_xvec_soft')
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('isolated Gamma candidate required')
    root=project/'data/coordination/p5/worker-voice-flow'/uuid.uuid4().hex
    root.mkdir(parents=True,mode=0o700);state=root/'state'
    report={'kind':'p5_actual_practical_worker_with_private_voice','complete':False,
        'production_activated':False,'paid_calls':0,'external_submissions':0,
        'real_audio':args.real_audio,'human_listening_assessed':False,'real_microphone_assessed':False,
        'source_identity':'fresh_isolated_identity','timing_gate':'durably_saved_real_candidate_before_protected_checks',
        'events':[],'voice':[],'model':args.model,'endpoint_scope':'explicit_local_only'}
    publish(root/'report.json',report)
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/ps',timeout=5) as response:
        if json.load(response).get('models'):
            raise RuntimeError('selected endpoint occupied; no inference sent')
    def model_identity():
        with urllib.request.urlopen(args.endpoint.rstrip('/')+'/api/tags',timeout=5) as response:
            matches=[row for row in json.load(response).get('models',[])
                     if row.get('name')==args.model or row.get('model')==args.model]
        if len(matches)!=1 or not matches[0].get('digest'):
            raise RuntimeError('explicit local model digest unavailable')
        return {'name':args.model,'digest':matches[0]['digest']}
    report['model_identity_before']=model_identity()
    os.environ.update({'SHANA_TTS_PROVIDER':'qwen-tts' if args.real_audio else 'stub',
        'SHANA_TTS_PROFILE':args.profile if args.real_audio else 'default',
        'SHANA_STT_PROVIDER':'faster-whisper' if args.real_audio else 'stub',
        'SHANA_STT_MODEL':'base.en','SHANA_STT_DEVICE':'cpu','SHANA_STT_COMPUTE_TYPE':'int8',
        'SHANA_AUDIO_OUTPUT_DIR':str(root/'provider-audio')})
    source=source_files(project);checkers=checker_files(project)
    report.update(source_sha256=hashed(source),checker_sha256=hashed(checkers))
    publish(root/'source-files.json',source);publish(root/'checker-files.json',checkers)
    host=None;heartbeat=None;stop=threading.Event();started=time.monotonic()
    voice_started={};first_output_poll={};poll_lock=threading.Lock()
    consumer='p5-work-voice';prefix='/api/coordination/interaction'
    def checkpoint():publish(root/'report.json',report)
    def event(kind,**values):
        report['events'].append({'kind':kind,'at':time.time(),'elapsed_seconds':round(time.monotonic()-started,3),**values})
        checkpoint()
    try:
        host=Host(project,state,args.model,args.endpoint).start();report['host_pid']=host.process.pid
        def post(path,**values):
            return host.request('POST',prefix+path,json={'audience':'owner_private','request_id':uuid.uuid4().hex,
                'consumer_id':consumer,**values})
        def poll():return host.request('GET',prefix+'/poll',params={'audience':'owner_private','consumer_id':consumer})
        def control(action,**values):
            return host.request('POST','/api/coordination/control',json={'audience':'owner_private',
                'request_id':uuid.uuid4().hex,'action':action,**values})
        def personal():return host.request('GET','/api/coordination/personal',params={'audience':'owner_private'})
        def voice(text, audio=None):
            rid=uuid.uuid4().hex;before=time.monotonic()
            with poll_lock:voice_started[rid]=before
            body={'audience':'owner_private','request_id':rid,'consumer_id':consumer,
                'session_id':'p5-worker-companionship','observation':{'channel':'private_voice',
                'speaker':'owner','address_cue':'name','confidence':.8}}
            if audio is None:body['text']=text;path='/input'
            else:body['audio_base64']=base64.b64encode(audio).decode();path='/audio-input'
            value=host.request('POST',prefix+path,json=body)
            row={'request_id':rid,'intended_text':text,'audio_used':audio is not None,
                'seconds':round(time.monotonic()-before,3),'receipt':value,
                'server_first_audio_ready_ms':value.get('first_audio_ready_ms')}
            with poll_lock:row['first_output_poll']=first_output_poll.get(rid)
            with sqlite3.connect(state/'coordination.sqlite3') as db:
                found=db.execute('SELECT body FROM records WHERE kind=? AND id=?',('turn',rid)).fetchone()
                if found:row['actual_turn']=json.loads(found[0])
            report['voice'].append(row);checkpoint()
            if value.get('output_id'):
                output=poll()['output'];assert output and output['id']==value['output_id']
                row['output']=output
                if output['audio_available']:
                    response=host.client.get(prefix+'/audio/'+output['id'],params={'audience':'owner_private',
                        'consumer_id':consumer,'generation':output['generation'],'output_epoch':output['output_epoch']})
                    response.raise_for_status();data=response.content
                    (root/(rid+'-received.wav')).write_bytes(data)
                    row['received_audio']={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                        'evidence':'fetched_only_no_playback_or_hearing_claim'}
                post('/interrupt',reason='owner_stop')
                checkpoint()
            return value
        post('/connect');post('/control',action='voice_enabled',value=True)
        post('/control',action='speech_enabled',value=args.real_audio)
        def keepalive():
            while not stop.wait(1):
                try:
                    value=poll();output=value.get('output')
                    if output:
                        rid=output.get('utterance_id')
                        with poll_lock:
                            if rid in voice_started and rid not in first_output_poll:
                                first_output_poll[rid]={'client_seen_ms':round((time.monotonic()-voice_started[rid])*1000,3),
                                    'output_id':output['id'],'chunk_index':output.get('chunk_index'),
                                    'chunk_count':output.get('chunk_count'),'audio_available':output['audio_available'],
                                    'server_first_audio_ready_ms':output.get('first_audio_ready_ms'),
                                    'evidence':'first_poll_of_generated_output_not_playback_or_hearing'}
                except Exception:pass
        heartbeat=threading.Thread(target=keepalive,daemon=True);heartbeat.start()
        identity=personal()['identity_id'];report['identity_before']=identity
        foreground='Shana, while the tool is being built, what is one sensible reason to take a short stretch break?'
        audio=None
        if args.real_audio:
            from gamma.voice.tts import TTSService
            before=time.monotonic();generated=TTSService().synthesize(foreground,emotion='neutral')
            audio=Path(generated.audio_path).read_bytes();(root/'generated-foreground-input.wav').write_bytes(audio)
            Path(generated.audio_path).unlink()
            report['generated_foreground_audio']={'sha256':hashlib.sha256(audio).hexdigest(),
                'seconds':round(time.monotonic()-before,3),'text':foreground,'provider':generated.provider}
        spec={'mode':'build','goal':GOAL,'files':{'text_metrics.py':'# Implement the requested disposable CLI.\n',
            'README.md':GOAL,'p5_metrics_check.py':CHECK},'editable':['text_metrics.py'],
            'checks':[{'name':'metrics_cli_flow','module':'p5_metrics_check','kind':'regression'}]}
        identifier=host.prepare(spec);report['task_id']=identifier
        (state/'hold-candidate').touch();host.start_work(identifier)
        marker=until(lambda:json.loads((state/'worker-http-started.json').read_text())
            if (state/'worker-http-started.json').exists() else None,lambda v:v is not None,120)
        during=host.work(identifier)
        event('foreground_begins_after_actual_worker_http_started',worker_http=marker,
            run_status=during['run']['status'],stage=during['checkpoint']['stage'],
            revision=during['run']['revision'],attempt_count=during['checkpoint']['attempt_count'])
        assert during['run']['status']=='running',during
        value=voice(foreground,audio)
        assert value['status']=='generated',value
        saved=until(lambda:json.loads((state/'candidate-ready.json').read_text())
            if (state/'candidate-ready.json').exists() else None,lambda v:v is not None,180)
        assert saved['run_id']==identifier
        before=host.work(identifier);report['saved_candidate_before_controls']=before
        assert before['checkpoint']['stage']=='candidate_saved'
        event('actual_candidate_durably_saved_before_mute_sleep',checkpoint_ref=before['run']['checkpoint_ref'],
            source_sha256=before['checkpoint']['source_sha256'],attempt_count=before['checkpoint']['attempt_count'])
        post('/control',action='speech_enabled',value=False)
        after_mute=host.work(identifier)
        assert after_mute['run']['checkpoint_ref']==before['run']['checkpoint_ref']
        assert after_mute['run']['desired_state']=='running'
        assert host.request('GET','/api/coordination/status',params={'audience':'owner_private'})['awake']
        control('sleep');(state/'hold-candidate').unlink()
        asleep=until(lambda:host.work(identifier),lambda v:v.get('run') and v['run']['status'] in
            {'completed','blocked','needs_checker','stopped'},180)
        report['worker_completed_while_asleep']=asleep
        assert asleep['run']['status']=='completed',asleep
        sleeping=host.request('GET','/api/coordination/status',params={'audience':'owner_private'})
        assert not sleeping['awake']
        event('real_protected_checks_completed_while_asleep',checkpoint_ref=asleep['run']['checkpoint_ref'],
            source_sha256=asleep['checkpoint']['source_sha256'],attempt_count=asleep['checkpoint']['attempt_count'])
        wake=control('wake');finished=host.work(identifier);report['wake_result']=wake
        assert finished['checkpoint']['source_sha256']==before['checkpoint']['source_sha256']
        assert finished['checkpoint']['attempt_count']==before['checkpoint']['attempt_count']
        assert finished['run']['expires_at']==before['run']['expires_at']
        assert personal()['identity_id']==identity
        assert finished['files']['p5_metrics_check.py']==CHECK
        assert finished['files']['README.md']==GOAL
        independent=independent_check(finished['files']);report['independent_cli']=independent
        assert independent['passed'],independent
        report['final_task']=finished;report['identity_after']=personal()['identity_id']
        event('same_identity_saved_candidate_and_limits_retained_after_wake')
        result=voice('Shana, how did the disposable text metrics task '+identifier+' turn out? What was actually checked?')
        response=result.get('result',{}).get('assistant_response',{})
        report['checked_status_receipt']=bool(response.get('reason_code')=='work_status'
            and response.get('state_updates',{}).get('activity_run_id')==identifier)
        assert report['checked_status_receipt'],response
        report['source_stable']=source_files(project)==source
        report['checker_stable']=checker_files(project)==checkers
        report['model_identity_after']=model_identity()
        report['model_identity_stable']=report['model_identity_after']==report['model_identity_before']
        assert report['source_stable'] and report['checker_stable']
        assert report['model_identity_stable']
        report['complete']=True
    finally:
        stop.set()
        if heartbeat:heartbeat.join(3)
        if host:
            (state/'hold-candidate').unlink(missing_ok=True)
            host.stop();report['host_absent']=host.process.poll() is not None
        report['seconds']=round(time.monotonic()-started,3);checkpoint()
        print(json.dumps({'report':str(root/'report.json'),'complete':report['complete'],'seconds':report['seconds']}),flush=True)


if __name__=='__main__':main()
